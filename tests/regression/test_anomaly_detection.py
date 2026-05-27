"""
F1.5 anomaly-detection regression tests.

Coverage targets (≥20 tests):

  baseline math ─ rolling window correctness, edge cases
  detectors ───── z-score spike, stale snapshot, quarantine growth,
                  regression warning rate, regression frequency escalation,
                  TPI distribution drift, missing data, run failure burst
  rollup ──────── severity selection, health score
  report ──────── deterministic rendering, lineage references present
  metrics ─────── prom file written with expected metric families
  runner ──────── exit codes (SAFE/WARN/CRITICAL), --fail-on-critical
  retention ───── dry-run by default, safe-roots enforcement
  false-positive ─ stable baseline produces no spurious anomalies
  lineage ──────── anomaly.run_ids are populated from store
"""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from serie_a_scout.analytics import (  # noqa: E402
    AnomalyEngine, AnomalyEngineConfig, AnomalyReport, AnomalySeverity,
    AnomalyThresholds, AnomalyType, BaselineWindow, DetectedAnomaly,
    compute_baseline, render_anomaly_report, rolling_window,
    write_anomaly_report,
)
from serie_a_scout.analytics.anomaly_engine import (  # noqa: E402
    _parse_regression_max_shift, _compute_health,
)
from serie_a_scout.core import LineageStore, RunContext  # noqa: E402
from serie_a_scout.core.lineage_store import STATUS_FAIL, STATUS_OK, STATUS_PARTIAL  # noqa: E402
from serie_a_scout.core.retention import (  # noqa: E402
    prune_old_lineage, prune_old_metrics, prune_old_quarantine,
)
from serie_a_scout.obs.anomaly_metrics import emit_anomaly_metrics  # noqa: E402


FIXED_NOW = datetime(2026, 5, 16, 12, 0, 0, tzinfo=timezone.utc)


# ─────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────
@pytest.fixture
def store(tmp_path):
    s = LineageStore(tmp_path / "lineage.db")
    yield s
    s.close()


@pytest.fixture
def thresholds():
    """Stable thresholds independent of config/anomaly.yml on disk."""
    return AnomalyThresholds(
        window_days=14,
        min_samples=5,
        duration_zscore_warn=2.5,
        duration_zscore_critical=4.0,
        failure_burst_zscore_warn=2.5,
        failure_burst_zscore_critical=4.0,
        snapshot_staleness_warn_hours=36,
        snapshot_staleness_critical_hours=96,
        quarantine_growth_warn=20,
        quarantine_growth_critical=100,
        regression_warn_share_warn=0.20,
        regression_warn_share_critical=0.50,
        tpi_drift_warn_pct=0.08,
        tpi_drift_critical_pct=0.15,
        expected_runs_per_window={"snapshot": 4, "regression": 4},
        health_deduction={"CRITICAL": 20, "WARN": 5, "INFO": 1},
    )


@pytest.fixture
def engine(store, thresholds, tmp_path):
    """
    Engine pointing at empty snapshot/quarantine/report dirs by default.
    Tests can write files into these dirs to trigger detectors.
    """
    cfg = AnomalyEngineConfig(
        snapshot_root=tmp_path / "snapshots",
        quarantine_dir=tmp_path / "quarantine",
        regression_report_dir=tmp_path / "regression_reports",
    )
    cfg.snapshot_root.mkdir(parents=True, exist_ok=True)
    cfg.quarantine_dir.mkdir(parents=True, exist_ok=True)
    cfg.regression_report_dir.mkdir(parents=True, exist_ok=True)
    return AnomalyEngine(
        store=store, thresholds=thresholds, config=cfg,
        now=lambda: FIXED_NOW,
    )


def _seed_runs(
    store: LineageStore, *, stage: str, durations: list[float],
    statuses: list[str] | None = None,
    start_offset_minutes: int = 0,
) -> list[str]:
    """Seed `pipeline_run` rows with given durations (seconds) and statuses."""
    rids: list[str] = []
    statuses = statuses or ([STATUS_OK] * len(durations))
    base = FIXED_NOW - timedelta(days=10)
    for i, (d, st) in enumerate(zip(durations, statuses)):
        started = base + timedelta(minutes=start_offset_minutes + i * 60)
        rec = store.start_run(
            stage=stage,
            started_at=started.isoformat(timespec="seconds"),
        )
        ended = started + timedelta(seconds=d)
        store.finish_run(
            rec.run_id, status=st, exit_code=(0 if st == STATUS_OK else 1),
            ended_at=ended.isoformat(timespec="seconds"),
        )
        rids.append(rec.run_id)
    return rids


