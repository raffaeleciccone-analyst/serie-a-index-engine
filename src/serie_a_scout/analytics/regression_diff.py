"""
TPI regression diff engine.

Given two `LoadedSnapshot`-shaped views (baseline vs candidate), the engine
quantifies how much the TPI distribution has moved. Outputs are:

* a `RegressionDiff` dataclass (JSON/Markdown ready)
* a severity classification (`SAFE` | `WARNING` | `CRITICAL`)
* per-player breakdowns for the largest movers

The engine is pytest-agnostic on purpose — the same code path is used by
the CI report generator and by ad-hoc analyst notebooks.

Design notes:
  * we never touch the live database; both inputs are pre-loaded TPI tables.
  * comparisons are computed on the *intersection* of player ids in the two
    snapshots. The size of that intersection is itself a guarded metric.
  * Spearman correlation degrades gracefully if scipy isn't installed
    (we fall back to a numpy implementation that matches scipy on ranks
    without ties; with ties it differs by O(1e-6) at most).
  * thresholds are read from `config/regression.yml` but can be overridden
    programmatically for ad-hoc / future per-league runs.
"""
from __future__ import annotations

import math
import statistics
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import numpy as np
import pandas as pd


# ─────────────────────────────────────────────────────────────────────
# Severity enum
# ─────────────────────────────────────────────────────────────────────
class RegressionSeverity(str, Enum):
    """Drift classification used by reports, metrics and CI gating."""
    SAFE = "SAFE"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"

    @property
    def is_failure(self) -> bool:
        return self is RegressionSeverity.CRITICAL


# ─────────────────────────────────────────────────────────────────────
# Threshold configuration
# ─────────────────────────────────────────────────────────────────────
_DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[3] / "config" / "regression.yml"


@dataclass
class RegressionThresholds:
    """Decoded contents of `config/regression.yml`. Defaults match the file."""
    schema_version: str = "1.0.0"

    spearman_min: float = 0.95
    spearman_warn: float = 0.97

    top10_overlap_min: int = 8
    top20_overlap_min: int = 16
    top10_overlap_warn: int = 9
    top20_overlap_warn: int = 18

    max_top_player_delta: float = 0.05
    top_player_delta_warn: float = 0.03

    distribution_mean_delta_pct: float = 0.10
    distribution_std_delta_pct: float = 0.15
    distribution_p95_delta_pct: float = 0.15
    distribution_mean_delta_pct_warn: float = 0.05
    distribution_std_delta_pct_warn: float = 0.08
    distribution_p95_delta_pct_warn: float = 0.08

    large_delta_threshold: float = 0.15
    large_delta_share_warn: float = 0.05
    large_delta_share_critical: float = 0.10

    min_common_player_share: float = 0.90

    tpi_dimensions: tuple[str, ...] = ("totale", "casa", "trasferta", "vs_top6", "vs_forti")
    leagues: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)


def load_thresholds(path: Path | str | None = None) -> RegressionThresholds:
    """
    Load thresholds from YAML (PyYAML if available; otherwise from defaults).

    A missing file is NOT an error — we fall back to dataclass defaults so
    the regression suite can run in minimal environments. A malformed file
    IS an error.
    """
    path = Path(path) if path else _DEFAULT_CONFIG_PATH
    if not path.is_file():
        return RegressionThresholds()

    text = path.read_text(encoding="utf-8")
    try:
        import yaml  # type: ignore
        raw = yaml.safe_load(text) or {}
    except ImportError:
        raw = _minimal_yaml_parse(text)

    if not isinstance(raw, dict):
        raise ValueError(f"{path} non è un mapping YAML")

    init: dict[str, Any] = {}
    for f_name in RegressionThresholds.__dataclass_fields__:
        if f_name in raw:
            init[f_name] = raw[f_name]
    if "tpi_dimensions" in init and isinstance(init["tpi_dimensions"], list):
        init["tpi_dimensions"] = tuple(init["tpi_dimensions"])
    if "leagues" in init and init["leagues"] is None:
        init["leagues"] = {}
    return RegressionThresholds(**init)


# ─────────────────────────────────────────────────────────────────────
# Result dataclasses
# ─────────────────────────────────────────────────────────────────────
@dataclass
class MovedPlayer:
    player_id: int
    nome: str
    squadra: str
    old_tpi: float
    new_tpi: float
    delta: float
    old_rank: int
    new_rank: int
    rank_delta: int


