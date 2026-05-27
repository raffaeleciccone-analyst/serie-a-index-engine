"""
RunContext — context manager for instrumenting pipeline runs.

Usage:

    with RunContext(stage="snapshot") as run:
        run.register_artifact(path=..., kind="snapshot", ...)
        run.set_rows_out(123)
        ...

Behaviour:

* On entry: opens a `pipeline_run` row in status='running' (with parent
  if nested via another active RunContext).
* On exit (success): writes status='ok' and exit_code=0.
* On exit (exception): writes status='fail', stores the exception type
  and message, and re-raises (no swallow).
* `set_status('partial')` short-circuits the success path with the
  partial status.

Nested runs:
  A second `RunContext()` opened inside an active one inherits
  `parent_run_id` automatically. This builds the provenance chain.

Determinism:
  The class accepts an optional `clock` callable, used only by tests,
  to inject deterministic ISO timestamps.

Thread-safety:
  The active-run stack is per-thread (`threading.local`). Cross-thread
  nesting is intentionally NOT supported — open a fresh root context in
  each worker.
"""
from __future__ import annotations

import hashlib
import os
import platform
import socket
import subprocess
import threading
import time
import traceback
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Any, Callable, Mapping

from .lineage_store import (
    STATUS_FAIL, STATUS_OK, STATUS_PARTIAL, STATUS_RUNNING,
    ArtifactRecord, LineageStore, RunRecord, get_default_store, utc_now_iso,
)


# ─────────────────────────────────────────────────────────────────────
# Active-run stack (per thread)
# ─────────────────────────────────────────────────────────────────────
class _ActiveStack(threading.local):
    def __init__(self) -> None:
        super().__init__()
        self.stack: list["RunContext"] = []


_ACTIVE = _ActiveStack()


def current_run() -> "RunContext | None":
    """Return the innermost active RunContext, or None."""
    return _ACTIVE.stack[-1] if _ACTIVE.stack else None


# ─────────────────────────────────────────────────────────────────────
# RunContext
# ─────────────────────────────────────────────────────────────────────
@dataclass
class _ExtraEvent:
    """Free-form event appended to the run's `extra.events` list."""
    ts: str
    label: str
    payload: dict[str, Any]


