"""Il selettore di stagione si costruisce dai dati, non da una tabella scritta a mano.

Il difetto che questi test bloccano: la stagione corrente era ricopiata in tre
posti — la tabella delle viste, il nome del CSV completo e la nota
dell'aggregato — e il conteggio dei qualificati in un quarto, dentro il testo
del link ("tutti i 351", quando erano gia' 354). Tre costanti da aggiornare a
mano ogni agosto, cioe' tre occasioni di dimenticarne una, piu' un numero che
si era gia' scollato dalla realta'.

Ora la stagione la dichiara il payload (`stagione`), le altre viste si
scoprono dai file presenti e il conteggio viene dal payload. Questi test
tengono ferma quella promessa: al cambio di stagione non si tocca codice.
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import parte2_dashboard as P  # noqa: E402


def _archivio(tmp_path, quanti: dict) -> Path:
    for nome, n in quanti.items():
        (tmp_path / nome).write_text(
            json.dumps({"players": [{"id": i} for i in range(n)]}), encoding="utf-8")
    return tmp_path


@pytest.fixture
def output_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(P, "OUTPUT_DIR", tmp_path)
    return tmp_path


def test_etichetta_e_nome_csv_seguono_la_stagione():
    assert P._et("2025-26") == "2025/26"
    assert P._nome_csv("2026-27") == "serie_a_tpi_2026-27.csv"
    # l'aggregato non e' una stagione: non puo' prendere il nome di nessuna
    assert P._nome_csv(None) == "serie_a_tpi_tutte-le-stagioni.csv"


def test_agosto_2026_non_richiede_modifiche_al_codice(output_dir):
    """La prova del nove: una stagione in piu' e nessuna riga da cambiare."""
    _archivio(output_dir, {
        "payload_lista.json": 380,                       # 2026-27, in corso
        "payload_lista_2025-26.json": 354,
        "payload_lista_2024-25.json": 356,
        "payload_lista_tutte-le-stagioni.json": 700,
    })
    voci = P._stagioni_disponibili("2026-27")
    assert [v["et_it"] for v in voci] == ["2026/27", "2025/26", "2024/25", "3 stagioni"]
    assert [v["n"] for v in voci] == [380, 354, 356, 700]
    # le stagioni concluse vengono dalla piu' recente alla piu' vecchia
    assert voci[1]["file"] == "payload_lista_2025-26.json"


def test_l_aggregato_dice_quali_stagioni_contiene(output_dir):
    _archivio(output_dir, {
        "payload_lista.json": 380,
        "payload_lista_2025-26.json": 354,
        "payload_lista_2024-25.json": 356,
        "payload_lista_tutte-le-stagioni.json": 700,
    })
    agg = P._stagioni_disponibili("2026-27")[-1]
    assert "2026/27, 2025/26 e 2024/25" in agg["nota_it"]
    # e non deve spacciarsi per una classifica di stagione
    assert "non e' la classifica" in agg["nota_it"].lower()


def test_una_vista_senza_file_non_viene_offerta(output_dir):
    """Meglio non offrirla che darle un 404 in faccia a chi ci clicca."""
    _archivio(output_dir, {"payload_lista.json": 380})
    voci = P._stagioni_disponibili("2026-27")
    assert [v["file"] for v in voci] == ["payload_lista.json"]


def test_un_file_illeggibile_non_ferma_il_build(output_dir):
    _archivio(output_dir, {"payload_lista.json": 380})
    (output_dir / "payload_lista_2025-26.json").write_text("{ non e' json", encoding="utf-8")
    voci = P._stagioni_disponibili("2026-27")
    assert [v["file"] for v in voci] == ["payload_lista.json"]


@pytest.mark.parametrize("voci,atteso", [
    ([], ""),
    (["2025/26"], "2025/26"),
    (["2025/26", "2024/25"], "2025/26 e 2024/25"),
    (["2026/27", "2025/26", "2024/25"], "2026/27, 2025/26 e 2024/25"),
])
def test_elenco_leggibile(voci, atteso):
    """Con due stagioni "a e b" bastava; con tre diventava "a e b e c"."""
    assert P._elenca(voci, "e") == atteso


def test_il_payload_dichiara_la_sua_stagione():
    """Senza questo campo le pagine tornerebbero a riscriversela a mano."""
    f = ROOT / "dashboard_output" / "payload.json"
    if not f.is_file():
        pytest.skip("payload non generato in questo ambiente")
    d = json.loads(f.read_text(encoding="utf-8"))
    assert "stagione" in d, "il payload deve dire a quale stagione si riferisce"
    assert d["stagione"] is None or "-" in d["stagione"]


# ── La lega, che ha appena smesso di essere una costante ────────────────────
import config  # noqa: E402


def test_lega_e_anno_vengono_da_config():
    """Erano scritti dentro parte4_aggiorna.py, nel punto in cui si scarica.

    Andava bene finche' il campionato era uno solo. Understat ne espone cinque
    con la stessa API: tenerli in config permette di servirne un altro con due
    variabili d'ambiente invece di duplicare il motore.
    """
    assert config.LEGA_UNDERSTAT == "ITA-Serie A"      # il default resta la Serie A
    assert config.anno_understat("2025-26") == 2025    # Understat vuole l'anno d'inizio
    assert config.anno_understat("2026-27") == 2026


