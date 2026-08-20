"""Toglie le righe partita che Understat non accredita a nessuno dei due.

L'ULTIMO RESIDUO (20/08/2026)
-----------------------------
Chiusi gli altri tre difetti — righe finite sull'omonimo, schede sdoppiate dai
cambi di maglia, partite mai importate — restavano 26 righe su tre giocatori:

    Federico Ceccherini   29 partite nel DB, 9 su Understat
    Matteo Lavelli         4 partite nel DB, 1 su Understat
    Pedro                 62 partite nel DB, 59 su Understat

Sono copie di righe di compagni di squadra: le 20 di Ceccherini ricalcano
Audero e Baschirotto, quelle di Pedro ricalcano Patric. Ma qui manca il vicino
in anagrafica — l'id accanto non c'entra niente — quindi `ripara_righe_omonimi`
non le vede: quella regola pretende che la riga risulti DEL VICINO, e un vicino
non c'e'.

PERCHE' "RIGA IDENTICA" NON BASTA, DI NUOVO
--------------------------------------------
Le 20 di Ceccherini sono tutte da 90 minuti e quasi tutte senza tiri: righe che
un difensore e un portiere qualsiasi producono uguali per pura coincidenza.
Cancellare per somiglianza sarebbe lo stesso errore che su Scuffet aveva fatto
scendere un portiere da 27 partite a 5.

La prova sta altrove, ed e' disponibile solo adesso: da quando non manca piu'
una riga a nessuno, il database e Understat combaciano giocatore per giocatore.
Se una riga avanza, non c'e' nessuno che la stia aspettando — e' in eccesso, e
non serve sapere da chi e' stata copiata per sapere che va tolta.

LA REGOLA
---------
Si cancella la riga della partita g del giocatore A se:

  1. A e' identificato per `understat_id`, oppure per nome se quel nome su
     Understat appartiene a una persona sola;
  2. Understat non accredita ad A la partita g;
  3. TEST DI ACCETTAZIONE: tolte quelle righe, le partite di A coincidono
     ESATTAMENTE con quelle di Understat. Qui la coincidenza esatta si puo'
     pretendere davvero — le partite mancanti sono gia' state importate, quindi
     non c'e' piu' un secondo difetto a sporcare il conto.

USO
    python ripara_righe_orfane.py            # mostra e basta
    python ripara_righe_orfane.py --esegui   # scrive davvero
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
from sqlalchemy import create_engine, text

import config
from ripara_righe_omonimi import carica_understat
from importa_partite_mancanti import riscontro

BACKUP = Path(r"C:\dev") / f"_backup_righe_orfane_{datetime.now():%Y%m%d_%H%M}"


def main() -> None:
    esegui = "--esegui" in sys.argv
    eng = create_engine(config.db_url())
    per_id, per_nome, id_per_nome = carica_understat()
    if not per_id:
        raise SystemExit("cache Understat vuota o assente")
    idx = {"id": per_id, "nome": per_nome, "amb": id_per_nome}

    with eng.connect() as cx:
        ana = pd.read_sql(text("SELECT id, TRIM(CONCAT_WS(' ',nome,cognome)) n, understat_id "
                               "FROM giocatori"), cx)
        rig = pd.read_sql(text(
            "SELECT gp.giocatore_id gid, gp.calendario_id cid, c.game_id_understat gu, gp.minuti "
            "FROM giocatore_partita gp JOIN calendario c ON c.id = gp.calendario_id "
            "WHERE gp.minuti > 0 AND c.game_id_understat IS NOT NULL"), cx)

    per_g = rig.groupby("gid").gu.apply(lambda s: set(int(x) for x in s)).to_dict()
    piano: list[tuple[int, str, list[int]]] = []
    saltati: list[str] = []

    for _, r in ana.iterrows():
        db = per_g.get(int(r.id))
        if not db:
            continue
        us, _pid, via = riscontro(r, idx)
        if not us:
            continue
        extra = db - set(us)
        if not extra:
            continue
        resta = db - extra
        if resta != set(us):
            saltati.append(f"{r.n}: tolte le {len(extra)} in piu' resterebbero "
                           f"{len(resta)} partite, Understat ne conta {len(us)}")
            continue
        cids = [int(x) for x in rig[(rig.gid == int(r.id)) & (rig.gu.isin(extra))].cid]
        piano.append((int(r.id), str(r.n), cids))
        print(f"  {str(r.n)[:30]:30} -{len(cids):3d} righe  (da {len(db)} a {len(us)})  {via}")

    tot = sum(len(c) for _, _, c in piano)
    print(f"\nda cancellare: {tot} righe su {len(piano)} giocatori")
    if saltati:
        print("NON TOCCATI:")
        for s in saltati[:15]:
            print(f"   {s}")

    if not esegui:
        print("\nAnteprima soltanto. Rilancia con --esegui per scrivere.")
        return
    if not piano:
        print("\nNiente da fare.")
        return

    BACKUP.mkdir(parents=True, exist_ok=True)
    with eng.begin() as cx:
        for gid, nome, cids in piano:
            df = pd.read_sql(text("SELECT * FROM giocatore_partita WHERE giocatore_id=:g "
                                  "AND calendario_id IN :c"),
                             cx, params={"g": gid, "c": tuple(cids)})
            df.to_csv(BACKUP / f"{gid}.csv", index=False, encoding="utf-8")
            cx.execute(text("DELETE FROM giocatore_partita WHERE giocatore_id=:g "
                            "AND calendario_id IN :c"), {"g": gid, "c": tuple(cids)})
            print(f"   {nome}: {len(cids)} righe cancellate")
    print(f"\nbackup -> {BACKUP}")


if __name__ == "__main__":
    main()