@dataclass
class DistributionShift:
    mean_old: float
    mean_new: float
    mean_delta_pct: float
    std_old: float
    std_new: float
    std_delta_pct: float
    p95_old: float
    p95_new: float
    p95_delta_pct: float


@dataclass
class RegressionDiff:
    season: str
    giornata: int
    dimension: str
    n_baseline: int
    n_candidate: int
    n_common: int
    spearman: float
    top10_overlap: int
    top20_overlap: int
    top_player_delta: float
    top_player_id: int | None
    distribution_shift: DistributionShift
    large_deltas: list[MovedPlayer]
    biggest_climbers: list[MovedPlayer]
    biggest_fallers: list[MovedPlayer]
    severity: RegressionSeverity
    failure_reasons: list[str]
    warning_reasons: list[str]
    thresholds: RegressionThresholds

    @property
    def passed(self) -> bool:
        return not self.severity.is_failure

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["severity"] = self.severity.value
        return d


# ─────────────────────────────────────────────────────────────────────
# Public API — composable building blocks
# ─────────────────────────────────────────────────────────────────────
def compute_spearman(
    old_values: Sequence[float] | pd.Series,
    new_values: Sequence[float] | pd.Series,
) -> float:
    """
    Spearman rank correlation. Uses scipy if installed, otherwise a
    numpy implementation. Returns NaN if fewer than 2 finite samples.
    """
    a = np.asarray(old_values, dtype=float)
    b = np.asarray(new_values, dtype=float)
    if a.shape != b.shape:
        raise ValueError(f"shape mismatch: {a.shape} vs {b.shape}")
    mask = np.isfinite(a) & np.isfinite(b)
    if mask.sum() < 2:
        return float("nan")
    a, b = a[mask], b[mask]
    try:
        from scipy.stats import spearmanr  # type: ignore
        rho, _ = spearmanr(a, b)
        return float(rho)
    except ImportError:
        return float(_numpy_spearman(a, b))


def compare_rankings(
    baseline: pd.DataFrame,
    candidate: pd.DataFrame,
    *,
    dimension: str = "totale",
    top_n: int = 10,
) -> int:
    """
    Top-N overlap (cardinality of the intersection of the two top-N sets).

    Each dataframe must carry `player_id` and `tpi_<dimension>`. Stable
    secondary sort key on `player_id` to make ties deterministic.
    """
    col = f"tpi_{dimension}"
    base_top = _top_player_ids(baseline, col, top_n)
    cand_top = _top_player_ids(candidate, col, top_n)
    return len(base_top & cand_top)


def detect_large_movements(
    baseline: pd.DataFrame,
    candidate: pd.DataFrame,
    *,
    dimension: str = "totale",
    threshold: float = 0.15,
) -> list[MovedPlayer]:
    """Return every player whose |Δ TPI| exceeds `threshold`, sorted by |Δ| desc."""
    merged = _merge_for_diff(baseline, candidate, dimension)
    big = merged[merged["abs_delta"] >= float(threshold)]
    big = big.sort_values("abs_delta", ascending=False)
    return [_row_to_moved(row) for _, row in big.iterrows()]


def summarize_distribution_changes(
    baseline: pd.DataFrame,
    candidate: pd.DataFrame,
    *,
    dimension: str = "totale",
) -> DistributionShift:
    """Mean/std/p95 of the TPI populations + relative deltas."""
    col = f"tpi_{dimension}"
    a = baseline[col].dropna().to_numpy(dtype=float)
    b = candidate[col].dropna().to_numpy(dtype=float)
    return DistributionShift(
        mean_old=_safe_float(np.mean(a) if a.size else float("nan")),
        mean_new=_safe_float(np.mean(b) if b.size else float("nan")),
        mean_delta_pct=_relative_change(np.mean(a) if a.size else float("nan"),
                                        np.mean(b) if b.size else float("nan")),
        std_old=_safe_float(np.std(a, ddof=0) if a.size else float("nan")),
        std_new=_safe_float(np.std(b, ddof=0) if b.size else float("nan")),
        std_delta_pct=_relative_change(np.std(a, ddof=0) if a.size else float("nan"),
                                       np.std(b, ddof=0) if b.size else float("nan")),
        p95_old=_safe_float(np.percentile(a, 95) if a.size else float("nan")),
        p95_new=_safe_float(np.percentile(b, 95) if b.size else float("nan")),
        p95_delta_pct=_relative_change(np.percentile(a, 95) if a.size else float("nan"),
                                       np.percentile(b, 95) if b.size else float("nan")),
    )


