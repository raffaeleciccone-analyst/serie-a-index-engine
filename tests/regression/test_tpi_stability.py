"""
F1.4 — TPI regression stability test suite.

Compares a baseline WORM snapshot to a "candidate" snapshot representing the
*current* state of the analytics pipeline. Three resolution strategies for
the candidate, in order:

  1. `REGRESSION_CANDIDATE_DIR` env var → directory path
  2. `REGRESSION_CANDIDATE_SEASON` + `REGRESSION_CANDIDATE_GIORNATA`
  3. fall back to the baseline snapshot itself (proves the engine is
     deterministic; the only way this fails is if the engine drifts)

The suite also contains synthetic tests that *perturb* the candidate and
assert that the diff engine actually flags the perturbation — this is the
proof that the watchdog works, not just that the happy path passes.

Generated report goes under `reports/regression/` and is uploaded as a
GitHub Actions artefact.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from regression.helpers import LoadedSnapshot, load_snapshot  # noqa: E402
from serie_a_scout.analytics import (  # noqa: E402
    RegressionSeverity,
    load_thresholds,
    run_regression_diff,
    write_report,
)
from serie_a_scout.obs import emit_regression_metrics  # noqa: E402

# Questi test girano sui dati, non solo sul codice: senza, non c'e' niente
# da misurare, e fallire direbbe il falso. Si saltano dichiarando cosa
# manca — vedi regression/helpers/ambiente.py.
from regression.helpers.ambiente import senza_snapshot  # noqa: E402

pytestmark = senza_snapshot


# ─────────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────────
BASELINE_SEASON = os.environ.get("REGRESSION_BASELINE_SEASON", "2025-26")
BASELINE_GIORNATA = int(os.environ.get("REGRESSION_BASELINE_GIORNATA", "36"))
PRIMARY_DIMENSION = os.environ.get("REGRESSION_DIMENSION", "totale")


# ─────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────
@pytest.fixture(scope="module")
def thresholds():
    return load_thresholds()


@pytest.fixture(scope="module")
def baseline() -> LoadedSnapshot:
    snap = load_snapshot(BASELINE_SEASON, BASELINE_GIORNATA)
    if snap.tpi_table.empty:
        pytest.skip(f"Baseline snapshot {BASELINE_SEASON}/g{BASELINE_GIORNATA} è vuota.")
    return snap


@pytest.fixture(scope="module")
def candidate(baseline: LoadedSnapshot) -> LoadedSnapshot:
    """
    Resolve the candidate snapshot to compare against `baseline`.

    Order:
      1. `REGRESSION_CANDIDATE_DIR`
      2. `REGRESSION_CANDIDATE_SEASON` + `REGRESSION_CANDIDATE_GIORNATA`
      3. baseline (determinism check)
    """
    cand_dir = os.environ.get("REGRESSION_CANDIDATE_DIR")
    if cand_dir:
        path = Path(cand_dir)
        season, gn = _parse_snapshot_dir(path)
        return load_snapshot(season, gn, base_dir=path.parents[1])

    cand_season = os.environ.get("REGRESSION_CANDIDATE_SEASON")
    cand_giornata = os.environ.get("REGRESSION_CANDIDATE_GIORNATA")
    if cand_season and cand_giornata:
        return load_snapshot(cand_season, int(cand_giornata))

    return baseline


@pytest.fixture(scope="module")
def diff(baseline: LoadedSnapshot, candidate: LoadedSnapshot, thresholds):
    """Run the regression diff once per module and reuse it."""
    return run_regression_diff(
        baseline.tpi_table,
        candidate.tpi_table,
        season=baseline.season,
        giornata=baseline.giornata,
        dimension=PRIMARY_DIMENSION,
        thresholds=thresholds,
    )


# ─────────────────────────────────────────────────────────────────────
# Snapshot loader sanity
# ─────────────────────────────────────────────────────────────────────
def test_baseline_loader_is_deterministic(baseline):
    second = load_snapshot(BASELINE_SEASON, BASELINE_GIORNATA)
    # frame equality up to dtype-stable columns
    pd.testing.assert_frame_equal(
        baseline.tpi_table.reset_index(drop=True),
        second.tpi_table.reset_index(drop=True),
    )


def test_baseline_manifest_integrity(baseline):
    assert baseline.manifest.season == BASELINE_SEASON
    assert baseline.manifest.giornata == BASELINE_GIORNATA
    assert any(f.path == "payload.json" for f in baseline.manifest.files)


def test_baseline_contains_all_tpi_dimensions(baseline, thresholds):
    for dim in thresholds.tpi_dimensions:
        col = f"tpi_{dim}"
        assert col in baseline.tpi_table.columns, f"manca {col}"


# ─────────────────────────────────────────────────────────────────────
# Required assertions: Spearman, Top-N, Top player, Distribution
# ─────────────────────────────────────────────────────────────────────
def test_spearman_rank_correlation_above_threshold(diff, thresholds):
    assert diff.spearman == pytest.approx(diff.spearman)  # not NaN
    assert diff.spearman >= thresholds.spearman_min, (
        f"Spearman ρ={diff.spearman:.4f} < {thresholds.spearman_min}: "
        f"reasons={diff.failure_reasons}"
    )


def test_top10_overlap_above_threshold(diff, thresholds):
    assert diff.top10_overlap >= thresholds.top10_overlap_min, (
        f"Top-10 overlap {diff.top10_overlap} < {thresholds.top10_overlap_min}"
    )


def test_top20_overlap_above_threshold(diff, thresholds):
    assert diff.top20_overlap >= thresholds.top20_overlap_min, (
        f"Top-20 overlap {diff.top20_overlap} < {thresholds.top20_overlap_min}"
    )


def test_top_player_delta_within_threshold(diff, thresholds):
    if np.isnan(diff.top_player_delta):
        pytest.skip("Top player non presente nel candidate (eligibilità persa).")
    assert diff.top_player_delta <= thresholds.max_top_player_delta, (
        f"Δ top player = {diff.top_player_delta:.4f} > {thresholds.max_top_player_delta}"
    )


def test_distribution_drift_within_thresholds(diff, thresholds):
    dist = diff.distribution_shift
    for name, value, hard in [
        ("mean", dist.mean_delta_pct, thresholds.distribution_mean_delta_pct),
        ("std",  dist.std_delta_pct,  thresholds.distribution_std_delta_pct),
        ("p95",  dist.p95_delta_pct,  thresholds.distribution_p95_delta_pct),
    ]:
        if np.isnan(value):
            continue
        assert value <= hard, f"Distribuzione {name} drift {value:.2%} > {hard:.2%}"


def test_population_share_retained(diff, thresholds):
    share = diff.n_common / diff.n_baseline if diff.n_baseline else 0.0
    assert share >= thresholds.min_common_player_share, (
        f"Solo il {share:.1%} dei giocatori baseline è presente nel candidate."
    )


# ─────────────────────────────────────────────────────────────────────
# Outlier detection
# ─────────────────────────────────────────────────────────────────────
def test_outliers_are_serialisable(diff):
    payload = [
        {
            "player_id": m.player_id, "nome": m.nome, "squadra": m.squadra,
            "delta": m.delta, "rank_delta": m.rank_delta,
        }
        for m in diff.large_deltas
    ]
    assert json.dumps(payload)


# ─────────────────────────────────────────────────────────────────────
# Severity gate (the actual CI failure)
# ─────────────────────────────────────────────────────────────────────
def test_regression_is_not_critical(diff):
    assert diff.severity is not RegressionSeverity.CRITICAL, (
        "Severity CRITICAL. Reasons:\n  - " + "\n  - ".join(diff.failure_reasons)
    )


# ─────────────────────────────────────────────────────────────────────
# Report + metrics emission (always runs, even on PASS)
# ─────────────────────────────────────────────────────────────────────
def test_emit_report_and_metrics(diff):
    report_path = write_report(diff)
    assert report_path.is_file()
    assert report_path.stat().st_size > 0

    dist = diff.distribution_shift
    max_dist_shift = max(
        (v for v in [dist.mean_delta_pct, dist.std_delta_pct, dist.p95_delta_pct]
         if v is not None and not np.isnan(v)),
        default=0.0,
    )

    metric_path = emit_regression_metrics(
        season=diff.season,
        giornata=diff.giornata,
        dimension=diff.dimension,
        spearman=diff.spearman,
        top10_overlap=diff.top10_overlap,
        top20_overlap=diff.top20_overlap,
        large_deltas=len(diff.large_deltas),
        distribution_shift_pct=max_dist_shift,
        severity=diff.severity.value,
        failed=diff.severity.is_failure,
    )
    assert metric_path.is_file()


# ─────────────────────────────────────────────────────────────────────
# Synthetic perturbation tests (prove the watchdog works)
# ─────────────────────────────────────────────────────────────────────
def test_engine_flags_inverted_ranking(baseline, thresholds):
    """If we INVERT the TPI rankings the diff must be CRITICAL."""
    cand = baseline.tpi_table.copy()
    cand["tpi_totale"] = -cand["tpi_totale"]
    diff = run_regression_diff(
        baseline.tpi_table, cand,
        season=baseline.season, giornata=baseline.giornata,
        dimension="totale", thresholds=thresholds,
    )
    assert diff.severity is RegressionSeverity.CRITICAL
    assert diff.spearman < 0


def test_engine_flags_scaled_distribution(baseline, thresholds):
    """Scaling TPI by 2× must trigger distribution drift CRITICAL."""
    cand = baseline.tpi_table.copy()
    cand["tpi_totale"] = cand["tpi_totale"] * 2.0
    diff = run_regression_diff(
        baseline.tpi_table, cand,
        season=baseline.season, giornata=baseline.giornata,
        dimension="totale", thresholds=thresholds,
    )
    # ranking-wise it's a monotone transform → Spearman ~1, but distribution
    # mean/std/p95 will explode.
    assert diff.severity is RegressionSeverity.CRITICAL
    assert diff.distribution_shift.std_delta_pct > thresholds.distribution_std_delta_pct


def test_engine_flags_top_player_corruption(baseline, thresholds):
    """Bumping the top player far above its baseline must trip Δ-top-player."""
    cand = baseline.tpi_table.copy()
    if cand["tpi_totale"].dropna().empty:
        pytest.skip("Empty TPI column.")
    top_idx = cand["tpi_totale"].idxmax()
    cand.loc[top_idx, "tpi_totale"] = float(cand.loc[top_idx, "tpi_totale"]) + 1.0
    diff = run_regression_diff(
        baseline.tpi_table, cand,
        season=baseline.season, giornata=baseline.giornata,
        dimension="totale", thresholds=thresholds,
    )
    assert diff.top_player_delta > thresholds.max_top_player_delta
    assert diff.severity is RegressionSeverity.CRITICAL


def test_engine_passes_on_identity(baseline, thresholds):
    """Comparing baseline to itself MUST be SAFE — no false positives."""
    diff = run_regression_diff(
        baseline.tpi_table, baseline.tpi_table.copy(),
        season=baseline.season, giornata=baseline.giornata,
        dimension="totale", thresholds=thresholds,
    )
    assert diff.severity is RegressionSeverity.SAFE
    assert diff.spearman == pytest.approx(1.0, abs=1e-9)
    assert diff.top10_overlap == 10
    assert diff.top20_overlap == 20


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────
def _parse_snapshot_dir(path: Path) -> tuple[str, int]:
    """Expect `.../snapshots/<season>/giornata_<NN>`."""
    if not path.is_dir():
        pytest.skip(f"REGRESSION_CANDIDATE_DIR non esistente: {path}")
    if not path.name.startswith("giornata_"):
        pytest.skip(f"Path candidate non riconosciuto: {path}")
    season = path.parent.name
    try:
        giornata = int(path.name.split("_", 1)[1])
    except ValueError:
        pytest.skip(f"Path candidate non parsabile: {path}")
        raise  # unreachable, but type checker happy
    return season, giornata
