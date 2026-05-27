"""
Safe player entity resolution.

Replaces the dangerous name-only lookup used in legacy ingestion code:

    # BEFORE (silent collision risk):
    giocatori_map = {nome.lower(): id for id, nome in cursor.fetchall()}
    gid = giocatori_map.get(input_name.lower())   # collision = silent corruption

    # AFTER (this module):
    resolver = PlayerResolver(players)
    res = resolver.resolve(input_name, squadra_id)
    if res.status is ResolveStatus.OK:
        gid = res.giocatore_id
    else:
        # ambiguous / not_found → quarantined, NOT silently assigned

Why this is critical:
  * Two players in different teams can share a normalized name
    (e.g. "Lautaro" present in Inter AND Parma in 25/26).
  * A January transfer means the same canonical player has a NEW squadra_id;
    the resolver flags this as `team_mismatch` rather than silently assigning
    his goals to the old club's player.
  * Without this safety net, Understat ingestion would happily write match
    stats against the WRONG player_id.

Quarantine: every non-OK resolution is appended to a JSONL log so a human
can review and decide (add alias, fix DB, etc.). The path is configurable
to make the module unit-testable.
"""
from __future__ import annotations

import json
import os
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Iterable


# Default location of the quarantine log. Tests override via `quarantine_path`.
DEFAULT_QUARANTINE_PATH = (
    Path(__file__).resolve().parents[3] / "logs" / "quarantine" / "player_resolution.jsonl"
)


# ──────────────────────────────────────────────────────────────────
# Normalization
# ──────────────────────────────────────────────────────────────────
def normalize_name(s: str | None) -> str:
    """
    Canonical name form:
      * NFKD decompose
      * drop combining marks (accents)
      * lowercase, strip, collapse whitespace
      * remove a few invisible chars (NBSP, SHY)
    """
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", str(s))
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower().strip()
    # NBSP, SHY, ZWSP/ZWNJ/ZWJ, BOM
    for invisible in (" ", "­", "​", "‌", "‍", "﻿"):
        s = s.replace(invisible, " " if invisible == " " else "")
    s = re.sub(r"\s+", " ", s)
    return s.strip()


# ──────────────────────────────────────────────────────────────────
# Result model
# ──────────────────────────────────────────────────────────────────
class ResolveStatus(str, Enum):
    OK = "ok"                  # safe assignment, use giocatore_id
    NOT_FOUND = "not_found"    # no candidate at all
    AMBIGUOUS = "ambiguous"    # >1 candidate, can't decide safely


@dataclass
class ResolveResult:
    status: ResolveStatus
    giocatore_id: int | None = None
    confidence: float = 0.0     # 0..1, only meaningful when status == OK
    reason: str = ""            # short machine code for the decision
    candidates: list[dict[str, Any]] = field(default_factory=list)

    def is_ok(self) -> bool:
        return self.status is ResolveStatus.OK