class RunContext:
    """
    Context manager that owns the lifecycle of one `pipeline_run` row.

    Parameters
    ----------
    stage
        Pipeline stage (e.g. 'snapshot', 'regression', 'audit'). Free-form.
    store
        Override the underlying LineageStore (used by tests).
    parent_run_id
        Force a specific parent. By default we inherit from the active stack.
    rows_in
        Optional input row count, recorded at start.
    extra
        Arbitrary JSON-serialisable mapping persisted in `extra_json`.
    clock
        Optional callable that returns an ISO-8601 UTC string; used by tests
        to make timestamps deterministic.
    git_commit
        Optional override; auto-detected from `git rev-parse HEAD` if absent.
    """

    def __init__(
        self,
        *,
        stage: str,
        store: LineageStore | None = None,
        parent_run_id: str | None = None,
        rows_in: int | None = None,
        extra: Mapping[str, Any] | None = None,
        clock: Callable[[], str] | None = None,
        git_commit: str | None = None,
        run_id: str | None = None,
        host: str | None = None,
    ) -> None:
        self.stage = str(stage)
        self.store = store or get_default_store()
        self._clock = clock or utc_now_iso
        self._initial_extra: dict[str, Any] = dict(extra or {})
        self._initial_extra.setdefault("events", [])
        self._rows_in = rows_in
        self._rows_out: int | None = None
        self._parent_id_explicit = parent_run_id
        self._desired_run_id = run_id
        self._desired_host = host or socket.gethostname() or platform.node()
        self._desired_git = git_commit if git_commit is not None else _detect_git_commit()

        self._record: RunRecord | None = None
        self._perf_t0: float | None = None
        self._desired_status: str | None = None  # set via set_status('partial')
        self._desired_exit_code: int | None = None
        self._error: BaseException | None = None
        self._closed = False

    # ──────────────────── context manager ────────────────────
    def __enter__(self) -> "RunContext":
        parent = self._parent_id_explicit
        if parent is None:
            parent_ctx = current_run()
            if parent_ctx is not None:
                parent = parent_ctx.run_id
        self._perf_t0 = time.perf_counter()
        self._record = self.store.start_run(
            stage=self.stage,
            parent_run_id=parent,
            run_id=self._desired_run_id,
            rows_in=self._rows_in,
            host=self._desired_host,
            git_commit=self._desired_git,
            extra=self._initial_extra,
            started_at=self._clock(),
        )
        _ACTIVE.stack.append(self)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> bool:
        # Pop only if we're on top — defensive against misuse
        if _ACTIVE.stack and _ACTIVE.stack[-1] is self:
            _ACTIVE.stack.pop()

        if self._closed:
            return False
        self._closed = True

        if exc_val is not None:
            err_summary = f"{exc_type.__name__ if exc_type else 'Exception'}: {exc_val}"
            err_payload = self._collect_error_payload(exc_val, exc_tb)
            self.store.finish_run(
                self.run_id,
                status=STATUS_FAIL,
                exit_code=self._desired_exit_code if self._desired_exit_code is not None else 1,
                rows_out=self._rows_out,
                error_message=err_summary,
                ended_at=self._clock(),
                extra={"error": err_payload},
            )
            self._error = exc_val
            return False  # re-raise

        status = self._desired_status or STATUS_OK
        exit_code = self._desired_exit_code if self._desired_exit_code is not None else 0
        self.store.finish_run(
            self.run_id,
            status=status,
            exit_code=exit_code,
            rows_out=self._rows_out,
            ended_at=self._clock(),
        )
        return False

    # ──────────────────── public API ────────────────────
    @property
    def run_id(self) -> str:
        if self._record is None:
            raise RuntimeError("RunContext non aperto — usa `with RunContext(...) as run:`")
        return self._record.run_id

    @property
    def parent_run_id(self) -> str | None:
        return self._record.parent_run_id if self._record else None

    @property
    def started_at(self) -> str:
        if self._record is None:
            raise RuntimeError("RunContext non aperto")
        return self._record.started_at

    def elapsed_seconds(self) -> float:
        if self._perf_t0 is None:
            return 0.0
        return max(0.0, time.perf_counter() - self._perf_t0)

    def set_rows_in(self, n: int) -> None:
        self._rows_in = int(n)

    def set_rows_out(self, n: int) -> None:
        self._rows_out = int(n)

    def set_exit_code(self, code: int) -> None:
        self._desired_exit_code = int(code)

    def set_status(self, status: str) -> None:
        if status not in (STATUS_OK, STATUS_PARTIAL, STATUS_FAIL):
            raise ValueError(f"status non valido: {status!r}")
        self._desired_status = status

    def add_event(self, label: str, **payload: Any) -> None:
        """Append a free-form event into `extra.events`. Persisted on close."""
        if self._record is None:
            return
        ev = _ExtraEvent(ts=self._clock(), label=str(label), payload=dict(payload))
        events = self._record.extra.setdefault("events", [])  # type: ignore[union-attr]
        events.append({"ts": ev.ts, "label": ev.label, "payload": ev.payload})

    # ──────────────────── artifact API ────────────────────
    def register_artifact(
        self,
        *,
        kind: str,
        path: str | Path,
        checksum: str | None = None,
        compute_checksum: bool = False,
        rows: int | None = None,
        bytes_size: int | None = None,
        meta: Mapping[str, Any] | None = None,
    ) -> ArtifactRecord:
        """
        Register an artifact produced by *this* run.

        If `compute_checksum=True` and the file exists, SHA-256 is computed
        and `bytes_size` is auto-filled. If `path` is a directory, no
        checksum is computed (you'd need a recursive digest, out of scope).
        """
        p = Path(path)
        rel_path = _to_repo_relative(p)

        if compute_checksum and checksum is None and p.is_file():
            checksum = _sha256_file(p)
        if bytes_size is None and p.is_file():
            bytes_size = p.stat().st_size

        meta_dict = dict(meta or {})
        if not p.exists():
            meta_dict.setdefault("missing_on_register", True)

        return self.store.register_artifact(
            run_id=self.run_id,
            kind=str(kind),
            path=str(rel_path),
            checksum=checksum,
            bytes_size=bytes_size,
            rows=rows,
            meta=meta_dict,
            created_at=self._clock(),
        )

    def register_input(self, artifact: ArtifactRecord | int) -> None:
        """Declare that *this* run consumed an existing artifact."""
        aid = artifact.artifact_id if isinstance(artifact, ArtifactRecord) else int(artifact)
        if aid is None:
            raise ValueError("artifact_id assente")
        self.store.register_input(run_id=self.run_id, artifact_id=aid)

    # ──────────────────── helpers ────────────────────
    def _collect_error_payload(
        self, exc_val: BaseException, exc_tb: TracebackType | None,
    ) -> dict[str, Any]:
        return {
            "type": type(exc_val).__name__,
            "message": str(exc_val),
            "traceback_tail": _tail_traceback(exc_tb, max_lines=15),
        }


# ─────────────────────────────────────────────────────────────────────
# Free helpers
# ─────────────────────────────────────────────────────────────────────
def register_artifact(
    run_id: str,
    *,
    kind: str,
    path: str | Path,
    checksum: str | None = None,
    rows: int | None = None,
    bytes_size: int | None = None,
    meta: Mapping[str, Any] | None = None,
    store: LineageStore | None = None,
    compute_checksum: bool = False,
) -> ArtifactRecord:
    """
    Standalone artifact registration (used when no RunContext is open).

    Prefer `RunContext.register_artifact` when possible — the standalone
    form is mainly here for legacy / shell-script-based integrations.
    """
    s = store or get_default_store()
    p = Path(path)
    rel_path = _to_repo_relative(p)
    if compute_checksum and checksum is None and p.is_file():
        checksum = _sha256_file(p)
    if bytes_size is None and p.is_file():
        bytes_size = p.stat().st_size
    return s.register_artifact(
        run_id=run_id,
        kind=str(kind),
        path=str(rel_path),
        checksum=checksum,
        bytes_size=bytes_size,
        rows=rows,
        meta=dict(meta or {}),
    )


def _detect_git_commit() -> str | None:
    try:
        r = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=3,
        )
        if r.returncode == 0:
            return r.stdout.strip() or None
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass
    return None


def _sha256_file(p: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        while True:
            block = fh.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def _to_repo_relative(p: Path) -> str:
    """Return path relative to repo root when possible; else absolute string."""
    try:
        repo_root = Path(__file__).resolve().parents[3]
        return str(p.resolve().relative_to(repo_root)).replace(os.sep, "/")
    except (ValueError, OSError):
        return str(p)


def _tail_traceback(tb: TracebackType | None, *, max_lines: int) -> list[str]:
    if tb is None:
        return []
    lines = traceback.format_tb(tb)
    flat: list[str] = []
    for chunk in lines:
        flat.extend(line.rstrip() for line in chunk.splitlines() if line.strip())
    return flat[-max_lines:]
