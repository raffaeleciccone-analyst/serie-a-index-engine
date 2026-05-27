"""
Modello dati per gli esiti dell'audit. Ogni `Finding` rappresenta un'anomalia
rilevata in una tabella o pipeline. Sono raccolti in un `Report` esportabile
in JSON.
"""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any
import datetime as dt
import json


class Severity(str, Enum):
    INFO = "info"          # nota informativa, no impact
    LOW = "low"            # rumore, basso impact, fix consigliato
    MEDIUM = "medium"      # impact sui calcoli, fix raccomandato
    HIGH = "high"          # corrompe le classifiche, fix urgente
    CRITICAL = "critical"  # blocca le pipeline o produce dati falsi


class Area(str, Enum):
    SCHEMA = "schema"                # DDL, indici, FK
    REFERENTIAL = "referential"      # FK rotte, orfani
    DUPLICATES = "duplicates"        # ID duplicati o concetti duplicati
    CONSISTENCY = "consistency"      # somma aggregata != somma per-riga
    TEMPORAL = "temporal"            # date incoerenti, range sbagliati
    NAMING = "naming"                # nomi inconsistenti
    UNUSED = "unused"                # dati morti, tabelle vuote
    PIPELINE = "pipeline"            # bug script, race, idempotenza
    SECURITY = "security"            # credenziali hardcoded, PII
    METRIC = "metric"                # KPI/aggregati anomali


@dataclass
class Finding:
    code: str                # es. "DUP-001"
    title: str
    area: Area
    severity: Severity
    table: str | None = None
    rows_affected: int = 0
    description: str = ""
    root_cause: str = ""
    fix_available: bool = False
    fix_strategy: str = ""
    samples: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["area"] = self.area.value
        d["severity"] = self.severity.value
        return d


@dataclass
class Report:
    db_name: str
    started_at: str = field(default_factory=lambda: dt.datetime.now().isoformat())
    completed_at: str | None = None
    findings: list[Finding] = field(default_factory=list)
    tables_inspected: list[str] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)

    def add(self, f: Finding) -> None:
        self.findings.append(f)

    def by_severity(self) -> dict[str, int]:
        out: dict[str, int] = {s.value: 0 for s in Severity}
        for f in self.findings:
            out[f.severity.value] += 1
        return out

    def reliability_score(self) -> int:
        """Punteggio 0-100. CRITICAL pesa 25, HIGH 10, MEDIUM 4, LOW 1, INFO 0."""
        weights = {Severity.CRITICAL: 25, Severity.HIGH: 10, Severity.MEDIUM: 4,
                   Severity.LOW: 1, Severity.INFO: 0}
        penalty = sum(weights[f.severity] for f in self.findings)
        return max(0, 100 - penalty)

    def to_json(self) -> str:
        return json.dumps({
            "db_name": self.db_name,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "summary": {
                **self.summary,
                "by_severity": self.by_severity(),
                "reliability_score": self.reliability_score(),
                "total_findings": len(self.findings),
                "tables_inspected": self.tables_inspected,
            },
            "findings": [f.to_dict() for f in self.findings],
        }, indent=2, ensure_ascii=False, default=str)
