"""Pinned TLS intermediate expiry guard.

The Banrep intermediate is pinned only because the server omits it from its
TLS chain. This test FAILS in CI when the pin is about to expire so the label
gets renewed here (a build failure), never as a production alert.
"""

from __future__ import annotations

import ssl
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PINNED_CERT = REPO_ROOT / "config" / "certs" / "geotrust_ev_rsa_ca_g2.pem"
MIN_DAYS = 30


def _not_after(path: Path) -> datetime:
    info = ssl._ssl._test_decode_cert(str(path))  # type: ignore[attr-defined]
    parsed = datetime.strptime(info["notAfter"], "%b %d %H:%M:%S %Y %Z")
    return parsed.replace(tzinfo=UTC)


def test_pinned_certificate_parses() -> None:
    assert PINNED_CERT.exists()
    not_after = _not_after(PINNED_CERT)
    assert not_after > datetime(2020, 1, 1, tzinfo=UTC)


def test_pinned_certificate_does_not_expire_in_less_than_30_days() -> None:
    not_after = _not_after(PINNED_CERT)
    remaining_days = (not_after - datetime.now(UTC)).days
    assert remaining_days >= MIN_DAYS, (
        f"pinned cert {PINNED_CERT.name} expires in {remaining_days} days "
        f"({not_after.isoformat()}); renew it from the AIA URL"
    )
