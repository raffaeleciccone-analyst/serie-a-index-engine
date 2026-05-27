"""
Snapshot restore engine.

Two operating modes:

* **verify** (always run first): reads `manifest.json` and re-hashes each
  declared file. Any mismatch → abort.
* **apply**: with `dry_run=True` just prints what *would* be written; with
  `dry_run=False` performs the destructive restore inside a single
  transaction.

Restore strategy (per table, default):
  1. TRUNCATE the destination
  2. Bulk INSERT from parquet (chunked)
  3. Verify row count vs manifest

Because we *do not* know in general how downstream consumers depend on PK
values, we restore IDs verbatim. The caller may pass `tables=[...]` to
restore only a subset.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd
from sqlalchemy import create_engine, text

from .checksum import sha256_file
from .manifest import Manifest


@dataclass
class VerifyResult:
    ok: bool
    errors: list[str]
    manifest: Manifest | None


def verify_snapshot(snapshot_dir: Path) -> VerifyResult:
    """Re-compute checksums and validate against manifest. Pure read."""
    snapshot_dir = Path(snapshot_dir)
    manifest_path = snapshot_dir / "manifest.json"
    if not manifest_path.is_file():
        return VerifyResult(False, [f"manifest.json mancante in {snapshot_dir}"], None)

    try:
        manifest = Manifest.from_json(manifest_path.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        return VerifyResult(False, [f"manifest.json corrotto: {e}"], None)

    errors: list[str] = []
    seen_files: set[str] = set()
    for entry in manifest.files:
        p = snapshot_dir / entry.path
        seen_files.add(entry.path)
        if not p.is_file():
            errors.append(f"file mancante: {entry.path}")
            continue
        actual_size = p.stat().st_size
        if actual_size != entry.bytes_size:
            errors.append(
                f"{entry.path}: size mismatch (atteso {entry.bytes_size}, "
                f"trovato {actual_size})"
            )
        actual = sha256_file(p)
        if actual != entry.sha256:
            errors.append(
                f"{entry.path}: sha256 mismatch (atteso {entry.sha256[:12]}…, "
                f"trovato {actual[:12]}…)"
            )

    # File-extra non vietati ma segnalati come info
    for p in snapshot_dir.iterdir():
        if p.name in ("manifest.json", "restore.sh"):
            continue
        if p.name not in seen_files:
            errors.append(f"file extra (non in manifest): {p.name}")

    return VerifyResult(len(errors) == 0, errors, manifest)


@dataclass
class RestoreReport:
    table: str
    rows_in_snapshot: int
    rows_restored: int
    ok: bool
    message: str = ""


def restore_snapshot(
    snapshot_dir: Path,
    *,
    db_url: str,
    tables: list[str] | None = None,
    dry_run: bool = True,
    truncate: bool = True,
) -> list[RestoreReport]:
    """
    Restore a snapshot. Returns one RestoreReport per table.

    Caller MUST verify integrity first via `verify_snapshot()`; this
    function re-verifies as a safety net and aborts on any error.
    """
    snapshot_dir = Path(snapshot_dir)

    vr = verify_snapshot(snapshot_dir)
    if not vr.ok:
        raise RuntimeError(
            "Integrità snapshot fallita. Errori:\n  - "
            + "\n  - ".join(vr.errors)
        )
    assert vr.manifest is not None
    manifest = vr.manifest

    # Tabelle target
    available = {e.table: e for e in manifest.files if e.table}
    requested = tables or list(available.keys())
    missing = [t for t in requested if t not in available]
    if missing:
        raise ValueError(f"Tabelle richieste non presenti nello snapshot: {missing}")

    reports: list[RestoreReport] = []
    engine = create_engine(db_url, pool_pre_ping=True)

    # Use ONE explicit transaction so a failure aborts the whole restore
    with engine.begin() as conn:
        for table in requested:
            entry = available[table]
            pq = snapshot_dir / entry.path
            df = pd.read_parquet(pq)

            if dry_run:
                reports.append(RestoreReport(
                    table=table, rows_in_snapshot=entry.rows or 0,
                    rows_restored=0, ok=True,
                    message=f"DRY-RUN: avrebbe scritto {len(df)} righe",
                ))
                continue

            # Disable FK checks for the whole restore (we restore all parents
            # and children together). MySQL session-scoped.
            conn.execute(text("SET FOREIGN_KEY_CHECKS=0"))
            try:
                if truncate:
                    conn.execute(text(f"TRUNCATE TABLE `{table}`"))
                # to_sql with method=multi is much faster than row-by-row
                df.to_sql(
                    table, con=conn, if_exists="append", index=False,
                    chunksize=1000, method="multi",
                )
                # Verify
                got = conn.execute(text(f"SELECT COUNT(*) FROM `{table}`")).scalar() or 0
                ok = (got == len(df))
                reports.append(RestoreReport(
                    table=table, rows_in_snapshot=entry.rows or 0,
                    rows_restored=int(got), ok=ok,
                    message="OK" if ok else f"row count mismatch (atteso {len(df)}, trovato {got})",
                ))
            finally:
                conn.execute(text("SET FOREIGN_KEY_CHECKS=1"))

    return reports
