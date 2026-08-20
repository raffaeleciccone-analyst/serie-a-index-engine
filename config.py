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
DB_NAME: str = os.environ.get("DB_NAME", "serie_a_25_26")
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


def db_url(driver: str = "mysql+pymysql") -> str:
    """SQLAlchemy URL con password URL-encoded (gestisce '@', ':' nella pwd)."""
    override = os.environ.get("SERIE_A_DB_URL")
    if override:
        return override
    pwd = DB_PASSWORD or _require("DB_PASSWORD")
    return f"{driver}://{DB_USER}:{quote_plus(pwd)}@{DB_HOST}/{DB_NAME}"
