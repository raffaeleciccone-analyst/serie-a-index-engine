"""
Lineage export: JSON dump + Mermaid DAG for the runs/artifacts captured in
the SQLite lineage store.

Outputs
-------
* `reports/lineage/lineage_<run_id>.md`      — per-run report w/ Mermaid
* `reports/lineage/latest_lineage_report.md` — global rollup
* `reports/lineage/lineage_<ts>.json`        — raw JSON dump (optional)

Mermaid syntax:
  flowchart LR
    R_<short>["stage<br/>status"] --> A_<id>(["kind: path"])
    A_<id> --> R_<short2>

Truncated short ids keep the diagram readable on GitHub.
"""
from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from ..core.lineage_store import (
    ArtifactRecord, LineageStore, RunRecord, get_default_store,
)


_REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_REPORT_DIR = _REPO_ROOT / "reports" / "lineage"


# ─────────────────────────────────────────────────────────────────────
# Pure helpers
# ─────────────────────────────────────────────────────────────────────
def collect_lineage(
    store: LineageStore | None = None,
    *,
    run_id: str | None = None,
    limit_runs: int | None = 200,
) -> dict[str, object]:
    """
    Collect a JSON-serialisable lineage payload.

    If `run_id` is provided, returns only that run + its ancestors and the
    chain of consumer runs (provenance both ways). Otherwise returns the
    most recent `limit_runs` runs.
    """
    s = store or get_default_store()

    if run_id is not None:
        runs = _collect_chain(s, run_id)
    else:
        runs = s.iter_runs(limit=limit_runs)

    run_ids = {r.run_id for r in runs}
    artifacts: list[ArtifactRecord] = []
    inputs: list[dict[str, object]] = []
    for r in runs:
        for a in s.artifacts_for(r.run_id):
            artifacts.append(a)
        for inp in s.inputs_for(r.run_id):
            if inp.artifact_id is None:
                continue
            inputs.append({
                "run_id": r.run_id,
                "artifact_id": int(inp.artifact_id),
            })

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "runs": [_run_to_dict(r) for r in runs],
        "artifacts": [_artifact_to_dict(a) for a in artifacts],
        "inputs": inputs,
        "counters": s.aggregate_counters(),
        "diagnostics": s.detect_orphans(),
        "edges": _edges_for_runs(runs, run_ids, artifacts, inputs),
    }


def render_mermaid(lineage: dict[str, object]) -> str:
    """Render the lineage payload as a Mermaid flowchart."""
    runs: list[dict[str, object]] = lineage.get("runs", [])  # type: ignore[assignment]
    artifacts: list[dict[str, object]] = lineage.get("artifacts", [])  # type: ignore[assignment]
    edges: list[dict[str, str]] = lineage.get("edges", [])  # type: ignore[assignment]

    lines: list[str] = ["```mermaid", "flowchart LR"]
    if not runs:
        lines.append("  empty[\"No lineage runs captured\"]")
        lines.append("```")
        return "\n".join(lines)

    for r in runs:
        node_id = _run_node(r["run_id"])
        label = f"{_escape(r['stage'])}<br/>{_escape(r['status'])}"
        # Color hint via class
        status = str(r.get("status", ""))
        shape_open, shape_close = "[\"", "\"]"
        lines.append(f'  {node_id}{shape_open}{label}{shape_close}')
        cls = {
            "ok": "okRun",
            "fail": "failRun",
            "running": "runningRun",
            "partial": "partialRun",
        }.get(status, "okRun")
        lines.append(f"  class {node_id} {cls}")

    seen_artifacts: set[int] = set()
    for a in artifacts:
        aid = int(a["artifact_id"])
        if aid in seen_artifacts:
            continue
        seen_artifacts.add(aid)
        node_id = _artifact_node(aid)
        label = f"{_escape(a['kind'])}<br/>{_escape(_short_path(str(a['path'])))}"
        lines.append(f'  {node_id}(["{label}"])')

    for e in edges:
        lines.append(f"  {e['from']} --> {e['to']}")

    # Mermaid classes (colours render where supported)
    lines.append("  classDef okRun fill:#e8f5e9,stroke:#2e7d32,color:#1b5e20")
    lines.append("  classDef failRun fill:#ffebee,stroke:#c62828,color:#b71c1c")
    lines.append("  classDef runningRun fill:#fff8e1,stroke:#f57f17,color:#5d4037")
    lines.append("  classDef partialRun fill:#fffde7,stroke:#f9a825,color:#6d4c41")
    lines.append("```")
    return "\n".join(lines)


