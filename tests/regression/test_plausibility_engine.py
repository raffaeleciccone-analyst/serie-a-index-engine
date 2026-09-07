"""
Test sintetico del plausibility engine.
Inietta valori corrotti in una temp table e verifica che gli check li peschino.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "audit"))

import mysql.connector
from lib.db import DB_HOST, DB_USER, DB_PASSWORD, DB_NAME
from lib.findings import Report
from lib.checks_metric import (
    check_extreme_metrics_xg_p90,
    check_goals_vs_shots,
    check_minutes_range,
)
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parents[2] / "tests"))

# Questi test girano sui dati, non solo sul codice: senza, non c'e' niente
# da misurare, e fallire direbbe il falso. Si saltano dichiarando cosa
# manca — vedi regression/helpers/ambiente.py.
from regression.helpers.ambiente import senza_database  # noqa: E402

pytestmark = senza_database


def run_check_on_synthetic(checker, setup_sql: list[str], cleanup_sql: list[str]):
    """Setup → run check → cleanup, returns finding count."""
    db = mysql.connector.connect(host=DB_HOST, user=DB_USER, password=DB_PASSWORD,
                                 database=DB_NAME, autocommit=True)
    c = db.cursor()
    try:
        for s in setup_sql: c.execute(s)
        report = Report(db_name=DB_NAME)
        checker(report)
        n = len(report.findings)
    finally:
        for s in cleanup_sql: c.execute(s)
        c.close(); db.close()
    return n, report.findings


def test_xg_p90_extreme():
    # crea un giocatore fasullo con xG=15 in 90'
    n, findings = run_check_on_synthetic(
        check_extreme_metrics_xg_p90,
        setup_sql=[
            "INSERT INTO squadre (id, nome) VALUES (9999, '__test_team__') "
            "ON DUPLICATE KEY UPDATE nome=VALUES(nome)",
            "INSERT INTO giocatori (id, nome, squadra_id, ruolo, minuti, xg) "
            "VALUES (999999, '__plausibility_test__', 9999, 'ATT', 500, 100) "
            "ON DUPLICATE KEY UPDATE minuti=VALUES(minuti), xg=VALUES(xg)",
        ],
        cleanup_sql=[
            "DELETE FROM giocatori WHERE id=999999",
            "DELETE FROM squadre WHERE id=9999",
        ],
    )
    assert n >= 1, "Plausibility engine non ha rilevato xG/90=15 (HIGH)"
    print(f"✓ xG/90 extreme: {n} finding(s)")


def test_goals_gt_shots():
    n, _ = run_check_on_synthetic(
        check_goals_vs_shots,
        setup_sql=[
            "INSERT INTO squadre (id, nome) VALUES (9999, '__test_team__') "
            "ON DUPLICATE KEY UPDATE nome=VALUES(nome)",
            "INSERT INTO giocatori (id, nome, squadra_id, ruolo, minuti, goal, tiri) "
            "VALUES (999998, '__shots_test__', 9999, 'ATT', 500, 10, 5) "
            "ON DUPLICATE KEY UPDATE goal=VALUES(goal), tiri=VALUES(tiri)",
        ],
        cleanup_sql=[
            "DELETE FROM giocatori WHERE id=999998",
            "DELETE FROM squadre WHERE id=9999",
        ],
    )
    assert n >= 1, "Plausibility engine non ha rilevato goal>tiri (CRITICAL)"
    print(f"✓ goals>shots: {n} finding(s)")


def test_minutes_out_of_range():
    n, _ = run_check_on_synthetic(
        check_minutes_range,
        setup_sql=[
            "INSERT INTO squadre (id, nome) VALUES (9999, '__test_team__') "
            "ON DUPLICATE KEY UPDATE nome=VALUES(nome)",
            "INSERT INTO giocatori (id, nome, squadra_id, ruolo, minuti) "
            "VALUES (999997, '__minutes_test__', 9999, 'CEN', 9999) "
            "ON DUPLICATE KEY UPDATE minuti=VALUES(minuti)",
        ],
        cleanup_sql=[
            "DELETE FROM giocatori WHERE id=999997",
            "DELETE FROM squadre WHERE id=9999",
        ],
    )
    assert n >= 1, "Plausibility engine non ha rilevato minuti=9999 (HIGH)"
    print(f"✓ minuti out-of-range: {n} finding(s)")


if __name__ == "__main__":
    test_xg_p90_extreme()
    test_goals_gt_shots()
    test_minutes_out_of_range()
    print("\nTutti i test plausibility passati.")
