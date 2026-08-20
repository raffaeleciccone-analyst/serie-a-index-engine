"""Riunisce i record di `giocatori` che descrivono la stessa persona.

COSA SUCCEDE (20/08/2026)
------------------------
Ottantuno giocatori esistono due volte in anagrafica: una volta col cognome
raddoppiato ("Nikola Krstovic Krstovic", id sotto 5000) e una volta col nome
pulito ("Nikola Krstovic", id sopra il milione). Sono i cambi di maglia:
l'import di una stagione ha creato un record nuovo invece di riconoscere quello
che c'era gia'.

Le partite non si sovrappongono mai — verificato su tutte e 81 le coppie, zero
partite in comune — quindi NON c'e' doppio conteggio: c'e' una carriera tagliata
in due meta', una per club.

COSA ROMPE, E COSA NO
---------------------
Le classifiche di stagione singola sono giuste: dentro una stagione ogni meta'
contiene tutte le partite che servono. E' l'aggregato a sbagliare, perche' li'
le due meta' andrebbero sommate e invece restano separate:

  - nella vista "Due stagioni" 36 giocatori compaiono DUE VOLTE, uno per maglia,
    con due TPI diversi (Krstovic Atalanta 0.79 e Krstovic Lecce 0.49);
  - "Sopra le attese" confronta la stagione con la base storica dello stesso
    id: per questi 81 la base e' mezza, quindi il confronto e' senza senso.

LA REGOLA
---------
Due record si uniscono solo se valgono tutte:

  1. il gruppo di nome ha esattamente due record con righe partita (tre o piu'
     si lasciano stare: vanno guardati a mano);
  2. zero partite in comune — se se ne sovrappone anche una sola non e' una
     carriera spezzata ma qualcos'altro, e si ferma;
  3. il nome non e' ambiguo su Understat (un solo player_id), altrimenti si
     rischia di fondere due persone diverse — cioe' di rifare il bug che
     `ripara_righe_omonimi.py` ha appena finito di togliere;
  4. TEST DI ACCETTAZIONE: nell'unione non deve finire NIENTE di estraneo,
     cioe' nessuna partita che Understat non attribuisca a quel giocatore.

Il primo tentativo pretendeva invece che l'unione fosse anche COMPLETA, e cosi'
si rifiutava di unire chi, oltre a essere sdoppiato, aveva pure delle partite
mancanti: dieci coppie restavano separate per un difetto che con lo sdoppiamento
non c'entra. E' lo stesso errore che aveva bloccato Venturino e Luperto nel
primo script — due difetti diversi misurati con un test solo — ripetuto qui.
Qui pero' costava di piu': con le due meta' ancora separate,
`importa_partite_mancanti.py` le avrebbe riempite tutte e due, inventando un
doppio conteggio che prima non esisteva. Va lanciato prima questo, poi quello.

Le righe vengono spostate sul record canonico (quello con l'id piu' basso, che
e' l'anagrafica principale); il doppione resta in tabella ma senza partite, e
il motore lo ignora da solo perche' aggrega con un JOIN su `giocatore_partita`.
Niente DELETE su `giocatori`: se qualcosa va storto si torna indietro con la
mappa salvata nel backup.

USO
    python unisci_record_doppioni.py            # mostra e basta
    python unisci_record_doppioni.py --esegui   # scrive davvero
"""
from __future__ import annotations

import collections
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
from sqlalchemy import create_engine, text

import config
from ripara_righe_omonimi import _nm, carica_understat

BACKUP = Path(r"C:\dev") / f"_backup_unione_doppioni_{datetime.now():%Y%m%d_%H%M}"


def chiave_nome(nome: str) -> str:
    """Il nome vero, tolto il cognome raddoppiato.

    L'anagrafica difettosa scrive "Lorenzo Colombo Colombo": il primo tentativo
    prendeva i primi due token, che va bene per i nomi di due parole e sbaglia
    su tutti gli altri. "Koni de Winter Winter" diventava "koni de", che su
    Understat non esiste, e quella coppia veniva rifiutata per il motivo
    sbagliato. Meglio togliere la ripetizione in coda e tenere il nome intero.
    """
    t = _nm(nome).split()
    while len(t) >= 2 and t[-1] == t[-2]:
        t.pop()
    return " ".join(t)


