"""
Provider abstraction layer (F2.0 prep, F3 expansion).

Modules:
  * `base`              — Protocol + provider metadata + result types
  * `canonical_schema`  — canonical column set every provider must produce
  * `merge`             — multi-provider conflict resolution
  * `mock`              — in-memory adapter for tests
"""
from .base import (
    FetchResult,
    FootballDataProvider,
    ProviderError,
    ProviderInfo,
)
from .canonical_schema import (
    CANONICAL_PLAYER_COLUMNS,
    CANONICAL_PLAYER_MATCH_COLUMNS,
    CANONICAL_TEAM_COLUMNS,
    CANONICAL_TYPES,
    enforce_canonical_schema,
    validate_canonical_schema,
)
from .merge import (
    MergeConflict,
    MergeReport,
    MergeStrategy,
    merge_provider_frames,
)
from .mock import MockProvider

__all__ = [
    "CANONICAL_PLAYER_COLUMNS",
    "CANONICAL_PLAYER_MATCH_COLUMNS",
    "CANONICAL_TEAM_COLUMNS",
    "CANONICAL_TYPES",
    "FetchResult",
    "FootballDataProvider",
    "MergeConflict",
    "MergeReport",
    "MergeStrategy",
    "MockProvider",
    "ProviderError",
    "ProviderInfo",
    "enforce_canonical_schema",
    "merge_provider_frames",
    "validate_canonical_schema",
]
