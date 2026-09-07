"""La sentinella legge il calendario come lo leggerebbe un lettore.

Il difetto che questi test bloccano: "la terza giornata e' in archivio" non
vuol dire "ci sono trenta partite". Con i turni infrasettimanali e i rinvii una
giornata resta aperta per giorni, e in quella finestra il conteggio grezzo dice
gia' di si'. Pubblicare li' vorrebbe dire mettere in classifica squadre con una
partita in meno delle altre — lo sbilanciamento che l'indice esiste per
correggere, rimesso dentro dal calendario.

Quindi la giornata di una squadra e' la sua ennesima partita in ordine di data,
e le giornate chiuse sono quelle che hanno giocato tutte. Da lo stesso
calendario esce anche *quando* si chiude quella che manca, che e' quello che
serve al preavviso: saperlo il venerdi' vale piu' che scoprirlo il lunedi'.
"""
import json
import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import sentinella as S  # noqa: E402

ORA = datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc)


def _calendario(partite, monkeypatch):
    """Un soccerdata finto che restituisce esattamente questo calendario.

    `partite` e' una lista di (giorni_da_ORA, casa, trasferta, giocata).
    """
    righe = [{"date": ORA + timedelta(days=g), "home_team": c,
              "away_team": t, "is_result": bool(fatta)}
             for g, c, t, fatta in partite]

    class _U:
        def __init__(self, *a, **k):
            pass

        def read_schedule(self):
            return pd.DataFrame(righe)

    monkeypatch.setitem(sys.modules, "soccerdata",
                        types.SimpleNamespace(Understat=_U))


@pytest.fixture(autouse=True)
def orologio_fermo(monkeypatch):
    """Il tempo non scorre durante un test: il preavviso dipende da quando e'."""
    monkeypatch.setattr(S, "adesso", lambda: ORA)


# ── quante giornate hanno giocato tutte ─────────────────────────────────────

def test_una_giornata_aperta_non_conta(monkeypatch):
    """Tre squadre su quattro hanno giocato il secondo turno: siamo ancora a una."""
    _calendario([(-7, "A", "B", True), (-7, "C", "D", True),
                 (-1, "B", "C", True), (+2, "D", "A", False)], monkeypatch)
    c = S.leggi_calendario("ENG-Premier League", "2026-27", soglia=3)
    assert c.complete == 1
    assert c.partite == 3


def test_una_giornata_chiusa_conta(monkeypatch):
    _calendario([(-7, "A", "B", True), (-7, "C", "D", True),
                 (-1, "B", "C", True), (-1, "D", "A", True)], monkeypatch)
    assert S.leggi_calendario("ENG-Premier League", "2026-27", soglia=3).complete == 2


def test_un_campionato_non_cominciato_e_zero(monkeypatch):
    _calendario([(+3, "A", "B", False), (+3, "C", "D", False)], monkeypatch)
    c = S.leggi_calendario("ENG-Premier League", "2026-27", soglia=3)
    assert c.complete == 0 and c.partite == 0


def test_un_calendario_vuoto_e_un_errore_non_uno_zero(monkeypatch):
    """Zero giornate e nessun calendario sono due cose diverse."""
    _calendario([], monkeypatch)
    c = S.leggi_calendario("ENG-Premier League", "2026-27", soglia=3)
    assert not c.ok


def test_understat_irraggiungibile_non_solleva(monkeypatch):
    class _Rotto:
        def __init__(self, *a, **k):
            raise ConnectionError("niente rete")

    monkeypatch.setitem(sys.modules, "soccerdata",
                        types.SimpleNamespace(Understat=_Rotto))
    c = S.leggi_calendario("ENG-Premier League", "2026-27", soglia=3)
    assert not c.ok and "niente rete" in c.errore


# ── quando si chiude quella che manca ───────────────────────────────────────

def test_dice_quando_si_chiude_la_giornata_che_serve(monkeypatch):
    _calendario([(-7, "A", "B", True), (-7, "C", "D", True),     # 1a, chiusa
                 (-1, "B", "C", True), (-1, "D", "A", True),     # 2a, chiusa
                 (+2, "A", "C", False), (+3, "B", "D", False)],  # 3a, aperta
                monkeypatch)
    c = S.leggi_calendario("ENG-Premier League", "2026-27", soglia=3)
    assert c.complete == 2
    assert c.giornata_attesa == 3
    assert c.chiusura == ORA + timedelta(days=3)   # l'ULTIMA partita del turno


