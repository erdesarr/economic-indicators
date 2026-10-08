"""Source adapter registry."""

from __future__ import annotations

from etl.sources.banrep import BanrepAdapter
from etl.sources.base import SourceAdapter
from etl.sources.bcb import BcbAdapter
from etl.sources.investing import InvestingAdapter
from etl.sources.larepublica import LaRepublicaAdapter


def get_adapter(source_name: str) -> SourceAdapter:
    if source_name.startswith("larepublica"):
        return LaRepublicaAdapter(source_name)
    if source_name == "banrep":
        return BanrepAdapter()
    if source_name == "bcb":
        return BcbAdapter()
    if source_name == "investing":
        return InvestingAdapter()
    raise KeyError(f"no adapter registered for source {source_name!r}")
