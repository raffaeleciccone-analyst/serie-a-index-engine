"""
Baseline snapshot loader for the F1.4 TPI regression suite.

A "loaded snapshot" is the in-memory projection of one WORM directory under
`snapshots/<season>/giornata_<NN>/`. We expose three views:

* `LoadedSnapshot.tpi_table` — wide pandas DataFrame keyed by `player_id`
  with one column per TPI dimension (`totale`, `casa`, …). Deterministic
  ordering by `player_id`.
* `LoadedSnapshot.players` — typed list of `PlayerTPI` for direct iteration.
* `LoadedSnapshot.parquet`   — raw parquet artefacts (giocatori, calendario,
  …) materialised as DataFrames.

Integrity is verified BEFORE any data is exposed: we call
`serie_a_scout.snapshots.verify_snapshot()` and abort with
`SnapshotLoadError` on any mismatch. This is intentional — the regression
engine must never feed a corrupted baseline into its diff.

Determinism contract:
  * rows ordered by `player_id` ascending
  * columns sorted alphabetically in every DataFrame returned
  * NaN handling: TPI values absent in the snapshot remain `NaN`, callers
    can drop them explicitly via `LoadedSnapshot.drop_missing()`.
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Iterator, Mapping

import pandas as pd

# Make the `serie_a_scout` package importable when the loader is used
# outside pytest (e.g. from a CLI report run).
_REPO_ROOT = Path(__file__).resolve().parents[3]
_SRC = _REPO_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from serie_a_scout.snapshots import Manifest, verify_snapshot  # noqa: E402


# ─────────────────────────────────────────────────────────────────────
# Errors & dataclasses
# ─────────────────────────────────────────────────────────────────────
class SnapshotLoadError(RuntimeError):
    """Raised when the snapshot is missing, corrupted or schema-incompatible."""


@dataclass(frozen=True)
class PlayerTPI:
    """One player's TPI vector inside a snapshot. Frozen for hash-safety."""
    player_id: int
    nome: str
    squadra: str
    ruolo: str
    minuti: int
    tpi_totale: float
    tpi_casa: float | None
    tpi_trasferta: float | None
    tpi_vs_top6: float | None
    tpi_vs_forti: float | None
    n_app: int | None


@dataclass
class LoadedSnapshot:
    """In-memory projection of a WORM snapshot, ready for diffing."""
    season: str
    giornata: int
    manifest: Manifest
    payload: Mapping[str, object]
    players: list[PlayerTPI]
    tpi_table: pd.DataFrame
    parquet: dict[str, pd.DataFrame] = field(default_factory=dict)
    snapshot_dir: Path = field(default_factory=Path)

    # ───────── convenience views ─────────
    def tpi_series(self, dimension: str = "totale") -> pd.Series:
        """Return TPI series indexed by player_id for one dimension."""
        col = f"tpi_{dimension}"
        if col not in self.tpi_table.columns:
            raise SnapshotLoadError(
                f"Dimensione TPI assente nello snapshot: {dimension!r} "
                f"(disponibili: {sorted(c[4:] for c in self.tpi_table.columns if c.startswith('tpi_'))})"
            )
        return self.tpi_table[col]

    def ranked_players(self, dimension: str = "totale") -> pd.DataFrame:
        """Return players sorted by TPI[dim] desc, with stable secondary key."""
        col = f"tpi_{dimension}"
        return (
            self.tpi_table
            .dropna(subset=[col])
            .sort_values(by=[col, "player_id"], ascending=[False, True])
            .reset_index(drop=True)
        )

    def drop_missing(self, dimension: str = "totale") -> "LoadedSnapshot":
        """Return a copy with rows missing the given dimension removed."""
        col = f"tpi_{dimension}"
        kept = self.tpi_table.dropna(subset=[col])
        kept_ids = set(kept["player_id"].astype(int))
        return LoadedSnapshot(
            season=self.season,
            giornata=self.giornata,
            manifest=self.manifest,
            payload=self.payload,
            players=[p for p in self.players if p.player_id in kept_ids],
            tpi_table=kept.reset_index(drop=True),
            parquet=self.parquet,
            snapshot_dir=self.snapshot_dir,
        )


# ─────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────
def snapshot_dir_for(season: str, giornata: int, base_dir: Path | str | None = None) -> Path:
    """Resolve the canonical snapshot directory for a (season, giornata)."""
    base = Path(base_dir) if base_dir else _REPO_ROOT / "snapshots"
    return base / season / f"giornata_{int(giornata):02d}"


