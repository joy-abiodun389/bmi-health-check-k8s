"""Client storage.

Postgres is used whenever DATABASE_URL is set (that is, in the cluster). Local
runs and unit tests fall back to an in-memory store so the app is usable and
testable without a database.
"""

from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol

logger = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS clients (
    id            BIGSERIAL PRIMARY KEY,
    email         TEXT        NOT NULL UNIQUE,
    full_name     TEXT        NOT NULL,
    password_hash TEXT        NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_login_at TIMESTAMPTZ
);
"""


class EmailAlreadyRegistered(Exception):
    """Raised when a signup collides with an existing email."""


@dataclass(frozen=True)
class Client:
    email: str
    full_name: str
    password_hash: str
    created_at: datetime | None = None
    last_login_at: datetime | None = None


class ClientRepository(Protocol):
    def create(self, email: str, full_name: str, password_hash: str) -> Client: ...

    def find(self, email: str) -> Client | None: ...

    def touch_login(self, email: str) -> None: ...

    def count(self) -> int: ...


class InMemoryClientRepository:
    """Used for tests and for running the app without a database."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._clients: dict[str, Client] = {}

    def create(self, email: str, full_name: str, password_hash: str) -> Client:
        with self._lock:
            if email in self._clients:
                raise EmailAlreadyRegistered(email)
            client = Client(
                email=email,
                full_name=full_name,
                password_hash=password_hash,
                created_at=datetime.now(timezone.utc),
            )
            self._clients[email] = client
            return client

    def find(self, email: str) -> Client | None:
        with self._lock:
            return self._clients.get(email)

    def touch_login(self, email: str) -> None:
        with self._lock:
            existing = self._clients.get(email)
            if existing:
                self._clients[email] = Client(
                    email=existing.email,
                    full_name=existing.full_name,
                    password_hash=existing.password_hash,
                    created_at=existing.created_at,
                    last_login_at=datetime.now(timezone.utc),
                )

    def count(self) -> int:
        with self._lock:
            return len(self._clients)


class PostgresClientRepository:
    def __init__(self, dsn: str) -> None:
        from psycopg_pool import ConnectionPool

        # min_size=0 so a database outage never blocks pod startup; readiness
        # stays green for the BMI calculator even if signup is degraded.
        self._pool = ConnectionPool(dsn, min_size=0, max_size=4, open=True, timeout=10)

    def initialize(self) -> None:
        with self._pool.connection() as conn:
            conn.execute(SCHEMA)

    def create(self, email: str, full_name: str, password_hash: str) -> Client:
        from psycopg import errors

        try:
            with self._pool.connection() as conn:
                row = conn.execute(
                    """
                    INSERT INTO clients (email, full_name, password_hash)
                    VALUES (%s, %s, %s)
                    RETURNING email, full_name, password_hash, created_at, last_login_at
                    """,
                    (email, full_name, password_hash),
                ).fetchone()
        except errors.UniqueViolation as exc:
            raise EmailAlreadyRegistered(email) from exc
        return Client(*row)

    def find(self, email: str) -> Client | None:
        with self._pool.connection() as conn:
            row = conn.execute(
                """
                SELECT email, full_name, password_hash, created_at, last_login_at
                FROM clients WHERE email = %s
                """,
                (email,),
            ).fetchone()
        return Client(*row) if row else None

    def touch_login(self, email: str) -> None:
        with self._pool.connection() as conn:
            conn.execute(
                "UPDATE clients SET last_login_at = now() WHERE email = %s", (email,)
            )

    def count(self) -> int:
        with self._pool.connection() as conn:
            return conn.execute("SELECT count(*) FROM clients").fetchone()[0]

    def close(self) -> None:
        self._pool.close()


_repository: ClientRepository | None = None


def build_repository() -> ClientRepository:
    dsn = os.getenv("DATABASE_URL")
    if not dsn:
        logger.warning("DATABASE_URL unset; storing clients in memory only")
        return InMemoryClientRepository()

    repository = PostgresClientRepository(dsn)
    repository.initialize()
    logger.info("connected to Postgres; %s client(s) registered", repository.count())
    return repository


def get_repository() -> ClientRepository:
    """FastAPI dependency; overridden in tests."""
    global _repository
    if _repository is None:
        _repository = build_repository()
    return _repository


def set_repository(repository: ClientRepository | None) -> None:
    global _repository
    _repository = repository
