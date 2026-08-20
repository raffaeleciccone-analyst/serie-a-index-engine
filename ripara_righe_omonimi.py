"""Toglie le righe partita finite sul giocatore sbagliato per omonimia di nome.

COSA HA TROVATO L'INDAGINE (20/08/2026)
---------------------------------------
Dieci record di `giocatori` contengono righe di `giocatore_partita` che
appartengono a un'altra persona. Le coppie condividono tutte il NOME DI
BATTESIMO:

    Federico Ravaglia  <- Federico Bernardeschi
    Simone Scuffet     <- Simone Canestrelli
    Matteo Guendouzi   <- Matteo Cancellieri
    Evan Ndicka        <- Evan Ferguson          ... e altre sei

Da qui venivano le statistiche impossibili che l'audit segnalava da giorni: un
portiere con 1 gol e 66 minuti a partita (Ravaglia), un altro con 0.76 xG
(Scuffet). Non erano etichette sbagliate: erano le partite di un attaccante.

LA REGOLA, E PERCHE' NON BASTA "LE RIGHE UGUALI"
------------------------------------------------
Cancellare tutte le righe identiche a quelle del vicino distrugge dati veri:
un portiere e un difensore che giocano entrambi 90 minuti senza gol producono
righe identiche PER COINCIDENZA. Applicata cosi', Scuffet scendeva da 27
partite a 5, quando Understat ne conta 21.

Quindi la verita' la dice Understat, che e' la fonte da cui quei dati vengono:

  1. per ogni giocatore si prende da Understat il multiinsieme dei minuti
     giocati (una voce per partita);
  2. si confronta col multiinsieme del database: le righe IN ECCESSO sono
     quelle da togliere;
  3. fra le candidate si cancellano solo quelle identiche alla riga del vicino
     nella stessa partita — cioe' quelle per cui esiste anche la prova del
     duplicato, non solo il conteggio.

TEST DI ACCETTAZIONE
--------------------
Dopo la cancellazione, partite e minuti del giocatore devono coincidere con
Understat. Se una coppia non torna, quella coppia NON si tocca e viene
segnalata: meglio lasciare un dato sporco che cancellarne uno buono.

USO
    python ripara_righe_omonimi.py            # mostra e basta
    python ripara_righe_omonimi.py --esegui   # scrive davvero
"""
from __future__ import annotations

import collections
import glob
import html
import json
import os
import sys
import unicodedata
from datetime import datetime
from pathlib import Path

import pandas as pd
from sqlalchemy import create_engine, text

import config

BACKUP = Path(r"C:\dev") / f"_backup_righe_omonimi_{datetime.now():%Y%m%d_%H%M}"
CACHE_US = Path(os.path.expanduser("~/soccerdata")) / "data" / "Understat"

# (chi ha ricevuto le righe, da chi) — trovate cercando i record con >=5 righe
# identiche a quelle dell'id precedente. Gli id sono consecutivi perche'
# l'anagrafica e' ordinata per nome, non per un difetto di numerazione.
COPPIE = [(4113, 4112), (4332, 4331), (3961, 3960), (3975, 3974), (4448, 4447),
          (4194, 4193), (4374, 4373), (4324, 4323), (4009, 4008), (4337, 4336)]

CAMPI = ["minuti", "goal", "xg", "xa"]


def _nm(s) -> str:
    return (unicodedata.normalize("NFKD", html.unescape(str(s or "")))
            .encode("ascii", "ignore").decode().lower().strip())


