"""
Season registry — single source of truth for which seasons the platform
covers.

The registry composes three inputs:

1. The declarative list in `config/seasons.yml` (which seasons we *expect*
   to track, with their canonical `expected_giornate` and date range).
2. The on-disk snapshot tree (`snapshots/<season>/giornata_NN/`) — what
   we *actually have* persisted.
3. The lineage store (`pipeline_run` rows tagged with stage in
   {`snapshot`, `ingest`, `historical_backfill`}) — what we *actually ran*.

Output: a `SeasonStatus` per season with coverage %, missing giornate,
freshness, checksum stats, and the lineage run-ids that produced (or
failed to produce) the snapshots.

Determinism: the registry never mutates anything. It is a pure read
against config + filesystem + DB. Callers that want to record provenance
should wrap their actions in a `RunContext(stage="historical_backfill")`.

Forward-compat: the YAML carries `league` per entry, so when F4 brings
multi-league support the loader needs no schema change.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from ..core.lineage_store import LineageStore, RunRecord, get_default_store
from ..snapshots.manifest import Manifest
from ..snapshots.restorer import verify_snapshot


_REPO_ROOT = Path(__file__).resolve().parents[3]
SEASON_REGISTRY_DEFAULT_PATH: Path = _REPO_ROOT / "config" / "seasons.yml"
DEFAULT_SNAPSHOT_ROOT: Path = _REPO_ROOT / "snapshots"


# ─────────────────────────────────────────────────────────────────────
# YAML dataclasses
# ─────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class SeasonConfig:
    """One row of the `seasons:` list in `config/seasons.yml`."""
    id: str                      # "2025-26"
    league: str
    expected_giornate: int
    earliest_match_date: str | None = None
    latest_match_date: str | None = None

    def __post_init__(self) -> None:
        if "/" in self.id or ".." in self.id:
            raise ValueError(f"season id non valida: {self.id!r}")
        if self.expected_giornate <= 0:
            raise ValueError(
                f"expected_giornate must be > 0 (was {self.expected_giornate})"
            )


@dataclass
class SeasonsConfigFile:
    schema_version: str
    seasons: list[SeasonConfig]
    provider_precedence: Mapping[str, list[str]] = field(default_factory=dict)


def load_season_config(path: Path | str | None = None) -> SeasonsConfigFile:
    """
    Load `config/seasons.yml`. PyYAML is preferred; a minimal parser
    is used as fallback so the file resolves on minimal CI images.
    """
    p = Path(path) if path else SEASON_REGISTRY_DEFAULT_PATH
    if not p.is_file():
        return SeasonsConfigFile(schema_version="1.0.0", seasons=[])
    text = p.read_text(encoding="utf-8")
    try:
        import yaml  # type: ignore
        raw = yaml.safe_load(text) or {}
    except ImportError:
        raw = _minimal_yaml_parse(text)
    if not isinstance(raw, dict):
        raise ValueError(f"{p} non è un mapping YAML")

    seasons_raw = raw.get("seasons") or []
    seasons: list[SeasonConfig] = []
    for s in seasons_raw:
        if not isinstance(s, dict):
            continue
        try:
            seasons.append(SeasonConfig(
                id=str(s["id"]),
                league=str(s.get("league", "serie-a")),
                expected_giornate=int(s.get("expected_giornate", 38)),
                earliest_match_date=s.get("earliest_match_date"),
                latest_match_date=s.get("latest_match_date"),
            ))
        except (KeyError, ValueError) as e:
            raise ValueError(f"Stagione malformata in {p}: {s!r} ({e})") from e

    provider_precedence = raw.get("provider_precedence") or {}
    if not isinstance(provider_precedence, dict):
        provider_precedence = {}
    return SeasonsConfigFile(
        schema_version=str(raw.get("schema_version", "1.0.0")),
        seasons=seasons,
        provider_precedence={
            k: list(v) for k, v in provider_precedence.items()
            if isinstance(v, list)
        },
    )


# ─────────────────────────────────────────────────────────────────────
# Status dataclass
# ─────────────────────────────────────────────────────────────────────
@dataclass
class SeasonStatus:
    season: str
    league: str
    expected_giornate: int
    available_giornate: list[int]
    missing_giornate: list[int]
    snapshot_count: int
    checksum_ok: int
    checksum_fail: int
    last_snapshot_at: str | None
    latest_ingestion_at: str | None
    ingestion_runs: int
    freshness_hours: float | None
    coverage_pct: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ─────────────────────────────────────────────────────────────────────
# Registry
# ─────────────────────────────────────────────────────────────────────
class SeasonRegistry:
    """
    Read-only view over seasons.yml + snapshots/ + lineage DB.

    Construct once; instance is cheap and stateless across queries.
    """

    def __init__(
        self,
        *,
        config: SeasonsConfigFile | None = None,
        snapshot_root: Path | str | None = None,
        store: LineageStore | None = None,
        config_path: Path | str | None = None,
        now: datetime | None = None,
    ) -> None:
        self.config: SeasonsConfigFile = config or load_season_config(config_path)
        self.snapshot_root: Path = Path(snapshot_root) if snapshot_root else DEFAULT_SNAPSHOT_ROOT
        self.store: LineageStore = store or get_default_store()
        self._now: datetime = now or datetime.now(timezone.utc)

    # ────────────────────── public ──────────────────────
    def seasons(self) -> list[SeasonConfig]:
        """Declared seasons, ordered as in the YAML."""
        return list(self.config.seasons)

    def season_ids(self) -> list[str]:
        return [s.id for s in self.config.seasons]

    def discover_seasons_on_disk(self) -> list[str]:
        """Return seasons that have a directory under `snapshots/`."""
        if not self.snapshot_root.is_dir():
            return []
        return sorted(
            p.name for p in self.snapshot_root.iterdir()
            if p.is_dir() and not p.name.startswith(".")
        )

    def status(self, season: str) -> SeasonStatus:
        cfg = self._cfg_for(season)
        snapshot_dir = self.snapshot_root / season
        available, ok, fail, last_snap_iso = self._scan_snapshot_dir(snapshot_dir)
        ingestion_runs, latest_ingestion_iso = self._lineage_for(season)
        missing = [g for g in range(1, cfg.expected_giornate + 1) if g not in available]
        coverage = (len(available) / cfg.expected_giornate * 100.0) if cfg.expected_giornate else 0.0
        freshness = _iso_to_age_hours(last_snap_iso, self._now)
        return SeasonStatus(
            season=season,
            league=cfg.league,
            expected_giornate=cfg.expected_giornate,
            available_giornate=available,
            missing_giornate=missing,
            snapshot_count=len(available),
            checksum_ok=ok,
            checksum_fail=fail,
            last_snapshot_at=last_snap_iso,
            latest_ingestion_at=latest_ingestion_iso,
            ingestion_runs=ingestion_runs,
            freshness_hours=freshness,
            coverage_pct=round(coverage, 2),
        )

    def all_statuses(self) -> list[SeasonStatus]:
        return [self.status(s.id) for s in self.config.seasons]

    def provider_precedence(self) -> Mapping[str, list[str]]:
        return self.config.provider_precedence

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at": self._now.isoformat(timespec="seconds"),
            "schema_version": self.config.schema_version,
            "seasons": [s.to_dict() for s in self.all_statuses()],
            "provider_precedence": dict(self.provider_precedence()),
        }

    # ────────────────────── internals ──────────────────────
    def _cfg_for(self, season: str) -> SeasonConfig:
        for s in self.config.seasons:
            if s.id == season:
                return s
        # Tolerate seasons present on disk but not declared (informational).
        return SeasonConfig(id=season, league="unknown", expected_giornate=38)

    def _scan_snapshot_dir(
        self, snapshot_dir: Path,
    ) -> tuple[list[int], int, int, str | None]:
        """Return (available_giornate, ok, fail, last_snapshot_at_iso)."""
        if not snapshot_dir.is_dir():
            return [], 0, 0, None
        available: list[int] = []
        ok = fail = 0
        last_iso: str | None = None
        for g in sorted(snapshot_dir.iterdir()):
            if not g.is_dir():
                continue
            if not g.name.startswith("giornata_"):
                continue
            try:
                gn = int(g.name.split("_", 1)[1])
            except ValueError:
                continue
            available.append(gn)
            mp = g / "manifest.json"
            if mp.is_file():
                vr = verify_snapshot(g)
                if vr.ok:
                    ok += 1
                else:
                    fail += 1
                if vr.manifest and vr.manifest.created_at_utc:
                    if last_iso is None or vr.manifest.created_at_utc > last_iso:
                        last_iso = vr.manifest.created_at_utc
            else:
                fail += 1
        return sorted(available), ok, fail, last_iso

    def _lineage_for(self, season: str) -> tuple[int, str | None]:
        """How many ingestion-related runs touched this season?"""
        rows = self.store.iter_runs(stage="snapshot", limit=10000)
        rows += self.store.iter_runs(stage="historical_backfill", limit=10000)
        rows += self.store.iter_runs(stage="ingest", limit=10000)
        n = 0
        latest: str | None = None
        for r in rows:
            if (r.extra or {}).get("season") == season:
                n += 1
                if latest is None or r.started_at > latest:
                    latest = r.started_at
        return n, latest


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────
def _iso_to_age_hours(iso_ts: str | None, now: datetime) -> float | None:
    if not iso_ts:
        return None
    try:
        ts = datetime.fromisoformat(str(iso_ts))
    except ValueError:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return round((now - ts).total_seconds() / 3600.0, 2)


# ─────────────────────────────────────────────────────────────────────
# Minimal YAML parser for `seasons.yml`
# ─────────────────────────────────────────────────────────────────────
def _minimal_yaml_parse(text: str) -> dict[str, Any]:
    """Parser intenzionalmente minimale per la shape di seasons.yml.

    Supports:
      schema_version: "1.0.0"
      seasons:
        - id: "..."
          league: "..."
          expected_giornate: 38
          earliest_match_date: "..."
          latest_match_date: "..."
      provider_precedence:
        xg: ["a", "b"]
        ...

    No anchors. Two-level nesting only. Tested via test_season_registry.
    """
    lines = [l for l in text.splitlines()]
    out: dict[str, Any] = {}
    i = 0
    while i < len(lines):
        raw = lines[i]
        line = raw.split("#", 1)[0].rstrip()
        i += 1
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip())
        if indent != 0:
            continue
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        key = key.strip()
        val = val.strip()
        if key == "seasons":
            items, consumed = _parse_seasons_list(lines, i)
            out["seasons"] = items
            i = consumed
        elif key == "provider_precedence":
            d, consumed = _parse_provider_block(lines, i)
            out["provider_precedence"] = d
            i = consumed
        elif val:
            out[key] = _coerce_scalar(val)
    return out


def _parse_seasons_list(lines: list[str], start: int) -> tuple[list[dict], int]:
    out: list[dict] = []
    cur: dict | None = None
    i = start
    while i < len(lines):
        raw = lines[i]
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            i += 1
            continue
        indent = len(line) - len(line.lstrip())
        if indent == 0:
            break
        stripped = line.strip()
        if stripped.startswith("- "):
            if cur is not None:
                out.append(cur)
            cur = {}
            stripped = stripped[2:].strip()
        if ":" in stripped:
            k, _, v = stripped.partition(":")
            if cur is None:
                cur = {}
            cur[k.strip()] = _coerce_scalar(v.strip()) if v.strip() else None
        i += 1
    if cur is not None:
        out.append(cur)
    return out, i


def _parse_provider_block(lines: list[str], start: int) -> tuple[dict[str, list[str]], int]:
    out: dict[str, list[str]] = {}
    i = start
    while i < len(lines):
        raw = lines[i]
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            i += 1
            continue
        indent = len(line) - len(line.lstrip())
        if indent == 0:
            break
        stripped = line.strip()
        if ":" in stripped:
            k, _, v = stripped.partition(":")
            k = k.strip()
            v = v.strip()
            if v.startswith("[") and v.endswith("]"):
                inner = v[1:-1]
                items = [
                    x.strip().strip('"').strip("'")
                    for x in inner.split(",") if x.strip()
                ]
                out[k] = items
        i += 1
    return out, i


def _coerce_scalar(val: str) -> Any:
    s = val.strip().strip('"').strip("'")
    if s in ("true", "True"):
        return True
    if s in ("false", "False"):
        return False
    if s in ("null", "None", "~", ""):
        return None
    try:
        if "." in s or "e" in s or "E" in s:
            return float(s)
        return int(s)
    except ValueError:
        return s
