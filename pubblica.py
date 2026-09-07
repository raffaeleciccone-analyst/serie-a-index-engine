"""Esegue in fila tutto quello che serve a pubblicare il sito di una lega.

Nasce dal runbook `docs/runbooks/cambio_stagione.md`, che e' una lista di passi
in un ordine preciso. Una lista in un file di testo ha due difetti: un passo si
salta senza accorgersene, e quando si salta non da' errore — da' un sito con
meta' pagine su una stagione e meta' sull'altra. Le uniche cose che restano a
mano sono quelle che una macchina non puo' decidere: il link al CSV nel README
e il commit.

Uso:
    python pubblica.py --lega premier --stagione 2026-27          # elenca
    python pubblica.py --lega premier --stagione 2026-27 --esegui
    python pubblica.py --lega premier --stagione 2026-27 --esegui --da parte1
    python pubblica.py --lega premier --solo-verifica

Senza `--esegui` non tocca niente: stampa i passi che farebbe, nell'ordine, con
il comando esatto. E' il modo di guardare la procedura prima di lanciarla.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BASE_DIR = Path(__file__).resolve().parent

# I nomi che si battono davvero, non quelli che vuole Understat. "premier" e
# "serie-a" sono quello che una persona scrive; la stringa completa con il
# trattino e le maiuscole giuste e' un'occasione di sbagliare senza motivo.
ALIAS_LEGA = {
    "premier": "ENG-Premier League",
    "premier-league": "ENG-Premier League",
    "eng": "ENG-Premier League",
    "serie-a": "ITA-Serie A",
    "seriea": "ITA-Serie A",
    "ita": "ITA-Serie A",
}


def risolvi_lega(nome: str) -> str:
    chiave = nome.strip().lower().replace(" ", "-").replace("_", "-")
    if chiave in ALIAS_LEGA:
        return ALIAS_LEGA[chiave]
    # anche il nome completo va bene: chi lo sa gia' non deve impararne un altro
    for valore in ALIAS_LEGA.values():
        if nome.strip().lower() == valore.lower():
            return valore
    raise SystemExit(
        "Lega sconosciuta: %r. Sono: %s"
        % (nome, ", ".join(sorted(set(ALIAS_LEGA)))))


# ════════════════════════════════════════════════════════════════════════════
#  L'archivio della stagione conclusa
# ════════════════════════════════════════════════════════════════════════════
def archivia(uscita: Path, stagione_nuova: str, esegui: bool) -> list[str]:
    """Mette al sicuro i payload della stagione conclusa, prima di riscriverli.

    E' il passo con la trappola: `parte1_analisi.py` sovrascrive `payload.json`,
    quindi va fatto prima — ma rifatto dopo archivierebbe la stagione nuova
    sotto il nome di quella vecchia, cancellando l'unica copia. Qui la
    condizione non e' "e' gia' stato fatto?" ma "che stagione c'e' dentro?": se
    `payload.json` e' gia' quella nuova non c'e' niente da archiviare, e un file
    di archivio che esiste gia' non si tocca mai.
    """
    fatti: list[str] = []
    corrente = uscita / "payload.json"
    if not corrente.is_file():
        return ["payload.json non c'e' ancora: niente da archiviare"]
    try:
        vecchia = json.loads(corrente.read_text(encoding="utf-8")).get("stagione")
    except (OSError, ValueError) as e:
        raise SystemExit("payload.json non si legge (%s): l'archivio si ferma qui." % e)

    if not vecchia:
        return ["payload.json non dichiara la stagione: archivio saltato"]
    if str(vecchia) == stagione_nuova:
        return ["payload.json e' gia' la %s: niente da archiviare" % stagione_nuova]

    coppie = [("payload.json", "payload_%s.json" % vecchia),
              ("payload_lista.json", "payload_lista_%s.json" % vecchia),
              ("payload_full.json", "payload_full_%s.json" % vecchia)]
    for origine, destinazione in coppie:
        src, dst = uscita / origine, uscita / destinazione
        if not src.is_file():
            fatti.append("%s non c'e': saltato" % origine)
            continue
        if dst.exists():
            fatti.append("%s c'e' gia': non si sovrascrive" % destinazione)
            continue
        if esegui:
            shutil.copy2(src, dst)
        fatti.append("%s -> %s" % (origine, destinazione))
    return fatti


# ════════════════════════════════════════════════════════════════════════════
#  I passi
# ════════════════════════════════════════════════════════════════════════════
def passi(stagione: str, lega: str = "") -> list[tuple[str, list[str] | None, str]]:
    """(nome, comando, a cosa serve). Comando None = lo fa questo script.

    Le pagine che esistono solo per una lega entrano nella sequenza solo per
    quella (`config.PAGINE_EXTRA`). Il caso di mercato era l'unica pagina del
    sito fuori da qui, e non per una decisione: il suo generatore stava dentro
    il repo pubblicato invece che nel motore, quindi nessuno lo chiamava. Al
    cambio di stagione restava indietro da solo.
    """
    elenco: list[tuple[str, list[str] | None, str]] = [
        ("parte4", ["parte4_aggiorna.py"],
         "scarica e carica le partite della stagione"),
        ("game-log", ["deriva_game_log.py", "--season", stagione, "--esegui"],
         "il game log per squadra"),
        ("anagrafica", ["anagrafica_da_hexi.py", "--esegui"],
         "rose e ruoli da heXI"),
        ("archivio", None,
         "mette al sicuro i payload della stagione conclusa"),
        ("parte1", ["parte1_analisi.py"],
         "l'indice della stagione"),
        ("aggregato", ["parte1_analisi.py", "--tutte-le-stagioni"],
         "la vista su tutte le stagioni"),
        ("parte2", ["parte2_dashboard.py"],
         "la classifica"),
        ("home", ["pagina_home.py"],
         "la homepage"),
        ("guida", ["pagina_guida.py"],
         "il metodo"),
        ("squadre", ["pagina_squadra.py"],
         "le pagine squadra, e toglie quelle uscite dal campionato"),
        ("validazione", ["parte3_valida_tpi.py", "--solo-pagina"],
         "riscrive la pagina SENZA rimisurare"),
    ]
    if lega == "ITA-Serie A":
        # Dopo l'archivio: il caso si costruisce sull'ultima stagione che ha
        # abbastanza minuti, e a settembre quella e' l'annata appena archiviata.
        elenco.append(
            ("caso-mercato", ["caso_mercato.py"],
             "il caso di mercato, sull'ultima stagione con abbastanza minuti"))
    return elenco


# ════════════════════════════════════════════════════════════════════════════
#  La verifica finale
# ════════════════════════════════════════════════════════════════════════════
# `<small[^>]*>` e non `<small>`: la pagina Pro dichiara la stagione con un
# attributo (`data-st-it`) e la scrive a runtime dal payload. Con la regex
# stretta quel tag non corrispondeva piu', la pagina usciva dal controllo
# senza dirlo, e la verifica passava perche' guardava una pagina in meno.
_BARRA = re.compile(r'class="nav-brand"[^>]*>([^<]*)<small[^>]*>([^<]*)</small>')


def verifica(repo: Path, uscita: Path, stagione: str) -> list[str]:
    """Le pagine pubblicate parlano tutte della stessa stagione?

    E' la domanda che nessuno dei passi si fa, perche' ognuno guarda solo la
    propria pagina. Un passo saltato si vede qui e solo qui: la validazione che
    dice ancora "25/26" mentre le altre venticinque dicono "26/27" non e' un
    errore per nessuno degli script che le hanno scritte.
    """
    import config
    attesa = config.etichetta_stagione(stagione, breve=True)
    guai: list[str] = []

    pagine = sorted(repo.glob("*.html"))
    if not pagine:
        return ["nessuna pagina in %s" % repo]

    per_etichetta: dict[str, list[str]] = {}
    marchi: dict[str, list[str]] = {}
    for pagina in pagine:
        testo = pagina.read_text(encoding="utf-8", errors="replace")
        trovata = _BARRA.search(testo)
        if not trovata:
            continue
        marchi.setdefault(trovata.group(1).strip(), []).append(pagina.name)
        etichetta = trovata.group(2).strip()
        # Barra vuota + segnaposto `data-st-*` = la stagione la mette il
        # payload quando la pagina si apre, e non c'e' niente da confrontare
        # qui. Non e' un buco nel controllo: che il payload pubblicato sia
        # quello giusto lo verifica il blocco su payload.json qui sotto, e da
        # li' quelle pagine derivano tutto.
        if not etichetta and "data-st-it" in testo:
            continue
        per_etichetta.setdefault(etichetta, []).append(pagina.name)

    for etichetta, quali in sorted(per_etichetta.items()):
        if etichetta != attesa:
            guai.append("stagione %s (invece di %s) in: %s"
                        % (etichetta, attesa, ", ".join(quali)))

    for marchio, quali in sorted(marchi.items()):
        if marchio != config.SITO_MARCHIO:
            guai.append("il sito si chiama %r (la configurazione dice %r) in: %s"
                        % (marchio, config.SITO_MARCHIO, ", ".join(quali)))

    payload = repo / "payload.json"
    dichiarata = None
    if payload.is_file():
        try:
            dichiarata = json.loads(payload.read_text(encoding="utf-8")).get("stagione")
        except (OSError, ValueError):
            dichiarata = None
        if dichiarata and str(dichiarata) != stagione:
            guai.append("payload.json pubblicato e' la %s, non la %s"
                        % (dichiarata, stagione))

    squadre = list(repo.glob("squadra-*.html"))
    if squadre and len(squadre) < 16:
        guai.append("solo %d pagine squadra: un campionato ne ha di piu'" % len(squadre))

    # Le pagine squadra che avanzano. Il confronto e' col payload pubblicato e
    # non con la cartella del motore: quella e' un'area di lavoro, e puo' essere
    # rimasta indietro o essere gia' andata avanti — misurarci contro darebbe
    # allarmi che non c'entrano niente con il sito. Ha senso solo se il payload
    # e' della stagione che stiamo verificando.
    if squadre and dichiarata and str(dichiarata) == stagione:
        from pagina_squadra import slug
        try:
            giocatori = json.loads(payload.read_text(encoding="utf-8")).get("players") or []
        except (OSError, ValueError):
            giocatori = []
        attese = {"squadra-%s.html" % slug(g["squadra"]) for g in giocatori if g.get("squadra")}
        orfane = {p.name for p in squadre} - attese
        if orfane and attese:
            guai.append("pagine di squadre che non sono piu' nel campionato: %s"
                        % ", ".join(sorted(orfane)))
    return guai


# ════════════════════════════════════════════════════════════════════════════
def _leggi_config(ambiente: dict) -> dict:
    """config in un processo a parte: qui dentro sarebbe gia' importato.

    `config` decide database, cartelle, repo e nome del sito al momento
    dell'import, leggendo l'ambiente. Importarlo qui vorrebbe dire fissarlo
    prima di sapere quale lega ci hanno chiesto.
    """
    letto = subprocess.run(
        [sys.executable, "-c",
         "import config, json; print(json.dumps({"
         "'stagione': config.SEASON_CORRENTE, 'uscita': str(config.cartella_uscita()),"
         "'repo': str(config.cartella_pubblicazione()), 'sito': config.SITO_NOME}))"],
        cwd=str(BASE_DIR), env=ambiente, capture_output=True, text=True)
    if letto.returncode != 0:
        raise SystemExit((letto.stderr or "").strip() or "config non si carica")
    return json.loads(letto.stdout.strip().splitlines()[-1])


def _avvisa(testo: str) -> None:
    """Manda l'esito su Telegram, se il bot c'e'.

    Il giro completo dura parecchi minuti — parte4 scarica una stagione intera —
    e nessuno resta a guardarlo. Un bot che ti avvisa quando comincia e poi non
    ti dice com'e' finita ti lascia comunque a controllare a mano.

    Se il bot non e' configurato non e' un guaio: il messaggio salta e la
    pubblicazione va avanti. E' un avviso, non un passo della procedura.
    """
    try:
        import sentinella
        sentinella.manda(testo)
        print("(avviso mandato su Telegram)")
    except SystemExit as e:
        print("(avviso non mandato: %s)" % str(e).splitlines()[0])
    except Exception as e:
        print("(avviso non mandato: %s)" % e)


def _stampa_verifica(repo: Path, uscita: Path, stagione: str, ambiente: dict) -> int:
    codice = ("import json, pathlib, pubblica; "
              "print(json.dumps(pubblica.verifica(pathlib.Path(%r), pathlib.Path(%r), %r)))"
              % (str(repo), str(uscita), stagione))
    letto = subprocess.run([sys.executable, "-c", codice],
                           cwd=str(BASE_DIR), env=ambiente,
                           capture_output=True, text=True)
    if letto.returncode != 0:
        print("Verifica non riuscita:\n%s" % (letto.stderr or "").strip())
        return 1
    guai = json.loads(letto.stdout.strip().splitlines()[-1])
    if not guai:
        print("Verifica: tutte le pagine pubblicate dicono la stessa stagione, "
              "e il sito ha un nome solo.")
        return 0
    print("Verifica — %d cosa/e da guardare:" % len(guai))
    for g in guai:
        print("  · %s" % g)
    return 1


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Genera e pubblica il sito di una lega, nell'ordine giusto.")
    ap.add_argument("--lega", required=True, help="premier | serie-a")
    ap.add_argument("--stagione", help="es. 2026-27 (default: quella in .env)")
    ap.add_argument("--esegui", action="store_true",
                    help="senza questo non tocca niente: elenca e basta")
    ap.add_argument("--da", metavar="PASSO", help="riparte da questo passo")
    ap.add_argument("--fino", metavar="PASSO", help="si ferma dopo questo passo")
    ap.add_argument("--salta", metavar="PASSI", default="",
                    help="passi da non fare, separati da virgola")
    ap.add_argument("--solo-verifica", action="store_true",
                    help="non genera niente: controlla il sito gia' pubblicato")
    ap.add_argument("--avvisa", action="store_true",
                    help="a fine giro manda l'esito su Telegram (serve la "
                         "sentinella configurata)")
    a = ap.parse_args()

    lega = risolvi_lega(a.lega)
    ambiente = dict(os.environ, SERIE_A_LEGA=lega)
    if a.stagione:
        ambiente["SERIE_A_SEASON"] = a.stagione

    cfg = _leggi_config(ambiente)
    stagione = cfg["stagione"]
    uscita, repo = Path(cfg["uscita"]), Path(cfg["repo"])
    ambiente["SERIE_A_SEASON"] = stagione

    print("=" * 74)
    print("%s — %s — stagione %s" % (cfg["sito"], lega, stagione))
    print("uscita: %s" % uscita)
    print("repo:   %s" % repo)
    print("=" * 74)

    if a.solo_verifica:
        return _stampa_verifica(repo, uscita, stagione, ambiente)

    elenco = passi(stagione, lega)
    nomi = [n for n, _, _ in elenco]
    for scelto in (a.da, a.fino):
        if scelto and scelto not in nomi:
            raise SystemExit("passo sconosciuto: %r. Sono: %s"
                             % (scelto, ", ".join(nomi)))
    inizio = nomi.index(a.da) if a.da else 0
    fine = nomi.index(a.fino) + 1 if a.fino else len(elenco)
    saltati = {s.strip() for s in a.salta.split(",") if s.strip()}

    if not a.esegui:
        print("\nGiro a vuoto: nessun file viene toccato. "
              "Aggiungi --esegui per farlo davvero.\n")

    for numero, (nome, comando, scopo) in enumerate(elenco[inizio:fine], inizio + 1):
        if nome in saltati:
            print("\n[%2d/%d] %-11s SALTATO" % (numero, len(elenco), nome))
            continue
        print("\n[%2d/%d] %-11s %s" % (numero, len(elenco), nome, scopo))

        if comando is None:
            for riga in archivia(uscita, stagione, a.esegui):
                print("        %s" % riga)
            continue

        print("        $ python %s" % " ".join(comando))
        if not a.esegui:
            continue
        esito = subprocess.run([sys.executable] + comando,
                               cwd=str(BASE_DIR), env=ambiente)
        if esito.returncode != 0:
            print("\n" + "!" * 74)
            print("Il passo %r si e' fermato (codice %d)." % (nome, esito.returncode))
            print("Quando l'hai sistemato, riprendi da li' senza rifare il resto:")
            print("    python pubblica.py --lega %s --stagione %s --esegui --da %s"
                  % (a.lega, stagione, nome))
            print("!" * 74)
            if a.avvisa:
                _avvisa("%s %s — la pubblicazione si e' fermata.\n\n"
                        "Passo: %s (%s)\n"
                        "Codice: %d\n\n"
                        "Per riprendere da li':\n"
                        "cd %s\n"
                        "python pubblica.py --lega %s --stagione %s --esegui --da %s"
                        % (cfg["sito"], stagione, nome, scopo, esito.returncode,
                           BASE_DIR, a.lega, stagione, nome))
            return esito.returncode

    if not a.esegui:
        return 0

    print("\n" + "=" * 74)
    esito = _stampa_verifica(repo, uscita, stagione, ambiente)
    print("\nRestano le due cose che decidi tu:")
    print("  1. il link al CSV nel README del repo del sito")
    print("  2. cd %s && git add -A && git commit && git push" % repo)
    if a.avvisa:
        pagine = len(list(repo.glob("*.html")))
        squadre = len(list(repo.glob("squadra-*.html")))
        _avvisa("%s — pubblicata la %s.\n\n"
                "%d pagine, di cui %d di squadra.\n"
                "Verifica: %s\n\n"
                "Restano da fare a mano:\n"
                "1. il link al CSV nel README\n"
                "2. cd %s\n"
                "   git add -A && git commit && git push"
                % (cfg["sito"], stagione.replace("-", "/"), pagine, squadre,
                   "tutto allineato" if esito == 0
                   else "ci sono cose da guardare, vedi il terminale", repo))
    return esito


if __name__ == "__main__":
    sys.exit(main())
