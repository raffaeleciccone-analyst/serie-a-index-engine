"""La sequenza di pubblicazione, e il controllo che nessun passo sia saltato.

Il difetto che questi test bloccano non e' in una riga di codice: e' nella
procedura. Tredici passi in un ordine preciso, e quando se ne salta uno non
esce nessun errore — esce un sito con meta' pagine su una stagione e meta'
sull'altra, che e' proprio il tipo di guasto che si scopre da un lettore.

Due punti meritano un test per conto loro:

· **l'archivio.** Va fatto prima di `parte1_analisi.py`, che sovrascrive
  `payload.json`. Rifatto dopo, archivierebbe la stagione nuova sotto il nome
  di quella vecchia — cancellando l'unica copia della stagione conclusa, che e'
  quella su cui gira il backtest.
· **la verifica finale.** E' l'unico punto in cui qualcuno confronta le pagine
  fra loro: ogni script guarda solo la propria.
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import config  # noqa: E402
import pubblica as P  # noqa: E402


# ── i nomi delle leghe ──────────────────────────────────────────────────────

@pytest.mark.parametrize("scritto", ["premier", "Premier", "premier-league",
                                     "ENG-Premier League", "eng"])
def test_la_premier_si_chiama_in_tanti_modi(scritto):
    assert P.risolvi_lega(scritto) == "ENG-Premier League"


@pytest.mark.parametrize("scritto", ["serie-a", "seriea", "Serie_A", "ITA-Serie A"])
def test_anche_la_serie_a(scritto):
    assert P.risolvi_lega(scritto) == "ITA-Serie A"


def test_una_lega_che_non_esiste_lo_dice_subito():
    with pytest.raises(SystemExit) as e:
        P.risolvi_lega("bundesliga")
    assert "premier" in str(e.value)


# ── l'archivio ──────────────────────────────────────────────────────────────

def _payload(cartella, nome, stagione):
    (cartella / nome).write_text(json.dumps({"stagione": stagione, "players": []}),
                                 encoding="utf-8")


def test_archivia_la_stagione_conclusa(tmp_path):
    for nome in ("payload.json", "payload_lista.json", "payload_full.json"):
        _payload(tmp_path, nome, "2025-26")

    P.archivia(tmp_path, "2026-27", esegui=True)

    for nome in ("payload_2025-26.json", "payload_lista_2025-26.json",
                 "payload_full_2025-26.json"):
        assert (tmp_path / nome).is_file(), nome
    # gli originali restano: li sovrascrivera' parte1, non l'archivio
    assert (tmp_path / "payload.json").is_file()


def test_rilanciarlo_dopo_il_cambio_non_cancella_niente(tmp_path):
    """Il caso che costa caro: l'archivio rifatto a stagione gia' cambiata."""
    _payload(tmp_path, "payload.json", "2026-27")
    (tmp_path / "payload_2025-26.json").write_text(
        json.dumps({"stagione": "2025-26", "players": [{"id": 1}]}), encoding="utf-8")

    righe = P.archivia(tmp_path, "2026-27", esegui=True)

    assert "niente da archiviare" in " ".join(righe)
    dentro = json.loads((tmp_path / "payload_2025-26.json").read_text(encoding="utf-8"))
    assert dentro["stagione"] == "2025-26"


def test_un_archivio_che_esiste_non_si_sovrascrive(tmp_path):
    _payload(tmp_path, "payload.json", "2025-26")
    (tmp_path / "payload_2025-26.json").write_text("intoccabile", encoding="utf-8")

    righe = P.archivia(tmp_path, "2026-27", esegui=True)

    assert "non si sovrascrive" in " ".join(righe)
    assert (tmp_path / "payload_2025-26.json").read_text(encoding="utf-8") == "intoccabile"


def test_senza_esegui_non_copia_niente(tmp_path):
    _payload(tmp_path, "payload.json", "2025-26")
    P.archivia(tmp_path, "2026-27", esegui=False)
    assert not (tmp_path / "payload_2025-26.json").exists()


# ── la verifica finale ──────────────────────────────────────────────────────

def _pagina(cartella, nome, marchio, stagione_breve):
    (cartella / nome).write_text(
        '<a class="nav-brand" href="index.html">%s <small>%s</small></a>'
        % (marchio, stagione_breve), encoding="utf-8")


@pytest.fixture
def sito(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SITO_MARCHIO", "Premier League Index")
    repo, uscita = tmp_path / "repo", tmp_path / "uscita"
    repo.mkdir(); uscita.mkdir()
    return repo, uscita


def test_un_sito_coerente_non_ha_niente_da_dire(sito):
    repo, uscita = sito
    for nome in ("index.html", "validazione.html", "guida_completa.html"):
        _pagina(repo, nome, "Premier League Index", "26/27")
    assert P.verifica(repo, uscita, "2026-27") == []


def _pagina_dal_payload(cartella, nome, marchio):
    """Come la pagina Pro: la barra e' vuota e la stagione arriva a runtime."""
    (cartella / nome).write_text(
        '<a class="nav-brand" href="index.html">%s '
        '<small data-st-it="{curBreve}"></small></a>' % marchio, encoding="utf-8")


