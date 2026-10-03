"""Lightweight CloudWatch metrics for request performance.

Deliberately publishes a handful of aggregated metrics on a timer rather than
per-request datapoints: custom metrics are billed per metric per month, so the
whole app costs a few cents instead of the tens of dollars that cluster-wide
Container Insights would add.

Disabled unless METRICS_ENABLED=true, so tests and local runs stay offline.
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

NAMESPACE = os.getenv("METRICS_NAMESPACE", "BMI/HealthCheck")
SERVICE = os.getenv("METRICS_SERVICE", "bmi-api")
FLUSH_SECONDS = int(os.getenv("METRICS_FLUSH_SECONDS", "60"))


def resolve_region() -> str | None:
    """botocore only reads AWS_DEFAULT_REGION, so check AWS_REGION too."""
    return os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or None


def metrics_enabled() -> bool:
    return os.getenv("METRICS_ENABLED", "false").lower() == "true"


@dataclass
class _Window:
    """Counters accumulated between flushes."""

    requests: int = 0
    errors: int = 0
    bmi_calculations: int = 0
    latencies_ms: list[float] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not (self.requests or self.errors or self.bmi_calculations)


class MetricsCollector:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._window = _Window()

    def record_request(self, duration_ms: float, status_code: int) -> None:
        with self._lock:
            self._window.requests += 1
            self._window.latencies_ms.append(duration_ms)
            if status_code >= 500:
                self._window.errors += 1

    def record_bmi_calculation(self) -> None:
        with self._lock:
            self._window.bmi_calculations += 1

    def drain(self) -> _Window:
        with self._lock:
            window, self._window = self._window, _Window()
        return window


collector = MetricsCollector()


def build_metric_data(window: _Window) -> list[dict]:
    dimensions = [{"Name": "Service", "Value": SERVICE}]
    data: list[dict] = [
        {
            "MetricName": "RequestCount",
            "Dimensions": dimensions,
            "Unit": "Count",
            "Value": float(window.requests),
        },
        {
            "MetricName": "ErrorCount",
            "Dimensions": dimensions,
            "Unit": "Count",
            "Value": float(window.errors),
        },
        {
            "MetricName": "BmiCalculations",
            "Dimensions": dimensions,
            "Unit": "Count",
            "Value": float(window.bmi_calculations),
        },
    ]

    if window.latencies_ms:
        samples = window.latencies_ms
        data.append(
            {
                "MetricName": "LatencyMs",
                "Dimensions": dimensions,
                "Unit": "Milliseconds",
                "StatisticValues": {
                    "SampleCount": float(len(samples)),
                    "Sum": float(sum(samples)),
                    "Minimum": float(min(samples)),
                    "Maximum": float(max(samples)),
                },
            }
        )

    return data


async def publish_loop(stop: asyncio.Event) -> None:
    """Flush aggregated metrics until stopped."""
    try:
        import boto3
    except ImportError:
        logger.warning("boto3 unavailable; metrics publishing disabled")
        return

    try:
        client = boto3.client("cloudwatch", region_name=resolve_region())
    except Exception:
        logger.exception("could not create CloudWatch client; metrics disabled")
        return

    logger.info(
        "publishing metrics to CloudWatch namespace %s every %ss",
        NAMESPACE,
        FLUSH_SECONDS,
    )

    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=FLUSH_SECONDS)
        except asyncio.TimeoutError:
            pass

        window = collector.drain()
        if window.is_empty():
            continue

        try:
            await asyncio.to_thread(
                client.put_metric_data,
                Namespace=NAMESPACE,
                MetricData=build_metric_data(window),
            )
            logger.info(
                "published metrics: %s requests, %s errors, %s calculations",
                window.requests,
                window.errors,
                window.bmi_calculations,
            )
        except Exception:  # keep serving traffic even if CloudWatch rejects
            logger.exception("failed to publish metrics")
