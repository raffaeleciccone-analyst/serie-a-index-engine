"""Il nome del sito e la stagione visibile arrivano dalla configurazione.

I due difetti che questi test bloccano, tutti e due invisibili fino al primo
giro dopo il cambio di annata:

1. **Il nome.** Il sito della Premier e' stato rinominato "Premier League
   Index" a mano su ventisei pagine nel repo pubblicato. Il motore continuava a
   comporre "Premier League Scout": la prima rigenerazione avrebbe rimesso il
   vecchio nome ovunque, in silenzio. Il nome per intero e il marchio della
   barra adesso si dichiarano in `config.IDENTITA_LEGA`, e nessuno riscrive a
   mano la parola "Index".

2. **La stagione.** "25/26" era battuto a mano nella barra, nel piede e nella
   filigrana delle immagini scaricabili. Adesso viene da `SERIE_A_SEASON`, e
   se i dati dicono un'altra stagione il giro si ferma invece di pubblicare una
   classifica con l'etichetta sbagliata.
"""
import importlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import config  # noqa: E402


def _config_di(lega: str, stagione: str, monkeypatch):
    """config ricaricato come se lo avesse importato un giro su quella lega."""
    monkeypatch.setenv("SERIE_A_LEGA", lega)
    monkeypatch.setenv("SERIE_A_SEASON", stagione)
    return importlib.reload(config)


@pytest.fixture(autouse=True)
def config_pulito():
    """Ogni test lascia il modulo com'era: gli altri lo importano gia' carico."""
    yield
    importlib.reload(config)


# ── Il nome del sito ────────────────────────────────────────────────────────

def test_il_sito_della_premier_si_chiama_premier_league_index(monkeypatch):
    c = _config_di("ENG-Premier League", "2026-27", monkeypatch)
    assert c.SITO_NOME == "Premier League Index"
    # nella barra ci sta per intero: "Premier League" da solo e' il campionato
    assert c.SITO_MARCHIO == "Premier League Index"


def test_il_sito_della_serie_a_non_cambia_nome(monkeypatch):
    c = _config_di("ITA-Serie A", "2025-26", monkeypatch)
    assert c.SITO_NOME == "Serie A Scout Index"
    assert c.SITO_MARCHIO == "Serie A Scout"


@pytest.mark.parametrize("lega", list(config.IDENTITA_LEGA))
def test_nessun_nome_finisce_con_index_due_volte(lega, monkeypatch):
    """Le pagine scrivono il nome cosi' com'e': "Index" non si riattacca."""
    c = _config_di(lega, "2025-26", monkeypatch)
    assert c.SITO_NOME.endswith("Index")
    assert not c.SITO_NOME.endswith("Index Index")


def test_il_titolo_dell_eroe_spezza_il_nome_senza_riscriverlo(monkeypatch):
    """L'ultima parola va in corsivo sotto: si prende, non si ribatte."""
    _config_di("ENG-Premier League", "2026-27", monkeypatch)
    import pagina_home
    importlib.reload(pagina_home)
    assert pagina_home._titolo_hero() == "Premier League<br><em>Index</em>"

    _config_di("ITA-Serie A", "2025-26", monkeypatch)
    importlib.reload(pagina_home)
    assert pagina_home._titolo_hero() == "Serie A Scout<br><em>Index</em>"


# ── La stagione visibile ────────────────────────────────────────────────────

@pytest.mark.parametrize("season, lunga, breve", [
    ("2025-26", "2025/26", "25/26"),
    ("2026-27", "2026/27", "26/27"),
    ("2030-31", "2030/31", "30/31"),
])
def test_l_etichetta_della_stagione_si_calcola(season, lunga, breve):
    assert config.etichetta_stagione(season) == lunga
    assert config.etichetta_stagione(season, breve=True) == breve


def test_una_stagione_scritta_male_si_mostra_com_e():
    """Meglio una stagione scritta male di una inventata."""
    assert config.etichetta_stagione("boh") == "boh"


def test_la_barra_segue_la_stagione_senza_toccare_il_codice(monkeypatch):
    c = _config_di("ENG-Premier League", "2026-27", monkeypatch)
    import pagina_stile
    importlib.reload(pagina_stile)
    barra = pagina_stile.nav("index.html")
    assert ">Premier League Index <small>26/27</small><" in barra
    assert "25/26" not in barra
    assert 'title="Premier League Index"' in barra


# ── I dati e l'etichetta parlano della stessa stagione ──────────────────────

def test_una_stagione_diversa_da_quella_dichiarata_ferma_il_giro(monkeypatch):
    c = _config_di("ENG-Premier League", "2025-26", monkeypatch)
    with pytest.raises(SystemExit) as e:
        c.pretendi_stagione_coerente("2026-27", "payload.json")
    assert "2026-27" in str(e.value) and "2025-26" in str(e.value)


def test_la_stagione_che_coincide_non_dice_niente(monkeypatch):
    c = _config_di("ENG-Premier League", "2026-27", monkeypatch)
    assert c.pretendi_stagione_coerente("2026-27") is None
    # un payload che non dichiara la stagione e' un payload vecchio, non un errore
    assert c.pretendi_stagione_coerente(None) is None