def minuti_understat() -> dict[str, list[int]]:
    """nome normalizzato -> lista dei minuti giocati, una voce per partita."""
    fuori: dict[str, list[int]] = collections.defaultdict(list)
    for mf in glob.glob(str(CACHE_US / "match_*.json")):
        try:
            md = json.load(open(mf, encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for lato in ("h", "a"):
            for _pid, info in md.get("rosters", {}).get(lato, {}).items():
                t = int(info.get("time", 0) or 0)
                if t > 0:
                    fuori[_nm(info.get("player"))].append(t)
    return fuori


def righe_da_togliere(df_a: pd.DataFrame, df_b: pd.DataFrame,
                      minuti_veri: list[int]) -> list[int]:
    """Le righe di A che il database ha in piu' rispetto a Understat.

    Si tolgono solo dove c'e' anche la prova del duplicato (riga identica a
    quella del vicino nella stessa partita): il conteggio dice QUANTE, il
    duplicato dice QUALI.
    """
    condivise = set(df_a.index) & set(df_b.index)
    candidate = [c for c in condivise if df_a.loc[c][CAMPI].equals(df_b.loc[c][CAMPI])]

    ecc = collections.Counter(int(m) for m in df_a["minuti"])
    ecc.subtract(collections.Counter(int(m) for m in minuti_veri))

    da_togliere: list[int] = []
    # prima i minutaggi piu' in eccesso: se una riga da 90' e' in eccesso di 1 e
    # ce ne sono tre candidate, se ne toglie una sola.
    for cid in sorted(candidate, key=lambda c: -ecc[int(df_a.loc[c]["minuti"])]):
        m = int(df_a.loc[cid]["minuti"])
        if ecc[m] > 0:
            da_togliere.append(cid)
            ecc[m] -= 1
    return da_togliere


def main() -> None:
    esegui = "--esegui" in sys.argv
    eng = create_engine(config.db_url())
    us = minuti_understat()
    if not us:
        raise SystemExit(f"cache Understat vuota o assente: {CACHE_US}")

    q_righe = text("SELECT gp.giocatore_id gid, gp.calendario_id cid, gp.minuti, gp.goal, "
                   "COALESCE(gp.xg,0) xg, COALESCE(gp.xa,0) xa "
                   "FROM giocatore_partita gp WHERE gp.giocatore_id IN (:a,:b) AND gp.minuti>0")
    q_nomi = text("SELECT id, TRIM(CONCAT_WS(' ',nome,cognome)) n, ruolo FROM giocatori "
                  "WHERE id IN (:a,:b)")

    piano: list[tuple[int, str, list[int]]] = []
    non_torna: list[str] = []

    print(f"{'giocatore':24} {'nel DB':>14} {'dopo':>14} {'Understat':>14}   esito")
    for a, b in COPPIE:
        with eng.connect() as cx:
            d = pd.read_sql(q_righe, cx, params={"a": a, "b": b})
            nomi = pd.read_sql(q_nomi, cx, params={"a": a, "b": b}).set_index("id")
        A = d[d.gid == a].set_index("cid")
        B = d[d.gid == b].set_index("cid")
        # il nome vero e' i primi due token: l'anagrafica ha il cognome doppio
        # ("Federico Ravaglia Ravaglia"), residuo dello stesso import difettoso
        nome = " ".join(str(nomi.loc[a].n).split()[:2])
        veri = us.get(_nm(nome), [])
        if not veri:
            non_torna.append(f"{nome}: non e' in cache Understat")
            continue

        togliere = righe_da_togliere(A, B, veri)
        resta = A.drop(index=togliere, errors="ignore")
        ok = len(resta) == len(veri) and abs(resta["minuti"].sum() - sum(veri)) < 2
        print(f"  {nome[:22]:22} {len(A):3d}p {int(A.minuti.sum()):5d}'"
              f" {len(resta):3d}p {int(resta.minuti.sum()):5d}'"
              f" {len(veri):3d}p {sum(veri):5d}'   {'OK' if ok else 'NON TORNA'}")
        if ok and togliere:
            piano.append((a, nome, togliere))
        elif not ok:
            non_torna.append(f"{nome}: dopo la pulizia farebbe {len(resta)}p/"
                             f"{int(resta.minuti.sum())}', Understat dice "
                             f"{len(veri)}p/{sum(veri)}'")

    tot = sum(len(t) for _, _, t in piano)
    print(f"\nda cancellare: {tot} righe su {len(piano)} giocatori")
    if non_torna:
        print("NON TOCCATI (il conto non torna, meglio lasciarli sporchi):")
        for r in non_torna:
            print(f"   {r}")

    if not esegui:
        print("\nAnteprima soltanto. Rilancia con --esegui per scrivere.")
        return
    if not piano:
        print("\nNiente da fare.")
        return

    BACKUP.mkdir(parents=True, exist_ok=True)
    with eng.begin() as cx:
        for gid, nome, cids in piano:
            df = pd.read_sql(
                text("SELECT * FROM giocatore_partita WHERE giocatore_id=:g "
                     "AND calendario_id IN :c"),
                cx, params={"g": gid, "c": tuple(cids)})
            df.to_csv(BACKUP / f"{gid}_{_nm(nome).replace(' ','_')}.csv",
                      index=False, encoding="utf-8")
            cx.execute(text("DELETE FROM giocatore_partita WHERE giocatore_id=:g "
                            "AND calendario_id IN :c"),
                       {"g": gid, "c": tuple(cids)})
            print(f"   {nome}: {len(cids)} righe cancellate")
    print(f"\nbackup delle righe rimosse -> {BACKUP}")
    print("Ora rigenera il payload: le classifiche cambiano.")


if __name__ == "__main__":
    main()
