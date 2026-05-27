"""
journal.py — Append-only audit trail in JSONL.

Ogni esecuzione di audit/fix/prevent/parte4/ecc. può chiamare `append_event`
per registrare uno snapshot dell'evento. Il file è in `audit/reports/journal.jsonl`
ed è append-only (mai sovrascritto). Per inviarlo a un log management esterno
basta tail-fwd del file.

Schema evento minimo:
  {ts, run_id, source, event, exit_code?, score?, by_severity?, payload?}
"""
from __future__ import annotations
import datetime as dt
import json
import os
import platform
import uuid
from pathlib import Path
from typing import Any

JOURNAL_PATH = Path(__file__).parent.parent / "reports" / "journal.jsonl"


def _now_iso() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def new_run_id() -> str:
    return uuid.uuid4().hex


def append_event(
    source: str,
    event: str,
    *,
    run_id: str | None = None,
    exit_code: int | None = None,
    score: int | None = None,
    by_severity: dict[str, int] | None = None,
    payload: dict[str, Any] | None = None,
) -> str:
    """Appende una riga JSONL al journal. Ritorna il run_id usato."""
    rid = run_id or new_run_id()
    rec = {
        "ts": _now_iso(),
        "run_id": rid,
        "source": source,
        "event": event,
        "host": platform.node(),
        "pid": os.getpid(),
    }
    if exit_code is not None: rec["exit_code"] = exit_code
    if score is not None: rec["score"] = score
    if by_severity is not None: rec["by_severity"] = by_severity
    if payload: rec["payload"] = payload

    JOURNAL_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(JOURNAL_PATH, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
    return rid


def tail(n: int = 20) -> list[dict]:
    """Ritorna le ultime N entry (per debug/CLI)."""
    if not JOURNAL_PATH.exists():
        return []
    lines = JOURNAL_PATH.read_text(encoding="utf-8").splitlines()
    return [json.loads(l) for l in lines[-n:] if l.strip()]
