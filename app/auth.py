"""Password hashing and signed session cookies.

Uses only the standard library: scrypt for password hashing and an HMAC-signed
cookie for sessions, so the image needs no extra native crypto dependencies.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import os
import secrets
import time

logger = logging.getLogger(__name__)

SESSION_COOKIE = "bmi_session"
SESSION_TTL_SECONDS = int(os.getenv("SESSION_TTL_SECONDS", str(12 * 60 * 60)))

# scrypt parameters: n=2**14 keeps sign-in well under 100ms on a t3.small
# while staying far more expensive than a plain hash for an attacker.
_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1
_SALT_BYTES = 16


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def session_secret() -> bytes:
    """Shared signing key; must be identical across replicas."""
    configured = os.getenv("SESSION_SECRET")
    if configured:
        return configured.encode()

    # Dev fallback only: each process gets its own key, so sessions do not
    # survive a restart and would not validate across replicas.
    global _EPHEMERAL_SECRET
    if _EPHEMERAL_SECRET is None:
        _EPHEMERAL_SECRET = secrets.token_bytes(32)
        logger.warning("SESSION_SECRET unset; using an ephemeral per-process key")
    return _EPHEMERAL_SECRET


_EPHEMERAL_SECRET: bytes | None = None


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(_SALT_BYTES)
    derived = hashlib.scrypt(
        password.encode(), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P
    )
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${_b64encode(salt)}${_b64encode(derived)}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        scheme, n, r, p, salt, expected = encoded.split("$")
        if scheme != "scrypt":
            return False
        derived = hashlib.scrypt(
            password.encode(),
            salt=_b64decode(salt),
            n=int(n),
            r=int(r),
            p=int(p),
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(_b64encode(derived), expected)


def issue_session(email: str, now: float | None = None) -> str:
    expires_at = int((time.time() if now is None else now) + SESSION_TTL_SECONDS)
    payload = _b64encode(f"{email}|{expires_at}".encode())
    signature = hmac.new(session_secret(), payload.encode(), hashlib.sha256).digest()
    return f"{payload}.{_b64encode(signature)}"


def read_session(token: str | None, now: float | None = None) -> str | None:
    """Return the email in a valid, unexpired token, else None."""
    if not token or "." not in token:
        return None

    payload, _, signature = token.partition(".")
    expected = hmac.new(session_secret(), payload.encode(), hashlib.sha256).digest()
    if not hmac.compare_digest(_b64encode(expected), signature):
        return None

    try:
        email, _, expires_at = _b64decode(payload).decode().rpartition("|")
        if (time.time() if now is None else now) > int(expires_at):
            return None
    except (ValueError, UnicodeDecodeError):
        return None

    return email or None