def test_anno_understat_segue_la_stagione_corrente():
    assert config.anno_understat() == config.anno_understat(config.SEASON_CORRENTE)


@pytest.mark.parametrize("brutta", ["", "2025", "duemilaventicinque", None])
def test_una_stagione_illeggibile_si_lamenta_subito(brutta, monkeypatch):
    """Meglio fermarsi qui che scaricare l'annata sbagliata e accorgersene dopo."""
    if brutta is None:
        monkeypatch.setattr(config, "SEASON_CORRENTE", "non-una-stagione")
        with pytest.raises(ValueError):
            config.anno_understat()
        return
    if brutta == "2025":
        assert config.anno_understat("2025") == 2025   # tollerato: e' gia' l'anno
        return
    with pytest.raises(ValueError):
        config.anno_understat(brutta)


# ── La stagione in corso si dichiara ────────────────────────────────────────
# Il difetto che questi test bloccano: il sito apre sempre sulla stagione
# dichiarata dal payload, e a settembre quella e' una classifica di tre
# giornate. Senza una riga che lo dica, la pagina pubblica come definitivo un
# ordine che la validazione, due link piu' in la', misura a rho 0.29.

def test_la_stagione_in_corso_lo_dice_e_conta_le_giornate(output_dir):
    _archivio(output_dir, {"payload_lista.json": 120})
    voce = P._stagioni_disponibili(
        "2026-27", {"stagione_in_corso": True, "n_giornate": 3, "giornate_totali": 38})[0]
    assert voce["in_corso"] is True
    assert "3 giornate su 38" in voce["nota_it"]
    assert "provvisoria" in voce["nota_it"]
    assert "3 of 38 matchdays" in voce["nota_en"]


def test_la_stagione_conclusa_non_si_dichiara_in_corso(output_dir):
    _archivio(output_dir, {"payload_lista.json": 356})
    voce = P._stagioni_disponibili(
        "2025-26", {"stagione_in_corso": False, "n_giornate": 38, "giornate_totali": 38})[0]
    assert voce["in_corso"] is False
    assert "verifiche" in voce["nota_it"]


def test_senza_meta_la_voce_resta_quella_di_prima(output_dir):
    """I chiamanti vecchi non devono cambiare per una funzione in piu'."""
    _archivio(output_dir, {"payload_lista.json": 356})
    voce = P._stagioni_disponibili("2025-26")[0]
    assert voce["in_corso"] is False


def test_il_rho_si_cita_solo_se_e_quello_misurato(output_dir, monkeypatch):
    """Un rho interpolato sarebbe un numero che nessuno ha calcolato."""
    import json as _json
    _archivio(output_dir, {"payload_lista.json": 120})
    (output_dir / "validazione_sintesi.json").write_text(
        _json.dumps({"convergenza": {"prima_giornata": 3, "rho_prima": 0.287}}),
        encoding="utf-8")

    # giornata 3: e' esattamente quella misurata, il numero si puo' dire
    voce = P._stagioni_disponibili(
        "2026-27", {"stagione_in_corso": True, "n_giornate": 3, "giornate_totali": 38})[0]
    assert "0,29" in voce["nota_it"] and "0.29" in voce["nota_en"]

    # giornata 7: fra due vintage, nessuno l'ha misurata — si tace
    voce = P._stagioni_disponibili(
        "2026-27", {"stagione_in_corso": True, "n_giornate": 7, "giornate_totali": 38})[0]
    assert "rho" not in voce["nota_it"]
    assert "provvisoria" in voce["nota_it"]


def test_l_aggregato_conta_le_stagioni_che_contiene_non_i_file_accanto(output_dir):
    """L'etichetta ne annunciava tre mentre il file ne conteneva due.

    Succede da solo al cambio di stagione: la stagione nuova arriva, il file
    dell'aggregato no — e' fra quelli che vanno rigenerati a mano.
    """
    import json as _json
    _archivio(output_dir, {
        "payload_lista.json": 243,
        "payload_lista_2025-26.json": 356,
        "payload_lista_2024-25.json": 354,
    })
    (output_dir / "payload_lista_tutte-le-stagioni.json").write_text(
        _json.dumps({"stagioni_incluse": ["2024-25", "2025-26"],
                     "players": [{"id": i} for i in range(510)]}), encoding="utf-8")
    agg = P._stagioni_disponibili("2026-27")[-1]
    assert agg["et_it"] == "2 stagioni"
    assert "2026/27" not in agg["nota_it"]


def test_senza_dichiarazione_l_aggregato_torna_a_contare_i_file(output_dir):
    """I file vecchi non dichiarano niente: devono continuare a funzionare."""
    _archivio(output_dir, {
        "payload_lista.json": 243,
        "payload_lista_2025-26.json": 356,
        "payload_lista_tutte-le-stagioni.json": 510,
    })
    agg = P._stagioni_disponibili("2026-27")[-1]
    assert agg["et_it"] == "2 stagioni"
    assert "2026/27 e 2025/26" in agg["nota_it"]
