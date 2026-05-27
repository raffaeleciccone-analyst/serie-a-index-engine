"""
checks_metric.py — Plausibility engine.

Verifica che i KPI principali restino entro range fisicamente sensati.
Sentinella anti-corruzione: se viene sovrascritto un campo (es. SUM
duplicata) un valore esce dal range e l'audit lo segnala come HIGH.

Convenzione codici:
  MET-001  xG/90 fuori range
  MET-002  xA/90 fuori range
  MET-003  Goals/90 fuori range
  MET-004  Goals > Tiri (logica impossibile)
  MET-005  Minuti negativi o > 36*90+slack
  MET-006  Conversion ratio (G/xG) estremo
  MET-007  Boost ratio fuori da [0.3, 4.0]
  MET-008  Età < 15 o > 45
  MET-009  Calo brusco partite: titolari con minuti/giornate < 30% media
  MET-010  Drift media squadra > 3σ vs storico

Tutti i range sono pubblicati come costanti per essere tunable e testabili.
"""
from __future__ import annotations
from .db import fetch_all
from .findings import Report, Finding, Severity, Area


# ── Range fisici plausibili (Serie A 2025/26) ─────────────────────────────────
KPI_RANGES = {
    "xg_per_90":    (0.0, 1.8),    # i top scorer storici stanno tra 0.7 e 1.5
    "xa_per_90":    (0.0, 1.0),
    "goal_per_90":  (0.0, 2.0),
    "assist_per_90":(0.0, 1.0),
    "tiri_per_90":  (0.0, 7.0),
    "conv_ratio":   (0.0, 5.0),    # G/xG, oltre 5 è quasi sempre dato sporco
    "boost_ratio":  (0.3, 4.0),
    "eta":          (15, 45),
    # 38 giornate × 95' (overtime in tutte) = 3610. 4000 lascia slack senza
    # nascondere errori veri di duplicazione (es. 7200 = doppio totale).
    "minuti":       (0, 4000),
    # xG squadra: il massimo storico Serie A è ~5; permettiamo fino a 7
    "xg_squadra":   (0.0, 7.0),
}


def check_extreme_metrics_xg_p90(report: Report) -> None:
    """MET-001 xG/90 fuori range plausibile."""
    lo, hi = KPI_RANGES["xg_per_90"]
    rows = fetch_all(f"""
        SELECT id, nome, minuti, xg, ROUND(xg/(minuti/90.0), 3) AS xg_p90
        FROM giocatori WHERE minuti >= 300
          AND (xg < 0 OR xg/(minuti/90.0) NOT BETWEEN {lo} AND {hi})
    """)
    if rows:
        report.add(Finding(
            code="MET-001", area=Area.METRIC, severity=Severity.HIGH,
            title=f"{len(rows)} giocatori con xG/90 fuori [{lo},{hi}]",
            table="giocatori", rows_affected=len(rows),
            description="xG/90 fuori dal range fisico atteso per Serie A.",
            root_cause="SUM duplicata, errata aggregazione, o bug di calcolo.",
            fix_strategy="UPDATE giocatori SET xg=(SELECT SUM(xg) FROM giocatore_partita WHERE giocatore_id=g.id)",
            samples=[{"id": r[0], "nome": r[1], "xg_p90": float(r[4] or 0)} for r in rows[:5]],
        ))


def check_extreme_metrics_xa_p90(report: Report) -> None:
    """MET-002 xA/90 fuori range."""
    lo, hi = KPI_RANGES["xa_per_90"]
    rows = fetch_all(f"""
        SELECT id, nome, ROUND(xa/(minuti/90.0), 3) AS p
        FROM giocatori WHERE minuti >= 300 AND (xa/(minuti/90.0) NOT BETWEEN {lo} AND {hi})
    """)
    if rows:
        report.add(Finding(
            code="MET-002", area=Area.METRIC, severity=Severity.MEDIUM,
            title=f"{len(rows)} giocatori con xA/90 fuori [{lo},{hi}]",
            table="giocatori", rows_affected=len(rows),
            samples=[{"id": r[0], "nome": r[1], "xa_p90": float(r[2])} for r in rows[:5]],
        ))


def check_extreme_metrics_goal_p90(report: Report) -> None:
    """MET-003 Goals/90 fuori range."""
    lo, hi = KPI_RANGES["goal_per_90"]
    rows = fetch_all(f"""
        SELECT id, nome, goal, minuti, ROUND(goal/(minuti/90.0), 3) AS p
        FROM giocatori WHERE minuti >= 300 AND (goal/(minuti/90.0) NOT BETWEEN {lo} AND {hi})
    """)
    if rows:
        report.add(Finding(
            code="MET-003", area=Area.METRIC, severity=Severity.HIGH,
            title=f"{len(rows)} giocatori con Goal/90 fuori [{lo},{hi}]",
            table="giocatori", rows_affected=len(rows),
            samples=[{"id": r[0], "nome": r[1], "goal": r[2], "g_p90": float(r[4])} for r in rows[:5]],
        ))