# ─────────────────────────────────────────────────────────────────────
# Baseline math
# ─────────────────────────────────────────────────────────────────────
def test_rolling_window_basic():
    out = rolling_window([1, 2, 3, 4, 5], window=3)
    assert out == pytest.approx([1.0, 1.5, 2.0, 3.0, 4.0])


def test_rolling_window_window_one_returns_identity():
    out = rolling_window([1.5, 2.5, 3.5], window=1)
    assert out == [1.5, 2.5, 3.5]


def test_rolling_window_rejects_zero_window():
    with pytest.raises(ValueError):
        rolling_window([1, 2, 3], window=0)


def test_compute_baseline_skips_nonfinite():
    b = compute_baseline(
        [1.0, 2.0, float("nan"), 3.0, float("inf")],
        metric="x", window_days=14, min_samples=2,
    )
    assert b.n_samples == 3
    assert b.mean == pytest.approx(2.0)
    assert b.is_usable is True


def test_baseline_window_zscore_on_unusable_returns_zero():
    b = BaselineWindow(
        metric="x", n_samples=1, mean=0, std=0, p50=0, p95=0,
        window_days=14, min_samples_required=5,
    )
    assert b.is_usable is False
    assert b.zscore(99.0) == 0.0


# ─────────────────────────────────────────────────────────────────────
# Detectors — z-score spike
# ─────────────────────────────────────────────────────────────────────
def test_engine_flags_duration_zscore_spike(engine, store):
    # 6 quick runs (with some variance) + 1 huge spike → z >> threshold
    _seed_runs(store, stage="snapshot",
               durations=[1.0, 1.1, 0.9, 1.05, 0.95, 1.02, 60.0])
    _seed_runs(store, stage="regression",
               durations=[1.0, 1.05, 0.95, 1.02, 0.98])
    report = engine.scan()
    spikes = [a for a in report.anomalies
              if a.type is AnomalyType.DURATION_ZSCORE_SPIKE]
    assert len(spikes) >= 1
    assert spikes[0].severity is AnomalySeverity.CRITICAL
    assert spikes[0].stage == "snapshot"
    assert len(spikes[0].run_ids) == 1
    assert spikes[0].suggested_action  # non-empty
    assert "z_score" in spikes[0].evidence


def test_stable_baseline_produces_no_spike(engine, store):
    # All durations within a tight band → no z-score anomaly
    _seed_runs(store, stage="snapshot", durations=[2.0, 2.1, 1.9, 2.0, 2.05, 2.02, 1.98])
    _seed_runs(store, stage="regression", durations=[1.0, 1.1, 0.9, 1.0, 1.05])
    report = engine.scan()
    spikes = [a for a in report.anomalies
              if a.type is AnomalyType.DURATION_ZSCORE_SPIKE]
    assert spikes == []


# ─────────────────────────────────────────────────────────────────────
# Detectors — stale snapshot
# ─────────────────────────────────────────────────────────────────────
def test_stale_snapshot_detected(engine, tmp_path):
    season = engine.cfg.snapshot_root / "2025-26"
    gd = season / "giornata_36"
    gd.mkdir(parents=True)
    # Force mtime to be older than warn threshold (36h)
    old = (FIXED_NOW - timedelta(hours=72)).timestamp()
    import os
    os.utime(gd, (old, old))
    report = engine.scan()
    stale = [a for a in report.anomalies if a.type is AnomalyType.STALE_SNAPSHOT]
    assert len(stale) == 1
    assert stale[0].severity is AnomalySeverity.WARN
    assert stale[0].evidence["season"] == "2025-26"
    assert stale[0].evidence["age_hours"] >= 36
    assert "giornata_36" in stale[0].artifact_path


def test_fresh_snapshot_does_not_fire(engine, tmp_path):
    season = engine.cfg.snapshot_root / "2025-26"
    gd = season / "giornata_36"
    gd.mkdir(parents=True)
    # mtime is "now" → no stale anomaly
    fresh = FIXED_NOW.timestamp()
    import os
    os.utime(gd, (fresh, fresh))
    report = engine.scan()
    stale = [a for a in report.anomalies if a.type is AnomalyType.STALE_SNAPSHOT]
    assert stale == []