def test_a_soglia_raggiunta_non_c_e_piu_niente_da_aspettare(monkeypatch):
    _calendario([(-7, "A", "B", True), (-7, "C", "D", True),
                 (-5, "B", "C", True), (-5, "D", "A", True),
                 (-1, "A", "C", True), (-1, "B", "D", True)], monkeypatch)
    c = S.leggi_calendario("ENG-Premier League", "2026-27", soglia=3)
    assert c.complete == 3 and c.chiusura is None


# ── i due messaggi, uno per volta ───────────────────────────────────────────

@pytest.fixture
def stato(tmp_path, monkeypatch):
    monkeypatch.setattr(S, "STATO", tmp_path / "sentinella.json")
    return tmp_path / "sentinella.json"


def _con(monkeypatch, partite):
    _calendario(partite, monkeypatch)
    mandati = []
    monkeypatch.setattr(S, "manda", lambda t: mandati.append(t))
    return mandati


VICINA = [(-7, "A", "B", True), (-7, "C", "D", True),
          (-5, "B", "C", True), (-5, "D", "A", True),
          (+1, "A", "C", False), (+1, "B", "D", False)]
LONTANA = [(-7, "A", "B", True), (-7, "C", "D", True),
           (-5, "B", "C", True), (-5, "D", "A", True),
           (+20, "A", "C", False), (+20, "B", "D", False)]
CHIUSA = [(-7, "A", "B", True), (-7, "C", "D", True),
          (-5, "B", "C", True), (-5, "D", "A", True),
          (-1, "A", "C", True), (-1, "B", "D", True)]


def test_il_preavviso_arriva_quando_la_giornata_sta_per_chiudersi(monkeypatch, stato):
    mandati = _con(monkeypatch, VICINA)
    S.controlla("premier", "2026-27", 3, 72, manda_davvero=True, forza=False)
    assert len(mandati) == 1 and "manca poco" in mandati[0]


def test_il_preavviso_non_arriva_con_venti_giorni_di_anticipo(monkeypatch, stato):
    mandati = _con(monkeypatch, LONTANA)
    S.controlla("premier", "2026-27", 3, 72, manda_davvero=True, forza=False)
    assert mandati == []


def test_il_via_libera_porta_dentro_il_comando(monkeypatch, stato):
    mandati = _con(monkeypatch, CHIUSA)
    S.controlla("premier", "2026-27", 3, 72, manda_davvero=True, forza=False)
    assert len(mandati) == 1
    assert "pubblica.py --lega premier --stagione 2026-27 --esegui" in mandati[0]


def test_ogni_messaggio_arriva_una_volta_sola(monkeypatch, stato):
    """Girando ogni giorno, il secondo giorno non deve ripetersi."""
    mandati = _con(monkeypatch, CHIUSA)
    for _ in range(3):
        S.controlla("premier", "2026-27", 3, 72, manda_davvero=True, forza=False)
    assert len(mandati) == 1


def test_il_via_libera_arriva_anche_dopo_il_preavviso(monkeypatch, stato):
    """Sono due messaggi diversi: il primo non zittisce il secondo."""
    mandati = _con(monkeypatch, VICINA)
    S.controlla("premier", "2026-27", 3, 72, manda_davvero=True, forza=False)
    _calendario(CHIUSA, monkeypatch)
    S.controlla("premier", "2026-27", 3, 72, manda_davvero=True, forza=False)
    assert len(mandati) == 2
    assert "manca poco" in mandati[0] and "si puo' pubblicare" in mandati[1]


def test_con_stato_non_manda_e_non_si_segna_niente(monkeypatch, stato):
    mandati = _con(monkeypatch, CHIUSA)
    S.controlla("premier", "2026-27", 3, 72, manda_davvero=False, forza=False)
    assert mandati == []
    assert not stato.exists()


