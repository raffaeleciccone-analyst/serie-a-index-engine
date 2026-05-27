"""
Deterministic Markdown reports for anomaly scans.

Default file name is `anomaly_latest.md` (no wall-clock timestamp in the
filename — the timestamp lives inside the document). A per-scan file can
be produced as `anomaly_<run_id>.md` when a lineage run-id is provided.

The renderer is pure: same `AnomalyReport` in → same string out.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from .anomaly_models import AnomalyReport, AnomalySeverity, DetectedAnomaly


_REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_REPORT_DIR = _REPO_ROOT / "reports" / "anomaly"


def render_report(report: AnomalyReport) -> str:
    sections: list[str] = []
    sections.append("# Anomaly scan report")
    sections.append("")
    sections.append(f"**Generated:** {report.generated_at}")
    sections.append(f"**Window:** {report.window_days} days")
    sections.append(f"**Lineage run:** "
                    f"{('`' + report.lineage_run_id[:12] + '`') if report.lineage_run_id else '–'}")
    sections.append(f"**Highest severity:** {_badge(report.severity)}")
    sections.append(f"**Operational health score:** **{report.health_score}/100**")
    sections.append("")

    # Counters
    sections.append("## Counts")
    sections.append("")
    sections.append("| Severity | Count |")
    sections.append("|---|---:|")
    for sev in (AnomalySeverity.CRITICAL, AnomalySeverity.WARN, AnomalySeverity.INFO):
        sections.append(f"| {sev.value} | {report.counts_by_severity.get(sev.value, 0)} |")
    sections.append("")

    if not report.anomalies:
        sections.append("## Anomalies")
        sections.append("")
        sections.append("_Nessuna anomalia rilevata._")
        sections.append("")
    else:
        sections.append("## Anomalies")
        sections.append("")
        for a in report.anomalies:
            sections.append(_render_anomaly_block(a))
            sections.append("")

    # By type
    sections.append("## Breakdown per tipo")
    sections.append("")
    sections.append("| Type | Count |")
    sections.append("|---|---:|")
    for t, n in sorted(report.counts_by_type.items()):
        sections.append(f"| `{t}` | {n} |")
    sections.append("")

    # Inspected stages
    sections.append("## Stages ispezionate")
    sections.append("")
    for s in report.inspected_stages:
        sections.append(f"- `{s}`")
    sections.append("")

    # Suggested actions roll-up
    sections.append("## Suggested actions")
    sections.append("")
    if not report.anomalies:
        sections.append("_Nessuna azione raccomandata._")
    else:
        for i, a in enumerate(report.anomalies, 1):
            sections.append(f"{i}. **[{a.severity.value}/{a.type.value}]** {a.suggested_action}")
    sections.append("")

    return "\n".join(sections).rstrip() + "\n"


def write_report(
    report: AnomalyReport,
    *,
    out_dir: Path | str | None = None,
    file_name: str = "anomaly_latest.md",
) -> Path:
    """Write the rendered report. Filename is deterministic by default."""
    out = Path(out_dir) if out_dir else DEFAULT_REPORT_DIR
    out.mkdir(parents=True, exist_ok=True)
    path = out / file_name
    path.write_text(render_report(report), encoding="utf-8")
    return path


def write_json_dump(
    report: AnomalyReport,
    *,
    out_dir: Path | str | None = None,
    file_name: str = "anomaly_latest.json",
) -> Path:
    out = Path(out_dir) if out_dir else DEFAULT_REPORT_DIR
    out.mkdir(parents=True, exist_ok=True)
    path = out / file_name
    path.write_text(
        json.dumps(report.to_dict(), indent=2, sort_keys=True, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    return path


# ─────────────────────────────────────────────────────────────────────
# Internals
# ─────────────────────────────────────────────────────────────────────
def _render_anomaly_block(a: DetectedAnomaly) -> str:
    lines: list[str] = []
    lines.append(f"### {_badge(a.severity)} · `{a.type.value}`")
    lines.append("")
    lines.append("| Field | Value |")
    lines.append("|---|---|")
    lines.append(f"| stage | `{a.stage or '–'}` |")
    lines.append(f"| confidence | {a.confidence:.2f} |")
    lines.append(f"| value | {_fmt_num(a.value)} |")
    lines.append(f"| threshold | {_fmt_num(a.threshold)} |")
    if a.artifact_path:
        lines.append(f"| artifact | `{a.artifact_path}` |")
    if a.run_ids:
        lines.append(f"| run_ids | {_format_run_ids(a.run_ids)} |")
    if a.upstream_run_ids:
        lines.append(f"| upstream | {_format_run_ids(a.upstream_run_ids)} |")
    if a.downstream_run_ids:
        lines.append(f"| downstream | {_format_run_ids(a.downstream_run_ids)} |")
    if a.baseline is not None and a.baseline.is_usable:
        lines.append(
            f"| baseline | n={a.baseline.n_samples} "
            f"mean={a.baseline.mean:.3f} std={a.baseline.std:.3f} "
            f"p95={a.baseline.p95:.3f} |"
        )
    lines.append("")
    lines.append("**Evidence**")
    lines.append("")
    lines.append("```json")
    lines.append(json.dumps(a.evidence, indent=2, sort_keys=True, default=str))
    lines.append("```")
    lines.append("")
    lines.append("**Suggested action**")
    lines.append("")
    lines.append(f"> {a.suggested_action}")
    return "\n".join(lines)


def _badge(s: AnomalySeverity) -> str:
    return {
        AnomalySeverity.INFO: "🟦 INFO",
        AnomalySeverity.WARN: "🟧 WARN",
        AnomalySeverity.CRITICAL: "🟥 CRITICAL",
    }[s]


def _fmt_num(v: float) -> str:
    if v is None:
        return "–"
    if isinstance(v, float):
        if v != v:                              # NaN
            return "n/a"
        return f"{v:.4f}".rstrip("0").rstrip(".")
    return str(v)


def _format_run_ids(ids: Iterable[str]) -> str:
    short = ["`" + str(r)[:12] + "`" for r in ids if r]
    return ", ".join(short) if short else "–"
