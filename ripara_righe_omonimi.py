"""Toglie le righe partita finite sul giocatore sbagliato per omonimia di nome.

COSA HA TROVATO L'INDAGINE (20/08/2026)
---------------------------------------
Dieci record di `giocatori` contengono righe di `giocatore_partita` che
appartengono a un'altra persona. Le coppie condividono tutte il NOME DI
BATTESIMO:

    Federico Ravaglia   <- Federico Bernardeschi
    Simone Scuffet      <- Simone Canestrelli
    Matteo Guendouzi    <- Matteo Cancellieri
    Evan Ndicka         <- Evan Ferguson
    Lorenzo Venturino   <- Lorenzo Colombo
    Sebastiano Luperto  <- Sebastiano Esposito      ... e altre quattro

Da qui venivano le statistiche impossibili che l'audit segnalava da giorni: un
portiere con 1 gol e 66 minuti a partita (Ravaglia), un altro con 0.76 xG
(Scuffet). Non erano etichette sbagliate: erano le partite di un attaccante.

DUE REGOLE PRIMA DI QUESTA, E PERCHE' NON BASTAVANO
---------------------------------------------------
Cancellare tutte le righe identiche a quelle del vicino distrugge dati veri: un
portiere e un difensore che giocano entrambi 90 minuti senza gol producono
righe identiche PER COINCIDENZA. Applicata cosi', Scuffet scendeva da 27
partite a 5, quando Understat ne conta 21.

La seconda regola confrontava con Understat il MULTIINSIEME dei minuti e
cancellava le righe in eccesso, scegliendo fra le candidate solo quelle
duplicate. Otto coppie su dieci tornavano; Venturino e Luperto no, e il
conteggio non sapeva dire perche'. Il difetto era di fondo: sapeva QUANTE
righe c'erano di troppo, non QUALI, e soprattutto sommava in un unico numero
due difetti diversi.

Perche' i modi di sbagliare sono due, e vanno tenuti separati:

    righe DI TROPPO  -> partite di un altro finite qui. E' questo il bug.
    righe MANCANTI   -> partite che il DB non ha mai importato. Difetto
                        preesistente e diffuso (su un campione di 400
                        giocatori, 64 ne hanno qualcuna), che con l'omonimia
                        non c'entra e che questo script non tocca.

Un test di accettazione che pretenda "dopo la pulizia i conti devono combaciare
con Understat" mescola i due e si blocca sul secondo anche quando il primo e'
risolto. Era esattamente il caso di Venturino e Luperto: 25 e 8 righe altrui da
togliere, e in piu' 9 e 14 partite che al DB mancavano da prima.

LA REGOLA DI ADESSO: OGNI RIGA GIUDICATA DA SOLA
-------------------------------------------------
`calendario.game_id_understat` e `giocatori.understat_id` permettono di dire
riga per riga di chi e', senza passare per i conteggi. Una riga di A nella
partita g con m minuti si cancella solo se valgono tutte e tre:

  1. Understat NON accredita ad A la partita g con m minuti;
  2. Understat accredita al vicino B la partita g con m minuti esatti;
  3. nel DB esiste per B, nella stessa partita, una riga identica (minuti, gol,
     xg, xa) — la prova materiale della copia.

La 2 e la 3 sono ridondanti apposta: la 2 e' la fonte, la 3 e' l'impronta
lasciata dal duplicato. Una coincidenza che superi entrambe dovrebbe essere
fortunata due volte.

TEST DI ACCETTAZIONE
--------------------
Dopo la cancellazione ogni riga rimasta ad A dev'essere confermata da Understat
(partita presente, stessi minuti): zero righe inspiegate. NON si pretende che i
conti combacino — se ad A mancano partite quello e' l'altro difetto, e viene
stampato a parte invece di bloccare la riparazione.

Chi non ha un riscontro Understat utilizzabile non si tocca, e nemmeno chi ha
un nome ambiguo sulla fonte: meglio lasciare un dato sporco che cancellarne uno
buono.

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


def carica_understat() -> tuple[dict, dict, dict]:
    """Legge la cache una volta sola.

    Restituisce: minuti per player_id, minuti per nome normalizzato, e quanti
    player_id distinti stanno sotto ogni nome — serve a rifiutare i nomi
    ambigui, dove il ripiego sul nome mescolerebbe due persone e ricreerebbe
    da capo il bug che stiamo riparando.
    """
    per_id: dict[int, dict[int, int]] = collections.defaultdict(dict)
    per_nome: dict[str, dict[int, int]] = collections.defaultdict(dict)
    id_per_nome: dict[str, set] = collections.defaultdict(set)
    for mf in glob.glob(str(CACHE_US / "match_*.json")):
        try:
            g = int(Path(mf).stem.split("_")[1])
            md = json.load(open(mf, encoding="utf-8"))
        except (OSError, ValueError, IndexError):
            continue
        for lato in ("h", "a"):
            for _k, i in md.get("rosters", {}).get(lato, {}).items():
                pid = int(i.get("player_id") or 0)
                t = int(i.get("time") or 0)
                if not pid or t <= 0:
                    continue
                n = _nm(i.get("player"))
                per_id[pid][g] = t
                per_nome[n][g] = t
                id_per_nome[n].add(pid)
    return per_id, per_nome, id_per_nome


def partite_understat(riga, us) -> tuple[dict, str]:
    """Le partite Understat del giocatore: per understat_id, se manca per nome.

    Il nome vero sta nei primi due token: l'anagrafica ha il cognome doppio
    ("Federico Ravaglia Ravaglia"), residuo dello stesso import difettoso.
    """
    per_id, per_nome, id_per_nome = us
    uid = riga.understat_id
    if not pd.isna(uid) and int(uid) and per_id.get(int(uid)):
        return per_id[int(uid)], f"understat_id {int(uid)}"
    nome = _nm(" ".join(str(riga.n).split()[:2]))
    if len(id_per_nome.get(nome, ())) > 1:
        return {}, f"nome '{nome}' ambiguo sulla fonte"
    return per_nome.get(nome, {}), f"nome '{nome}'"


def righe_da_togliere(A: pd.DataFrame, B: pd.DataFrame,
                      usA: dict, usB: dict) -> list:
    """I calendario_id delle righe di A che risultano del vicino B.

    A e B sono indicizzati per game_id_understat: il conteggio non c'entra
    piu', ogni riga viene giudicata per conto suo.
    """
    fuori = []
    for g, r in A.iterrows():
        g, m = int(g), int(r.minuti)
        if usA.get(g) == m:                          # 1. non risulta sua?
            continue
        if usB.get(g) != m:                          # 2. risulta del vicino?
            continue
        if g not in B.index:                         # 3. c'e' il duplicato?
            continue
        if not A.loc[g][CAMPI].equals(B.loc[g][CAMPI]):
            continue
        fuori.append(int(r.cid))
    return fuori


def main() -> None:
    esegui = "--esegui" in sys.argv
    eng = create_engine(config.db_url())
    us = carica_understat()
    if not us[0]:
        raise SystemExit(f"cache Understat vuota o assente: {CACHE_US}")

    q_righe = text(
        "SELECT gp.giocatore_id gid, gp.calendario_id cid, c.game_id_understat gu, "
        "gp.minuti, gp.goal, COALESCE(gp.xg,0) xg, COALESCE(gp.xa,0) xa "
        "FROM giocatore_partita gp JOIN calendario c ON c.id = gp.calendario_id "
        "WHERE gp.giocatore_id IN (:a,:b) AND gp.minuti > 0 "
        "AND c.game_id_understat IS NOT NULL")
    q_nomi = text("SELECT id, TRIM(CONCAT_WS(' ',nome,cognome)) n, ruolo, understat_id "
                  "FROM giocatori WHERE id IN (:a,:b)")

    piano = []
    saltati = []
    mancanti = []

    print(f"{'giocatore':26}{'nel DB':>12}{'dopo':>12}{'Understat':>12}  togliere  esito")
    for a, b in COPPIE:
        with eng.connect() as cx:
            d = pd.read_sql(q_righe, cx, params={"a": a, "b": b})
            nomi = pd.read_sql(q_nomi, cx, params={"a": a, "b": b}).set_index("id")
        A = d[d.gid == a].set_index("gu")
        B = d[d.gid == b].set_index("gu")
        nome = " ".join(str(nomi.loc[a].n).split()[:2])
        usA, viaA = partite_understat(nomi.loc[a], us)
        usB, viaB = partite_understat(nomi.loc[b], us)
        if not usA or not usB:
            saltati.append(f"{nome}: nessun riscontro Understat ({viaA} / {viaB})")
            continue

        togliere = righe_da_togliere(A, B, usA, usB)
        resta = A[~A.cid.isin(togliere)]
        # accettazione: non deve restare nulla che Understat non confermi
        inspiegate = [int(g) for g, r in resta.iterrows() if usA.get(int(g)) != int(r.minuti)]
        ok = not inspiegate
        print(f"  {nome[:24]:24}{len(A):4d}p{int(A.minuti.sum()):6d}'"
              f"{len(resta):4d}p{int(resta.minuti.sum()):6d}'"
              f"{len(usA):4d}p{sum(usA.values()):6d}'"
              f"   {len(togliere):4d}     {'OK' if ok else 'NON TORNA'}")
        if not ok:
            saltati.append(f"{nome}: {len(inspiegate)} righe che Understat non conferma "
                           f"e che non risultano nemmeno del vicino")
            continue
        if togliere:
            piano.append((a, nome, togliere))
        assenti = len(set(usA) - set(int(g) for g in resta.index))
        if assenti:
            mancanti.append(f"{nome}: {assenti} partite che Understat ha e il DB no")

    tot = sum(len(t) for _, _, t in piano)
    print(f"\nda cancellare: {tot} righe su {len(piano)} giocatori")
    if saltati:
        print("NON TOCCATI (meglio lasciarli sporchi che cancellare a caso):")
        for r in saltati:
            print(f"   {r}")
    if mancanti:
        print("\nRIGHE MANCANTI — e' l'altro difetto, non l'omonimia: qui non si risolve.")
        for r in mancanti:
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
