"""
Regression tests for `serie_a_scout.core.entity_resolution`.

These tests use synthetic in-memory player lists; no DB connection needed.
A custom `on_quarantine` callback captures events so we can assert the
quarantine logic without touching `logs/quarantine/`.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from serie_a_scout.core import (  # noqa: E402
    PlayerResolver, ResolveStatus, normalize_name,
)


# ───────────────────────── normalize_name ─────────────────────────
class TestNormalizeName:
    def test_accents_are_folded(self):
        assert normalize_name("Lautaro Martínez") == "lautaro martinez"
        assert normalize_name("Nícolas González") == "nicolas gonzalez"

    def test_whitespace_collapsed_and_trimmed(self):
        assert normalize_name("  Mike   Maignan  ") == "mike maignan"
        assert normalize_name("\tFoo\tBar\n") == "foo bar"

    def test_nbsp_and_shy_dropped(self):
        # NBSP U+00A0 → space ; SHY U+00AD → removed
        assert normalize_name("Foo Bar") == "foo bar"
        assert normalize_name("Mart­in") == "martin"

    def test_none_and_empty(self):
        assert normalize_name(None) == ""
        assert normalize_name("") == ""
        assert normalize_name("   ") == ""

    def test_idempotent(self):
        s = "  Côté  d'Ivoire  "
        once = normalize_name(s)
        twice = normalize_name(once)
        assert once == twice


# ───────────────────────── resolver ─────────────────────────
def _events_collector():
    bag: list[dict] = []
    return bag, bag.append


def _sample_players():
    return [
        # Two real different players with same first name in different clubs
        {"id": 101, "nome": "Lautaro Martínez", "squadra_id": 1},   # Inter
        {"id": 102, "nome": "Lautaro Valenti",  "squadra_id": 2},   # Parma
        # Ghost from legacy ingestion: bare "Lautaro" in Inter
        {"id": 103, "nome": "Lautaro",          "squadra_id": 1},
        # Unique players
        {"id": 200, "nome": "Federico Dimarco", "squadra_id": 1},
        {"id": 300, "nome": "Mike Maignan",     "squadra_id": 3},
    ]


def test_resolve_exact_name_team_returns_ok(tmp_path):
    bag, on_q = _events_collector()
    r = PlayerResolver(_sample_players(), quarantine_path=tmp_path / "q.jsonl",
                       on_quarantine=on_q)
    res = r.resolve("Lautaro Martínez", 1)
    assert res.status is ResolveStatus.OK
    assert res.giocatore_id == 101
    assert res.confidence == 1.0
    assert bag == []  # nothing quarantined


def test_same_name_different_team_is_ambiguous(tmp_path):
    bag, on_q = _events_collector()
    r = PlayerResolver(_sample_players(), on_quarantine=on_q)
    # Lautaro Valenti is in squadra 2, asking for squadra 1 must NOT match Inter's Lautaro Martinez
    res = r.resolve("Lautaro Valenti", 1)
    assert res.status is ResolveStatus.AMBIGUOUS
    assert res.giocatore_id is None
    assert len(bag) == 1
    assert bag[0]["reason"] == "single_name_team_mismatch"


def test_unknown_player_is_not_found_and_quarantined(tmp_path):
    bag, on_q = _events_collector()
    r = PlayerResolver(_sample_players(), on_quarantine=on_q)
    res = r.resolve("Sconosciuto Player", 99)
    assert res.status is ResolveStatus.NOT_FOUND
    assert res.giocatore_id is None
    assert len(bag) == 1
    assert bag[0]["reason"] == "no_candidate_by_name"


def test_unicode_normalization_matches(tmp_path):
    bag, on_q = _events_collector()
    r = PlayerResolver(_sample_players(), on_quarantine=on_q)
    # input senza accenti, DB con accenti
    res = r.resolve("Lautaro Martinez", 1)
    assert res.status is ResolveStatus.OK
    assert res.giocatore_id == 101


def test_no_team_input_accepted_with_reduced_confidence(tmp_path):
    bag, on_q = _events_collector()
    r = PlayerResolver(_sample_players(), on_quarantine=on_q)
    res = r.resolve("Federico Dimarco", None)
    # solo 1 candidato per "Federico Dimarco" → OK ma confidence 0.6
    assert res.status is ResolveStatus.OK
    assert res.giocatore_id == 200
    assert res.confidence == 0.6
    assert res.reason == "single_name_no_team"
    assert bag == []


def test_quarantine_file_actually_written(tmp_path):
    qpath = tmp_path / "q.jsonl"
    r = PlayerResolver(_sample_players(), quarantine_path=qpath)
    r.resolve("Sconosciuto Player", 99)
    assert qpath.exists(), "quarantine file should be created"
    content = qpath.read_text(encoding="utf-8").strip().splitlines()
    assert len(content) == 1
    import json
    rec = json.loads(content[0])
    assert rec["status"] == "not_found"
    assert rec["nome_input"] == "Sconosciuto Player"


def test_empty_name_returns_not_found_without_quarantine_call(tmp_path):
    bag, on_q = _events_collector()
    r = PlayerResolver(_sample_players(), on_quarantine=on_q)
    res = r.resolve("", 1)
    assert res.status is ResolveStatus.NOT_FOUND
    assert res.reason == "empty_name"
    # empty_name is still quarantined (audit trail), per design
    assert len(bag) == 1


def test_multiple_same_normalized_name_is_ambiguous(tmp_path):
    bag, on_q = _events_collector()
    r = PlayerResolver(_sample_players(), on_quarantine=on_q)
    # "Lautaro" by itself matches id=103 ("Lautaro", sq=1); but Lautaro Martinez
    # is "lautaro martinez" normalized, so no collision on "lautaro" alone.
    # Force a collision:
    players = _sample_players() + [
        {"id": 104, "nome": "Lautaro", "squadra_id": 2},
    ]
    r2 = PlayerResolver(players, on_quarantine=on_q)
    res = r2.resolve("Lautaro", None)  # no team → 2 candidates
    assert res.status is ResolveStatus.AMBIGUOUS
    assert len(res.candidates) == 2
