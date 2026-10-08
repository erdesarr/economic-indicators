"""Day-over-day percentage variation with base-gap semantics.

Rules (business-approved):
  * Compare against the natural previous day when a reading exists.
  * Otherwise use the most recent reading within ``max_gap_days`` and mark
    ``base_gap = True``.
  * No reading at all -> ``pct = None`` (and ``base_gap = True``).
  * Division by zero -> ``pct = None``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta


@dataclass(frozen=True)
class Variation:
    pct: float | None
    base_gap: bool
    base_date: date | None


def compute_variation(
    current_value: float,
    current_date: date,
    previous_value: float | None,
    previous_date: date | None,
    max_gap_days: int = 7,
) -> Variation:
    if previous_value is None or previous_date is None:
        return Variation(pct=None, base_gap=True, base_date=None)
    if previous_date > current_date - timedelta(days=1):
        # Defensive: future or same-day base cannot be used.
        return Variation(pct=None, base_gap=True, base_date=None)
    if (current_date - previous_date).days > max_gap_days:
        return Variation(pct=None, base_gap=True, base_date=None)
    base_gap = previous_date != current_date - timedelta(days=1)
    if previous_value == 0:
        return Variation(pct=None, base_gap=base_gap, base_date=previous_date)
    pct = (current_value - previous_value) / previous_value * 100.0
    return Variation(pct=pct, base_gap=base_gap, base_date=previous_date)
