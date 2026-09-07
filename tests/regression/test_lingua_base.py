"""Il sito si apre nella lingua della sua lega, e il testo a schermo la segue.

Il difetto che questi test bloccano: la lingua era scritta a mano in due punti
(`<html lang="it">` nel guscio e nel template della dashboard) e una terza
volta dentro i18n.js (`DEFAULT_LANG = "it"`). Il motore serve due campionati,
ma il sito sulla Premier League apriva in italiano — e apriva in italiano
proprio davanti a chi ha il browser in italiano, cioe' davanti a noi.

E il testo fra i tag, quello che si vede prima che lo script parta e quello che
leggono i motori di ricerca, era l'italiano su tutte e due i siti.
"""
import importlib
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def _ricarica(lega: str, monkeypatch):
    """config e chi lo legge, riletti con un'altra lega."""
    monkeypatch.setenv("SERIE_A_LEGA", lega)
    import config
    importlib.reload(config)
    import pagina_stile
    importlib.reload(pagina_stile)
    return config, pagina_stile


@pytest.fixture(autouse=True)
def _ripristina(monkeypatch):
    yield
    monkeypatch.delenv("SERIE_A_LEGA", raising=False)
    import config
    importlib.reload(config)
    import pagina_stile
    importlib.reload(pagina_stile)


def test_la_premier_dichiara_inglese(monkeypatch):
    cfg, ps = _ricarica("ENG-Premier League", monkeypatch)
    assert cfg.LINGUA == "en"
    html = ps.guscio("T", "d_it", "d_en", "index.html", "", [])
    assert '<html lang="en">' in html


def test_la_serie_a_resta_in_italiano(monkeypatch):
    cfg, ps = _ricarica("ITA-Serie A", monkeypatch)
    assert cfg.LINGUA == "it"
    html = ps.guscio("T", "d_it", "d_en", "index.html", "", [])
    assert '<html lang="it">' in html


def test_il_testo_visibile_segue_la_lingua_del_sito(monkeypatch):
    _, ps = _ricarica("ENG-Premier League", monkeypatch)
    frammento = f'<p {ps.bi("Chi c\'e\' in cima", "Who is on top")}>Chi c\'e\' in cima</p>'
    fuori = ps.testo_base(frammento)
    assert ">Who is on top<" in fuori
    # i due attributi restano intatti: il selettore deve poter tornare indietro
    assert 'data-it="Chi c\'e\' in cima"' in fuori
    assert 'data-en="Who is on top"' in fuori


def test_in_italiano_non_tocca_niente(monkeypatch):
    _, ps = _ricarica("ITA-Serie A", monkeypatch)
    frammento = f'<p {ps.bi("Chi c\'e\' in cima", "Who is on top")}>Chi c\'e\' in cima</p>'
    assert ps.testo_base(frammento) == frammento


def test_el_scrive_la_lingua_del_sito(monkeypatch):
    _, ps = _ricarica("ENG-Premier League", monkeypatch)
    assert ps.el("h2", "Quanto regge", "How well it holds").endswith(
        ">How well it holds</h2>")


def test_non_tocca_il_testo_che_non_e_quello_dell_attributo(monkeypatch):
    """Se fra i tag c'e' altro, si lascia stare: si sostituisce solo l'esatto."""
    _, ps = _ricarica("ENG-Premier League", monkeypatch)
    frammento = '<p data-it="Uno" data-en="One">Tutt\'altro testo</p>'
    assert ps.testo_base(frammento) == frammento