def test_una_pagina_che_prende_la_stagione_dal_payload_non_e_un_guaio(sito):
    """La Pro non ha piu' la stagione scritta dentro: la scrive il payload.

    Non c'e' niente da confrontare con le altre pagine, e pretendere una
    scritta fissa vorrebbe dire chiederle di tornare al difetto di prima.
    """
    repo, uscita = sito
    _pagina(repo, "index.html", "Premier League Index", "26/27")
    _pagina_dal_payload(repo, "dashboard_pro.html", "Premier League Index")

    assert P.verifica(repo, uscita, "2026-27") == []


def test_una_barra_vuota_senza_segnaposto_si_vede(sito):
    """Vuota e basta e' un'altra cosa: la stagione l'ha persa e nessuno la mette."""
    repo, uscita = sito
    _pagina(repo, "index.html", "Premier League Index", "26/27")
    _pagina(repo, "dashboard_pro.html", "Premier League Index", "")

    guai = P.verifica(repo, uscita, "2026-27")

    assert len(guai) == 1 and "dashboard_pro.html" in guai[0]


def test_una_barra_con_attributi_resta_sotto_controllo(sito):
    """La regex allargata non deve diventare un buco.

    Prima pretendeva `<small>` senza attributi: la pagina Pro, che ne ha uno,
    usciva dal controllo in silenzio e la verifica passava guardando una
    pagina in meno. Con gli attributi ammessi, una stagione sbagliata li'
    dentro si vede come su ogni altra pagina.
    """
    repo, uscita = sito
    _pagina(repo, "index.html", "Premier League Index", "26/27")
    (repo / "dashboard_pro.html").write_text(
        '<a class="nav-brand" href="index.html">Premier League Index '
        '<small data-st-it="{curBreve}">25/26</small></a>', encoding="utf-8")

    guai = P.verifica(repo, uscita, "2026-27")

    assert len(guai) == 1
    assert "25/26" in guai[0] and "dashboard_pro.html" in guai[0]


def test_la_pagina_rimasta_indietro_si_vede(sito):
    """E' il passo saltato: la validazione non riscritta dice ancora 25/26."""
    repo, uscita = sito
    _pagina(repo, "index.html", "Premier League Index", "26/27")
    _pagina(repo, "validazione.html", "Premier League Index", "25/26")

    guai = P.verifica(repo, uscita, "2026-27")

    assert len(guai) == 1
    assert "25/26" in guai[0] and "validazione.html" in guai[0]


def test_il_nome_vecchio_rimasto_addosso_si_vede(sito):
    repo, uscita = sito
    _pagina(repo, "index.html", "Premier League Index", "26/27")
    _pagina(repo, "guida_completa.html", "Premier League Scout", "26/27")

    guai = P.verifica(repo, uscita, "2026-27")

    assert any("Premier League Scout" in g and "guida_completa.html" in g for g in guai)


def test_un_payload_di_un_altra_stagione_si_vede(sito):
    repo, uscita = sito
    _pagina(repo, "index.html", "Premier League Index", "26/27")
    (repo / "payload.json").write_text(
        json.dumps({"stagione": "2025-26", "players": []}), encoding="utf-8")

    guai = P.verifica(repo, uscita, "2026-27")

    assert any("payload.json" in g for g in guai)


def test_le_pagine_delle_squadre_uscite_si_vedono(sito):
    repo, uscita = sito
    _pagina(repo, "index.html", "Premier League Index", "26/27")
    (repo / "payload.json").write_text(json.dumps({
        "stagione": "2026-27",
        "players": [{"id": 1, "squadra": "Arsenal"}, {"id": 2, "squadra": "Hull"}],
    }), encoding="utf-8")
    for nome in ("squadra-arsenal.html", "squadra-hull.html", "squadra-burnley.html"):
        _pagina(repo, nome, "Premier League Index", "26/27")

    guai = P.verifica(repo, uscita, "2026-27")

    assert any("squadra-burnley.html" in g for g in guai)
    assert not any("squadra-arsenal.html" in g for g in guai)


# ── l'avviso di fine giro ───────────────────────────────────────────────────
# E' un avviso, non un passo della procedura: se il bot non c'e', o Telegram non
# risponde, la pubblicazione e' comunque andata a buon fine e non deve
# risultare fallita per un messaggio non partito.

def test_un_avviso_non_mandato_non_fa_fallire_niente(monkeypatch, capsys):
    import sentinella
    monkeypatch.setattr(sentinella, "manda",
                        lambda t: (_ for _ in ()).throw(SystemExit("niente token")))
    P._avvisa("qualcosa")
    assert "non mandato" in capsys.readouterr().out


def test_un_telegram_che_esplode_non_fa_fallire_niente(monkeypatch, capsys):
    import sentinella
    monkeypatch.setattr(sentinella, "manda",
                        lambda t: (_ for _ in ()).throw(OSError("rete giu'")))
    P._avvisa("qualcosa")
    assert "non mandato" in capsys.readouterr().out


def test_quando_il_bot_c_e_il_messaggio_parte(monkeypatch, capsys):
    import sentinella
    mandati = []
    monkeypatch.setattr(sentinella, "manda", mandati.append)
    P._avvisa("pubblicata")
    assert mandati == ["pubblicata"]
    assert "avviso mandato" in capsys.readouterr().out
