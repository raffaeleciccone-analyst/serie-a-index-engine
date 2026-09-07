"""Il caso di mercato entra nella sequenza, e dichiara la stagione dei suoi dati.

Due difetti, tutti e due invisibili fino al cambio di annata:

1. **Era fuori dalla sequenza.** Il generatore stava dentro `serie-a-index`,
   cioe' dentro il repo del sito pubblicato — l'unico posto dove un generatore
   non deve stare. Per questo `pubblica.py` non lo chiamava: al cambio di
   stagione la pagina restava indietro da sola, con "25/26" battuto a mano nella
   barra mentre le altre venticinque dicevano "26/27".

2. **Non si puo' rifare a settembre.** Il caso vuole almeno 1500 minuti a testa,
   circa diciassette partite intere: sull'annata in corso nessuno ci arriva e la
   pagina uscirebbe vuota. Quindi si tiene l'ultima stagione conclusa — e lo
   dice, invece di far credere che tre nomi del 2025-26 siano del 2026-27.
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import caso_mercato as cm  # noqa: E402
from pubblica import passi  # noqa: E402


def _payload(path: Path, stagione: str, quanti: int, minuti: int):
    path.write_text(json.dumps({
        "stagione": stagione,
        "players": [{"id": i, "minuti": minuti} for i in range(quanti)],
    }), encoding="utf-8")


def test_la_pagina_solo_lega_e_nella_sequenza_della_sua_lega():
    nomi_ita = [n for n, _, _ in passi("2026-27", "ITA-Serie A")]
    nomi_eng = [n for n, _, _ in passi("2026-27", "ENG-Premier League")]
    assert "caso-mercato" in nomi_ita
    assert "caso-mercato" not in nomi_eng


def test_il_caso_arriva_dopo_l_archivio():
    """L'ordine non e' estetico: prima la stagione conclusa dev'essere al sicuro
    in `payload_lista_<annata>.json`, altrimenti il caso non ha su cosa
    costruirsi."""
    nomi = [n for n, _, _ in passi("2026-27", "ITA-Serie A")]
    assert nomi.index("archivio") < nomi.index("caso-mercato")


def test_salta_la_stagione_in_corso_e_prende_l_ultima_conclusa(tmp_path, monkeypatch):
    _payload(tmp_path / "payload_lista.json", "2026-27", 300, 200)          # 3 giornate
    _payload(tmp_path / "payload_lista_2025-26.json", "2025-26", 300, 2400)  # conclusa
    monkeypatch.setattr(cm, "DOVE", [tmp_path])
    stagione, file_scelto, _ = cm.scegli_stagione(None)
    assert stagione == "2025-26"
    assert file_scelto.name == "payload_lista_2025-26.json"


def test_una_stagione_senza_minuti_si_rifiuta_invece_di_uscire_vuota(tmp_path, monkeypatch):
    _payload(tmp_path / "payload_lista.json", "2026-27", 300, 200)
    monkeypatch.setattr(cm, "DOVE", [tmp_path])
    with pytest.raises(SystemExit) as e:
        cm.scegli_stagione(None)
    assert "1500" in str(e.value) and "2026-27" in str(e.value)


def test_chiedere_una_stagione_in_corso_lo_dice(tmp_path, monkeypatch):
    _payload(tmp_path / "payload_lista.json", "2026-27", 300, 200)
    _payload(tmp_path / "payload_lista_2025-26.json", "2025-26", 300, 2400)
    monkeypatch.setattr(cm, "DOVE", [tmp_path])
    with pytest.raises(SystemExit) as e:
        cm.scegli_stagione("2026-27")
    assert "troppo pochi" in str(e.value)


def test_senza_payload_non_inventa(tmp_path, monkeypatch):
    monkeypatch.setattr(cm, "DOVE", [tmp_path])
    with pytest.raises(SystemExit) as e:
        cm.scegli_stagione(None)
    assert "Nessun payload_lista" in str(e.value)
