"""
WORM (write-once-read-many) snapshot writer.

A snapshot is a directory tree under `<base_dir>/<season>/giornata_<NN>/`:

    manifest.json
    restore.sh
    giocatori.parquet
    giocatore_partita.parquet
    t_infortuni.parquet
    calendario.parquet
    squadre.parquet
    payload.json       (optional, copied verbatim from dashboard_output/)

Guarantees:

* **Atomic**: writes happen in a sibling `.tmp.<run_id>` directory and are
  renamed onto the target only after manifest + checksums are computed.
* **Idempotent on success**: a second invocation for the same
  `(season, giornata)` fails with `SnapshotExistsError` unless `--force`.
* **Deterministic**: rows ordered by `id`, columns sorted alphabetically.
  Same DB state → same parquet bytes → same SHA-256.
* **Integrity-verifiable**: every file has a SHA-256 in `manifest.json`.

The writer is intentionally narrow: it owns persistence, not orchestration.
Callers (CLI in `snapshots/take.py`, pipeline hooks) are responsible for
deciding *when* to take a snapshot.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Mapping

import pandas as pd
from sqlalchemy import create_engine

from .checksum import sha256_file
from .manifest import (
    FileEntry, Manifest, SCHEMA_VERSION,
    detect_git_commit, new_run_id, now_utc_iso,
)


# Tabelle catturate in ogni snapshot, con ORDER BY deterministico.
# L'ordine canonico è per `id` ASC. Le colonne vengono ordinate
# alfabeticamente prima della serializzazione parquet.
SNAPSHOT_TABLES: dict[str, str] = {
    "giocatori":          "SELECT * FROM giocatori          ORDER BY id ASC",
    "giocatore_partita":  "SELECT * FROM giocatore_partita  ORDER BY id ASC",
    "t_infortuni":        "SELECT * FROM t_infortuni        ORDER BY id ASC",
    "calendario":         "SELECT * FROM calendario         ORDER BY id ASC",
    "squadre":            "SELECT * FROM squadre            ORDER BY id ASC",
}

# Preferiamo zstd (compressione migliore); fallback a snappy se la build
# di pyarrow non lo supporta.
PREFERRED_COMPRESSION: tuple[str, ...] = ("zstd", "snappy", "gzip")


class SnapshotExistsError(RuntimeError):
    """Snapshot directory already exists and `force=False`."""


class SnapshotWriter:
    """
    Build a WORM snapshot of the analytics DB.

    Parameters
    ----------
    season : str
        Season tag, e.g. "2025-26". Used as first-level directory.
    giornata : int
        Matchday number (1..38). Zero-padded to two digits in the dirname.
    db_url : str
        SQLAlchemy URL (e.g. ``mysql+pymysql://USER:PASS@host/db``).  # pragma: allowlist secret
    base_dir : Path
        Root for all snapshots. The actual snapshot dir is
        ``base_dir / season / "giornata_NN"``.
    reliability_score : int | None
        Audit reliability score at snapshot time; recorded in manifest.
    payload_json_path : Path | None
        If provided and existing, copied verbatim into the snapshot.
    force : bool
        If True, overwrite an existing snapshot (the previous dir is moved
        aside as ``<dir>.backup.<short_run_id>``, not deleted).
    tables : Mapping[str, str]
        Override the default table → query map.
    """

    def __init__(
        self,
        *,
        season: str,
        giornata: int,
        db_url: str,
        base_dir: Path,
        reliability_score: int | None = None,
        payload_json_path: Path | None = None,
        force: bool = False,
        tables: Mapping[str, str] = SNAPSHOT_TABLES,
    ):
        if not season or "/" in season or ".." in season:
            raise ValueError(f"season invalida: {season!r}")
        if not (1 <= int(giornata) <= 60):
            raise ValueError(f"giornata fuori range plausibile: {giornata}")

        self.season = season
        self.giornata = int(giornata)
        self.db_url = db_url
        self.base_dir = Path(base_dir)
        self.reliability_score = reliability_score
        self.payload_json_path = Path(payload_json_path) if payload_json_path else None
        self.force = force
        self.tables = dict(tables)

        self.run_id = new_run_id()
        self.target_dir = self.base_dir / season / f"giornata_{self.giornata:02d}"
        self.tmp_dir = self.target_dir.parent / f".{self.target_dir.name}.tmp.{self.run_id[:8]}"

        self._engine = None

    # ──────────────────────────────────────────────────────────────
    # Lifecycle
    # ──────────────────────────────────────────────────────────────
    @property
    def engine(self):
        if self._engine is None:
            self._engine = create_engine(self.db_url, pool_pre_ping=True)
        return self._engine

    def write(self) -> Manifest:
        """Execute the snapshot. Returns the produced manifest."""
        # 1) idempotency
        if self.target_dir.exists() and not self.force:
            raise SnapshotExistsError(
                f"Snapshot già esistente: {self.target_dir}. "
                f"Usa force=True (o --force su CLI) per sovrascrivere."
            )

        # 2) atomic staging directory
        self.tmp_dir.mkdir(parents=True, exist_ok=False)

        files: list[FileEntry] = []
        try:
            files.extend(self._dump_tables())
            files.extend(self._copy_payload_if_any())

            manifest = Manifest(
                schema_version=SCHEMA_VERSION,
                run_id=self.run_id,
                season=self.season,
                giornata=self.giornata,
                created_at_utc=now_utc_iso(),
                reliability_score=self.reliability_score,
                git_commit=detect_git_commit(),
                source_tables=list(self.tables.keys()),
                files=files,
            )
            (self.tmp_dir / "manifest.json").write_text(
                manifest.to_json(), encoding="utf-8",
            )
            (self.tmp_dir / "restore.sh").write_text(
                self._generate_restore_script(), encoding="utf-8",
            )

            self._atomic_promote()
            return manifest
        except Exception:
            shutil.rmtree(self.tmp_dir, ignore_errors=True)
            raise

    # ──────────────────────────────────────────────────────────────
    # Internals
    # ──────────────────────────────────────────────────────────────
    def _dump_tables(self) -> list[FileEntry]:
        out: list[FileEntry] = []
        for table, query in self.tables.items():
            df = pd.read_sql(query, self.engine)
            # Column order deterministic (alphabetical) so byte-equality holds.
            df = df.reindex(sorted(df.columns), axis=1)

            pq = self.tmp_dir / f"{table}.parquet"
            self._write_parquet(df, pq)

            out.append(FileEntry(
                path=pq.name,
                sha256=sha256_file(pq),
                bytes_size=pq.stat().st_size,
                rows=len(df),
                table=table,
            ))
        return out

    def _write_parquet(self, df: pd.DataFrame, path: Path) -> None:
        last_err: Exception | None = None
        for codec in PREFERRED_COMPRESSION:
            try:
                df.to_parquet(path, compression=codec, index=False)
                return
            except Exception as e:  # noqa: BLE001
                last_err = e
        # Last resort: no compression
        try:
            df.to_parquet(path, compression=None, index=False)
        except Exception as e:
            raise RuntimeError(
                f"Impossibile scrivere parquet {path.name}: ultimi errori {last_err} | {e}"
            ) from e

    def _copy_payload_if_any(self) -> list[FileEntry]:
        if not self.payload_json_path or not self.payload_json_path.exists():
            return []
        dst = self.tmp_dir / "payload.json"
        shutil.copyfile(self.payload_json_path, dst)
        return [FileEntry(
            path=dst.name,
            sha256=sha256_file(dst),
            bytes_size=dst.stat().st_size,
            rows=None, table=None,
        )]

    def _atomic_promote(self) -> None:
        """Move tmp_dir → target_dir. On force, archive the old target."""
        if self.target_dir.exists():
            # force=True: archivia, non cancella
            archive = self.target_dir.with_name(
                f"{self.target_dir.name}.backup.{self.run_id[:8]}"
            )
            self.target_dir.rename(archive)
        self.target_dir.parent.mkdir(parents=True, exist_ok=True)
        os.replace(self.tmp_dir, self.target_dir)

    def _generate_restore_script(self) -> str:
        """A small bash wrapper that delegates to snapshots/restore.py."""
        return (
            "#!/usr/bin/env bash\n"
            f"# Auto-generated. Snapshot: {self.season}/giornata_{self.giornata:02d}\n"
            f"# run_id: {self.run_id}\n"
            "# Use this script to restore the snapshot it lives in.\n"
            "set -euo pipefail\n"
            'HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"\n'
            'PROJECT="$(cd "$HERE/../../.." && pwd)"\n'
            'python "$PROJECT/snapshots/restore.py" --snapshot "$HERE" "$@"\n'
        )
