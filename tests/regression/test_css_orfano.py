"""Ogni classe scritta nelle pagine ha una regola che la disegna.

Il difetto che questi test bloccano: la home e la guida si attaccavano il
proprio CSS con `html.replace("</style>", CSS_EXTRA + "</style>")`. Reggeva
finche' il guscio portava il foglio dentro un <style>; quando il CSS e' uscito
nel file esterno per alleggerire il sito, quel `replace` non ha piu' trovato
niente da sostituire — e `str.replace` senza riscontro non solleva niente, non
avverte, restituisce la stringa com'era.

Il CSS di due pagine e' sparito senza un errore. Sulla home sono rimaste senza
regola quindici classi (`clas`, `cl-row`, `cl-n`, `cl-id`, `cl-bar`, `cl-v`,
`porte`, `porte-g`, `porte-lbl`, `porta`, `porta-t`, `porta-d`, `pri`,
`oltre`, `let-nm`): la classifica d'apertura e i tasti d'ingresso venivano
resi come testo `display:inline`, cioe' un paragrafo di nomi appiccicati.
Sulla guida sono rimaste `form`, `form-big` e `oltre`, cioe' i riquadri delle
formule — il contenuto per cui quella pagina esiste.
"""
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import pagina_stile  # noqa: E402


def _classi_usate(html: str) -> set[str]:
    return {c for m in re.finditer(r'class="([^"]+)"', html)
            for c in m.group(1).split() if not c.startswith("{")}


def _classi_definite(html: str) -> set[str]:
    """Le classi con una regola: il foglio condiviso piu' lo <style> di pagina."""
    css = pagina_stile.CSS
    for blocco in re.findall(r"<style>(.*?)</style>", html, re.S):
        css += blocco
    return set(re.findall(r"\.([a-zA-Z][\w-]*)", css))


def test_lo_stile_di_pagina_arriva_nella_pagina():
    """Il caso esatto della regressione: un extra passato non deve sparire."""
    html = pagina_stile.guscio("T", "d", "d", "index.html", "<p>corpo</p>", [],
                               stile_extra=".sentinella{color:red}")
    assert ".sentinella{color:red}" in html
    assert "<style>" in html


def test_senza_extra_non_si_apre_uno_style_vuoto():
    html = pagina_stile.guscio("T", "d", "d", "index.html", "<p>corpo</p>", [])
    assert "<style>" not in html


def _render(modulo_nome: str):
    import json
    import importlib
    mod = importlib.import_module(modulo_nome)
    pay_path = mod.OUTPUT_DIR / "payload.json"
    if not pay_path.is_file():
        pay_path = mod.DEMO_DIR / "payload.json"
    if not pay_path.is_file():
        pytest.skip(f"payload.json non disponibile per {modulo_nome}")
    pay = json.loads(pay_path.read_text(encoding="utf-8"))
    val_path = mod.OUTPUT_DIR / "validazione_dati.json"
    val = json.loads(val_path.read_text(encoding="utf-8")) if val_path.is_file() else None
    if modulo_nome == "pagina_guida":
        # la guida vuole anche la configurazione: i pesi e le soglie che stampa
        # nelle formule vengono da li'.
        import parte1_analisi as p1
        return mod.render(p1.Config(), pay, val)
    return mod.render(pay, val)


@pytest.mark.parametrize("modulo", ["pagina_home", "pagina_guida"])
def test_nessuna_classe_senza_regola(modulo):
    html = _render(modulo)
    orfane = _classi_usate(html) - _classi_definite(html)
    assert not orfane, f"{modulo}: classi senza una regola CSS -> {sorted(orfane)}"
