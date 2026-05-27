"""Analytics package — TPI engine, regression diff, anomaly detection."""
from .anomaly_engine import (
    AnomalyEngine,
    AnomalyEngineConfig,
    compute_baseline,
    rolling_window,
)
from .anomaly_models import (
    AnomalyReport,
    AnomalySeverity,
    AnomalyThresholds,
    AnomalyType,
    BaselineWindow,
    DetectedAnomaly,
    load_thresholds as load_anomaly_thresholds,
)
from .anomaly_report import (
    DEFAULT_REPORT_DIR as ANOMALY_REPORT_DIR,
    render_report as render_anomaly_report,
    write_json_dump as write_anomaly_json,
    write_report as write_anomaly_report,
)
from .regression_diff import (
    DistributionShift,
    MovedPlayer,
    RegressionDiff,
    RegressionSeverity,
    RegressionThresholds,
    compare_rankings,
    compute_spearman,
    detect_large_movements,
    explain_top_deltas,
    load_thresholds,
    run_regression_diff,
    summarize_distribution_changes,
)
from .regression_report import (
    DEFAULT_REPORT_DIR,
    render_report,
    write_report,
)

__all__ = [
    "ANOMALY_REPORT_DIR",
    "AnomalyEngine",
    "AnomalyEngineConfig",
    "AnomalyReport",
    "AnomalySeverity",
    "AnomalyThresholds",
    "AnomalyType",
    "BaselineWindow",
    "DEFAULT_REPORT_DIR",
    "DetectedAnomaly",
    "DistributionShift",
    "MovedPlayer",
    "RegressionDiff",
    "RegressionSeverity",
    "RegressionThresholds",
    "compare_rankings",
    "compute_baseline",
    "compute_spearman",
    "detect_large_movements",
    "explain_top_deltas",
    "load_anomaly_thresholds",
    "load_thresholds",
    "render_anomaly_report",
    "render_report",
    "rolling_window",
    "run_regression_diff",
    "summarize_distribution_changes",
    "write_anomaly_json",
    "write_anomaly_report",
    "write_report",
]
