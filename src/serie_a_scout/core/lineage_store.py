"""
Lineage persistence — SQLite implementation of `serie_a_meta.pipeline_run`
and `serie_a_meta.pipeline_artifact`.

WHY SQLITE (deviation from roadmap §4.2):
  The roadmap names a MySQL schema for lineage. We deliberately persist to a
  local SQLite file (`audit/lineage/lineage.db`) instead, because:
    * CI runs without DB credentials (F1.4 stays green).
    * Lineage must survive an outage of the production DB — it's a
      forensic trail, not transactional state.
    * SQLite is in the stdlib → no new dependency, Windows-clean.
  The schema is portable: only ANSI types + ISO-8601 strings for timestamps.
  Migrating to MySQL later is a one-shot dump.

CONCURRENCY:
  * The connection is opened with `isolation_level=None` + WAL mode, so
    concurrent readers do not block writers. We use a per-process
    `_LOCK` to serialise writes inside the same Python interpreter and a
    `BEGIN IMMEDIATE` for cross-process safety.
  * Every write happens inside a transaction; on failure the row is not
    inserted (we re-raise the original exception).

DETERMINISM:
  * `run_id` is a random uuid4 hex by default but the caller can override
    it (used in tests).
  * Timestamps are produced via `utc_now_iso()` which uses second-level
    precision — this keeps round-trips byte-stable in the JSON exports.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping


# ─────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────
SCHEMA_VERSION: str = "1.0.0"

_REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DB_PATH: Path = _REPO_ROOT / "audit" / "lineage" / "lineage.db"

# Pipeline stages used by the project. Free-form for forward compat (we
# accept any string), but documented so the export layer can render a
# canonical ordering when stages are unknown.
KNOWN_STAGES: tuple[str, ...] = (
    "ingest",
    "validate",
    "audit",
    "fix",
    "transform",
    "snapshot",
    "snapshot_restore",
    "regression",
    "serve",
)

# Artifact kinds (open enum)
KNOWN_KINDS: tuple[str, ...] = (
    "snapshot", "parquet", "json", "markdown", "prom",
    "html", "payload", "csv", "report",
)

# Status values
STATUS_RUNNING = "running"
STATUS_OK = "ok"
STATUS_FAIL = "fail"
STATUS_PARTIAL = "partial"
STATUS_VALUES = (STATUS_RUNNING, STATUS_OK, STATUS_FAIL, STATUS_PARTIAL)


_LOCK = threading.RLock()


# ─────────────────────────────────────────────────────────────────────
# Dataclasses
# ─────────────────────────────────────────────────────────────────────
@dataclass
class RunRecord:
    run_id: str
    parent_run_id: str | None
    stage: str
    status: str
    started_at: str
    ended_at: str | None = None
    duration_ms: int | None = None
    rows_in: int | None = None
    rows_out: int | None = None
    exit_code: int | None = None
    error_message: str | None = None
    host: str | None = None
    git_commit: str | None = None
    schema_version: str = SCHEMA_VERSION
    extra: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ArtifactRecord:
    artifact_id: int | None
    run_id: str
    kind: str
    path: str
    checksum: str | None
    bytes_size: int | None
    rows: int | None
    created_at: str
    meta: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────
def utc_now_iso() -> str:
    """ISO-8601 UTC, second precision. Deterministic format."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_run_id() -> str:
    """Random opaque run identifier (uuid4 hex)."""
    return uuid.uuid4().hex


def _json_or_none(d: Mapping[str, Any] | None) -> str | None:
    if not d:
        return None
    return json.dumps(d, ensure_ascii=False, sort_keys=True, default=str)


def _parse_extra(text: str | None) -> dict[str, Any]:
    if not text:
        return {}
    try:
        v = json.loads(text)
        return v if isinstance(v, dict) else {}
    except json.JSONDecodeError:
        return {}


