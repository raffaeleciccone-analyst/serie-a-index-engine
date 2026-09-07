"""Le pagine non pescano dalla stagione sbagliata quando ne comincia una nuova.

`payload_full.json` contiene tutti i qualificati invece dei primi cento, ed e'
per questo la fonte preferita della homepage e delle pagine squadra. Ma non si
rigenera al cambio di annata: e' l'ingresso del backtest, e il backtest gira
sulla stagione conclusa — la procedura dice esplicitamente di non rifarlo.

Quindi da settembre quel file e' la stagione scorsa. Preso com'era:

  · le ventisei pagine squadra sarebbero uscite con le rose e i numeri
    dell'anno prima sotto il titolo del campionato nuovo;
  · la homepage avrebbe confrontato la stagione vecchia con se stessa sotto un
    testo che annuncia il movimento di quella nuova.

Nessun controllo sui dati poteva vederlo: i numeri sono veri, e' l'annata a
essere un'altra. E le pagine delle squadre retrocesse restavano pubblicate, con
i dati vecchi, raggiungibili dal loro indirizzo anche senza un link.
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import config  # noqa: E402
import pagina_home  # noqa: E402
import pagina_squadra  # noqa: E402


def _payload(stagione, quanti, primo_id=0):
    return {"stagione": stagione,
            "players": [{"id": i, "nome": f"G{i}", "squadra": "Arsenal",
                         "rank": {"TPI": i + 1}}
                        for i in range(primo_id, primo_id + quanti)]}


@pytest.fixture
def uscita(tmp_path, monkeypatch):
    monkeypatch.setattr(pagina_home, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(pagina_squadra, "OUTPUT_DIR", tmp_path)
    return tmp_path


def _scrivi(cartella, nome, dati):
    (cartella / nome).write_text(json.dumps(dati), encoding="utf-8")


# ── homepage ────────────────────────────────────────────────────────────────

def test_la_homepage_ignora_il_payload_completo_di_un_altra_stagione(uscita):
    _scrivi(uscita, "payload_full.json", _payload("2025-26", 300))
    pay = _payload("2026-27", 100, primo_id=1000)
    fuori = pagina_home._classifica_completa(pay)
    assert len(fuori) == 100
    assert 1000 in fuori and 0 not in fuori


def test_la_homepage_usa_il_payload_completo_della_stessa_stagione(uscita):
    _scrivi(uscita, "payload_full.json", _payload("2026-27", 300))
    _scrivi(uscita, "payload.json", _payload("2026-27", 100, primo_id=1000))
    pay = _payload("2026-27", 100, primo_id=1000)
    fuori = pagina_home._classifica_completa(pay)
    assert len(fuori) == 300


# ── pagine squadra ──────────────────────────────────────────────────────────

def test_le_pagine_squadra_ignorano_il_completo_di_un_altra_stagione(uscita, monkeypatch):
    monkeypatch.setattr(config, "SEASON_CORRENTE", "2026-27")
    _scrivi(uscita, "payload_full.json", _payload("2025-26", 300))
    _scrivi(uscita, "payload.json", _payload("2026-27", 100, primo_id=1000))
    assert len(pagina_squadra._carica()["players"]) == 100


def test_le_pagine_squadra_si_fermano_se_la_stagione_non_e_quella_dichiarata(uscita, monkeypatch):
    monkeypatch.setattr(config, "SEASON_CORRENTE", "2025-26")
    _scrivi(uscita, "payload.json", _payload("2026-27", 100))
    with pytest.raises(SystemExit):
        pagina_squadra._carica()


# ── le squadre che non ci sono piu' ─────────────────────────────────────────

def test_le_squadre_retrocesse_non_restano_pubblicate(tmp_path, monkeypatch):
    uscita, repo = tmp_path / "out", tmp_path / "repo"
    uscita.mkdir(); repo.mkdir()
    monkeypatch.setattr(pagina_squadra, "OUTPUT_DIR", uscita)
    monkeypatch.setattr(pagina_squadra, "DEMO_DIR", repo)
    vive = {f"squadra-{i}.html" for i in range(20)}
    for cartella in (uscita, repo):
        for nome in vive | {"squadra-burnley.html", "squadra-west-ham.html"}:
            (cartella / nome).write_text("x", encoding="utf-8")

    pagina_squadra._togli_squadre_uscite(vive)

    for cartella in (uscita, repo):
        rimaste = {f.name for f in cartella.glob("squadra-*.html")}
        assert rimaste == vive


def test_un_giro_incompleto_non_cancella_niente(tmp_path, monkeypatch):
    """Quattro squadre nel payload sono un errore a monte, non diciotto retrocessioni."""
    uscita = tmp_path / "out"; uscita.mkdir()
    monkeypatch.setattr(pagina_squadra, "OUTPUT_DIR", uscita)
    monkeypatch.setattr(pagina_squadra, "DEMO_DIR", tmp_path / "assente")
    tutte = {f"squadra-{i}.html" for i in range(20)}
    for nome in tutte:
        (uscita / nome).write_text("x", encoding="utf-8")

    pagina_squadra._togli_squadre_uscite({"squadra-1.html", "squadra-2.html"})

    assert {f.name for f in uscita.glob("squadra-*.html")} == tutte