def explain_top_deltas(
    baseline: pd.DataFrame,
    candidate: pd.DataFrame,
    *,
    dimension: str = "totale",
    n: int = 10,
) -> tuple[list[MovedPlayer], list[MovedPlayer]]:
    """Return (biggest_climbers, biggest_fallers) ordered by signed rank delta."""
    merged = _merge_for_diff(baseline, candidate, dimension)
    if merged.empty:
        return [], []
    climbers_df = merged.sort_values("rank_delta", ascending=True).head(n)   # negative rank_delta = climbed
    fallers_df = merged.sort_values("rank_delta", ascending=False).head(n)
    climbers = [_row_to_moved(r) for _, r in climbers_df.iterrows()]
    fallers = [_row_to_moved(r) for _, r in fallers_df.iterrows()]
    return climbers, fallers


def run_regression_diff(
    baseline_table: pd.DataFrame,
    candidate_table: pd.DataFrame,
    *,
    season: str,
    giornata: int,
    dimension: str = "totale",
    thresholds: RegressionThresholds | None = None,
) -> RegressionDiff:
    """
    Run the full regression diff and classify severity.

    `baseline_table` / `candidate_table` must follow the schema produced by
    `tests/regression/helpers/load_snapshot.py::LoadedSnapshot.tpi_table`.
    """
    th = thresholds or load_thresholds()
    col = f"tpi_{dimension}"
    if col not in baseline_table.columns or col not in candidate_table.columns:
        raise ValueError(f"colonna {col} mancante in baseline o candidate")

    base = baseline_table.dropna(subset=[col]).copy()
    cand = candidate_table.dropna(subset=[col]).copy()

    merged = _merge_for_diff(base, cand, dimension)

    spearman = compute_spearman(merged["old_tpi"], merged["new_tpi"]) if len(merged) >= 2 else float("nan")
    top10 = compare_rankings(base, cand, dimension=dimension, top_n=10)
    top20 = compare_rankings(base, cand, dimension=dimension, top_n=20)

    top_player_id, top_player_delta = _top_player_drift(base, cand, dimension)

    dist = summarize_distribution_changes(base, cand, dimension=dimension)

    large = [
        m for m in detect_large_movements(
            base, cand, dimension=dimension, threshold=th.large_delta_threshold,
        )
    ]
    climbers, fallers = explain_top_deltas(base, cand, dimension=dimension, n=10)

    severity, fail_reasons, warn_reasons = _classify(
        thresholds=th,
        spearman=spearman,
        top10=top10,
        top20=top20,
        top_player_delta=top_player_delta,
        distribution=dist,
        n_baseline=len(base),
        n_candidate=len(cand),
        n_common=len(merged),
        n_large=len(large),
    )

    return RegressionDiff(
        season=season,
        giornata=giornata,
        dimension=dimension,
        n_baseline=len(base),
        n_candidate=len(cand),
        n_common=len(merged),
        spearman=spearman,
        top10_overlap=top10,
        top20_overlap=top20,
        top_player_delta=top_player_delta,
        top_player_id=top_player_id,
        distribution_shift=dist,
        large_deltas=large,
        biggest_climbers=climbers,
        biggest_fallers=fallers,
        severity=severity,
        failure_reasons=fail_reasons,
        warning_reasons=warn_reasons,
        thresholds=th,
    )


# ─────────────────────────────────────────────────────────────────────
# Internals
# ─────────────────────────────────────────────────────────────────────
def _merge_for_diff(
    baseline: pd.DataFrame,
    candidate: pd.DataFrame,
    dimension: str,
) -> pd.DataFrame:
    col = f"tpi_{dimension}"
    base = baseline[["player_id", "nome", "squadra", col]].rename(columns={col: "old_tpi"})
    cand = candidate[["player_id", "nome", "squadra", col]].rename(columns={col: "new_tpi"})

    base = base.dropna(subset=["old_tpi"]).copy()
    cand = cand.dropna(subset=["new_tpi"]).copy()

    base["old_rank"] = (
        base["old_tpi"].rank(method="min", ascending=False).astype(int)
    )
    cand["new_rank"] = (
        cand["new_tpi"].rank(method="min", ascending=False).astype(int)
    )

    merged = base.merge(
        cand[["player_id", "new_tpi", "new_rank"]],
        on="player_id", how="inner", validate="one_to_one",
    )
    merged["delta"] = merged["new_tpi"] - merged["old_tpi"]
    merged["abs_delta"] = merged["delta"].abs()
    # rank_delta < 0 → climber, > 0 → faller (smaller rank = better)
    merged["rank_delta"] = merged["new_rank"] - merged["old_rank"]
    return merged.sort_values("player_id").reset_index(drop=True)


