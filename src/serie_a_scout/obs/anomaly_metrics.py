"""
Prometheus textfile metrics for the F1.5 anomaly subsystem.

Writes to `obs/metrics/anomaly.prom`. Each scan rewrites the file via
the existing `emit_metric` helper which deduplicates HELP/TYPE headers.

Idempotency: calling `emit_anomaly_metrics()` twice with the same input
adds an additional sample (the textfile collector keeps the latest);
labelsets are identical, so the timeseries is stable.

Metric set (matches roadmap §F1.5):

  * serie_a_anomalies_total{type,severity}     gauge cumulative count in current scan
  * serie_a_anomaly_severity{type}             gauge 0/1/2
  * serie_a_pipeline_health_score              gauge 0-100
  * serie_a_snapshot_staleness_seconds{season} gauge
  * serie_a_quarantine_growth{file}            gauge new lines in window
  * serie_a_regression_warning_rate            gauge share
  * serie_a_ingestion_duration_zscore{stage}   gauge last z-score
"""
from __future__ import annotations

import math
from pathlib import Path

from ..analytics.anomaly_models import (
    AnomalyReport, AnomalySeverity, AnomalyType, DetectedAnomaly,
)
from .metrics import emit_metric


METRIC_FILE = "anomaly.prom"


def emit_anomaly_metrics(report: AnomalyReport) -> Path:
    """Emit one metric sample per anomaly facet. Returns the `.prom` path."""
    # Health
    path = emit_metric(
        "serie_a_pipeline_health_score",
        int(report.health_score),
        labels={"window_days": str(report.window_days)},
        help_text="Operational health score [0,100] computed from anomaly severities",
        metric_type="gauge",
        file_name=METRIC_FILE,
    )

    # Per-type / per-severity rollups
    for sev_str, n in report.counts_by_severity.items():
        emit_metric(
            "serie_a_anomalies_total",
            int(n),
            labels={"severity": sev_str, "type": "all"},
            help_text="Anomalies detected in the most recent scan",
            metric_type="gauge",
            file_name=METRIC_FILE,
        )
    for type_str, n in report.counts_by_type.items():
        emit_metric(
            "serie_a_anomalies_total",
            int(n),
            labels={"severity": "all", "type": type_str},
            help_text="Anomalies detected in the most recent scan",
            metric_type="gauge",
            file_name=METRIC_FILE,
        )

    # Severity numeric per type — useful for alert rules
    sev_by_type: dict[str, AnomalySeverity] = {}
    for a in report.anomalies:
        existing = sev_by_type.get(a.type.value)
        if existing is None or _rank(a.severity) > _rank(existing):
            sev_by_type[a.type.value] = a.severity
    for t in AnomalyType:
        sev = sev_by_type.get(t.value, AnomalySeverity.INFO)
        emit_metric(
            "serie_a_anomaly_severity",
            sev.numeric(),
            labels={"type": t.value},
            help_text="Highest severity observed per anomaly type (0=INFO,1=WARN,2=CRITICAL)",
            metric_type="gauge",
            file_name=METRIC_FILE,
        )

    # Detector-specific metrics
    for a in report.anomalies:
        if a.type is AnomalyType.STALE_SNAPSHOT:
            season = str(a.evidence.get("season") or "unknown")
            emit_metric(
                "serie_a_snapshot_staleness_seconds",
                int(round(_safe(a.value) * 3600.0)),
                labels={"season": season},
                help_text="Age (seconds) of the most-recent snapshot per season",
                metric_type="gauge",
                file_name=METRIC_FILE,
            )
        elif a.type is AnomalyType.QUARANTINE_GROWTH:
            f = str(a.evidence.get("file") or "unknown")
            emit_metric(
                "serie_a_quarantine_growth",
                int(_safe(a.value)),
                labels={"file": f},
                help_text="New quarantine entries within the window",
                metric_type="gauge",
                file_name=METRIC_FILE,
            )
        elif a.type is AnomalyType.REGRESSION_WARNING_RATE:
            emit_metric(
                "serie_a_regression_warning_rate",
                round(_safe(a.value), 6),
                labels={},
                help_text="Share of regression runs in WARNING/CRITICAL in the window",
                metric_type="gauge",
                file_name=METRIC_FILE,
            )
        elif a.type is AnomalyType.DURATION_ZSCORE_SPIKE:
            stage = str(a.stage or "unknown")
            z = float(a.evidence.get("z_score", 0.0))
            emit_metric(
                "serie_a_ingestion_duration_zscore",
                round(z, 4),
                labels={"stage": stage},
                help_text="Latest pipeline-run duration z-score per stage",
                metric_type="gauge",
                file_name=METRIC_FILE,
            )

    return path


def _rank(sev: AnomalySeverity) -> int:
    return {AnomalySeverity.INFO: 0, AnomalySeverity.WARN: 1, AnomalySeverity.CRITICAL: 2}[sev]


def _safe(v: float) -> float:
    if v is None or (isinstance(v, float) and (math.isnan(v) or math.isinf(v))):
        return 0.0
    return float(v)