def render_report(lineage: dict[str, object], *, title: str | None = None) -> str:
    """Render the full Markdown lineage report."""
    runs: list[dict[str, object]] = lineage.get("runs", [])  # type: ignore[assignment]
    artifacts: list[dict[str, object]] = lineage.get("artifacts", [])  # type: ignore[assignment]
    counters: dict[str, int] = lineage.get("counters", {})  # type: ignore[assignment]
    diag: dict[str, list] = lineage.get("diagnostics", {})  # type: ignore[assignment]
    generated_at = lineage.get("generated_at", "")

    sections: list[str] = []
    sections.append(f"# {title or 'Lineage report'}")
    sections.append("")
    sections.append(f"**Generated:** {generated_at}")
    sections.append(f"**Runs in view:** {len(runs)}  ·  **Artifacts:** {len(artifacts)}")
    sections.append("")

    sections.append("## Counters (cumulative)")
    sections.append("")
    sections.append("| Metric | Value |")
    sections.append("|---|---:|")
    for k in ("runs_total", "failures_total", "artifacts_total", "rows_total"):
        sections.append(f"| {k} | {counters.get(k, 0)} |")
    sections.append("")

    sections.append("## Runs")
    sections.append("")
    sections.append(_runs_table(runs))
    sections.append("")

    sections.append("## Artifacts")
    sections.append("")
    sections.append(_artifacts_table(artifacts))
    sections.append("")

    sections.append("## DAG")
    sections.append("")
    sections.append(render_mermaid(lineage))
    sections.append("")

    sections.append("## Diagnostics")
    sections.append("")
    if not any(diag.get(k) for k in ("runs_still_running", "artifacts_missing_file", "runs_without_artifacts")):
        sections.append("_Nessuna anomalia rilevata._")
    else:
        if diag.get("runs_still_running"):
            sections.append("**Runs ancora in stato `running`** (potenziale orfano):")
            for rid in diag["runs_still_running"]:
                sections.append(f"  - `{rid}`")
            sections.append("")
        if diag.get("artifacts_missing_file"):
            sections.append("**Artifact con file mancante su disco:**")
            for aid in diag["artifacts_missing_file"]:
                sections.append(f"  - artifact_id={aid}")
            sections.append("")
        if diag.get("runs_without_artifacts"):
            sections.append("**Run completati senza artifact prodotti:**")
            for rid in diag["runs_without_artifacts"]:
                sections.append(f"  - `{rid}`")
            sections.append("")

    return "\n".join(sections).rstrip() + "\n"


