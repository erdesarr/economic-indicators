"""Candidate resolution: primary -> fallback -> forward_fill with a guard.

Business-approved chain for each ACTIVE sheet row:

  * candidates: [primary, fallback (when configured), forward_fill];
  * the variation guard applies only to FRESH candidates (``status == "ok"``):
    when the previous day exists and ``|variation_pct| > threshold`` the
    candidate is discarded with a reason ("variación X% excede umbral Y%");
  * forward_fill is never discarded (it is the final net; it copies a previous
    reading, so its variation is ~0);
  * the displayed variation always belongs to the variable whose value was
    used (primary, fallback, or the previous reading for forward_fill);
  * no usable candidate -> the row is omitted and reported.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from form_updater.sheet import IndicatorRow


@dataclass(frozen=True)
class VariableSnapshot:
    """One variable's latest ETL state (from latest.json + history)."""

    value: float
    variation_pct: float | None
    status: str  # "ok" | "forward_filled"
    previous_value: float | None = None
    previous_variation_pct: float | None = None


@dataclass(frozen=True)
class Resolution:
    row: IndicatorRow
    value: float
    variation_pct: float | None
    source: str  # "primary" | "fallback" | "forward_filled"
    source_slug: str
    discarded: tuple[str, ...] = ()
    reason: str | None = None

    @property
    def degraded(self) -> bool:
        return self.source != "primary" or bool(self.discarded)


@dataclass(frozen=True)
class ResolutionFailure:
    row: IndicatorRow
    reasons: tuple[str, ...]


def resolve_row(
    row: IndicatorRow,
    data: Mapping[str, VariableSnapshot],
    threshold: float,
) -> Resolution | ResolutionFailure:
    """Resolve one sheet row using the approved chain and variation guard."""

    discarded: list[str] = []

    def evaluate(slug: str, source: str) -> Resolution | None:
        snapshot = data.get(slug)
        if snapshot is None:
            discarded.append(f"{source} {slug}: no data in latest.json")
            return None
        if snapshot.status == "forward_filled":
            # Not a guard discard: this snapshot is the forward_fill candidate.
            return None
        if snapshot.status != "ok":
            discarded.append(f"{source} {slug}: status {snapshot.status}")
            return None
        variation = snapshot.variation_pct
        if variation is not None and abs(variation) > threshold:
            discarded.append(
                f"{source} {slug}: variación {variation:+.2f}% excede umbral {threshold:g}%"
            )
            return None
        return Resolution(
            row=row,
            value=snapshot.value,
            variation_pct=variation,
            source=source,
            source_slug=slug,
            discarded=tuple(discarded),
        )

    primary = evaluate(row.indicador, "primary")
    if primary is not None:
        return primary

    if row.fallback:
        fallback = evaluate(row.fallback, "fallback")
        if fallback is not None:
            return fallback

    snapshot = data.get(row.indicador)
    if snapshot is not None:
        value = snapshot.previous_value
        variation = snapshot.previous_variation_pct
        if snapshot.status == "forward_filled" and value is None:
            # The ETL already copied the previous reading into `value`.
            value = snapshot.value
            variation = snapshot.variation_pct
        if value is not None:
            return Resolution(
                row=row,
                value=value,
                variation_pct=variation,
                source="forward_filled",
                source_slug=row.indicador,
                discarded=tuple(discarded),
                reason="forward_fill: previous stored reading",
            )

    return ResolutionFailure(
        row=row,
        reasons=tuple(discarded) + ("no usable candidate (primary/fallback/forward_fill)",),
    )
