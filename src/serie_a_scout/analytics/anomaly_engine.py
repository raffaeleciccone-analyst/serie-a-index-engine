"""
F1.5 anomaly-detection engine.

Aggregates evidence from:

  * lineage SQLite store (`pipeline_run`, `pipeline_artifact`)
  * snapshots directory (mtime of latest snapshot per season)
  * quarantine JSONL logs (`logs/quarantine/*.jsonl`)
  * historical regression reports (`reports/regression/*.md`)

…then runs a battery of detectors and returns an `AnomalyReport`.

Design rules:

* **Deterministic** — given the same inputs and `now()`, the engine
  produces the same anomaly set. The `now` parameter can be injected
  in tests to remove wall-clock dependency.
* **Graceful degradation** — every detector handles missing inputs by
  returning an empty list (and emitting a WARN-level INFO anomaly when
  the absence itself is meaningful, e.g. missing-data detector).
* **Every anomaly** carries severity + confidence + evidence +
  suggested_action + lineage references (run_ids when applicable).
* **No silent fails** — file-system errors are caught and surfaced as
  INFO anomalies of type `MISSING_DATA`.
"""
from __future__ import annotations

import json
import math
import re
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

import numpy as np

from ..core.lineage_store import (
    LineageStore, RunRecord, get_default_store,
)
from .anomaly_models import (
    AnomalyReport, AnomalySeverity, AnomalyThresholds, AnomalyType,
    BaselineWindow, DetectedAnomaly, load_thresholds,
)


_REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_SNAPSHOT_ROOT = _REPO_ROOT / "snapshots"
DEFAULT_QUARANTINE_DIR = _REPO_ROOT / "logs" / "quarantine"
DEFAULT_REGRESSION_REPORT_DIR = _REPO_ROOT / "reports" / "regression"


# ─────────────────────────────────────────────────────────────────────
# Baseline helpers (pure functions, no I/O)
# ─────────────────────────────────────────────────────────────────────
def rolling_window(
    values: Sequence[float], *, window: int,
) -> list[float]:
    """Trailing rolling-mean over a numeric sequence. Edge-aware."""
    if window <= 0:
        raise ValueError("window must be > 0")
    if not values:
        return []
    out: list[float] = []
    for i in range(len(values)):
        lo = max(0, i - window + 1)
        slice_ = values[lo: i + 1]
        out.append(float(np.mean(slice_)))
    return out


def compute_baseline(
    samples: Sequence[float],
    *,
    metric: str,
    window_days: int,
    min_samples: int,
) -> BaselineWindow:
    """Aggregate basic statistics over a numeric sample for z-score detectors."""
    arr = np.asarray(list(samples), dtype=float)
    arr = arr[np.isfinite(arr)]
    n = int(arr.size)
    if n == 0:
        mean = std = p50 = p95 = 0.0
    else:
        mean = float(np.mean(arr))
        std = float(np.std(arr, ddof=0))
        p50 = float(np.percentile(arr, 50))
        p95 = float(np.percentile(arr, 95))
    return BaselineWindow(
        metric=metric,
        n_samples=n,
        mean=mean,
        std=std,
        p50=p50,
        p95=p95,
        window_days=window_days,
        min_samples_required=min_samples,
    )


def _zscore_severity(
    z: float, *, warn: float, critical: float,
) -> AnomalySeverity:
    if abs(z) >= critical:
        return AnomalySeverity.CRITICAL
    if abs(z) >= warn:
        return AnomalySeverity.WARN
    return AnomalySeverity.INFO


def _zscore_confidence(z: float, *, warn: float, critical: float) -> float:
    """Map |z| to [0,1]: 0 at warn, 1 at 2× critical."""
    a = abs(z)
    if a <= warn:
        return min(1.0, max(0.0, (a / warn) * 0.5))
    span = max(1e-9, (critical - warn))
    extra = (a - warn) / span     # 0 at warn, 1 at critical
    return min(1.0, 0.5 + 0.5 * extra)


# ─────────────────────────────────────────────────────────────────────
# Engine
# ─────────────────────────────────────────────────────────────────────
@dataclass
class AnomalyEngineConfig:
    snapshot_root: Path = DEFAULT_SNAPSHOT_ROOT
    quarantine_dir: Path = DEFAULT_QUARANTINE_DIR
    regression_report_dir: Path = DEFAULT_REGRESSION_REPORT_DIR