# ─────────────────────────────────────────────────────────────────────
# Detectors — quarantine growth
# ─────────────────────────────────────────────────────────────────────
def test_quarantine_explosion_detected(engine):
    qf = engine.cfg.quarantine_dir / "player_resolution.jsonl"
    # 150 fresh lines → exceeds CRITICAL threshold (100)
    lines = []
    for i in range(150):
        ts = (FIXED_NOW - timedelta(hours=1)).isoformat(timespec="seconds")
        lines.append(json.dumps({"ts": ts, "kind": "AMBIGUOUS", "name": f"P{i}"}))
    qf.write_text("\n".join(lines), encoding="utf-8")
    report = engine.scan()
    qs = [a for a in report.anomalies if a.type is AnomalyType.QUARANTINE_GROWTH]
    assert len(qs) == 1
    assert qs[0].severity is AnomalySeverity.CRITICAL
    assert qs[0].value == 150
    assert qs[0].artifact_path.endswith("player_resolution.jsonl")


def test_quarantine_below_threshold_no_anomaly(engine):
    qf = engine.cfg.quarantine_dir / "player_resolution.jsonl"
    lines = []
    for i in range(5):
        ts = FIXED_NOW.isoformat(timespec="seconds")
        lines.append(json.dumps({"ts": ts, "kind": "AMBIGUOUS"}))
    qf.write_text("\n".join(lines), encoding="utf-8")
    report = engine.scan()
    qs = [a for a in report.anomalies if a.type is AnomalyType.QUARANTINE_GROWTH]
    assert qs == []


# ─────────────────────────────────────────────────────────────────────
# Detectors — regression warning rate
# ─────────────────────────────────────────────────────────────────────
def test_regression_warning_rate_critical(engine, store):
    # 6 regression runs, 4 in partial → 4/6 = 66% > 50% critical share
    _seed_runs(store, stage="regression",
               durations=[1.0] * 6,
               statuses=[STATUS_OK, STATUS_PARTIAL, STATUS_PARTIAL,
                         STATUS_PARTIAL, STATUS_PARTIAL, STATUS_OK])
    report = engine.scan()
    rate = [a for a in report.anomalies
            if a.type is AnomalyType.REGRESSION_WARNING_RATE]
    assert len(rate) == 1
    assert rate[0].severity is AnomalySeverity.CRITICAL
    assert rate[0].value > 0.50


def test_regression_clean_runs_no_rate_anomaly(engine, store):
    _seed_runs(store, stage="regression",
               durations=[1.0] * 5, statuses=[STATUS_OK] * 5)
    report = engine.scan()
    rate = [a for a in report.anomalies
            if a.type is AnomalyType.REGRESSION_WARNING_RATE]
    assert rate == []


# ─────────────────────────────────────────────────────────────────────
# Detectors — TPI distribution drift
# ─────────────────────────────────────────────────────────────────────
def test_tpi_distribution_drift_parsed_from_report(engine):
    """
    The engine reads the largest |Δ relativo| value from the regression
    distribution table. Build a synthetic report at 17% drift → CRITICAL.
    """
    md = engine.cfg.regression_report_dir / "regression_test.md"
    md.write_text(
        "## Distribuzione TPI\n\n"
        "| Statistic | Baseline | Candidate | Δ relativo | Warn / Crit |\n"
        "|---|---:|---:|---:|---:|\n"
        "| mean | 0.30 | 0.31 | 1.00% | 5% / 10% |\n"
        "| std  | 0.40 | 0.45 | 12.00% | 8% / 15% |\n"
        "| p95  | 1.20 | 1.40 | 17.00% | 8% / 15% |\n",
        encoding="utf-8",
    )
    # touch mtime to "now"
    import os
    now = FIXED_NOW.timestamp()
    os.utime(md, (now, now))
    report = engine.scan()
    drift = [a for a in report.anomalies if a.type is AnomalyType.TPI_DISTRIBUTION_DRIFT]
    assert len(drift) == 1
    assert drift[0].severity is AnomalySeverity.CRITICAL
    assert drift[0].evidence["max_distribution_shift_pct"] == pytest.approx(0.17, abs=1e-6)


