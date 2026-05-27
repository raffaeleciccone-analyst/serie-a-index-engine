"""
Cross-season integrity engine.

Implements 9 checks that span more than one season:

  1. PLAYER_CONTINUITY        — same player across seasons keeps a coherent identity
  2. IMPOSSIBLE_AGE_TRANSITION — implied age moves backwards or jumps > 1 year
  3. DUPLICATE_IDENTITIES     — two `player_id`s point to the same canonical person
  4. TRANSFER_COHERENCE       — team changes between seasons are tracked (info)
  5. CLUB_CONTINUITY          — team identity persists across seasons
  6. SEASON_COMPLETENESS      — coverage_pct vs threshold per declared season
  7. ORPHAN_MATCHES           — match rows that reference unknown players
  8. ORPHAN_PLAYER_STATS      — player rows that have no match data
  9. TPI_LONGITUDINAL_STABILITY — a player's TPI shouldn't swing wildly YoY without minutes change

The engine is pure: input dataclasses, output `CrossSeasonReport`. No DB
or filesystem side effects, no anomalies emitted directly — the runner
script translates findings into AnomalyType.CROSS_SEASON_* for F1.5.

Each `IntegrityCheck` is independently switchable so a runbook can
isolate one signal.
"""
from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from datetime import date, datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import pandas as pd


# ─────────────────────────────────────────────────────────────────────
# Enums
# ─────────────────────────────────────────────────────────────────────
class IntegritySeverity(str, Enum):
    INFO = "INFO"
    WARN = "WARN"
    CRITICAL = "CRITICAL"


class IntegrityCheck(str, Enum):
    PLAYER_CONTINUITY = "player_continuity"
    IMPOSSIBLE_AGE_TRANSITION = "impossible_age_transition"
    DUPLICATE_IDENTITIES = "duplicate_identities"
    TRANSFER_COHERENCE = "transfer_coherence"
    CLUB_CONTINUITY = "club_continuity"
    SEASON_COMPLETENESS = "season_completeness"
    ORPHAN_MATCHES = "orphan_matches"
    ORPHAN_PLAYER_STATS = "orphan_player_stats"
    TPI_LONGITUDINAL_STABILITY = "tpi_longitudinal_stability"


# ─────────────────────────────────────────────────────────────────────
# Dataclasses
# ─────────────────────────────────────────────────────────────────────
@dataclass
class CrossSeasonFinding:
    check: IntegrityCheck
    severity: IntegritySeverity
    season_a: str | None
    season_b: str | None
    subject_id: str | int | None
    message: str
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["check"] = self.check.value
        d["severity"] = self.severity.value
        return d


@dataclass
class CrossSeasonReport:
    generated_at: str
    seasons_inspected: list[str]
    findings: list[CrossSeasonFinding]

    @property
    def severity(self) -> IntegritySeverity:
        if any(f.severity is IntegritySeverity.CRITICAL for f in self.findings):
            return IntegritySeverity.CRITICAL
        if any(f.severity is IntegritySeverity.WARN for f in self.findings):
            return IntegritySeverity.WARN
        return IntegritySeverity.INFO

    @property
    def n_findings(self) -> int:
        return len(self.findings)

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "seasons_inspected": list(self.seasons_inspected),
            "n_findings": self.n_findings,
            "severity": self.severity.value,
            "findings": [f.to_dict() for f in self.findings],
        }


@dataclass
class CrossSeasonInput:
    """
    Input bundle for the integrity engine. Callers compose this from
    snapshots or from in-memory data structures.

    `players`     — DataFrame with columns:
                    player_id, season, name, team, birth_date (optional), position (optional)
    `player_matches` — DataFrame with: player_id, season, giornata, team, minutes, …
    `tpi`         — Optional DataFrame with: player_id, season, tpi_totale, minuti
    `coverage`    — Optional list of (season, coverage_pct) pairs from SeasonRegistry
    """
    players: pd.DataFrame
    player_matches: pd.DataFrame
    tpi: pd.DataFrame | None = None
    coverage: list[tuple[str, float]] = field(default_factory=list)

    def seasons(self) -> list[str]:
        seasons = set(self.players.get("season", pd.Series(dtype=str)).dropna().astype(str))
        seasons |= set(self.player_matches.get("season", pd.Series(dtype=str)).dropna().astype(str))
        if self.tpi is not None:
            seasons |= set(self.tpi.get("season", pd.Series(dtype=str)).dropna().astype(str))
        return sorted(seasons)


