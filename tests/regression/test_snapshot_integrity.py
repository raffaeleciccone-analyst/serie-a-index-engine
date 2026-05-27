"""
Regression tests for the WORM snapshot subsystem.

These tests hit the real production database (read-only writes go into a
temp snapshot dir under `tmp_path`). They require the audit/lib/db.py
credentials to be valid.

What we cover here:

  * a fresh snapshot writes parquet + manifest + restore.sh
  * every file's actual SHA-256 matches manifest
  * manifest JSON round-trips (write → read → assert equal)
  * trying to write again on the same target FAILS unless --force
  * `--force` archives the old target (does not delete it)
  * `verify_snapshot()` returns ok=True on a fresh snapshot
  * `verify_snapshot()` detects a tampered parquet (bit flip)
  * `restore_snapshot(dry_run=True)` reads parquet without writing the DB
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "audit"))

from lib.db import DB_HOST, DB_USER, DB_PASSWORD, DB_NAME  # noqa: E402
from serie_a_scout.snapshots import (  # noqa: E402
    Manifest, SnapshotExistsError, SnapshotWriter,
    sha256_file, verify_snapshot, restore_snapshot,
)


def _db_url() -> str:
    return f"mysql+pymysql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}/{DB_NAME}"


@pytest.fixture
def writer_factory(tmp_path):
    """Factory che produce un SnapshotWriter su tmp_path."""
    def _factory(season="test-season", giornata=1, **kw):
        return SnapshotWriter(
            season=season, giornata=giornata,
            db_url=_db_url(), base_dir=tmp_path, **kw,
        )
    return _factory


# ────────────────── write happy path ──────────────────
def test_snapshot_creates_expected_files(writer_factory, tmp_path):
    w = writer_factory(giornata=1)
    manifest = w.write()
    snap = w.target_dir
    assert snap.is_dir()
    assert (snap / "manifest.json").is_file()
    assert (snap / "restore.sh").is_file()
    # ogni file dichiarato esiste
    for f in manifest.files:
        assert (snap / f.path).is_file(), f"missing {f.path}"
    # tabelle minime previste
    table_names = {f.table for f in manifest.files if f.table}
    assert {"giocatori", "giocatore_partita", "t_infortuni", "calendario",
            "squadre"}.issubset(table_names)


def test_checksums_match_manifest(writer_factory):
    w = writer_factory(giornata=2)
    manifest = w.write()
    for entry in manifest.files:
        actual = sha256_file(w.target_dir / entry.path)
        assert actual == entry.sha256, f"sha mismatch on {entry.path}"
        assert (w.target_dir / entry.path).stat().st_size == entry.bytes_size


def test_manifest_json_roundtrip(writer_factory):
    w = writer_factory(giornata=3)
    m = w.write()
    text = (w.target_dir / "manifest.json").read_text(encoding="utf-8")
    m2 = Manifest.from_json(text)
    assert m2.run_id == m.run_id
    assert m2.giornata == m.giornata
    assert m2.season == m.season
    assert m2.schema_version == m.schema_version
    assert {f.path for f in m2.files} == {f.path for f in m.files}


# ────────────────── idempotency / force ──────────────────
def test_second_write_fails_without_force(writer_factory):
    writer_factory(giornata=4).write()
    with pytest.raises(SnapshotExistsError):
        writer_factory(giornata=4).write()


def test_force_archives_previous(writer_factory):
    w1 = writer_factory(giornata=5)
    w1.write()
    w2 = writer_factory(giornata=5, force=True)
    w2.write()
    # nuovo è in place
    assert w2.target_dir.is_dir()
    # vecchio archiviato (non cancellato)
    backups = list(w2.target_dir.parent.glob("giornata_05.backup.*"))
    assert len(backups) == 1, f"expected 1 backup dir, got {backups}"


# ────────────────── verify ──────────────────
def test_verify_passes_on_fresh_snapshot(writer_factory):
    w = writer_factory(giornata=6)
    w.write()
    vr = verify_snapshot(w.target_dir)
    assert vr.ok, vr.errors
    assert vr.manifest is not None


def test_verify_detects_tampered_file(writer_factory):
    w = writer_factory(giornata=7)
    manifest = w.write()
    # corrompi UN byte di un parquet
    target = w.target_dir / manifest.files[0].path
    data = bytearray(target.read_bytes())
    data[0] ^= 0xFF
    target.write_bytes(bytes(data))
    vr = verify_snapshot(w.target_dir)
    assert not vr.ok
    assert any("sha256 mismatch" in e or "size mismatch" in e for e in vr.errors)


def test_verify_detects_missing_file(writer_factory):
    w = writer_factory(giornata=8)
    manifest = w.write()
    (w.target_dir / manifest.files[0].path).unlink()
    vr = verify_snapshot(w.target_dir)
    assert not vr.ok
    assert any("file mancante" in e for e in vr.errors)


# ────────────────── dry-run restore ──────────────────
def test_dry_run_restore_does_not_write_db(writer_factory):
    w = writer_factory(giornata=9)
    w.write()
    reports = restore_snapshot(w.target_dir, db_url=_db_url(), dry_run=True)
    assert all(r.ok for r in reports)
    assert all(r.rows_restored == 0 for r in reports)
    assert all("DRY-RUN" in r.message for r in reports)


# ────────────────── deterministic content ──────────────────
def test_snapshot_is_deterministic_for_same_db(writer_factory):
    """Due snapshot consecutivi sullo stesso DB devono avere identici sha256
    per ciascun file (ordering deterministico)."""
    w1 = writer_factory(giornata=10)
    m1 = w1.write()
    w2 = writer_factory(giornata=10, force=True)
    m2 = w2.write()
    by_path1 = {f.path: f.sha256 for f in m1.files if f.table}
    by_path2 = {f.path: f.sha256 for f in m2.files if f.table}
    assert by_path1 == by_path2, "snapshot non deterministico"
