"""I controlli dell'audit girano davvero, e se non girano il rapporto lo dice.

I difetti che questi test bloccano, trovati tutti e tre nella stessa esecuzione:

  · `check_xg_consistency` aveva un `%s` per il filtro di stagione che nessuno
    interpolava — mancava il `% _solo_stagione_corrente()` che il controllo
    gemello sui minuti ha. Il driver lasciava il `%s` nel testo, la query
    diventava `ON gp.giocatore_id = g.ids` e MySQL rispondeva "Unknown column
    's' in 'on clause'".
  · `check_id_orphans_no_fk` e `check_duplicates_t_player_analytics`
    interrogavano `t_player_analytics`, tabella di uno schema precedente che in
    nessuno dei due database esiste piu'.
  · E il motivo per cui nessuno se n'era accorto: un check che crasha finiva
    solo nel log. Il JSON e il riepilogo contavano i findings dei check
    riusciti e stampavano "Reliability score 81/100" come se fossero girati
    tutti e sedici. Tre controlli morivano a ogni giro e il rapporto non li
    nominava: un controllo che non gira non e' un controllo passato.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "audit"))

from lib import checks as C            # noqa: E402
from lib.findings import Report        # noqa: E402


@pytest.fixture
def sql_catturato(monkeypatch):
    """Intercetta le query invece di eseguirle: qui non serve un database."""
    viste: list[str] = []

    def _all(sql, params=()):
        viste.append(sql)
        return []

    def _one(sql, params=()):
        viste.append(sql)
        return (0,)

    monkeypatch.setattr(C, "fetch_all", _all)
    monkeypatch.setattr(C, "fetch_one", _one)
    monkeypatch.setattr(C, "_solo_stagione_corrente", lambda alias="gp": " AND gp.season = '2025-26'")
    return viste


def test_xg_consistency_non_lascia_placeholder(sql_catturato):
    C.check_xg_consistency(Report(db_name="test"))
    assert sql_catturato, "il controllo non ha interrogato niente"
    sql = sql_catturato[0]
    assert "%s" not in sql, f"placeholder non sostituito, arriva cosi' al driver: {sql}"
    assert "gp.season" in sql, "il filtro di stagione non e' entrato nella query"


def test_xg_consistency_confronta_una_stagione_sola(sql_catturato):
    """Senza filtro confronterebbe un anno con la somma di tutti gli anni."""
    C.check_xg_consistency(Report(db_name="test"))
    sql = sql_catturato[0]
    i_join = sql.index("LEFT JOIN giocatore_partita")
    i_group = sql.index("GROUP BY")
    assert "gp.season" in sql[i_join:i_group], \
        "il filtro deve stare nella ON del join, non dopo il raggruppamento"


@pytest.mark.parametrize("check", ["check_id_orphans_no_fk",
                                   "check_duplicates_t_player_analytics"])
def test_tabella_assente_non_fa_crashare_il_check(check, monkeypatch):
    monkeypatch.setattr(C, "_tabella_esiste", lambda nome: False)
    monkeypatch.setattr(C, "fetch_one", lambda sql, params=(): (0,))
    monkeypatch.setattr(C, "fetch_all", lambda sql, params=(): [])
    report = Report(db_name="test")
    getattr(C, check)(report)          # non deve sollevare
    assert report.findings, f"{check}: tabella assente, ma il rapporto non lo dice"
    assert all(f.severity.value == "info" for f in report.findings), \
        "una tabella che non esiste non e' un'anomalia dei dati"


def test_tabella_presente_il_check_interroga_davvero(monkeypatch):
    """Il salto vale solo per le tabelle assenti: se c'e', il controllo gira."""
    viste: list[str] = []
    monkeypatch.setattr(C, "_tabella_esiste", lambda nome: True)
    monkeypatch.setattr(C, "fetch_one",
                        lambda sql, params=(): (viste.append(sql), (0,))[1])
    report = Report(db_name="test")
    C.check_duplicates_t_player_analytics(report)
    assert any("t_player_analytics" in s for s in viste), \
        "tabella presente ma il controllo non l'ha interrogata"
    assert not report.findings


def test_un_check_che_crasha_finisce_nel_rapporto(monkeypatch, tmp_path):
    """Il difetto che ha nascosto gli altri due per mesi."""
    # `audit` da solo e' il pacchetto: lo script sta in audit/audit.py
    import audit.audit as A

    def _esplode(report):
        raise RuntimeError("1054 (42S22): Unknown column 's' in 'on clause'")

    _esplode.__name__ = "check_finto_rotto"
    monkeypatch.setattr(A, "CHECKS", [_esplode])
    monkeypatch.setattr(sys, "argv", ["audit.py", "--quiet",
                                      "--out", str(tmp_path / "r.json")])
    A.main()

    import json
    d = json.loads((tmp_path / "r.json").read_text(encoding="utf-8"))
    rotti = [f for f in d["findings"] if f["code"] == "AUD-001"]
    assert rotti, "il check e' crashato e il rapporto non lo nomina"
    assert rotti[0]["severity"] == "high"
    assert "check_finto_rotto" in rotti[0]["title"]
    assert d["summary"]["reliability_score"] < 100, \
        "un controllo non eseguito non puo' lasciare il punteggio intatto"