# ─────────────────────────────────────────────────────────────────────
# Disk I/O
# ─────────────────────────────────────────────────────────────────────
def write_json_dump(
    lineage: dict[str, object],
    *,
    out_dir: Path | str | None = None,
    timestamp: datetime | None = None,
) -> Path:
    out = Path(out_dir) if out_dir else DEFAULT_REPORT_DIR
    out.mkdir(parents=True, exist_ok=True)
    ts = (timestamp or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
    path = out / f"lineage_{ts}.json"
    path.write_text(
        json.dumps(lineage, indent=2, sort_keys=True, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    return path


def write_report_md(
    lineage: dict[str, object],
    *,
    out_dir: Path | str | None = None,
    file_name: str | None = None,
    title: str | None = None,
) -> Path:
    out = Path(out_dir) if out_dir else DEFAULT_REPORT_DIR
    out.mkdir(parents=True, exist_ok=True)
    name = file_name or "latest_lineage_report.md"
    path = out / name
    path.write_text(render_report(lineage, title=title), encoding="utf-8")
    return path


def write_run_report(
    run_id: str,
    *,
    store: LineageStore | None = None,
    out_dir: Path | str | None = None,
) -> Path:
    """Per-run report: `lineage_<short_run_id>.md`."""
    lineage = collect_lineage(store, run_id=run_id)
    short = run_id[:12]
    return write_report_md(
        lineage,
        out_dir=out_dir,
        file_name=f"lineage_{short}.md",
        title=f"Lineage run `{short}`",
    )


# ─────────────────────────────────────────────────────────────────────
# Internals
# ─────────────────────────────────────────────────────────────────────
def _collect_chain(store: LineageStore, run_id: str) -> list[RunRecord]:
    """Walk ancestors AND downstream consumers from `run_id`."""
    seen: dict[str, RunRecord] = {}

    # ancestors
    cur = store.get_run(run_id)
    if cur is None:
        return []
    seen[cur.run_id] = cur
    parent_id = cur.parent_run_id
    while parent_id and parent_id not in seen:
        p = store.get_run(parent_id)
        if p is None:
            break
        seen[p.run_id] = p
        parent_id = p.parent_run_id

    # descendants (children + artifact consumers, BFS)
    queue: list[str] = [cur.run_id]
    while queue:
        rid = queue.pop(0)
        for child in store.children_of(rid):
            if child.run_id not in seen:
                seen[child.run_id] = child
                queue.append(child.run_id)
        for art in store.artifacts_for(rid):
            if art.artifact_id is None:
                continue
            for consumer in store.consumers_of(art.artifact_id):
                if consumer.run_id not in seen:
                    seen[consumer.run_id] = consumer
                    queue.append(consumer.run_id)

    return sorted(seen.values(), key=lambda r: (r.started_at, r.run_id))


def _edges_for_runs(
    runs: list[RunRecord],
    run_ids: set[str],
    artifacts: list[ArtifactRecord],
    inputs: list[dict[str, object]],
) -> list[dict[str, str]]:
    edges: list[dict[str, str]] = []

    # parent → child
    for r in runs:
        if r.parent_run_id and r.parent_run_id in run_ids:
            edges.append({
                "from": _run_node(r.parent_run_id),
                "to": _run_node(r.run_id),
                "label": "child",
            })

    # run → artifact
    art_index: dict[int, ArtifactRecord] = {
        int(a.artifact_id): a for a in artifacts if a.artifact_id is not None
    }
    for a in artifacts:
        if a.artifact_id is None:
            continue
        edges.append({
            "from": _run_node(a.run_id),
            "to": _artifact_node(int(a.artifact_id)),
            "label": "produces",
        })

    # artifact → consumer run
    for inp in inputs:
        aid = int(inp["artifact_id"])
        rid = str(inp["run_id"])
        if aid not in art_index:
            continue
        edges.append({
            "from": _artifact_node(aid),
            "to": _run_node(rid),
            "label": "consumes",
        })

    # de-duplicate while preserving order
    seen: set[tuple[str, str]] = set()
    deduped: list[dict[str, str]] = []
    for e in edges:
        key = (e["from"], e["to"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(e)
    return deduped


def _run_to_dict(r: RunRecord) -> dict[str, object]:
    d = asdict(r)
    d["extra"] = dict(r.extra) if r.extra else {}
    return d


def _artifact_to_dict(a: ArtifactRecord) -> dict[str, object]:
    d = asdict(a)
    d["meta"] = dict(a.meta) if a.meta else {}
    return d


def _runs_table(runs: list[dict[str, object]]) -> str:
    if not runs:
        return "_Nessun run._"
    header = "| run_id | stage | status | started | duration ms | exit | parent |"
    sep = "|---|---|---|---|---:|---:|---|"
    body: list[str] = []
    for r in runs:
        body.append(
            f"| `{str(r['run_id'])[:12]}` "
            f"| {r['stage']} "
            f"| {r['status']} "
            f"| {r['started_at']} "
            f"| {r.get('duration_ms') if r.get('duration_ms') is not None else '–'} "
            f"| {r.get('exit_code') if r.get('exit_code') is not None else '–'} "
            f"| {('`'+str(r['parent_run_id'])[:12]+'`') if r.get('parent_run_id') else '–'} |"
        )
    return "\n".join([header, sep, *body])


def _artifacts_table(arts: list[dict[str, object]]) -> str:
    if not arts:
        return "_Nessun artifact._"
    header = "| id | kind | path | bytes | rows | sha256 | run |"
    sep = "|---:|---|---|---:|---:|---|---|"
    body = []
    for a in arts:
        cs = a.get("checksum") or ""
        body.append(
            f"| {a['artifact_id']} "
            f"| {a['kind']} "
            f"| `{a['path']}` "
            f"| {a.get('bytes_size') if a.get('bytes_size') is not None else '–'} "
            f"| {a.get('rows') if a.get('rows') is not None else '–'} "
            f"| {(cs[:12] + '…') if cs else '–'} "
            f"| `{str(a['run_id'])[:12]}` |"
        )
    return "\n".join([header, sep, *body])


def _run_node(run_id: str) -> str:
    return "R_" + run_id[:10]


def _artifact_node(artifact_id: int) -> str:
    return f"A_{artifact_id}"


def _short_path(p: str) -> str:
    parts = p.replace("\\", "/").split("/")
    if len(parts) <= 3:
        return p
    return ".../" + "/".join(parts[-2:])


def _escape(v: object) -> str:
    s = str(v)
    return s.replace('"', "'").replace("|", "\\|")