class AnomalyEngine:
    """
    Detect anomalies using rolling windows from the lineage store +
    file-system inspection of snapshots and reports.

    `now` and `thresholds` can be injected to make every run deterministic.
    """

    def __init__(
        self,
        *,
        store: LineageStore | None = None,
        thresholds: AnomalyThresholds | None = None,
        config: AnomalyEngineConfig | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.store = store or get_default_store()
        self.th = thresholds or load_thresholds()
        self.cfg = config or AnomalyEngineConfig()
        self._now = now or (lambda: datetime.now(timezone.utc))

    # ────────────────── orchestration ──────────────────
    def scan(
        self, *, stages: Sequence[str] | None = None,
        since_run: str | None = None,
    ) -> AnomalyReport:
        """Run every detector and return a single rolled-up report."""
        inspected: list[str] = list(stages) if stages else ["snapshot", "regression",
                                                            "audit", "fix", "ingest",
                                                            "snapshot_restore"]
        anomalies: list[DetectedAnomaly] = []

        # ─ rolling-baseline detectors ─
        try:
            anomalies.extend(self._detect_run_duration_anomalies(inspected, since_run))
        except Exception as e:  # noqa: BLE001
            anomalies.append(self._fs_warning("duration_detector", str(e)))

        try:
            anomalies.extend(self._detect_failure_burst(inspected))
        except Exception as e:  # noqa: BLE001
            anomalies.append(self._fs_warning("failure_burst_detector", str(e)))

        # ─ file-system detectors ─
        try:
            anomalies.extend(self._detect_stale_snapshots())
        except Exception as e:  # noqa: BLE001
            anomalies.append(self._fs_warning("stale_snapshot_detector", str(e)))

        try:
            anomalies.extend(self._detect_quarantine_growth())
        except Exception as e:  # noqa: BLE001
            anomalies.append(self._fs_warning("quarantine_detector", str(e)))

        # ─ semantic detectors ─
        try:
            anomalies.extend(self._detect_regression_warning_rate())
        except Exception as e:  # noqa: BLE001
            anomalies.append(self._fs_warning("regression_rate_detector", str(e)))

        try:
            anomalies.extend(self._detect_regression_frequency_escalation())
        except Exception as e:  # noqa: BLE001
            anomalies.append(self._fs_warning("regression_freq_detector", str(e)))

        try:
            anomalies.extend(self._detect_tpi_distribution_drift())
        except Exception as e:  # noqa: BLE001
            anomalies.append(self._fs_warning("tpi_drift_detector", str(e)))

        try:
            anomalies.extend(self._detect_missing_data())
        except Exception as e:  # noqa: BLE001
            anomalies.append(self._fs_warning("missing_data_detector", str(e)))

        # ─ rollup ─
        counts_sev: dict[str, int] = {s.value: 0 for s in AnomalySeverity}
        counts_type: dict[str, int] = {t.value: 0 for t in AnomalyType}
        for a in anomalies:
            counts_sev[a.severity.value] += 1
            counts_type[a.type.value] += 1
        health = _compute_health(anomalies, self.th.health_deduction)

        return AnomalyReport(
            generated_at=self._now().isoformat(timespec="seconds"),
            window_days=self.th.window_days,
            anomalies=sorted(
                anomalies, key=lambda a: (-_severity_rank(a.severity), -a.confidence),
            ),
            health_score=health,
            counts_by_severity=counts_sev,
            counts_by_type=counts_type,
            inspected_stages=inspected,
        )

    # ───────────────────── detectors ─────────────────────
    def _detect_run_duration_anomalies(
        self, stages: Sequence[str], since_run: str | None,
    ) -> list[DetectedAnomaly]:
        out: list[DetectedAnomaly] = []
        window_cutoff = self._now() - timedelta(days=self.th.window_days)
        for stage in stages:
            runs = self._runs_in_window(stage, window_cutoff)
            durations = [(r.duration_ms / 1000.0) for r in runs if r.duration_ms is not None]
            if len(durations) < self.th.min_samples:
                continue
            baseline = compute_baseline(
                durations[:-1] if len(durations) > 1 else durations,
                metric=f"duration.{stage}",
                window_days=self.th.window_days,
                min_samples=self.th.min_samples,
            )
            if not baseline.is_usable:
                continue
            last = runs[-1]
            if last.duration_ms is None:
                continue
            value = last.duration_ms / 1000.0
            z = baseline.zscore(value)
            sev = _zscore_severity(
                z, warn=self.th.duration_zscore_warn,
                critical=self.th.duration_zscore_critical,
            )
            if sev is AnomalySeverity.INFO:
                continue
            if since_run is not None and last.run_id != since_run:
                # filtering by --since-run: only the matching run can fire
                continue
            out.append(DetectedAnomaly(
                type=AnomalyType.DURATION_ZSCORE_SPIKE,
                severity=sev,
                confidence=_zscore_confidence(
                    z, warn=self.th.duration_zscore_warn,
                    critical=self.th.duration_zscore_critical,
                ),
                value=value,
                threshold=baseline.mean + self.th.duration_zscore_warn * baseline.std,
                evidence={
                    "stage": stage,
                    "run_duration_sec": value,
                    "z_score": round(z, 3),
                    "baseline_mean_sec": round(baseline.mean, 3),
                    "baseline_std_sec": round(baseline.std, 3),
                    "baseline_n": baseline.n_samples,
                    "started_at": last.started_at,
                },
                suggested_action=(
                    f"Indaga la run `{last.run_id[:12]}` (stage {stage}). "
                    "Verifica logs, lock files o degradi DB. "
                    "Se la durata è giustificata, allarga la baseline."
                ),
                stage=stage,
                run_ids=[last.run_id],
                upstream_run_ids=[last.parent_run_id] if last.parent_run_id else [],
                baseline=baseline,
            ))
        return out

    def _detect_failure_burst(self, stages: Sequence[str]) -> list[DetectedAnomaly]:
        window_cutoff = self._now() - timedelta(days=self.th.window_days)
        out: list[DetectedAnomaly] = []
        for stage in stages:
            runs = self._runs_in_window(stage, window_cutoff)
            if len(runs) < self.th.min_samples:
                continue
            # Daily failure count series → z-score on the last day vs prior days.
            daily = self._daily_counts(runs, statuses=("fail",))
            if len(daily) < self.th.min_samples:
                continue
            baseline_samples = list(daily.values())[:-1]
            last_value = list(daily.values())[-1]
            baseline = compute_baseline(
                baseline_samples,
                metric=f"failures.{stage}",
                window_days=self.th.window_days,
                min_samples=self.th.min_samples,
            )
            if not baseline.is_usable:
                continue
            z = baseline.zscore(last_value)
            sev = _zscore_severity(
                z, warn=self.th.failure_burst_zscore_warn,
                critical=self.th.failure_burst_zscore_critical,
            )
            if sev is AnomalySeverity.INFO:
                continue
            recent_failed = [r for r in runs if r.status == "fail"][-5:]
            out.append(DetectedAnomaly(
                type=AnomalyType.RUN_FAILURE_BURST,
                severity=sev,
                confidence=_zscore_confidence(
                    z, warn=self.th.failure_burst_zscore_warn,
                    critical=self.th.failure_burst_zscore_critical,
                ),
                value=float(last_value),
                threshold=baseline.mean + self.th.failure_burst_zscore_warn * baseline.std,
                evidence={
                    "stage": stage,
                    "failures_today": last_value,
                    "z_score": round(z, 3),
                    "daily_counts": daily,
                },
                suggested_action=(
                    f"Più fallimenti del solito su stage `{stage}`. "
                    "Apri il runbook della stage, controlla credenziali / "
                    "rate-limit del provider e i lock file."
                ),
                stage=stage,
                run_ids=[r.run_id for r in recent_failed],
                baseline=baseline,
            ))
        return out

    def _detect_stale_snapshots(self) -> list[DetectedAnomaly]:
        out: list[DetectedAnomaly] = []
        root = self.cfg.snapshot_root
        if not root.is_dir():
            return out
        for season_dir in sorted(root.iterdir()):
            if not season_dir.is_dir():
                continue
            latest_g, latest_mtime = self._latest_giornata_dir(season_dir)
            if latest_g is None:
                continue
            age_hours = (self._now().timestamp() - latest_mtime) / 3600.0
            if age_hours < self.th.snapshot_staleness_warn_hours:
                continue
            sev = (
                AnomalySeverity.CRITICAL
                if age_hours >= self.th.snapshot_staleness_critical_hours
                else AnomalySeverity.WARN
            )
            confidence = min(1.0, age_hours / max(1.0, self.th.snapshot_staleness_critical_hours))
            out.append(DetectedAnomaly(
                type=AnomalyType.STALE_SNAPSHOT,
                severity=sev,
                confidence=confidence,
                value=round(age_hours, 2),
                threshold=float(self.th.snapshot_staleness_warn_hours),
                evidence={
                    "season": season_dir.name,
                    "latest_giornata": latest_g.name,
                    "age_hours": round(age_hours, 2),
                },
                suggested_action=(
                    f"Snapshot stagione `{season_dir.name}` invecchiato "
                    f"({age_hours:.1f}h). Esegui `python snapshots/take.py "
                    f"--season {season_dir.name} --giornata <N>` o verifica "
                    "lo scheduler dell'ingestion."
                ),
                stage="snapshot",
                artifact_path=_repo_rel(latest_g),
            ))
        return out

    def _detect_quarantine_growth(self) -> list[DetectedAnomaly]:
        out: list[DetectedAnomaly] = []
        qdir = self.cfg.quarantine_dir
        if not qdir.is_dir():
            return out
        window_cutoff = self._now() - timedelta(days=self.th.window_days)
        for f in sorted(qdir.glob("*.jsonl")):
            new_lines = self._count_jsonl_lines_since(f, window_cutoff)
            if new_lines < self.th.quarantine_growth_warn:
                continue
            sev = (
                AnomalySeverity.CRITICAL
                if new_lines >= self.th.quarantine_growth_critical
                else AnomalySeverity.WARN
            )
            confidence = min(1.0, new_lines / max(1, self.th.quarantine_growth_critical))
            out.append(DetectedAnomaly(
                type=AnomalyType.QUARANTINE_GROWTH,
                severity=sev,
                confidence=confidence,
                value=float(new_lines),
                threshold=float(self.th.quarantine_growth_warn),
                evidence={
                    "file": _repo_rel(f),
                    "new_lines_in_window": new_lines,
                    "window_days": self.th.window_days,
                },
                suggested_action=(
                    f"Quarantena `{f.name}` cresciuta di {new_lines} righe in "
                    f"{self.th.window_days}gg. Probabile drift del mapping "
                    "anagrafico o cambio schema provider — esegui "
                    "`python parte4_aggiorna.py --validate-only`."
                ),
                stage="ingest",
                artifact_path=_repo_rel(f),
            ))
        return out

    def _detect_regression_warning_rate(self) -> list[DetectedAnomaly]:
        window_cutoff = self._now() - timedelta(days=self.th.window_days)
        runs = self._runs_in_window("regression", window_cutoff)
        if not runs:
            return []
        total = len(runs)
        warn_or_worse = sum(
            1 for r in runs
            if r.status in ("partial", "fail")
            or (r.extra and r.extra.get("severity") in ("WARNING", "CRITICAL"))
        )
        if total == 0:
            return []
        share = warn_or_worse / total
        if share < self.th.regression_warn_share_warn:
            return []
        sev = (
            AnomalySeverity.CRITICAL
            if share >= self.th.regression_warn_share_critical
            else AnomalySeverity.WARN
        )
        confidence = min(1.0, share / max(0.01, self.th.regression_warn_share_critical))
        return [DetectedAnomaly(
            type=AnomalyType.REGRESSION_WARNING_RATE,
            severity=sev,
            confidence=confidence,
            value=round(share, 4),
            threshold=self.th.regression_warn_share_warn,
            evidence={
                "regression_runs_in_window": total,
                "warn_or_worse": warn_or_worse,
                "share": round(share, 4),
            },
            suggested_action=(
                "Troppi run regression hanno chiuso in WARNING/CRITICAL. "
                "Apri `docs/runbooks/regression_failure.md` e verifica se "
                "la baseline ha bisogno di rebaseline o se c'è drift reale."
            ),
            stage="regression",
            run_ids=[r.run_id for r in runs[-5:]],
        )]

    def _detect_regression_frequency_escalation(self) -> list[DetectedAnomaly]:
        """Half-window vs other half: more failures in the recent half = escalation."""
        window_cutoff = self._now() - timedelta(days=self.th.window_days)
        runs = self._runs_in_window("regression", window_cutoff)
        if len(runs) < 4:
            return []
        midpoint = self._now() - timedelta(days=self.th.window_days / 2)

        def is_warn(r: RunRecord) -> bool:
            return r.status in ("partial", "fail") or (
                r.extra and r.extra.get("severity") in ("WARNING", "CRITICAL")
            )

        recent = [r for r in runs if datetime.fromisoformat(r.started_at) >= midpoint]
        prior = [r for r in runs if datetime.fromisoformat(r.started_at) < midpoint]
        if not recent or not prior:
            return []
        recent_share = sum(1 for r in recent if is_warn(r)) / len(recent)
        prior_share = sum(1 for r in prior if is_warn(r)) / max(1, len(prior))
        delta = recent_share - prior_share
        if delta < 0.2:                # need >20pp escalation
            return []
        sev = AnomalySeverity.CRITICAL if delta >= 0.4 else AnomalySeverity.WARN
        confidence = min(1.0, delta / 0.5)
        return [DetectedAnomaly(
            type=AnomalyType.REGRESSION_FREQUENCY_ESCALATION,
            severity=sev,
            confidence=confidence,
            value=round(delta, 4),
            threshold=0.2,
            evidence={
                "recent_share": round(recent_share, 4),
                "prior_share": round(prior_share, 4),
                "recent_n": len(recent),
                "prior_n": len(prior),
            },
            suggested_action=(
                "Quota di regression WARN/CRITICAL in escalation nella metà "
                "recente del periodo. Indaga la causa e considera un "
                "rebaseline pianificato."
            ),
            stage="regression",
            run_ids=[r.run_id for r in recent[-5:]],
        )]

    def _detect_tpi_distribution_drift(self) -> list[DetectedAnomaly]:
        """
        Scan recent regression markdown reports for the distribution-shift
        table; if max relative shift exceeds threshold, fire.
        """
        reports = self._iter_recent_regression_reports()
        if not reports:
            return []
        out: list[DetectedAnomaly] = []
        latest_path, latest_shift = reports[-1]
        if latest_shift is None or latest_shift < self.th.tpi_drift_warn_pct:
            return out
        sev = (
            AnomalySeverity.CRITICAL
            if latest_shift >= self.th.tpi_drift_critical_pct
            else AnomalySeverity.WARN
        )
        confidence = min(1.0, latest_shift / max(0.01, self.th.tpi_drift_critical_pct))
        out.append(DetectedAnomaly(
            type=AnomalyType.TPI_DISTRIBUTION_DRIFT,
            severity=sev,
            confidence=confidence,
            value=round(latest_shift, 4),
            threshold=self.th.tpi_drift_warn_pct,
            evidence={
                "report": _repo_rel(latest_path),
                "max_distribution_shift_pct": round(latest_shift, 4),
                "history_len": len(reports),
            },
            suggested_action=(
                "Drift della distribuzione TPI accumulato. "
                "Verifica se è intenzionale (nuova formula z-score, SOS) o "
                "se proviene da provider corruption; vedi "
                "`docs/runbooks/regression_failure.md` §4."
            ),
            stage="regression",
            artifact_path=_repo_rel(latest_path),
        ))
        return out

    def _detect_missing_data(self) -> list[DetectedAnomaly]:
        out: list[DetectedAnomaly] = []
        window_cutoff = self._now() - timedelta(days=self.th.window_days)
        for stage, expected in self.th.expected_runs_per_window.items():
            seen = self._runs_in_window(stage, window_cutoff)
            n = len(seen)
            if n >= expected:
                continue
            sev = AnomalySeverity.CRITICAL if n == 0 else AnomalySeverity.WARN
            confidence = 1.0 if n == 0 else min(1.0, (expected - n) / expected)
            out.append(DetectedAnomaly(
                type=AnomalyType.MISSING_DATA,
                severity=sev,
                confidence=confidence,
                value=float(n),
                threshold=float(expected),
                evidence={
                    "stage": stage,
                    "observed_runs": n,
                    "expected_runs": expected,
                    "window_days": self.th.window_days,
                },
                suggested_action=(
                    f"Solo {n} run di stage `{stage}` negli ultimi "
                    f"{self.th.window_days}gg (attesi ≥{expected}). "
                    "Lo scheduler è giù? Controlla il journal e l'orchestrator."
                ),
                stage=stage,
            ))
        return out

    # ───────────────────── helpers ─────────────────────
    def _runs_in_window(
        self, stage: str, window_cutoff: datetime,
    ) -> list[RunRecord]:
        runs = self.store.iter_runs(stage=stage, limit=10000)
        kept: list[RunRecord] = []
        for r in runs:
            try:
                ts = datetime.fromisoformat(r.started_at)
            except ValueError:
                continue
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            if ts >= window_cutoff:
                kept.append(r)
        kept.sort(key=lambda r: r.started_at)
        return kept

    @staticmethod
    def _daily_counts(
        runs: Sequence[RunRecord], *, statuses: Sequence[str],
    ) -> dict[str, int]:
        buckets: dict[str, int] = {}
        for r in runs:
            try:
                day = r.started_at[:10]   # YYYY-MM-DD prefix
            except (TypeError, IndexError):
                continue
            buckets.setdefault(day, 0)
            if r.status in statuses:
                buckets[day] += 1
        return dict(sorted(buckets.items()))

    @staticmethod
    def _latest_giornata_dir(season_dir: Path) -> tuple[Path | None, float]:
        latest: tuple[Path, float] | None = None
        for g in season_dir.iterdir():
            if not g.is_dir():
                continue
            if not g.name.startswith("giornata_"):
                continue
            try:
                mtime = g.stat().st_mtime
            except OSError:
                continue
            if latest is None or mtime > latest[1]:
                latest = (g, mtime)
        if latest is None:
            return None, 0.0
        return latest

    def _count_jsonl_lines_since(
        self, path: Path, cutoff: datetime,
    ) -> int:
        """Count JSON lines whose `ts` >= cutoff (or all if `ts` absent)."""
        if not path.is_file():
            return 0
        cutoff_naive = cutoff.replace(tzinfo=None)
        n = 0
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ts_str = rec.get("ts") or rec.get("created_at")
                if ts_str is None:
                    n += 1
                    continue
                try:
                    ts = datetime.fromisoformat(str(ts_str))
                except ValueError:
                    n += 1
                    continue
                ts_naive = ts.replace(tzinfo=None)
                if ts_naive >= cutoff_naive:
                    n += 1
        return n

    def _iter_recent_regression_reports(self) -> list[tuple[Path, float | None]]:
        """Return (path, max_distribution_shift_pct) for reports in window."""
        rdir = self.cfg.regression_report_dir
        if not rdir.is_dir():
            return []
        cutoff = self._now() - timedelta(days=self.th.window_days)
        cutoff_ts = cutoff.timestamp()
        out: list[tuple[Path, float | None]] = []
        for p in sorted(rdir.glob("regression_*.md")):
            try:
                if p.stat().st_mtime < cutoff_ts:
                    continue
            except OSError:
                continue
            out.append((p, _parse_regression_max_shift(p)))
        return out

    def _fs_warning(self, source: str, message: str) -> DetectedAnomaly:
        return DetectedAnomaly(
            type=AnomalyType.MISSING_DATA,
            severity=AnomalySeverity.INFO,
            confidence=0.5,
            value=0.0,
            threshold=0.0,
            evidence={"source": source, "error": message[:240]},
            suggested_action=(
                f"Detector `{source}` ha intercettato un errore: {message[:200]}. "
                "Indaga ma non considerare bloccante."
            ),
            stage=source,
        )


# ─────────────────────────────────────────────────────────────────────
# Module-level helpers exposed for tests
# ─────────────────────────────────────────────────────────────────────
_REPORT_DIST_RE = re.compile(
    r"\|\s*(mean|std|p95)\s*\|.*?\|.*?\|\s*([0-9.+-]+)%\s*\|", re.IGNORECASE,
)


def _repo_rel(p: Path) -> str:
    """Return repo-relative POSIX path when possible; absolute POSIX otherwise."""
    try:
        return str(p.resolve().relative_to(_REPO_ROOT)).replace("\\", "/")
    except (ValueError, OSError):
        return str(p).replace("\\", "/")


def _parse_regression_max_shift(path: Path) -> float | None:
    """
    Pull the largest `|Δ relativo|` value from the distribution table in a
    regression report. Returns the fraction (0.0123 = 1.23%) or None.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    pct: list[float] = []
    for m in _REPORT_DIST_RE.finditer(text):
        try:
            pct.append(abs(float(m.group(2))) / 100.0)
        except ValueError:
            continue
    if not pct:
        return None
    return max(pct)


def _severity_rank(s: AnomalySeverity) -> int:
    return {AnomalySeverity.INFO: 0, AnomalySeverity.WARN: 1, AnomalySeverity.CRITICAL: 2}[s]


def _compute_health(
    anomalies: Iterable[DetectedAnomaly],
    deductions: Mapping[str, int],
) -> int:
    score = 100
    for a in anomalies:
        score -= int(deductions.get(a.severity.value, 0))
    return max(0, min(100, score))
