"""
In-memory provider for tests.

`MockProvider` returns the DataFrames it was constructed with — no I/O,
no scraping, no network. It is the canonical example of how a real
adapter (FBref, Understat) should look from the outside.

A real adapter will:

  1. fetch raw data (HTTP, file, …)
  2. coerce into the canonical schema (`enforce_canonical_schema`)
  3. return a `FetchResult` with `df=` set and `extra=` carrying any
     scraping metadata (HTML hash, request id, …)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

import pandas as pd

from .base import FetchResult, ProviderError, ProviderInfo, utc_now_iso
from .canonical_schema import enforce_canonical_schema


@dataclass
class MockProvider:
    """Provider that yields pre-supplied DataFrames per (season, kind)."""
    info: ProviderInfo
    data: Mapping[tuple[str, str], pd.DataFrame] = field(default_factory=dict)

    def fetch_players(self, season: str) -> FetchResult:
        return self._fetch(season, "players")

    def fetch_player_matches(self, season: str) -> FetchResult:
        return self._fetch(season, "player_matches")

    def fetch_teams(self, season: str) -> FetchResult:
        return self._fetch(season, "teams")

    def _fetch(self, season: str, kind: str) -> FetchResult:
        df = self.data.get((season, kind))
        if df is None:
            raise ProviderError(
                f"{self.info.name}: no data for ({season!r}, {kind!r})"
            )
        if "provider" not in df.columns:
            df = df.copy()
            df["provider"] = self.info.name
        if "season" not in df.columns:
            df = df.copy()
            df["season"] = season
        df = enforce_canonical_schema(df, kind=kind)
        return FetchResult(
            provider=self.info.name,
            season=season,
            kind=kind,
            fetched_at=utc_now_iso(),
            rows=len(df),
            df=df,
        )