def test_parse_regression_max_shift_no_table_returns_none(tmp_path):
    p = tmp_path / "empty.md"
    p.write_text("# nothing\n", encoding="utf-8")
    assert _parse_regression_max_shift(p) is None


# ─────────────────────────────────────────────────────────────────────
# Detectors — missing data
# ─────────────────────────────────────────────────────────────────────
def test_missing_data_critical_when_zero_runs(engine):
    report = engine.scan()
    missing = [a for a in report.anomalies
               if a.type is AnomalyType.MISSING_DATA
               and a.stage in ("snapshot", "regression")]
    # both snapshot and regression are expected ≥4 → CRITICAL when zero
    assert {a.stage for a in missing} == {"snapshot", "regression"}
    assert all(a.severity is AnomalySeverity.CRITICAL for a in missing)


def test_missing_data_warn_when_below_expected(engine, store):
    _seed_runs(store, stage="snapshot", durations=[1.0, 1.0])     # only 2 of 4
    _seed_runs(store, stage="regression", durations=[1.0, 1.0, 1.0, 1.0, 1.0])
    report = engine.scan()
    missing = [a for a in report.anomalies
               if a.type is AnomalyType.MISSING_DATA and a.stage == "snapshot"]
    assert len(missing) == 1
    assert missing[0].severity is AnomalySeverity.WARN


# ─────────────────────────────────────────────────────────────────────
# Run failure burst
# ─────────────────────────────────────────────────────────────────────
def test_failure_burst_zscore(engine, store):
    """
    Need non-zero baseline variance: 5 prior days with sporadic
    1-failure days + a sudden 8-failure spike today.
    """
    base = FIXED_NOW - timedelta(days=12)
    # 5 prior days: mix of 0/1 failure to give std > 0
    sporadic = [1, 0, 1, 0, 1, 0]   # 6 days, 3 with 1 failure
    for i, n_fail in enumerate(sporadic):
        day = base + timedelta(days=i)
        # one OK run always
        rec = store.start_run(stage="ingest",
                              started_at=day.isoformat(timespec="seconds"))
        store.finish_run(rec.run_id, status=STATUS_OK,
                         ended_at=(day + timedelta(seconds=1)).isoformat(timespec="seconds"))
        # n_fail failure runs same day
        for k in range(n_fail):
            t = day + timedelta(hours=2 + k)
            rec = store.start_run(stage="ingest",
                                  started_at=t.isoformat(timespec="seconds"))
            store.finish_run(rec.run_id, status=STATUS_FAIL,
                             ended_at=(t + timedelta(seconds=1)).isoformat(timespec="seconds"))
    # Today: 8 failures
    last = FIXED_NOW - timedelta(hours=1)
    for j in range(8):
        rec = store.start_run(stage="ingest",
                              started_at=(last + timedelta(seconds=j)).isoformat(timespec="seconds"))
        store.finish_run(rec.run_id, status=STATUS_FAIL,
                         ended_at=(last + timedelta(seconds=j + 1)).isoformat(timespec="seconds"))
    report = engine.scan()
    bursts = [a for a in report.anomalies if a.type is AnomalyType.RUN_FAILURE_BURST]
    assert len(bursts) >= 1
    assert bursts[0].stage == "ingest"


# ─────────────────────────────────────────────────────────────────────
# Rollup, severity, health score
# ─────────────────────────────────────────────────────────────────────
def test_report_severity_is_max_of_anomalies():
    crit = DetectedAnomaly(
        type=AnomalyType.MISSING_DATA, severity=AnomalySeverity.CRITICAL,
        confidence=1.0, value=0, threshold=4, evidence={}, suggested_action="x",
    )
    info = DetectedAnomaly(
        type=AnomalyType.MISSING_DATA, severity=AnomalySeverity.INFO,
        confidence=0.1, value=3, threshold=4, evidence={}, suggested_action="x",
    )
    rpt = AnomalyReport(
        generated_at="2026-05-16T12:00:00+00:00", window_days=14,
        anomalies=[info, crit], health_score=60,
        counts_by_severity={"INFO": 1, "WARN": 0, "CRITICAL": 1},
        counts_by_type={t.value: 0 for t in AnomalyType},
        inspected_stages=["snapshot"],
    )
    assert rpt.severity is AnomalySeverity.CRITICAL
    assert rpt.passed is False


