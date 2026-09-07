"""
audit.py — Runner principale dell'audit forense.

Esegue tutti i check registrati in lib/checks.py, raccoglie i Finding,
calcola il punteggio di affidabilità e salva un report JSON in
`audit/reports/audit_<timestamp>.json`.

Uso:
  python audit/audit.py                  # esegue tutti i check
  python audit/audit.py --quiet          # solo riepilogo
  python audit/audit.py --check DUP-001  # solo un check specifico (futuro)
"""
from __future__ import annotations
import argparse
import datetime as dt
import sys
from pathlib import Path

# Best-effort: expose serie_a_scout for lineage tracking. If absent, audit
# continues to work — F0/F1.3 backwards compatibility.
_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from lib.db import DB_NAME
from lib.findings import Area, Finding, Report, Severity
from lib.checks import CHECKS
from lib.log import get_logger
from lib.journal import append_event, new_run_id

try:
    from serie_a_scout.core import RunContext, get_default_store
    from serie_a_scout.obs import emit_run_metrics
    _LINEAGE_AVAILABLE = True
except Exception:  # noqa: BLE001
    RunContext = None  # type: ignore[assignment]
    _LINEAGE_AVAILABLE = False

log = get_logger("audit")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quiet", action="store_true", help="solo riepilogo, no dettaglio")
    ap.add_argument("--out", default=None, help="path output JSON")
    args = ap.parse_args()

    report = Report(db_name=DB_NAME)
    run_id = new_run_id()
    append_event("audit.py", "started", run_id=run_id)
    log.info(f"╔══ Audit forense `{DB_NAME}` (run_id={run_id[:8]}) ══╗")

    lineage_ctx = (
        RunContext(stage="audit", extra={"db_name": DB_NAME, "journal_run_id": run_id})
        if _LINEAGE_AVAILABLE else None
    )
    if lineage_ctx is not None:
        lineage_ctx.__enter__()

    for fn in CHECKS:
        try:
            log.info(f"▶  {fn.__name__}")
            fn(report)
        except Exception as e:
            log.exception(f"❌ Check {fn.__name__} crashato: {e}")
            if lineage_ctx is not None:
                lineage_ctx.add_event("check.crashed", check=fn.__name__,
                                      error=type(e).__name__, message=str(e))
            # Il crash entra nel report. Prima restava solo nel log: il JSON e il
            # riepilogo contavano i findings dei check riusciti e stampavano un
            # reliability score come se fossero girati tutti. Per mesi tre check
            # sono morti a ogni esecuzione — due su una tabella che non esiste
            # piu', uno su una query rotta — e il rapporto continuava a dire
            # 81/100 senza mai nominarli. Un controllo che non gira non e' un
            # controllo passato, ed e' l'unica cosa che questo progetto non puo'
            # permettersi di lasciar intendere.
            report.add(Finding(
                code="AUD-001", area=Area.PIPELINE, severity=Severity.HIGH,
                title=f"Check `{fn.__name__}` non eseguito: {type(e).__name__}",
                table=None, rows_affected=0,
                description=(f"Il controllo e' crashato e non ha esaminato niente. "
                             f"Quello che doveva verificare resta non verificato. "
                             f"Errore: {e}"),
                root_cause="Query o schema fuori sincrono con il codice del check.",
                fix_available=False,
                fix_strategy=f"Correggi {fn.__name__} in audit/lib/checks.py, poi rilancia.",
            ))

    report.completed_at = dt.datetime.now().isoformat()

    # Output JSON
    out_dir = Path(__file__).parent / "reports"
    out_dir.mkdir(exist_ok=True)
    ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = Path(args.out) if args.out else out_dir / f"audit_{ts}.json"
    out_path.write_text(report.to_json(), encoding="utf-8")
    if lineage_ctx is not None:
        lineage_ctx.register_artifact(
            kind="json", path=str(out_path),
            compute_checksum=True,
            rows=len(report.findings),
            meta={"reliability_score": report.reliability_score()},
        )

    # Riepilogo console
    print()
    print("═" * 60)
    print(f"  AUDIT REPORT — {DB_NAME}")
    print("═" * 60)
    print(f"Tabelle ispezionate: {len(report.tables_inspected)}")
    print(f"Findings totali:     {len(report.findings)}")
    sev = report.by_severity()
    for s in Severity:
        n = sev[s.value]
        if n:
            print(f"  {s.value.upper():8s}: {n}")
    print(f"\nReliability score:   {report.reliability_score()}/100")
    print(f"Report JSON:         {out_path}")
    print()

    if not args.quiet:
        print("─── Top findings (HIGH/CRITICAL) ───")
        for f in sorted(report.findings, key=lambda x: ["info","low","medium","high","critical"].index(x.severity.value), reverse=True):
            if f.severity in (Severity.HIGH, Severity.CRITICAL):
                print(f"\n  [{f.severity.value.upper():8s}] {f.code}: {f.title}")
                if f.rows_affected:
                    print(f"    righe affette: {f.rows_affected}")
                if f.root_cause:
                    print(f"    root cause: {f.root_cause}")
                if f.fix_strategy:
                    print(f"    fix: {f.fix_strategy[:200]}")

    # exit code: 0=clean, 1=anomalie, 2=critiche
    if sev.get("critical", 0):
        rc = 2
    elif sev.get("high", 0):
        rc = 1
    else:
        rc = 0

    append_event("audit.py", "completed",
                 run_id=run_id, exit_code=rc,
                 score=report.reliability_score(),
                 by_severity=sev,
                 payload={"report_path": str(out_path),
                          "findings_total": len(report.findings),
                          "lineage_run_id": lineage_ctx.run_id if lineage_ctx else None})

    if lineage_ctx is not None:
        lineage_ctx.set_rows_out(len(report.findings))
        lineage_ctx.set_exit_code(rc)
        if rc == 2:
            lineage_ctx.set_status("partial")
        lineage_ctx.add_event("audit.completed",
                              score=report.reliability_score(),
                              by_severity=sev)
        lineage_ctx.__exit__(None, None, None)
        try:
            emit_run_metrics(get_default_store().get_run(lineage_ctx.run_id))
        except Exception:  # noqa: BLE001
            pass

    return rc


if __name__ == "__main__":
    import sys
    sys.exit(main())