# ──────────────────────────────────────────────────────────────────
# Resolver
# ──────────────────────────────────────────────────────────────────
class PlayerResolver:
    """
    Index a list of canonical players and provide safe lookup.

    The resolver pre-computes TWO indexes:
      * (name_normalized, squadra_id) → giocatore_id     # primary, exact
      * name_normalized → [candidate dicts]              # for ambiguity detection
    """

    def __init__(
        self,
        players: Iterable[dict[str, Any]],
        *,
        quarantine_path: Path | None = None,
        on_quarantine: Callable[[dict[str, Any]], None] | None = None,
    ):
        self._by_name_team: dict[tuple[str, int], int] = {}
        self._by_name: dict[str, list[dict[str, Any]]] = {}
        for p in players:
            gid = int(p["id"])
            name_norm = normalize_name(p.get("nome"))
            sq_id = int(p["squadra_id"]) if p.get("squadra_id") is not None else None
            if not name_norm:
                continue
            if sq_id is not None:
                # If two rows happen to share (name, squadra) we keep the LAST id
                # but the existence of two rows is itself a DB bug; the audit
                # check_duplicates_by_normalized_name will catch it.
                self._by_name_team[(name_norm, sq_id)] = gid
            self._by_name.setdefault(name_norm, []).append({
                "id": gid,
                "nome": p.get("nome"),
                "squadra_id": sq_id,
            })

        self._quarantine_path = Path(quarantine_path) if quarantine_path else DEFAULT_QUARANTINE_PATH
        self._on_quarantine = on_quarantine

    # ----------- public api -----------
    def resolve(
        self,
        nome: str,
        squadra_id: int | None,
        *,
        provider: str = "understat",
        extra: dict[str, Any] | None = None,
    ) -> ResolveResult:
        """
        Resolve (nome, squadra_id) to a single giocatore_id.

        Resolution order:
          1. exact (name, squadra_id)            → confidence 1.0  OK
          2. single candidate by name + team match (case insensitive sq) → 0.95  OK
          3. single candidate by name, no team in input → 0.6  OK
          4. single candidate by name, DIFFERENT team in input → AMBIGUOUS (quarantine)
          5. multiple candidates by name → AMBIGUOUS (quarantine)
          6. no candidates → NOT_FOUND (quarantine)
        """
        name_norm = normalize_name(nome)
        if not name_norm:
            return self._not_ok(ResolveStatus.NOT_FOUND, "empty_name", nome, squadra_id,
                                provider, extra)

        # 1) primary path
        if squadra_id is not None:
            gid = self._by_name_team.get((name_norm, int(squadra_id)))
            if gid is not None:
                return ResolveResult(
                    ResolveStatus.OK, giocatore_id=gid,
                    confidence=1.0, reason="exact_name_team",
                )

        # 2..5) fallback via name index
        cands = self._by_name.get(name_norm, [])
        if not cands:
            return self._not_ok(ResolveStatus.NOT_FOUND, "no_candidate_by_name",
                                nome, squadra_id, provider, extra)

        if len(cands) == 1:
            c = cands[0]
            if squadra_id is None:
                # input lacks team → accept with reduced confidence
                return ResolveResult(
                    ResolveStatus.OK, giocatore_id=c["id"],
                    confidence=0.6, reason="single_name_no_team",
                    candidates=cands,
                )
            if c["squadra_id"] == int(squadra_id):
                return ResolveResult(
                    ResolveStatus.OK, giocatore_id=c["id"],
                    confidence=0.95, reason="single_name_team_match",
                )
            # team mismatch: NOT safe — likely a transfer or an alias clash
            return self._not_ok(
                ResolveStatus.AMBIGUOUS, "single_name_team_mismatch",
                nome, squadra_id, provider,
                {**(extra or {}), "db_squadra_id": c["squadra_id"], "db_id": c["id"]},
                candidates=cands,
            )

        # >1 candidates with same normalized name
        return self._not_ok(
            ResolveStatus.AMBIGUOUS, f"multiple_candidates_{len(cands)}",
            nome, squadra_id, provider, extra, candidates=cands,
        )

    # ----------- ambiguity reporting -----------
    @property
    def quarantine_path(self) -> Path:
        return self._quarantine_path

    def _not_ok(
        self,
        status: ResolveStatus, reason: str,
        nome: str, squadra_id: int | None,
        provider: str, extra: dict[str, Any] | None,
        candidates: list[dict[str, Any]] | None = None,
    ) -> ResolveResult:
        event = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "provider": provider,
            "status": status.value,
            "reason": reason,
            "nome_input": nome,
            "nome_normalized": normalize_name(nome),
            "squadra_id_input": squadra_id,
            "candidates": (candidates or [])[:5],
            "extra": extra or {},
        }
        if self._on_quarantine is not None:
            self._on_quarantine(event)
        else:
            self._append_to_jsonl(event)
        return ResolveResult(
            status=status, giocatore_id=None, confidence=0.0,
            reason=reason, candidates=candidates or [],
        )

    def _append_to_jsonl(self, event: dict[str, Any]) -> None:
        try:
            self._quarantine_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._quarantine_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")
        except OSError:
            # never let observability take down ingestion
            pass
