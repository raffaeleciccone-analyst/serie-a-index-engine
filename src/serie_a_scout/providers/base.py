"""
Provider abstraction — protocol every football-data adapter implements.

A provider is a thin layer that:

  * exposes a stable, documented set of `fetch_*` methods
  * returns DataFrames already mapped to the canonical schema
  * declares its own `confidence` (used by the merge engine)
  * never persists anything — persistence is owned by the ingestion stage

Implementations (Understat, FBref, Transfermarkt, …) live as siblings of
this file. Tests instantiate `MockProvider` from `providers.mock` and
plug it into the merge engine.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Protocol, runtime_checkable

import pandas as pd


class ProviderError(RuntimeError):
    """Generic provider failure — wrap network / parsing exceptions."""


@dataclass(frozen=True)
class ProviderInfo:
    """Static metadata about a provider."""
    name: str
    confidence: float                  # in [0, 1]
    schema_version: str = "1.0.0"
    supports_seasons: tuple[str, ...] = ()
    docs_url: str | None = None
    rate_limit_per_minute: int | None = None


@dataclass
class FetchResult:
    """Wrapper around a provider DataFrame with lineage breadcrumbs."""
    provider: str
    season: str
    kind: str                          # "players" | "player_matches" | "teams"
    fetched_at: str                    # ISO-8601 UTC
    rows: int
    df: pd.DataFrame
    extra: Mapping[str, Any] = field(default_factory=dict)

    def short(self) -> str:
        return f"{self.provider}:{self.season}:{self.kind} ({self.rows} rows)"


@runtime_checkable
class FootballDataProvider(Protocol):
    """Every adapter MUST implement this protocol."""
    info: ProviderInfo

    def fetch_players(self, season: str) -> FetchResult: ...
    def fetch_player_matches(self, season: str) -> FetchResult: ...
    def fetch_teams(self, season: str) -> FetchResult: ...


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