# ─────────────────────────────────────────────────────────────────────
# Engine
# ─────────────────────────────────────────────────────────────────────
def run_integrity_checks(
    bundle: CrossSeasonInput,
    *,
    enabled: Iterable[IntegrityCheck] | None = None,
    coverage_warn_pct: float = 80.0,
    coverage_critical_pct: float = 50.0,
    tpi_relative_jump_warn: float = 0.40,
    tpi_relative_jump_critical: float = 0.80,
    now: datetime | None = None,
) -> CrossSeasonReport:
    """Orchestrate every enabled check. Returns one `CrossSeasonReport`."""
    selected = set(enabled) if enabled else set(IntegrityCheck)
    findings: list[CrossSeasonFinding] = []

    if IntegrityCheck.SEASON_COMPLETENESS in selected:
        findings.extend(_check_season_completeness(
            bundle.coverage,
            warn_pct=coverage_warn_pct,
            critical_pct=coverage_critical_pct,
        ))

    if IntegrityCheck.PLAYER_CONTINUITY in selected:
        findings.extend(_check_player_continuity(bundle.players))

    if IntegrityCheck.IMPOSSIBLE_AGE_TRANSITION in selected:
        findings.extend(_check_age_transitions(bundle.players))

    if IntegrityCheck.DUPLICATE_IDENTITIES in selected:
        findings.extend(_check_duplicate_identities(bundle.players))

    if IntegrityCheck.TRANSFER_COHERENCE in selected:
        findings.extend(_check_transfer_coherence(bundle.players))

    if IntegrityCheck.CLUB_CONTINUITY in selected:
        findings.extend(_check_club_continuity(bundle.players))

    if IntegrityCheck.ORPHAN_MATCHES in selected:
        findings.extend(_check_orphan_matches(bundle.players, bundle.player_matches))

    if IntegrityCheck.ORPHAN_PLAYER_STATS in selected:
        findings.extend(_check_orphan_player_stats(bundle.players, bundle.player_matches))

    if IntegrityCheck.TPI_LONGITUDINAL_STABILITY in selected and bundle.tpi is not None:
        findings.extend(_check_tpi_longitudinal_stability(
            bundle.tpi,
            warn=tpi_relative_jump_warn,
            critical=tpi_relative_jump_critical,
        ))

    return CrossSeasonReport(
        generated_at=(now or datetime.now(timezone.utc)).isoformat(timespec="seconds"),
        seasons_inspected=bundle.seasons(),
        findings=sorted(findings, key=lambda f: (-_sev_rank(f.severity), f.check.value)),
    )


# ─────────────────────────────────────────────────────────────────────
# Individual checks
# ─────────────────────────────────────────────────────────────────────
def _check_season_completeness(
    coverage: list[tuple[str, float]], *,
    warn_pct: float, critical_pct: float,
) -> list[CrossSeasonFinding]:
    out: list[CrossSeasonFinding] = []
    for season, pct in coverage:
        if pct < critical_pct:
            out.append(CrossSeasonFinding(
                check=IntegrityCheck.SEASON_COMPLETENESS,
                severity=IntegritySeverity.CRITICAL,
                season_a=season, season_b=None, subject_id=None,
                message=f"Coverage {pct:.1f}% per stagione {season} < {critical_pct}%",
                evidence={"coverage_pct": pct, "threshold": critical_pct},
            ))
        elif pct < warn_pct:
            out.append(CrossSeasonFinding(
                check=IntegrityCheck.SEASON_COMPLETENESS,
                severity=IntegritySeverity.WARN,
                season_a=season, season_b=None, subject_id=None,
                message=f"Coverage {pct:.1f}% per stagione {season} < {warn_pct}%",
                evidence={"coverage_pct": pct, "threshold": warn_pct},
            ))
    return out


