"""
Regression tests for the F1.2 lineage tracking subsystem.

Coverage targets (all green):

  1. Schema bootstrap creates required tables.
  2. RunContext writes a 'running' row on enter and 'ok' on exit.
  3. Exception inside the context → status='fail', message persisted,
     exception re-raised.
  4. Nested RunContext inherits parent_run_id from the active stack.
  5. `register_artifact` persists checksum + bytes + rows + meta.
  6. Standalone `register_artifact()` works without an active context.
  7. `register_input` joins an artifact to a downstream run.
  8. Detected orphans:
        - runs stuck in 'running'
        - artifacts whose file is missing on disk
        - completed runs with zero artifacts
  9. Concurrent-safe lock: two threads writing artifacts simultaneously
     leave the store consistent (no duplicate auto-ids, no partial txn).
 10. Corrupted lineage JSON in `extra_json` does not crash readers.
 11. Deterministic timestamps via injected clock.
 12. Lineage export JSON dump round-trips.
 13. Mermaid renderer emits stable graph for a known input.
 14. `latest_lineage_report.md` is written and non-empty.
 15. Per-run report writes a file named `lineage_<short>.md`.
 16. Metric emitter writes `lineage.prom` with the expected metric names.
 17. Rollback on `register_artifact` for an unknown run_id leaves DB clean.
"""
from __future__ import annotations

import json
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from serie_a_scout.core import (  # noqa: E402
    LineageStore, RunContext, current_run, register_artifact,
)
from serie_a_scout.core.lineage_store import (  # noqa: E402
    STATUS_FAIL, STATUS_OK, STATUS_PARTIAL, STATUS_RUNNING,
)
from serie_a_scout.obs.lineage_export import (  # noqa: E402
    collect_lineage, render_mermaid, render_report,
    write_json_dump, write_report_md, write_run_report,
)
from serie_a_scout.obs.lineage_metrics import emit_run_metrics  # noqa: E402


# ─────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────
@pytest.fixture
def store(tmp_path) -> LineageStore:
    db = tmp_path / "lineage.db"
    s = LineageStore(db)
    yield s
    s.close()


@pytest.fixture
def fixed_clock():
    """Inject deterministic ISO timestamps incrementing by 1 second each call."""
    counter = {"i": 0}

    def _clock() -> str:
        counter["i"] += 1
        # 2026-05-16T00:00:0i+00:00
        return f"2026-05-16T00:00:{counter['i']:02d}+00:00"
    return _clock


# ─────────────────────────────────────────────────────────────────────
# Tests
# ─────────────────────────────────────────────────────────────────────
def test_schema_bootstrap_creates_tables(store: LineageStore):
    rows = store.conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
    names = {r["name"] for r in rows}
    assert "pipeline_run" in names
    assert "pipeline_artifact" in names
    assert "pipeline_artifact_input" in names


def test_run_context_records_ok_on_success(store: LineageStore, fixed_clock):
    with RunContext(stage="snapshot", store=store, clock=fixed_clock) as run:
        run.set_rows_out(7)
        rec_mid = store.get_run(run.run_id)
        assert rec_mid.status == STATUS_RUNNING
    rec = store.get_run(run.run_id)
    assert rec.status == STATUS_OK
    assert rec.exit_code == 0
    assert rec.rows_out == 7
    assert rec.duration_ms is not None
    assert rec.duration_ms >= 0


def test_run_context_records_fail_on_exception(store: LineageStore, fixed_clock):
    with pytest.raises(RuntimeError, match="boom"):
        with RunContext(stage="snapshot", store=store, clock=fixed_clock) as run:
            run_id = run.run_id
            raise RuntimeError("boom")
    rec = store.get_run(run_id)
    assert rec.status == STATUS_FAIL
    assert rec.exit_code == 1
    assert "boom" in (rec.error_message or "")
    assert "error" in rec.extra and rec.extra["error"]["type"] == "RuntimeError"


def test_nested_runs_link_via_parent(store: LineageStore, fixed_clock):
    with RunContext(stage="pipeline", store=store, clock=fixed_clock) as outer:
        with RunContext(stage="snapshot", store=store, clock=fixed_clock) as inner:
            assert inner.parent_run_id == outer.run_id
            assert current_run() is inner
        assert current_run() is outer
    assert current_run() is None
    children = store.children_of(outer.run_id)
    assert {c.run_id for c in children} == {inner.run_id}


