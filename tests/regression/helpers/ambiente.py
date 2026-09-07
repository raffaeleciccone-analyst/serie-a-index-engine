"""Cosa serve a un test per poter dire qualcosa, e cosa fare quando non c'e'.

Alcune verifiche non girano sul codice: girano sui dati. Il confronto fra
vintage vuole la cartella `snapshots/`; i controlli di plausibilita' e
d'integrita' vogliono il database. Dove ci sono, misurano; dove non ci sono,
non c'e' niente da misurare.

Il difetto che questo file chiude: fino a ieri, senza quei due, quei test non
si limitavano a non dire niente — **fallivano**. Trentuno rossi su un clone
pulito, con dentro "Access denied for user 'root'@'localhost'", che sembra un
guasto del progetto e invece e' l'assenza di una password. Uno addirittura si
piantava, perche' il connettore MySQL riprova finche' non scade il tempo: su
una vetrina pubblica, dove la prima cosa che uno fa e' clonare e lanciare i
test, la promessa del progetto e' proprio che le verifiche si possono rilanciare.

Un test che fallisce perche' i dati non ci sono non dice niente, e nasconde
quelli che invece qualcosa la dicono. Quindi si salta, dichiarando cosa manca.
Dove i dati ci sono — cioe' nel motore — non cambia niente: girano tutti.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

RADICE = Path(__file__).resolve().parents[3]


def _snapshot_presenti() -> bool:
    """La cartella con le fotografie del backtest, e almeno una dentro."""
    cartella = RADICE / "snapshots"
    return cartella.is_dir() and any(cartella.glob("*/giornata_*"))


def _database_configurato() -> bool:
    """Una password c'e'? Non si prova a connettersi: costa e puo' bloccare.

    `config` carica `.env` nell'ambiente, e senza `.env` la password resta
    vuota — che e' esattamente la condizione di chi ha appena clonato.
    """
    try:
        import config  # noqa: F401  (e' l'import che legge .env)
    except Exception:
        pass
    return bool(os.environ.get("DB_PASSWORD", "").strip())


SNAPSHOT_PRESENTI = _snapshot_presenti()
DATABASE_CONFIGURATO = _database_configurato()

senza_snapshot = pytest.mark.skipif(
    not SNAPSHOT_PRESENTI,
    reason="serve la cartella snapshots/ con almeno una giornata: "
           "queste verifiche confrontano due fotografie dell'indice")

# `anomaly_runner.py` vive in `snapshots/`, quindi sparisce insieme a quella
# cartella. Sono due test su trentadue: il resto del file gira benissimo senza,
# e marcare tutto il modulo ne avrebbe zittiti trenta che qualcosa da dire ce
# l'hanno.
RUNNER_ANOMALIE = (RADICE / "snapshots" / "anomaly_runner.py").is_file()

senza_runner_anomalie = pytest.mark.skipif(
    not RUNNER_ANOMALIE,
    reason="serve snapshots/anomaly_runner.py: e' il lanciatore, non il motore")

senza_database = pytest.mark.skipif(
    not DATABASE_CONFIGURATO,
    reason="serve un database configurato (DB_PASSWORD in .env): "
           "queste verifiche interrogano i dati veri, non un campione")
