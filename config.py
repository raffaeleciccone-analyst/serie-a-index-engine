"""
config.py — caricatore credenziali centralizzato (fail-fast).

Tutte le credenziali e i parametri runtime vivono in .env (gitignored).
Questo modulo:
  · carica .env nella process env (idempotente)
  · espone DB_HOST/DB_USER/DB_PASSWORD/DB_NAME e db_url() già pronti
  · LANCIA RuntimeError se DB_PASSWORD manca — niente fallback hardcoded

Uso:
    from config import DB_PASSWORD, db_url
    engine = create_engine(db_url())
"""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import quote_plus

_REPO_ROOT = Path(__file__).resolve().parent
_ENV_FILE = _REPO_ROOT / ".env"


def _load_env() -> None:
    """Carica .env nella process env senza sovrascrivere variabili già presenti."""
    if not _ENV_FILE.exists():
        return
    for raw in _ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


_load_env()


def _require(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        raise RuntimeError(
            f"Variabile {name} mancante. Imposta in {_ENV_FILE} o nella shell.\n"
            f"Esempio: echo '{name}=...' >> {_ENV_FILE}"
        )
    return val


DB_HOST: str = os.environ.get("DB_HOST", "localhost")
DB_USER: str = os.environ.get("DB_USER", "root")
# La password si pretende quando serve DAVVERO, non all'import del modulo.
# Prima il fail-fast stava qui: importare config — e quindi parte1, parte3, i
# test — era impossibile senza un database, anche per le funzioni che con il
# database non c'entrano niente (risoluzione dei ruoli, formattazione, calcoli
# puri). Il controllo non e' sparito: si e' spostato in db_url(), che e' il
# punto in cui una password mancante e' un problema vero.
DB_PASSWORD: str = os.environ.get("DB_PASSWORD", "")

# Stagione che il sito pubblica. Da quando il DB contiene anche il backfill
# 2024-25, "quale stagione" non e' piu' una domanda con una risposta ovvia:
# parte1 senza filtro le aggregava tutte e produceva una classifica che
# sembrava plausibile ma non era di nessuna stagione. Il valore sta qui perche'
# a cambio stagione si tocca un punto solo.
SEASON_CORRENTE: str = os.environ.get("SERIE_A_SEASON", "2025-26")


# Il campionato. Sta qui per lo stesso motivo della stagione: era scritto dentro
# parte4_aggiorna.py, cioe' nel punto in cui si scarica, e questo bastava
# finche' il campionato era uno solo. Understat ne espone cinque
# ("ENG-Premier League", "ESP-La Liga", "FRA-Ligue 1", "GER-Bundesliga",
# "ITA-Serie A") con la stessa API e lo stesso formato, quindi il motore puo'
# servirne piu' d'uno cambiando due variabili d'ambiente invece che
# duplicando il codice — e una correzione resta una sola correzione.
LEGA_UNDERSTAT: str = os.environ.get("SERIE_A_LEGA", "ITA-Serie A")


# Il database lo decide il campionato, come la cartella di uscita e il repo.
# Era `DB_NAME` fissato in .env sulla Serie A: per generare la Premier bisognava
# ricordarsi di sovrascriverlo a mano ogni volta, e dimenticarsene una volta
# sola voleva dire scrivere squadre e partite inglesi dentro il database
# italiano. E' la stessa forma dei difetti di oggi — una risorsa condivisa, il
# nome scritto a mano, nessuno che controlla — quindi qui la risposta e' la
# stessa: la lega e la risorsa viaggiano insieme.
DATABASE_LEGA: dict[str, str] = {
    "ITA-Serie A":        "serie_a_25_26",
    "ENG-Premier League": "premier_25_26",
}
_db_dichiarato = os.environ.get("DB_NAME")
DB_NAME: str = DATABASE_LEGA.get(LEGA_UNDERSTAT, _db_dichiarato or "serie_a_25_26")


# Come si chiama la lega quando la vede un lettore, e come si chiamano i file
# che porta il suo nome. Era tutto scritto dentro le pagine: il titolo, il
# marchio in alto a sinistra, il nome del CSV scaricabile. Finche' il
# campionato era uno solo non dava fastidio; al secondo, tradurre a mano quelle
# stringhe avrebbe creato le due copie del motore che la prima decisione del
# progetto rifiuta.
#
# `slug` entra nei nomi dei file (dashboard_<slug>.html, <slug>_tpi_<stagione>.csv):
# resta "serie_a" per la Serie A, cosi' gli indirizzi gia' pubblicati non
# cambiano e nessun link esistente si rompe.
#
# `sito` e' il nome per intero, quello che va nel <title>, nel piede e sotto il
# monogramma. `marchio` e' come si firma la barra in cima, dove lo spazio e'
# una riga sola. Erano un campo solo, con " Index" attaccato dal codice in
# nove punti: funzionava finche' tutti i siti si chiamavano "<qualcosa> Scout
# Index", e la barra poteva sempre essere il nome senza l'ultima parola.
#
# Il sito della Premier e' stato rinominato "Premier League Index" a mano su
# ventisei pagine, nel repo pubblicato, il 27 agosto. Il motore non lo sapeva:
# continuava a comporre "Premier League Scout", quindi la prima rigenerazione —
# quella del cambio stagione — avrebbe rimesso il vecchio nome su tutte e
# ventisei, in silenzio. Li' la barra non puo' nemmeno essere il nome senza
# l'ultima parola, perche' resterebbe "Premier League", che e' il campionato e
# non il sito. Quindi i due nomi si dichiarano, invece di ricavarne uno
# dall'altro.
IDENTITA_LEGA: dict[str, dict[str, str]] = {
    "ITA-Serie A":        {"nome": "Serie A",        "sito": "Serie A Scout Index",
                           "marchio": "Serie A Scout",        "slug": "serie_a"},
    "ENG-Premier League": {"nome": "Premier League", "sito": "Premier League Index",
                           "marchio": "Premier League Index", "slug": "premier_league"},
    "ESP-La Liga":        {"nome": "La Liga",        "sito": "La Liga Scout Index",
                           "marchio": "La Liga Scout",        "slug": "la_liga"},
    "GER-Bundesliga":     {"nome": "Bundesliga",     "sito": "Bundesliga Scout Index",
                           "marchio": "Bundesliga Scout",     "slug": "bundesliga"},
    "FRA-Ligue 1":        {"nome": "Ligue 1",        "sito": "Ligue 1 Scout Index",
                           "marchio": "Ligue 1 Scout",        "slug": "ligue_1"},
}

# L'id con cui Understat numera i campionati nella propria cache. Serve a
# leggere i file giusti: i `match_*.json` scaricati finiscono tutti nella stessa
# cartella, senza niente addosso che dica da quale torneo vengono, e l'unico
# elenco affidabile di quali partite appartengono a una lega e' il file di
# stagione `league_<id>_season_<anno>.json`. Senza questo id la derivazione dei
# ruoli leggeva l'intera cartella: dopo aver generato la Premier, la Serie A si
# ritrovava i ruoli calcolati sulle partite inglesi.
UNDERSTAT_LEAGUE_ID: dict[str, str] = {
    "ENG-Premier League": "1",
    "ITA-Serie A":        "2",
    "GER-Bundesliga":     "3",
    "ESP-La Liga":        "4",
    "FRA-Ligue 1":        "5",
}


# La lingua in cui il sito si apre. Il sito e' bilingue e il selettore resta
# dove sta: questa e' la lingua di partenza, quella che vede chi arriva senza
# aver mai scelto, e quella del testo che resta a schermo se JavaScript non
# gira. Sulla Serie A e' l'italiano; sulla Premier era comunque l'italiano,
# perche' la lingua era scritta a mano in due `<html lang="it">` — un sito
# sulla Premier League che si apre in italiano davanti a un lettore inglese.
LINGUA_BASE: dict[str, str] = {
    "ENG-Premier League": "en",
    "ITA-Serie A":        "it",
    "ESP-La Liga":        "en",
    "GER-Bundesliga":     "en",
    "FRA-Ligue 1":        "en",
}


# Il repo che pubblica il sito di questa lega. Ogni modulo che genera una
# pagina ne scrive due copie: una in `cartella_uscita()` e una qui, che e' la
# cartella da cui si committa. Era `serie-a-index` per tutti, scritta uguale in
# cinque file — quindi generare la Premier scriveva le pagine della Premier
# DENTRO il repo della Serie A. E' cosi' che la pagina di validazione del Serie
# A si e' ritrovata intitolata "Premier League Scout Index", con i numeri
# inglesi al posto dei propri, sul progetto la cui unica promessa e' pubblicare
# tutte le verifiche.
REPO_PUBBLICAZIONE: dict[str, str] = {
    "ITA-Serie A":        "serie-a-index",
    "ENG-Premier League": "premier-league-index",
}


# Le pagine in piu' che esistono solo per certe leghe. Il caso di mercato e la
# pagina Pro sono stati scritti sulla Serie A e non hanno un gemello altrove:
# lasciarli nel menu di un altro campionato significa pubblicare due link rotti
# in cima a ogni pagina.
PAGINE_EXTRA: dict[str, list[tuple[str, str, str]]] = {
    "ITA-Serie A": [("caso-mercato.html", "Caso di mercato", "Market case"),
                    ("dashboard_pro.html", "TPI Pro", "TPI Pro")],
}

_ident = IDENTITA_LEGA.get(LEGA_UNDERSTAT, IDENTITA_LEGA["ITA-Serie A"])
LEGA_NOME: str = _ident["nome"]     # "Premier League"
SITO_NOME: str = _ident["sito"]     # "Premier League Index" — il nome per intero
SITO_MARCHIO: str = _ident.get("marchio") or _ident["sito"]  # come si firma la barra
LEGA_SLUG: str = _ident["slug"]     # "premier"
PAGINE_LEGA: list = PAGINE_EXTRA.get(LEGA_UNDERSTAT, [])
LEGA_ID_UNDERSTAT: str = UNDERSTAT_LEAGUE_ID.get(LEGA_UNDERSTAT, "2")  # "2" = Serie A
LINGUA: str = LINGUA_BASE.get(LEGA_UNDERSTAT, "it")   # "en" sulla Premier
EN_BASE: bool = LINGUA == "en"

# L'assistente conversazionale risponde da un backend costruito sui dati della
# Serie A: metterlo su un altro campionato vorrebbe dire pubblicare uno
# strumento che risponde con sicurezza sul torneo sbagliato.
ASSISTENTE: bool = LEGA_UNDERSTAT == "ITA-Serie A"


def cartella_uscita(base=None):
    """Dove finiscono i file generati, una cartella per lega.

    La Serie A tiene "dashboard_output" perche' e' quella gia' pubblicata; le
    altre leghe prendono un suffisso. Stava scritta in cinque file diversi, e
    generare un secondo campionato sovrascriveva il primo senza dirlo.
    """
    from pathlib import Path
    base = Path(base) if base else Path(__file__).resolve().parent
    nome = "dashboard_output" if LEGA_UNDERSTAT == "ITA-Serie A"         else "dashboard_output_" + LEGA_UNDERSTAT.split("-")[0].lower()
    return base / nome


def cartella_pubblicazione(base=None):
    """Il repo del sito di QUESTA lega, dove finisce la copia da committare.

    Si puo' forzare con SERIE_A_DEMO_DIR, che e' come si chiamava quando la
    cartella era una sola. Per una lega senza un repo dichiarato si ricava dal
    nome ("la-liga-index"): meglio una cartella nuova che scrivere in quella
    di un altro campionato.
    """
    import os as _os
    from pathlib import Path
    forzata = _os.environ.get("SERIE_A_DEMO_DIR")
    if forzata:
        return Path(forzata)
    base = Path(base) if base else Path(__file__).resolve().parent.parent
    nome = REPO_PUBBLICAZIONE.get(LEGA_UNDERSTAT,
                                  LEGA_SLUG.replace("_", "-") + "-index")
    return base / nome


def anno_understat(season: str | None = None) -> int:
    """Understat vuole l'anno d'inizio come numero: '2025-26' -> 2025.

    Era scritto a mano accanto alla lega (`seasons=2025`), quindi a cambio
    stagione erano due valori da ricordarsi invece di uno.
    """
    # `is None` e non `or`: la stringa vuota e' falsa in Python, quindi con `or`
    # una stagione vuota scivolava in silenzio su quella corrente e si sarebbe
    # scaricata l'annata sbagliata senza che niente lo dicesse. None significa
    # "quella corrente"; "" significa che qualcuno ha sbagliato, e va detto.
    s = SEASON_CORRENTE if season is None else season
    try:
        return int(str(s).split("-")[0])
    except (ValueError, IndexError):
        raise ValueError(f"stagione non interpretabile: {s!r} (attesa 'AAAA-AA')")


def etichetta_stagione(season: str | None = None, breve: bool = False) -> str:
    """'2025-26' come lo legge chi apre il sito: '2025/26', o '25/26' se breve.

    Serviva perche' la stagione visibile in pagina non era la stagione: era la
    stringa "25/26" battuta a mano nella barra in cima, nel piede della
    classifica e dentro la filigrana delle immagini scaricabili. Quattro punti,
    tre file, nessuno collegato a `SEASON_CORRENTE` — quindi al primo cambio di
    annata il sito avrebbe pubblicato la classifica del 2026/27 con "25/26"
    scritto sopra, che e' l'errore piu' facile da vedere e il piu' difficile da
    spiegare a chi arriva da un link.
    """
    s = SEASON_CORRENTE if season is None else season
    testo = str(s).strip()
    inizio, _, fine = testo.partition("-")
    if not fine or not inizio.isdigit():
        # Un formato che non conosciamo si mostra com'e': meglio una stagione
        # scritta male di una stagione inventata.
        return testo
    return f"{inizio[-2:] if breve else inizio}/{fine}"


# Le due forme gia' pronte, per non ripetere la chiamata in ogni template.
SEASON_ETICHETTA: str = etichetta_stagione()            # "2025/26"
SEASON_ETICHETTA_BREVE: str = etichetta_stagione(breve=True)  # "25/26"


def pretendi_stagione_coerente(stagione_payload, dove: str = "il payload") -> None:
    """Ferma il giro se i dati e l'etichetta parlano di due stagioni diverse.

    La classifica di una pagina viene dal payload; la stagione scritta nella
    barra in cima, nel piede e nella filigrana viene da SERIE_A_SEASON. Sono
    due strade per lo stesso numero, quindi possono divergere — e divergono
    esattamente quando fa piu' danno: rigenerando il sito dopo il cambio di
    annata con la variabile ancora ferma su quella vecchia. Ne esce una
    classifica 2026/27 con "25/26" scritto sopra, che nessun controllo sui dati
    puo' vedere perche' i dati sono giusti: e' l'etichetta a mentire.
    """
    if not stagione_payload:
        return
    if str(stagione_payload) == SEASON_CORRENTE:
        return
    raise SystemExit(
        f"\nStagione incoerente: {dove} e' {stagione_payload}, "
        f"SERIE_A_SEASON e' {SEASON_CORRENTE}.\n"
        f"La pagina uscirebbe con la classifica di una stagione e l'etichetta "
        f"dell'altra.\n"
        f"Imposta SERIE_A_SEASON={stagione_payload} e rilancia, oppure rigenera "
        f"i dati sulla stagione che vuoi pubblicare."
    )


def db_url(driver: str = "mysql+pymysql") -> str:
    """SQLAlchemy URL con password URL-encoded (gestisce '@', ':' nella pwd)."""
    override = os.environ.get("SERIE_A_DB_URL")
    if override:
        return override
    pwd = DB_PASSWORD or _require("DB_PASSWORD")
    return f"{driver}://{DB_USER}:{quote_plus(pwd)}@{DB_HOST}/{DB_NAME}"