def test_register_artifact_persists_metadata(store: LineageStore, tmp_path, fixed_clock):
    file = tmp_path / "thing.json"
    file.write_text("{}", encoding="utf-8")
    with RunContext(stage="snapshot", store=store, clock=fixed_clock) as run:
        art = run.register_artifact(
            kind="json", path=file, compute_checksum=True,
            rows=1, meta={"foo": "bar"},
        )
    fetched = store.artifacts_for(run.run_id)
    assert len(fetched) == 1
    assert fetched[0].kind == "json"
    assert fetched[0].rows == 1
    assert fetched[0].checksum and len(fetched[0].checksum) == 64
    assert fetched[0].bytes_size == 2  # "{}"
    assert fetched[0].meta == {"foo": "bar"}
    assert art.artifact_id == fetched[0].artifact_id


def test_standalone_register_artifact_without_context(store: LineageStore, fixed_clock):
    # need an existing run first
    rec = store.start_run(stage="snapshot", started_at=fixed_clock())
    store.finish_run(rec.run_id, status=STATUS_OK, ended_at=fixed_clock())
    art = register_artifact(
        rec.run_id, kind="json", path="payload.json",
        bytes_size=42, rows=10, store=store,
    )
    assert art.artifact_id is not None
    arts = store.artifacts_for(rec.run_id)
    assert len(arts) == 1 and arts[0].bytes_size == 42


def test_register_input_joins_runs(store: LineageStore, tmp_path, fixed_clock):
    with RunContext(stage="snapshot", store=store, clock=fixed_clock) as producer:
        art = producer.register_artifact(kind="snapshot", path="snapshots/x")
    with RunContext(stage="regression", store=store, clock=fixed_clock) as consumer:
        consumer.register_input(art)
    consumers = store.consumers_of(art.artifact_id)
    assert {c.run_id for c in consumers} == {consumer.run_id}
    inputs = store.inputs_for(consumer.run_id)
    assert {i.artifact_id for i in inputs} == {art.artifact_id}


def test_diagnostics_detect_orphans(store: LineageStore, tmp_path, fixed_clock):
    # Run stuck in running (open but never close)
    stuck = store.start_run(stage="snapshot", started_at=fixed_clock())
    # Completed run with NO artifacts
    empty = store.start_run(stage="audit", started_at=fixed_clock())
    store.finish_run(empty.run_id, status=STATUS_OK, ended_at=fixed_clock())
    # Completed run WITH artifact that does not exist on disk
    has_missing = store.start_run(stage="snapshot", started_at=fixed_clock())
    store.finish_run(has_missing.run_id, status=STATUS_OK, ended_at=fixed_clock())
    store.register_artifact(
        run_id=has_missing.run_id, kind="snapshot",
        path=str(tmp_path / "does_not_exist.parquet"),
    )

    diag = store.detect_orphans()
    assert stuck.run_id in diag["runs_still_running"]
    assert empty.run_id in diag["runs_without_artifacts"]
    assert len(diag["artifacts_missing_file"]) >= 1


