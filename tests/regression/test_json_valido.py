"""I payload sono JSON che un browser accetta, non JSON che accetta Python.

Il difetto che questi test bloccano: `deep_clean` guardava solo stringhe,
dizionari e liste, quindi un float NaN ci passava attraverso intatto, e
`json.dumps` lo scriveva come `NaN`. Python quel file lo rilegge senza fiatare;
i browser no — `NaN` non e' JSON valido, e `JSON.parse` muore sul primo.

Sei ruoli mancanti bastavano a rendere illeggibile un file da mezzo mega. Sul
sito pubblicato erano payload_lista_2024-25.json e
payload_lista_tutte-le-stagioni.json: chi cambiava stagione o chiedeva "Sopra
le attese" prendeva un SyntaxError e nessuna risposta. Il payload dei primi
cento non ne conteneva, quindi la pagina sembrava sana — e l'unico avviso di
quel guasto era un tooltip, quindi non lo sapeva nessuno.
"""
import json
import math
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import config  # noqa: E402
from parte1_analisi import deep_clean  # noqa: E402


def _severo(testo: str):
    """json.loads che rifiuta NaN e Infinity, come fa un browser."""
    def esplodi(costante):
        raise ValueError(f"valore non JSON: {costante}")
    return json.loads(testo, parse_constant=esplodi)


def test_deep_clean_toglie_i_nan():
    dentro = {"ruolo": float("nan"), "tpi": 1.5, "nome": "Tizio"}
    fuori = deep_clean(dentro)
    assert fuori["ruolo"] is None
    assert fuori["tpi"] == 1.5
    assert fuori["nome"] == "Tizio"


def test_deep_clean_toglie_anche_gli_infiniti():
    fuori = deep_clean({"a": float("inf"), "b": float("-inf")})
    assert fuori == {"a": None, "b": None}


def test_deep_clean_scende_in_liste_e_dizionari_annidati():
    dentro = {"players": [{"kpi": {"xg": float("nan")}}, {"kpi": {"xg": 0.4}}]}
    fuori = deep_clean(dentro)
    assert fuori["players"][0]["kpi"]["xg"] is None
    assert fuori["players"][1]["kpi"]["xg"] == 0.4


def test_il_risultato_e_leggibile_da_un_browser():
    testo = json.dumps(deep_clean({"ruolo": float("nan")}), allow_nan=False)
    assert _severo(testo) == {"ruolo": None}


def test_senza_pulizia_il_file_non_e_json():
    """Il comportamento di prima, per far vedere che il test morde."""
    testo = json.dumps({"ruolo": float("nan")})
    assert "NaN" in testo
    with pytest.raises(ValueError):
        _severo(testo)


FILE_ATTESI = ("payload.json", "payload_lista.json",
               "payload_lista_2024-25.json", "payload_lista_tutte-le-stagioni.json")

# I repo di pubblicazione si chiedono a config invece di scriverli a mano: se
# uno viene rinominato, questa lista lo segue da sola.
REPO = sorted(set(config.REPO_PUBBLICAZIONE.values()))


def _payload_attesi():
    """I payload da controllare, con scritto il motivo quando uno non c'e'.

    Prima la lista era filtrata con `if p.is_file()`, e i file mancanti
    sparivano in silenzio. Quando la cartella della Premier si chiamava ancora
    `premier-league-scout-index`, quattro test non venivano piu' raccolti: la
    suite diceva «tutto verde» con 172 test invece di 176, e nessuno controllava
    piu' i payload della Premier. Un file che manca ora e' un caso dichiarato —
    saltato con un motivo scritto, oppure un errore — mai un test che scompare.
    """
    for repo in REPO:
        cartella = ROOT.parent / repo
        for nome in FILE_ATTESI:
            yield pytest.param(
                cartella / nome,
                marks=pytest.mark.skipif(
                    not cartella.is_dir(),
                    reason=f"il repo {repo} non c'e' su questa macchina: lega mai pubblicata"),
                id=f"{repo}/{nome}")


@pytest.mark.parametrize("percorso", list(_payload_attesi()))
def test_i_payload_pubblicati_sono_json_valido(percorso):
    """Ogni file che una pagina scarica deve poter essere letto da JSON.parse."""
    assert percorso.is_file(), (
        f"manca {percorso.name} in {percorso.parent.name}. Il repo c'e', quindi quella "
        f"lega e' pubblicata: un payload che manca e' una pagina che non carica.")
    _severo(percorso.read_text(encoding="utf-8"))
