"""
Prometheus textfile metrics for the lineage subsystem.

Writes to `obs/metrics/lineage.prom`. Re-uses the project's existing
`emit_metric` helper so HELP/TYPE headers are deduplicated.

Metric set (matches roadmap §F1.2):

  * serie_a_pipeline_runs_total{stage,status}        — gauge (cumulative)
  * serie_a_pipeline_failures_total{stage}           — gauge (cumulative)
  * serie_a_pipeline_duration_seconds{stage,run_id}  — gauge (single run)
  * serie_a_pipeline_artifacts_total{kind}           — gauge (cumulative)
  * serie_a_pipeline_rows_total{stage}               — gauge (cumulative rows_out)
"""
from __future__ import annotations

from pathlib import Path
from typing import Mapping

from ..core.lineage_store import LineageStore, RunRecord, get_default_store
from .metrics import emit_metric


METRIC_FILE = "lineage.prom"


def emit_run_metrics(run: RunRecord, *, store: LineageStore | None = None) -> Path:
    """
    Emit metrics for a freshly closed run + the global counters.

    Safe to call multiple times; Prometheus textfile collectors keep the
    latest sample per labelset.
    """
    s = store or get_default_store()
    labels = {"stage": run.stage, "status": run.status}

    path = emit_metric(
        "serie_a_pipeline_duration_seconds",
        round((run.duration_ms or 0) / 1000.0, 3),
        labels={"stage": run.stage, "run_id": run.run_id[:12]},
        help_text="Wall-clock duration of a pipeline run (seconds)",
        metric_type="gauge",
        file_name=METRIC_FILE,
    )

    counters = s.aggregate_counters()
    emit_metric(
        "serie_a_pipeline_runs_total",
        int(counters["runs_total"]),
        labels=labels,
        help_text="Cumulative pipeline runs observed",
        metric_type="gauge",
        file_name=METRIC_FILE,
    )
    emit_metric(
        "serie_a_pipeline_failures_total",
        int(counters["failures_total"]),
        labels={"stage": run.stage},
        help_text="Cumulative pipeline run failures",
        metric_type="gauge",
        file_name=METRIC_FILE,
    )
    emit_metric(
        "serie_a_pipeline_rows_total",
        int(counters["rows_total"]),
        labels={"stage": run.stage},
        help_text="Cumulative rows_out across pipeline runs",
        metric_type="gauge",
        file_name=METRIC_FILE,
    )
    # one sample per artifact kind for this run
    artifacts = s.artifacts_for(run.run_id)
    by_kind: dict[str, int] = {}
    for a in artifacts:
        by_kind[a.kind] = by_kind.get(a.kind, 0) + 1
    for kind, n in by_kind.items():
        emit_metric(
            "serie_a_pipeline_artifacts_total",
            int(n),
            labels={"kind": kind, "stage": run.stage},
            help_text="Artifacts registered, by kind",
            metric_type="gauge",
            file_name=METRIC_FILE,
        )

    return path


def emit_global_snapshot(*, store: LineageStore | None = None) -> Path:
    """Re-emit cumulative counters without any specific run context."""
    s = store or get_default_store()
    counters = s.aggregate_counters()
    path = emit_metric(
        "serie_a_pipeline_runs_total",
        int(counters["runs_total"]),
        labels={"stage": "all", "status": "any"},
        help_text="Cumulative pipeline runs observed",
        metric_type="gauge",
        file_name=METRIC_FILE,
    )
    emit_metric(
        "serie_a_pipeline_failures_total",
        int(counters["failures_total"]),
        labels={"stage": "all"},
        help_text="Cumulative pipeline run failures",
        metric_type="gauge",
        file_name=METRIC_FILE,
    )
    emit_metric(
        "serie_a_pipeline_artifacts_total",
        int(counters["artifacts_total"]),
        labels={"kind": "all", "stage": "all"},
        help_text="Artifacts registered, by kind",
        metric_type="gauge",
        file_name=METRIC_FILE,
    )
    emit_metric(
        "serie_a_pipeline_rows_total",
        int(counters["rows_total"]),
        labels={"stage": "all"},
        help_text="Cumulative rows_out across pipeline runs",
        metric_type="gauge",
        file_name=METRIC_FILE,
    )
    return path
