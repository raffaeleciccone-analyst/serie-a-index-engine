"""
Prometheus textfile collector emitter.

We write metrics to plain `.prom` files in `obs/metrics/`.  A Prometheus
node-exporter `textfile` collector (or Grafana Agent) can scrape that
directory directly with zero protocol overhead.

Design choices:
  * append-mode: every emission appends a new sample. Trends are visible
    via timestamps embedded in the file name when needed.
  * `# HELP` / `# TYPE` are emitted ONLY the first time the metric name
    appears in a given file, to keep the file Prom-compliant.

This module is intentionally dependency-free (no `prometheus_client`).
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Mapping


METRICS_DIR = Path(__file__).resolve().parents[3] / "obs" / "metrics"


def _format_labels(labels: Mapping[str, str] | None) -> str:
    if not labels:
        return ""
    parts = []
    for k in sorted(labels):
        v = str(labels[k]).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
        parts.append(f'{k}="{v}"')
    return "{" + ",".join(parts) + "}"


def _file_has_metric_header(path: Path, name: str) -> bool:
    if not path.exists():
        return False
    needle = f"# TYPE {name} "
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            if line.startswith(needle):
                return True
    return False


def emit_metric(
    name: str,
    value: float | int,
    *,
    labels: Mapping[str, str] | None = None,
    help_text: str = "",
    metric_type: str = "gauge",
    file_name: str = "default.prom",
) -> Path:
    """
    Append a metric sample to `obs/metrics/<file_name>`.

    Returns the path written. Thread-safe enough for occasional emissions;
    do NOT use for high-frequency loops (use the prometheus_client lib).
    """
    METRICS_DIR.mkdir(parents=True, exist_ok=True)
    path = METRICS_DIR / file_name

    lines: list[str] = []
    if not _file_has_metric_header(path, name):
        if help_text:
            lines.append(f"# HELP {name} {help_text}")
        lines.append(f"# TYPE {name} {metric_type}")
    lines.append(f"{name}{_format_labels(labels)} {value}")

    with open(path, "a", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    return path


def increment_counter(
    name: str, *,
    labels: Mapping[str, str] | None = None,
    help_text: str = "",
    file_name: str = "default.prom",
) -> Path:
    """Convenience wrapper for counter-style emission of value 1."""
    return emit_metric(
        name, 1, labels=labels, help_text=help_text,
        metric_type="counter", file_name=file_name,
    )
