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
DB_PASSWORD: str = _require("DB_PASSWORD")

# Stagione che il sito pubblica. Da quando il DB contiene anche il backfill
# 2024-25, "quale stagione" non e' piu' una domanda con una risposta ovvia:
# parte1 senza filtro le aggregava tutte e produceva una classifica che
# sembrava plausibile ma non era di nessuna stagione. Il valore sta qui perche'
# a cambio stagione si tocca un punto solo.
SEASON_CORRENTE: str = os.environ.get("SERIE_A_SEASON", "2025-26")


def db_url(driver: str = "mysql+pymysql") -> str:
    """SQLAlchemy URL con password URL-encoded (gestisce '@', ':' nella pwd)."""
    override = os.environ.get("SERIE_A_DB_URL")
    if override:
        return override
    return f"{driver}://{DB_USER}:{quote_plus(DB_PASSWORD)}@{DB_HOST}/{DB_NAME}"