def main() -> None:
    esegui = "--esegui" in sys.argv
    eng = create_engine(config.db_url())
    per_id, per_nome, id_per_nome = carica_understat()
    if not per_id:
        raise SystemExit("cache Understat vuota o assente")

    with eng.connect() as cx:
        ana = pd.read_sql(text(
            "SELECT g.id, TRIM(CONCAT_WS(' ',g.nome,g.cognome)) n, g.squadra_id, "
            "g.understat_id, sq.nome squadra FROM giocatori g "
            "LEFT JOIN squadre sq ON sq.id = g.squadra_id"), cx)
        rig = pd.read_sql(text(
            "SELECT gp.giocatore_id gid, gp.calendario_id cid, c.game_id_understat gu "
            "FROM giocatore_partita gp JOIN calendario c ON c.id = gp.calendario_id "
            "WHERE gp.minuti > 0 AND c.game_id_understat IS NOT NULL"), cx)

    per_g = rig.groupby("gid").gu.apply(lambda s: set(int(x) for x in s)).to_dict()
    gruppi: dict[str, list] = collections.defaultdict(list)
    for _, r in ana.iterrows():
        if per_g.get(int(r.id)):
            gruppi[chiave_nome(r.n)].append(r)

    piano: list[tuple[int, int, str]] = []
    saltati: list[str] = []
    resta: list[str] = []
    for chiave, recs in sorted(gruppi.items()):
        if len(recs) < 2:
            continue
        if len(recs) > 2:
            saltati.append(f"{chiave}: {len(recs)} record, da guardare a mano")
            continue
        if len(id_per_nome.get(chiave, ())) > 1:
            saltati.append(f"{chiave}: nome ambiguo su Understat, non si fonde")
            continue
        a, b = sorted(recs, key=lambda r: int(r.id))       # canonico = id piu' basso
        ga, gb = per_g[int(a.id)], per_g[int(b.id)]
        comuni = ga & gb
        if comuni:
            saltati.append(f"{chiave}: {len(comuni)} partite in comune, non e' una "
                           f"carriera spezzata")
            continue
        us = set(per_nome.get(chiave, {}))
        unione = ga | gb
        if not us:
            saltati.append(f"{chiave}: nessun riscontro Understat")
            continue
        # Il test giusto e' che l'unione non contenga NIENTE DI ESTRANEO.
        # Pretendere anche che sia completa rifiutava di unire chi in piu' ha
        # delle partite mancanti — e quelle sono l'altro difetto, non un motivo
        # per lasciare una persona spezzata in due. Era la stessa confusione fra
        # i due difetti che aveva bloccato Venturino e Luperto nel primo script,
        # rifatta qui; e qui costava piu' cara, perche' con le meta' separate
        # importa_partite_mancanti.py le riempie tutt'e due e inventa un doppio
        # conteggio che prima non c'era.
        estranee = unione - us
        if estranee:
            saltati.append(f"{chiave}: {len(estranee)} partite che Understat non "
                           f"gli attribuisce, non si fonde")
            continue
        if unione != us:
            resta.append(f"{chiave}: unito, ma restano {len(us - unione)} partite "
                         f"da importare a parte")
        piano.append((int(b.id), int(a.id), chiave))

    print(f"coppie esaminate : {sum(1 for v in gruppi.values() if len(v) > 1)}")
    print(f"da unire         : {len(piano)}")
    print(f"lasciate stare   : {len(saltati)}")
    for r in saltati[:20]:
        print(f"   {r}")
    if len(saltati) > 20:
        print(f"   ... e altre {len(saltati) - 20}")
    if resta:
        print("uniti, ma con partite ancora da importare:")
        for r in resta:
            print(f"   {r}")

    if piano:
        idx = ana.set_index("id")
        print(f"\nprime dieci unioni (le righe passano da -> a):")
        for da, a, chiave in piano[:10]:
            print(f"   {chiave:26} id{da} '{idx.loc[da].squadra}' {len(per_g[da]):3d}p"
                  f"  ->  id{a} '{idx.loc[a].squadra}' {len(per_g[a]):3d}p"
                  f"   = {len(per_g[da] | per_g[a]):3d}p")

    if not esegui:
        print("\nAnteprima soltanto. Rilancia con --esegui per scrivere.")
        return
    if not piano:
        print("\nNiente da fare.")
        return

    BACKUP.mkdir(parents=True, exist_ok=True)
    mappa = pd.DataFrame(piano, columns=["da_giocatore_id", "a_giocatore_id", "nome"])
    mappa.to_csv(BACKUP / "mappa_unioni.csv", index=False, encoding="utf-8")
    rig[rig.gid.isin(mappa.da_giocatore_id)].to_csv(
        BACKUP / "righe_spostate.csv", index=False, encoding="utf-8")

    with eng.begin() as cx:
        for da, a, chiave in piano:
            cx.execute(text("UPDATE giocatore_partita SET giocatore_id=:a WHERE giocatore_id=:d"),
                       {"a": a, "d": da})
        print(f"\n{len(piano)} record uniti.")
    print(f"backup della mappa -> {BACKUP}")
    print("Ora rigenera i payload: l'aggregato a due stagioni cambia.")


if __name__ == "__main__":
    main()
