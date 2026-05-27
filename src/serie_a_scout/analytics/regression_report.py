"""
Human-readable regression reports.

Given a `RegressionDiff` produced by `regression_diff.run_regression_diff()`,
this module emits a Markdown document into `reports/regression/` and returns
the path. The report is the artefact uploaded by the GitHub Action.

Design goals:
  * the report is self-contained — a reviewer can open it without re-running
    the diff and understand what changed.
  * markdown tables only (no HTML), so it renders correctly on GitHub.
  * the file name is deterministic: `regression_<UTC-ts>_<season>_g<NN>.md`.
"""
from __future__ import annotations

import math
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from .regression_diff import (
    DistributionShift, MovedPlayer, RegressionDiff, RegressionSeverity,
)


_REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_REPORT_DIR = _REPO_ROOT / "reports" / "regression"


def render_report(diff: RegressionDiff) -> str:
    """Render the diff as a Markdown document."""
    th = diff.thresholds
    badge = _severity_badge(diff.severity)

    sections: list[str] = []
    sections.append(f"# Regression TPI — {diff.season} · giornata {diff.giornata:02d}")
    sections.append("")
    sections.append(f"**Status:** {badge}")
    sections.append(f"**Dimension:** `{diff.dimension}`")
    sections.append(f"**Generated:** {datetime.now(timezone.utc).isoformat(timespec='seconds')}")
    sections.append("")

    sections.append("## Sintesi")
    sections.append("")
    sections.append("| Metric | Value | Threshold (warn / crit) |")
    sections.append("|---|---:|---:|")
    sections.append(f"| Spearman ρ | {_fmt_float(diff.spearman, 4)} | ≥ {th.spearman_warn} / ≥ {th.spearman_min} |")
    sections.append(f"| Top-10 overlap | {diff.top10_overlap}/10 | ≥ {th.top10_overlap_warn} / ≥ {th.top10_overlap_min} |")
    sections.append(f"| Top-20 overlap | {diff.top20_overlap}/20 | ≥ {th.top20_overlap_warn} / ≥ {th.top20_overlap_min} |")
    sections.append(f"| Δ top player | {_fmt_float(diff.top_player_delta, 4)} | ≤ {th.top_player_delta_warn} / ≤ {th.max_top_player_delta} |")
    sections.append(f"| Players baseline | {diff.n_baseline} | – |")
    sections.append(f"| Players candidate | {diff.n_candidate} | – |")
    sections.append(f"| Common | {diff.n_common} | ≥ {th.min_common_player_share:.0%} |")
    sections.append(f"| Large deltas (>{th.large_delta_threshold}) | {len(diff.large_deltas)} | – |")
    sections.append("")

    sections.append("## Distribuzione TPI")
    sections.append("")
    sections.append(_distribution_table(diff.distribution_shift, th))
    sections.append("")

    if diff.severity is not RegressionSeverity.SAFE:
        sections.append("## Perché ha fallito" if diff.severity is RegressionSeverity.CRITICAL else "## Avvisi")
        sections.append("")
        bucket = diff.failure_reasons if diff.severity is RegressionSeverity.CRITICAL else diff.warning_reasons
        for reason in bucket:
            sections.append(f"- {reason}")
        if diff.severity is RegressionSeverity.CRITICAL and diff.warning_reasons:
            sections.append("")
            sections.append("**Avvisi addizionali:**")
            for reason in diff.warning_reasons:
                sections.append(f"- {reason}")
        sections.append("")

    sections.append("## Biggest climbers")
    sections.append("")
    sections.append(_movement_table(diff.biggest_climbers, mode="climber"))
    sections.append("")

    sections.append("## Biggest fallers")
    sections.append("")
    sections.append(_movement_table(diff.biggest_fallers, mode="faller"))
    sections.append("")

    sections.append("## Giocatori con drift sopra soglia")
    sections.append("")
    if not diff.large_deltas:
        sections.append("_Nessun giocatore oltre la soglia configurata._")
    else:
        sections.append(_movement_table(diff.large_deltas[:25], mode="abs"))
        if len(diff.large_deltas) > 25:
            sections.append("")
            sections.append(f"_…e altri {len(diff.large_deltas) - 25} giocatori (tronco a 25 per leggibilità)._")
    sections.append("")

    sections.append("## Configurazione thresholds")
    sections.append("")
    sections.append("```yaml")
    for k, v in asdict(th).items():
        if k == "leagues":
            continue
        sections.append(f"{k}: {v}")
    sections.append("```")
    sections.append("")

    return "\n".join(sections).rstrip() + "\n"