def _top_player_ids(df: pd.DataFrame, col: str, n: int) -> set[int]:
    if df.empty or col not in df.columns:
        return set()
    top = (
        df.dropna(subset=[col])
        .sort_values(by=[col, "player_id"], ascending=[False, True])
        .head(n)
    )
    return set(int(x) for x in top["player_id"].tolist())


def _top_player_drift(
    baseline: pd.DataFrame, candidate: pd.DataFrame, dimension: str,
) -> tuple[int | None, float]:
    col = f"tpi_{dimension}"
    if baseline.empty:
        return None, 0.0
    ranked = baseline.dropna(subset=[col]).sort_values(
        by=[col, "player_id"], ascending=[False, True]
    )
    if ranked.empty:
        return None, 0.0
    top_id = int(ranked.iloc[0]["player_id"])
    old_v = float(ranked.iloc[0][col])
    match = candidate[candidate["player_id"] == top_id]
    if match.empty or pd.isna(match.iloc[0][col]):
        return top_id, float("nan")
    new_v = float(match.iloc[0][col])
    return top_id, abs(new_v - old_v)


def _row_to_moved(row: pd.Series) -> MovedPlayer:
    return MovedPlayer(
        player_id=int(row["player_id"]),
        nome=str(row.get("nome", "")),
        squadra=str(row.get("squadra", "")),
        old_tpi=_safe_float(row["old_tpi"]),
        new_tpi=_safe_float(row["new_tpi"]),
        delta=_safe_float(row["delta"]),
        old_rank=int(row["old_rank"]),
        new_rank=int(row["new_rank"]),
        rank_delta=int(row["rank_delta"]),
    )


def _classify(
    *,
    thresholds: RegressionThresholds,
    spearman: float,
    top10: int,
    top20: int,
    top_player_delta: float,
    distribution: DistributionShift,
    n_baseline: int,
    n_candidate: int,
    n_common: int,
    n_large: int,
) -> tuple[RegressionSeverity, list[str], list[str]]:
    th = thresholds
    fail: list[str] = []
    warn: list[str] = []

    # population guard
    base_share = (n_common / n_baseline) if n_baseline else 0.0
    if n_baseline > 0 and base_share < th.min_common_player_share:
        fail.append(
            f"Solo il {base_share:.1%} dei giocatori baseline è presente nel candidate "
            f"(soglia minima {th.min_common_player_share:.0%}). "
            f"Possibile corruzione ingestion."
        )

    # spearman
    if math.isnan(spearman):
        fail.append("Spearman non calcolabile (campione < 2).")
    else:
        if spearman < th.spearman_min:
            fail.append(f"Spearman ρ={spearman:.4f} < soglia critica {th.spearman_min}.")
        elif spearman < th.spearman_warn:
            warn.append(f"Spearman ρ={spearman:.4f} < soglia warning {th.spearman_warn}.")

    # top-N
    if top10 < th.top10_overlap_min:
        fail.append(f"Top-10 overlap {top10}/10 < {th.top10_overlap_min}.")
    elif top10 < th.top10_overlap_warn:
        warn.append(f"Top-10 overlap {top10}/10 < {th.top10_overlap_warn} (warning).")

    if top20 < th.top20_overlap_min:
        fail.append(f"Top-20 overlap {top20}/20 < {th.top20_overlap_min}.")
    elif top20 < th.top20_overlap_warn:
        warn.append(f"Top-20 overlap {top20}/20 < {th.top20_overlap_warn} (warning).")

    # top player
    if not math.isnan(top_player_delta):
        if top_player_delta > th.max_top_player_delta:
            fail.append(
                f"Δ top player = {top_player_delta:.4f} > {th.max_top_player_delta}."
            )
        elif top_player_delta > th.top_player_delta_warn:
            warn.append(
                f"Δ top player = {top_player_delta:.4f} > {th.top_player_delta_warn} (warning)."
            )

    # distribution
    def _check(name: str, value: float, hard: float, soft: float) -> None:
        if math.isnan(value):
            return
        if value > hard:
            fail.append(f"Distribuzione {name} drift {value:.2%} > {hard:.2%}.")
        elif value > soft:
            warn.append(f"Distribuzione {name} drift {value:.2%} > {soft:.2%} (warning).")

    _check("mean", distribution.mean_delta_pct, th.distribution_mean_delta_pct, th.distribution_mean_delta_pct_warn)
    _check("std", distribution.std_delta_pct,  th.distribution_std_delta_pct,  th.distribution_std_delta_pct_warn)
    _check("p95", distribution.p95_delta_pct,  th.distribution_p95_delta_pct,  th.distribution_p95_delta_pct_warn)

    # large delta share
    if n_common:
        share = n_large / n_common
        if share > th.large_delta_share_critical:
            fail.append(
                f"Quota giocatori con |Δ|>{th.large_delta_threshold}: "
                f"{share:.1%} > {th.large_delta_share_critical:.0%}."
            )
        elif share > th.large_delta_share_warn:
            warn.append(
                f"Quota giocatori con |Δ|>{th.large_delta_threshold}: "
                f"{share:.1%} > {th.large_delta_share_warn:.0%} (warning)."
            )

    if fail:
        return RegressionSeverity.CRITICAL, fail, warn
    if warn:
        return RegressionSeverity.WARNING, fail, warn
    return RegressionSeverity.SAFE, fail, warn


