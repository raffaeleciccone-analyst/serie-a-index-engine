"""
F1.5 anomaly-detection data model.

All result objects are JSON-serialisable dataclasses. The enums use string
values so they round-trip through Markdown reports and Prometheus labels
without ambiguity.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Mapping


_REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CONFIG_PATH = _REPO_ROOT / "config" / "anomaly.yml"


# ─────────────────────────────────────────────────────────────────────
# Enums
# ─────────────────────────────────────────────────────────────────────
class AnomalySeverity(str, Enum):
    """Mirrors RegressionSeverity so the CI gate can stay consistent."""
    INFO = "INFO"
    WARN = "WARN"
    CRITICAL = "CRITICAL"

    @property
    def is_failure(self) -> bool:
        return self is AnomalySeverity.CRITICAL

    def numeric(self) -> int:
        """0 / 1 / 2 — used in metrics emission."""
        return {self.INFO: 0, self.WARN: 1, self.CRITICAL: 2}[self]


class AnomalyType(str, Enum):
    """All detector outputs are tagged with one of these labels."""
    DURATION_ZSCORE_SPIKE = "duration_zscore_spike"
    STALE_SNAPSHOT = "stale_snapshot"
    QUARANTINE_GROWTH = "quarantine_growth"
    REGRESSION_WARNING_RATE = "regression_warning_rate"
    REGRESSION_FREQUENCY_ESCALATION = "regression_frequency_escalation"
    TPI_DISTRIBUTION_DRIFT = "tpi_distribution_drift"
    MISSING_DATA = "missing_data"
    RUN_FAILURE_BURST = "run_failure_burst"


# ─────────────────────────────────────────────────────────────────────
# Dataclasses
# ─────────────────────────────────────────────────────────────────────
@dataclass
class BaselineWindow:
    """Rolling baseline summary used by z-score detectors."""
    metric: str
    n_samples: int
    mean: float
    std: float
    p50: float
    p95: float
    window_days: int
    min_samples_required: int

    @property
    def is_usable(self) -> bool:
        return (
            self.n_samples >= self.min_samples_required
            and self.std > 0
        )

    def zscore(self, value: float) -> float:
        if not self.is_usable:
            return 0.0
        return (value - self.mean) / self.std

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DetectedAnomaly:
    """One anomaly returned by the engine."""
    type: AnomalyType
    severity: AnomalySeverity
    confidence: float                     # in [0, 1]
    value: float                          # observed value
    threshold: float                      # value at which severity flipped
    evidence: dict[str, Any]              # JSON-safe diagnostics
    suggested_action: str
    stage: str | None = None
    run_ids: list[str] = field(default_factory=list)
    artifact_path: str | None = None
    upstream_run_ids: list[str] = field(default_factory=list)
    downstream_run_ids: list[str] = field(default_factory=list)
    baseline: BaselineWindow | None = None

    def __post_init__(self) -> None:
        # clamp confidence to [0,1] defensively
        if self.confidence < 0:
            self.confidence = 0.0
        elif self.confidence > 1:
            self.confidence = 1.0

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["type"] = self.type.value
        d["severity"] = self.severity.value
        return d


@dataclass
class AnomalyReport:
    """Top-level result of one anomaly scan."""
    generated_at: str
    window_days: int
    anomalies: list[DetectedAnomaly]
    health_score: int
    counts_by_severity: dict[str, int]
    counts_by_type: dict[str, int]
    inspected_stages: list[str]
    lineage_run_id: str | None = None

    @property
    def severity(self) -> AnomalySeverity:
        """Highest severity among detected anomalies."""
        if not self.anomalies:
            return AnomalySeverity.INFO
        order = {AnomalySeverity.INFO: 0, AnomalySeverity.WARN: 1, AnomalySeverity.CRITICAL: 2}
        return max(self.anomalies, key=lambda a: order[a.severity]).severity

    @property
    def passed(self) -> bool:
        return self.severity is not AnomalySeverity.CRITICAL

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "window_days": self.window_days,
            "anomalies": [a.to_dict() for a in self.anomalies],
            "health_score": self.health_score,
            "counts_by_severity": dict(self.counts_by_severity),
            "counts_by_type": dict(self.counts_by_type),
            "inspected_stages": list(self.inspected_stages),
            "lineage_run_id": self.lineage_run_id,
        }


# ─────────────────────────────────────────────────────────────────────
# Threshold loader
# ─────────────────────────────────────────────────────────────────────
@dataclass
class AnomalyThresholds:
    schema_version: str = "1.0.0"
    window_days: int = 14
    min_samples: int = 5

    duration_zscore_warn: float = 2.5
    duration_zscore_critical: float = 4.0

    failure_burst_zscore_warn: float = 2.5
    failure_burst_zscore_critical: float = 4.0

    snapshot_staleness_warn_hours: float = 36
    snapshot_staleness_critical_hours: float = 96

    quarantine_growth_warn: int = 20
    quarantine_growth_critical: int = 100

    regression_warn_share_warn: float = 0.20
    regression_warn_share_critical: float = 0.50

    tpi_drift_warn_pct: float = 0.08
    tpi_drift_critical_pct: float = 0.15

    expected_runs_per_window: Mapping[str, int] = field(default_factory=lambda: {
        "snapshot": 4, "regression": 4,
    })

    health_deduction: Mapping[str, int] = field(default_factory=lambda: {
        "CRITICAL": 20, "WARN": 5, "INFO": 1,
    })

    retention: Mapping[str, int] = field(default_factory=lambda: {
        "lineage_run_keep_days": 90,
        "prom_metrics_keep_days": 30,
        "quarantine_keep_days": 60,
    })


def load_thresholds(path: Path | str | None = None) -> AnomalyThresholds:
    """
    Load `config/anomaly.yml`. Missing file → defaults. PyYAML if available,
    else a minimal parser (good enough for the file's shape).
    """
    path = Path(path) if path else DEFAULT_CONFIG_PATH
    if not path.is_file():
        return AnomalyThresholds()

    text = path.read_text(encoding="utf-8")
    try:
        import yaml  # type: ignore
        raw = yaml.safe_load(text) or {}
    except ImportError:
        raw = _minimal_yaml_parse(text)

    if not isinstance(raw, dict):
        raise ValueError(f"{path} non è un mapping YAML")

    init: dict[str, Any] = {}
    fields = AnomalyThresholds.__dataclass_fields__
    for f_name in fields:
        if f_name in raw:
            init[f_name] = raw[f_name]
    return AnomalyThresholds(**init)


def _minimal_yaml_parse(text: str) -> dict[str, Any]:
    """Same parser shape used by F1.4. Supports the keys in anomaly.yml."""
    out: dict[str, Any] = {}
    stack: list[tuple[int, str, dict[str, Any]]] = []
    current_top: str | None = None
    current_map: dict[str, Any] | None = None

    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip())
        stripped = line.strip()
        if ":" not in stripped:
            continue
        key, _, val = stripped.partition(":")
        key = key.strip()
        val = val.strip()
        if indent == 0:
            if val == "" or val == "{}":
                current_top = key
                current_map = {} if val == "" else {}
                out[key] = current_map if val == "" else {}
                if val == "{}":
                    current_top = None
                    current_map = None
            else:
                out[key] = _coerce_scalar(val)
                current_top = None
                current_map = None
        else:
            # nested
            if current_map is None or current_top is None:
                continue
            if val == "" or val == "{}":
                continue  # don't support 2-level nesting in fallback
            current_map[key] = _coerce_scalar(val)
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