def test_concurrent_inserts_are_consistent(store: LineageStore, fixed_clock):
    # Open one run, then spawn N threads each adding M artifacts.
    run = store.start_run(stage="snapshot", started_at=fixed_clock())
    n_threads, m_each = 4, 25

    def _worker(idx: int) -> None:
        for j in range(m_each):
            store.register_artifact(
                run_id=run.run_id,
                kind="json",
                path=f"art_{idx}_{j}.json",
            )

    threads = [threading.Thread(target=_worker, args=(i,)) for i in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    arts = store.artifacts_for(run.run_id)
    assert len(arts) == n_threads * m_each
    ids = [a.artifact_id for a in arts]
    assert len(set(ids)) == len(ids), "duplicate artifact_id"


def test_corrupted_extra_json_does_not_break_reader(store: LineageStore, fixed_clock):
    rec = store.start_run(stage="snapshot", started_at=fixed_clock())
    # Corrupt extra_json directly
    store.conn.execute(
        "UPDATE pipeline_run SET extra_json = ? WHERE run_id = ?",
        ("{not valid json", rec.run_id),
    )
    fetched = store.get_run(rec.run_id)
    assert fetched is not None
    assert fetched.extra == {}  # silently treated as empty


def test_deterministic_clock_produces_stable_timestamps(store: LineageStore):
    ticks = iter([
        "2026-05-16T00:00:01+00:00",
        "2026-05-16T00:00:02+00:00",
    ])
    def clock(): return next(ticks)
    with RunContext(stage="snapshot", store=store, clock=clock) as run:
        pass
    rec = store.get_run(run.run_id)
    assert rec.started_at == "2026-05-16T00:00:01+00:00"
    assert rec.ended_at == "2026-05-16T00:00:02+00:00"


def test_lineage_json_dump_roundtrips(store: LineageStore, tmp_path, fixed_clock):
    with RunContext(stage="snapshot", store=store, clock=fixed_clock) as run:
        run.register_artifact(kind="json", path="payload.json", rows=10)
    lineage = collect_lineage(store)
    path = write_json_dump(lineage, out_dir=tmp_path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["counters"]["runs_total"] == 1
    assert raw["counters"]["artifacts_total"] == 1
    assert raw["runs"][0]["run_id"] == run.run_id


def test_mermaid_render_contains_run_and_artifact_nodes(store: LineageStore, fixed_clock):
    with RunContext(stage="snapshot", store=store, clock=fixed_clock) as run:
        art = run.register_artifact(kind="json", path="payload.json")
    lineage = collect_lineage(store)
    md = render_mermaid(lineage)
    assert "flowchart LR" in md
    assert f"R_{run.run_id[:10]}" in md
    assert f"A_{art.artifact_id}" in md


def test_latest_report_is_written(store: LineageStore, tmp_path, fixed_clock):
    with RunContext(stage="snapshot", store=store, clock=fixed_clock) as run:
        run.register_artifact(kind="json", path="payload.json")
    lineage = collect_lineage(store)
    path = write_report_md(lineage, out_dir=tmp_path)
    assert path.is_file()
    assert path.stat().st_size > 0
    txt = path.read_text(encoding="utf-8")
    assert "# Lineage report" in txt
    assert "DAG" in txt


def test_per_run_report_uses_short_id(store: LineageStore, tmp_path, fixed_clock):
    with RunContext(stage="snapshot", store=store, clock=fixed_clock) as run:
        run.register_artifact(kind="json", path="payload.json")
    path = write_run_report(run.run_id, store=store, out_dir=tmp_path)
    assert path.is_file()
    assert run.run_id[:12] in path.name


def test_emit_run_metrics_writes_prom_file(store: LineageStore, tmp_path, monkeypatch, fixed_clock):
    # Redirect the obs metrics dir into tmp_path
    monkeypatch.setattr(
        "serie_a_scout.obs.metrics.METRICS_DIR",
        tmp_path,
    )
    with RunContext(stage="snapshot", store=store, clock=fixed_clock) as run:
        run.register_artifact(kind="json", path="payload.json")
    rec = store.get_run(run.run_id)
    out = emit_run_metrics(rec, store=store)
    assert out.is_file()
    txt = out.read_text(encoding="utf-8")
    for name in (
        "serie_a_pipeline_runs_total",
        "serie_a_pipeline_failures_total",
        "serie_a_pipeline_duration_seconds",
        "serie_a_pipeline_artifacts_total",
        "serie_a_pipeline_rows_total",
    ):
        assert name in txt, f"missing metric {name}"


def test_register_artifact_unknown_run_id_raises_and_leaves_db_clean(store: LineageStore):
    with pytest.raises(KeyError):
        store.register_artifact(
            run_id="does-not-exist",
            kind="json",
            path="x.json",
        )
    n_arts = store.conn.execute(
        "SELECT COUNT(*) FROM pipeline_artifact"
    ).fetchone()[0]
    assert n_arts == 0


def test_partial_status_round_trips(store: LineageStore, fixed_clock):
    with RunContext(stage="regression", store=store, clock=fixed_clock) as run:
        run.set_status("partial")
        run.set_exit_code(2)
    rec = store.get_run(run.run_id)
    assert rec.status == STATUS_PARTIAL
    assert rec.exit_code == 2


def test_collect_lineage_for_specific_run_includes_chain(store: LineageStore, fixed_clock):
    with RunContext(stage="snapshot", store=store, clock=fixed_clock) as producer:
        art = producer.register_artifact(kind="snapshot", path="snapshots/x")
    with RunContext(stage="regression", store=store, clock=fixed_clock) as consumer:
        consumer.register_input(art)

    lineage = collect_lineage(store, run_id=producer.run_id)
    run_ids = {r["run_id"] for r in lineage["runs"]}
    assert producer.run_id in run_ids
    assert consumer.run_id in run_ids
    # And edges should connect them via the artifact
    edges = {(e["from"], e["to"]) for e in lineage["edges"]}
    assert (f"R_{producer.run_id[:10]}", f"A_{art.artifact_id}") in edges
    assert (f"A_{art.artifact_id}", f"R_{consumer.run_id[:10]}") in edges
