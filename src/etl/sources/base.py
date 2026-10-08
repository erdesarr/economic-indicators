"""Source adapter protocol shared by all extractors."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from etl.http import HttpClient
from etl.models import IndicatorConfig, Settings, SourceConfig, SourceResult


class SourceAdapter(Protocol):
    """Extracts values for one source (group of indicators)."""

    name: str

    def fetch(
        self,
        indicators: Sequence[IndicatorConfig],
        source: SourceConfig,
        settings: Settings,
        client: HttpClient,
    ) -> dict[str, SourceResult]:
        """Return one :class:`SourceResult` per requested indicator slug."""
        ...
