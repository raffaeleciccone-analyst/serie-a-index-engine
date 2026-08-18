"""Il ruolo pubblicato e' quello giocato davvero, e vale per tutte e due le liste.

Il difetto che questi test bloccano: il ruolo veniva risolto (posizioni
Understat + override) solo sul dataframe dell'analisi, mentre la rosa
pubblicata usava il campo grezzo dell'anagrafica. Nella stessa build Alex Meret
usciva DIF nella rosa del Napoli e POR fra i qualificati — e i portieri li
escludevamo filtrando proprio quel campo inaffidabile, cosi' Meret restava in
classifica con TPI nullo e Jay Idzes, difensore etichettato POR, spariva.
"""
import json
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from parte1_analisi import apply_role_pipeline, _role_key  # noqa: E402


class _Cfg:
    def __init__(self, override=None):
        self.ruolo_override = override or {}


def _frame():
    return pd.DataFrame(
        {
            "giocatore_id": [1, 2, 3],
            "giocatore": ["Alex Meret", "Jay Idzes", "Nessuno Ignoto"],
            "ruolo": ["DIF", "POR", None],
            "minuti": [982, 3065, 12],
        }
    )


def test_understat_vince_sul_ruolo_dell_anagrafica():
    df = apply_role_pipeline(
        _frame(), {"alex meret": "POR", "jay idzes": "DIF"}, _Cfg(), "test"
    )
    assert list(df["ruolo"][:2]) == ["POR", "DIF"]
    # chi non ha posizione Understat resta com'era: pandas lo tiene NaN
    assert pd.isna(df["ruolo"].iloc[2])


def test_override_manuale_vince_su_understat():
    df = apply_role_pipeline(
        _frame(), {"jay idzes": "DIF"}, _Cfg({"Jay Idzes": "CEN"}), "test"
    )
    assert df.loc[df["giocatore"] == "Jay Idzes", "ruolo"].iloc[0] == "CEN"


def test_i_nomi_si_agganciano_anche_con_gli_accenti():
    df = pd.DataFrame({"giocatore": ["Pervis Estupiñán"], "ruolo": ["CEN"]})
    df = apply_role_pipeline(df, {_role_key("Pervis Estupiñán"): "DIF"}, _Cfg())
    assert df["ruolo"].iloc[0] == "DIF"


def test_una_lista_senza_ruolo_non_si_rompe():
    df = pd.DataFrame({"nome": ["Tizio"], "minuti": [10]})
    assert apply_role_pipeline(df, {"tizio": "ATT"}, _Cfg()).equals(df)


# ── Gli invarianti sul payload pubblicato ────────────────────────────────
PAYLOADS = [
    p
    for p in (
        ROOT / "dashboard_output" / "payload.json",
        ROOT / "dashboard_output" / "payload_full.json",
    )
    if p.exists()
]


@pytest.mark.parametrize("path", PAYLOADS, ids=lambda p: p.name)
def test_payload_coerente_sui_ruoli(path):
    if not PAYLOADS:
        pytest.skip("payload non generato")
    testo = path.read_text(encoding="utf-8")
    # NaN non e' JSON: ci finiva il ruolo dei giocatori senza posizione nota,
    # e la pagina scriveva "NaN" accanto al nome.
    assert "NaN" not in testo
    d = json.loads(testo)

    for blocco in ("players", "roster"):
        ruoli = [r["ruolo"] for r in d[blocco]]
        assert all(isinstance(x, str) for x in ruoli), f"{blocco}: ruolo non stringa"
        assert "POR" not in ruoli, f"{blocco}: un portiere in un indice offensivo"

    # Un qualificato senza TPI vuol dire che e' entrato per sbaglio: era
    # esattamente il caso di Meret, 329o su 329 con tutti i contesti nulli.
    assert all(p["tpi"]["totale"] is not None for p in d["players"])

    # Le due liste devono dire lo stesso ruolo per la stessa persona.
    ruolo_roster = {r["id"]: r["ruolo"] for r in d["roster"]}
    disaccordi = [
        p["nome"]
        for p in d["players"]
        if p["id"] in ruolo_roster and ruolo_roster[p["id"]] != p["ruolo"]
    ]
    assert not disaccordi, f"ruolo diverso fra classifica e rosa: {disaccordi[:5]}"
