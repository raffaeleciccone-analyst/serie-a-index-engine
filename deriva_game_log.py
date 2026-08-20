"""Ricostruisce `t_squadra_game_log` da dati che il database ha gia'.

COS'E' QUELLA TABELLA
---------------------
Non e' una tabella importata: e' una **vista materializzata** di
`squadra_calendario` piu' il nome squadra e l'avversario presi da `calendario`.
Nessuna fonte esterna, nessuno scraping. La riga per squadra di ogni partita,
con xG fatti e subiti — ed e' da lei che il motore ricava le "difese solide",
uno dei cinque contesti del TPI.

IL DIFETTO (trovato il 20/08/2026)
-----------------------------------
Per il 2025-26 conteneva 560 righe su 760: mancavano del tutto le giornate
31-38 e diverse delle presenti avevano 12-18 righe invece di 20. Non perche' i
dati non ci fossero — `calendario` ha tutte e 380 le partite senza un gol ne'
un xG mancante, `squadra_calendario` tutte e 760 le righe — ma perche' la
derivazione viveva dentro `backfill_24_25_completo.py`, con la stagione scritta
come costante, e su questa stagione non era mai stata rilanciata. Le ultime
otto giornate sono arrivate dopo l'ultima esecuzione.

Da li' veniva anche la pezza `estrai_xg_concessi_hexi.py`: il suo confronto
("il Bologna entra fra le sei piu' solide e il Milan esce") metteva la nostra
tabella incompleta contro quella completa di Sportmonks, e attribuiva alla
fonte una differenza che era solo un quarto di stagione mancante.

Sta qui e non li' perche' una derivazione non e' un backfill: va rifatta ogni
volta che arrivano giornate nuove, non una volta sola in fondo a una
migrazione. E prende la stagione come argomento, per lo stesso motivo per cui
l'ha presa tutto il resto oggi.

E' un UPSERT sulla chiave unica (squadra_id, calendario_id): rilanciarlo non
duplica niente, aggiorna e basta.

USO
    python deriva_game_log.py                      # anteprima, stagione corrente
    python deriva_game_log.py --season 2024-25     # un'altra stagione
    python deriva_game_log.py --esegui             # scrive davvero
"""
from __future__ import annotations

import argparse
import logging

import pandas as pd
from sqlalchemy import create_engine, text

import config

log = logging.getLogger("deriva_game_log")

SQL = """
INSERT INTO t_squadra_game_log
  (squadra_id, squadra, calendario_id, ruolo, goal_fatti, goal_subiti,
   xg, xg_subiti, risultato, punti, squadra_casa_id, squadra_trasferta_id,
   xg_casa, xg_trasferta, avversario_id, xg_avversario, season)
SELECT
  sc.squadra_id, sq.nome, sc.calendario_id, sc.ruolo,
  sc.goal_fatti, sc.goal_subiti, sc.xg, sc.xg_subiti, sc.risultato, sc.punti,
  c.squadra_casa_id, c.squadra_trasferta_id, c.xg_casa, c.xg_trasferta,
  CASE WHEN sc.ruolo = 'casa' THEN c.squadra_trasferta_id ELSE c.squadra_casa_id END,
  CASE WHEN sc.ruolo = 'casa' THEN c.xg_trasferta ELSE c.xg_casa END,
  c.season
FROM squadra_calendario sc
JOIN calendario c ON c.id = sc.calendario_id
JOIN squadre sq   ON sq.id = sc.squadra_id
WHERE c.season = :s
ON DUPLICATE KEY UPDATE
  squadra = VALUES(squadra),
  goal_fatti = VALUES(goal_fatti), goal_subiti = VALUES(goal_subiti),
  xg = VALUES(xg), xg_subiti = VALUES(xg_subiti),
  risultato = VALUES(risultato), punti = VALUES(punti),
  squadra_casa_id = VALUES(squadra_casa_id),
  squadra_trasferta_id = VALUES(squadra_trasferta_id),
  xg_casa = VALUES(xg_casa), xg_trasferta = VALUES(xg_trasferta),
  avversario_id = VALUES(avversario_id), xg_avversario = VALUES(xg_avversario),
  season = VALUES(season)
"""


def difese_solide(cx, season: str, n: int = 8) -> pd.DataFrame:
    """Le squadre che concedono meno xG per partita — il contesto in gioco."""
    return pd.read_sql(text(
        "SELECT squadra, ROUND(AVG(xg_avversario), 3) xg_concessi, COUNT(*) partite "
        "FROM t_squadra_game_log WHERE season = :s "
        "GROUP BY squadra ORDER BY xg_concessi LIMIT :n"),
        cx, params={"s": season, "n": n})


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--season", default=config.SEASON_CORRENTE)
    ap.add_argument("--esegui", action="store_true")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    eng = create_engine(config.db_url())
    with eng.connect() as cx:
        attese = pd.read_sql(text(
            "SELECT COUNT(*) n FROM squadra_calendario sc JOIN calendario c "
            "ON c.id = sc.calendario_id WHERE c.season = :s"), cx, params={"s": a.season}).n.iloc[0]
        presenti = pd.read_sql(text(
            "SELECT COUNT(*) n FROM t_squadra_game_log WHERE season = :s"),
            cx, params={"s": a.season}).n.iloc[0]
        print(f"stagione {a.season}: {presenti} righe presenti, {attese} derivabili "
              f"({attese - presenti} mancanti)")
        if presenti:
            print("\ndifese piu' solide ORA (dati parziali se ne mancano):")
            print(difese_solide(cx, a.season).to_string(index=False))

    if not a.esegui:
        print("\nAnteprima soltanto. Rilancia con --esegui per scrivere.")
        return

    with eng.begin() as cx:
        n = cx.execute(text(SQL), {"s": a.season}).rowcount
    print(f"\n{n} righe inserite o aggiornate.")
    with eng.connect() as cx:
        dopo = pd.read_sql(text("SELECT COUNT(*) n FROM t_squadra_game_log WHERE season = :s"),
                           cx, params={"s": a.season}).n.iloc[0]
        print(f"ora sono {dopo} su {attese} derivabili")
        print("\ndifese piu' solide DOPO:")
        print(difese_solide(cx, a.season).to_string(index=False))
    print("\nLe difese solide sono un contesto del TPI: rigenera i payload.")


if __name__ == "__main__":
    main()
