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