def test_un_giro_andato_a_vuoto_non_brucia_l_avviso(monkeypatch, stato):
    """Understat muto oggi non deve far perdere il messaggio di domani."""
    class _Rotto:
        def __init__(self, *a, **k):
            raise ConnectionError("niente rete")

    monkeypatch.setitem(sys.modules, "soccerdata",
                        types.SimpleNamespace(Understat=_Rotto))
    mandati = []
    monkeypatch.setattr(S, "manda", lambda t: mandati.append(t))
    assert S.controlla("premier", "2026-27", 3, 72, True, False) == 1
    assert not stato.exists()

    mandati = _con(monkeypatch, CHIUSA)
    S.controlla("premier", "2026-27", 3, 72, manda_davvero=True, forza=False)
    assert len(mandati) == 1


def test_le_due_leghe_si_ricordano_a_parte(monkeypatch, stato):
    mandati = _con(monkeypatch, CHIUSA)
    S.controlla("premier", "2026-27", 3, 72, manda_davvero=True, forza=False)
    S.controlla("serie-a", "2026-27", 3, 72, manda_davvero=True, forza=False)
    assert len(mandati) == 2


def test_uno_stato_illeggibile_non_ferma_niente(monkeypatch, stato):
    """Meglio un avviso in piu' che un avviso perso perche' un file e' rotto."""
    stato.write_text("{ questo non e' json", encoding="utf-8")
    mandati = _con(monkeypatch, CHIUSA)
    S.controlla("premier", "2026-27", 3, 72, manda_davvero=True, forza=False)
    assert len(mandati) == 1


# ── come si legge una data ──────────────────────────────────────────────────

def test_la_data_si_legge_nel_fuso_di_chi_la_legge():
    """Understat scrive in UTC; il messaggio lo legge qualcuno a Roma."""
    domenica = datetime(2026, 9, 6, 15, 30, tzinfo=timezone.utc)
    assert S.quando(domenica) == "domenica 6 settembre alle 17:30"


# ── le credenziali mancanti si spiegano ─────────────────────────────────────

# Vuoto e non cancellato: `config` ricarica .env con `setdefault`, quindi una
# variabile tolta tornerebbe da sola appena il bot e' configurato davvero — e
# questi due test comincerebbero a fallire il giorno in cui tutto funziona.
def test_senza_token_dice_come_si_prende(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "")
    with pytest.raises(SystemExit) as e:
        S._credenziali()
    assert "BotFather" in str(e.value)


def test_senza_chat_id_dice_come_si_prende(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:AA")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "")
    with pytest.raises(SystemExit) as e:
        S._credenziali()
    assert "--chat-id" in str(e.value)


def test_senza_bot_configurato_il_giro_non_esplode(monkeypatch, stato):
    """Il primo giro col task pianificato e il token non ancora messo."""
    _calendario(CHIUSA, monkeypatch)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "")
    assert S.controlla("premier", "2026-27", 3, 72, True, False) == 1
    assert not stato.exists()


# ── .env si aggiorna, non si riscrive ───────────────────────────────────────
# Dentro c'e' anche la password del database. Un file di configurazione
# riscritto da zero e' il modo piu' rapido di perdere una riga che nessuno si
# ricorda di avere messo li'.