def test_health_score_deducts_per_severity(thresholds):
    anomalies = [
        DetectedAnomaly(type=AnomalyType.MISSING_DATA,
                        severity=AnomalySeverity.CRITICAL,
                        confidence=1.0, value=0, threshold=1,
                        evidence={}, suggested_action="x"),
        DetectedAnomaly(type=AnomalyType.STALE_SNAPSHOT,
                        severity=AnomalySeverity.WARN,
                        confidence=0.5, value=40, threshold=36,
                        evidence={}, suggested_action="x"),
    ]
    score = _compute_health(anomalies, thresholds.health_deduction)
    assert score == 100 - 20 - 5


def test_confidence_clamped_into_unit_interval():
    a = DetectedAnomaly(
        type=AnomalyType.STALE_SNAPSHOT, severity=AnomalySeverity.WARN,
        confidence=99.0, value=1.0, threshold=1.0,
        evidence={}, suggested_action="x",
    )
    assert a.confidence == 1.0
    b = DetectedAnomaly(
        type=AnomalyType.STALE_SNAPSHOT, severity=AnomalySeverity.WARN,
        confidence=-1.0, value=1.0, threshold=1.0,
        evidence={}, suggested_action="x",
    )
    assert b.confidence == 0.0


# ─────────────────────────────────────────────────────────────────────
# Report rendering — deterministic
# ─────────────────────────────────────────────────────────────────────
def test_report_renderer_is_deterministic():
    a = DetectedAnomaly(
        type=AnomalyType.STALE_SNAPSHOT,
        severity=AnomalySeverity.WARN,
        confidence=0.7, value=40, threshold=36,
        evidence={"season": "2025-26", "age_hours": 40},
        suggested_action="Rerun snapshot",
        stage="snapshot",
        artifact_path="snapshots/2025-26/giornata_36",
    )
    rpt = AnomalyReport(
        generated_at="2026-05-16T12:00:00+00:00", window_days=14,
        anomalies=[a], health_score=95,
        counts_by_severity={"INFO": 0, "WARN": 1, "CRITICAL": 0},
        counts_by_type={t.value: (1 if t is a.type else 0) for t in AnomalyType},
        inspected_stages=["snapshot"],
    )
    one = render_anomaly_report(rpt)
    two = render_anomaly_report(rpt)
    assert one == two
    assert "WARN" in one
    assert "Rerun snapshot" in one
    assert "snapshots/2025-26/giornata_36" in one


def test_write_report_uses_anomaly_latest_filename(tmp_path):
    a = DetectedAnomaly(
        type=AnomalyType.STALE_SNAPSHOT, severity=AnomalySeverity.WARN,
        confidence=0.7, value=40, threshold=36,
        evidence={}, suggested_action="x", stage="snapshot",
    )
    rpt = AnomalyReport(
        generated_at="2026-05-16T12:00:00+00:00", window_days=14,
        anomalies=[a], health_score=95,
        counts_by_severity={"INFO": 0, "WARN": 1, "CRITICAL": 0},
        counts_by_type={t.value: 0 for t in AnomalyType},
        inspected_stages=["snapshot"],
    )
    p = write_anomaly_report(rpt, out_dir=tmp_path)
    assert p.name == "anomaly_latest.md"


# ─────────────────────────────────────────────────────────────────────
# Metrics emission
# ─────────────────────────────────────────────────────────────────────
def test_emit_anomaly_metrics_writes_expected_families(tmp_path, monkeypatch):
    monkeypatch.setattr("serie_a_scout.obs.metrics.METRICS_DIR", tmp_path)
    a = DetectedAnomaly(
        type=AnomalyType.STALE_SNAPSHOT, severity=AnomalySeverity.WARN,
        confidence=0.7, value=40, threshold=36,
        evidence={"season": "2025-26"},
        suggested_action="x", stage="snapshot",
    )
    rpt = AnomalyReport(
        generated_at="2026-05-16T12:00:00+00:00", window_days=14,
        anomalies=[a], health_score=80,
        counts_by_severity={"INFO": 0, "WARN": 1, "CRITICAL": 0},
        counts_by_type={t.value: (1 if t is a.type else 0) for t in AnomalyType},
        inspected_stages=["snapshot"],
    )
    out = emit_anomaly_metrics(rpt)
    text = out.read_text(encoding="utf-8")
    for needed in (
        "serie_a_pipeline_health_score",
        "serie_a_anomalies_total",
        "serie_a_anomaly_severity",
        "serie_a_snapshot_staleness_seconds",
    ):
        assert needed in text, f"missing metric family {needed}"