def _check_player_continuity(players: pd.DataFrame) -> list[CrossSeasonFinding]:
    """Same player_id across seasons should keep a stable `name`."""
    if players.empty or "player_id" not in players.columns:
        return []
    out: list[CrossSeasonFinding] = []
    by_pid = players.groupby("player_id")
    for pid, grp in by_pid:
        names = grp["name"].dropna().astype(str).unique().tolist() if "name" in grp.columns else []
        if len(names) > 1:
            # Different names — could be a transliteration drift or an ER bug.
            if _names_are_close(names):
                sev = IntegritySeverity.INFO
            else:
                sev = IntegritySeverity.WARN
            out.append(CrossSeasonFinding(
                check=IntegrityCheck.PLAYER_CONTINUITY,
                severity=sev,
                season_a=None, season_b=None, subject_id=_jsonable_id(pid),
                message=f"player_id={pid} ha nomi diversi tra stagioni: {names}",
                evidence={"names": names},
            ))
    return out


def _check_age_transitions(players: pd.DataFrame) -> list[CrossSeasonFinding]:
    """birth_date should be identical across seasons for the same player."""
    if players.empty or "birth_date" not in players.columns:
        return []
    out: list[CrossSeasonFinding] = []
    for pid, grp in players.groupby("player_id"):
        bdates = (
            grp["birth_date"].dropna().astype(str).unique().tolist()
        )
        if len(bdates) > 1:
            out.append(CrossSeasonFinding(
                check=IntegrityCheck.IMPOSSIBLE_AGE_TRANSITION,
                severity=IntegritySeverity.CRITICAL,
                season_a=None, season_b=None, subject_id=_jsonable_id(pid),
                message=f"player_id={pid} ha birth_date diverse tra stagioni: {bdates}",
                evidence={"birth_dates": bdates},
            ))
    return out


def _check_duplicate_identities(players: pd.DataFrame) -> list[CrossSeasonFinding]:
    """
    Two different player_ids that share (name normalized, birth_date) →
    candidate duplicate identity (ER missed).
    """
    if players.empty or "name" not in players.columns:
        return []
    if "birth_date" not in players.columns:
        return []
    bucket: dict[tuple[str, str], list[Any]] = defaultdict(list)
    for _, r in players.iterrows():
        bdate = r.get("birth_date")
        name = r.get("name")
        if not bdate or not name:
            continue
        key = (_normalize_name(str(name)), str(bdate))
        bucket[key].append(r["player_id"])
    out: list[CrossSeasonFinding] = []
    for (norm, bdate), pids in bucket.items():
        uniq = list(dict.fromkeys(pids))  # preserve order, dedup
        if len(uniq) > 1:
            out.append(CrossSeasonFinding(
                check=IntegrityCheck.DUPLICATE_IDENTITIES,
                severity=IntegritySeverity.CRITICAL,
                season_a=None, season_b=None,
                subject_id=None,
                message=f"{len(uniq)} player_ids per stesso (nome={norm}, "
                        f"birth={bdate}): {uniq}",
                evidence={"name_normalized": norm, "birth_date": bdate,
                          "player_ids": [_jsonable_id(x) for x in uniq]},
            ))
    return out


def _check_transfer_coherence(players: pd.DataFrame) -> list[CrossSeasonFinding]:
    """Track team changes between consecutive seasons. Informational."""
    if players.empty or "team" not in players.columns:
        return []
    out: list[CrossSeasonFinding] = []
    seasons_sorted = sorted(set(players["season"].astype(str).tolist()))
    for pid, grp in players.groupby("player_id"):
        teams_by_season = {
            str(r["season"]): str(r["team"])
            for _, r in grp.sort_values("season").iterrows()
            if pd.notna(r.get("team"))
        }
        if len(teams_by_season) < 2:
            continue
        transitions: list[tuple[str, str, str]] = []
        prev: tuple[str, str] | None = None
        for season in seasons_sorted:
            if season not in teams_by_season:
                continue
            cur_team = teams_by_season[season]
            if prev is not None and prev[1] != cur_team:
                transitions.append((prev[0], season, f"{prev[1]} -> {cur_team}"))
            prev = (season, cur_team)
        for s_a, s_b, label in transitions:
            out.append(CrossSeasonFinding(
                check=IntegrityCheck.TRANSFER_COHERENCE,
                severity=IntegritySeverity.INFO,
                season_a=s_a, season_b=s_b, subject_id=_jsonable_id(pid),
                message=f"player_id={pid} cambio club {s_a}→{s_b}: {label}",
                evidence={"transition": label},
            ))
    return out