# ─────────────────────────────────────────────────────────────────────
# Store
# ─────────────────────────────────────────────────────────────────────
class LineageStore:
    """
    SQLite-backed persistence of pipeline runs and artifacts.

    Construct once per process; the underlying connection is opened lazily
    and re-used. Safe for sequential use across threads (thread-locked).
    """

    def __init__(self, db_path: Path | str | None = None):
        self.db_path = Path(db_path) if db_path else DEFAULT_DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: sqlite3.Connection | None = None
        self._initialised = False

    # ──────────────────── lifecycle ────────────────────
    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(
                str(self.db_path),
                isolation_level=None,           # autocommit; we wrap explicit txns
                check_same_thread=False,
                timeout=10.0,
            )
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
        if not self._initialised:
            self._init_schema()
            self._initialised = True
        return self._conn

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None
            self._initialised = False

    def _init_schema(self) -> None:
        c = self._conn
        assert c is not None
        with _LOCK:
            c.executescript(_SCHEMA_SQL)

    @contextmanager
    def _txn(self) -> Iterator[sqlite3.Connection]:
        """Serialised IMMEDIATE transaction. Rolls back on exception."""
        c = self.conn
        with _LOCK:
            c.execute("BEGIN IMMEDIATE")
            try:
                yield c
                c.execute("COMMIT")
            except BaseException:
                try:
                    c.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
                raise

    # ──────────────────── runs ────────────────────
    def start_run(
        self,
        *,
        stage: str,
        parent_run_id: str | None = None,
        run_id: str | None = None,
        rows_in: int | None = None,
        host: str | None = None,
        git_commit: str | None = None,
        extra: Mapping[str, Any] | None = None,
        started_at: str | None = None,
    ) -> RunRecord:
        """Insert a new run with status='running'. Returns the record."""
        rid = run_id or new_run_id()
        rec = RunRecord(
            run_id=rid,
            parent_run_id=parent_run_id,
            stage=str(stage),
            status=STATUS_RUNNING,
            started_at=started_at or utc_now_iso(),
            rows_in=rows_in,
            host=host,
            git_commit=git_commit,
            extra=dict(extra or {}),
        )
        with self._txn() as c:
            c.execute(
                """
                INSERT INTO pipeline_run (
                    run_id, parent_run_id, stage, status,
                    started_at, ended_at, duration_ms,
                    rows_in, rows_out, exit_code, error_message,
                    host, git_commit, schema_version, extra_json
                ) VALUES (?, ?, ?, ?, ?, NULL, NULL, ?, NULL, NULL, NULL, ?, ?, ?, ?)
                """,
                (
                    rec.run_id, rec.parent_run_id, rec.stage, rec.status,
                    rec.started_at, rec.rows_in, rec.host, rec.git_commit,
                    rec.schema_version, _json_or_none(rec.extra),
                ),
            )
        return rec

    def finish_run(
        self,
        run_id: str,
        *,
        status: str,
        exit_code: int | None = None,
        rows_out: int | None = None,
        error_message: str | None = None,
        ended_at: str | None = None,
        extra: Mapping[str, Any] | None = None,
    ) -> RunRecord:
        """Close an existing run; computes `duration_ms` from `started_at`."""
        if status not in STATUS_VALUES:
            raise ValueError(f"status non valido: {status!r}")
        end = ended_at or utc_now_iso()
        with self._txn() as c:
            row = c.execute(
                "SELECT * FROM pipeline_run WHERE run_id = ?", (run_id,)
            ).fetchone()
            if not row:
                raise KeyError(f"run sconosciuto: {run_id}")
            if row["status"] != STATUS_RUNNING:
                # idempotent re-close: just return whatever is there
                return self._row_to_run(row)

            duration_ms = _duration_ms(row["started_at"], end)
            merged_extra = _parse_extra(row["extra_json"])
            if extra:
                merged_extra.update(extra)

            c.execute(
                """
                UPDATE pipeline_run SET
                    status        = ?,
                    ended_at      = ?,
                    duration_ms   = ?,
                    rows_out      = COALESCE(?, rows_out),
                    exit_code     = ?,
                    error_message = ?,
                    extra_json    = ?
                 WHERE run_id = ?
                """,
                (
                    status, end, duration_ms, rows_out, exit_code,
                    error_message, _json_or_none(merged_extra), run_id,
                ),
            )
            row = c.execute(
                "SELECT * FROM pipeline_run WHERE run_id = ?", (run_id,)
            ).fetchone()
        return self._row_to_run(row)

    def get_run(self, run_id: str) -> RunRecord | None:
        c = self.conn
        row = c.execute(
            "SELECT * FROM pipeline_run WHERE run_id = ?", (run_id,)
        ).fetchone()
        return self._row_to_run(row) if row else None

    def iter_runs(
        self, *, stage: str | None = None, status: str | None = None,
        limit: int | None = None,
    ) -> list[RunRecord]:
        sql = "SELECT * FROM pipeline_run WHERE 1=1"
        params: list[Any] = []
        if stage is not None:
            sql += " AND stage = ?"
            params.append(stage)
        if status is not None:
            sql += " AND status = ?"
            params.append(status)
        sql += " ORDER BY started_at DESC, run_id DESC"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(int(limit))
        rows = self.conn.execute(sql, params).fetchall()
        return [self._row_to_run(r) for r in rows]

    def children_of(self, run_id: str) -> list[RunRecord]:
        rows = self.conn.execute(
            "SELECT * FROM pipeline_run WHERE parent_run_id = ? "
            "ORDER BY started_at ASC, run_id ASC",
            (run_id,),
        ).fetchall()
        return [self._row_to_run(r) for r in rows]

    # ──────────────────── artifacts ────────────────────
    def register_artifact(
        self,
        *,
        run_id: str,
        kind: str,
        path: str,
        checksum: str | None = None,
        bytes_size: int | None = None,
        rows: int | None = None,
        meta: Mapping[str, Any] | None = None,
        created_at: str | None = None,
    ) -> ArtifactRecord:
        """Persist an artifact produced by `run_id`. Returns the record."""
        if not run_id:
            raise ValueError("run_id obbligatorio")
        if not kind:
            raise ValueError("kind obbligatorio")
        if not path:
            raise ValueError("path obbligatorio")
        with self._txn() as c:
            exists = c.execute(
                "SELECT 1 FROM pipeline_run WHERE run_id = ?", (run_id,)
            ).fetchone()
            if not exists:
                raise KeyError(f"run sconosciuto: {run_id}")
            ts = created_at or utc_now_iso()
            c.execute(
                """
                INSERT INTO pipeline_artifact (
                    run_id, kind, path, checksum, bytes_size, rows,
                    created_at, meta_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id, str(kind), str(path), checksum,
                    int(bytes_size) if bytes_size is not None else None,
                    int(rows) if rows is not None else None,
                    ts, _json_or_none(meta),
                ),
            )
            artifact_id = c.execute("SELECT last_insert_rowid()").fetchone()[0]
        return ArtifactRecord(
            artifact_id=int(artifact_id),
            run_id=run_id, kind=str(kind), path=str(path),
            checksum=checksum, bytes_size=bytes_size, rows=rows,
            created_at=ts, meta=dict(meta or {}),
        )

    def register_input(
        self, *, run_id: str, artifact_id: int, registered_at: str | None = None,
    ) -> None:
        """Record that `run_id` consumed `artifact_id` (downstream provenance)."""
        ts = registered_at or utc_now_iso()
        with self._txn() as c:
            c.execute(
                "INSERT OR IGNORE INTO pipeline_artifact_input "
                "(run_id, artifact_id, registered_at) VALUES (?, ?, ?)",
                (run_id, int(artifact_id), ts),
            )

    def artifacts_for(self, run_id: str) -> list[ArtifactRecord]:
        rows = self.conn.execute(
            "SELECT * FROM pipeline_artifact WHERE run_id = ? "
            "ORDER BY artifact_id ASC",
            (run_id,),
        ).fetchall()
        return [self._row_to_artifact(r) for r in rows]

    def find_artifacts(
        self, *, kind: str | None = None, path_like: str | None = None,
    ) -> list[ArtifactRecord]:
        sql = "SELECT * FROM pipeline_artifact WHERE 1=1"
        params: list[Any] = []
        if kind is not None:
            sql += " AND kind = ?"
            params.append(kind)
        if path_like is not None:
            sql += " AND path LIKE ?"
            params.append(path_like)
        sql += " ORDER BY artifact_id DESC"
        rows = self.conn.execute(sql, params).fetchall()
        return [self._row_to_artifact(r) for r in rows]

    def consumers_of(self, artifact_id: int) -> list[RunRecord]:
        rows = self.conn.execute(
            """
            SELECT r.* FROM pipeline_run r
              JOIN pipeline_artifact_input i ON i.run_id = r.run_id
             WHERE i.artifact_id = ?
             ORDER BY r.started_at ASC, r.run_id ASC
            """,
            (int(artifact_id),),
        ).fetchall()
        return [self._row_to_run(r) for r in rows]

    def inputs_for(self, run_id: str) -> list[ArtifactRecord]:
        rows = self.conn.execute(
            """
            SELECT a.* FROM pipeline_artifact a
              JOIN pipeline_artifact_input i ON i.artifact_id = a.artifact_id
             WHERE i.run_id = ?
             ORDER BY a.artifact_id ASC
            """,
            (run_id,),
        ).fetchall()
        return [self._row_to_artifact(r) for r in rows]

    # ──────────────────── diagnostics ────────────────────
    def detect_orphans(self) -> dict[str, list[str | int]]:
        """
        Return diagnostics:
          * `runs_still_running`  → run rows stuck in `running` state
          * `artifacts_missing_file` → artifacts whose `path` is no longer on disk
          * `runs_without_artifacts` → completed runs that produced nothing
        """
        runs_running = [
            r["run_id"] for r in self.conn.execute(
                "SELECT run_id FROM pipeline_run WHERE status = ?",
                (STATUS_RUNNING,),
            ).fetchall()
        ]
        missing_files: list[str | int] = []
        for r in self.conn.execute(
            "SELECT artifact_id, path FROM pipeline_artifact"
        ).fetchall():
            p = Path(r["path"])
            if not p.is_absolute():
                p = _REPO_ROOT / p
            if not p.exists():
                missing_files.append(int(r["artifact_id"]))
        runs_no_artifacts = [
            r["run_id"] for r in self.conn.execute(
                """
                SELECT r.run_id FROM pipeline_run r
                 LEFT JOIN pipeline_artifact a ON a.run_id = r.run_id
                 WHERE r.status IN ('ok','partial') AND a.artifact_id IS NULL
                """
            ).fetchall()
        ]
        return {
            "runs_still_running": runs_running,
            "artifacts_missing_file": missing_files,
            "runs_without_artifacts": runs_no_artifacts,
        }

    def aggregate_counters(self) -> dict[str, int]:
        """Return cumulative counters for metric emission."""
        c = self.conn
        total = c.execute("SELECT COUNT(*) FROM pipeline_run").fetchone()[0]
        failed = c.execute(
            "SELECT COUNT(*) FROM pipeline_run WHERE status = ?", (STATUS_FAIL,),
        ).fetchone()[0]
        artifacts = c.execute("SELECT COUNT(*) FROM pipeline_artifact").fetchone()[0]
        rows_total = c.execute(
            "SELECT COALESCE(SUM(rows_out), 0) FROM pipeline_run"
        ).fetchone()[0] or 0
        return {
            "runs_total": int(total),
            "failures_total": int(failed),
            "artifacts_total": int(artifacts),
            "rows_total": int(rows_total),
        }

    # ──────────────────── row → dataclass ────────────────────
    @staticmethod
    def _row_to_run(row: sqlite3.Row | None) -> RunRecord:
        assert row is not None
        return RunRecord(
            run_id=row["run_id"],
            parent_run_id=row["parent_run_id"],
            stage=row["stage"],
            status=row["status"],
            started_at=row["started_at"],
            ended_at=row["ended_at"],
            duration_ms=row["duration_ms"],
            rows_in=row["rows_in"],
            rows_out=row["rows_out"],
            exit_code=row["exit_code"],
            error_message=row["error_message"],
            host=row["host"],
            git_commit=row["git_commit"],
            schema_version=row["schema_version"],
            extra=_parse_extra(row["extra_json"]),
        )

    @staticmethod
    def _row_to_artifact(row: sqlite3.Row) -> ArtifactRecord:
        return ArtifactRecord(
            artifact_id=int(row["artifact_id"]),
            run_id=row["run_id"],
            kind=row["kind"],
            path=row["path"],
            checksum=row["checksum"],
            bytes_size=row["bytes_size"],
            rows=row["rows"],
            created_at=row["created_at"],
            meta=_parse_extra(row["meta_json"]),
        )


def _duration_ms(started_at: str, ended_at: str) -> int:
    try:
        a = datetime.fromisoformat(started_at)
        b = datetime.fromisoformat(ended_at)
        delta = b - a
        return int(delta.total_seconds() * 1000)
    except ValueError:
        return 0


# ─────────────────────────────────────────────────────────────────────
# Schema DDL
# ─────────────────────────────────────────────────────────────────────
_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS pipeline_run (
    run_id          TEXT PRIMARY KEY,
    parent_run_id   TEXT,
    stage           TEXT NOT NULL,
    status          TEXT NOT NULL,
    started_at      TEXT NOT NULL,
    ended_at        TEXT,
    duration_ms     INTEGER,
    rows_in         INTEGER,
    rows_out        INTEGER,
    exit_code       INTEGER,
    error_message   TEXT,
    host            TEXT,
    git_commit      TEXT,
    schema_version  TEXT NOT NULL,
    extra_json      TEXT,
    FOREIGN KEY (parent_run_id) REFERENCES pipeline_run(run_id)
);

CREATE INDEX IF NOT EXISTS idx_pipeline_run_stage   ON pipeline_run(stage);
CREATE INDEX IF NOT EXISTS idx_pipeline_run_parent  ON pipeline_run(parent_run_id);
CREATE INDEX IF NOT EXISTS idx_pipeline_run_started ON pipeline_run(started_at);
CREATE INDEX IF NOT EXISTS idx_pipeline_run_status  ON pipeline_run(status);

CREATE TABLE IF NOT EXISTS pipeline_artifact (
    artifact_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          TEXT NOT NULL,
    kind            TEXT NOT NULL,
    path            TEXT NOT NULL,
    checksum        TEXT,
    bytes_size      INTEGER,
    rows            INTEGER,
    created_at      TEXT NOT NULL,
    meta_json       TEXT,
    FOREIGN KEY (run_id) REFERENCES pipeline_run(run_id)
);

CREATE INDEX IF NOT EXISTS idx_pipeline_artifact_run  ON pipeline_artifact(run_id);
CREATE INDEX IF NOT EXISTS idx_pipeline_artifact_kind ON pipeline_artifact(kind);
CREATE INDEX IF NOT EXISTS idx_pipeline_artifact_path ON pipeline_artifact(path);

CREATE TABLE IF NOT EXISTS pipeline_artifact_input (
    run_id          TEXT NOT NULL,
    artifact_id     INTEGER NOT NULL,
    registered_at   TEXT NOT NULL,
    PRIMARY KEY (run_id, artifact_id),
    FOREIGN KEY (run_id) REFERENCES pipeline_run(run_id),
    FOREIGN KEY (artifact_id) REFERENCES pipeline_artifact(artifact_id)
);

CREATE INDEX IF NOT EXISTS idx_pipeline_artifact_input_artifact
    ON pipeline_artifact_input(artifact_id);

CREATE TABLE IF NOT EXISTS pipeline_schema_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
INSERT OR IGNORE INTO pipeline_schema_meta(key, value)
     VALUES ('schema_version', '%s');
""" % SCHEMA_VERSION


# ─────────────────────────────────────────────────────────────────────
# Module-level singleton convenience
# ─────────────────────────────────────────────────────────────────────
_DEFAULT_STORE: LineageStore | None = None


def get_default_store(db_path: Path | str | None = None) -> LineageStore:
    """Process-wide default store. The first call sets the path."""
    global _DEFAULT_STORE
    if _DEFAULT_STORE is None or db_path is not None:
        _DEFAULT_STORE = LineageStore(db_path)
    return _DEFAULT_STORE


def reset_default_store() -> None:
    """Reset the process-wide store (used by tests)."""
    global _DEFAULT_STORE
    if _DEFAULT_STORE is not None:
        _DEFAULT_STORE.close()
    _DEFAULT_STORE = None