# ─────────────────────────────────────────────────────────────────────
# Lineage linking
# ─────────────────────────────────────────────────────────────────────
def test_engine_links_run_ids_in_anomalies(engine, store):
    rids = _seed_runs(
        store, stage="snapshot",
        durations=[1.0, 1.1, 0.9, 1.05, 0.95, 1.02, 60.0],
    )
    report = engine.scan()
    spikes = [a for a in report.anomalies
              if a.type is AnomalyType.DURATION_ZSCORE_SPIKE]
    assert spikes
    # last run is the spike
    assert spikes[0].run_ids[0] == rids[-1]


# ─────────────────────────────────────────────────────────────────────
# CLI runner exit codes
# ─────────────────────────────────────────────────────────────────────
def test_runner_exit_code_safe(tmp_path, monkeypatch):
    # Empty store → CRITICAL missing-data anomalies BUT without
    # --fail-on-critical, exit is 0.
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config").mkdir()
    # write a minimal anomaly.yml so the runner does not pick up the
    # real config (would be repo-relative anyway)
    (tmp_path / "config" / "anomaly.yml").write_text(
        "schema_version: '1.0.0'\nwindow_days: 7\nmin_samples: 5\n",
        encoding="utf-8",
    )
    sys.path.insert(0, str(ROOT / "snapshots"))
    from anomaly_runner import main  # noqa: E402
    rc = main(["--out-dir", str(tmp_path / "anomaly")])
    assert rc == 0


def test_runner_fail_on_critical(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "anomaly.yml").write_text(
        "schema_version: '1.0.0'\nwindow_days: 7\nmin_samples: 5\n",
        encoding="utf-8",
    )
    sys.path.insert(0, str(ROOT / "snapshots"))
    from anomaly_runner import main  # noqa: E402
    rc = main(["--out-dir", str(tmp_path / "anomaly"), "--fail-on-critical"])
    assert rc == 2


# ─────────────────────────────────────────────────────────────────────
# Retention dry-run
# ─────────────────────────────────────────────────────────────────────
def test_prune_old_lineage_dry_run_keeps_rows(store):
    # rec from a year ago → should be a candidate but not deleted
    long_ago = (datetime.now(timezone.utc) - timedelta(days=400)).isoformat(timespec="seconds")
    rec = store.start_run(stage="snapshot", started_at=long_ago)
    store.finish_run(rec.run_id, status=STATUS_OK,
                     ended_at=long_ago)
    rep = prune_old_lineage(keep_days=30, dry_run=True, store=store)
    assert any(c["run_id"] == rec.run_id for c in rep.candidates)
    assert rep.deleted == []
    # row still present
    assert store.get_run(rec.run_id) is not None


def test_prune_old_metrics_refuses_outside_safe_roots(tmp_path):
    with pytest.raises(PermissionError):
        prune_old_metrics(keep_days=1, dry_run=True, metrics_dir=tmp_path)


def test_prune_old_quarantine_refuses_outside_safe_roots(tmp_path):
    with pytest.raises(PermissionError):
        prune_old_quarantine(keep_days=1, dry_run=True, quarantine_dir=tmp_path)


# ─────────────────────────────────────────────────────────────────────
# Empty store → only missing-data fires
# ─────────────────────────────────────────────────────────────────────
def test_empty_store_only_emits_missing_data(engine):
    report = engine.scan()
    types = {a.type for a in report.anomalies}
    # CRITICAL missing_data is expected; no spurious z-score or quarantine
    assert AnomalyType.DURATION_ZSCORE_SPIKE not in types
    assert AnomalyType.QUARANTINE_GROWTH not in types
    assert AnomalyType.STALE_SNAPSHOT not in types
    assert AnomalyType.MISSING_DATA in types


# ─────────────────────────────────────────────────────────────────────
# AnomalyType enum coverage
# ─────────────────────────────────────────────────────────────────────
def test_every_anomaly_type_has_metrics_label_friendly_value():
    for t in AnomalyType:
        assert t.value == t.value.lower()
        assert " " not in t.value
        assert "," not in t.value