def check_goals_vs_shots(report: Report) -> None:
    """MET-004 Goals > Tiri (impossibile)."""
    rows = fetch_all("""
        SELECT id, nome, goal, tiri FROM giocatori WHERE goal > tiri AND tiri > 0
    """)
    if rows:
        report.add(Finding(
            code="MET-004", area=Area.METRIC, severity=Severity.CRITICAL,
            title=f"{len(rows)} giocatori con goal > tiri (impossibile)",
            table="giocatori", rows_affected=len(rows),
            description="Violazione logica: ogni goal richiede almeno un tiro.",
            root_cause="Probabile bug di mappatura colonne Understat.",
            samples=[{"id": r[0], "nome": r[1], "goal": r[2], "tiri": r[3]} for r in rows[:5]],
        ))


def check_minutes_range(report: Report) -> None:
    """MET-005 Minuti fuori range fisico."""
    lo, hi = KPI_RANGES["minuti"]
    rows = fetch_all(f"""
        SELECT id, nome, minuti FROM giocatori
        WHERE minuti < {lo} OR minuti > {hi}
    """)
    if rows:
        report.add(Finding(
            code="MET-005", area=Area.METRIC, severity=Severity.HIGH,
            title=f"{len(rows)} giocatori con minuti fuori [{lo},{hi}]",
            table="giocatori", rows_affected=len(rows),
            samples=[{"id": r[0], "nome": r[1], "minuti": r[2]} for r in rows[:5]],
        ))


def check_conversion_ratio(report: Report) -> None:
    """MET-006 Conversion ratio G/xG estremo (>5 quasi sempre = dato sporco)."""
    lo, hi = KPI_RANGES["conv_ratio"]
    rows = fetch_all(f"""
        SELECT id, nome, goal, xg, ROUND(goal/NULLIF(xg,0), 2) AS r
        FROM giocatori WHERE minuti >= 500 AND xg > 1
              AND (goal/xg NOT BETWEEN {lo} AND {hi})
    """)
    if rows:
        report.add(Finding(
            code="MET-006", area=Area.METRIC, severity=Severity.MEDIUM,
            title=f"{len(rows)} giocatori con G/xG estremo (≥{hi} o <{lo})",
            table="giocatori", rows_affected=len(rows),
            description="Conversion ratio estremo: possibili dati corrotti o casi davvero outlier.",
            samples=[{"id": r[0], "nome": r[1], "G": r[2], "xG": float(r[3]),
                      "ratio": float(r[4] or 0)} for r in rows[:5]],
        ))


def check_age_range(report: Report) -> None:
    """MET-008 Età fuori [15, 45] (Serie A non ha extrabambini né nonni)."""
    lo, hi = KPI_RANGES["eta"]
    rows = fetch_all(f"""
        SELECT id, nome, data_nascita,
               TIMESTAMPDIFF(YEAR, data_nascita, CURDATE()) AS eta
        FROM giocatori
        WHERE data_nascita IS NOT NULL AND (
              TIMESTAMPDIFF(YEAR, data_nascita, CURDATE()) NOT BETWEEN {lo} AND {hi})
    """)
    if rows:
        report.add(Finding(
            code="MET-008", area=Area.METRIC, severity=Severity.MEDIUM,
            title=f"{len(rows)} giocatori con età fuori [{lo},{hi}]",
            table="giocatori", rows_affected=len(rows),
            samples=[{"id": r[0], "nome": r[1], "data": str(r[2]), "eta": r[3]} for r in rows[:5]],
        ))


def check_calendario_xg_squadra(report: Report) -> None:
    """MET-010 xG squadra per partita fuori range plausibile."""
    lo, hi = KPI_RANGES["xg_squadra"]
    rows = fetch_all(f"""
        SELECT id, giornata, squadra_casa, squadra_trasferta, xg_casa, xg_trasferta
        FROM calendario
        WHERE xg_casa > {hi} OR xg_trasferta > {hi} OR xg_casa < {lo} OR xg_trasferta < {lo}
    """)
    if rows:
        report.add(Finding(
            code="MET-010", area=Area.METRIC, severity=Severity.MEDIUM,
            title=f"{len(rows)} partite con xG squadra fuori range",
            table="calendario", rows_affected=len(rows),
            samples=[{"id": r[0], "giornata": r[1], "casa": r[2], "trasf": r[3],
                      "xg_c": float(r[4]), "xg_t": float(r[5])} for r in rows[:5]],
        ))


# Registry dei plausibility check (registrabili in lib/checks.py CHECKS)
METRIC_CHECKS = [
    check_extreme_metrics_xg_p90,
    check_extreme_metrics_xa_p90,
    check_extreme_metrics_goal_p90,
    check_goals_vs_shots,
    check_minutes_range,
    check_conversion_ratio,
    check_age_range,
    check_calendario_xg_squadra,
]
