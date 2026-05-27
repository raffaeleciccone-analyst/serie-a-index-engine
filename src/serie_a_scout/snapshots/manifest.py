"""
Snapshot manifest: declarative record of what's inside a WORM snapshot.

A `Manifest` is serialized as `manifest.json` next to the parquet artifacts.
It contains the data needed to:

* verify integrity (SHA-256 per file)
* audit provenance (git commit, run_id, timestamp)
* drive a deterministic restore (source tables, schema_version, row counts)

The schema is versioned (`schema_version`) to allow forward-compatible reads
of older snapshots.
"""
from __future__ import annotations

import json
import subprocess
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


# Bump on incompatible manifest changes. Minor schema additions stay 1.x.
SCHEMA_VERSION: str = "1.0.0"


@dataclass
class FileEntry:
    """Single artifact inside a snapshot directory."""
    path: str                 # path relative to the snapshot dir
    sha256: str               # hex digest
    bytes_size: int
    rows: int | None = None   # row count if tabular (None for JSON/binary)
    table: str | None = None  # source table name if tabular


@dataclass
class Manifest:
    """
    Full snapshot manifest.

    Fields are intentionally typed and JSON-serializable. Use
    `to_json()` / `from_json()` for round-trip; the format is human-readable
    and stable across runs (sorted keys, indented).
    """
    schema_version: str
    run_id: str
    season: str
    giornata: int
    created_at_utc: str
    reliability_score: int | None
    git_commit: str | None
    source_tables: list[str]
    files: list[FileEntry]
    extras: dict[str, Any] = field(default_factory=dict)

    # ──────────────────────────────────────────────────────────────
    # Serialization
    # ──────────────────────────────────────────────────────────────
    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        # Sort files by path for determinism
        d["files"] = sorted(d["files"], key=lambda f: f["path"])
        return d

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, ensure_ascii=False, sort_keys=True)

    @classmethod
    def from_json(cls, text: str) -> "Manifest":
        d = json.loads(text)
        files = [FileEntry(**f) for f in d.pop("files", [])]
        return cls(files=files, **d)


# ──────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────
def now_utc_iso() -> str:
    """Current time in ISO-8601 UTC with seconds precision (no microseconds)."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_run_id() -> str:
    """Random opaque identifier for this snapshot run."""
    return uuid.uuid4().hex


def detect_git_commit() -> str | None:
    """
    Return current HEAD commit short SHA if the project is a git repo,
    else None. Best-effort; failure is silent (snapshot is not a git op).
    """
    try:
        r = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=3,
        )
        if r.returncode == 0:
            return r.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass
    return None
