"""Rimette in `giocatore_partita` le partite che Understat ha e il database no.

IL TERZO DIFETTO
----------------
Dopo aver tolto le righe finite sul giocatore sbagliato (ripara_righe_omonimi)
e dopo aver riunito le schede sdoppiate dai cambi di maglia
(unisci_record_doppioni), resta un buco vero: circa 650 partite che Understat
attribuisce a un giocatore e che nel database non ci sono affatto. Non sono
finite altrove — sono assenti.

Colpisce una trentina di giocatori, e non a caso: sono quasi tutti gente che ha
cambiato squadra a stagione in corso, cioe' lo stesso terreno da cui nascevano
gli altri due difetti.

COSA FA
-------
Per ogni giocatore confronta le sue partite col riscontro Understat e inserisce
quelle che mancano, leggendo i valori dalla cache in ~/soccerdata: minuti, gol,
assist, tiri, xG, xA, cartellini, xGChain, xGBuildup. npxG e npg si ricavano
togliendo i rigori, presi dai tiri con situation == 'Penalty' — la stessa
regola di parte4_aggiorna.py, non una seconda versione che potrebbe divergere.

Il lato casa/trasferta non viene indovinato: lo dice `h_a` nel roster di
Understat, che e' la stessa fonte della riga.

LE REGOLE PER NON RIFARE I DANNI DI PRIMA
-----------------------------------------
  1. si scrive solo su giocatori identificati per `understat_id`, oppure per
     nome se e solo se quel nome su Understat appartiene a UNA persona sola.
     Il bug che abbiamo appena finito di riparare nasceva esattamente
     dall'abbinamento per nome fatto senza questo controllo;
  2. mai due righe per la stessa coppia (giocatore, partita): se una riga
     c'e' gia', anche con numeri diversi, non si tocca — questo script
     aggiunge cio' che manca, non corregge cio' che c'e';
  3. la partita dev'essere in `calendario` con quel `game_id_understat`;
  4. TEST DI ACCETTAZIONE, giocatore per giocatore: dopo l'inserimento le sue
     partite devono coincidere ESATTAMENTE con quelle di Understat. Se un
     giocatore non tornerebbe, le sue righe non si scrivono e viene segnalato.

USO
    python importa_partite_mancanti.py            # mostra e basta
    python importa_partite_mancanti.py --esegui   # scrive davvero
"""
from __future__ import annotations

import collections
import glob
import json
import os
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
from sqlalchemy import create_engine, text

import config
from ripara_righe_omonimi import _nm
from unisci_record_doppioni import chiave_nome

CACHE_US = Path(os.path.expanduser("~/soccerdata")) / "data" / "Understat"
BACKUP = Path(r"C:\dev") / f"_backup_import_mancanti_{datetime.now():%Y%m%d_%H%M}"


def leggi_cache() -> tuple[dict, dict, dict, dict]:
    """roster per (partita, player_id), indice per giocatore, nomi, rigori."""
    roster: dict[tuple[int, int], dict] = {}
    per_id: dict[int, set] = collections.defaultdict(set)
    per_nome: dict[str, set] = collections.defaultdict(set)
    id_per_nome: dict[str, set] = collections.defaultdict(set)
    rigori: dict[tuple[int, str], list] = {}
    for mf in glob.glob(str(CACHE_US / "match_*.json")):
        try:
            g = int(Path(mf).stem.split("_")[1])
            md = json.load(open(mf, encoding="utf-8"))
        except (OSError, ValueError, IndexError):
            continue
        for lato in ("h", "a"):
            for _k, i in md.get("rosters", {}).get(lato, {}).items():
                pid = int(i.get("player_id") or 0)
                if not pid or int(i.get("time") or 0) <= 0:
                    continue
                n = _nm(i.get("player"))
                roster[(g, pid)] = i
                per_id[pid].add(g)
                per_nome[n].add(g)
                id_per_nome[n].add(pid)
            for s in md.get("shots", {}).get(lato, []):
                if s.get("situation") != "Penalty":
                    continue
                k = (g, _nm(s.get("player")))
                e = rigori.setdefault(k, [0.0, 0])
                e[0] += float(s.get("xG", 0) or 0)
                if s.get("result") == "Goal":
                    e[1] += 1
    return roster, {"id": per_id, "nome": per_nome, "amb": id_per_nome}, roster, rigori


def riscontro(riga, idx) -> tuple[set, int | None, str]:
    """Le partite Understat del giocatore, e con quale player_id."""
    uid = riga.understat_id
    if not pd.isna(uid) and int(uid) and idx["id"].get(int(uid)):
        return idx["id"][int(uid)], int(uid), f"understat_id {int(uid)}"
    n = chiave_nome(riga.n)
    pids = idx["amb"].get(n, set())
    if len(pids) != 1:
        return set(), None, (f"nome '{n}' " +
                             ("ambiguo su Understat" if pids else "assente da Understat"))
    return idx["nome"][n], next(iter(pids)), f"nome '{n}'"