def load_snapshot(
    season: str = "2025-26",
    giornata: int = 36,
    *,
    base_dir: Path | str | None = None,
    load_parquet: bool = True,
    verify: bool = True,
) -> LoadedSnapshot:
    """
    Load and (optionally) verify a WORM snapshot.

    Parameters
    ----------
    season, giornata
        Snapshot coordinates (default points at the latest known baseline).
    base_dir
        Override the `snapshots/` root. Useful in tests.
    load_parquet
        When False, skip materialising parquet tables (faster for diff-only
        runs that only need TPI vectors).
    verify
        When False, skip checksum verification. ONLY for unit tests of the
        loader itself; production callers must keep verify=True.
    """
    snap = snapshot_dir_for(season, giornata, base_dir)
    if not snap.is_dir():
        raise SnapshotLoadError(f"Snapshot directory mancante: {snap}")

    if verify:
        vr = verify_snapshot(snap)
        if not vr.ok:
            raise SnapshotLoadError(
                "Verifica integrità fallita per snapshot "
                f"{season}/giornata_{giornata:02d}:\n  - "
                + "\n  - ".join(vr.errors)
            )
        manifest = vr.manifest
    else:
        manifest_path = snap / "manifest.json"
        if not manifest_path.is_file():
            raise SnapshotLoadError(f"manifest.json mancante in {snap}")
        manifest = Manifest.from_json(manifest_path.read_text(encoding="utf-8"))

    if manifest is None:
        raise SnapshotLoadError(f"Manifest non leggibile in {snap}")

    payload = _load_payload(snap)
    players = _materialise_players(payload)
    tpi_table = _players_to_dataframe(players)

    parquet: dict[str, pd.DataFrame] = {}
    if load_parquet:
        for entry in manifest.files:
            if entry.table is None:
                continue
            df = pd.read_parquet(snap / entry.path)
            df = df.reindex(sorted(df.columns), axis=1)
            parquet[entry.table] = df

    return LoadedSnapshot(
        season=manifest.season,
        giornata=manifest.giornata,
        manifest=manifest,
        payload=payload,
        players=players,
        tpi_table=tpi_table,
        parquet=parquet,
        snapshot_dir=snap,
    )


def iter_snapshots(
    season: str | None = None,
    *,
    base_dir: Path | str | None = None,
) -> Iterator[tuple[str, int, Path]]:
    """
    Enumerate every available snapshot, yielding `(season, giornata, dir)`.

    Designed for future multi-season regression (P9 future-proofing).
    """
    base = Path(base_dir) if base_dir else _REPO_ROOT / "snapshots"
    if not base.is_dir():
        return
    for season_dir in sorted(p for p in base.iterdir() if p.is_dir()):
        if season is not None and season_dir.name != season:
            continue
        for gdir in sorted(season_dir.iterdir()):
            if not gdir.is_dir():
                continue
            if not gdir.name.startswith("giornata_"):
                continue
            try:
                gn = int(gdir.name.split("_", 1)[1])
            except ValueError:
                continue
            yield season_dir.name, gn, gdir


# ─────────────────────────────────────────────────────────────────────
# Internals
# ─────────────────────────────────────────────────────────────────────
def _load_payload(snap: Path) -> Mapping[str, object]:
    payload_path = snap / "payload.json"
    if not payload_path.is_file():
        raise SnapshotLoadError(f"payload.json mancante in {snap}")
    try:
        return json.loads(payload_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise SnapshotLoadError(f"payload.json corrotto: {e}") from e


def _materialise_players(payload: Mapping[str, object]) -> list[PlayerTPI]:
    raw_players = payload.get("players")
    if not isinstance(raw_players, list):
        raise SnapshotLoadError("payload.players missing or not a list")

    out: list[PlayerTPI] = []
    for rec in raw_players:
        if not isinstance(rec, dict):
            continue
        tpi = rec.get("tpi") or {}
        ctx_tot = (rec.get("ctx") or {}).get("totale") or {}
        try:
            out.append(PlayerTPI(
                player_id=int(rec["id"]),
                nome=str(rec.get("nome", "")),
                squadra=str(rec.get("squadra", "")),
                ruolo=str(rec.get("ruolo", "")),
                minuti=int(rec.get("minuti") or 0),
                tpi_totale=_safe_float(tpi.get("totale")),
                tpi_casa=_safe_float_or_none(tpi.get("casa")),
                tpi_trasferta=_safe_float_or_none(tpi.get("trasferta")),
                tpi_vs_top6=_safe_float_or_none(tpi.get("vs_top6")),
                tpi_vs_forti=_safe_float_or_none(tpi.get("vs_forti")),
                n_app=_safe_int_or_none(ctx_tot.get("n_app")),
            ))
        except (KeyError, TypeError, ValueError) as e:
            raise SnapshotLoadError(
                f"player record malformato: {rec!r} ({e})"
            ) from e

    # deterministic ordering
    out.sort(key=lambda p: p.player_id)
    return out


def _players_to_dataframe(players: Iterable[PlayerTPI]) -> pd.DataFrame:
    rows = [{
        "player_id":    p.player_id,
        "nome":         p.nome,
        "squadra":      p.squadra,
        "ruolo":        p.ruolo,
        "minuti":       p.minuti,
        "n_app":        p.n_app,
        "tpi_totale":   p.tpi_totale,
        "tpi_casa":     p.tpi_casa,
        "tpi_trasferta": p.tpi_trasferta,
        "tpi_vs_top6":  p.tpi_vs_top6,
        "tpi_vs_forti": p.tpi_vs_forti,
    } for p in players]
    df = pd.DataFrame(rows)
    if df.empty:
        return df.reindex(sorted(df.columns), axis=1)
    df = df.sort_values("player_id").reset_index(drop=True)
    return df.reindex(sorted(df.columns), axis=1)


def _safe_float(v: object) -> float:
    if v is None:
        return float("nan")
    try:
        return float(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return float("nan")


def _safe_float_or_none(v: object) -> float | None:
    if v is None:
        return None
    try:
        return float(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _safe_int_or_none(v: object) -> int | None:
    if v is None:
        return None
    try:
        return int(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
