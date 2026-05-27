"""
CLI entry point used by `.github/workflows/regression.yml`.

Runs the diff engine against a baseline / candidate pair, writes the
Markdown report, emits Prometheus metrics, and exits with:

  * 0  → SAFE
  * 1  → WARNING (CI is configured to still pass; the report shows warnings)
  * 2  → CRITICAL (CI fails)

This file is intentionally separate from `test_tpi_stability.py` so the
report can be generated even when the test suite is skipped (e.g. when
running just the snapshot-verify step).
"""
from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

# allow running as `python tests/regression/run_regression.py` from repo root
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from regression.helpers import load_snapshot, snapshot_dir_for  # noqa: E402
from serie_a_scout.analytics import (  # noqa: E402
    RegressionSeverity, load_thresholds, run_regression_diff, write_report,
)
from serie_a_scout.obs import emit_regression_metrics, emit_run_metrics  # noqa: E402
from serie_a_scout.core import RunContext, get_default_store  # noqa: E402


EXIT_SAFE = 0
EXIT_WARNING = 1
EXIT_CRITICAL = 2


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv or sys.argv[1:])
    th = load_thresholds(args.config)

    lineage_extra = {
        "baseline_season": args.baseline_season,
        "baseline_giornata": args.baseline_giornata,
        "candidate_season": args.candidate_season,
        "candidate_giornata": args.candidate_giornata,
        "candidate_dir": args.candidate_dir,
        "dimension": args.dimension,
    }
    final_run_id: str | None = None
    final_exit: int = EXIT_SAFE
    with RunContext(stage="regression", extra=lineage_extra) as run:
        final_run_id = run.run_id
        baseline = load_snapshot(args.baseline_season, args.baseline_giornata)
        if args.candidate_dir:
            cand_path = Path(args.candidate_dir)
            season = cand_path.parent.name
            gn = int(cand_path.name.split("_", 1)[1])
            candidate = load_snapshot(season, gn, base_dir=cand_path.parents[1])
        elif args.candidate_giornata is not None:
            candidate = load_snapshot(args.candidate_season or args.baseline_season,
                                      args.candidate_giornata)
        else:
            print("[regression] no candidate provided -> using baseline (determinism check)")
            candidate = baseline

        # Link consumed snapshots in lineage (provenance chain)
        store = get_default_store()
        for snap_dir in {baseline.snapshot_dir, candidate.snapshot_dir}:
            for art in store.find_artifacts(kind="snapshot", path_like=f"%{snap_dir.name}"):
                if art.artifact_id is not None:
                    run.register_input(art)

        diff = run_regression_diff(
            baseline.tpi_table, candidate.tpi_table,
            season=baseline.season, giornata=baseline.giornata,
            dimension=args.dimension, thresholds=th,
        )

        report_path = write_report(diff, out_dir=args.report_dir)
        print(f"[regression] report: {report_path}")
        run.register_artifact(
            kind="markdown",
            path=str(report_path),
            compute_checksum=True,
            meta={"severity": diff.severity.value,
                  "spearman": diff.spearman,
                  "top10": diff.top10_overlap,
                  "top20": diff.top20_overlap},
        )

        dist = diff.distribution_shift
        finite = [v for v in (dist.mean_delta_pct, dist.std_delta_pct, dist.p95_delta_pct)
                  if v is not None and not math.isnan(v)]
        max_shift = max(finite) if finite else 0.0

        metric_path = emit_regression_metrics(
            season=diff.season,
            giornata=diff.giornata,
            dimension=diff.dimension,
            spearman=diff.spearman,
            top10_overlap=diff.top10_overlap,
            top20_overlap=diff.top20_overlap,
            large_deltas=len(diff.large_deltas),
            distribution_shift_pct=max_shift,
            severity=diff.severity.value,
            failed=diff.severity.is_failure,
        )
        print(f"[regression] metrics: {metric_path}")
        run.register_artifact(kind="prom", path=str(metric_path),
                              meta={"emitter": "regression"})

        run.set_rows_out(diff.n_common)
        run.set_exit_code(
            EXIT_CRITICAL if diff.severity is RegressionSeverity.CRITICAL else
            (EXIT_WARNING if (diff.severity is RegressionSeverity.WARNING and args.fail_on_warning)
             else EXIT_SAFE)
        )
        if diff.severity is RegressionSeverity.CRITICAL:
            run.set_status("partial")  # CRITICAL is a known-bad outcome, not an exception
            final_exit = EXIT_CRITICAL
        elif diff.severity is RegressionSeverity.WARNING:
            final_exit = EXIT_WARNING if args.fail_on_warning else EXIT_SAFE

        print(f"[regression] severity={diff.severity.value} "
              f"spearman={diff.spearman:.4f} "
              f"top10={diff.top10_overlap}/10 "
              f"top20={diff.top20_overlap}/20")

        if diff.severity is RegressionSeverity.CRITICAL:
            print("[regression] CRITICAL — failure reasons:")
            for r in diff.failure_reasons:
                print(f"  - {r}")
        elif diff.severity is RegressionSeverity.WARNING:
            print("[regression] WARNING — warning reasons:")
            for r in diff.warning_reasons:
                print(f"  - {r}")

    # Emit metrics AFTER the context closes so status reflects the final state.
    if final_run_id is not None:
        rec = get_default_store().get_run(final_run_id)
        if rec is not None:
            emit_run_metrics(rec)
    return final_exit


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run TPI regression stability diff.")
    p.add_argument("--baseline-season", default="2025-26")
    p.add_argument("--baseline-giornata", type=int, default=36)
    p.add_argument("--candidate-season", default=None)
    p.add_argument("--candidate-giornata", type=int, default=None)
    p.add_argument("--candidate-dir", default=None,
                   help="Override: path to a snapshot directory")
    p.add_argument("--dimension", default="totale")
    p.add_argument("--config", default=None,
                   help="Path to regression.yml (default: config/regression.yml)")
    p.add_argument("--report-dir", default=None,
                   help="Override report output dir (default: reports/regression/)")
    p.add_argument("--fail-on-warning", action="store_true",
                   help="Treat WARNING as non-zero exit (default: pass on warning).")
    return p.parse_args(argv)


if __name__ == "__main__":
    sys.exit(main())