def _safe_float(v: Any) -> float:
    try:
        f = float(v)
        return f if math.isfinite(f) else float("nan")
    except (TypeError, ValueError):
        return float("nan")


def _relative_change(old: float, new: float) -> float:
    if not math.isfinite(old) or not math.isfinite(new):
        return float("nan")
    if abs(old) < 1e-12:
        return 0.0 if abs(new) < 1e-12 else float("inf")
    return abs(new - old) / abs(old)


def _numpy_spearman(a: np.ndarray, b: np.ndarray) -> float:
    """Fallback Spearman using ranks + Pearson; handles ties via average rank."""
    ra = _avg_rank(a)
    rb = _avg_rank(b)
    if ra.std() == 0 or rb.std() == 0:
        return float("nan")
    return float(np.corrcoef(ra, rb)[0, 1])


def _avg_rank(arr: np.ndarray) -> np.ndarray:
    order = np.argsort(arr, kind="mergesort")
    ranks = np.empty_like(order, dtype=float)
    ranks[order] = np.arange(1, len(arr) + 1)
    # average across ties
    unique_vals, inverse, counts = np.unique(arr, return_inverse=True, return_counts=True)
    sums = np.zeros_like(unique_vals, dtype=float)
    np.add.at(sums, inverse, ranks)
    averages = sums / counts
    return averages[inverse]


# ─────────────────────────────────────────────────────────────────────
# Minimal YAML parser (last-resort fallback when PyYAML unavailable)
# ─────────────────────────────────────────────────────────────────────
def _minimal_yaml_parse(text: str) -> dict[str, Any]:
    """
    Parse the *exact* shape of `config/regression.yml`. Supports:
      key: scalar
      key:
        - item
        - item
      key: {}
    No anchors, no nesting beyond a single list. Intentional — this is a
    last-resort fallback used only in CI environments without PyYAML.
    """
    out: dict[str, Any] = {}
    current_key: str | None = None
    current_list: list[Any] | None = None

    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        if line.startswith("  - "):
            if current_list is None or current_key is None:
                continue
            current_list.append(_coerce_scalar(line[4:].strip()))
            continue
        # top-level key
        if current_key is not None and current_list is not None:
            out[current_key] = current_list
            current_list = None
            current_key = None

        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        key = key.strip()
        val = val.strip()
        if val == "" or val == "{}":
            current_key = key
            current_list = [] if val == "" else None
            if val == "{}":
                out[key] = {}
                current_key = None
            continue
        out[key] = _coerce_scalar(val)

    if current_key is not None and current_list is not None:
        out[current_key] = current_list
    return out


def _coerce_scalar(val: str) -> Any:
    s = val.strip().strip('"').strip("'")
    if s in ("true", "True"):
        return True
    if s in ("false", "False"):
        return False
    if s in ("null", "None", "~"):
        return None
    try:
        if "." in s or "e" in s or "E" in s:
            return float(s)
        return int(s)
    except ValueError:
        return s
