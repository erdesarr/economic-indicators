"""HTTP helpers with UA rotation, retries and exponential backoff."""

from __future__ import annotations

import functools
import logging
import random
import tempfile
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import certifi
import requests

from etl.models import Settings

logger = logging.getLogger(__name__)

RETRY_BASE_SECONDS = 1.0

# Some servers send an incomplete TLS chain. suameca.banrep.gov.co sends the
# leaf + root but omits the intermediate "GeoTrust EV RSA CA G2" (published in
# the leaf's AIA extension), which browsers resolve via the OS cache. We pin
# the intermediate explicitly and combine it with certifi's roots.
EXTRA_CA_HOSTS: dict[str, str] = {
    "suameca.banrep.gov.co": "config/certs/geotrust_ev_rsa_ca_g2.pem",
}


@functools.lru_cache(maxsize=8)
def extra_ca_bundle(host: str | None) -> str | None:
    """Return a CA bundle path for hosts with an incomplete chain."""

    if host is None:
        return None
    relative = EXTRA_CA_HOSTS.get(host)
    if relative is None:
        return None
    intermediate = Path(relative)
    if not intermediate.exists():
        logger.warning("extra CA file missing for %s: %s", host, intermediate)
        return None
    roots = Path(certifi.where()).read_text(encoding="utf-8")
    bundle = Path(tempfile.gettempdir()) / f"etl_ca_bundle_{host}.pem"
    bundle.write_text(roots + "\n" + intermediate.read_text(encoding="utf-8"), encoding="utf-8")
    return str(bundle)


class FetchError(RuntimeError):
    """Raised when a source cannot be fetched after all retries."""


class PermanentFetchError(FetchError):
    """Raised for non-retryable client errors (HTTP 4xx)."""


class HttpClient:
    """Small requests wrapper shared by the HTTP/JSON adapters."""

    def __init__(self, settings: Settings, session: requests.Session | None = None) -> None:
        self._settings = settings
        self._session = session or requests.Session()

    def _headers(self) -> dict[str, str]:
        return {
            "User-Agent": random.choice(self._settings.user_agents),
            "Accept-Language": "es-CO,es;q=0.9,en;q=0.8",
        }

    def get(self, url: str, retries: int = 3, accept: str | None = None) -> requests.Response:
        headers = self._headers()
        if accept:
            headers["Accept"] = accept
        last_error: Exception | None = None
        verify = extra_ca_bundle(urlsplit(url).hostname)
        for attempt in range(1, retries + 1):
            try:
                response = self._session.get(
                    url,
                    headers=headers,
                    timeout=self._settings.request_timeout,
                    verify=verify,
                )
                if response.status_code >= 500:
                    raise FetchError(f"server error {response.status_code} for {url}")
                if response.status_code >= 400:
                    raise PermanentFetchError(f"client error {response.status_code} for {url}")
                response.raise_for_status()
                return response
            except PermanentFetchError:
                raise
            except (requests.RequestException, FetchError) as exc:
                last_error = exc
                logger.warning(
                    "fetch failed (attempt %s/%s) url=%s error=%s", attempt, retries, url, exc
                )
                if attempt < retries:
                    time.sleep(RETRY_BASE_SECONDS * (2 ** (attempt - 1)))
        raise FetchError(f"giving up on {url} after {retries} attempts: {last_error}")

    def get_text(self, url: str, retries: int = 3) -> str:
        response = self.get(url, retries=retries, accept="text/html,application/xhtml+xml")
        return response.text

    def get_json(self, url: str, retries: int = 3) -> Any:
        """Fetch and parse JSON, retrying on network errors AND bad bodies.

        Some endpoints (e.g. BCB SGS) intermittently answer 200 with a
        non-JSON body or 502; both are retried with backoff.
        """

        last_error: Exception | None = None
        for attempt in range(1, retries + 1):
            try:
                response = self.get(url, retries=1, accept="application/json")
                return response.json()
            except (FetchError, ValueError) as exc:
                last_error = exc
                logger.warning(
                    "json fetch failed (attempt %s/%s) url=%s error=%s",
                    attempt,
                    retries,
                    url,
                    exc,
                )
                if attempt < retries:
                    time.sleep(RETRY_BASE_SECONDS * (2 ** (attempt - 1)))
        raise FetchError(f"giving up on {url} after {retries} attempts: {last_error}")

    def sleep_politeness(self) -> None:
        """Courtesy delay between requests to the same host."""

        time.sleep(self._settings.politeness_seconds)
