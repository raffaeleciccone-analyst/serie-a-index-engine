"""
F2.0 — Historical (multi-season) primitives.

Cross-cutting concerns that don't fit a single pipeline stage live here:

* `season_registry`     — single source of truth for available seasons
* `cross_season_integrity` — integrity checks spanning multiple seasons
* `snapshot_manager`    — multi-season snapshot view + comparison
"""
from .cross_season_integrity import (
    CrossSeasonFinding,
    CrossSeasonReport,
    IntegrityCheck,
    IntegritySeverity,
    run_integrity_checks,
)
from .season_registry import (
    SEASON_REGISTRY_DEFAULT_PATH,
    SeasonConfig,
    SeasonRegistry,
    SeasonStatus,
    load_season_config,
)
from .snapshot_manager import (
    CrossSeasonManifest,
    SnapshotComparison,
    HistoricalSnapshotManager,
)

__all__ = [
    "CrossSeasonFinding",
    "CrossSeasonManifest",
    "CrossSeasonReport",
    "HistoricalSnapshotManager",
    "IntegrityCheck",
    "IntegritySeverity",
    "SEASON_REGISTRY_DEFAULT_PATH",
    "SeasonConfig",
    "SeasonRegistry",
    "SeasonStatus",
    "SnapshotComparison",
    "load_season_config",
    "run_integrity_checks",
]
