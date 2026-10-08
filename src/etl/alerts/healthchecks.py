"""Minimal Healthchecks.io client (start / success / fail pings).

The UUID URL comes from the ``HEALTHCHECKS_URL`` environment variable
(GitHub Secret). If it is missing, alerting is disabled gracefully with a log
warning; the ETL itself never fails because of alerting.

Ping contract (https://healthchecks.io/docs/http_api/):
  * POST ``<url>/start``  -> run started
  * POST ``<url>``        -> success (optional body stored as the log)
  * POST ``<url>/fail``   -> failure (body stored as the log)
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

import httpx

from etl.alerts.summary import build_run_body

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS = 10.0
DEFAULT_RETRIES = 3
RETRY_BASE_SECONDS = 1.0


class HealthchecksError(RuntimeError):
    """Retryable Healthchecks transport/server error."""


class HealthchecksClient:
    """Small POST client with retries and backoff."""

    def __init__(
        self,
        url: str,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        retries: int = DEFAULT_RETRIES,
        client: httpx.Client | None = None,
    ) -> None:
        self.url = url.rstrip("/")
        self.timeout = timeout
        self.retries = retries
        self._client = client or httpx.Client()

    @classmethod
    def from_env(cls) -> HealthchecksClient | None:
        url = os.environ.get("HEALTHCHECKS_URL")
        if not url:
            logger.warning("HEALTHCHECKS_URL is not configured; Healthchecks alerts disabled")
            return None
        return cls(url)

    def _post(self, path: str, body: str | None) -> bool:
        target = f"{self.url}{path}"
        last_error: Exception | None = None
        for attempt in range(1, self.retries + 1):
            try:
                response = self._client.post(
                    target,
                    content=(body or "").encode("utf-8"),
                    headers={"Content-Type": "text/plain; charset=utf-8"},
                    timeout=self.timeout,
                )
                if response.status_code >= 500:
                    raise HealthchecksError(f"server error {response.status_code} for {target}")
                if response.status_code >= 400:
                    logger.warning(
                        "healthchecks rejected ping (%s) status=%s",
                        target,
                        response.status_code,
                    )
                    return False
                return True
            except (httpx.HTTPError, HealthchecksError) as exc:
                last_error = exc
                logger.warning(
                    "healthchecks ping failed (attempt %s/%s) url=%s error=%s",
                    attempt,
                    self.retries,
                    target,
                    exc,
                )
                if attempt < self.retries:
                    time.sleep(RETRY_BASE_SECONDS * (2 ** (attempt - 1)))
        logger.error("giving up on healthchecks ping %s: %s", target, last_error)
        return False

    def ping_start(self) -> bool:
        return self._post("/start", None)

    def ping_success(self, body: str | None = None) -> bool:
        return self._post("", body)

    def ping_fail(self, body: str | None = None) -> bool:
        return self._post("/fail", body)


def _client_or_none(client: HealthchecksClient | None) -> HealthchecksClient | None:
    if client is not None:
        return client
    return HealthchecksClient.from_env()


def notify_start(client: HealthchecksClient | None = None) -> bool:
    resolved = _client_or_none(client)
    if resolved is None:
        return False
    return resolved.ping_start()


def notify_report(
    report_path: Path | str = Path("artifacts/run_report.json"),
    client: HealthchecksClient | None = None,
) -> bool:
    """Ping /fail when the run had failures, /success otherwise."""

    path = Path(report_path)
    if not path.exists():
        logger.warning("run report not found at %s; skipping Healthchecks ping", path)
        return False
    report: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    resolved = _client_or_none(client)
    if resolved is None:
        return False
    body = build_run_body(report)
    if report.get("failures"):
        return resolved.ping_fail(body)
    return resolved.ping_success(body)


def notify_crash(step: str, client: HealthchecksClient | None = None) -> bool:
    resolved = _client_or_none(client)
    if resolved is None:
        return False
    return resolved.ping_fail(f"crash en paso {step}")
