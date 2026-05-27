"""
Emit regression-stability metrics into `obs/metrics/regression.prom`.

Wraps the project's existing Prometheus textfile emitter
(`serie_a_scout.obs.metrics.emit_metric`). One emission per metric per
regression run; existing samples are appended (the collector keeps the
latest).
"""
from __future__ import annotations

import math
from pathlib import Path

from .metrics import emit_metric, increment_counter


METRIC_FILE = "regression.prom"


def emit_regression_metrics(
    *,
    season: str,
    giornata: int,
    dimension: str,
    spearman: float,
    top10_overlap: int,
    top20_overlap: int,
    large_deltas: int,
    distribution_shift_pct: float,
    severity: str,
    failed: bool,
) -> Path:
    """Emit one sample per metric, return the .prom file path."""
    labels = {
        "season": season,
        "giornata": f"{int(giornata):02d}",
        "dimension": dimension,
        "severity": severity,
    }

    path = emit_metric(
        "tpi_regression_spearman",
        _safe(spearman),
        labels=labels,
        help_text="Spearman rank correlation between baseline and candidate TPI",
        metric_type="gauge",
        file_name=METRIC_FILE,
    )
    emit_metric(
        "tpi_regression_top10_overlap",
        int(top10_overlap),
        labels=labels,
        help_text="Number of baseline top-10 players still present in candidate top-10",
        metric_type="gauge",
        file_name=METRIC_FILE,
    )
    emit_metric(
        "tpi_regression_top20_overlap",
        int(top20_overlap),
        labels=labels,
        help_text="Number of baseline top-20 players still present in candidate top-20",
        metric_type="gauge",
        file_name=METRIC_FILE,
    )
    emit_metric(
        "tpi_regression_large_deltas_total",
        int(large_deltas),
        labels=labels,
        help_text="Players whose |TPI delta| exceeds the configured threshold",
        metric_type="gauge",
        file_name=METRIC_FILE,
    )
    emit_metric(
        "tpi_distribution_shift_pct",
        _safe(distribution_shift_pct),
        labels=labels,
        help_text="Maximum relative shift (mean/std/p95) of the TPI distribution",
        metric_type="gauge",
        file_name=METRIC_FILE,
    )

    if failed:
        increment_counter(
            "tpi_regression_failures_total",
            labels=labels,
            help_text="Cumulative count of CRITICAL regression failures",
            file_name=METRIC_FILE,
        )

    return path


def _safe(v: float) -> float:
    """Replace NaN / inf with 0.0 — Prometheus rejects non-finite scalars."""
    if v is None or (isinstance(v, float) and (math.isnan(v) or math.isinf(v))):
        return 0.0
    return float(v)