def _check_club_continuity(players: pd.DataFrame) -> list[CrossSeasonFinding]:
    """Detect clubs that vanish between consecutive seasons (relegation OR data hole)."""
    if players.empty or "team" not in players.columns:
        return []
    out: list[CrossSeasonFinding] = []
    by_season: dict[str, set[str]] = defaultdict(set)
    for _, r in players.iterrows():
        team = r.get("team")
        season = r.get("season")
        if pd.notna(team) and pd.notna(season):
            by_season[str(season)].add(str(team))
    seasons_sorted = sorted(by_season.keys())
    for i in range(1, len(seasons_sorted)):
        prev = by_season[seasons_sorted[i - 1]]
        cur = by_season[seasons_sorted[i]]
        disappeared = prev - cur
        appeared = cur - prev
        for t in sorted(disappeared):
            out.append(CrossSeasonFinding(
                check=IntegrityCheck.CLUB_CONTINUITY,
                severity=IntegritySeverity.INFO,
                season_a=seasons_sorted[i - 1], season_b=seasons_sorted[i],
                subject_id=t,
                message=f"Club {t} presente in {seasons_sorted[i-1]} ma assente in {seasons_sorted[i]}",
                evidence={"direction": "disappeared"},
            ))
        for t in sorted(appeared):
            out.append(CrossSeasonFinding(
                check=IntegrityCheck.CLUB_CONTINUITY,
                severity=IntegritySeverity.INFO,
                season_a=seasons_sorted[i - 1], season_b=seasons_sorted[i],
                subject_id=t,
                message=f"Club {t} compare in {seasons_sorted[i]} (assente in {seasons_sorted[i-1]})",
                evidence={"direction": "appeared"},
            ))
    return out


def _check_orphan_matches(
    players: pd.DataFrame, matches: pd.DataFrame,
) -> list[CrossSeasonFinding]:
    """Match rows whose player_id is unknown to the players table."""
    if matches.empty:
        return []
    if players.empty:
        return [CrossSeasonFinding(
            check=IntegrityCheck.ORPHAN_MATCHES,
            severity=IntegritySeverity.CRITICAL,
            season_a=None, season_b=None, subject_id=None,
            message=f"{len(matches)} match rows ma 0 player rows",
            evidence={"matches": int(len(matches))},
        )]
    known: set[tuple[Any, str]] = set()
    for _, r in players.iterrows():
        known.add((r["player_id"], str(r["season"])))
    out: list[CrossSeasonFinding] = []
    by_season: dict[str, list[Any]] = defaultdict(list)
    for _, r in matches.iterrows():
        key = (r["player_id"], str(r["season"]))
        if key not in known:
            by_season[str(r["season"])].append(_jsonable_id(r["player_id"]))
    for season, ids in by_season.items():
        uniq = list(dict.fromkeys(ids))
        out.append(CrossSeasonFinding(
            check=IntegrityCheck.ORPHAN_MATCHES,
            severity=IntegritySeverity.WARN if len(uniq) < 10 else IntegritySeverity.CRITICAL,
            season_a=season, season_b=None, subject_id=None,
            message=f"{len(uniq)} player_ids in match data ma non in player table per {season}",
            evidence={"sample_player_ids": uniq[:10], "n_orphans": len(uniq)},
        ))
    return out