def main() -> None:
    esegui = "--esegui" in sys.argv
    eng = create_engine(config.db_url())
    roster, idx, _r2, rigori = leggi_cache()
    if not roster:
        raise SystemExit(f"cache Understat vuota o assente: {CACHE_US}")

    with eng.connect() as cx:
        ana = pd.read_sql(text("SELECT id, TRIM(CONCAT_WS(' ',nome,cognome)) n, understat_id "
                               "FROM giocatori"), cx)
        rig = pd.read_sql(text(
            "SELECT gp.giocatore_id gid, c.game_id_understat gu FROM giocatore_partita gp "
            "JOIN calendario c ON c.id = gp.calendario_id "
            "WHERE c.game_id_understat IS NOT NULL"), cx)
        cal = pd.read_sql(text("SELECT id, game_id_understat gu, season FROM calendario "
                               "WHERE game_id_understat IS NOT NULL"), cx)

    cal_id = dict(zip(cal.gu.astype(int), cal.id))
    cal_st = dict(zip(cal.gu.astype(int), cal.season))
    # tutte le righe presenti, anche quelle a zero minuti: la regola 2 dice di
    # non aggiungerne una seconda per la stessa partita, qualunque sia il valore
    per_g = rig.groupby("gid").gu.apply(lambda s: set(int(x) for x in s)).to_dict()

    # ── Rete di sicurezza: una persona, un record ────────────────────────────
    # Se la stessa persona ha ancora due schede con partite, questo script le
    # riempirebbe TUTTE E DUE fino al totale di Understat: due volte lo stesso
    # giocatore in classifica, con i numeri completi. Un doppio conteggio che
    # prima non c'era, creato dalla riparazione. E' successo in anteprima —
    # "Milan Djuric Djuric" e "Milan Djuric" portati entrambi a 50 partite —
    # perche' il test di accettazione guardava la scheda e non la persona.
    # Prima si unisce (unisci_record_doppioni.py), poi si importa.
    con_righe = {int(i) for i in ana.id if per_g.get(int(i))}
    gruppi: dict[str, list] = collections.defaultdict(list)
    for _, r in ana.iterrows():
        if int(r.id) in con_righe:
            gruppi[chiave_nome(r.n)].append(str(r.n))
    spezzati = {k: v for k, v in gruppi.items() if len(v) > 1}
    if spezzati:
        print("FERMO: queste persone hanno ancora due schede con partite.")
        print("Riempirle entrambe creerebbe un doppio conteggio. Lancia prima")
        print("`python unisci_record_doppioni.py --esegui`.\n")
        for k, v in sorted(spezzati.items())[:15]:
            print(f"   {k}: {' | '.join(v)}")
        if len(spezzati) > 15:
            print(f"   ... e altre {len(spezzati) - 15}")
        raise SystemExit(1)

    da_inserire: list[dict] = []
    saltati: list[str] = []
    toccati = 0
    for _, r in ana.iterrows():
        db = per_g.get(int(r.id))
        if not db:
            continue                      # schede svuotate dall'unione: non e' un buco
        us, pid, via = riscontro(r, idx)
        if not us:
            continue                      # senza riscontro non si inventa niente
        manca = us - db
        if not manca:
            continue
        righe = []
        fermo = None
        for g in sorted(manca):
            cid = cal_id.get(g)
            if cid is None:
                fermo = f"la partita {g} non e' in calendario"
                break
            i = roster.get((g, pid))
            if i is None:
                fermo = f"nessun roster per la partita {g}"
                break
            xg = float(i.get("xG", 0) or 0)
            goal = int(i.get("goals", 0) or 0)
            pen = rigori.get((g, _nm(i.get("player"))), (0.0, 0))
            righe.append({
                "season": cal_st.get(g), "giocatore_id": int(r.id), "calendario_id": int(cid),
                "ruolo": "casa" if i.get("h_a") == "h" else "trasferta",
                "minuti": int(i.get("time") or 0), "goal": goal,
                "assist": int(i.get("assists", 0) or 0), "tiri": int(i.get("shots", 0) or 0),
                "xg": round(xg, 3), "xa": round(float(i.get("xA", 0) or 0), 3),
                "gialli": int(i.get("yellow_card", 0) or 0),
                "rossi": int(i.get("red_card", 0) or 0),
                "npxg": round(max(0.0, xg - pen[0]), 3), "npg": max(0, goal - pen[1]),
                "xg_chain": round(float(i.get("xGChain", 0) or 0), 3),
                "xg_buildup": round(float(i.get("xGBuildup", 0) or 0), 3),
            })
        if fermo:
            saltati.append(f"{r.n}: {fermo}")
            continue
        # accettazione: con queste righe il giocatore torna esatto?
        if (db | manca) != us:
            saltati.append(f"{r.n}: anche aggiungendole resterebbe fuori conto "
                           f"({len(db | manca)} contro {len(us)})")
            continue
        da_inserire.extend(righe)
        toccati += 1
        print(f"  {str(r.n)[:30]:30} +{len(righe):3d} partite  (da {len(db)} a {len(us)})  {via}")

    print(f"\nrighe da inserire: {len(da_inserire)} su {toccati} giocatori")
    if saltati:
        print("NON TOCCATI:")
        for s in saltati[:15]:
            print(f"   {s}")
        if len(saltati) > 15:
            print(f"   ... e altri {len(saltati) - 15}")

    if not esegui:
        print("\nAnteprima soltanto. Rilancia con --esegui per scrivere.")
        return
    if not da_inserire:
        print("\nNiente da fare.")
        return

    BACKUP.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame(da_inserire)
    df.to_csv(BACKUP / "righe_inserite.csv", index=False, encoding="utf-8")
    with eng.begin() as cx:
        df.to_sql("giocatore_partita", cx, if_exists="append", index=False)
    print(f"\n{len(df)} righe inserite. Copia -> {BACKUP}")
    print("Ora rigenera i payload: cambiano minuti, partite e quindi il TPI.")


if __name__ == "__main__":
    main()
