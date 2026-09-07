"""La vista di una stagione si puo' mandare a qualcuno con un link.

La classifica si apre sulla stagione in corso. A settembre quella e' tre
giornate: onesta — la pagina dichiara su quante e' calcolata — ma non e' quello
che vuoi mostrare a chi arriva la prima volta da un link esterno. La vista su
piu' stagioni ha molti piu' minuti per giocatore ed esisteva gia', ma era
raggiungibile solo premendo un pulsante: nessun indirizzo la apriva, quindi non
si poteva linkare.

Ora ogni voce del selettore ha uno `slug` e l'indirizzo la sceglie
(`?stagione=tutte`). Questi test guardano il lato che genera la pagina; il
comportamento nel browser e' stato verificato a mano su un server locale:
`?stagione=tutte` apre le tre stagioni insieme (474 giocatori), uno slug che non
esiste ricade sulla stagione corrente senza mostrare errori, e premere un
pulsante riscrive l'indirizzo.
"""
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import config  # noqa: E402

# Il repo del sito si chiede a `config`, non si scrive qui: un percorso assoluto
# in un test e' un percorso che vale su una macchina sola — e questo file
# finisce anche nella copia pubblica del motore.
_LEGA = "ENG-Premier League"
PAGINA = (ROOT.parent / config.REPO_PUBBLICAZIONE[_LEGA]
          / ("dashboard_%s.html" % config.IDENTITA_LEGA[_LEGA]["slug"]))

pytestmark = pytest.mark.skipif(
    not PAGINA.is_file(),
    reason="serve la dashboard pubblicata: e' un controllo sul sito, non sul codice")


def _stagioni():
    testo = PAGINA.read_text(encoding="utf-8", errors="replace")
    m = re.search(r"const STAGIONI = (\[.*?\]);", testo, re.S)
    assert m, "la pagina non dichiara piu' STAGIONI"
    return json.loads(m.group(1)), testo


def test_ogni_vista_ha_un_nome_per_l_indirizzo():
    stagioni, _ = _stagioni()
    assert stagioni, "nessuna stagione nel selettore"
    for s in stagioni:
        assert s.get("slug"), "una vista senza slug non si puo' linkare: %r" % s.get("et_it")


def test_la_vista_su_piu_stagioni_si_chiama_tutte():
    """E' l'indirizzo che si mette in un post: deve restare questo."""
    stagioni, _ = _stagioni()
    assert "tutte" in [s.get("slug") for s in stagioni]


def test_gli_slug_non_si_ripetono():
    stagioni, _ = _stagioni()
    slug = [s["slug"] for s in stagioni]
    assert len(slug) == len(set(slug)), "due viste con lo stesso slug: %s" % slug


def test_la_pagina_legge_e_riscrive_l_indirizzo():
    _, testo = _stagioni()
    for pezzo in ("_slugStagione", "_indiceDaUrl", "_scriviUrl", "replaceState"):
        assert pezzo in testo, "manca %s: l'indirizzo non verrebbe letto o scritto" % pezzo
