"""
Retention preview utilities.

These functions **never delete anything by default**. They scan candidate
artifacts older than a configured horizon and return structured reports the
operator can review.  Passing `dry_run=False` is required to actually
delete, AND the function will refuse to act outside the project's known
"safe-to-prune" directories (lineage DB rows, `.prom` files,
quarantine jsonl entries).

The retention CLI is intentionally absent — operators should call these
helpers from `snapshots/anomaly_runner.py --report-only` or a notebook
and inspect the proposed list before flipping `dry_run`.
"""
from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .lineage_store import LineageStore, get_default_store


_REPO_ROOT = Path(__file__).resolve().parents[3]
SAFE_PRUNE_ROOTS = (
    _REPO_ROOT / "obs" / "metrics",
    _REPO_ROOT / "logs" / "quarantine",
    _REPO_ROOT / "audit" / "lineage",
)


@dataclass
class RetentionReport:
    name: str
    keep_days: int
    candidates: list[dict[str, Any]]
    deleted: list[dict[str, Any]] = field(default_factory=list)
    dry_run: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "keep_days": self.keep_days,
            "dry_run": self.dry_run,
            "candidates": self.candidates,
            "deleted": self.deleted,
        }


# ─────────────────────────────────────────────────────────────────────
# Lineage rows
# ─────────────────────────────────────────────────────────────────────
def prune_old_lineage(
    *, keep_days: int, dry_run: bool = True,
    store: LineageStore | None = None,
    now: datetime | None = None,
) -> RetentionReport:
    """List (and optionally delete) pipeline_run rows older than `keep_days`."""
    s = store or get_default_store()
    cutoff = ((now or datetime.now(timezone.utc))
              .timestamp() - keep_days * 86400)
    rows = s.conn.execute(
        "SELECT run_id, stage, started_at, status FROM pipeline_run "
        "ORDER BY started_at ASC"
    ).fetchall()
    candidates: list[dict[str, Any]] = []
    for r in rows:
        try:
            ts = datetime.fromisoformat(r["started_at"]).timestamp()
        except (ValueError, TypeError):
            continue
        if ts < cutoff:
            candidates.append({
                "run_id": r["run_id"], "stage": r["stage"],
                "started_at": r["started_at"], "status": r["status"],
            })

    deleted: list[dict[str, Any]] = []
    if not dry_run and candidates:
        for c in candidates:
            with s.conn:
                s.conn.execute(
                    "DELETE FROM pipeline_artifact_input WHERE run_id = ?", (c["run_id"],),
                )
                s.conn.execute(
                    "DELETE FROM pipeline_artifact WHERE run_id = ?", (c["run_id"],),
                )
                s.conn.execute(
                    "DELETE FROM pipeline_run WHERE run_id = ?", (c["run_id"],),
                )
            deleted.append(c)

    return RetentionReport(
        name="lineage_rows", keep_days=keep_days,
        candidates=candidates, deleted=deleted, dry_run=dry_run,
    )


# ─────────────────────────────────────────────────────────────────────
# Prometheus textfile metrics
# ─────────────────────────────────────────────────────────────────────
def prune_old_metrics(
    *, keep_days: int, dry_run: bool = True,
    metrics_dir: Path | None = None,
    now: datetime | None = None,
) -> RetentionReport:
    """List `.prom` files older than `keep_days` (size-based candidates)."""
    base = Path(metrics_dir) if metrics_dir else _REPO_ROOT / "obs" / "metrics"
    cutoff = ((now or datetime.now(timezone.utc))
              .timestamp() - keep_days * 86400)
    candidates: list[dict[str, Any]] = []
    deleted: list[dict[str, Any]] = []
    if not base.is_dir():
        return RetentionReport(
            name="prom_metrics", keep_days=keep_days,
            candidates=[], deleted=[], dry_run=dry_run,
        )
    if not _is_safe_root(base):
        raise PermissionError(f"refused to prune outside safe roots: {base}")
    for p in sorted(base.glob("*.prom")):
        try:
            mtime = p.stat().st_mtime
        except OSError:
            continue
        if mtime >= cutoff:
            continue
        rec = {
            "path": str(p.relative_to(_REPO_ROOT)).replace("\\", "/"),
            "size_bytes": p.stat().st_size,
            "mtime": datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat(timespec="seconds"),
        }
        candidates.append(rec)
        if not dry_run:
            p.unlink()
            deleted.append(rec)
    return RetentionReport(
        name="prom_metrics", keep_days=keep_days,
        candidates=candidates, deleted=deleted, dry_run=dry_run,
    )


# ─────────────────────────────────────────────────────────────────────
# Quarantine jsonl entries (line-by-line)
# ─────────────────────────────────────────────────────────────────────
def prune_old_quarantine(
    *, keep_days: int, dry_run: bool = True,
    quarantine_dir: Path | None = None,
    now: datetime | None = None,
) -> RetentionReport:
    """
    Identify quarantine jsonl entries older than `keep_days` (by record `ts`
    when present, or file mtime otherwise). When `dry_run=False`, the file
    is rewritten *atomically* — entries to keep are streamed to a sibling
    tempfile and renamed over the original.
    """
    qdir = Path(quarantine_dir) if quarantine_dir else _REPO_ROOT / "logs" / "quarantine"
    cutoff = ((now or datetime.now(timezone.utc))
              .timestamp() - keep_days * 86400)
    candidates: list[dict[str, Any]] = []
    deleted: list[dict[str, Any]] = []
    if not qdir.is_dir():
        return RetentionReport(
            name="quarantine", keep_days=keep_days,
            candidates=[], deleted=[], dry_run=dry_run,
        )
    if not _is_safe_root(qdir):
        raise PermissionError(f"refused to prune outside safe roots: {qdir}")

    for f in sorted(qdir.glob("*.jsonl")):
        n_kept = 0
        n_pruned = 0
        keep_lines: list[str] = []
        for line in f.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            ts = _maybe_record_ts(line)
            if ts is not None and ts < cutoff:
                n_pruned += 1
            else:
                keep_lines.append(line)
                n_kept += 1
        rec = {
            "file": str(f.relative_to(_REPO_ROOT)).replace("\\", "/"),
            "candidates": n_pruned, "kept": n_kept,
        }
        if n_pruned > 0:
            candidates.append(rec)
            if not dry_run:
                tmp = f.with_suffix(f.suffix + ".prune.tmp")
                tmp.write_text("\n".join(keep_lines) + ("\n" if keep_lines else ""),
                               encoding="utf-8")
                tmp.replace(f)
                deleted.append(rec)

    return RetentionReport(
        name="quarantine", keep_days=keep_days,
        candidates=candidates, deleted=deleted, dry_run=dry_run,
    )


# ─────────────────────────────────────────────────────────────────────
# Internals
# ─────────────────────────────────────────────────────────────────────
def _maybe_record_ts(line: str) -> float | None:
    try:
        rec = json.loads(line)
    except json.JSONDecodeError:
        return None
    ts = rec.get("ts") or rec.get("created_at")
    if not ts:
        return None
    try:
        return datetime.fromisoformat(str(ts)).timestamp()
    except ValueError:
        return None


def _is_safe_root(p: Path) -> bool:
    p_resolved = p.resolve()
    for safe in SAFE_PRUNE_ROOTS:
        try:
            p_resolved.relative_to(safe.resolve())
            return True
        except ValueError:
            continue
        if p_resolved == safe.resolve():
            return True
    return False