def test_env_conserva_tutto_il_resto(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    prima = "DB_PASSWORD=segretissima\nDB_HOST=localhost\n"  # pragma: allowlist secret
    env.write_text(prima, encoding="utf-8")
    monkeypatch.setattr(S, "ENV", env)

    S._scrivi_env({"TELEGRAM_BOT_TOKEN": "123:AA", "TELEGRAM_CHAT_ID": "42"})

    testo = env.read_text(encoding="utf-8")
    assert "DB_PASSWORD=segretissima" in testo
    assert "DB_HOST=localhost" in testo
    assert "TELEGRAM_BOT_TOKEN=123:AA" in testo
    assert "TELEGRAM_CHAT_ID=42" in testo


def test_env_sostituisce_una_chiave_gia_presente(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    prima = "TELEGRAM_CHAT_ID=vecchio\nDB_PASSWORD=segretissima\n"  # pragma: allowlist secret
    env.write_text(prima, encoding="utf-8")
    monkeypatch.setattr(S, "ENV", env)

    S._scrivi_env({"TELEGRAM_CHAT_ID": "nuovo"})

    testo = env.read_text(encoding="utf-8")
    assert "TELEGRAM_CHAT_ID=nuovo" in testo
    assert "vecchio" not in testo
    assert testo.count("TELEGRAM_CHAT_ID") == 1
    assert "DB_PASSWORD=segretissima" in testo


def test_env_si_puo_creare_da_zero(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    monkeypatch.setattr(S, "ENV", env)
    S._scrivi_env({"TELEGRAM_CHAT_ID": "42"})
    assert "TELEGRAM_CHAT_ID=42" in env.read_text(encoding="utf-8")


# ── trovare la chat ─────────────────────────────────────────────────────────

def _updates(monkeypatch, risultato):
    monkeypatch.setattr(S, "_chiama", lambda metodo, corpo, token: risultato)


def test_si_trova_anche_un_gruppo_appena_aggiunto(monkeypatch):
    """Aggiungere il bot a un gruppo non produce un messaggio: produce altro."""
    _updates(monkeypatch, [
        {"my_chat_member": {"chat": {"id": -100123, "type": "supergroup",
                                     "title": "Lega Fantacalcio"}}},
    ])
    viste = S.chat_disponibili("tok")
    assert viste == {"-100123": ("Lega Fantacalcio", "gruppo")}


def test_si_trova_la_chat_privata(monkeypatch):
    _updates(monkeypatch, [
        {"message": {"chat": {"id": 55, "type": "private",
                              "first_name": "Raffaele"}}},
    ])
    id_chat, (nome, tipo) = next(iter(S.chat_disponibili("tok").items()))
    assert id_chat == "55" and nome == "Raffaele" and "solo a te" in tipo


def test_un_aggiornamento_senza_chat_non_rompe_niente(monkeypatch):
    _updates(monkeypatch, [{"poll": {"id": "1"}}, {"message": {}}])
    assert S.chat_disponibili("tok") == {}


def test_l_intestazione_non_si_ripete(tmp_path, monkeypatch):
    """Le due chiavi si scrivono in due momenti: il token subito, il

    destinatario dopo che hai premuto Avvia. Il commento va scritto una volta.
    """
    env = tmp_path / ".env"
    monkeypatch.setattr(S, "ENV", env)
    S._scrivi_env({"TELEGRAM_BOT_TOKEN": "123:AA"})
    S._scrivi_env({"TELEGRAM_CHAT_ID": "42"})
    assert env.read_text(encoding="utf-8").count(S.TESTATA) == 1


# ── quale stagione si guarda ────────────────────────────────────────────────
# Il difetto che questi test bloccano, trovato provando il task pianificato: la
# sentinella prendeva la stagione da `config.SEASON_CORRENTE`, che e' quella che
# il SITO PUBBLICA. Sono due domande diverse, e restano diverse per mesi ogni
# anno — da agosto, quando il campionato ricomincia, a quando si rigenera il
# sito. Cioe' esattamente la finestra in cui questo script serve.
#
# Con la stagione sbagliata avrebbe letto il calendario di quella conclusa, che
# ha 38 giornate su 38, e mandato "si puo' pubblicare la 2025/26" la mattina
# dopo — a proposito di una stagione pubblicata a maggio.

@pytest.mark.parametrize("quando_e, attesa", [
    (datetime(2026, 9, 4, 9, 0, tzinfo=timezone.utc), "2026-27"),   # settembre
    (datetime(2026, 7, 1, 9, 0, tzinfo=timezone.utc), "2026-27"),   # appena girata
    (datetime(2026, 6, 30, 9, 0, tzinfo=timezone.utc), "2025-26"),  # un giorno prima
    (datetime(2027, 5, 20, 9, 0, tzinfo=timezone.utc), "2026-27"),  # fine stagione
    (datetime(2029, 12, 1, 9, 0, tzinfo=timezone.utc), "2029-30"),  # e fra tre anni
])
def test_la_stagione_da_guardare_si_ricava_dalla_data(quando_e, attesa, monkeypatch):
    monkeypatch.setattr(S, "adesso", lambda: quando_e)
    assert S.stagione_da_guardare() == attesa


def test_non_si_guarda_la_stagione_che_il_sito_pubblica(monkeypatch, stato):
    """Il task pianificato gira senza variabili d'ambiente: deve decidere da se'."""
    import config
    monkeypatch.setattr(config, "SEASON_CORRENTE", "2025-26")
    monkeypatch.setenv("SERIE_A_SEASON", "2025-26")
    viste = []
    monkeypatch.setattr(S, "leggi_calendario",
                        lambda lega, stagione, soglia: viste.append(stagione) or S.Calendario(errore="basta"))
    S.controlla("premier", None, 3, 72, manda_davvero=False, forza=False)
    assert viste == ["2026-27"]     # ORA e' il 3 settembre 2026


# ── il giro fatto da sola ───────────────────────────────────────────────────

def _finto_giro(monkeypatch, codice=0):
    """Prende il posto di pubblica.py: registra come lo si chiama."""
    chiamate = []

    def finto(alias, stagione):
        chiamate.append((alias, stagione))
        return codice, r"C:\dev\serie-a-index", "ultima riga"

    monkeypatch.setattr(S, "pubblica_ora", finto)
    return chiamate


def test_senza_il_flag_il_comando_resta_da_copiare(monkeypatch, stato):
    """Il comportamento di prima non cambia: avvisa e basta."""
    mandati = _con(monkeypatch, CHIUSA)
    chiamate = _finto_giro(monkeypatch)

    S.controlla("premier", "2026-27", 3, 72, manda_davvero=True, forza=False)

    assert chiamate == []
    assert "pubblica.py --lega premier" in mandati[0]


def test_col_flag_il_giro_parte_da_solo(monkeypatch, stato):
    mandati = _con(monkeypatch, CHIUSA)
    chiamate = _finto_giro(monkeypatch)

    S.controlla("premier", "2026-27", 3, 72, manda_davvero=True, forza=False,
                pubblica=True)

    assert chiamate == [("premier", "2026-27")]
    assert len(mandati) == 2                      # parto, e poi ho finito
    assert "ci penso io" in mandati[0]
    assert "pronta" in mandati[1]


def test_il_giro_si_ferma_prima_del_push(monkeypatch, stato):
    """La riga che separa il risparmio di tempo dal pubblicare alla cieca."""
    mandati = _con(monkeypatch, CHIUSA)
    _finto_giro(monkeypatch)

    S.controlla("premier", "2026-27", 3, 72, manda_davvero=True, forza=False,
                pubblica=True)

    assert "git push" in mandati[1], "non dice cosa resta da fare"
    assert "online c'e' ancora la stagione vecchia" in mandati[1]


def test_il_giro_non_si_ripete_il_giorno_dopo(monkeypatch, stato):
    """Girando ogni mattina, non si ripubblica la stessa stagione."""
    _con(monkeypatch, CHIUSA)
    chiamate = _finto_giro(monkeypatch)

    for _ in range(3):
        S.controlla("premier", "2026-27", 3, 72, manda_davvero=True,
                    forza=False, pubblica=True)

    assert len(chiamate) == 1


def test_un_giro_fallito_domani_si_ripete(monkeypatch, stato):
    """Understat muto o rete caduta: non deve restare li' creduto fatto."""
    mandati = _con(monkeypatch, CHIUSA)
    chiamate = _finto_giro(monkeypatch, codice=2)

    for _ in range(2):
        S.controlla("premier", "2026-27", 3, 72, manda_davvero=True,
                    forza=False, pubblica=True)

    assert len(chiamate) == 2, "un giro fallito e' stato segnato come fatto"
    assert "si e' fermato" in mandati[1]


def test_con_stato_non_pubblica_niente(monkeypatch, stato):
    """`--stato` legge e stampa: non deve far partire mezz'ora di lavoro."""
    _con(monkeypatch, CHIUSA)
    chiamate = _finto_giro(monkeypatch)

    S.controlla("premier", "2026-27", 3, 72, manda_davvero=False, forza=False,
                pubblica=True)

    assert chiamate == []


def test_il_giro_lancia_pubblica_senza_toccare_git(monkeypatch, stato):
    """Il comando vero, come viene costruito: niente commit, niente push."""
    visti = {}

    class Esito:
        returncode = 0
        stdout = r"repo:   C:\dev\serie-a-index" + "\n"
        stderr = ""

    def finta_run(comando, **kw):
        visti["comando"] = comando
        return Esito()

    monkeypatch.setattr(S.subprocess, "run", finta_run)
    codice, repo, _ = S.pubblica_ora("serie-a", "2026-27")

    assert codice == 0
    assert repo.endswith("serie-a-index"), "il repo si legge dall'uscita"
    assert visti["comando"][1:] == ["pubblica.py", "--lega", "serie-a",
                                    "--stagione", "2026-27", "--esegui"]
    assert not any("push" in x or "commit" in x for x in visti["comando"])