def write_report(
    diff: RegressionDiff,
    *,
    out_dir: Path | str | None = None,
    timestamp: datetime | None = None,
) -> Path:
    """Write the rendered report and return its path."""
    out = Path(out_dir) if out_dir else DEFAULT_REPORT_DIR
    out.mkdir(parents=True, exist_ok=True)
    ts = (timestamp or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
    fname = f"regression_{ts}_{diff.season}_g{diff.giornata:02d}.md"
    path = out / fname
    path.write_text(render_report(diff), encoding="utf-8")
    return path


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────
def _severity_badge(s: RegressionSeverity) -> str:
    return {
        RegressionSeverity.SAFE:     "**PASS** · SAFE",
        RegressionSeverity.WARNING:  "**PASS (with warnings)** · WARNING",
        RegressionSeverity.CRITICAL: "**FAIL** · CRITICAL",
    }[s]


def _fmt_float(v: float, digits: int) -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "n/a"
    return f"{v:.{digits}f}"


def _distribution_table(dist: DistributionShift, th) -> str:  # type: ignore[no-untyped-def]
    lines = [
        "| Statistic | Baseline | Candidate | Δ relativo | Warn / Crit |",
        "|---|---:|---:|---:|---:|",
        f"| mean | {_fmt_float(dist.mean_old, 4)} | {_fmt_float(dist.mean_new, 4)} | "
        f"{_fmt_pct(dist.mean_delta_pct)} | "
        f"{th.distribution_mean_delta_pct_warn:.0%} / {th.distribution_mean_delta_pct:.0%} |",
        f"| std | {_fmt_float(dist.std_old, 4)} | {_fmt_float(dist.std_new, 4)} | "
        f"{_fmt_pct(dist.std_delta_pct)} | "
        f"{th.distribution_std_delta_pct_warn:.0%} / {th.distribution_std_delta_pct:.0%} |",
        f"| p95 | {_fmt_float(dist.p95_old, 4)} | {_fmt_float(dist.p95_new, 4)} | "
        f"{_fmt_pct(dist.p95_delta_pct)} | "
        f"{th.distribution_p95_delta_pct_warn:.0%} / {th.distribution_p95_delta_pct:.0%} |",
    ]
    return "\n".join(lines)


def _fmt_pct(v: float) -> str:
    if v is None or math.isnan(v):
        return "n/a"
    if math.isinf(v):
        return "∞"
    return f"{v:.2%}"


def _movement_table(moves: Iterable[MovedPlayer], *, mode: str) -> str:
    rows = list(moves)
    if not rows:
        return "_Nessun dato._"
    header = "| Player | Squadra | Old TPI | New TPI | Δ TPI | Old rank | New rank | Δ rank |"
    sep = "|---|---|---:|---:|---:|---:|---:|---:|"
    body = [
        f"| {m.nome} | {m.squadra} | {_fmt_float(m.old_tpi, 4)} | {_fmt_float(m.new_tpi, 4)} | "
        f"{_fmt_signed(m.delta, 4)} | {m.old_rank} | {m.new_rank} | {_fmt_signed_int(m.rank_delta)} |"
        for m in rows
    ]
    return "\n".join([header, sep, *body])


def _fmt_signed(v: float, digits: int) -> str:
    if v is None or math.isnan(v):
        return "n/a"
    return f"{v:+.{digits}f}"


def _fmt_signed_int(v: int) -> str:
    return f"{v:+d}"