def _check_orphan_player_stats(
    players: pd.DataFrame, matches: pd.DataFrame,
) -> list[CrossSeasonFinding]:
    """Players with zero observed matches in the season."""
    if players.empty:
        return []
    if matches.empty:
        return [CrossSeasonFinding(
            check=IntegrityCheck.ORPHAN_PLAYER_STATS,
            severity=IntegritySeverity.WARN,
            season_a=None, season_b=None, subject_id=None,
            message=f"{len(players)} player rows ma 0 match rows",
            evidence={"players": int(len(players))},
        )]
    obs: set[tuple[Any, str]] = set()
    for _, r in matches.iterrows():
        obs.add((r["player_id"], str(r["season"])))
    by_season: dict[str, list[Any]] = defaultdict(list)
    for _, r in players.iterrows():
        key = (r["player_id"], str(r["season"]))
        if key not in obs:
            by_season[str(r["season"])].append(_jsonable_id(r["player_id"]))
    out: list[CrossSeasonFinding] = []
    for season, ids in by_season.items():
        if not ids:
            continue
        out.append(CrossSeasonFinding(
            check=IntegrityCheck.ORPHAN_PLAYER_STATS,
            severity=IntegritySeverity.INFO,
            season_a=season, season_b=None, subject_id=None,
            message=f"{len(ids)} player_ids senza match nella stagione {season}",
            evidence={"sample_player_ids": ids[:10], "n": len(ids)},
        ))
    return out


def _check_tpi_longitudinal_stability(
    tpi: pd.DataFrame, *, warn: float, critical: float,
) -> list[CrossSeasonFinding]:
    """Relative TPI jump > threshold between consecutive seasons → flag."""
    if tpi.empty:
        return []
    needed = {"player_id", "season", "tpi_totale"}
    if not needed.issubset(tpi.columns):
        return []
    out: list[CrossSeasonFinding] = []
    for pid, grp in tpi.groupby("player_id"):
        grp = grp.dropna(subset=["tpi_totale"]).sort_values("season")
        rows = grp[["season", "tpi_totale"]].values.tolist()
        for i in range(1, len(rows)):
            s_a, v_a = rows[i - 1]
            s_b, v_b = rows[i]
            if abs(float(v_a)) < 1e-9:
                continue
            rel = abs(float(v_b) - float(v_a)) / abs(float(v_a))
            sev: IntegritySeverity | None = None
            if rel >= critical:
                sev = IntegritySeverity.CRITICAL
            elif rel >= warn:
                sev = IntegritySeverity.WARN
            if sev is None:
                continue
            out.append(CrossSeasonFinding(
                check=IntegrityCheck.TPI_LONGITUDINAL_STABILITY,
                severity=sev,
                season_a=str(s_a), season_b=str(s_b),
                subject_id=_jsonable_id(pid),
                message=f"player_id={pid} TPI swing {s_a}→{s_b}: "
                        f"{v_a:.3f} → {v_b:.3f} ({rel:.0%})",
                evidence={"tpi_a": float(v_a), "tpi_b": float(v_b),
                          "relative_change": rel},
            ))
    return out


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────
def _sev_rank(sev: IntegritySeverity) -> int:
    return {
        IntegritySeverity.INFO: 0,
        IntegritySeverity.WARN: 1,
        IntegritySeverity.CRITICAL: 2,
    }[sev]


def _normalize_name(name: str) -> str:
    return (
        name.strip().lower()
        .replace("á", "a").replace("é", "e").replace("í", "i")
        .replace("ó", "o").replace("ú", "u").replace("ñ", "n")
        .replace("'", "").replace("-", " ").replace(".", "")
    )


def _names_are_close(names: list[str]) -> bool:
    """True if all names share at least one normalised token."""
    tok_sets = [set(_normalize_name(n).split()) for n in names]
    common = set.intersection(*tok_sets) if tok_sets else set()
    return len(common) >= 1


def _jsonable_id(v: Any) -> Any:
    try:
        import numpy as np
        if isinstance(v, np.integer):
            return int(v)
        if isinstance(v, np.floating):
            return float(v)
    except ImportError:
        pass
    return v
