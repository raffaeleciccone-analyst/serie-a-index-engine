"""Ogni lega scrive nelle proprie cartelle, e in nessun'altra.

Il difetto che questi test bloccano: `DEMO_DIR` — la cartella del repo da cui
si committa il sito — era `serie-a-index` scritta uguale in cinque moduli,
qualunque fosse il campionato. Generare la Premier scriveva le pagine della
Premier dentro il repo della Serie A.

E' successo davvero, su tutti e tre i moduli:
  · parte3_valida_tpi ha messo `validazione.html` della Premier sopra quella
    del Serie A — pagina intitolata "Premier League Scout Index", 343 giocatori
    invece di 354, rho 0.803 invece di 0.75, sul progetto la cui unica promessa
    e' pubblicare tutte le proprie verifiche;
  · pagina_home ha fatto lo stesso con `index.html`;
  · parte2_dashboard ci ha depositato accanto la dashboard inglese.
"""
import importlib
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

MODULI_CHE_PUBBLICANO = ["pagina_home", "pagina_guida", "pagina_squadra",
                         "parte2_dashboard", "parte3_valida_tpi"]


def _con_lega(lega: str, monkeypatch):
    monkeypatch.setenv("SERIE_A_LEGA", lega)
    monkeypatch.delenv("SERIE_A_DEMO_DIR", raising=False)
    import config
    importlib.reload(config)
    return config


@pytest.fixture(autouse=True)
def _ripristina(monkeypatch):
    yield
    monkeypatch.delenv("SERIE_A_LEGA", raising=False)
    import config
    importlib.reload(config)


@pytest.mark.parametrize("lega,atteso", [
    ("ITA-Serie A", "serie-a-index"),
    ("ENG-Premier League", "premier-league-index"),
])
def test_ogni_lega_ha_il_suo_repo(lega, atteso, monkeypatch):
    cfg = _con_lega(lega, monkeypatch)
    assert cfg.cartella_pubblicazione().name == atteso


def test_una_lega_nuova_non_finisce_nel_repo_di_un_altra(monkeypatch):
    """Meglio una cartella che non esiste che scrivere in casa d'altri."""
    cfg = _con_lega("ESP-La Liga", monkeypatch)
    nome = cfg.cartella_pubblicazione().name
    assert nome not in ("serie-a-index", "premier-league-index")
    assert nome == "la-liga-index"


def test_le_due_leghe_non_condividono_nessuna_cartella(monkeypatch):
    ita = _con_lega("ITA-Serie A", monkeypatch)
    coppia_ita = (ita.cartella_uscita(), ita.cartella_pubblicazione())
    eng = _con_lega("ENG-Premier League", monkeypatch)
    coppia_eng = (eng.cartella_uscita(), eng.cartella_pubblicazione())
    assert not set(coppia_ita) & set(coppia_eng), \
        f"cartella in comune fra le due leghe: {set(coppia_ita) & set(coppia_eng)}"


@pytest.mark.parametrize("modulo", MODULI_CHE_PUBBLICANO)
def test_nessun_modulo_si_scrive_il_repo_a_mano(modulo):
    """La cartella la chiede al config: se torna a essere una costante, qui si vede."""
    testo = (ROOT / f"{modulo}.py").read_text(encoding="utf-8")
    righe = [r for r in testo.splitlines()
             if r.strip().startswith("DEMO_DIR") and "=" in r]
    assert righe, f"{modulo}: DEMO_DIR non trovata"
    for r in righe:
        assert "cartella_pubblicazione" in r, f"{modulo}: repo scritto a mano -> {r.strip()}"
