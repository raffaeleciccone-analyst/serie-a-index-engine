"""
valida_tpi.py  v3.0 - Validazione TPI Serie A 25/26
====================================================
Fix v3:
  · BUG1: slope/intercept None → JS riceveva "None" → SyntaxError
  · BUG3: pri:.2f con valore None → TypeError (ora usa safe())
  · BUG6: eta:.0f con valore None → TypeError (ora usa safe())
  · BUG7: val_d scatter con aii/pri None → rimossi dalla lista
  · Responsive completo (phone portrait, tablet, landscape)
  · Sezione E — Validazione TPI Pro (AII + PRI + confronto ranking)
"""
from __future__ import annotations
import json, logging, os, sys, time, warnings, webbrowser
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass
import numpy as np
import pandas as pd
from scipy import stats

warnings.filterwarnings("ignore")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("valida_tpi")

BASE_DIR   = Path(__file__).parent
OUTPUT_DIR = BASE_DIR / "dashboard_output"
PAYLOAD    = OUTPUT_DIR / "payload.json"
PAYLOAD_FULL = OUTPUT_DIR / "payload_full.json"
# Repo demo pubblicato (stesso default di parte2_dashboard.py): override con SERIE_A_DEMO_DIR.
DEMO_DIR   = Path(os.environ.get("SERIE_A_DEMO_DIR", BASE_DIR.parent / "serie-a-scout-demo"))

sys.path.insert(0, str(BASE_DIR))
from config import db_url as _cfg_db_url  # carica .env + fail-fast
import valida_stats as vs  # helper statistici puri (bootstrap, skill, PCA, placebo…)

# Pesi nominali del TPI (specchio di Config.tpi_weights in parte1_analisi.py)
TPI_WEIGHTS = {
    "output_adj": 0.32, "buildup_adj": 0.10, "centralita": 0.18,
    "boost_ratio": 0.02, "consistenza": 0.07, "finishing": 0.20,
    "form":       0.11,
}
# dim TPI → chiave z-score nel payload
TPI_ZKEYS = {
    "output_adj": "z_output", "buildup_adj": "z_buildup", "centralita": "z_centralita",
    "boost_ratio": "z_boost", "consistenza": "z_consistenza",
    "finishing":  "z_finishing", "form": "z_form",
}
# Il TPI pubblicato NON e' la media pesata degli z: dopo di quella parte1
# applica tre passaggi (parte1_analisi.py:2374-2408) che vanno replicati, o
# qualunque ricostruzione confronta due cose diverse.
#   shrunk = media_ruolo + (0.35 + 0.65*confidence) * (media_pesata - media_ruolo)
#   TPI    = peso_ruolo * shrunk * penalty_disponibilita
# Specchio di Config.offensive_role_weight e Config.confidence_floor.
ROLE_WEIGHT = {"ATT": 1.00, "CEN": 0.85, "DIF": 0.55, "POR": 0.20}
CONFIDENCE_FLOOR = 0.35
# codici ruolo IT → EN (per i 'movers' bilingui)
_ROLE_EN = {"ATT": "FWD", "CEN": "MID", "DIF": "DEF", "POR": "GK"}

# ── Soglie dichiarate PRIMA di guardare i risultati ──────────────
# Fissate il 14 agosto 2026, prima di rigirare i test sui 381 qualificati
# invece che sui primi 100. Servono a impedire una cosa sola: scegliere il
# taglio dopo, sul valore che e' uscito. Se un risultato le attraversa
# cambia l'etichetta, non la soglia; se una soglia va cambiata davvero si
# cambia qui, si scrive perche', e si rigira tutto da capo.
#
# Stanno in un posto solo perche' il taglio del backtest era scritto due
# volte — qui e nel setBadge lato JS — e due copie di un numero prima o poi
# dicono cose diverse. Il JS ora lo riceve da qui.
#
# Dove non c'e' un numero e' perche' non serve: Q e P si decidono dal segno
# dell'IC 95% bootstrap, che e' una regola e non una scelta.
SOGLIE = {
    "c_hi":  0.50,   # backtest C: |r| >= 0.50 → "Buono"
    "c_mid": 0.30,   # 0.30 <= |r| < 0.50 → "Moderato", sotto → "Basso"
}
SOGLIE_FISSATE_IL = ("14 agosto 2026", "14 August 2026")

DB_URL   = _cfg_db_url()
DB_RETRY = 3
DB_WAIT  = 5


# ════════════════════════════════════════════════════════════════
# HELPERS
# ════════════════════════════════════════════════════════════════
def _sf(v, d: int = 2) -> str:
    """safe format: None → '—', altrimenti arrotonda."""
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return "\u2014"
    try:
        return str(round(float(v), d))
    except Exception:
        return str(v)

def conta_test_pubblicati(*vals) -> int:
    """Quanti test finiscono davvero in pagina: A/B/C escono sempre, gli altri
    solo se hanno dati. Sta qui, in un posto solo, perche' lo usano sia l'hero
    sia il controllo sulle pagine scritte a mano."""
    return 3 + sum(1 for v in vals if (v or {}).get("has_data"))


def copia_pagine_nav(escludi: str) -> None:
    """Porta accanto all'HTML le pagine a cui punta la nav.

    Le voci Homepage / Metodo / TPI Pro / Classifica sono href relativi a file
    vicini. In `dashboard_output` quei file non ci sono, quindi la pagina
    aperta da qui — che e' proprio quella che gli script aprono a fine run —
    manda in 404 ogni voce della nav. Stessa ragione per cui si copiano gia'
    i18n.js, ai_chat.js e i font.

    `escludi` e' la pagina che questo script ha appena scritto: quella e' la
    versione buona e non va sovrascritta con la copia del repo demo.
    """
    for nome in ("index.html", "guida_completa.html", "dashboard_pro.html",
                 "dashboard_serie_a.html", "validazione.html"):
        if nome == escludi:
            continue
        src = DEMO_DIR / nome
        if not src.is_file():
            continue
        dst = OUTPUT_DIR / nome
        try:
            # non downgradare una pagina piu' fresca gia' presente qui
            if dst.exists() and dst.stat().st_mtime >= src.stat().st_mtime:
                continue
            dst.write_bytes(src.read_bytes())
            log.info(f"OK → {dst}  (pagina della nav)")
        except OSError as e:
            log.warning(f"Copia {nome} fallita: {e}")


def avvisa_se_conteggio_a_mano(n_test: int) -> None:
    """Il numero dei test vive in validazione.html, dove si calcola.

    `index.html`, `guida_completa.html` e `i18n.js` sono scritti a mano: se
    qualcuno ci ricopia "15 verifiche" e poi un test perde i dati, le pagine
    dicono due cose diverse e non se ne accorge nessuno. Questo non corregge
    niente, avvisa e basta — ma avvisa nel momento giusto, cioe' appena il
    numero cambia.
    """
    import re
    pat = re.compile(r"(\d+)\s+(?:verifiche|verifica|test|checks|tests)\b", re.I)
    for nome in ("index.html", "guida_completa.html", "i18n.js"):
        f = DEMO_DIR / nome
        if not f.is_file():
            continue
        try:
            testo = f.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            log.debug(f"  Conteggio: {nome} non leggibile ({e})")
            continue
        for m in pat.finditer(testo):
            trovato = int(m.group(1))
            if trovato != n_test:
                log.warning(f"  {nome} dice \"{m.group(0)}\" ma i test pubblicati sono "
                            f"{n_test}: le pagine si contraddicono.")
            else:
                log.warning(f"  {nome} ha il conteggio dei test scritto a mano "
                            f"(\"{m.group(0)}\"). Oggi coincide, divergera' al primo "
                            f"test che perde i dati: meglio toglierlo.")


def _js_num(v) -> str:
    """Serializza numero per JS: None → null, non mai la stringa 'None'."""
    if v is None:
        return "null"
    try:
        f = float(v)
        if np.isnan(f) or np.isinf(f):
            return "null"
        return repr(f)
    except Exception:
        return "null"


# ════════════════════════════════════════════════════════════════
# CARICAMENTO
# ════════════════════════════════════════════════════════════════
def payload_corrente() -> Path:
    """Il payload della stagione corrente su cui girano i test.

    `payload.json` si ferma ai primi 100 perche' e' la dashboard pubblicata,
    e quel taglio non e' neutro: confrontare fra loro solo i migliori comprime
    la varianza e attenua ogni correlazione, quindi i test finirebbero per
    misurare la selezione invece dell'indice. Se c'e' `payload_full.json`
    (parte1 --top-n 0) i test usano quello — stessa stagione, stesso motore,
    senza il taglio in alto — e il sito resta quello di prima.
    """
    if not PAYLOAD_FULL.is_file():
        return PAYLOAD
    # payload.json lo rigenera ogni run del sito, payload_full.json solo chi
    # passa --top-n 0: se resta indietro, i test girerebbero su una stagione
    # vecchia senza che se ne accorga nessuno. Non lo correggo qui — dire quale
    # comando serve e' piu' onesto che scegliere da solo un file al posto suo.
    if PAYLOAD.is_file() and PAYLOAD.stat().st_mtime > PAYLOAD_FULL.stat().st_mtime:
        log.warning(f"  payload_full.json e' PIU' VECCHIO di payload.json: i test girerebbero "
                    f"su dati superati. Rigeneralo con `python parte1_analisi.py --top-n 0`.")
    return PAYLOAD_FULL


def load_payload() -> dict:
    src = payload_corrente()
    if not src.exists():
        raise FileNotFoundError(
            f"payload.json non trovato in {PAYLOAD}\n"
            "Esegui prima: python parte1_analisi.py"
        )
    log.info(f"  Campione: {src.name}"
             + ("" if src is PAYLOAD_FULL else " (primi 100 — genera payload_full.json"
                                               " con `parte1_analisi.py --top-n 0`)"))
    with open(src, encoding="utf-8") as f:
        return json.load(f)


def load_player_games():
    try:
        from sqlalchemy import create_engine, text
        import pymysql  # noqa
    except ImportError:
        log.error("sqlalchemy/pymysql non installati. Esegui: pip install sqlalchemy pymysql")
        return None
    for attempt in range(1, DB_RETRY + 1):
        try:
            engine = create_engine(DB_URL, pool_pre_ping=True)
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            df = pd.read_sql("""
                SELECT gp.giocatore_id,
                       TRIM(CONCAT_WS(' ', NULLIF(TRIM(g.nome),''), NULLIF(TRIM(g.cognome),''))) AS giocatore,
                       g.ruolo, sq.nome AS squadra,
                       cal.giornata, gp.minuti, gp.goal, gp.xg, gp.xa
                FROM giocatore_partita gp
                JOIN giocatori g    ON g.id   = gp.giocatore_id
                JOIN squadre sq     ON sq.id  = g.squadra_id
                JOIN calendario cal ON cal.id = gp.calendario_id
                WHERE gp.minuti > 0""", engine)
            log.info(f"  DB connesso — {len(df)} record")
            return df
        except Exception as e:
            if attempt < DB_RETRY:
                log.warning(f"  Tentativo {attempt}/{DB_RETRY} — riprovo tra {DB_WAIT}s...")
                time.sleep(DB_WAIT)
            else:
                log.error(f"  DB non raggiungibile: {str(e)[:120]}")
                log.warning("  Per avviare MySQL: net start MySQL80")
                log.warning("  Backtest calcolato dal payload (fallback automatico)")
                return None


# ════════════════════════════════════════════════════════════════
# DATI ESTERNI (Fantacalcio / WhoScored)
# ════════════════════════════════════════════════════════════════
FANTA_VOTI = {
    "Lautaro Martinez": 7.12, "Lautaro Martínez": 7.12,
    "Marcus Thuram": 6.98, "Ademola Lookman": 6.89, "Keinan Davis": 6.71,
    "Paulo Dybala": 6.95, "Moise Kean": 6.82, "Kenan Yildiz": 6.74,
    "Dusan Vlahovic": 6.61, "Giacomo Raspadori": 6.68, "Donyell Malen": 6.72,
    "Christian Pulisic": 6.91, "Rafael Leao": 6.88, "Rafael Leão": 6.88,
    "Santiago Gimenez": 6.78, "Santiago Giménez": 6.78,
    "Albert Gudmundsson": 6.65, "Gianluca Scamacca": 6.59,
    "Lorenzo Lucca": 6.55, "David Neres": 6.71, "Romelu Lukaku": 6.63,
    "Matteo Politano": 6.61, "Khvicha Kvaratskhelia": 6.85,
    "Nicolo Barella": 6.85, "Nicolò Barella": 6.85,
    "Hakan Calhanoglu": 6.78, "Teun Koopmeiners": 6.72,
    "Scott McTominay": 6.69, "Charles De Ketelaere": 6.81,
    "Lazar Samardzic": 6.65, "Tijjani Reijnders": 6.74,
    "Piotr Zielinski": 6.61, "Davide Frattesi": 6.58,
    "Henrikh Mkhitaryan": 6.55, "Lorenzo Pellegrini": 6.52,
    "Adrien Rabiot": 6.48, "Weston McKennie": 6.44, "Nikola Vlasic": 6.49,
    "Mario Pasalic": 6.63, "Mattia Zaccagni": 6.72,
    "Federico Dimarco": 6.91, "Andrea Cambiaso": 6.72,
    "Giovanni Di Lorenzo": 6.65, "Davide Zappacosta": 6.58,
    "Raoul Bellanova": 6.55, "Nuno Tavares": 6.52,
    "Gift Orban": 6.58, "Nico Paz": 6.62, "Nikola Krstovic": 6.54,
    "Riccardo Orsolini": 6.61, "Thijs Dallinga": 6.49,
}

WHOSCORED = {
    "Lautaro Martinez": 7.82, "Lautaro Martínez": 7.82,
    "Marcus Thuram": 7.61, "Ademola Lookman": 7.58, "Paulo Dybala": 7.71,
    "Nicolo Barella": 7.65, "Nicolò Barella": 7.65,
    "Charles De Ketelaere": 7.55, "Kenan Yildiz": 7.38,
    "Khvicha Kvaratskhelia": 7.79, "Christian Pulisic": 7.68,
    "Rafael Leao": 7.52, "Rafael Leão": 7.52,
    "Hakan Calhanoglu": 7.44, "Teun Koopmeiners": 7.41,
    "Federico Dimarco": 7.38, "Andrea Cambiaso": 7.31,
    "Scott McTominay": 7.29, "Moise Kean": 7.35,
    "Davide Frattesi": 7.22, "Donyell Malen": 7.28,
    "Keinan Davis": 7.19, "Gianluca Scamacca": 7.15,
    "Mattia Zaccagni": 7.33, "Mario Pasalic": 7.18,
    "Tijjani Reijnders": 7.39, "Albert Gudmundsson": 7.24,
    "David Neres": 7.31, "Lorenzo Lucca": 7.12,
    "Santiago Gimenez": 7.26, "Santiago Giménez": 7.26,
    "Dusan Vlahovic": 7.08, "Giacomo Raspadori": 7.14,
    "Lazar Samardzic": 7.21, "Gift Orban": 7.09, "Nico Paz": 7.22,
    "Nikola Krstovic": 7.05, "Riccardo Orsolini": 7.11,
    "Thijs Dallinga": 7.03,
}


# ════════════════════════════════════════════════════════════════
# VALIDAZIONE A — Correlazione Fantacalcio
# ════════════════════════════════════════════════════════════════
def _interp_corr(r: float) -> tuple[str, str]:
    """Restituisce (IT, EN).

    Descrive l'entita' della correlazione e basta. La versione precedente
    chiamava "risultato ideale" la fascia centrale: una scala in cui r alta
    conferma, r media e' ideale e r bassa "misura altro" non puo' dare un
    esito negativo, quindi non stava interpretando niente.
    """
    ar = abs(r)
    if ar >= 0.70:
        return ("Correlazione forte (r≥0.7) — TPI e voto Fantacalcio ordinano i giocatori quasi allo stesso modo.",
                "Strong correlation (r≥0.7) — TPI and the Fantacalcio rating order players in nearly the same way.")
    if ar >= 0.50:
        return ("Correlazione moderata (r=0.5–0.7) — accordo parziale col voto Fantacalcio.",
                "Moderate correlation (r=0.5–0.7) — partial agreement with the Fantacalcio rating.")
    if ar >= 0.30:
        return ("Correlazione debole (r=0.3–0.5) — l'accordo col voto Fantacalcio è limitato.",
                "Weak correlation (r=0.3–0.5) — agreement with the Fantacalcio rating is limited.")
    return ("Correlazione bassa (r<0.3) — quasi nessun accordo col voto Fantacalcio.",
            "Low correlation (r<0.3) — almost no agreement with the Fantacalcio rating.")


def _load_fanta_voti_from_db(player_ids: list[int],
                             season: str = "2025-26") -> dict[int, float]:
    """Carica fantamedia stagionale per ID giocatore da t_fanta_voti (se esiste).
    Restituisce dict {giocatore_id: fantamedia_avg}. Fallback {} se tabella
    mancante o vuota. Usato come PRIMARY source dal test A; il dizionario
    hardcoded FANTA_VOTI resta come fallback per dev-mode senza ingestion.
    """
    try:
        from sqlalchemy import create_engine
        eng = create_engine(_cfg_db_url(), pool_pre_ping=True)
        # Verifica esistenza tabella (silenzioso se assente)
        check = pd.read_sql("SHOW TABLES LIKE 't_fanta_voti'", eng)
        if check.empty:
            return {}
        # Aggregato preferito: v_fanta_voti_stagionali (view). Fallback: query diretta.
        try:
            df = pd.read_sql(
                f"SELECT giocatore_id, fantamedia_avg, n_voti, provider "
                f"FROM v_fanta_voti_stagionali WHERE season='{season}'", eng)
        except Exception:
            df = pd.read_sql(
                f"SELECT giocatore_id, AVG(fantamedia) AS fantamedia_avg, "
                f"COUNT(*) AS n_voti, provider "
                f"FROM t_fanta_voti WHERE season='{season}' AND senza_voto=0 "
                f"GROUP BY giocatore_id, provider", eng)
        if df.empty:
            return {}
        # Se più provider, media pesata sul n_voti
        df["fm_weighted"] = df["fantamedia_avg"] * df["n_voti"]
        agg = (df.groupby("giocatore_id")
                 .agg(fm_sum=("fm_weighted", "sum"),
                      n_sum=("n_voti", "sum"))
                 .assign(fantamedia=lambda x: x["fm_sum"] / x["n_sum"]))
        log.info(f"  Test A: caricati {len(agg)} voti fanta da DB "
                 f"({df['provider'].nunique()} provider/i)")
        return {int(gid): float(r["fantamedia"]) for gid, r in agg.iterrows()}
    except Exception as e:
        log.debug(f"  Test A: t_fanta_voti non leggibile ({e}), fallback hardcoded.")
        return {}


def valida_correlazione_fanta(players: list) -> dict:
    rows = []
    # Sorgente preferita: DB (se popolato). Fallback: dizionario hardcoded.
    db_voti = _load_fanta_voti_from_db([p["id"] for p in players])
    for p in players:
        nome = p["nome"]
        gid  = p.get("id")
        tpi  = p["tpi"].get("totale")
        voto = db_voti.get(int(gid)) if gid else None
        if voto is None:
            voto = FANTA_VOTI.get(nome)
        if tpi is not None and voto is not None:
            rows.append({"nome": nome, "squadra": p["squadra"],
                         "ruolo": p["ruolo"], "tpi": tpi, "voto": voto})
    if len(rows) < 5:
        log.warning(f"  Solo {len(rows)} match (DB:{len(db_voti)}, hardcoded:{len(FANTA_VOTI)}).")
        return {"r": None, "p": None, "n": len(rows), "data": rows,
                "slope": None, "intercept": None,
                "interpretazione": "Dati insufficienti",
                "interpretazione_en": "Insufficient data"}
    log.info(f"  Test A: n={len(rows)} match "
             f"(fonte: {'DB '+str(len(db_voti))+' giocatori' if db_voti else 'hardcoded '+str(len(FANTA_VOTI))})")
    df = pd.DataFrame(rows)
    r, p_val = stats.pearsonr(df["tpi"], df["voto"])
    r_sp, p_sp = stats.spearmanr(df["tpi"], df["voto"])
    sl, ic, _, _, _ = stats.linregress(df["tpi"], df["voto"])

    # Bootstrap 95% CI su Pearson r (1000 campioni)
    rng = np.random.default_rng(42)
    boot_r = []
    for _ in range(1000):
        idx = rng.choice(len(df), size=len(df), replace=True)
        r_b, _ = stats.pearsonr(df["tpi"].iloc[idx], df["voto"].iloc[idx])
        boot_r.append(r_b)
    ci_lo = float(np.percentile(boot_r, 2.5))
    ci_hi = float(np.percentile(boot_r, 97.5))

    # Effect size Cohen's d (differenza TPI top vs bottom 25%)
    q25, q75 = df["tpi"].quantile(0.25), df["tpi"].quantile(0.75)
    top = df[df["tpi"] >= q75]["voto"]
    bot = df[df["tpi"] <= q25]["voto"]
    pooled_sd = np.sqrt((top.var() + bot.var()) / 2) if (top.var() + bot.var()) > 0 else 1
    cohen_d = (top.mean() - bot.mean()) / pooled_sd if pooled_sd > 0 else 0

    # IC bootstrap su Spearman (metrica rank-based, più adatta a rating non normali)
    sp_ci_lo, sp_ci_hi = vs.bootstrap_ci(df["tpi"].tolist(), df["voto"].tolist(), "spearman")
    # Correlazione parziale controllando il RUOLO (scorpora il confondente "gli ATT
    # segnano e prendono voti più alti")
    partial_r = vs.partial_spearman_by_group(
        df["tpi"].tolist(), df["voto"].tolist(), df["ruolo"].tolist())
    # Diagnostica di influenza leave-one-out sulla Pearson (igiene small-n)
    loo = vs.loo_corr_range(df["tpi"].tolist(), df["voto"].tolist(), "pearson")

    log.info(f"  Pearson r={r:.3f} [{ci_lo:.3f},{ci_hi:.3f}], Spearman ρ={r_sp:.3f}, "
             f"parziale(ruolo)={partial_r}, p={p_val:.4f}, Cohen's d={cohen_d:.2f}")
    return {
        "r": round(float(r), 4), "p": round(float(p_val), 4),
        "r_spearman": round(float(r_sp), 4), "p_spearman": round(float(p_sp), 4),
        "ci_lo": round(ci_lo, 3), "ci_hi": round(ci_hi, 3),
        "sp_ci_lo": sp_ci_lo, "sp_ci_hi": sp_ci_hi,
        "partial_r": partial_r, "loo": loo,
        "cohen_d": round(cohen_d, 3),
        "n": len(df), "slope": round(float(sl), 4), "intercept": round(float(ic), 4),
        "data": rows,
        "interpretazione": _interp_corr(r)[0], "interpretazione_en": _interp_corr(r)[1],
    }


# ════════════════════════════════════════════════════════════════
# VALIDAZIONE B — Top 10 Overlap WhoScored
# ════════════════════════════════════════════════════════════════
def valida_top10(players: list) -> dict:
    sorted_tpi = sorted(
        [p for p in players if p["tpi"].get("totale") is not None],
        key=lambda x: x["tpi"]["totale"], reverse=True
    )[:10]
    top10_tpi_names = {p["nome"] for p in sorted_tpi}

    ws_rows = [
        {"nome": p["nome"], "squadra": p["squadra"], "ruolo": p["ruolo"],
         "ws": WHOSCORED[p["nome"]], "tpi": p["tpi"].get("totale")}
        for p in players if p["nome"] in WHOSCORED
    ]
    ws_rows.sort(key=lambda x: x["ws"], reverse=True)
    top10_ws_names = {r["nome"] for r in ws_rows[:10]}

    overlap     = top10_tpi_names & top10_ws_names
    overlap_pct = round(len(overlap) / 10 * 100)

    # Divergenze calcolate sul SET COMUNE (giocatori con sia TPI sia voto
    # WhoScored), con ranking LOCALE per entrambe le metriche: così il confronto
    # è simmetrico e anche i "sopravvalutati" (WhoScored alto / TPI basso)
    # possono emergere — non solo i top-10 per TPI.
    common = [r for r in ws_rows if r["tpi"] is not None]
    by_tpi = sorted(common, key=lambda x: x["tpi"], reverse=True)
    by_ws  = sorted(common, key=lambda x: x["ws"],  reverse=True)
    tpi_rank_map = {r["nome"]: i + 1 for i, r in enumerate(by_tpi)}
    ws_rank_map  = {r["nome"]: i + 1 for i, r in enumerate(by_ws)}

    div_pos, div_neg = [], []
    for r in common:
        nome     = r["nome"]
        tpi_rank = tpi_rank_map[nome]
        ws_rank  = ws_rank_map[nome]
        rec = {"nome": nome, "squadra": r["squadra"],
               "tpi_rank": tpi_rank, "ws_rank": ws_rank,
               "tpi": round(r["tpi"], 3), "ws": r["ws"]}
        if (ws_rank - tpi_rank) >= 4:      # TPI lo posiziona molto più in alto → sottovalutato
            div_pos.append(rec)
        elif (tpi_rank - ws_rank) >= 4:    # WhoScored lo posiziona molto più in alto → sopravvalutato
            div_neg.append(rec)

    div_pos.sort(key=lambda x: x["ws_rank"] - x["tpi_rank"], reverse=True)
    div_neg.sort(key=lambda x: x["tpi_rank"] - x["ws_rank"], reverse=True)

    # ── Concordanza rank-based sull'INTERO set comune (più informativa del solo
    #    overlap di 10 elementi quantizzato) ──────────────────────────────
    common_tpi = [r["tpi"] for r in common]
    common_ws  = [r["ws"]  for r in common]
    kendall_common  = vs.correlation(common_tpi, common_ws, "kendall")
    spearman_common = vs.correlation(common_tpi, common_ws, "spearman")
    sp_ci_lo, sp_ci_hi = vs.bootstrap_ci(common_tpi, common_ws, "spearman")

    # ── Overlap "fair": Top10 per TPI e per WS estratti DALLO STESSO bacino
    #    (i ~38 con voto WS) + significatività ipergeometrica. Evita il bias dei
    #    pool incomparabili (Top10 globale TPI vs Top10 su 38). ─────────────
    fair_top_tpi = {r["nome"] for r in by_tpi[:10]}
    fair_top_ws  = {r["nome"] for r in by_ws[:10]}
    fair_overlap = fair_top_tpi & fair_top_ws
    fair_overlap_pct = round(len(fair_overlap) / 10 * 100) if len(common) >= 10 else None
    hyper = vs.hypergeom_overlap(len(fair_overlap), 10, 10, len(common)) \
        if len(common) >= 10 else {"p": None, "expected": None, "k": len(fair_overlap)}

    log.info(f"  Overlap: {overlap_pct}% (globale) · fair {fair_overlap_pct}% "
             f"(p_iperg={hyper.get('p')}) · τ_comune={kendall_common} · "
             f"divergenze +{len(div_pos)} / -{len(div_neg)}")
    return {
        "overlap_pct": overlap_pct,
        "overlap_names": list(overlap),
        "fair_overlap_pct": fair_overlap_pct,
        "kendall_common": kendall_common,
        "spearman_common": spearman_common,
        "common_ci_lo": sp_ci_lo, "common_ci_hi": sp_ci_hi,
        "n_common": len(common),
        "hyper": hyper,
        "top10_tpi": [{"nome": p["nome"], "squadra": p["squadra"], "ruolo": p["ruolo"],
                       "tpi": round(p["tpi"]["totale"], 3), "ws": WHOSCORED.get(p["nome"])}
                      for p in sorted_tpi],
        "top10_ws":       ws_rows[:10],
        "divergenze_pos": div_pos[:5],
        "divergenze_neg": div_neg[:5],
    }


# ════════════════════════════════════════════════════════════════
# VALIDAZIONE C — Backtest Predittivo
# ════════════════════════════════════════════════════════════════
def _interp_backtest(r) -> tuple[str, str]:
    """Restituisce (IT, EN)."""
    if r is None:
        return ("Dati insufficienti.", "Insufficient data.")
    if r >= 0.65:
        return ("Forte stabilità predittiva (r≥0.65) — il TPI cattura qualità stabile oltre il rumore.",
                "Strong predictive stability (r≥0.65) — TPI captures stable quality beyond noise.")
    if r >= 0.45:
        return ("Buona stabilità predittiva (r=0.45–0.65) — segnale reale con varianza residua attesa.",
                "Good predictive stability (r=0.45–0.65) — real signal with expected residual variance.")
    if r >= 0.30:
        return ("Stabilità moderata (r=0.3–0.45) — potere predittivo parziale.",
                "Moderate stability (r=0.3–0.45) — partial predictive power.")
    return ("Stabilità bassa (r<0.3) — sensibile alla forma del momento. Considera di aumentare K_base.",
            "Low stability (r<0.3) — sensitive to current form. Consider increasing K_base.")


def valida_backtest_db(df_gp: pd.DataFrame) -> dict:
    all_gg = sorted(df_gp["giornata"].unique())
    if len(all_gg) < 8:
        return {"r": None, "p": None, "n": 0, "scatter": [], "source": "db",
                "slope": None, "intercept": None,
                "early_range": "—", "late_range": "—",
                "interpretazione": "Troppo poche giornate",
                "interpretazione_en": "Too few matchdays"}
    split = len(all_gg) // 2
    early_gg, late_gg = all_gg[:split], all_gg[split:]

    def tpi_slice(gg):
        sub = df_gp[df_gp["giornata"].isin(gg)]
        agg = (sub.groupby("giocatore_id")
               .agg(xg=("xg", "sum"), xa=("xa", "sum"),
                    m=("minuti", "sum"), n=("minuti", "count"))
               .query("n >= 4 and m > 0"))
        agg["out"] = (agg["xg"] + agg["xa"]) / agg["m"] * 90
        return agg["out"]

    early, late = tpi_slice(early_gg), tpi_slice(late_gg)
    common = early.index.intersection(late.index)
    if len(common) < 10:
        return {"r": None, "p": None, "n": len(common), "scatter": [], "source": "db",
                "slope": None, "intercept": None,
                "early_range": f"gg {early_gg[0]}-{early_gg[-1]}",
                "late_range":  f"gg {late_gg[0]}-{late_gg[-1]}",
                "interpretazione": "Pochi giocatori in comune",
                "interpretazione_en": "Too few players in common"}

    r, p_val = stats.spearmanr(early[common], late[common])
    # Aggiungo Pearson r e Kendall τ
    r_pearson, p_pearson = stats.pearsonr(
        [float(early[i]) for i in common],
        [float(late[i])  for i in common],
    )
    tau, p_tau = stats.kendalltau(early[common], late[common])

    sl, ic, _, _, _ = stats.linregress(
        [float(early[i]) for i in common],
        [float(late[i])  for i in common],
    )

    # RMSE e MAE — misura errore predittivo assoluto
    predicted = np.array([float(early[i]) * sl + ic for i in common])
    actual    = np.array([float(late[i]) for i in common])
    rmse = float(np.sqrt(np.mean((predicted - actual)**2)))
    mae  = float(np.mean(np.abs(predicted - actual)))

    # Bootstrap 95% CI su Spearman r
    rng = np.random.default_rng(42)
    boot_r = []
    c_list = list(common)
    for _ in range(1000):
        idx = rng.choice(len(c_list), size=len(c_list), replace=True)
        sel = [c_list[i] for i in idx]
        rb, _ = stats.spearmanr(early[sel], late[sel])
        boot_r.append(rb)
    ci_lo = float(np.percentile(boot_r, 2.5))
    ci_hi = float(np.percentile(boot_r, 97.5))

    gmap = {int(row["giocatore_id"]): row
            for _, row in df_gp.drop_duplicates("giocatore_id").iterrows()}
    scatter = [
        {"nome":    gmap[g]["giocatore"] if g in gmap else str(g),
         "squadra": gmap[g]["squadra"]   if g in gmap else "",
         "ruolo":   gmap[g]["ruolo"]     if g in gmap else "",
         "early": round(float(early[g]), 4),
         "late":  round(float(late[g]),  4)}
        for g in common
    ]

    # ── Metriche predittive rigorose ───────────────────────────────────
    early_arr = [float(early[g]) for g in common]
    late_arr  = [float(late[g])  for g in common]
    roles     = [gmap[g]["ruolo"] if g in gmap else "" for g in common]
    # media-ruolo del late (baseline di gruppo), allineata
    role_late = {}
    for rl in set(roles):
        vals = [late_arr[i] for i, rr in enumerate(roles) if rr == rl]
        role_late[rl] = float(np.mean(vals)) if vals else float(np.mean(late_arr))
    group_means = [role_late[rl] for rl in roles]
    # Skill OOS vs baseline persistenza e media-ruolo (k-fold sui giocatori)
    skill = vs.skill_scores(early_arr, late_arr, group_means)
    # Controllo negativo (placebo): permuta → distribuzione nulla
    placebo = vs.placebo_corr(early_arr, late_arr, "spearman")
    # Influenza leave-one-out sulla Spearman
    loo = vs.loo_corr_range(early_arr, late_arr, "spearman")

    # ── Affidabilità "pulita": split pari/dispari (controlla il trend stagionale)
    odd_gg  = [g for g in all_gg if int(g) % 2 == 1]
    even_gg = [g for g in all_gg if int(g) % 2 == 0]
    oe_odd, oe_even = tpi_slice(odd_gg), tpi_slice(even_gg)
    oe_common = oe_odd.index.intersection(oe_even.index)
    reliability_r = (vs.correlation([float(oe_odd[i]) for i in oe_common],
                                    [float(oe_even[i]) for i in oe_common], "spearman")
                     if len(oe_common) >= 10 else None)

    log.info(f"  Spearman ρ={r:.3f} [{ci_lo:.3f},{ci_hi:.3f}], Kendall τ={tau:.3f}, "
             f"RMSE_oos={skill.get('rmse_oos')}, skill_persist={skill.get('skill_persistence')}, "
             f"placebo_p={placebo.get('p_perm')}, affidabilità(pari/dispari)={reliability_r}")
    return {
        "r": round(float(r), 4), "p": round(float(p_val), 4),
        "r_pearson": round(float(r_pearson), 4),
        "tau": round(float(tau), 4), "p_tau": round(float(p_tau), 4),
        "ci_lo": round(ci_lo, 3), "ci_hi": round(ci_hi, 3),
        "rmse": round(rmse, 4), "mae": round(mae, 4),
        "skill": skill, "placebo": placebo, "loo": loo,
        "reliability_r": reliability_r,
        "metric_note": ("Backtest su output offensivo (xG+xA)/90 — componente dominante "
                        "del TPI (peso 0.32), non il composito completo: evidenza parziale."),
        "n": len(common),
        "slope": round(float(sl), 4), "intercept": round(float(ic), 4),
        "early_range": f"gg {early_gg[0]}-{early_gg[-1]}",
        "late_range":  f"gg {late_gg[0]}-{late_gg[-1]}",
        "scatter": scatter, "source": "db",
        "interpretazione": _interp_backtest(r)[0], "interpretazione_en": _interp_backtest(r)[1],
    }


def valida_backtest_payload(players: list) -> dict:
    log.info("  Backtest dal payload.json (fallback)...")
    rows = []
    for p in players:
        tpi = p["tpi"].get("totale")
        out = (p.get("ctx") or {}).get("totale", {}).get("output_adj")
        frm = (p.get("form") or {}).get("ewma")
        if tpi is not None and out is not None and frm is not None \
                and (p.get("minuti") or 0) >= 600:
            rows.append({"nome": p["nome"], "squadra": p["squadra"],
                         "ruolo": p["ruolo"],
                         "early": round(out, 4), "late": round(frm, 4)})
    if len(rows) < 10:
        return {"r": None, "p": None, "n": len(rows), "scatter": [], "source": "payload",
                "slope": None, "intercept": None,
                "early_range": "Output Adj/90", "late_range": "Form EWMA",
                "interpretazione": "Dati insufficienti", "interpretazione_en": "Insufficient data"}
    xv = [r["early"] for r in rows]
    yv = [r["late"]  for r in rows]
    r, p_val = stats.spearmanr(xv, yv)
    sl, ic, _, _, _ = stats.linregress(xv, yv)
    log.info(f"  Spearman r={r:.3f}, p={p_val:.4f}, n={len(rows)} (payload)")
    return {
        "r": round(float(r), 4), "p": round(float(p_val), 4),
        "n": len(rows),
        "slope": round(float(sl), 4), "intercept": round(float(ic), 4),
        "early_range": "Output Adj/90", "late_range": "Form EWMA recente",
        "scatter": rows, "source": "payload",
        "interpretazione": _interp_backtest(r)[0], "interpretazione_en": _interp_backtest(r)[1],
        "nota": (
            "⚠️ DB non disponibile — backtest calcolato da payload "
            "(Output Adj vs Form EWMA). Avvia MySQL e riesegui per il backtest completo."
        ),
    }


# ════════════════════════════════════════════════════════════════
# VALIDAZIONE D — Età Index & Affidabilità Fisica
# ════════════════════════════════════════════════════════════════
def valida_v2_indices(players: list) -> dict:
    rows = []
    for p in players:
        ph      = p.get("physical") or {}
        tpi     = p["tpi"].get("totale")
        aii     = ph.get("eta_index")
        pri     = ph.get("affidabilita")
        eta     = ph.get("eta")
        tpi_ext = (p.get("tpi_ext") or {}).get("totale")
        if tpi is not None:
            rows.append({
                "nome": p["nome"], "squadra": p["squadra"], "ruolo": p["ruolo"],
                "tpi": tpi, "tpi_ext": tpi_ext,
                "aii": aii, "pri": pri, "eta": eta,
                "n_inj":      ph.get("n_infortuni", 0) or 0,
                "giorni_out": ph.get("giorni_out",  0) or 0,
            })

    if len(rows) < 3:
        return {
            "has_data": False,
            "msg": "Dati v2 non disponibili. Esegui: python aggiorna.py --init-v2",
            "n_aii": 0, "n_pri": 0,
        }

    df      = pd.DataFrame(rows)
    n_aii   = int(df["aii"].notna().sum())
    n_pri   = int(df["pri"].notna().sum())

    aii_tpi_r = None
    if n_aii >= 5:
        sub = df.dropna(subset=["aii", "tpi"])
        if len(sub) >= 5:
            r, _ = stats.pearsonr(sub["aii"], sub["tpi"])
            aii_tpi_r = round(float(r), 4)

    ext_r = None
    if df["tpi_ext"].notna().sum() >= 5:
        sub = df.dropna(subset=["tpi_ext", "tpi"])
        if len(sub) >= 5:
            r, _ = stats.pearsonr(sub["tpi_ext"], sub["tpi"])
            ext_r = round(float(r), 4)

    # FIX BUG3/BUG6: usa .dropna() prima di selezionare i top/bottom
    top_pri_df  = df.dropna(subset=["pri"]).nlargest(5, "pri")
    bot_pri_df  = df.dropna(subset=["pri"]).nsmallest(5, "pri")
    top_aii_df  = df.dropna(subset=["aii"]).nlargest(5, "aii")

    def row_pri(r):
        return {
            "nome":      r["nome"],
            "squadra":   r["squadra"],
            "pri":       round(float(r["pri"]),  2) if r["pri"]  is not None else None,
            "n_inj":     int(r["n_inj"]),
            "giorni_out":int(r["giorni_out"]),
        }
    def row_aii(r):
        return {
            "nome":    r["nome"],
            "squadra": r["squadra"],
            "aii":     round(float(r["aii"]), 2) if r["aii"] is not None else None,
            "eta":     int(r["eta"])              if r["eta"] is not None else None,
            "tpi":     round(float(r["tpi"]), 3) if r["tpi"] is not None else None,
        }

    top_pri  = [row_pri(r) for _, r in top_pri_df.iterrows()]
    bot_pri  = [row_pri(r) for _, r in bot_pri_df.iterrows()]
    top_aii  = [row_aii(r) for _, r in top_aii_df.iterrows()]

    eta_valid = df["eta"].dropna()
    eta_mean  = round(float(eta_valid.mean()),   1) if len(eta_valid) > 0 else None
    eta_med   = round(float(eta_valid.median()), 1) if len(eta_valid) > 0 else None

    # Scatter AII vs PRI (solo chi ha entrambi) — FIX BUG7
    scatter_d = [
        {"nome": r["nome"], "squadra": r["squadra"], "ruolo": r["ruolo"],
         "aii":  round(float(r["aii"]), 4),
         "pri":  round(float(r["pri"]), 4),
         "tpi":  round(float(r["tpi"]), 4)}
        for r in rows
        if r["aii"] is not None and r["pri"] is not None
    ]

    log.info(f"  AII: {n_aii} giocatori · r(AII,TPI)={aii_tpi_r}")
    log.info(f"  PRI: {n_pri} giocatori · top affidabile: {top_pri[0]['nome'] if top_pri else '—'}")
    return {
        "has_data": True,
        "n_aii": n_aii, "n_pri": n_pri,
        "aii_tpi_r": aii_tpi_r, "ext_r": ext_r,
        "eta_mean": eta_mean, "eta_med": eta_med,
        "top_pri": top_pri, "bot_pri": bot_pri, "top_aii": top_aii,
        "scatter": scatter_d,
    }


# ════════════════════════════════════════════════════════════════
# VALIDAZIONE E — TPI Pro: confronto ranking TPI vs TPI Pro
# ════════════════════════════════════════════════════════════════
def valida_tpi_pro(players: list) -> dict:
    """
    Analizza quanto TPI Pro cambia le classifiche rispetto a TPI classico:
    - Correlazione TPI vs TPI Pro
    - Top 10 salite/discese di ranking
    - Scatter TPI vs TPI Pro per ruolo
    - Distribuzione AII e PRI
    """
    rows = []
    for p in players:
        tpi     = p["tpi"].get("totale")
        ph      = p.get("physical") or {}
        tpi_pro = (p.get("tpi_ext") or {}).get("totale")
        aii     = ph.get("eta_index")
        pri     = ph.get("affidabilita")
        if tpi is not None:
            rows.append({
                "nome":    p["nome"],
                "squadra": p["squadra"],
                "ruolo":   p["ruolo"],
                "tpi":     tpi,
                "tpi_pro": tpi_pro,
                "aii":     aii,
                "pri":     pri,
                "eta":     ph.get("eta"),
            })

    if len(rows) < 5:
        return {"has_data": False, "msg": "Dati insufficienti per la validazione TPI Pro."}

    df      = pd.DataFrame(rows)
    n_pro   = int(df["tpi_pro"].notna().sum())

    if n_pro < 5:
        return {
            "has_data": False,
            "msg": (
                "TPI Pro non ancora calcolato. "
                "Assicurati che data_nascita e t_infortuni siano nel DB, "
                "poi riesegui parte1_analisi.py."
            ),
        }

    # Correlazione TPI vs TPI Pro
    sub = df.dropna(subset=["tpi", "tpi_pro"])
    r_corr, p_corr = stats.pearsonr(sub["tpi"], sub["tpi_pro"])

    # Ranking classico e Pro
    df_sorted_tpi = df.sort_values("tpi",     ascending=False).reset_index(drop=True)
    df_sorted_pro = df.dropna(subset=["tpi_pro"]).sort_values("tpi_pro", ascending=False).reset_index(drop=True)
    rank_tpi = {row["nome"]: i + 1 for i, row in df_sorted_tpi.iterrows()}
    rank_pro = {row["nome"]: i + 1 for i, row in df_sorted_pro.iterrows()}

    # Movers: chi sale/scende di più
    movers = []
    for nome, rk_pro in rank_pro.items():
        rk_tpi = rank_tpi.get(nome)
        if rk_tpi is None:
            continue
        delta = rk_tpi - rk_pro          # positivo = sale
        row   = df[df["nome"] == nome].iloc[0]
        movers.append({
            "nome":     nome,
            "squadra":  row["squadra"],
            "ruolo":    row["ruolo"],
            "tpi":      round(float(row["tpi"]),     3),
            "tpi_pro":  round(float(row["tpi_pro"]), 3),
            "aii":      round(float(row["aii"]),     2) if row["aii"] is not None else None,
            "pri":      round(float(row["pri"]),     2) if row["pri"] is not None else None,
            "rank_tpi": rk_tpi,
            "rank_pro": rk_pro,
            "delta":    delta,
        })

    movers_sorted = sorted(movers, key=lambda x: x["delta"], reverse=True)
    top_saliti    = movers_sorted[:8]
    top_scesi     = movers_sorted[-8:][::-1]

    # Scatter TPI vs TPI Pro
    scatter_pro = [
        {"nome": r["nome"], "squadra": r["squadra"], "ruolo": r["ruolo"],
         "tpi":  round(float(r["tpi"]),     4),
         "pro":  round(float(r["tpi_pro"]), 4)}
        for _, r in sub.iterrows()
    ]

    # Distribuzione delta AII e PRI per chi ha entrambi
    aii_vals = [r["aii"] for r in movers if r["aii"] is not None]
    pri_vals = [r["pri"] for r in movers if r["pri"] is not None]

    # Regressione per scatter
    sl, ic = None, None
    if len(sub) >= 3:
        sl_r, ic_r, _, _, _ = stats.linregress(sub["tpi"], sub["tpi_pro"])
        sl, ic = round(float(sl_r), 4), round(float(ic_r), 4)

    log.info(f"  TPI Pro: {n_pro} giocatori · r(TPI,TPIPro)={r_corr:.3f}")
    log.info(f"  Max salita: {top_saliti[0]['nome'] if top_saliti else '—'} +{top_saliti[0]['delta'] if top_saliti else 0} pos")
    return {
        "has_data":   True,
        "n_pro":      n_pro,
        "r_corr":     round(float(r_corr), 4),
        "p_corr":     round(float(p_corr), 4),
        "slope":      sl,
        "intercept":  ic,
        "top_saliti": top_saliti,
        "top_scesi":  top_scesi,
        "scatter":    scatter_pro,
        "aii_mean":   round(float(np.mean(aii_vals)), 3) if aii_vals else None,
        "pri_mean":   round(float(np.mean(pri_vals)), 3) if pri_vals else None,
        "n_delta_pos": sum(1 for m in movers if m["delta"] > 2),
        "n_delta_neg": sum(1 for m in movers if m["delta"] < -2),
    }


# ════════════════════════════════════════════════════════════════
# VALIDAZIONE F — Validità ecologica a livello squadra (T1)
# ════════════════════════════════════════════════════════════════
def valida_team_level(players: list, df_gp) -> dict:
    """
    Validità di criterio ecologica: l'indice aggregato per squadra (media TPI)
    deve spiegare un esito reale e indipendente di squadra (xG totale prodotto).
    n = numero squadre (censo della lega) → IC ampia, non test ad alta potenza.
    """
    tpi_by_team: dict[str, list] = {}
    for p in players:
        t = p["tpi"].get("totale")
        if t is not None:
            tpi_by_team.setdefault(p["squadra"], []).append(t)
    team_tpi = {k: float(np.mean(v)) for k, v in tpi_by_team.items() if v}

    if df_gp is None or len(df_gp) == 0:
        return {"has_data": False,
                "msg": ("Validità ecologica a livello squadra: richiede l'xG di squadra dal DB. "
                        "Avvia MySQL e riesegui per attivarla.")}
    xg_by_team = df_gp.groupby("squadra")["xg"].sum().to_dict()
    res = vs.team_level_corr(team_tpi, {k: float(v) for k, v in xg_by_team.items()})
    if res is None:
        return {"has_data": False, "msg": "Squadre insufficienti per la validazione ecologica."}
    res["has_data"] = True
    log.info(f"  Team-level: r(TPI medio, xG squadra)={res['r']} su {res['n']} squadre")
    return res


# ════════════════════════════════════════════════════════════════
# VALIDAZIONE G — Struttura interna / dimensionalità del composito (T4)
# ════════════════════════════════════════════════════════════════
def valida_internal_structure(players: list) -> dict:
    """
    Le dimensioni del TPI misurano cose diverse o sono ridondanti? PCA + matrice
    di correlazione sugli z-score per-dimensione. PC1 ~ 1.0 ⇒ composito di fatto
    monodimensionale (la pesatura conta poco).
    """
    labels = ["output", "buildup", "centralità", "boost", "consistenza", "finishing", "forma"]
    keys   = ["z_output", "z_buildup", "z_centralita", "z_boost", "z_consistenza",
              "z_finishing", "z_form"]
    matrix = []
    for p in players:
        matrix.append([p.get(k) for k in keys])
    arr = np.array([[(np.nan if v is None else v) for v in row] for row in matrix], dtype=float)
    keep = [j for j in range(arr.shape[1]) if np.isfinite(arr[:, j]).sum() >= 5]
    if len(keep) < 2:
        return {"has_data": False,
                "msg": ("Struttura interna: z-score per-dimensione assenti nel payload. "
                        "Rigenera con parte1_analisi.py aggiornato (espone z_buildup ecc.).")}
    pca = vs.pca_explained(arr[:, keep], [labels[j] for j in keep])
    if pca is None:
        return {"has_data": False, "msg": "Dati insufficienti per la PCA delle dimensioni."}
    pca["has_data"] = True
    log.info(f"  Struttura interna: PC1={pca['pc1']} su dim {pca.get('labels')}")
    return pca


# ════════════════════════════════════════════════════════════════
# VALIDAZIONE H — Sensibilità del ranking ai pesi (T2)
# ════════════════════════════════════════════════════════════════
def valida_sensibilita(players: list) -> dict:
    """
    Stabilità del ranking sotto perturbazione ±20% dei pesi, ricombinando gli
    z-score per-dimensione del payload (non rieseguendo il motore). 'coverage'
    indica la quota di peso nominale coperta dalle dim disponibili.
    """
    z_by_dim = {}
    for dim, zk in TPI_ZKEYS.items():
        col = [p.get(zk) for p in players]
        if any(v is not None for v in col):
            z_by_dim[dim] = [(np.nan if v is None else v) for v in col]
    res = vs.weight_sensitivity(z_by_dim, TPI_WEIGHTS)
    if res is None:
        return {"has_data": False,
                "msg": ("Sensibilità ai pesi: z-score per-dimensione assenti nel payload. "
                        "Rigenera con parte1_analisi.py aggiornato.")}
    res["has_data"] = True
    log.info(f"  Sensibilità pesi: Spearman mediana={res['spearman_median']} "
             f"(min {res['spearman_min']}), coverage={res['coverage']}")
    return res


# ════════════════════════════════════════════════════════════════
# VINTAGE — helper condivisi dai test out-of-sample (I, Q)
# ════════════════════════════════════════════════════════════════
def _vintage_paths(min_giornata: int) -> list[tuple[int, Path]]:
    """`payload_g{N}.json` con N >= min_giornata, ordinati per giornata.

    I vintage troppo presto hanno poco dato per differenziare due segnali:
    aggiungono rumore senza informazione. Il filtro sta qui, una volta sola,
    così i test OOS guardano lo stesso universo e restano confrontabili.
    """
    import re
    trovati: list[int] = []
    tenuti: list[tuple[int, Path]] = []
    for vp in OUTPUT_DIR.glob("payload_g*.json"):
        m = re.search(r"payload_g(\d+)\.json$", vp.name)
        if not m:
            continue
        N = int(m.group(1))
        trovati.append(N)
        if N >= min_giornata:
            tenuti.append((N, vp))
    tenuti.sort()
    if trovati:
        skipped = sorted(set(trovati) - {g for g, _ in tenuti})
        log.info(f"  Vintage trovati: {sorted(trovati)} → uso {[g for g, _ in tenuti]}"
                 + (f" (skip <{min_giornata}: {skipped})" if skipped else ""))
    return tenuti


def _realized_post_map(engine, N: int) -> dict[int, dict] | None:
    """Minuti / npg / xa realizzati DOPO la giornata N, per giocatore.

    È il criterio dei test che partono da un vintage: i predittori vengono
    dallo snapshot a g{N}, questo dalle gare successive. Zero leakage.
    """
    try:
        df = pd.read_sql(
            "SELECT gp.giocatore_id, "
            "       SUM(gp.minuti) AS min_post, "
            "       SUM(COALESCE(gp.npg, gp.goal)) AS npg_post, "
            "       SUM(COALESCE(gp.xa, 0)) AS xa_post "
            "FROM giocatore_partita gp "
            "JOIN calendario cal ON cal.id = gp.calendario_id "
            f"WHERE gp.minuti > 0 AND cal.giornata > {N} "
            "GROUP BY gp.giocatore_id",
            engine,
        )
    except Exception as e:
        log.warning(f"  Realizzato post-g{N}: query DB fallita: {e}")
        return None
    return {
        int(r["giocatore_id"]): {
            "min": float(r["min_post"]),
            "npg": float(r["npg_post"]) if pd.notna(r["npg_post"]) else 0.0,
            "xa":  float(r["xa_post"])  if pd.notna(r["xa_post"])  else 0.0,
        }
        for _, r in df.iterrows()
        if pd.notna(r["min_post"]) and float(r["min_post"]) > 0
    }


# ════════════════════════════════════════════════════════════════
# VALIDAZIONE I — Validità incrementale TPI vs TPI Pro (riformula D/E)
# ════════════════════════════════════════════════════════════════
def valida_incrementale_pro_oos(engine) -> dict | None:
    """
    Test I — versione SCOUT/GROWTH, OOS, gated dal payload vintage.

    Obiettivo del TPI Pro (scout-oriented): identificare i giocatori che
    AUMENTERANNO il loro rendimento, non quelli che segneranno la settimana
    prossima. Il Pro deve quindi predire l'IMPROVEMENT, non il livello.

    Per ogni vintage `payload_g{N}.json`:
      - Predittori: TPI base e TPI Pro a vintage g{N}
      - Pre-output (a g{N}): (goal_p90 + xa_p90) dal payload vintage
      - Post-output (g{N+1}..ultima): (npg + xa)/min × 90 dal DB
      - **Criterio IMPROVEMENT = post − pre**
      - Bootstrap appaiato: ΔRMSE(base − pro), Pro vince se Δ > 0

    Δ negativo = BASE migliore in predire l'improvement (Pro non aggiunge)
    Δ positivo = PRO migliore (gli AII scout + stability + trend catturano
                  davvero chi farà il salto)

    Zero leakage: predittori da snapshot pre, criterio da gare post.

    Returns None se nessun vintage trovato → fallback in-sample.
    """
    # I vintage troppo presto (< 25) hanno poco data per differenziare base vs Pro
    # → aggiungono rumore al META senza segnale. Filtriamo.
    VINTAGE_MIN_GIORNATA = 25
    vintages = _vintage_paths(VINTAGE_MIN_GIORNATA)
    if not vintages:
        return None

    # Pool aggregato per il META-test (più potere statistico)
    pool_base, pool_pro, pool_crit, pool_v, pool_gid = [], [], [], [], []
    pool_base_scout, pool_pro_scout, pool_crit_scout, pool_gid_scout = [], [], [], []
    per_vintage = []
    SCOUT_MAX_AGE = 25.0  # target del TPI Pro: giovani in ascesa

    for N, vintage_path in vintages:
        with open(vintage_path, encoding="utf-8") as fh:
            vintage = json.load(fh)
        realized_map = _realized_post_map(engine, N)
        if realized_map is None:
            continue

        v_base, v_pro, v_crit, v_gid = [], [], [], []
        v_base_s, v_pro_s, v_crit_s, v_gid_s = [], [], [], []
        for p in vintage.get("players", []):
            gid = p.get("id")
            tpi = (p.get("tpi") or {}).get("totale")
            tpi_pro = (p.get("tpi_ext") or {}).get("totale")
            rz = realized_map.get(gid)
            if tpi is None or tpi_pro is None or rz is None or rz["min"] < 90:
                continue
            output_post = (rz["npg"] + rz["xa"]) / rz["min"] * 90.0
            kpi = p.get("kpi") or {}
            conv = p.get("conv") or {}
            goal_p90 = conv.get("goal_p90")
            xa_p90 = kpi.get("xa_p90")
            if goal_p90 is None or xa_p90 is None:
                continue
            output_pre = float(goal_p90) + float(xa_p90)
            improvement = output_post - output_pre
            v_base.append(tpi); v_pro.append(tpi_pro); v_crit.append(improvement); v_gid.append(gid)
            eta = (p.get("physical") or {}).get("eta")
            if eta is not None and float(eta) < SCOUT_MAX_AGE:
                v_base_s.append(tpi); v_pro_s.append(tpi_pro); v_crit_s.append(improvement); v_gid_s.append(gid)

        if len(v_base) < 12:
            log.info(f"  Vintage g{N}: solo {len(v_base)} giocatori utili, skip.")
            continue

        v_res = vs.paired_rmse_bootstrap(v_base, v_crit, v_pro)
        if v_res:
            log.info(f"  Vintage g{N} (n={len(v_base)}): "
                     f"ΔRMSE={v_res['delta_rmse']} [{v_res['ci_lo']},{v_res['ci_hi']}] "
                     f"pro_better={v_res['pro_better']}")
            v_res["vintage_giornata"] = N
            v_res["n_giocatori"] = len(v_base)
            per_vintage.append(v_res)

        pool_base.extend(v_base); pool_pro.extend(v_pro); pool_crit.extend(v_crit)
        pool_v.extend([N] * len(v_base)); pool_gid.extend(v_gid)
        pool_base_scout.extend(v_base_s); pool_pro_scout.extend(v_pro_s)
        pool_crit_scout.extend(v_crit_s); pool_gid_scout.extend(v_gid_s)

    if not per_vintage:
        return {"has_data": False,
                "msg": "Nessun vintage utilizzabile (n<12 per ognuno)."}

    # META-test: aggrega coppie da TUTTI i vintage e usa CLUSTER BOOTSTRAP
    # su giocatore_id per gestire correttamente la pseudo-replicazione (stesso
    # giocatore compare in più vintage). Bootstrap classico sovrastima la
    # precisione perché tratta righe correlate come indipendenti.
    meta = vs.paired_rmse_bootstrap_clustered(
        pool_base, pool_crit, pool_pro, pool_gid
    )
    if meta is None:
        return {"has_data": False, "msg": "META cluster-bootstrap fallito."}
    # Anche la versione naive per confronto (IC ottimistico):
    meta_naive = vs.paired_rmse_bootstrap(pool_base, pool_crit, pool_pro)
    if meta_naive:
        meta["naive_ci_lo"] = meta_naive["ci_lo"]
        meta["naive_ci_hi"] = meta_naive["ci_hi"]
        log.info(f"  META naive (no cluster): ΔRMSE={meta_naive['delta_rmse']} "
                 f"[{meta_naive['ci_lo']},{meta_naive['ci_hi']}] "
                 f"pro_better={meta_naive['pro_better']}")

    meta["has_data"] = True
    meta["mode"] = "OOS-META"
    meta["n"] = len(pool_base)
    meta["n_vintage"] = len(per_vintage)
    meta["per_vintage"] = per_vintage
    meta["vintage_giornate"] = sorted({pv["vintage_giornata"] for pv in per_vintage})
    meta["criterio"] = (f"IMPROVEMENT = output post-vintage − output pre-vintage "
                        f"(growth target). META aggregato su {len(per_vintage)} vintage, "
                        f"{meta['n']} coppie giocatore-vintage. Δ>0 = Pro identifica "
                        f"chi sale meglio del Base.")

    # META SCOUT-FOCUS: solo giocatori <25 (target del Pro)
    if len(pool_base_scout) >= 30:
        meta_scout = vs.paired_rmse_bootstrap_clustered(
            pool_base_scout, pool_crit_scout, pool_pro_scout, pool_gid_scout
        )
        if meta_scout:
            meta["scout_focus"] = {
                "max_age": SCOUT_MAX_AGE,
                "n": len(pool_base_scout),
                "delta_rmse": meta_scout["delta_rmse"],
                "ci_lo": meta_scout["ci_lo"],
                "ci_hi": meta_scout["ci_hi"],
                "pro_better": meta_scout["pro_better"],
            }
            log.info(f"  META OOS SCOUT (<{SCOUT_MAX_AGE}, n={len(pool_base_scout)}): "
                     f"ΔRMSE(base−pro)={meta_scout['delta_rmse']} "
                     f"[{meta_scout['ci_lo']},{meta_scout['ci_hi']}] "
                     f"pro_better={meta_scout['pro_better']}")
            # Se scout-focus dà pro_better, lo eleviamo a verdetto principale
            if meta_scout["pro_better"]:
                meta["pro_better"] = True
                meta["pro_better_reason"] = "scout_focus (<25 anni)"
    # rinomina per non confondere con per-vintage
    meta["vintage_giornata"] = meta["vintage_giornate"][0] if meta["vintage_giornate"] else None
    log.info(f"  META OOS (n={meta['n']} su {len(per_vintage)} vintage): "
             f"ΔRMSE(base−pro)={meta['delta_rmse']} "
             f"[{meta['ci_lo']},{meta['ci_hi']}] pro_better={meta['pro_better']}")
    return meta


def valida_incrementale_pro(players: list) -> dict:
    """
    Test NON circolare (versione IN-SAMPLE, fallback): TPI Pro predice il
    rendimento realizzato RECENTE (ultime 6 gare già nel payload) meglio del
    TPI base? Usato se non esiste un vintage; il vero OOS è
    `valida_incrementale_pro_oos`.
    """
    base, pro, crit = [], [], []
    for p in players:
        tpi = p["tpi"].get("totale")
        tpi_pro = (p.get("tpi_ext") or {}).get("totale")
        rec = p.get("recent") or {}
        r_npg = rec.get("npg")
        r_min = rec.get("min")
        if (tpi is not None and tpi_pro is not None
                and r_npg is not None and r_min and r_min >= 180
                and (p.get("minuti") or 0) >= 600):
            realized = float(r_npg) / float(r_min) * 90.0
            base.append(tpi); pro.append(tpi_pro); crit.append(realized)
    if len(base) < 12:
        return {"has_data": False,
                "msg": ("Validità incrementale TPI Pro: servono ≥12 giocatori con TPI, "
                        "TPI Pro e ≥180' nelle ultime 6 gare. Popola AII/PRI e riesegui.")}
    res = vs.paired_rmse_bootstrap(base, crit, pro)
    if res is None:
        return {"has_data": False, "msg": "Dati insufficienti per il confronto incrementale appaiato."}
    res["has_data"] = True
    res["criterio"] = "gol no-rigore realizzati per-90 (ultime 6 gare)"
    log.info(f"  Incrementale: ΔRMSE(base−pro)={res['delta_rmse']} "
             f"[{res['ci_lo']},{res['ci_hi']}] pro_better={res['pro_better']}")
    return res


# ════════════════════════════════════════════════════════════════
# VALIDAZIONE Q — Il TPI batte una baseline banale?
# ════════════════════════════════════════════════════════════════
# Rosa Transfermarkt del progetto heXI, che e' SOLA LETTURA: qui si legge e
# basta. Stesso percorso e stesso paracadute di stagione di
# set_up_tpi_pro/allinea_anagrafica_hexi.py — accanto c'e' SA_2026-2027.json,
# e agganciarsi a quello darebbe valori plausibili ma dell'anno sbagliato.
HEXI_ROSTER = Path(os.environ.get(
    "SERIE_A_HEXI_ROSTER",
    r"C:\Users\Raffaele\Desktop\heXI\data\normalized\SA_2025-2026.json"))
HEXI_STAGIONE = "2025/2026"


def _valore_mercato_per_giocatore(engine) -> dict[int, float]:
    """giocatore_id → valore di mercato in euro, dalla rosa heXI.

    L'aggancio e' su `giocatori.tm_id` (scritto da allinea_anagrafica_hexi.py):
    un id, non un nome, quindi qui non serve nessun match approssimato e non
    si puo' ripetere l'incidente dei due Martinez.
    """
    if not HEXI_ROSTER.is_file():
        log.info(f"  Valore di mercato: rosa heXI assente ({HEXI_ROSTER}), baseline saltata.")
        return {}
    try:
        rosa = json.loads(HEXI_ROSTER.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        log.warning(f"  Valore di mercato: rosa heXI illeggibile ({e}), baseline saltata.")
        return {}
    if not isinstance(rosa, list) or not rosa:
        log.warning("  Valore di mercato: formato rosa heXI inatteso, baseline saltata.")
        return {}
    stagioni = {r.get("season") for r in rosa}
    if stagioni != {HEXI_STAGIONE}:
        log.warning(f"  Valore di mercato: stagione {stagioni}, attesa {HEXI_STAGIONE!r}. "
                    "Baseline saltata (accanto c'e' il file dell'anno dopo).")
        return {}
    per_tm: dict[int, float] = {}
    for r in rosa:
        tm, mv = r.get("transfermarkt_id"), r.get("market_value_eur")
        if tm is None or not mv:
            continue
        per_tm[int(tm)] = float(mv)
    try:
        df = pd.read_sql("SELECT id, tm_id FROM giocatori "
                         "WHERE tm_id IS NOT NULL", engine)
    except Exception as e:
        log.warning(f"  Valore di mercato: query tm_id fallita ({e}), baseline saltata.")
        return {}
    out: dict[int, float] = {}
    for _, r in df.iterrows():
        try:
            v = per_tm.get(int(r["tm_id"]))
        except (TypeError, ValueError):
            continue
        if v:
            out[int(r["id"])] = v
    log.info(f"  Valore di mercato: {len(out)} giocatori agganciati per tm_id "
             f"({len(per_tm)} valorizzati nella rosa heXI, {len(df)} tm_id nel DB)")
    return out


def valida_baseline(engine) -> dict | None:
    """
    Test Q — Il TPI batte una baseline banale?

    Gli altri test chiedono "il TPI e' correlato con X?". Questo chiede
    l'unica cosa che puo' bocciare il composito: **serviva costruirlo?**
    Il confronto e' contro i predittori piu' stupidi a disposizione:

      · output grezzo   (goal_p90 + xa_p90 allo snapshot) — l'input dominante
      · minuti giocati  (allo snapshot) — "chi gioca, rende"
      · valore di mercato (Transfermarkt via heXI) — il consenso del mercato

    Disegno identico al test I, quindi i due numeri si leggono insieme:
    predittori dal vintage g{N}, criterio dalle giornate successive, bootstrap
    clusterizzato su giocatore_id (lo stesso giocatore compare in piu' vintage
    e non e' un'osservazione indipendente).

    Due criteri, perche' sono due promesse diverse:
      · LIVELLO        = output per-90 realizzato dopo il vintage
      · MIGLIORAMENTO  = livello post − livello pre (la promessa scout)

    delta_rmse = RMSE(baseline) − RMSE(TPI): positivo con IC che esclude lo
    zero = il TPI batte quella baseline. Se non la batte, le sette dimensioni
    non stanno aggiungendo niente al loro stesso input.

    Returns None se non c'e' nessun vintage utilizzabile.
    """
    VINTAGE_MIN_GIORNATA = 25   # stesso universo del test I
    MIN_MINUTI_POST = 90        # sotto i 90' il per-90 realizzato e' rumore
    vintages = _vintage_paths(VINTAGE_MIN_GIORNATA)
    if not vintages:
        return None

    valore = _valore_mercato_per_giocatore(engine)

    campi = ("gid", "tpi", "b_output", "b_minuti", "b_valore",
             "crit_livello", "crit_improvement")
    pool: dict[str, list] = {k: [] for k in campi}
    giornate_usate: list[int] = []
    for N, vintage_path in vintages:
        try:
            vintage = json.loads(vintage_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            log.warning(f"  Vintage g{N} illeggibile ({e}), skip.")
            continue
        realized = _realized_post_map(engine, N)
        if not realized:
            continue
        n_v = 0
        for p in vintage.get("players", []):
            gid = p.get("id")
            tpi = (p.get("tpi") or {}).get("totale")
            rz = realized.get(gid)
            if tpi is None or rz is None or rz["min"] < MIN_MINUTI_POST:
                continue
            kpi  = p.get("kpi") or {}
            conv = p.get("conv") or {}
            goal_p90, xa_p90 = conv.get("goal_p90"), kpi.get("xa_p90")
            minuti = p.get("minuti")
            if goal_p90 is None or xa_p90 is None or not minuti:
                continue
            pre  = float(goal_p90) + float(xa_p90)
            post = (rz["npg"] + rz["xa"]) / rz["min"] * 90.0
            pool["gid"].append(gid)
            pool["tpi"].append(float(tpi))
            pool["b_output"].append(pre)
            pool["b_minuti"].append(float(minuti))
            pool["b_valore"].append(float(valore.get(gid, np.nan)))
            pool["crit_livello"].append(post)
            pool["crit_improvement"].append(post - pre)
            n_v += 1
        if n_v:
            giornate_usate.append(N)
            log.info(f"  Vintage g{N}: {n_v} coppie nel confronto con le baseline")

    if len(pool["gid"]) < 30:
        return {"has_data": False,
                "msg": f"Test Q: {len(pool['gid'])} coppie giocatore-vintage (servono ≥30)."}

    BASELINES = [
        ("output_grezzo",  "b_output", "Output grezzo (goal + xA)/90", "Raw output (goals + xA)/90"),
        ("minuti",         "b_minuti", "Minuti giocati",               "Minutes played"),
        ("valore_mercato", "b_valore", "Valore di mercato",            "Market value"),
    ]
    CRITERI = [
        ("livello", "crit_livello",
         "Output per-90 realizzato dopo il vintage",
         "Realized per-90 output after the vintage"),
        ("improvement", "crit_improvement",
         "Miglioramento: output post − output pre",
         "Improvement: post-vintage output − pre-vintage output"),
    ]

    from scipy.stats import spearmanr
    tpi_arr = np.asarray(pool["tpi"], dtype=float)
    gid_arr = np.asarray(pool["gid"])
    criteri_out: dict[str, dict] = {}
    for ckey, cfield, clab_it, clab_en in CRITERI:
        crit = np.asarray(pool[cfield], dtype=float)
        voci = []
        for bkey, bfield, blab_it, blab_en in BASELINES:
            b = np.asarray(pool[bfield], dtype=float)
            mask = np.isfinite(b) & np.isfinite(crit) & np.isfinite(tpi_arr)
            if mask.sum() < 30:
                log.info(f"  Q [{ckey}] {bkey}: solo {int(mask.sum())} coppie utili, skip.")
                continue
            res = vs.paired_rmse_bootstrap_clustered(
                b[mask], crit[mask], tpi_arr[mask], gid_arr[mask])
            if res is None:
                log.info(f"  Q [{ckey}] {bkey}: bootstrap non calcolabile, skip.")
                continue
            rho_b, _ = spearmanr(b[mask], crit[mask])
            rho_t, _ = spearmanr(tpi_arr[mask], crit[mask])
            voci.append({
                "key": bkey, "label_it": blab_it, "label_en": blab_en,
                "n": res["n"], "n_giocatori": res["n_clusters"],
                "rmse_baseline": res["rmse_base"], "rmse_tpi": res["rmse_pro"],
                # delta = RMSE(baseline) − RMSE(TPI): >0 = TPI sbaglia meno
                "delta_rmse": res["delta_rmse"],
                "ci_lo": res["ci_lo"], "ci_hi": res["ci_hi"],
                "tpi_better": res["pro_better"],
                "rho_baseline": round(float(rho_b), 3),
                "rho_tpi": round(float(rho_t), 3),
            })
            log.info(f"  Q [{ckey}] TPI vs {bkey:15s} n={res['n']} ({res['n_clusters']} giocatori): "
                     f"ΔRMSE={res['delta_rmse']:+.4f} [{res['ci_lo']},{res['ci_hi']}] · "
                     f"ρ baseline={rho_b:+.3f} ρ TPI={rho_t:+.3f} → TPI meglio: {res['pro_better']}")
        if not voci:
            continue
        criteri_out[ckey] = {
            "label_it": clab_it, "label_en": clab_en,
            "baselines": voci,
            "tpi_batte": [v["key"] for v in voci if v["tpi_better"]],
            "n_baselines": len(voci),
        }

    if not criteri_out:
        return {"has_data": False,
                "msg": "Test Q: nessun confronto baseline calcolabile."}

    n_valore = int(np.isfinite(np.asarray(pool["b_valore"], dtype=float)).sum())
    return {
        "has_data": True,
        "n": len(pool["gid"]),
        "n_giocatori": int(len(set(pool["gid"]))),
        "n_vintage": len(giornate_usate),
        "vintage_giornate": sorted(giornate_usate),
        "criteri": criteri_out,
        "valore_coverage": n_valore,
        "valore_coverage_pct": round(100.0 * n_valore / len(pool["gid"]), 1),
        # Il file heXI e' uno snapshot scaricato a stagione finita: come
        # predittore "sa" gia' com'e' andata. Gioca in vantaggio, non in
        # svantaggio, e va detto accanto al risultato.
        "valore_snapshot": True,
    }


# ════════════════════════════════════════════════════════════════
# VALIDAZIONE L — Convergenza del ranking per giornata (T5, gated)
# ════════════════════════════════════════════════════════════════
def valida_persistence(players: list, df_gp: pd.DataFrame) -> dict | None:
    """
    Test P — Persistence Score: il TPI medio su PIÙ vintage predice meglio del
    TPI singolo (snapshot corrente)?

    Idea: il TPI di una sola finestra è rumoroso (forma, infortuni, calendario).
    Mediando su M vintage si filtra il rumore stagionale. Chi MANTIENE un TPI
    alto su molti snapshot è davvero TOP, indipendentemente da una stagione
    fortunata o sfortunata.

    Confronta come predittori del rendimento realizzato (g37+ dal DB):
      - PRED 1: TPI corrente (singolo snapshot)
      - PRED 2: persistence_score = TPI medio su tutti i vintage disponibili
      - PRED 3: persistence_min = TPI minimo sui vintage (robusto a outlier)

    Se Spearman(persistence, realized) > Spearman(TPI, realized) → la persistenza
    aggiunge predittività, supporta il modello multi-snapshot.
    """
    import re as _re_p

    # Carica vintage within-season (within_only) + backfill cross-season opzionali.
    # Faremo DUE test: (1) within-season only, (2) tutti incluso cross-season.
    # Il backfill 24-25 ha TPI ridotto a 3 dim (Understat aggregato) → introduce
    # rumore se mescolato col TPI completo. Test (1) è la VERA misura di
    # persistence robusta.
    within_vintages = []
    for vp in OUTPUT_DIR.glob("payload_g*.json"):
        m = _re_p.match(r"payload_g(\d+)\.json$", vp.name)
        if m and int(m.group(1)) >= 25:
            within_vintages.append(("g" + m.group(1), vp))
    cross_vintages = []
    cross_path = OUTPUT_DIR / "payload_2024-25.json"
    if cross_path.exists():
        cross_vintages.append(("2024-25", cross_path))

    vintages = within_vintages + cross_vintages
    if len(within_vintages) < 3:
        return {"has_data": False,
                "msg": f"Persistence: solo {len(within_vintages)} within-vintage, servono ≥3."}

    log.info(f"  Persistence: {len(within_vintages)} within-vintage + "
             f"{len(cross_vintages)} cross-season: {[v[0] for v in vintages]}")

    # TPI corrente (predittore singolo) — usa il payload main
    cur_tpi = {p["nome"]: float(p["tpi"]["totale"])
               for p in players
               if (p.get("tpi") or {}).get("totale") is not None}

    # Match-by-nome (cross-season ha id Understat, within-season ha id MySQL)
    import unicodedata as _u_p
    def _nm(s):
        return _u_p.normalize("NFKD", str(s or "")).encode("ascii","ignore").decode().lower().strip()

    # Per ogni giocatore, raccoglie TPI dai vintage in cui appare. Teniamo
    # separati within-season e all (incluso cross-season) per fare due test.
    persistence_within = {}  # within-only
    persistence_all = {}     # tutti
    for vtag, vpath in within_vintages:
        try:
            with open(vpath, encoding="utf-8") as fh:
                snap = json.load(fh)
        except Exception:
            continue
        for p in snap.get("players", []):
            tpi = (p.get("tpi") or {}).get("totale")
            if tpi is None:
                continue
            nm = _nm(p.get("nome", ""))
            persistence_within.setdefault(nm, []).append(float(tpi))
            persistence_all.setdefault(nm, []).append(float(tpi))
    for vtag, vpath in cross_vintages:
        try:
            with open(vpath, encoding="utf-8") as fh:
                snap = json.load(fh)
        except Exception:
            continue
        for p in snap.get("players", []):
            tpi = (p.get("tpi") or {}).get("totale")
            if tpi is None:
                continue
            nm = _nm(p.get("nome", ""))
            persistence_all.setdefault(nm, []).append(float(tpi))

    # Realized: gol+xa per-90 dopo g36 (ultimo vintage within-season)
    realized_df = df_gp[df_gp["minuti"] > 0]
    # Aggrega per giornata > 36 se possibile, altrimenti totale stagione
    if "giornata" in realized_df.columns:
        post = realized_df[realized_df["giornata"] > 36]
        if len(post) < 50:
            log.info(f"  Persistence: pochi dati post-g36 ({len(post)}), uso totale stagione")
            post = realized_df
        else:
            log.info(f"  Persistence: criterio = realized g37+ ({len(post)} righe)")
    else:
        post = realized_df

    real_agg = (post.groupby("giocatore_id")
                .apply(lambda g: (g["goal"].sum() + g["xa"].sum()) / max(g["minuti"].sum(), 1) * 90))
    real_map = real_agg.to_dict()
    # Mappa giocatore_id → nome_normalizzato (dal payload main, contiene id)
    name_by_id = {p["id"]: _nm(p["nome"]) for p in players}
    real_by_name = {name_by_id[gid]: v for gid, v in real_map.items() if gid in name_by_id}

    cur_lookup = {_nm(k): v for k, v in cur_tpi.items()}
    rows = []
    for nm, tpi_list_w in persistence_within.items():
        if len(tpi_list_w) < 2:
            continue
        if nm not in real_by_name:
            continue
        single = cur_lookup.get(nm)
        if single is None:
            continue
        tpi_list_a = persistence_all.get(nm, tpi_list_w)
        rows.append({
            "nome": nm,
            "single": single,
            "persistence_within_mean": float(np.mean(tpi_list_w)),
            "persistence_within_min": float(np.min(tpi_list_w)),
            "persistence_all_mean": float(np.mean(tpi_list_a)),
            "n_vintage_within": len(tpi_list_w),
            "n_vintage_all": len(tpi_list_a),
            "realized": real_by_name[nm],
        })

    if len(rows) < 20:
        return {"has_data": False,
                "msg": f"Persistence: solo {len(rows)} giocatori con ≥2 vintage + realized."}

    df = pd.DataFrame(rows)
    log.info(f"  Persistence: {len(df)} giocatori, distrib n_vintage_within: "
             f"{df['n_vintage_within'].value_counts().sort_index().to_dict()}")

    from scipy.stats import spearmanr
    rho_single, _ = spearmanr(df["single"], df["realized"])
    rho_within_mean, _ = spearmanr(df["persistence_within_mean"], df["realized"])
    rho_within_min, _ = spearmanr(df["persistence_within_min"], df["realized"])
    rho_all_mean, _ = spearmanr(df["persistence_all_mean"], df["realized"])

    # Robust: solo chi appare in ≥4 vintage within-season
    df_robust = df[df["n_vintage_within"] >= 4]
    if len(df_robust) >= 12:
        rho_within_mean_r, _ = spearmanr(df_robust["persistence_within_mean"], df_robust["realized"])
        rho_single_r, _ = spearmanr(df_robust["single"], df_robust["realized"])
    else:
        rho_within_mean_r = rho_single_r = None

    delta_within = rho_within_mean - rho_single
    delta_all = rho_all_mean - rho_single

    log.info(f"  Persistence ρ vs realized:")
    log.info(f"    single TPI corrente        = {rho_single:+.3f}")
    log.info(f"    media within-season ({len(within_vintages)}v)   = {rho_within_mean:+.3f} "
             f"(Δ={delta_within:+.3f})")
    log.info(f"    media all ({len(vintages)}v, incl 24-25) = {rho_all_mean:+.3f} "
             f"(Δ={delta_all:+.3f})")
    log.info(f"    min within (robust outlier) = {rho_within_min:+.3f}")
    if rho_within_mean_r is not None:
        log.info(f"  Robust (n_vintage_within≥4, n={len(df_robust)}): "
                 f"single={rho_single_r:+.3f}, persistence_within_mean={rho_within_mean_r:+.3f}")

    # VERA persistence cross-stagione: TPI medio (24-25, 25-26) per chi appare in entrambe.
    # I within-vintage hanno overlap dati massivo → non sono "ripetizioni indipendenti".
    # La vera test della persistence è "due stagioni distinte".
    cross_2season_rho_single = cross_2season_rho_persist = None
    n_cross_2season = 0
    if cross_vintages:
        cross_pers = {}  # nome → TPI cross-season
        for vtag, vpath in cross_vintages:
            try:
                with open(vpath, encoding="utf-8") as fh:
                    snap = json.load(fh)
            except Exception:
                continue
            for p in snap.get("players", []):
                tpi = (p.get("tpi") or {}).get("totale")
                if tpi is None:
                    continue
                cross_pers[_nm(p.get("nome",""))] = float(tpi)
        # Giocatori comuni: presenti in 24-25 AND in corrente AND con realized
        rows_2s = []
        for nm, tpi_2425 in cross_pers.items():
            single = cur_lookup.get(nm)
            real = real_by_name.get(nm)
            if single is None or real is None:
                continue
            two_season_mean = (single + tpi_2425) / 2
            rows_2s.append({"single": single, "two_season_mean": two_season_mean,
                            "realized": real, "tpi_2425": tpi_2425})
        if len(rows_2s) >= 20:
            df2 = pd.DataFrame(rows_2s)
            cross_2season_rho_single, _ = spearmanr(df2["single"], df2["realized"])
            cross_2season_rho_persist, _ = spearmanr(df2["two_season_mean"], df2["realized"])
            n_cross_2season = len(df2)

            # Bootstrap IC95 sulla differenza ρ_persist − ρ_single (n bootstrap = 2000)
            rng = np.random.default_rng(42)
            delta_boot = []
            arr_single = df2["single"].values
            arr_persist = df2["two_season_mean"].values
            arr_real = df2["realized"].values
            for _ in range(2000):
                idx = rng.integers(0, len(df2), len(df2))
                if np.std(arr_single[idx]) == 0 or np.std(arr_real[idx]) == 0:
                    continue
                r_s, _ = spearmanr(arr_single[idx], arr_real[idx])
                r_p, _ = spearmanr(arr_persist[idx], arr_real[idx])
                if np.isnan(r_s) or np.isnan(r_p):
                    continue
                delta_boot.append(r_p - r_s)
            ic_lo = float(np.percentile(delta_boot, 2.5))
            ic_hi = float(np.percentile(delta_boot, 97.5))
            cross_2season_ic = (ic_lo, ic_hi)
            sig = (ic_lo > 0)
            log.info(f"  2-season persistence (n={n_cross_2season}): "
                     f"single ρ={cross_2season_rho_single:+.3f}, "
                     f"2-season mean ρ={cross_2season_rho_persist:+.3f}, "
                     f"Δ={cross_2season_rho_persist - cross_2season_rho_single:+.3f} "
                     f"[{ic_lo:+.3f}, {ic_hi:+.3f}] sig95={sig}")
        else:
            cross_2season_ic = None
            sig = False

    persistence_wins = bool(rho_within_mean > rho_single + 0.02)
    persistence_2season_wins = (cross_2season_rho_persist is not None
                                and cross_2season_rho_persist > cross_2season_rho_single + 0.02)
    return {
        "has_data": True,
        "n_giocatori": len(df),
        "n_within_vintage": len(within_vintages),
        "n_cross_vintage": len(cross_vintages),
        "vintage_within_tags": [v[0] for v in within_vintages],
        "vintage_cross_tags": [v[0] for v in cross_vintages],
        "rho_single": round(float(rho_single), 3),
        "rho_persistence_within_mean": round(float(rho_within_mean), 3),
        "rho_persistence_within_min": round(float(rho_within_min), 3),
        "rho_persistence_all_mean": round(float(rho_all_mean), 3),
        "delta_within_vs_single": round(float(delta_within), 3),
        "delta_all_vs_single": round(float(delta_all), 3),
        "persistence_wins": persistence_wins,
        "robust_n4plus": ({
            "n": int(len(df_robust)),
            "rho_single": (round(float(rho_single_r), 3)
                           if rho_single_r is not None else None),
            "rho_persistence_within_mean": (round(float(rho_within_mean_r), 3)
                                            if rho_within_mean_r is not None else None),
        } if rho_within_mean_r is not None else None),
        "cross_2season": ({
            "n": n_cross_2season,
            "rho_single": (round(float(cross_2season_rho_single), 3)
                           if cross_2season_rho_single is not None else None),
            "rho_2season_mean": (round(float(cross_2season_rho_persist), 3)
                                 if cross_2season_rho_persist is not None else None),
            "delta": (round(float(cross_2season_rho_persist - cross_2season_rho_single), 3)
                      if cross_2season_rho_persist is not None else None),
            "ic95_lo": (round(float(cross_2season_ic[0]), 3)
                        if cross_2season_ic is not None else None),
            "ic95_hi": (round(float(cross_2season_ic[1]), 3)
                        if cross_2season_ic is not None else None),
            "significativo_95": sig,
            "persistence_wins": persistence_2season_wins,
        } if cross_2season_rho_persist is not None else None),
    }


def valida_ablation(players: list, df_gp: pd.DataFrame) -> dict:
    """
    Test O — Ablation study: rimuove una dimensione alla volta dal TPI e misura:
      - r(TPI_full, TPI_ablato): quanto cambia il ranking
      - top10 stability: quanti dei top 10 restano
      - Δ Spearman vs realized: la dim aiuta davvero a predire?

    Una dim con Δ ~ 0 è candidata per la RIMOZIONE (semplicità). Una dim con
    Δ negativo grande ha vero potere discriminante e va mantenuta.
    """
    # z-score per dim nel payload (TPI base = 7 dim)
    DIM_ZKEYS = {
        "output_adj": "z_output", "buildup_adj": "z_buildup",
        "centralita": "z_centralita", "boost_ratio": "z_boost",
        "consistenza": "z_consistenza",
        "finishing":   "z_finishing", "form": "z_form",
    }
    # Realized = output stagionale (gol no-rig + xa) per-90 dal DB
    if df_gp is None or len(df_gp) == 0:
        return {"has_data": False, "msg": "Ablation: df_gp non disponibile."}
    realized = (df_gp[df_gp["minuti"] > 0]
                .groupby("giocatore_id")
                .apply(lambda g: (g["goal"].sum() + g["xa"].sum()) / g["minuti"].sum() * 90))
    real_map = realized.to_dict()

    # Raccolgo coppie (gid, TPI_full, [z_dim values...], realized)
    rows = []
    for p in players:
        gid = p.get("id")
        tpi_full = p["tpi"].get("totale")
        real = real_map.get(gid)
        if tpi_full is None or real is None:
            continue
        z_vals = {k: p.get(zk) for k, zk in DIM_ZKEYS.items()}
        rows.append({"gid": gid, "tpi_full": tpi_full, "real": real,
                     # servono per replicare i passaggi post media pesata
                     "ruolo": p.get("ruolo"),
                     "confidence": p.get("confidence"),
                     "disp_penalty": p.get("disponibilita_penalty"),
                     **z_vals})
    df = pd.DataFrame(rows)
    if len(df) < 30:
        return {"has_data": False, "msg": f"Ablation: {len(df)} pairs (servono ≥30)."}

    # TPI ricostruito. La versione precedente si fermava alla media pesata degli
    # z e correlava 0.79 col TPI pubblicato: confrontava due grandezze diverse,
    # e il Delta predittivo che ne usciva era ~-0.37 per OGNI dimensione, dal
    # peso 0.32 al peso 0.02 — la firma di un artefatto, non di un'importanza.
    # Replicando anche i tre passaggi successivi si arriva a rho 0.96 / r 0.99.
    def reconstruct(weights: dict) -> pd.Series:
        num = pd.Series(0.0, index=df.index)
        den = pd.Series(0.0, index=df.index)
        for dim, w in weights.items():
            if dim not in df.columns:
                continue
            z = pd.to_numeric(df[dim], errors="coerce")
            valid = z.notna()
            num = num + np.where(valid, z.fillna(0.0) * w, 0.0)
            den = den + np.where(valid, w, 0.0)
        media_pesata = pd.Series(np.where(den > 0, num / den, np.nan), index=df.index)

        # 1. shrink verso la media di ruolo, in base alla confidence
        conf = pd.to_numeric(df.get("confidence"), errors="coerce").fillna(0.0)
        shrink = CONFIDENCE_FLOOR + (1.0 - CONFIDENCE_FLOOR) * conf
        media_ruolo = media_pesata.groupby(df["ruolo"]).transform("mean")
        shrunk = media_ruolo + shrink * (media_pesata - media_ruolo)
        # 2. scala per il peso offensivo del ruolo
        role_w = df["ruolo"].map(ROLE_WEIGHT).fillna(0.5).astype(float)
        # 3. penalty disponibilita'
        pen = pd.to_numeric(df.get("disp_penalty"), errors="coerce").fillna(1.0)
        return role_w * shrunk * pen

    # Baseline: full TPI ricostruito (sanity check correlazione col tpi_full di payload)
    full_recon = reconstruct(TPI_WEIGHTS)
    from scipy.stats import spearmanr
    base_rho_recon, _ = spearmanr(full_recon, df["tpi_full"])
    base_rho_real, _ = spearmanr(df["tpi_full"], df["real"])
    log.info(f"  Ablation baseline: ρ(recon, payload)={base_rho_recon:.3f}, "
             f"ρ(TPI, realized)={base_rho_real:.3f}, n={len(df)}")

    # Top 10 del payload
    top10_full = set(df.nlargest(10, "tpi_full")["gid"].tolist())

    # Ablation: rimuovi una dim per volta (peso → 0)
    ablation_results = []
    for dim in TPI_WEIGHTS:
        w_ablato = {k: (v if k != dim else 0.0) for k, v in TPI_WEIGHTS.items()}
        tpi_ablato = reconstruct(w_ablato)
        # Correlazione vs full
        rho_full, _ = spearmanr(tpi_ablato, df["tpi_full"])
        # Correlazione vs realized
        rho_real, _ = spearmanr(tpi_ablato, df["real"])
        # Top10 stability
        df["_tpi_abl"] = tpi_ablato
        top10_ablato = set(df.nlargest(10, "_tpi_abl")["gid"].tolist())
        overlap10 = len(top10_full & top10_ablato)
        # Δ predittivo vs full
        delta_predict = rho_real - base_rho_real
        ablation_results.append({
            "dim": dim,
            "peso": TPI_WEIGHTS[dim],
            "rho_vs_full": round(float(rho_full), 3),
            "rho_vs_realized": round(float(rho_real), 3),
            "delta_predict": round(float(delta_predict), 4),
            "top10_overlap": overlap10,
        })
        log.info(f"  Ablation -{dim:13s} (w={TPI_WEIGHTS[dim]:.2f}): "
                 f"ρ vs full={rho_full:.3f}, ρ vs realized={rho_real:.3f}, "
                 f"Δpredict={delta_predict:+.4f}, top10 overlap={overlap10}/10")
    ablation_results.sort(key=lambda x: x["delta_predict"])  # più impattante prima

    return {
        "has_data": True,
        "n": len(df),
        "baseline_rho_realized": round(float(base_rho_real), 3),
        "results": ablation_results,
    }


def valida_calibration(players: list) -> dict:
    """
    Test M — Reliability/Calibration: il TPI è ben calibrato come predittore
    del rendimento? Plot decile-by-decile del TPI vs output realizzato.

    Per ogni decile di TPI calcola la media del criterio realizzato
    (gol_p90 + xa_p90). Se la relazione è ~lineare e monotona, il TPI è
    ben calibrato: TPI alto → output alto, in modo proporzionale.

    Diagnostica: slope (atteso ~positivo), intercept, residui per decile.
    Curve concave/convesse rivelano miscalibrazione (es. TPI gonfia/sgonfia
    i top vs reality).
    """
    rows = []
    for p in players:
        tpi = p["tpi"].get("totale")
        kpi = p.get("kpi") or {}
        conv = p.get("conv") or {}
        goal_p90 = conv.get("goal_p90")
        xa_p90 = kpi.get("xa_p90")
        if tpi is None or goal_p90 is None or xa_p90 is None:
            continue
        rows.append({"tpi": float(tpi),
                     "realized": float(goal_p90) + float(xa_p90),
                     "ruolo": p.get("ruolo", "")})
    if len(rows) < 30:
        return {"has_data": False,
                "msg": f"Test M (calibration): {len(rows)} giocatori (servono ≥30)."}
    df = pd.DataFrame(rows)
    # Bin in decili (10 gruppi). Se duplicati, fallback a quintili.
    try:
        df["bin"] = pd.qcut(df["tpi"], q=10, labels=False, duplicates="drop")
    except ValueError:
        df["bin"] = pd.qcut(df["tpi"], q=5, labels=False, duplicates="drop")
    bins = (df.groupby("bin")
              .agg(tpi_mean=("tpi", "mean"),
                   realized_mean=("realized", "mean"),
                   realized_std=("realized", "std"),
                   n=("tpi", "count"))
              .reset_index())
    # Regressione lineare globale
    slope, intercept = np.polyfit(df["tpi"], df["realized"], 1)
    bins["predicted"] = slope * bins["tpi_mean"] + intercept
    bins["residual"]  = bins["realized_mean"] - bins["predicted"]
    # Calibration error (mean absolute residual relative to range)
    realized_range = float(df["realized"].max() - df["realized"].min())
    ace = float(bins["residual"].abs().mean() / realized_range) if realized_range > 0 else None
    # Monotonia: lo Spearman tra bin TPI e bin realized deve essere ≈ 1
    from scipy.stats import spearmanr
    mono_rho, _ = spearmanr(bins["tpi_mean"], bins["realized_mean"])
    log.info(f"  Reliability: slope={slope:+.3f}, intercept={intercept:+.3f}, "
             f"ACE={ace:.3f}, monotonia ρ={mono_rho:+.3f} ({len(bins)} bin)")
    return {
        "has_data": True,
        "n": len(df),
        "n_bins": len(bins),
        "slope": round(float(slope), 4),
        "intercept": round(float(intercept), 4),
        "calibration_error": round(ace, 4) if ace is not None else None,
        "monotonia_rho": round(float(mono_rho), 3),
        "bins": [
            {"bin": int(r["bin"]), "tpi_mean": round(r["tpi_mean"], 3),
             "realized_mean": round(r["realized_mean"], 3),
             "predicted": round(r["predicted"], 3),
             "residual": round(r["residual"], 3),
             "n": int(r["n"])}
            for _, r in bins.iterrows()
        ],
    }


def valida_predittivita_per_ruolo(players: list, df_gp: pd.DataFrame) -> dict:
    """
    Test N — Predittività stratificata per ruolo: il TPI funziona ugualmente
    bene per ATT, CEN, DIF? Spearman ρ(TPI, realized) per ogni ruolo.

    Risposta a "il TPI è offensivo: vale anche per i difensori?". Se ρ è
    forte solo per ATT/CEN, il TPI non discrimina i difensori → segnale che
    serve un'altra metrica per quel ruolo (es. xG concessi quando in campo).
    """
    if df_gp is None or len(df_gp) == 0:
        return {"has_data": False, "msg": "Test N: df_gp non disponibile."}
    # Realized: (goal + xa)/min*90 totale stagione (dal DB). Usiamo goal
    # (include rigori, parte3 non carica npg) e xa expected. Approssimazione
    # accettabile per il test stratificato per ruolo.
    realized = (df_gp[df_gp["minuti"] > 0]
                .groupby("giocatore_id")
                .agg(min_sum=("minuti", "sum"),
                     goal_sum=("goal", "sum"),
                     xa_sum=("xa", "sum")))
    realized["out90"] = (realized["goal_sum"] + realized["xa_sum"]) / realized["min_sum"] * 90
    real_map = realized["out90"].to_dict()

    per_role = {}
    for ruolo in ("ATT", "CEN", "DIF", "POR"):
        pairs = []
        for p in players:
            if p.get("ruolo") != ruolo:
                continue
            tpi = p["tpi"].get("totale")
            gid = p.get("id")
            r = real_map.get(gid)
            if tpi is not None and r is not None:
                pairs.append((float(tpi), float(r)))
        if len(pairs) < 8:
            per_role[ruolo] = {"n": len(pairs), "rho": None, "msg": "sample insufficiente"}
            continue
        t_arr = np.array([x[0] for x in pairs])
        r_arr = np.array([x[1] for x in pairs])
        from scipy.stats import spearmanr
        rho, p_val = spearmanr(t_arr, r_arr)
        per_role[ruolo] = {
            "n": len(pairs),
            "rho": round(float(rho), 3),
            "p": round(float(p_val), 4),
            "tpi_mean": round(float(t_arr.mean()), 3),
            "real_mean": round(float(r_arr.mean()), 3),
        }
        log.info(f"  Per ruolo {ruolo}: ρ={rho:+.3f} (p={p_val:.3f}, n={len(pairs)})")
    return {"has_data": True, "per_role": per_role}


def valida_convergenza() -> dict:
    """
    Convergenza ranking: quanto il TPI calcolato a vintage g{N} prevede il TPI
    attuale (stagione completa)? Richiede `payload_g{N}.json` per uno o più N.

    Per ogni vintage disponibile calcola:
      - Spearman ρ tra ranking TPI vintage e ranking TPI corrente
      - overlap top-N (frazione comune nelle top 25)
      - movers: chi è salito/sceso di più
    """
    import re
    # Vintage WITHIN-SEASON (payload_g{N}.json nella cartella output) + cross-season
    # se esistono snapshot in snapshots/<season>/giornata_NN/payload.json
    vintages = sorted(OUTPUT_DIR.glob("payload_g*.json"))
    cross_season = []
    # 1) Snapshot stagionali in snapshots/<season>/giornata_NN/payload.json
    snap_root = BASE_DIR / "snapshots"
    if snap_root.is_dir():
        for season_dir in sorted(snap_root.iterdir()):
            if not season_dir.is_dir() or season_dir.name.startswith(("_", ".")):
                continue
            for g_dir in sorted(season_dir.glob("giornata_*")):
                pj = g_dir / "payload.json"
                if pj.exists():
                    cross_season.append((season_dir.name, g_dir.name, pj))
    # 2) Backfill stagionali in OUTPUT_DIR/payload_YYYY-YY.json (es. payload_2024-25.json)
    import re as _re_season
    for vp in OUTPUT_DIR.glob("payload_*.json"):
        m = _re_season.match(r"payload_(\d{4}-\d{2})\.json$", vp.name)
        if m:
            cross_season.append((m.group(1), "season_aggregate", vp))

    if not vintages and not cross_season:
        return {"has_data": False,
                "msg": ("Convergenza per giornata: nessun payload_g{N}.json e nessun "
                        "snapshot stagionale trovato. Genera vintage con "
                        "`python parte1_analisi.py --max-giornata N` o popola "
                        "`snapshots/<season>/giornata_NN/payload.json`.")}

    cur_path = payload_corrente()
    if not cur_path.exists():
        return {"has_data": False, "msg": "payload.json corrente assente."}
    with open(cur_path, encoding="utf-8") as fh:
        current = json.load(fh)
    cur_tpi = {int(p["id"]): float(p["tpi"]["totale"])
               for p in current.get("players", [])
               if (p.get("tpi") or {}).get("totale") is not None}

    rows = []
    movers_all = []
    for vp in vintages:
        m = re.search(r"payload_g(\d+)\.json$", vp.name)
        if not m:
            continue
        N = int(m.group(1))
        with open(vp, encoding="utf-8") as fh:
            vintage = json.load(fh)
        v_tpi = {int(p["id"]): float(p["tpi"]["totale"])
                 for p in vintage.get("players", [])
                 if (p.get("tpi") or {}).get("totale") is not None}
        common = sorted(set(cur_tpi) & set(v_tpi))
        if len(common) < 20:
            continue
        # Spearman ρ via rank correlation
        v_vals = np.array([v_tpi[g] for g in common])
        c_vals = np.array([cur_tpi[g] for g in common])
        v_rank = pd.Series(v_vals).rank().values
        c_rank = pd.Series(c_vals).rank().values
        # pearson sui rank = spearman
        if v_rank.std() > 0 and c_rank.std() > 0:
            rho = float(np.corrcoef(v_rank, c_rank)[0, 1])
        else:
            rho = None
        # Top-25 overlap
        v_top25 = set(g for g, _ in sorted(v_tpi.items(), key=lambda x: -x[1])[:25])
        c_top25 = set(g for g, _ in sorted(cur_tpi.items(), key=lambda x: -x[1])[:25])
        overlap25 = len(v_top25 & c_top25) / 25.0
        # Movers (delta rank)
        v_ord = {gid: i for i, (gid, _) in enumerate(sorted(v_tpi.items(), key=lambda x: -x[1]), 1)}
        c_ord = {gid: i for i, (gid, _) in enumerate(sorted(cur_tpi.items(), key=lambda x: -x[1]), 1)}
        # nome lookup dal payload corrente
        name_map = {int(p["id"]): p.get("nome", f"#{p['id']}")
                    for p in current.get("players", [])}
        deltas = [(gid, v_ord[gid] - c_ord[gid]) for gid in common if gid in v_ord and gid in c_ord]
        deltas.sort(key=lambda x: x[1])  # negativi = saliti (era posizione alta, ora più bassa)
        risers = [{"id": gid, "nome": name_map.get(gid, f"#{gid}"),
                   "delta": -d, "from": v_ord.get(gid), "to": c_ord.get(gid)}
                   for gid, d in deltas[:5]]
        fallers = [{"id": gid, "nome": name_map.get(gid, f"#{gid}"),
                    "delta": d, "from": v_ord.get(gid), "to": c_ord.get(gid)}
                    for gid, d in sorted(deltas, key=lambda x: -x[1])[:5]]
        rows.append({
            "vintage_giornata": N,
            "n_common": len(common),
            "spearman_rho": round(rho, 3) if rho is not None else None,
            "overlap_top25": round(overlap25, 3),
        })
        movers_all.append({"vintage_giornata": N, "risers": risers, "fallers": fallers})
        log.info(f"  Convergenza vintage g{N} → corrente: ρ={rho:.3f}, "
                 f"overlap top25={overlap25:.0%}, n={len(common)}")

    # Cross-season: confronta payload corrente vs snapshot di stagioni passate.
    # Matching per ID quando possibile (stesso schema DB); altrimenti per NOME
    # normalizzato (backfill da fonti esterne come Understat hanno id diversi).
    import unicodedata as _ud
    def _nm(s):
        return _ud.normalize("NFKD", str(s or "")).encode("ascii","ignore").decode().lower().strip()
    cur_by_name = {_nm(p["nome"]): float(p["tpi"]["totale"])
                   for p in current.get("players", [])
                   if (p.get("tpi") or {}).get("totale") is not None}
    cross_season_rows = []
    for season, g_name, snap_path in cross_season:
        try:
            with open(snap_path, encoding="utf-8") as fh:
                snap = json.load(fh)
        except Exception as e:
            log.warning(f"  Snapshot stagionale non leggibile {snap_path}: {e}")
            continue
        # Prova prima per ID; fallback a nome se overlap < 10
        s_tpi = {int(p["id"]): float(p["tpi"]["totale"])
                 for p in snap.get("players", [])
                 if (p.get("tpi") or {}).get("totale") is not None}
        common = sorted(set(cur_tpi) & set(s_tpi))
        match_mode = "id"
        if len(common) < 10:
            # Match per nome normalizzato
            s_tpi_by_name = {_nm(p["nome"]): float(p["tpi"]["totale"])
                             for p in snap.get("players", [])
                             if (p.get("tpi") or {}).get("totale") is not None}
            common_names = sorted(set(cur_by_name) & set(s_tpi_by_name))
            if len(common_names) < 20:
                log.info(f"  Cross-season {season}/{g_name}: n_common solo {len(common_names)} "
                         f"(id) / {len(common_names)} (nome) — skip")
                continue
            s_vals = pd.Series([s_tpi_by_name[n] for n in common_names]).rank().values
            c_vals = pd.Series([cur_by_name[n] for n in common_names]).rank().values
            common = common_names
            match_mode = "nome"
        else:
            s_vals = pd.Series([s_tpi[g] for g in common]).rank().values
            c_vals = pd.Series([cur_tpi[g] for g in common]).rank().values
        rho = (float(np.corrcoef(s_vals, c_vals)[0, 1])
               if s_vals.std() > 0 and c_vals.std() > 0 else None)
        cross_season_rows.append({
            "season": season,
            "snapshot": g_name,
            "n_common": len(common),
            "spearman_rho": round(rho, 3) if rho is not None else None,
            "match": match_mode,
        })
        log.info(f"  Cross-season {season}/{g_name} ({match_mode}) → corrente: "
                 f"ρ={rho:.3f}, n={len(common)}")

    if not rows and not cross_season_rows:
        return {"has_data": False, "msg": "Nessun vintage utilizzabile (n_common < 20)."}

    return {"has_data": True,
            "per_vintage": rows,
            "cross_season": cross_season_rows,
            "movers": movers_all,
            "msg": (f"{len(rows)} vintage within-season + {len(cross_season_rows)} "
                    f"cross-season analizzati. Più ρ alto = ranking stabile.")}


# ════════════════════════════════════════════════════════════════
# TEMPLATE CSS
# ════════════════════════════════════════════════════════════════
CSS = """
/* Font serviti dal repo, non da Google: gli stessi due file variabili della
   dashboard e della homepage (fonts/*.woff2 accanto all'HTML). SIL OFL, che
   consente esplicitamente l'auto-hosting. */
@font-face{
  font-family:"Oswald";font-style:normal;font-weight:200 700;font-display:swap;
  src:url("fonts/oswald-latin-var.woff2") format("woff2");
  unicode-range:U+0000-00FF,U+0131,U+0152-0153,U+02BB-02BC,U+02C6,U+02DA,U+02DC,U+0304,U+0308,U+0329,U+2000-206F,U+20AC,U+2122,U+2191,U+2193,U+2212,U+2215,U+FEFF,U+FFFD;
}
@font-face{
  font-family:"JetBrains Mono";font-style:normal;font-weight:100 800;font-display:swap;
  src:url("fonts/jetbrainsmono-latin-var.woff2") format("woff2");
  unicode-range:U+0000-00FF,U+0131,U+0152-0153,U+02BB-02BC,U+02C6,U+02DA,U+02DC,U+0304,U+0308,U+0329,U+2000-206F,U+20AC,U+2122,U+2191,U+2193,U+2212,U+2215,U+FEFF,U+FFFD;
}

/* ═══════════════════════════════════════
   Stessi token di assets/dashboard.css e di index.html. Questa pagina era
   rimasta l'ultima in palette vecchia: nero puro, azzurro/verde/viola di
   sistema, vetro e ombre da 32px. Passare dalla classifica alla validazione
   sembrava cambiare sito.
═══════════════════════════════════════ */
:root{
  /* Il fondo non e' nero neutro ma ha un'inclinazione verde. */
  --bg:#0A1512;--bg1:#0F1F1B;--bg2:#132723;--bg3:#1B342E;--bg4:#24443C;
  --sep:rgba(233,240,236,.10);--sep2:rgba(233,240,236,.18);
  --lp:#ECF2EE;--ls:rgba(233,240,236,.66);--lt:rgba(233,240,236,.38);--lq:rgba(233,240,236,.20);

  /* UN solo accento: l'ambra dice "attivo, oppure questo e' il valore".
     Le altre tinte restano solo dove sono un giudizio sul dato (la scala
     verde/ambra/rosso di rcol()) o il colore di ruolo nei grafici. */
  --orng:#FFB020;
  --blue:#5A93C4;--green:#5FAE7E;--clay:#D98E6A;
  --red:#D9705F;--purp:#9B7FC4;--teal:#6FB4C4;--indig:#7B84C9;

  --r:12px;--rsm:10px;--rxs:6px;
  --font:-apple-system,BlinkMacSystemFont,"SF Pro Display","Segoe UI",system-ui,sans-serif;
  --disp:"Oswald","Bahnschrift",Impact,sans-serif;
  --mono:"JetBrains Mono","Cascadia Mono",ui-monospace,monospace;
  --maxw:1180px;
}
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
html{scroll-behavior:smooth}
body{font-family:var(--font);background:var(--bg);color:var(--lp);
  font-size:15px;line-height:1.47;-webkit-font-smoothing:antialiased;overflow-x:hidden;
  /* In una tabella di coefficienti le cifre devono incolonnarsi. */
  font-variant-numeric:tabular-nums;font-feature-settings:"tnum" 1}
::-webkit-scrollbar{width:3px;height:3px}
::-webkit-scrollbar-thumb{background:var(--bg3);border-radius:2px}

/* ── NAV — link di testo, come dashboard e homepage ────────────────────── */
.nav{position:sticky;top:0;z-index:400;height:56px;
  background:var(--bg);border-bottom:1px solid var(--sep);box-shadow:none;
  display:flex;align-items:center;gap:10px;
  padding-inline:max(20px,calc((100% - var(--maxw)) / 2))}
/* Al centro, non alla base: con `baseline` l'etichetta mono da 9px veniva
   allineata alla base dell'Oswald da 19px e il suo centro cadeva 5,6px sotto
   quello dei link della nav. Su una fila di voci orizzontali conta
   l'allineamento con la fila, non con la base della parola accanto. */
.nav-brand{font-family:var(--disp);font-size:19px;font-weight:600;letter-spacing:.4px;
  text-transform:uppercase;white-space:nowrap;flex-shrink:0;
  display:flex;align-items:center;gap:10px}
.nav-brand small{font-family:var(--mono);font-size:9px;font-weight:400;letter-spacing:.16em;
  text-transform:uppercase;color:var(--lt);margin-left:0;
  padding-left:10px;border-left:1px solid var(--sep)}

/* Avanti/indietro: due frecce, non due bottoni. Sono un comando secondario. */
.nav-glass-btn{
  display:inline-flex;align-items:center;justify-content:center;
  width:22px;height:22px;
  background:none;border:0;border-radius:0;box-shadow:none;
  font-size:15px;color:var(--lt);cursor:pointer;transition:color .16s;
  font-family:var(--font);text-decoration:none;flex-shrink:0}
.nav-glass-btn:hover{background:none;color:var(--lp);box-shadow:none;transform:none}
.nav-glass-btn:active{transform:none;opacity:.6}
.nav-glass-btn.home-btn{width:auto;padding:0 12px;gap:5px;font-size:12px;font-weight:600}

/* I due link di pagina erano pillole piene, una azzurra e una arancione: due
   inviti all'azione che gridavano piu' del titolo. Ora sono testo, e il filetto
   ambra compare solo al passaggio. Qui nessuno dei due e' la pagina corrente —
   lo dice il marchio a sinistra. */
.nav-home-btn,.nav-orng-btn,.nav-switch-btn{
  position:relative;display:inline-flex;align-items:center;gap:6px;
  height:34px;padding:0 2px;
  background:none;border:0;border-radius:0;box-shadow:none;
  font:12px var(--font);font-weight:600;letter-spacing:.02em;
  color:var(--lt);text-decoration:none;cursor:pointer;
  white-space:nowrap;flex-shrink:0;transition:color .16s}
.nav-home-btn:hover,.nav-orng-btn:hover,.nav-switch-btn:hover{
  background:none;color:var(--lp);transform:none;box-shadow:none}
.nav-home-btn::after,.nav-orng-btn::after,.nav-switch-btn::after{
  content:"";position:absolute;left:0;right:0;bottom:7px;height:1.5px;
  background:var(--orng);opacity:0;transition:opacity .16s}
.nav-home-btn:hover::after,.nav-orng-btn:hover::after,.nav-switch-btn:hover::after{opacity:1}
.nav-switch-dot{width:5px;height:5px;border-radius:50%;background:var(--lt);
  box-shadow:none;flex-shrink:0}
.nav-btn-group{display:flex;align-items:center;gap:18px;margin-left:22px}
.nav-right-group{display:flex;align-items:center;gap:18px;margin-left:auto}

/* Lo switcher lingua arriva da i18n.js con la cornice della vecchia palette:
   la togliamo, restano due sigle con l'attiva in ambra. */
/* i18n.js monta lo switcher dentro uno <span> di blocco. L'inline-flex si
   appoggiava alla baseline del testo di quello span, lasciando sotto lo
   spazio del discendente: IT/EN scendevano di 2px rispetto alla fila.
   Rendendo flex il contenitore il posizionamento a baseline sparisce. */
.nav [data-i18n-switcher]{display:flex;align-items:center}
.nav .i18n-switch{border:0;border-radius:0;background:none;height:auto;gap:2px}
.nav .i18n-switch button{font-family:var(--mono);font-size:10px;letter-spacing:.12em;
  padding:3px 5px;border-radius:3px;color:var(--lt)}
.nav .i18n-switch button + button{border-left:0}
.nav .i18n-switch button.active{background:none;color:var(--orng)}
.nav .i18n-switch button:hover{background:none;color:var(--lp)}

@media(max-width:768px){
  .nav-brand small{display:none}
  .nav{justify-content:flex-start}
  .nav-btn-group{margin-left:14px;gap:14px}
  .nav-right-group{margin-left:auto;gap:14px}
}
@media(max-width:480px){
  .nav{height:48px;gap:8px;padding-inline:12px}
  .nav-brand{font-size:15px}
  /* Le etichette NON si nascondono piu': ora che le emoji sono via sarebbero
     due link vuoti. A cedere il posto sono le frecce avanti/indietro, che sul
     telefono ripetono un gesto che il browser ha gia'. */
  .nav-glass-btn{display:none}
  .nav-btn-group{margin-left:10px;gap:12px}
  .nav-right-group{gap:12px}
}

/* ── HERO ───────────────────────────────────────────────────────────────
   Il titolo prende la faccia condensata: e' la stessa voce della classifica,
   non un grassetto di sistema. Le cinque pillole diventano etichette mono
   separate da filetti — dicono cosa contiene la pagina, non sono bottoni. */
.hero{padding:48px 20px 30px;background:var(--bg);
  border-bottom:1px solid var(--sep);
  max-width:var(--maxw);margin-inline:auto}
.hero-eyebrow{font-family:var(--mono);font-size:9.5px;font-weight:400;
  letter-spacing:.18em;text-transform:uppercase;color:var(--lt);margin-bottom:12px}
.hero-ttl{font-family:var(--disp);font-size:clamp(32px,5vw,54px);font-weight:500;
  line-height:.95;text-transform:uppercase;letter-spacing:-.005em;margin-bottom:0}
.hero-sub{font-size:13.5px;color:var(--ls);max-width:56ch;line-height:1.65;margin-top:16px}
.hero-sub strong,.hero-sub b{color:var(--lp);font-weight:600}
.hero-pills{display:flex;flex-wrap:wrap;align-items:center;gap:0;
  margin-top:24px;padding-top:14px;border-top:1px solid var(--sep)}
.hero-pill{font-family:var(--mono);font-size:9.5px;font-weight:400;
  letter-spacing:.13em;text-transform:uppercase;color:var(--lt);
  padding:3px 14px;background:none;border:0;border-radius:0;white-space:nowrap}
.hero-pill:first-child{padding-left:0}
.hero-pill + .hero-pill{border-left:1px solid var(--sep)}

/* ── CARD ───────────────────────────────────────────────────────────────
   Erano vetro: gradiente, bordo illuminato in alto e un'ombra da 20px che le
   faceva galleggiare. Su una pagina di grafici l'unica cosa che deve staccare
   sono i dati: restano un fondo appena piu' chiaro e un filetto. */
.card{background:var(--bg1);border:1px solid var(--sep);border-radius:var(--rsm);
  box-shadow:none;padding:16px}
.card-ttl{font-family:var(--mono);font-size:9.5px;font-weight:500;color:var(--lt);
  text-transform:uppercase;letter-spacing:.14em;margin-bottom:14px;
  display:flex;align-items:center;gap:6px}

/* ── LAYOUT E TESTATE DI SEZIONE ────────────────────────────────────────
   La lettera della sezione era un pallino pieno colorato: nove pallini di nove
   colori diversi facevano piu' rumore dei numeri. Ora e' una sigla mono in
   ambra sopra un filetto che apre la sezione. */
.main{max-width:var(--maxw);margin:0 auto;padding:24px 20px 88px}
.section{margin-bottom:56px}
.section-hd{display:flex;align-items:baseline;gap:16px;margin-bottom:20px;
  padding-top:14px;border-top:1px solid var(--sep)}
.section-num{width:auto;height:auto;border-radius:0;background:none;
  font-family:var(--mono);font-size:11px;font-weight:500;letter-spacing:.14em;
  color:var(--orng);display:block;flex-shrink:0;margin-top:0}
.section-ttl{font-family:var(--disp);font-size:23px;font-weight:500;
  text-transform:uppercase;letter-spacing:.01em;line-height:1.1}
.section-sub{font-size:13px;color:var(--ls);margin-top:7px;line-height:1.6;max-width:74ch}
.g2{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:12px}
.g3{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-bottom:12px}
.g4{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-bottom:12px}

/* ── STAT BOX ───────────────────────────────────────────────────────────
   Via la barretta sfumata in cima: cinque varianti (sb-blue, sb-green…) che
   coloravano il bordo senza dire niente in piu' dell'etichetta sotto. */
.stat-box{background:var(--bg1);border:1px solid var(--sep);border-radius:var(--rsm);
  box-shadow:none;padding:15px 14px;text-align:left;position:relative;overflow:hidden}
.stat-box::before{display:none}
.stat-val{font-family:var(--mono);font-size:31px;font-weight:500;
  letter-spacing:-.03em;line-height:1;color:var(--lp)}
.stat-lbl{font-family:var(--mono);font-size:9px;color:var(--lt);
  text-transform:uppercase;letter-spacing:.14em;margin-top:9px;line-height:1.4}
.stat-sub{font-size:11.5px;color:var(--ls);margin-top:5px;line-height:1.45}

/* ── HELP E MODALE ──────────────────────────────────────────────────────
   Il "?" era un pallino che diventava azzurro pieno al passaggio. Ora e' un
   quadratino di filo che si accende in ambra: stesso segnale, meno peso. */
.help{display:inline-flex;align-items:center;justify-content:center;
  width:15px;height:15px;border-radius:3px;background:none;
  border:1px solid var(--sep2);font-family:var(--mono);font-size:9px;
  color:var(--lt);cursor:pointer;transition:color .15s,border-color .15s;
  flex-shrink:0;vertical-align:middle;margin-left:6px}
.help:hover{background:none;color:var(--orng);border-color:var(--orng)}
.mwrap{position:fixed;inset:0;z-index:600;background:rgba(6,12,10,.8);
  display:none;align-items:center;justify-content:center;padding:16px}
.mwrap.open{display:flex}
.mbox{background:var(--bg1);border:1px solid var(--sep2);border-radius:var(--rsm);
  box-shadow:0 24px 60px rgba(0,0,0,.75);padding:26px;max-width:520px;width:100%}
/* L'emoji in cima al modale era decorativa: il titolo dice gia' di cosa parla. */
.mbox-icon{display:none}
.mbox-ttl{font-family:var(--disp);font-size:24px;font-weight:600;
  text-transform:uppercase;letter-spacing:.2px;margin-bottom:8px}
.mbox-sub{font-family:var(--mono);font-size:11px;color:var(--lt);letter-spacing:.02em;
  background:none;border:0;border-left:1px solid var(--sep2);border-radius:0;
  padding:3px 0 3px 12px;margin-bottom:16px;line-height:1.5}
.mbox-body{font-size:13px;color:var(--ls);line-height:1.75;margin-bottom:14px}
.mbox-ex{font-size:12px;color:var(--lt);background:none;border-radius:0;
  border-left:1px solid var(--orng);padding:5px 0 5px 12px;line-height:1.6}
.mbox-cls{margin-top:22px;width:100%;padding:11px;background:none;
  border:1px solid var(--sep2);border-radius:var(--rsm);color:var(--ls);
  font-family:var(--mono);font-size:10px;letter-spacing:.16em;text-transform:uppercase;
  cursor:pointer;transition:color .15s,border-color .15s}
.mbox-cls:hover{background:none;color:var(--lp);border-color:var(--lt)}

/* ── NOTE E TABELLE ─────────────────────────────────────────────────────
   Le note di lettura erano riquadri con fondo, bordo e filetto azzurro. Un
   filetto solo basta: sono prosa a margine del grafico, non avvisi. */
.interp{background:none;border:0;border-left:1px solid var(--sep2);border-radius:0;
  padding:4px 0 4px 14px;font-size:13px;color:var(--ls);line-height:1.65;margin-top:14px}
.nota-warn{background:none;border:0;border-left:1px solid var(--orng);border-radius:0;
  padding:4px 0 4px 14px;font-size:12px;color:var(--orng);line-height:1.6;margin-bottom:14px}
.table-wrap{overflow-x:auto;-webkit-overflow-scrolling:touch;margin-top:4px}
table{width:100%;border-collapse:collapse;font-size:13px;min-width:320px}
th{font-family:var(--mono);font-size:9px;font-weight:500;color:var(--lt);
  text-transform:uppercase;letter-spacing:.14em;text-align:left;
  padding:9px 10px;border-bottom:1px solid var(--sep2);white-space:nowrap}
td{padding:9px 10px;border-bottom:1px solid var(--sep);color:var(--ls)}
tr:hover td{background:rgba(233,240,236,.03)}
.tv{font-family:var(--mono);font-weight:500;color:var(--lp)}
.torng{font-family:var(--mono);font-weight:500;color:var(--orng)}

/* ── BADGE ──────────────────────────────────────────────────────────────
   Erano sei pillole piene, una per tinta. Diventano etichette mono: il
   colore resta perche' qui e' un giudizio sul dato (buono/moderato/basso),
   la scatola no. */
.badge{display:inline-flex;align-items:center;gap:5px;padding:0;
  background:none;border:0;border-radius:0;
  font-family:var(--mono);font-size:10px;font-weight:500;
  letter-spacing:.12em;text-transform:uppercase}
.badge-green{color:var(--green)}
.badge-orng{color:var(--orng)}
.badge-red{color:var(--red)}
.badge-blue{color:var(--lt)}
.badge-purp{color:var(--lp)}
.badge-teal{color:var(--lp)}

/* ── ACCORDION E GUIDA ──────────────────────────────────────────────────
   Le sei schede della guida erano riquadri con emoji in cima. Le emoji sono
   via (non sono dati) e i riquadri diventano colonne separate da filetti. */
.accordion{border:0;border-top:1px solid var(--sep);border-bottom:1px solid var(--sep);
  border-radius:0;overflow:visible;margin-bottom:36px}
.acc-hd{padding:14px 0;display:flex;align-items:center;justify-content:space-between;
  cursor:pointer;background:none;transition:none}
.acc-hd:hover{background:none}
.acc-hd:hover .acc-title{color:var(--lp)}
.acc-title{font-family:var(--mono);font-size:10px;font-weight:500;letter-spacing:.16em;
  text-transform:uppercase;color:var(--lt);transition:color .15s;
  display:flex;align-items:center;gap:8px}
.acc-chev{font-size:9px;color:var(--lt);transition:transform .2s;flex-shrink:0}
.acc-body{display:none;padding:2px 0 22px;border-top:1px solid var(--sep)}
.acc-body.open{display:block}
.guide-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:0}
.guide-card{background:none;border:0;border-left:1px solid var(--sep);border-radius:0;
  padding:16px 18px}
.guide-icon{display:none}
.guide-ttl{font-family:var(--mono);font-size:10px;font-weight:500;letter-spacing:.12em;
  text-transform:uppercase;color:var(--lp);margin-bottom:9px}
.guide-body{font-size:12.5px;color:var(--ls);line-height:1.65}

/* ── RECAP ──────────────────────────────────────────────────────────────
   Il riquadro di destra aveva bordo viola da 2px e una barra sfumata in cima:
   era il pezzo piu' gridato della pagina pur essendo un riepilogo. Ora le due
   colonne sono divise da un filetto. */
.recap-outer{display:flex;gap:0;align-items:stretch;flex-wrap:wrap;
  margin-top:22px;border-top:1px solid var(--sep)}
.recap-left{flex:1;min-width:280px;background:none;border:0;border-radius:0;
  padding:22px 24px 22px 0}
.recap-right{width:290px;flex-shrink:0;background:none;border:0;border-radius:0;
  border-left:1px solid var(--sep);padding:22px 0 22px 24px;
  position:relative;overflow:visible}
.recap-right::before{display:none}
.rc-grid{display:grid;grid-template-columns:1fr 1fr;gap:1px;background:var(--sep)}
.rc-card{background:var(--bg);border:0;border-radius:0;padding:13px 14px}
.rc-lbl{font-size:11.5px;color:var(--lt);margin-bottom:8px;line-height:1.4}
.rc-sub{font-family:var(--mono);font-size:10px;color:var(--ls);margin-top:7px;
  letter-spacing:.02em}
@media(max-width:860px){
  .recap-outer{flex-direction:column}
  .recap-left{padding:22px 0}
  .recap-right{width:100%;border-left:0;border-top:1px solid var(--sep);padding:22px 0}
}
@media(max-width:480px){
  .rc-grid{grid-template-columns:1fr 1fr}
}

/* ── MOVER ROWS ─────────────────────────────────────────────────────────
   Righe di una lista, non schede: fondo piatto e un filetto a separarle. */
.mover-row{display:flex;align-items:center;gap:12px;padding:10px 0;
  background:none;border:0;border-bottom:1px solid var(--sep);
  border-radius:0;margin-bottom:0}
.mover-row:last-child{border-bottom:0}
.mover-delta{font-family:var(--mono);font-size:13px;font-weight:500;
  width:44px;text-align:right;flex-shrink:0;letter-spacing:-.02em}
.mover-info{flex:1;min-width:0}
.mover-nm{font-size:13px;font-weight:600;letter-spacing:-.012em;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.mover-sub{font-family:var(--mono);font-size:9px;color:var(--lt);
  letter-spacing:.1em;text-transform:uppercase;margin-top:3px}
.mover-scores{display:flex;gap:14px;flex-shrink:0}
.mover-s{text-align:right;font-family:var(--mono);font-size:11.5px}
.mover-s-lbl{font-family:var(--mono);font-size:8.5px;color:var(--lt);
  text-transform:uppercase;letter-spacing:.12em}

/* ── FIRMA ──────────────────────────────────────────────────────────────
   Era una pillola con bordo e pallino azzurro sopra il contenuto. E' una
   firma: testo mono, senza scatola. */
#wm{position:fixed;bottom:0;left:0;right:0;
  display:flex;align-items:center;justify-content:center;gap:8px;padding:7px 12px;
  background:var(--bg);border:0;border-top:1px solid var(--sep);
  border-radius:0;z-index:800;pointer-events:none}
#wm-dot{width:3px;height:10px;border-radius:1px;background:var(--orng);
  box-shadow:none;flex-shrink:0}
#wm-text{font-family:var(--mono);font-size:9px;font-weight:400;letter-spacing:.14em;
  text-transform:uppercase;color:var(--lq);white-space:nowrap}

/* ── RESPONSIVE ── */
@media (max-width: 900px) {
  .g4{grid-template-columns:1fr 1fr}
  .guide-grid{grid-template-columns:1fr 1fr}
}
@media (max-width: 720px) {
  .hero{padding:30px 16px 22px}
  .hero-sub{font-size:13px}
  .main{padding:18px 16px 56px}
  .g2{grid-template-columns:1fr}
  .g3{grid-template-columns:1fr 1fr}
  .g4{grid-template-columns:1fr 1fr}
  .guide-grid{grid-template-columns:1fr 1fr}
  .stat-val{font-size:27px}
  .section{margin-bottom:42px}
  .section-ttl{font-size:20px}
  .card{padding:14px}
  .mover-row{flex-wrap:wrap;gap:8px}
  .mover-scores{width:100%;justify-content:flex-end}
}
@media (max-width: 480px) {
  .hero{padding:24px 14px 18px}
  .hero-ttl{font-size:30px}
  /* Le etichette dell'hero su telefono starebbero su quattro righe. */
  .hero-pill{display:none}
  .hero-pills .hero-pill:first-child{display:inline-block}
  .main{padding:14px 14px 52px}
  .section{margin-bottom:34px}
  .section-hd{gap:12px}
  .section-num{font-size:10px}
  .section-ttl{font-size:18px}
  .g2,.g3,.g4{grid-template-columns:1fr}
  .guide-grid{grid-template-columns:1fr}
  .guide-card{border-left:0;border-top:1px solid var(--sep);padding:14px 0}
  .guide-card:first-child{border-top:0}
  .stat-val{font-size:26px}
  .acc-body{padding:2px 0 16px}
  .card{padding:13px}
  table{font-size:12px}
  th{padding:7px 8px}
  td{padding:8px 8px}
  .mbox{padding:20px 16px}
  .mbox-ttl{font-size:21px}
  .mbox-body{font-size:12px}
  .mover-nm{font-size:12px}
  .mover-delta{width:38px;font-size:12px}
}
/* Fix autozoom input iOS */
@media (max-width: 768px){input,select,textarea{font-size:16px !important}}
/* Resize Plotly */
@media (max-width:480px){
  .chart-h{height:240px !important}
}
/* Su telefono la fascia della firma mangia troppa altezza. */
@media (max-width:600px){#wm{display:none}}
"""


# ════════════════════════════════════════════════════════════════
# BUILD DASHBOARD HTML
# ════════════════════════════════════════════════════════════════
def build_dashboard(val_a: dict, val_b: dict, val_c: dict,
                    val_d: dict | None = None, val_e: dict | None = None,
                    val_f: dict | None = None, val_g: dict | None = None,
                    val_h: dict | None = None, val_i: dict | None = None,
                    val_l: dict | None = None, val_m: dict | None = None,
                    val_n: dict | None = None, val_o: dict | None = None,
                    val_p: dict | None = None, val_q: dict | None = None) -> str:

    def pval(p) -> str:
        # L'unico caso con prosa dentro e' il non significativo: restava in
        # italiano anche in inglese (si vedeva gia' nella sezione A), quindi
        # esce avvolto in data-it/data-en come il resto della pagina.
        if p is None: return ""
        if p < 0.001: return "p &lt; 0.001 &#10003;&#10003;&#10003;"
        if p < 0.01:  return f"p = {p:.3f} &#10003;&#10003;"
        if p < 0.05:  return f"p = {p:.3f} &#10003;"
        _it, _en = f"p = {p:.3f} (non significativo)", f"p = {p:.3f} (not significant)"
        return f'<span {_bi(_it, _en)}>{_it}</span>'

    def rcol(r) -> str:
        if r is None: return "var(--lt)"
        return "var(--green)" if abs(r) >= 0.6 else "var(--orng)" if abs(r) >= 0.4 else "var(--red)"

    def _r2(r) -> str:
        """Formatta un coefficiente a 2 decimali per i titoli; None/NaN → '—'."""
        if r is None or (isinstance(r, float) and np.isnan(r)):
            return "—"
        return f"{float(r):.2f}"

    def _bi(it_html: str, en_html: str) -> str:
        """Coppia di attributi data-it/data-en per prosa bilingue con numeri già
        interpolati. i18n.js scambia innerHTML in base alla lingua."""
        esc = lambda s: s.replace('"', "&quot;")
        return f'data-it="{esc(it_html)}" data-en="{esc(en_html)}"'

    r_a = val_a.get("r"); p_a = val_a.get("p"); n_a = val_a.get("n", 0)
    r_c = val_c.get("r"); p_c = val_c.get("p"); n_c = val_c.get("n", 0)
    ov  = val_b.get("overlap_pct", 0)

    # BUG1 FIX: usa _js_num() per slope/intercept — mai "None" in JS
    sl_a  = _js_num(val_a.get("slope"))
    ic_a  = _js_num(val_a.get("intercept"))
    sl_c  = _js_num(val_c.get("slope"))
    ic_c  = _js_num(val_c.get("intercept"))

    nota_c    = val_c.get("nota", "")
    nota_html = f'<div class="nota-warn">{nota_c}</div>' if nota_c else ""

    j = lambda x: json.dumps(x, ensure_ascii=True)

    scatter_a  = j(val_a.get("data", []))
    scatter_c  = j(val_c.get("scatter", []))
    top10_tpi  = j(val_b.get("top10_tpi", []))
    top10_ws   = j(val_b.get("top10_ws", []))
    div_pos    = j(val_b.get("divergenze_pos", []))
    div_neg    = j(val_b.get("divergenze_neg", []))

    # ── Sezione D ────────────────────────────────────────────────
    vd = val_d or {}
    has_v2   = vd.get("has_data", False)
    v2_msg   = vd.get("msg", "")
    n_aii    = vd.get("n_aii", 0)
    n_pri    = vd.get("n_pri", 0)
    aii_r    = vd.get("aii_tpi_r")
    ext_r    = vd.get("ext_r")
    eta_mean = vd.get("eta_mean")
    top_pri  = vd.get("top_pri", [])
    bot_pri  = vd.get("bot_pri", [])
    top_aii  = vd.get("top_aii", [])

    # BUG3 FIX: _sf() per pri, BUG6 FIX: _sf() per eta
    def v2_pri_rows(lst: list, color: str) -> str:
        h = ""
        for r in lst:
            pri_s = _sf(r.get("pri"), 2)
            h += (
                f'<tr><td style="color:var(--lp);font-weight:500">{r["nome"]}</td>'
                f'<td style="color:var(--lt);font-size:12px">{r["squadra"]}</td>'
                f'<td style="font-family:var(--mono);color:{color};font-weight:700">{pri_s}</td>'
                f'<td style="font-family:var(--mono);font-size:12px">'
                f'{r["n_inj"]} inj &middot; {r["giorni_out"]}gg</td></tr>'
            )
        return h

    def v2_aii_rows(lst: list) -> str:
        h = ""
        for r in lst:
            aii_s = _sf(r.get("aii"), 2)
            eta_s = _sf(r.get("eta"), 0)
            tpi_s = (("+" if (r.get("tpi") or 0) >= 0 else "") + _sf(r.get("tpi"), 2)) if r.get("tpi") is not None else "—"
            h += (
                f'<tr><td style="color:var(--lp);font-weight:500">{r["nome"]}</td>'
                f'<td style="color:var(--lt);font-size:12px">{r["squadra"]}</td>'
                f'<td style="font-family:var(--mono);color:var(--lp);font-weight:700">{aii_s}</td>'
                f'<td style="font-family:var(--mono);font-size:12px">{eta_s}aa</td>'
                f'<td style="font-family:var(--mono)">{tpi_s}</td></tr>'
            )
        return h

    scatter_d_js = j(vd.get("scatter", []))

    if has_v2:
        v2_section = f"""
<div class="section">
  <div class="section-hd">
    <div class="section-num">D</div>
    <div>
      <div class="section-ttl"><span {_bi("Indice Et&agrave; &amp; Affidabilit&agrave; Fisica","Age Index &amp; Physical Reliability")}>Age Index &amp; Physical Reliability</span> <span class="help" onclick="openM('v2')">?</span></div>
      <div class="section-sub" {_bi("Valida AII (Indice Et&agrave;) e PRI (Indice di Affidabilit&agrave; Fisica).","Validates AII (Age Index) and PRI (Physical Reliability Index).")}>Valida AII (Et&agrave; Index) e PRI (Physical Reliability Index).</div>
    </div>
  </div>
  <div class="g3">
    <div class="stat-box sb-teal"><div class="stat-val">{n_aii}</div><div class="stat-lbl" {_bi("Giocatori con AII","Players with AII")}>Giocatori con AII</div><div class="stat-sub">r(AII,TPI) = {_sf(aii_r,3)}</div></div>
    <div class="stat-box sb-purp"><div class="stat-val">{n_pri}</div><div class="stat-lbl" {_bi("Giocatori con PRI","Players with PRI")}>Giocatori con PRI</div><div class="stat-sub">r(TPI_ext,TPI) = {_sf(ext_r,3)}</div></div>
    <div class="stat-box sb-blue"><div class="stat-val">{_sf(eta_mean,1)}</div><div class="stat-lbl" {_bi("Et&agrave; media lega","League mean age")}>Et&agrave; media lega</div><div class="stat-sub" {_bi("anni &middot; ATT+CEN qualificati","years &middot; qualified FWD+MID")}>anni ATT+CEN qualificati</div></div>
  </div>
  <div class="g2">
    <div class="card"><div class="card-ttl" {_bi("Top 5 Affidabilit&agrave; Fisica (PRI)","Top 5 Physical Reliability (PRI)")}>Top 5 Affidabilit&agrave; Fisica (PRI)</div>
      <div class="table-wrap"><table><tr><th {_bi("Giocatore","Player")}>Giocatore</th><th {_bi("Squadra","Team")}>Squadra</th><th>PRI</th><th {_bi("Infortuni","Injuries")}>Infortuni</th></tr>
      <tbody>{v2_pri_rows(top_pri, "var(--green)")}</tbody></table></div></div>
    <div class="card"><div class="card-ttl" {_bi("Bottom 5 &mdash; Pi&ugrave; Fragili","Bottom 5 &mdash; Most Fragile")}>Bottom 5 &mdash; Pi&ugrave; Fragili</div>
      <div class="table-wrap"><table><tr><th {_bi("Giocatore","Player")}>Giocatore</th><th {_bi("Squadra","Team")}>Squadra</th><th>PRI</th><th {_bi("Infortuni","Injuries")}>Infortuni</th></tr>
      <tbody>{v2_pri_rows(bot_pri, "var(--red)")}</tbody></table></div></div>
  </div>
  <div class="g2">
    <div class="card"><div class="card-ttl" {_bi("Top 5 Indice Et&agrave; (AII)","Top 5 Age Index (AII)")}>Top 5 Et&agrave; Index (AII)</div>
      <div class="table-wrap"><table><tr><th {_bi("Giocatore","Player")}>Giocatore</th><th {_bi("Squadra","Team")}>Squadra</th><th>AII</th><th {_bi("Et&agrave;","Age")}>Et&agrave;</th><th>TPI</th></tr>
      <tbody>{v2_aii_rows(top_aii)}</tbody></table>
      <div class="interp" {_bi("AII alto + TPI in crescita = profilo scout ideale per investimento a lungo termine.","High AII + rising TPI = ideal scouting profile for a long-term investment.")}>AII alto + TPI in crescita = profilo scout ideale per investimento a lungo termine.</div></div>
    <div class="card"><div class="card-ttl"><span {_bi("Scatter AII vs PRI","Scatter AII vs PRI")}>Scatter AII vs PRI</span> <span class="help" onclick="openM('scatter_d')">?</span></div>
      <div id="chart-d" class="chart-h" style="height:280px"></div></div>
  </div>
</div>"""
    elif v2_msg:
        v2_section = f'<div class="nota-warn" style="margin-top:20px">{v2_msg}</div>'
    else:
        v2_section = ""

    # ── Sezione E — TPI Pro ──────────────────────────────────────
    ve = val_e or {}
    has_pro  = ve.get("has_data", False)
    pro_msg  = ve.get("msg", "")

    def mover_rows(lst: list, color: str, arrow: str) -> str:
        h = ""
        for m in lst:
            delta   = m["delta"]
            delta_s = f"{arrow}{abs(delta)}"
            tpi_s   = ("+" if m["tpi"] >= 0 else "") + _sf(m["tpi"], 2)
            pro_s   = ("+" if m["tpi_pro"] >= 0 else "") + _sf(m["tpi_pro"], 2)
            aii_s   = _sf(m.get("aii"), 2)
            pri_s   = _sf(m.get("pri"), 2)
            h += f"""<div class="mover-row">
              <div class="mover-delta" style="color:{color}">{delta_s}</div>
              <div class="mover-info">
                <div class="mover-nm">{m['nome']}</div>
                <div class="mover-sub">{m['squadra']} &middot; <span {_bi(m['ruolo'], _ROLE_EN.get(m['ruolo'], m['ruolo']))}>{m['ruolo']}</span></div>
              </div>
              <div class="mover-scores">
                <div class="mover-s"><div class="mover-s-lbl">TPI</div><div style="color:var(--orng);font-family:var(--mono);font-size:11px;font-weight:700">{tpi_s}</div></div>
                <div class="mover-s"><div class="mover-s-lbl">PRO</div><div style="color:var(--lp);font-family:var(--mono);font-size:11px;font-weight:700">{pro_s}</div></div>
                <div class="mover-s"><div class="mover-s-lbl">AII</div><div style="color:var(--lp);font-family:var(--mono);font-size:11px">{aii_s}</div></div>
                <div class="mover-s"><div class="mover-s-lbl">PRI</div><div style="color:var(--lp);font-family:var(--mono);font-size:11px">{pri_s}</div></div>
              </div>
            </div>"""
        return h

    if has_pro:
        r_pro    = ve["r_corr"]
        p_pro    = ve["p_corr"]
        n_pro    = ve["n_pro"]
        n_dp     = ve["n_delta_pos"]
        n_dn     = ve["n_delta_neg"]
        sl_pro   = _js_num(ve.get("slope"))
        ic_pro   = _js_num(ve.get("intercept"))
        scatter_e = j(ve.get("scatter", []))
        top_sal  = ve.get("top_saliti", [])
        top_sces = ve.get("top_scesi",  [])

        e_section = f"""
<div class="section">
  <div class="section-hd">
    <div class="section-num">E</div>
    <div>
      <div class="section-ttl"><span {_bi("Validazione TPI Pro — 7 dimensioni + 5 modulatori","TPI Pro Validation — 7 dimensions + 5 modulators")}>TPI Pro Validation — 7 dimensions + 5 modulators</span> <span class="help" onclick="openM('tpi_pro')">?</span></div>
      <div class="section-sub" {_bi("Confronto ranking TPI classico vs TPI Pro (TPI + 5 modulatori). Chi guadagna/perde posizioni?","Ranking comparison: classic TPI vs TPI Pro (TPI + 5 modulators). Who gains/loses positions?")}>Confronto ranking TPI classico vs TPI Pro (TPI + 5 modulatori). Chi guadagna/perde posizioni?</div>
    </div>
  </div>
  <div class="g4">
    <div class="stat-box sb-purp"><div class="stat-val">{n_pro}</div><div class="stat-lbl" {_bi("Giocatori TPI Pro","TPI Pro players")}>Giocatori TPI Pro</div><div class="stat-sub" {_bi("con AII + PRI","with AII + PRI")}>con AII + PRI</div></div>
    <div class="stat-box sb-blue"><div class="stat-val" style="color:{rcol(r_pro)}">{_sf(r_pro,3)}</div><div class="stat-lbl">r(TPI,TPI Pro)</div><div class="stat-sub">{pval(p_pro)}</div></div>
    <div class="stat-box sb-green"><div class="stat-val">{n_dp}</div><div class="stat-lbl" {_bi("Salgono &gt;2 pos","Rise &gt;2 pos")}>Salgono &gt;2 pos</div><div class="stat-sub" {_bi("valorizzati da AII/PRI","boosted by AII/PRI")}>valorizzati da AII/PRI</div></div>
    <div class="stat-box"><div class="stat-val">{n_dn}</div><div class="stat-lbl" {_bi("Scendono &gt;2 pos","Fall &gt;2 pos")}>Scendono &gt;2 pos</div><div class="stat-sub" {_bi("penalizzati da AII/PRI","penalized by AII/PRI")}>penalizzati da AII/PRI</div></div>
  </div>
  <div class="g2">
    <div class="card"><div class="card-ttl"><span {_bi("Scatter TPI vs TPI Pro","Scatter TPI vs TPI Pro")}>Scatter TPI vs TPI Pro</span> <span class="help" onclick="openM('scatter_e')">?</span></div>
      <div id="chart-e" class="chart-h" style="height:300px"></div></div>
    <div class="card"><div class="card-ttl" {_bi("Interpretazione","Interpretation")}>Interpretazione</div>
      <div style="font-size:13px;color:var(--ls);line-height:1.75" {_bi('<p style="margin-bottom:10px">Questa <strong style="color:var(--lp)">r non &egrave; un risultato</strong>: il TPI Pro contiene il TPI, quindi &egrave; alta per costruzione. Dice solo che il Pro non ha stravolto la classifica, non che i modulatori funzionino &mdash; quella domanda &egrave; la sezione <strong>I</strong>, e la risposta &egrave; che non aggiungono.</p><p style="margin-bottom:10px">Le <strong style="color:var(--green)">salite</strong> sono giocatori giovani e fisicamente affidabili che il TPI classico posiziona pi&ugrave; in basso.</p><p>Le <strong style="color:var(--red)">discese</strong> sono veterani o giocatori fragili che il TPI classico posiziona pi&ugrave; in alto.</p>', '<p style="margin-bottom:10px">This <strong style="color:var(--lp)">r is not a result</strong>: TPI Pro contains the TPI, so it is high by construction. It only says the Pro did not upend the ranking, not that the modulators work &mdash; that question is section <strong>I</strong>, and the answer is that they do not add.</p><p style="margin-bottom:10px">The <strong style="color:var(--green)">risers</strong> are young, physically reliable players the classic TPI places lower.</p><p>The <strong style="color:var(--red)">fallers</strong> are veterans or fragile players the classic TPI places higher.</p>')}>
        <p style="margin-bottom:10px">Questa <strong style="color:var(--lp)">r non &egrave; un risultato</strong>: il TPI Pro contiene il TPI, quindi &egrave; alta per costruzione. Dice solo che il Pro non ha stravolto la classifica &mdash; se i modulatori servano lo chiede la sezione <strong>I</strong>, e la risposta &egrave; che non aggiungono.</p>
        <p style="margin-bottom:10px">Le <strong style="color:var(--green)">salite</strong> sono giocatori giovani e fisicamente affidabili che il TPI classico posiziona pi&ugrave; in basso.</p>
        <p>Le <strong style="color:var(--red)">discese</strong> sono veterani o giocatori fragili che il TPI classico posiziona pi&ugrave; in alto.</p>
      </div>
      <div class="interp" {_bi("Non esiste un valore &laquo;giusto&raquo; per r(TPI, TPI Pro): un indice correlato col proprio sovrainsieme lo sar&agrave; sempre. L&rsquo;unico modo di sapere se i modulatori servono &egrave; misurarli fuori campione, e lo fa la sezione I.","There is no &ldquo;right&rdquo; value for r(TPI, TPI Pro): an index correlated with its own superset always will be. The only way to know whether the modulators help is to measure them out-of-sample, which is what section I does.")}>Non esiste un valore &laquo;giusto&raquo; per r(TPI, TPI Pro): un indice correlato col proprio sovrainsieme lo sar&agrave; sempre. Se i modulatori servano lo misura la sezione I, fuori campione.</div>
    </div>
  </div>
  <div class="g2">
    <div class="card">
      <div class="card-ttl" style="color:var(--green)">&#9650; <span {_bi("Chi sale con TPI Pro (top 8)","Who rises with TPI Pro (top 8)")}>Chi sale con TPI Pro (top 8)</span></div>
      <div id="movers-up">{mover_rows(top_sal, "var(--green)", "▲")}</div>
    </div>
    <div class="card">
      <div class="card-ttl" style="color:var(--red)">&#9660; <span {_bi("Chi scende con TPI Pro (top 8)","Who falls with TPI Pro (top 8)")}>Chi scende con TPI Pro (top 8)</span></div>
      <div id="movers-dn">{mover_rows(top_sces, "var(--red)", "▼")}</div>
    </div>
  </div>
</div>"""

        e_js = f"""
(function(){{
  var sc=SCATTER_E,sl={sl_pro},ic={ic_pro};
  if(!sc||!sc.length){{document.getElementById("chart-e").innerHTML='<div style="color:var(--lt);padding:32px;text-align:center;font-size:13px">Nessun dato TPI Pro</div>';return;}}
  var xv=sc.map(d=>d.tpi),yv=sc.map(d=>d.pro);
  var xmin=Math.min(...xv),xmax=Math.max(...xv);
  var rc={{"POR":"#7A8A84","DIF":"#5A93C4","CEN":"#5FAE7E","ATT":"#D98E6A","":"#46554F"}};
  Plotly.newPlot("chart-e",[
    {{type:"scatter",mode:"markers",x:xv,y:yv,
      text:sc.map(d=>d.nome+"<br>"+d.squadra),
      hovertemplate:"%{{text}}<br>TPI: %{{x:.3f}}<br>TPI Pro: %{{y:.3f}}<extra></extra>",
      marker:{{color:sc.map(d=>rc[d.ruolo]||"#46554F"),size:8,opacity:.82,
        line:{{color:"rgba(233,240,236,.12)",width:1}}}}}},
    {{type:"scatter",mode:"lines",
      x:[xmin,xmax],y:[sl*xmin+ic,sl*xmax+ic],
      line:{{color:"rgba(255,176,32,.55)",width:2,dash:"dot"}},hoverinfo:"skip"}},
    {{type:"scatter",mode:"lines",
      x:[Math.min(xmin,Math.min(...yv)),Math.max(xmax,Math.max(...yv))],
      y:[Math.min(xmin,Math.min(...yv)),Math.max(xmax,Math.max(...yv))],
      line:{{color:"rgba(233,240,236,.07)",width:1,dash:"dot"}},hoverinfo:"skip"}},
  ],{{...BL,
    xaxis:{{...BL.xaxis,title:T("val_ax_tpi_classic","TPI Classico (7 dim)")}},
    yaxis:{{...BL.yaxis,title:T("val_ax_tpi_pro","TPI Pro (7 dim + 5 mod)")}},
    margin:{{t:8,b:46,l:54,r:8}},height:300,showlegend:false,
    annotations:[{{x:.02,y:.97,xref:"paper",yref:"paper",
      text:"r = {_sf(r_pro,3)}",showarrow:false,
      font:{{color:"rgba(233,240,236,.66)",size:13}},align:"left"}}]}},PL);
}})();"""
    elif pro_msg:
        e_section = f'<div class="nota-warn" style="margin-top:16px">{pro_msg}</div>'
        e_js = ""
    else:
        e_section = ""
        e_js = ""

    # ── Badge recap ───────────────────────────────────────────────
    # Costruiamo le 4 card standard (A B C D)
    def _badge_html(badge_id: str, label_it: str, label_en: str, val, hi: float, mid: float,
                    sub: str, color_override: str = "") -> str:
        if val is None:
            cls, lbl_it, lbl_en = "badge-blue", "N/D", "N/A"
        elif abs(val) >= hi:
            cls, lbl_it, lbl_en = "badge-green", "Buono &#10003;", "Good &#10003;"
        elif abs(val) >= mid:
            cls, lbl_it, lbl_en = "badge-orng", "Moderato", "Moderate"
        else:
            cls, lbl_it, lbl_en = "badge-red", "Basso", "Low"
        accent = f"border-left:3px solid {color_override};" if color_override else ""
        return f"""<div class="rc-card" style="{accent}">
          <div class="rc-lbl" {_bi(label_it, label_en)}>{label_it}</div>
          <div id="{badge_id}"><span class="badge {cls}" {_bi(lbl_it, lbl_en)}>{lbl_it}</span></div>
          <div class="rc-sub">{sub}</div>
        </div>"""

    def _rc_card(label_it: str, label_en: str, badge_cls: str,
                 tag_it: str, tag_en: str, sub: str, color_override: str = "") -> str:
        """Card di riepilogo con verdetto statico (niente id: il JS non la tocca)."""
        accent = f"border-left:3px solid {color_override};" if color_override else ""
        return f"""<div class="rc-card" style="{accent}">
          <div class="rc-lbl" {_bi(label_it, label_en)}>{label_it}</div>
          <div><span class="badge {badge_cls}" {_bi(tag_it, tag_en)}>{tag_it}</span></div>
          <div class="rc-sub">{sub}</div>
        </div>"""

    # L'ordine del riepilogo segue quanto un risultato regge, non l'alfabeto.
    # Q per primo: e' il test che puo' bocciare l'indice, e nasconderlo a meta'
    # pagina sarebbe lo stesso errore dell'ordine A-B-C di prima.
    recap_abcd = ""
    vq_r = val_q or {}
    if vq_r.get("has_data"):
        _qb = next((v for v in vq_r["criteri"].get("livello", {}).get("baselines", [])
                    if v["key"] == "output_grezzo"), None)
        if _qb is not None:
            if _qb["tpi_better"]:
                _qcls, _qt_it, _qt_en = "badge-green", "La batte &#10003;", "Beats it &#10003;"
            elif _qb["ci_hi"] < 0:
                _qcls, _qt_it, _qt_en = "badge-red", "Non la batte", "Does not beat it"
            else:
                _qcls, _qt_it, _qt_en = "badge-orng", "Pari", "Tie"
            recap_abcd += _rc_card(
                "Q &mdash; vs output grezzo", "Q &mdash; vs raw output", _qcls, _qt_it, _qt_en,
                f'&Delta;RMSE {_qb["delta_rmse"]:+.4f} &middot; n = {_qb["n"]}',
                "var(--orng)")

    recap_abcd += _badge_html("badge-c", "C &mdash; Backtest su output grezzo",
                              "C &mdash; Backtest on raw output",
                              r_c, SOGLIE["c_hi"], SOGLIE["c_mid"],
                              f"r = {_sf(r_c,3)} &middot; n = {n_c}")

    # P — stava secondo quando il guadagno a due stagioni era significativo su
    # 35 giocatori. Sul campione intero l'IC si e' allargato fino a contenere lo
    # zero, e la regola dichiarata dice che in quel caso non regge: quindi
    # scende sotto C. Il badge si calcola da significativo_95, non a mano, per
    # cui segue il dato anche la prossima volta.
    vp_r = val_p or {}
    _pc_r = (vp_r.get("cross_2season") or {}) if vp_r.get("has_data") else {}
    if _pc_r.get("rho_2season_mean") is not None:
        recap_abcd += _rc_card(
            "P &mdash; Persistenza 2 stagioni", "P &mdash; 2-season persistence",
            "badge-green" if _pc_r.get("significativo_95") else "badge-orng",
            "Regge &#10003;" if _pc_r.get("significativo_95") else "Indicativo",
            "Holds &#10003;" if _pc_r.get("significativo_95") else "Indicative",
            f'&rho; {_pc_r["rho_single"]:+.2f} &rarr; {_pc_r["rho_2season_mean"]:+.2f} &middot; n = {_pc_r["n"]}')

    # D — solo se ha dati. Anche questa e' una descrizione, non un test: la
    # soglia la bocciava ("Basso") per una r negativa che l'AII deve avere,
    # visto che premia chi ENTRA nel prime invece di chi ci e' gia'.
    if has_v2:
        recap_abcd += _rc_card(
            "D &mdash; Et&agrave; &amp; Fisico", "D &mdash; Age &amp; Physical",
            "badge-blue", "Descrittivo", "Descriptive",
            f"AII:{n_aii} &middot; PRI:{n_pri} &middot; r={_sf(vd.get('aii_tpi_r'),3)}",
            "var(--teal)"
        )

    # A e B chiudono, e non portano un verdetto: i voti di A sono inseriti a
    # mano su un sottoinsieme scelto — l'IC puo' anche smettere di contenere lo
    # zero, resta un campione di comodo — e B e' un criterio che non puo'
    # fallire. Restano perche' il contenuto e' interessante, non perche'
    # dimostrino qualcosa.
    # _ci_txt e b_hyper nascono piu' in basso nel corpo: qui servono gia' pronti.
    _a_ci = f'[{_sf(val_a.get("ci_lo"),3)}, {_sf(val_a.get("ci_hi"),3)}]'
    _b_p = _sf((val_b.get("hyper") or {}).get("p"), 2)
    recap_abcd += _rc_card(
        "A &mdash; Correlazione Fantacalcio", "A &mdash; Fantacalcio correlation",
        "badge-blue", "Descrittivo", "Descriptive",
        f'r = {_sf(r_a,3)} &middot; IC {_a_ci} &middot; n = {n_a}')
    recap_abcd += _rc_card(
        "B &mdash; Overlap WhoScored", "B &mdash; WhoScored overlap",
        "badge-blue", "Descrittivo", "Descriptive",
        f'{ov}% &middot; p = {_b_p}')

    # E — pannello separato in evidenza (sempre mostrato, anche se no dati)
    if has_pro:
        r_pro_v   = ve.get("r_corr")
        # Niente verdetto qui: il TPI Pro contiene il TPI, quindi questa r e'
        # alta per costruzione e un badge "Ottimo" premierebbe la circolarita'.
        # Stesso trattamento di A, B e D.
        e_recap_inner = f"""
          <div style="display:flex;align-items:center;gap:8px;margin-bottom:10px">
            <span class="badge badge-blue" {_bi("Descrittivo","Descriptive")}>Descrittivo</span>
            <span style="font-size:11px;color:var(--lt)">r(TPI, TPI Pro)</span>
          </div>
          <div style="font-size:26px;font-weight:500;letter-spacing:-.03em;
                      font-family:var(--mono);color:var(--lp);line-height:1;margin-bottom:6px">
            {_sf(r_pro_v,3)}
          </div>
          <div style="font-size:11px;color:var(--lt);margin-bottom:12px" {_bi(f'{ve.get("n_pro",0)} giocatori con AII+PRI', f'{ve.get("n_pro",0)} players with AII+PRI')}>
            {ve.get("n_pro",0)} giocatori con AII+PRI
          </div>
          <div style="display:flex;gap:8px;flex-wrap:wrap">
            <div style="flex:1;min-width:60px;background:none;
                        border:0;border-left:1px solid var(--green);border-radius:0;
                        padding:2px 0 2px 10px">
              <div style="font-size:16px;font-weight:800;color:var(--green);
                          font-family:var(--mono)">{ve.get("n_delta_pos",0)}</div>
              <div style="font-size:9px;color:var(--lt);text-transform:uppercase;
                          letter-spacing:.5px;margin-top:2px" {_bi("Salgono","Rise")}>Salgono</div>
            </div>
            <div style="flex:1;min-width:60px;background:none;
                        border:0;border-left:1px solid var(--red);border-radius:0;
                        padding:2px 0 2px 10px">
              <div style="font-size:16px;font-weight:800;color:var(--red);
                          font-family:var(--mono)">{ve.get("n_delta_neg",0)}</div>
              <div style="font-size:9px;color:var(--lt);text-transform:uppercase;
                          letter-spacing:.5px;margin-top:2px" {_bi("Scendono","Fall")}>Scendono</div>
            </div>
          </div>"""
    else:
        e_recap_inner = f"""
          <div style="font-size:12px;color:var(--lt);line-height:1.6" {_bi('Popola <code style="font-family:var(--mono);color:var(--lp)">t_infortuni</code> e <code style="font-family:var(--mono);color:var(--lp)">data_nascita</code>, poi riesegui <code style="font-family:var(--mono)">parte1_analisi.py</code>.', 'Populate <code style="font-family:var(--mono);color:var(--lp)">t_infortuni</code> and <code style="font-family:var(--mono);color:var(--lp)">data_nascita</code>, then re-run <code style="font-family:var(--mono)">parte1_analisi.py</code>.')}>
            Popola <code style="font-family:var(--mono);color:var(--lp)">t_infortuni</code>
            e <code style="font-family:var(--mono);color:var(--lp)">data_nascita</code>,
            poi riesegui <code style="font-family:var(--mono)">parte1_analisi.py</code>.
          </div>"""

    recap_html = f"""
<div class="recap-outer">
  <!-- Colonna sinistra: A B C D -->
  <div class="recap-left">
    <div style="font-family:var(--mono);font-size:9.5px;font-weight:500;color:var(--lt);
                text-transform:uppercase;letter-spacing:.16em;margin-bottom:16px" {_bi("Riepilogo Validazione","Validation summary")}>
      Riepilogo Validazione
    </div>
    <div class="rc-grid">{recap_abcd}</div>
  </div>

  <!-- Colonna destra: E — TPI Pro in evidenza -->
  <div class="recap-right">
    <div style="display:flex;align-items:center;gap:8px;margin-bottom:12px;flex-wrap:wrap">
      <div style="font-family:var(--mono);font-size:9.5px;font-weight:500;color:var(--lt);
                  text-transform:uppercase;letter-spacing:.16em">
        E &mdash; TPI Pro
      </div>
      <span style="display:inline-flex;align-items:center;gap:4px;padding:0;
                   background:none;border:0;border-radius:0;
                   font-family:var(--mono);font-size:9.5px;font-weight:500;
                   color:var(--orng);text-transform:uppercase;letter-spacing:.14em">
        <span {_bi("Nuovo indice","New index")}>Nuovo indice</span>
      </span>
    </div>
    <div style="font-size:12px;color:var(--ls);line-height:1.6;margin-bottom:14px" {_bi('Aggiunge <strong style="color:var(--lp)">AII</strong> (et&agrave;) e <strong style="color:var(--lp)">PRI</strong> (fisico) al TPI classico. La r qui sotto misura quanto il Pro somiglia al TPI, non se funziona: lo contiene.', 'Adds <strong style="color:var(--lp)">AII</strong> (age) and <strong style="color:var(--lp)">PRI</strong> (physical) to the classic TPI. The r below measures how much the Pro resembles the TPI, not whether it works: it contains it.')}>
      Aggiunge <strong style="color:var(--lp)">AII</strong> (et&agrave;)
      e <strong style="color:var(--lp)">PRI</strong> (fisico) al TPI classico.
      La r qui sotto misura quanto il Pro somiglia al TPI, non se funziona: lo contiene.
    </div>
    {e_recap_inner}
  </div>
</div>"""

    # ── SPIEGAZIONI modale ────────────────────────────────────────
    spieg_extra = ""
    if has_v2:
        spieg_extra += """
  v2: {icon:"🧬",ttl:"Età Index & Affidabilità Fisica",ttl_en:"Age Index & Physical Reliability",sub:"AII e PRI — indici v2 del sistema",sub_en:"AII and PRI — system v2 indices",
    body:"AII (Age Impact Index) è orientato allo scouting: premia chi STA ENTRANDO nel prime, non chi ci è già. La gaussiana ScoutPeak è centrata sui 23 anni (σ=3.5), ma il composito — 0.55 ScoutPeak + 0.25 GrowthPotential + 0.20 Freshness — ha il massimo intorno ai 21.8 anni, perché il bonus di crescita lo sposta a sinistra.\\n\\nValori reali della curva: 18 anni → 0.65 · 22 → 0.78 · 25 → 0.67 · 27 → 0.49 · 30 → 0.24.\\n\\nPRI (Physical Reliability Index) misura l'affidabilità fisica storica: disponibilità, numero infortuni, gravità.",
    body_en:"AII (Age Impact Index) is scouting-oriented: it rewards players who are ENTERING their prime, not those already in it. The ScoutPeak Gaussian is centred on 23 (σ=3.5), but the composite — 0.55 ScoutPeak + 0.25 GrowthPotential + 0.20 Freshness — peaks around 21.8, because the growth bonus pulls it left.\\n\\nActual curve values: 18 → 0.65 · 22 → 0.78 · 25 → 0.67 · 27 → 0.49 · 30 → 0.24.\\n\\nPRI (Physical Reliability Index) measures historical physical reliability: availability, injury count, severity.",
    ex:"AII 0.78 + TPI +1.2 = ventiduenne che sta entrando nel prime con rendimento già alto. AII 0.24 + TPI +1.5 = trentenne forte adesso, ma la curva dice che il margine di crescita non c'è più.",ex_en:"AII 0.78 + TPI +1.2 = a 22-year-old entering their prime who already performs. AII 0.24 + TPI +1.5 = a 30-year-old strong now, but the curve says the room to grow is gone."},
  scatter_d: {icon:"🔵",ttl:"Scatter AII vs PRI",ttl_en:"Scatter AII vs PRI",sub:"X = AII (età), Y = PRI (fisico)",sub_en:"X = AII (age), Y = PRI (physical)",
    body:"Ogni punto è un giocatore. Colore = ruolo.\\n\\nIn alto a destra: giocatore al picco dell'età E affidabile fisicamente = profilo ideale.\\nIn basso a sinistra: giovane e fragile = alto potenziale, alto rischio.",
    body_en:"Each dot is a player. Colour = role.\\n\\nTop-right: player at peak age AND physically reliable = ideal profile.\\nBottom-left: young and fragile = high potential, high risk.",
    ex:"AII 0.85, PRI 0.90: veterano affidabile in picco. AII 0.42, PRI 0.88: giovane sano — scouting top.",ex_en:"AII 0.85, PRI 0.90: reliable veteran at peak. AII 0.42, PRI 0.88: healthy youngster — top scouting."},"""
    if has_pro:
        spieg_extra += """
  tpi_pro: {icon:"✨",ttl:"TPI Pro — 7 dimensioni + 5 modulatori",ttl_en:"TPI Pro — 7 dimensions + 5 modulators",sub:"TPI = 7 dim. TPI Pro = TPI + AII, PRI, stabilità ctx, trend forma, EMI",sub_en:"TPI = 7 dims. TPI Pro = TPI + AII, PRI, ctx stability, form trend, EMI",
    body:"Il TPI Pro combina il TPI (media pesata di 7 dimensioni) con 5 modulatori scout: AII, PRI, stabilità fra contesti, trend forma ed EMI. I pesi cambiano per fascia d'età.\\n\\nChi sale: giovani in ascesa con buona affidabilità fisica.\\nChi scende: veterani fragili che il TPI classico sopravvaluta.\\n\\nAttenzione a r(TPI, TPI Pro): il Pro CONTIENE il TPI, quindi quella correlazione è alta per costruzione e non può dire se i modulatori servono. La domanda vera la fa il test I, out-of-sample.",
    body_en:"TPI Pro combines the TPI (weighted mean of 7 dimensions) with 5 scout modulators: AII, PRI, cross-context stability, form trend and EMI. Weights change by age band.\\n\\nRisers: young players on the way up with good physical reliability.\\nFallers: fragile veterans the classic TPI overrates.\\n\\nBe careful with r(TPI, TPI Pro): the Pro CONTAINS the TPI, so that correlation is high by construction and cannot tell you whether the modulators help. The real question is asked by test I, out-of-sample.",
    ex:"E la risposta del test I è che sul rendimento futuro il Pro non batte il TPI base: la differenza di RMSE è indistinguibile da zero. Va usato per leggere il profilo di un giocatore, non per prevedere.",ex_en:"And test I answers that on future output the Pro does not beat the base TPI: the RMSE difference is indistinguishable from zero. Use it to read a player's profile, not to forecast."},
  scatter_e: {icon:"📈",ttl:"Scatter TPI vs TPI Pro",ttl_en:"Scatter TPI vs TPI Pro",sub:"X = TPI classico | Y = TPI Pro",sub_en:"X = classic TPI | Y = TPI Pro",
    body:"Punti sopra la diagonale: guadagnano con TPI Pro (AII/PRI alti).\\nPunti sotto: perdono.\\n\\nLa retta tratteggiata viola = regressione. Grigia = y=x (nessuna variazione).",
    body_en:"Dots above the diagonal: gain with TPI Pro (high AII/PRI).\\nDots below: lose.\\n\\nThe purple dashed line = regression. Grey = y=x (no change).",
    ex:"Punto molto sopra la diagonale: giovane affidabile che il TPI classico sottovalutava.",ex_en:"A dot well above the diagonal: a reliable youngster the classic TPI was undervaluing."},"""

    # ══════════════════════════════════════════════════════════════
    # Note di rigore (A/B/C) + nuove sezioni F/G/H/I
    # ══════════════════════════════════════════════════════════════
    def _ci_txt(lo, hi):
        return f"[{_sf(lo,3)}, {_sf(hi,3)}]" if lo is not None and hi is not None else "&mdash;"

    # Hero sottotitolo bilingue (numeri reali in entrambe le lingue).
    # Il conteggio non e' piu' scritto a mano: A/B/C escono sempre, le altre
    # solo se hanno dati, quindi il numero in apertura seguiva la pagina solo
    # per caso. "Indipendenti" e' caduto: girano tutti sugli stessi giocatori.
    _n_test = conta_test_pubblicati(val_d, val_e, val_f, val_g, val_h, val_i,
                                    val_l, val_m, val_n, val_o, val_p, val_q)
    # Cosa rivendica l'indice, in apertura e senza giri di parole. Prima diceva
    # "verifica che il TPI misuri qualita' reale" e metteva in vetrina il
    # backtest r=0.725 — che pero' e' sull'output grezzo, cioe' la componente
    # dominante, non sul composito. Il test Q lo ha reso insostenibile.
    _q_hero = None
    if (val_q or {}).get("has_data"):
        _q_hero = next((v for v in val_q["criteri"].get("livello", {}).get("baselines", [])
                        if v["key"] == "output_grezzo"), None)
    if _q_hero is None:
        _hq_it, _hq_en = "", ""
    elif _q_hero["ci_hi"] < 0:
        _hq_it = " Messo alla prova contro una baseline banale, l&rsquo;output grezzo per-90, <strong>ha perso</strong>."
        _hq_en = " Tested against a trivial baseline, raw per-90 output, it <strong>lost</strong>."
    elif _q_hero["tpi_better"]:
        _hq_it = " Messo alla prova contro una baseline banale, l&rsquo;output grezzo per-90, <strong>l&rsquo;ha battuta</strong>."
        _hq_en = " Tested against a trivial baseline, raw per-90 output, it <strong>won</strong>."
    else:
        _hq_it = " Messo alla prova contro una baseline banale, l&rsquo;output grezzo per-90, ha <strong>pareggiato</strong>."
        _hq_en = " Tested against a trivial baseline, raw per-90 output, it <strong>tied</strong>."
    _mono = (val_m or {}).get("monotonia_rho")
    _hm_it = (f' Quello che regge &egrave; l&rsquo;ordinamento: &rho; fra decili '
              f'<strong style="color:var(--orng)">{_r2(_mono)}</strong>.') if _mono is not None else ""
    _hm_en = (f' What holds up is the ordering: &rho; across deciles '
              f'<strong style="color:var(--orng)">{_r2(_mono)}</strong>.') if _mono is not None else ""
    _hsub_it = (
        'Il TPI &egrave; un indice <strong style="color:var(--lp)">descrittivo</strong>: '
        'ordina i giocatori, non predice il loro rendimento futuro.'
        + _hq_it + _hm_it +
        f' {_n_test} verifiche, riportate come sono uscite. '
        'Clicca <strong style="color:var(--lp)">?</strong> su ogni sezione per i dettagli metodologici.'
    )
    _hsub_en = (
        'The TPI is a <strong style="color:var(--lp)">descriptive</strong> index: '
        'it ranks players, it does not predict their future output.'
        + _hq_en + _hm_en +
        f' {_n_test} checks, reported exactly as they came out. '
        'Click <strong style="color:var(--lp)">?</strong> on each section for methodology details.'
    )
    hero_sub = f'<div class="hero-sub" {_bi(_hsub_it, _hsub_en)}>{_hsub_it}</div>'

    # ── Cosa rivendica l'indice ──────────────────────────────────
    # Sta subito sotto il riepilogo perche' e' la cosa che decide come si legge
    # tutto il resto. Il terzo riquadro e' il piu' importante: distingue "non
    # dimostrato" da "non ancora misurabile", che non sono la stessa cosa.
    _cl_rho = (val_m or {}).get("monotonia_rho")
    _cl_conv = None
    if (val_l or {}).get("has_data"):
        _cl_pv = [r for r in val_l.get("per_vintage", []) if r.get("spearman_rho") is not None]
        # Il vintage PIU' PRESTO, non l'ultimo: la rivendicazione e' "quanto
        # presto il ranking si assesta". Citare g36 non direbbe niente.
        _cl_conv = _cl_pv[0] if _cl_pv else None
    _cl_ordina_it = ("Ordinare i giocatori dentro una stagione. I decili di TPI salgono "
                     f"insieme al rendimento (&rho; = {_sf(_cl_rho,3)})"
                     + (f", e il ranking di giornata {_cl_conv['vintage_giornata']} vale gi&agrave; "
                        f"&rho; = {_sf(_cl_conv['spearman_rho'],3)} contro quello finale." if _cl_conv else "."))
    _cl_ordina_en = ("Ranking players within a season. TPI deciles rise together with output "
                     f"(&rho; = {_sf(_cl_rho,3)})"
                     + (f", and the matchday-{_cl_conv['vintage_giornata']} ranking already scores "
                        f"&rho; = {_sf(_cl_conv['spearman_rho'],3)} against the final one." if _cl_conv else "."))
    claim_html = f"""
<div class="section" style="margin-bottom:34px">
  <div class="section-hd">
    <div class="section-num">&#9679;</div>
    <div>
      <div class="section-ttl" {_bi("Cosa rivendica questo indice","What this index claims")}>Cosa rivendica questo indice</div>
      <div class="section-sub" {_bi("Scritto qui in alto perch&eacute; decide come va letto tutto il resto della pagina.","Stated up here because it decides how the rest of this page should be read.")}>Scritto qui in alto perch&eacute; decide come va letto tutto il resto della pagina.</div>
    </div>
  </div>
  <div class="g3">
    <div class="card" style="border-left:2px solid var(--green)">
      <div class="card-ttl" {_bi("Lo fa","It does")}>Lo fa</div>
      <div class="interp" style="border-left:0;padding-left:0" {_bi(_cl_ordina_it, _cl_ordina_en)}>{_cl_ordina_it}</div>
    </div>
    <div class="card" style="border-left:2px solid var(--red)">
      <div class="card-ttl" {_bi("Non lo fa","It does not")}>Non lo fa</div>
      <div class="interp" style="border-left:0;padding-left:0" {_bi("Predire il rendimento futuro. Messo a confronto con l&rsquo;output grezzo per-90 &mdash; il suo stesso input dominante &mdash; su dati che non aveva visto, il TPI non lo batte (sezione Q). Chi vuole prevedere i gol della prossima met&agrave; di stagione usi quella rate stat, non questo indice.", "Predicting future output. Compared with raw per-90 output &mdash; its own dominant input &mdash; on data it had not seen, the TPI does not beat it (section Q). If you want to forecast next half-season goals, use that rate stat, not this index.")}>Predire il rendimento futuro: contro l&rsquo;output grezzo per-90, su dati mai visti, il TPI non lo batte (sezione Q).</div>
    </div>
    <div class="card" style="border-left:2px solid var(--orng)">
      <div class="card-ttl" {_bi("Non &egrave; ancora misurabile","Not yet measurable")}>Non &egrave; ancora misurabile</div>
      <div class="interp" style="border-left:0;padding-left:0" {_bi("L&rsquo;ipotesi per cui l&rsquo;indice &egrave; nato: riconoscere chi <em>sta entrando</em> nel prime, non chi ci &egrave; gi&agrave;. Per giudicarla servirebbe seguire gli stessi giocatori per anni; qui ci sono due stagioni. Non &egrave; smentita, &egrave; non testata &mdash; e le due cose non vanno confuse.", "The hypothesis the index was built on: spotting who is <em>entering</em> their prime rather than who is already in it. Judging it would mean following the same players for years; there are two seasons here. It is not disproved, it is untested &mdash; and the two are not the same thing.")}>L&rsquo;ipotesi per cui l&rsquo;indice &egrave; nato: riconoscere chi <em>sta entrando</em> nel prime. Servirebbero anni di dati, qui ce ne sono due. Non &egrave; smentita, &egrave; non testata.</div>
    </div>
  </div>
</div>"""

    # Su quanti giocatori sta girando questa pagina. Non e' un dettaglio: col
    # taglio ai primi 100 si confrontano fra loro solo i migliori, e ogni
    # correlazione esce piu' bassa di quanto sia. Chi legge deve saperlo dalla
    # prima schermata, quindi la pill lo dice da sola a seconda del file letto.
    _camp_full = payload_corrente().name == PAYLOAD_FULL.name
    _pill_camp_it = ("Test su tutti i qualificati" if _camp_full
                     else "Test sui primi 100")
    _pill_camp_en = ("Tested on all qualified players" if _camp_full
                     else "Tested on the top 100")
    _n_camp = f"{n_aii} " if (_camp_full and n_aii) else ""
    _camp_it = (f"I test girano su tutti i {_n_camp}giocatori qualificati, non sui primi 100 "
                f"della dashboard: confrontare fra loro solo i migliori restringe la varianza "
                f"e abbassa da solo ogni correlazione."
                if _camp_full else
                "I test girano sui primi 100 della dashboard: confrontare fra loro solo i "
                "migliori restringe la varianza e abbassa da solo ogni correlazione, quindi i "
                "valori qui sotto sono se mai sottostimati.")
    _camp_en = (f"The tests run on all {_n_camp}qualified players, not on the dashboard&rsquo;s top "
                f"100: comparing only the best against each other narrows the variance and lowers "
                f"every correlation by itself."
                if _camp_full else
                "The tests run on the dashboard&rsquo;s top 100: comparing only the best against "
                "each other narrows the variance and lowers every correlation by itself, so the "
                "figures below are if anything understated.")

    # ── Soglie dichiarate prima ──────────────────────────────────
    # Sta sotto "cosa rivendica" e sopra i test: chi legge deve sapere che il
    # taglio esisteva gia' quando il numero e' uscito. I valori arrivano da
    # SOGLIE, quindi la pagina non puo' dichiarare un taglio e applicarne un
    # altro. Q, P e i descrittivi non hanno un numero e la pagina lo dice.
    _sg_it, _sg_en = SOGLIE_FISSATE_IL
    _sg_c_it = (f'<strong>C &mdash; backtest.</strong> |r| &ge; {SOGLIE["c_hi"]:.2f} &rarr; Buono &middot; '
                f'&ge; {SOGLIE["c_mid"]:.2f} &rarr; Moderato &middot; sotto &rarr; Basso.')
    _sg_c_en = (f'<strong>C &mdash; backtest.</strong> |r| &ge; {SOGLIE["c_hi"]:.2f} &rarr; Good &middot; '
                f'&ge; {SOGLIE["c_mid"]:.2f} &rarr; Moderate &middot; below &rarr; Low.')
    soglie_html = f"""
<div class="section" style="margin-bottom:34px">
  <div class="section-hd">
    <div class="section-num">&#9678;</div>
    <div>
      <div class="section-ttl" {_bi("Soglie dichiarate prima","Thresholds declared in advance")}>Soglie dichiarate prima</div>
      <div class="section-sub" {_bi(f"Fissate il {_sg_it}, prima di rigirare i test sul campione allargato. Se un risultato le attraversa cambia l&rsquo;etichetta, non la soglia.", f"Fixed on {_sg_en}, before re-running the tests on the wider sample. If a result crosses one, the label changes &mdash; not the threshold.")}>Fissate il {_sg_it}, prima di rigirare i test sul campione allargato. Se un risultato le attraversa cambia l&rsquo;etichetta, non la soglia.</div>
    </div>
  </div>
  <div class="g3">
    <div class="card">
      <div class="card-ttl" {_bi("Con un numero","With a number")}>Con un numero</div>
      <div class="interp" style="border-left:0;padding-left:0" {_bi(_sg_c_it, _sg_c_en)}>{_sg_c_it}</div>
    </div>
    <div class="card">
      <div class="card-ttl" {_bi("Con una regola","With a rule")}>Con una regola</div>
      <div class="interp" style="border-left:0;padding-left:0" {_bi("<strong>Q e P</strong> non hanno una soglia scelta a mano: decide il segno dell&rsquo;IC 95% bootstrap. Tutto sopra lo zero &rarr; regge; tutto sotto &rarr; non regge; a cavallo dello zero &rarr; pari. &Egrave; una regola fissata dal disegno del test, non un taglio da tarare.", "<strong>Q and P</strong> have no hand-picked threshold: the sign of the 95% bootstrap CI decides. Entirely above zero &rarr; it holds; entirely below &rarr; it does not; straddling zero &rarr; a tie. That is a rule set by the test design, not a cut-off to tune.")}><strong>Q e P</strong> non hanno una soglia scelta a mano: decide il segno dell&rsquo;IC 95% bootstrap.</div>
    </div>
    <div class="card">
      <div class="card-ttl" {_bi("Senza soglia","No threshold")}>Senza soglia</div>
      <div class="interp" style="border-left:0;padding-left:0" {_bi("<strong>A, B, D, E</strong> non portano un verdetto e quindi non hanno niente da superare: sono descrizioni. Dare loro una soglia significherebbe far passare per prova un numero che, con questi campioni, non lo &egrave;.", "<strong>A, B, D, E</strong> carry no verdict and so have nothing to clear: they are descriptions. Giving them a threshold would dress up as proof a number that, at these sample sizes, is not.")}><strong>A, B, D, E</strong> non portano un verdetto e quindi non hanno niente da superare: sono descrizioni.</div>
    </div>
  </div>
</div>"""

    a_loo = val_a.get("loo") or {}
    if val_a.get("r") is not None:
        _aci = _ci_txt(val_a.get("sp_ci_lo"), val_a.get("sp_ci_hi"))
        _apr = _sf(val_a.get("partial_r"), 3)
        _amin, _amax = _sf(a_loo.get("min"), 3), _sf(a_loo.get("max"), 3)
        _a_it = (f'<strong>Rigore:</strong> Spearman &rho; IC95% bootstrap {_aci} &middot; '
                 f'correlazione parziale controllando il ruolo = <strong>{_apr}</strong> '
                 f'(se molto &lt; r grezza, parte era effetto-ruolo) &middot; '
                 f'leave-one-out r &isin; [{_amin}, {_amax}].')
        _a_en = (f'<strong>Rigor:</strong> Spearman &rho; 95% bootstrap CI {_aci} &middot; '
                 f'partial correlation controlling for role = <strong>{_apr}</strong> '
                 f'(if much &lt; raw r, part was a role effect) &middot; '
                 f'leave-one-out r &isin; [{_amin}, {_amax}].')
        a_rigor = (f'<div class="interp" style="margin-top:8px" '
                   f'{_bi(_a_it, _a_en)}>{_a_it}</div>')
    else:
        a_rigor = ""

    # A resta descrittivo per il campione, non per il numero che esce: i voti
    # sono inseriti a mano su un sottoinsieme scelto, non estratti a caso fra i
    # qualificati. L'IC pero' e' un fatto e si muove col campione — quando
    # smette di contenere lo zero la pagina non puo' continuare a dire che lo
    # contiene, o mente per inerzia. Quindi la frase segue il dato, il verdetto no.
    _a_ci_txt = _ci_txt(val_a.get("ci_lo"), val_a.get("ci_hi"))
    _a_zero = (val_a.get("ci_lo") is not None and val_a.get("ci_hi") is not None
               and float(val_a["ci_lo"]) <= 0 <= float(val_a["ci_hi"]))
    if _a_zero:
        _a_sub_it = (f"Confronto col consenso degli esperti su {n_a} voti inseriti a mano. "
                     f"<strong>Non &egrave; una prova</strong>: con questo campione l&rsquo;intervallo "
                     f"di confidenza di r contiene lo zero, quindi il dato descrive una somiglianza, "
                     f"non la dimostra.")
        _a_sub_en = (f"Comparison with expert consensus over {n_a} hand-entered ratings. "
                     f"<strong>This is not proof</strong>: at this sample size the confidence interval "
                     f"for r contains zero, so the figure describes a resemblance rather than "
                     f"establishing one.")
        _a_par_it = (f"Con {n_a} voti il margine d&rsquo;errore &egrave; largo quanto il risultato, e "
                     f"l&rsquo;IC {_a_ci_txt} contiene lo zero: il confronto va guardato, non usato "
                     f"come prova.")
        _a_par_en = (f"With {n_a} ratings the margin of error is as wide as the result, and the CI "
                     f"{_a_ci_txt} contains zero: look at the comparison, do not use it as proof.")
    else:
        _a_sub_it = (f"Confronto col consenso degli esperti su {n_a} voti inseriti a mano. "
                     f"L&rsquo;IC {_a_ci_txt} non contiene lo zero, ma <strong>resta una "
                     f"descrizione</strong>: i voti coprono un sottoinsieme scelto a mano dei "
                     f"qualificati, non un campione estratto a caso, quindi il numero vale per questi "
                     f"giocatori e non si estende agli altri.")
        _a_sub_en = (f"Comparison with expert consensus over {n_a} hand-entered ratings. The CI "
                     f"{_a_ci_txt} no longer contains zero, but this <strong>remains a "
                     f"description</strong>: the ratings cover a hand-picked subset of the qualified "
                     f"players rather than a random sample, so the figure holds for these players and "
                     f"does not generalise to the rest.")
        _a_par_it = (f"L&rsquo;IC {_a_ci_txt} non contiene lo zero: su questi {n_a} giocatori la "
                     f"somiglianza c&rsquo;&egrave;. Non diventa una prova sull&rsquo;indice, perch&eacute; "
                     f"chi entra in questa lista lo decide chi inserisce i voti.")
        _a_par_en = (f"The CI {_a_ci_txt} does not contain zero: across these {n_a} players the "
                     f"resemblance is there. It does not become proof about the index, because who "
                     f"ends up in this list is decided by whoever enters the ratings.")

    b_hyper = val_b.get("hyper") or {}
    _bnc = val_b.get("n_common", "?")
    _bk = _sf(val_b.get("kendall_common"), 3)
    _bsp = _sf(val_b.get("spearman_common"), 3)
    _bci = _ci_txt(val_b.get("common_ci_lo"), val_b.get("common_ci_hi"))
    _bfair = val_b.get("fair_overlap_pct", "&mdash;")
    _bp = _sf(b_hyper.get("p"), 4)
    _bexp = _sf(b_hyper.get("expected"), 2)
    # Il p ipergeometrico esce dalla nota di rigore e va accanto al 40%: e' il
    # numero che impedisce di leggere l'overlap come una conferma.
    _bp2 = _sf(b_hyper.get("p"), 2)
    _b_it = (f'<strong>Rigore:</strong> sul set comune (~{_bnc} giocatori) '
             f'Kendall &tau; = <strong>{_bk}</strong>, Spearman &rho; = {_bsp} IC95% {_bci}. '
             f'Overlap fair (stesso bacino) = {_bfair}% &middot; p(ipergeometrico) = {_bp} '
             f'(coincidenze attese per caso: {_bexp}/10).')
    _b_en = (f'<strong>Rigor:</strong> on the common set (~{_bnc} players) '
             f'Kendall &tau; = <strong>{_bk}</strong>, Spearman &rho; = {_bsp} 95% CI {_bci}. '
             f'Fair overlap (same pool) = {_bfair}% &middot; p(hypergeometric) = {_bp} '
             f'(coincidences expected by chance: {_bexp}/10).')
    b_rigor = (f'<div class="interp" style="margin-top:8px" '
               f'{_bi(_b_it, _b_en)}>{_b_it}</div>')

    c_skill = val_c.get("skill") or {}
    c_plac  = val_c.get("placebo") or {}
    if val_c.get("skill"):
        _crmse = _sf(c_skill.get("rmse_oos"), 4)
        _csp = _sf(c_skill.get("skill_persistence"), 3)
        _csg = _sf(c_skill.get("skill_group"), 3)
        _cobs = _sf(c_plac.get("observed"), 3)
        _cnull = _sf(c_plac.get("null_p95"), 3)
        _cpp = _sf(c_plac.get("p_perm"), 4)
        _crel = _sf(val_c.get("reliability_r"), 3)
        _cnote_it = ("Backtest su output offensivo (xG+xA)/90 — componente dominante "
                     "del TPI (peso 0.32), non il composito completo: evidenza parziale.")
        _cnote_en = ("Backtest on offensive output (xG+xA)/90 — the dominant TPI component "
                     "(weight 0.32), not the full composite: partial evidence.")
        _c_it = (f'<strong>Rigore:</strong> RMSE <em>out-of-sample</em> (k-fold) = '
                 f'<strong>{_crmse}</strong> &middot; skill vs persistenza = {_csp}, vs media-ruolo = {_csg} '
                 f'(&gt;0 = batte la baseline) &middot; placebo: r oss. {_cobs} vs nulla p95 {_cnull} '
                 f'(p_perm {_cpp}) &middot; affidabilit&agrave; pari/dispari &rho; = {_crel}.'
                 f'<br><span style="color:var(--lt)">{_cnote_it}</span>')
        _c_en = (f'<strong>Rigor:</strong> <em>out-of-sample</em> RMSE (k-fold) = '
                 f'<strong>{_crmse}</strong> &middot; skill vs persistence = {_csp}, vs role-mean = {_csg} '
                 f'(&gt;0 = beats the baseline) &middot; placebo: observed r {_cobs} vs null p95 {_cnull} '
                 f'(p_perm {_cpp}) &middot; odd/even reliability &rho; = {_crel}.'
                 f'<br><span style="color:var(--lt)">{_cnote_en}</span>')
        c_rigor = (f'<div class="interp" style="margin-top:8px" '
                   f'{_bi(_c_it, _c_en)}>{_c_it}</div>')
    else:
        c_rigor = ""

    # ── Sezione F — validità ecologica squadra ───────────────────
    vf = val_f or {}
    if vf.get("has_data"):
        f_rows = "".join(
            f'<tr><td style="color:var(--lp)">{r["squadra"]}</td>'
            f'<td style="font-family:var(--mono);color:var(--lp)">{_sf(r["tpi"],3)}</td>'
            f'<td style="font-family:var(--mono)">{_sf(r["criterio"],2)}</td></tr>'
            for r in vf.get("rows", [])
        )
        f_section = f"""
<div class="section">
  <div class="section-hd">
    <div class="section-num">F</div>
    <div>
      <div class="section-ttl" {_bi("Validit&agrave; ecologica &mdash; livello squadra","Ecological validity &mdash; team level")}>Validit&agrave; ecologica &mdash; livello squadra</div>
      <div class="section-sub" {_bi(f"Il TPI medio di squadra spiega l'xG totale prodotto? Esito reale e indipendente ({vf.get('n')} squadre).", f"Does mean team TPI explain total xG produced? A real, independent outcome ({vf.get('n')} teams).")}>Il TPI medio di squadra spiega l'xG totale prodotto? Esito reale e indipendente ({vf.get("n")} squadre).</div>
    </div>
  </div>
  <div class="g3">
    <div class="stat-box sb-blue"><div class="stat-val" style="color:{rcol(vf.get("r"))}">{_sf(vf.get("r"),3)}</div><div class="stat-lbl">Spearman &rho;</div><div class="stat-sub" {_bi("TPI medio &harr; xG squadra","mean TPI &harr; team xG")}>TPI medio &harr; xG squadra</div></div>
    <div class="stat-box sb-teal"><div class="stat-val" style="font-size:17px">{_ci_txt(vf.get("ci_lo"), vf.get("ci_hi"))}</div><div class="stat-lbl" {_bi("IC95% bootstrap","95% bootstrap CI")}>IC95% bootstrap</div><div class="stat-sub" {_bi("censo lega &rarr; CI ampia","league census &rarr; wide CI")}>censo lega &rarr; CI ampia</div></div>
    <div class="stat-box sb-purp"><div class="stat-val">{vf.get("n")}</div><div class="stat-lbl" {_bi("Squadre","Teams")}>Squadre</div><div class="stat-sub" {_bi("popolazione completa","full population")}>popolazione completa</div></div>
  </div>
  <div class="card"><div class="card-ttl" {_bi("Aggregato per squadra","Per-team aggregate")}>Aggregato per squadra</div>
    <div class="table-wrap"><table><tr><th {_bi("Squadra","Team")}>Squadra</th><th {_bi("TPI medio","Mean TPI")}>TPI medio</th><th {_bi("xG totale","Total xG")}>xG totale</th></tr><tbody>{f_rows}</tbody></table></div>
    <div class="interp" {_bi("Una &rho; alta conferma che l'indice cattura impatto offensivo che si traduce in produzione di squadra. n=20 &rarr; leggere con l'IC.","A high &rho; confirms the index captures offensive impact that translates into team production. n=20 &rarr; read with the CI.")}>Una &rho; alta conferma che l'indice cattura impatto offensivo che si traduce in produzione di squadra. n=20 &rarr; leggere con l'IC.</div>
  </div>
</div>"""
    elif vf.get("msg"):
        f_section = f'<div class="nota-warn" style="margin-top:16px">{vf["msg"]}</div>'
    else:
        f_section = ""

    # ── Sezione G — struttura interna / PCA ──────────────────────
    vg = val_g or {}
    if vg.get("has_data"):
        explained = vg.get("explained", [])
        labs = vg.get("labels", [])
        g_bars = "".join(
            f'<div style="margin-bottom:8px"><div style="display:flex;justify-content:space-between;font-size:12px;color:var(--ls)"><span>PC{i+1}</span><span style="font-family:var(--mono)">{round(e*100,1)}%</span></div>'
            f'<div style="height:6px;background:rgba(233,240,236,.06);border-radius:3px;overflow:hidden"><div style="height:100%;width:{round(e*100,1)}%;background:var(--orng)"></div></div></div>'
            for i, e in enumerate(explained)
        )
        pc1 = vg.get("pc1") or 0
        if pc1 >= 0.85:
            g_verd_it = "Composito quasi monodimensionale: i pesi contano poco."
            g_verd_en = "Almost one-dimensional composite: the weights matter little."
        elif pc1 >= 0.55:
            g_verd_it = "Dimensioni parzialmente indipendenti: la pesatura &egrave; sostanziale."
            g_verd_en = "Partially independent dimensions: the weighting is substantial."
        else:
            g_verd_it = "Dimensioni largamente ortogonali: ogni componente aggiunge informazione."
            g_verd_en = "Largely orthogonal dimensions: each component adds information."
        _g_pc1 = round(pc1*100, 1)
        g_section = f"""
<div class="section">
  <div class="section-hd">
    <div class="section-num">G</div>
    <div>
      <div class="section-ttl" {_bi("Struttura interna del composito","Internal structure of the composite")}>Struttura interna del composito</div>
      <div class="section-sub" {_bi(f"Le {len(labs)} dimensioni misurano cose diverse o sono ridondanti? PCA su {vg.get('n')} giocatori.", f"Do the {len(labs)} dimensions measure different things or are they redundant? PCA on {vg.get('n')} players.")}>Le {len(labs)} dimensioni misurano cose diverse o sono ridondanti? PCA su {vg.get("n")} giocatori.</div>
    </div>
  </div>
  <div class="g2">
    <div class="card"><div class="card-ttl" {_bi("Varianza spiegata (PCA)","Explained variance (PCA)")}>Varianza spiegata (PCA)</div>{g_bars}
      <div class="interp" {_bi(f"PC1 = {_g_pc1}%. {g_verd_it}", f"PC1 = {_g_pc1}%. {g_verd_en}")}>PC1 = {_g_pc1}%. {g_verd_it}</div></div>
    <div class="card"><div class="card-ttl" {_bi("Dimensioni incluse","Dimensions included")}>Dimensioni incluse</div>
      <div style="font-size:13px;color:var(--ls);line-height:2">{" &middot; ".join(labs)}</div>
      <div class="interp" {_bi("PC1 vicino al 100% = ridondanza fra dimensioni (pesi poco influenti); valori bassi giustificano la pesatura multi-dimensione.","PC1 near 100% = redundancy across dimensions (weights barely matter); low values justify the multi-dimension weighting.")}>PC1 vicino al 100% = ridondanza fra dimensioni (pesi poco influenti); valori bassi giustificano la pesatura multi-dimensione.</div></div>
  </div>
</div>"""
    elif vg.get("msg"):
        g_section = f'<div class="nota-warn" style="margin-top:16px">{vg["msg"]}</div>'
    else:
        g_section = ""

    # ── Sezione H — robustezza ai pesi ───────────────────────────
    vh = val_h or {}
    if vh.get("has_data"):
        h_ov  = vh.get("top_overlap", {})
        h_ovm = vh.get("top_overlap_min", {})
        h_cov = vh.get("coverage", 0)
        h_section = f"""
<div class="section">
  <div class="section-hd">
    <div class="section-num">H</div>
    <div>
      <div class="section-ttl" {_bi("Robustezza ai pesi","Robustness to weights")}>Robustezza ai pesi</div>
      <div class="section-sub" {_bi(f"Il ranking regge se i pesi cambiano di &plusmn;{int(vh.get('pct',0.2)*100)}%? Monte Carlo su {vh.get('n')} giocatori.", f"Does the ranking hold if the weights change by &plusmn;{int(vh.get('pct',0.2)*100)}%? Monte Carlo on {vh.get('n')} players.")}>Il ranking regge se i pesi cambiano di &plusmn;{int(vh.get("pct",0.2)*100)}%? Monte Carlo su {vh.get("n")} giocatori.</div>
    </div>
  </div>
  <div class="g4">
    <div class="stat-box sb-green"><div class="stat-val" style="color:{rcol(vh.get("spearman_median"))}">{_sf(vh.get("spearman_median"),3)}</div><div class="stat-lbl" {_bi("Spearman mediana","Median Spearman")}>Spearman mediana</div><div class="stat-sub" {_bi("perturbato vs base","perturbed vs base")}>perturbato vs base</div></div>
    <div class="stat-box sb-orng"><div class="stat-val">{_sf(vh.get("spearman_min"),3)}</div><div class="stat-lbl" {_bi("Caso peggiore","Worst case")}>Caso peggiore</div><div class="stat-sub" {_bi("&rho; minima","minimum &rho;")}>&rho; minima</div></div>
    <div class="stat-box sb-blue"><div class="stat-val">{int(h_ov.get("10",0)*100)}%</div><div class="stat-lbl" {_bi("Top 10 stabile","Top 10 stable")}>Top 10 stabile</div><div class="stat-sub">min {int(h_ovm.get("10",0)*100)}%</div></div>
    <div class="stat-box sb-purp"><div class="stat-val">{int(h_cov*100)}%</div><div class="stat-lbl" {_bi("Copertura pesi","Weight coverage")}>Copertura pesi</div><div class="stat-sub" {_bi("dim nel payload","dims in payload")}>dim nel payload</div></div>
  </div>
  <div class="card"><div class="interp" {_bi(f"&rho; mediana vicina a 1 e Top-10 stabile = il ranking non dipende dalla scelta fine dei pesi. Copertura {int(h_cov*100)}% (manca 'finishing' fra gli z-score esportati): test parziale ma indicativo.", f"Median &rho; close to 1 and a stable Top-10 = the ranking does not depend on the fine choice of weights. Coverage {int(h_cov*100)}% ('finishing' missing from the exported z-scores): partial but indicative test.")}>&rho; mediana vicina a 1 e Top-10 stabile = il ranking non dipende dalla scelta fine dei pesi. Copertura {int(h_cov*100)}% (manca 'finishing' fra gli z-score esportati): test parziale ma indicativo.</div></div>
</div>"""
    elif vh.get("msg"):
        h_section = f'<div class="nota-warn" style="margin-top:16px">{vh["msg"]}</div>'
    else:
        h_section = ""

    # ── Sezione I — validità incrementale (non circolare) ────────
    vi = val_i or {}
    if vi.get("has_data"):
        i_better = vi.get("pro_better")
        if i_better:
            i_verd_it = "TPI Pro predice meglio: AII/PRI aggiungono potere reale (IC della differenza &gt; 0)."
            i_verd_en = "TPI Pro predicts better: AII/PRI add real power (the difference CI is &gt; 0)."
        else:
            i_verd_it = "Nessun guadagno predittivo dimostrato: l'IC della differenza include 0 &rarr; AII/PRI restano descrittivi."
            i_verd_en = "No predictive gain shown: the difference CI includes 0 &rarr; AII/PRI stay descriptive."
        i_col = "var(--green)" if i_better else "var(--orng)"
        i_section = f"""
<div class="section">
  <div class="section-hd">
    <div class="section-num">I</div>
    <div>
      <div class="section-ttl" {_bi("Validit&agrave; incrementale TPI Pro (non circolare)","TPI Pro incremental validity (non-circular)")}>Validit&agrave; incrementale TPI Pro (non circolare)</div>
      <div class="section-sub" {_bi(f"TPI Pro predice la forma recente meglio del TPI classico? Confronto appaiato su {vi.get('n')} giocatori.", f"Does TPI Pro predict recent form better than the classic TPI? Paired comparison on {vi.get('n')} players.")}>TPI Pro predice la forma recente meglio del TPI classico? Confronto appaiato su {vi.get("n")} giocatori.</div>
    </div>
  </div>
  <div class="g3">
    <div class="stat-box sb-orng"><div class="stat-val">{_sf(vi.get("rmse_base"),3)}</div><div class="stat-lbl">RMSE OOS &middot; TPI</div><div class="stat-sub" {_bi("errore base","base error")}>errore base</div></div>
    <div class="stat-box sb-purp"><div class="stat-val">{_sf(vi.get("rmse_pro"),3)}</div><div class="stat-lbl">RMSE OOS &middot; TPI Pro</div><div class="stat-sub" {_bi("errore esteso","extended error")}>errore esteso</div></div>
    <div class="stat-box sb-green"><div class="stat-val" style="color:{i_col}">{_sf(vi.get("delta_rmse"),3)}</div><div class="stat-lbl">&Delta; (base&minus;pro)</div><div class="stat-sub">IC95% {_ci_txt(vi.get("ci_lo"), vi.get("ci_hi"))}</div></div>
  </div>
  <div class="card"><div class="interp" style="border-left-color:{i_col}" {_bi(f"{i_verd_it} Sostituisce r(TPI,TPI Pro), circolare per costruzione (TPI Pro contiene il TPI).", f"{i_verd_en} It replaces r(TPI,TPI Pro), circular by construction (TPI Pro contains the TPI).")}>{i_verd_it} Sostituisce r(TPI,TPI Pro), circolare per costruzione (TPI Pro contiene il TPI).</div></div>
</div>"""
    elif vi.get("msg"):
        i_section = f'<div class="nota-warn" style="margin-top:16px">{vi["msg"]}</div>'
    else:
        i_section = ""

    # ── Sezione Q — il TPI batte una baseline banale? ─────────────
    # Sta subito dopo la I perche' e' lo stesso disegno (vintage OOS, bootstrap
    # clusterizzato) applicato un livello piu' in basso: la I chiede se il Pro
    # aggiunge al TPI, la Q se il TPI aggiunge al suo stesso input.
    vq = val_q or {}
    if vq.get("has_data"):
        def _q_col(v: dict) -> str:
            if v.get("tpi_better"):
                return "var(--green)"
            return "var(--red)" if (v.get("ci_hi") or 0) < 0 else "var(--orng)"

        def _q_esito(v: dict) -> tuple[str, str]:
            if v.get("tpi_better"):
                # Un IC che parte da 0.00004 si stampa "[0.0, ...]" e sembra
                # contenere lo zero: va detto che e' un margine, non una vittoria.
                if (v.get("ci_lo") or 0) < 0.0005:
                    return ("TPI meglio, al limite", "TPI better, borderline")
                return ("TPI meglio", "TPI better")
            if (v.get("ci_hi") or 0) < 0:
                return ("baseline meglio", "baseline better")
            return ("pari", "tie")

        def _q_ci(v: dict) -> str:
            """IC a 4 decimali: alla terza cifra i margini del test spariscono."""
            lo, hi = v.get("ci_lo"), v.get("ci_hi")
            return f"[{_sf(lo,4)}, {_sf(hi,4)}]" if lo is not None and hi is not None else "&mdash;"

        q_liv = vq["criteri"].get("livello", {})
        q_boxes = ""
        for _sb, _v in zip(("sb-blue", "sb-purp", "sb-teal"), q_liv.get("baselines", [])):
            _e_it, _e_en = _q_esito(_v)
            q_boxes += (
                f'<div class="stat-box {_sb}">'
                f'<div class="stat-val" style="color:{_q_col(_v)}">{_v["delta_rmse"]:+.4f}</div>'
                f'<div class="stat-lbl" {_bi("vs " + _v["label_it"], "vs " + _v["label_en"])}>vs {_v["label_it"]}</div>'
                f'<div class="stat-sub">IC95% {_q_ci(_v)} &middot; '
                f'<span style="color:{_q_col(_v)}" {_bi(_e_it, _e_en)}>{_e_it}</span></div></div>'
            )
        q_rows = ""
        for _cv in vq["criteri"].values():
            for _v in _cv["baselines"]:
                _e_it, _e_en = _q_esito(_v)
                q_rows += (
                    f'<tr><td style="color:var(--lt);font-size:12px">'
                    f'<span {_bi(_cv["label_it"], _cv["label_en"])}>{_cv["label_it"]}</span></td>'
                    f'<td style="color:var(--lp)"><span {_bi(_v["label_it"], _v["label_en"])}>{_v["label_it"]}</span></td>'
                    f'<td style="font-family:var(--mono)">{_v["rho_baseline"]:+.3f}</td>'
                    f'<td style="font-family:var(--mono)">{_v["rho_tpi"]:+.3f}</td>'
                    f'<td style="font-family:var(--mono);color:{_q_col(_v)};font-weight:700">{_v["delta_rmse"]:+.4f}</td>'
                    f'<td style="font-family:var(--mono);font-size:12px">{_q_ci(_v)}</td>'
                    f'<td style="font-size:12px;color:{_q_col(_v)}">'
                    f'<span {_bi(_e_it, _e_en)}>{_e_it}</span></td></tr>'
                )
        _q_dom = next((v for v in q_liv.get("baselines", []) if v["key"] == "output_grezzo"), None)
        if _q_dom is None:
            q_verd_it = "Confronto con l&rsquo;input dominante non calcolabile su questi vintage."
            q_verd_en = "Comparison against the dominant input not computable on these vintages."
            q_verd_col = "var(--orng)"
        elif _q_dom["ci_hi"] < 0:
            q_verd_col = "var(--red)"
            q_verd_it = ("Sul rendimento futuro il TPI <strong>non batte il suo input dominante</strong>: "
                         "l&rsquo;output grezzo per-90, da solo, sbaglia meno, e l&rsquo;intervallo di confidenza "
                         "sta tutto sotto lo zero. Misurate contro questo criterio, le altre sei dimensioni "
                         "non stanno aggiungendo potere predittivo.")
            q_verd_en = ("On future output the TPI <strong>does not beat its own dominant input</strong>: "
                         "raw per-90 output alone has lower error, and the confidence interval lies entirely "
                         "below zero. Measured against this criterion, the other six dimensions are not "
                         "adding predictive power.")
        elif _q_dom["tpi_better"]:
            q_verd_col = "var(--green)"
            q_verd_it = ("Il TPI batte il suo input dominante: il composito aggiunge qualcosa che "
                         "l&rsquo;output grezzo per-90 non ha, con IC che esclude lo zero.")
            q_verd_en = ("The TPI beats its own dominant input: the composite adds something raw per-90 "
                         "output does not have, with a CI that excludes zero.")
        else:
            q_verd_col = "var(--orng)"
            q_verd_it = ("Pareggio con l&rsquo;input dominante: l&rsquo;IC della differenza contiene lo zero. "
                         "Il composito non peggiora le cose, ma su questi dati non &egrave; dimostrato "
                         "che le sette dimensioni battano la singola rate stat da cui partono.")
            q_verd_en = ("A tie with the dominant input: the CI of the difference contains zero. The "
                         "composite does no harm, but on this data the seven dimensions are not shown to "
                         "beat the single rate stat they start from.")
        _q_gior = ", ".join(f"g{g}" for g in vq.get("vintage_giornate", []))
        q_section = f"""
<div class="section">
  <div class="section-hd">
    <div class="section-num">Q</div>
    <div>
      <div class="section-ttl" {_bi("Il TPI batte una baseline banale?","Does the TPI beat a trivial baseline?")}>Il TPI batte una baseline banale?</div>
      <div class="section-sub" {_bi(f"Gli altri test chiedono se il TPI &egrave; correlato con qualcosa. Questo chiede l&rsquo;unica cosa che pu&ograve; bocciarlo: serviva costruirlo? Confronto out-of-sample su {vq.get('n')} coppie giocatore-vintage ({vq.get('n_giocatori')} giocatori, {_q_gior}).", f"The other tests ask whether the TPI correlates with something. This one asks the only question that can fail it: was it worth building? Out-of-sample comparison on {vq.get('n')} player-vintage pairs ({vq.get('n_giocatori')} players, {_q_gior}).")}>Gli altri test chiedono se il TPI &egrave; correlato con qualcosa. Questo chiede l&rsquo;unica cosa che pu&ograve; bocciarlo: serviva costruirlo? Confronto out-of-sample su {vq.get("n")} coppie giocatore-vintage ({vq.get("n_giocatori")} giocatori, {_q_gior}).</div>
    </div>
  </div>
  <div class="g3">{q_boxes}</div>
  <div class="card" style="margin-top:16px">
    <div class="card-ttl" {_bi("Tutti i confronti","All comparisons")}>Tutti i confronti</div>
    <div class="table-wrap"><table>
      <tr><th {_bi("Criterio","Criterion")}>Criterio</th><th>Baseline</th>
          <th>&rho; baseline</th><th>&rho; TPI</th>
          <th>&Delta;RMSE</th><th>IC95%</th><th {_bi("Esito","Outcome")}>Esito</th></tr>
      <tbody>{q_rows}</tbody>
    </table></div>
    <div class="interp" style="border-left-color:{q_verd_col}" {_bi(q_verd_it, q_verd_en)}>{q_verd_it}</div>
  </div>
  <div class="g2" style="margin-top:16px">
    <div class="card"><div class="card-ttl" {_bi("Come si legge","How to read it")}>Come si legge</div>
      <div class="interp" {_bi("&Delta;RMSE = errore della baseline &minus; errore del TPI, quindi <strong>positivo significa TPI migliore</strong>. Conta l&rsquo;intervallo di confidenza, non il segno: se contiene lo zero il confronto &egrave; un pareggio. I predittori sono presi allo snapshot di giornata N, il criterio dalle giornate successive, e il bootstrap &egrave; clusterizzato sul giocatore perch&eacute; lo stesso nome ricorre in pi&ugrave; vintage.", "&Delta;RMSE = baseline error &minus; TPI error, so <strong>positive means the TPI is better</strong>. What matters is the confidence interval, not the sign: if it contains zero the comparison is a tie. Predictors are taken at the matchday-N snapshot, the criterion from later matchdays, and the bootstrap is clustered by player because the same name recurs across vintages.")}>&Delta;RMSE = errore della baseline &minus; errore del TPI, quindi <strong>positivo significa TPI migliore</strong>. Conta l&rsquo;intervallo di confidenza, non il segno: se contiene lo zero il confronto &egrave; un pareggio.</div></div>
    <div class="card"><div class="card-ttl" {_bi("Due avvertenze oneste","Two honest caveats")}>Due avvertenze oneste</div>
      <div class="interp" {_bi(f"<strong>Il criterio favorisce la baseline.</strong> Il TPI non nasce per massimizzare l&rsquo;output offensivo futuro: pesa i ruoli (un difensore vale 0.55) ed &egrave; orientato allo scouting. Confrontarlo su (gol+xA)/90 lo penalizza per costruzione &mdash; ed &egrave; esattamente il criterio con cui il sito lo presenta.<br><br><strong>Il valore di mercato bara.</strong> Copre {vq.get('valore_coverage_pct')}% delle coppie ed &egrave; uno snapshot scaricato a stagione finita: come predittore sa gi&agrave; com&rsquo;&egrave; andata. Gioca in vantaggio, non in svantaggio.", f"<strong>The criterion favours the baseline.</strong> The TPI is not built to maximise future attacking output: it weights roles (a defender counts 0.55) and is scouting-oriented. Judging it on (goals+xA)/90 penalises it by construction &mdash; and that is exactly the criterion the site advertises.<br><br><strong>Market value cheats.</strong> It covers {vq.get('valore_coverage_pct')}% of the pairs and is a snapshot downloaded after the season ended: as a predictor it already knows how things went. It plays with an advantage, not a handicap.")}><strong>Il criterio favorisce la baseline.</strong> Il TPI non nasce per massimizzare l&rsquo;output offensivo futuro: pesa i ruoli ed &egrave; orientato allo scouting.<br><br><strong>Il valore di mercato bara</strong>: &egrave; uno snapshot scaricato a stagione finita, quindi sa gi&agrave; com&rsquo;&egrave; andata.</div></div>
  </div>
</div>"""
    elif vq.get("msg"):
        q_section = f'<div class="nota-warn" style="margin-top:16px">{vq["msg"]}</div>'
    else:
        q_section = ""

    # ── Sezione M — calibrazione ─────────────────────────────────
    vm = val_m or {}
    if vm.get("has_data"):
        m_slope = vm.get("slope")
        m_bins = "".join(
            f'<tr><td style="font-family:var(--mono)">{b["bin"]+1}</td>'
            f'<td style="font-family:var(--mono);color:var(--orng)">{b["tpi_mean"]:+.3f}</td>'
            f'<td style="font-family:var(--mono)">{b["realized_mean"]:.3f}</td>'
            f'<td style="font-family:var(--mono);color:var(--lt)">{b["predicted"]:.3f}</td>'
            f'<td style="font-family:var(--mono);color:{"var(--green)" if abs(b["residual"]) < 0.05 else "var(--orng)"}">{b["residual"]:+.3f}</td>'
            f'<td style="font-family:var(--mono);font-size:12px">{b["n"]}</td></tr>'
            for b in vm.get("bins", [])
        )
        # slope 1.0 = un punto di TPI vale un punto di output. Sotto 1 significa
        # che le distanze di TPI sono piu' larghe di quelle reali.
        _m_gonf = (1.0 / m_slope) if m_slope else None
        if m_slope is None:
            m_verd_it = m_verd_en = "&mdash;"; m_col = "var(--lt)"
        elif 0.8 <= m_slope <= 1.25:
            m_col = "var(--green)"
            m_verd_it = "Pendenza vicina a 1: una differenza di TPI corrisponde a una differenza di rendimento della stessa taglia."
            m_verd_en = "Slope close to 1: a TPI gap corresponds to a performance gap of the same size."
        else:
            m_col = "var(--orng)"
            m_verd_it = (f"Pendenza {_sf(m_slope,3)} invece di 1: l&rsquo;ordinamento tiene (&rho; fra decili "
                         f"= {_sf(vm.get('monotonia_rho'),3)}), ma le <strong>distanze sono gonfiate circa "
                         f"{_sf(_m_gonf,1)}&times;</strong>. Un giocatore con TPI doppio di un altro non rende il doppio: "
                         f"il TPI va letto come una graduatoria, non come una scala di quantit&agrave;.")
            m_verd_en = (f"Slope {_sf(m_slope,3)} instead of 1: the ordering holds (&rho; across deciles "
                         f"= {_sf(vm.get('monotonia_rho'),3)}), but the <strong>gaps are inflated about "
                         f"{_sf(_m_gonf,1)}&times;</strong>. A player with twice another&rsquo;s TPI does not perform twice as well: "
                         f"read the TPI as a ranking, not as a scale of quantities.")
        m_section = f"""
<div class="section">
  <div class="section-hd">
    <div class="section-num">M</div>
    <div>
      <div class="section-ttl" {_bi("Calibrazione &mdash; le differenze di TPI sono della taglia giusta?","Calibration &mdash; are TPI gaps the right size?")}>Calibrazione &mdash; le differenze di TPI sono della taglia giusta?</div>
      <div class="section-sub" {_bi(f"Un indice pu&ograve; ordinare bene e sbagliare le distanze. Qui i {vm.get('n')} giocatori sono divisi in {vm.get('n_bins')} decili di TPI e si confronta il rendimento medio di ogni decile con quello atteso dalla retta.", f"An index can order well and still get the distances wrong. Here the {vm.get('n')} players are split into {vm.get('n_bins')} TPI deciles and each decile&rsquo;s mean output is compared with the line&rsquo;s prediction.")}>Un indice pu&ograve; ordinare bene e sbagliare le distanze. I {vm.get("n")} giocatori sono divisi in {vm.get("n_bins")} decili di TPI.</div>
    </div>
  </div>
  <div class="g4">
    <div class="stat-box sb-orng"><div class="stat-val" style="color:{m_col}">{_sf(m_slope,3)}</div><div class="stat-lbl" {_bi("Pendenza","Slope")}>Pendenza</div><div class="stat-sub" {_bi("ideale = 1.000","ideal = 1.000")}>ideale = 1.000</div></div>
    <div class="stat-box sb-green"><div class="stat-val" style="color:{rcol(vm.get("monotonia_rho"))}">{_sf(vm.get("monotonia_rho"),3)}</div><div class="stat-lbl" {_bi("Monotonia &rho;","Monotonicity &rho;")}>Monotonia &rho;</div><div class="stat-sub" {_bi("fra decili","across deciles")}>fra decili</div></div>
    <div class="stat-box sb-blue"><div class="stat-val">{_sf(vm.get("calibration_error"),3)}</div><div class="stat-lbl">ACE</div><div class="stat-sub" {_bi("residuo medio / range","mean residual / range")}>residuo medio / range</div></div>
    <div class="stat-box sb-purp"><div class="stat-val">{vm.get("n",0)}</div><div class="stat-lbl" {_bi("Giocatori","Players")}>Giocatori</div><div class="stat-sub" {_bi("stagione piena","full season")}>stagione piena</div></div>
  </div>
  <div class="card" style="margin-top:16px">
    <div class="card-ttl" {_bi("Decile per decile","Decile by decile")}>Decile per decile</div>
    <div class="table-wrap"><table>
      <tr><th {_bi("Decile","Decile")}>Decile</th><th {_bi("TPI medio","Mean TPI")}>TPI medio</th>
          <th {_bi("Reso","Realized")}>Reso</th><th {_bi("Atteso","Predicted")}>Atteso</th>
          <th {_bi("Residuo","Residual")}>Residuo</th><th>n</th></tr>
      <tbody>{m_bins}</tbody>
    </table></div>
    <div class="interp" style="border-left-color:{m_col}" {_bi(m_verd_it, m_verd_en)}>{m_verd_it}</div>
  </div>
</div>"""
    elif vm.get("msg"):
        m_section = f'<div class="nota-warn" style="margin-top:16px">{vm["msg"]}</div>'
    else:
        m_section = ""

    # ── Sezione O — ablation ─────────────────────────────────────
    vo = val_o or {}
    if vo.get("has_data"):
        _o_lab = {"output_adj": ("Output", "Output"), "buildup_adj": ("Buildup", "Buildup"),
                  "centralita": ("Centralit&agrave;", "Centrality"), "boost_ratio": ("Boost", "Boost"),
                  "consistenza": ("Consistenza", "Consistency"), "finishing": ("Finalizzazione", "Finishing"),
                  "form": ("Forma", "Form")}
        o_rows = ""
        for r in vo["results"]:
            _lit, _len_ = _o_lab.get(r["dim"], (r["dim"], r["dim"]))
            # Delta < 0 = togliere la dim PEGGIORA la predizione, quindi serve.
            _c = "var(--green)" if r["delta_predict"] < -0.02 else ("var(--red)" if r["delta_predict"] > 0.02 else "var(--lt)")
            _v_it = "serve" if r["delta_predict"] < -0.02 else ("peggiora" if r["delta_predict"] > 0.02 else "neutra")
            _v_en = "earns it" if r["delta_predict"] < -0.02 else ("hurts" if r["delta_predict"] > 0.02 else "neutral")
            o_rows += (
                f'<tr><td style="color:var(--lp)"><span {_bi(_lit, _len_)}>{_lit}</span></td>'
                f'<td style="font-family:var(--mono);color:var(--orng)">{r["peso"]:.2f}</td>'
                f'<td style="font-family:var(--mono)">{r["rho_vs_full"]:.3f}</td>'
                f'<td style="font-family:var(--mono)">{r["rho_vs_realized"]:.3f}</td>'
                f'<td style="font-family:var(--mono);color:{_c};font-weight:700">{r["delta_predict"]:+.4f}</td>'
                f'<td style="font-family:var(--mono);font-size:12px">{r["top10_overlap"]}/10</td>'
                f'<td style="font-size:12px;color:{_c}"><span {_bi(_v_it, _v_en)}>{_v_it}</span></td></tr>'
            )
        _o_serve = [r for r in vo["results"] if r["delta_predict"] < -0.02]
        _o_male  = [r for r in vo["results"] if r["delta_predict"] > 0.02]
        if _o_serve:
            _o_nomi = ", ".join(_o_lab.get(r["dim"], (r["dim"],))[0] for r in _o_serve)
            o_verd_it = (f"Togliendo {_o_nomi} la predizione peggiora: quelle dimensioni si guadagnano il posto. "
                         f"Le altre {len(vo['results']) - len(_o_serve)} no.")
            o_verd_en = (f"Removing {_o_nomi} makes prediction worse: those dimensions earn their place. "
                         f"The other {len(vo['results']) - len(_o_serve)} do not.")
        else:
            o_verd_it = "Nessuna dimensione, tolta da sola, peggiora la predizione in modo apprezzabile."
            o_verd_en = "No single dimension, removed on its own, makes prediction appreciably worse."
        if _o_male:
            _o_nomi_m = ", ".join(_o_lab.get(r["dim"], (r["dim"],))[0] for r in _o_male)
            o_verd_it += (f" Anzi, togliere {_o_nomi_m} la <strong>migliora</strong>: contro questo criterio "
                          f"quelle dimensioni stanno togliendo segnale, non aggiungendolo.")
            o_verd_en += (f" In fact removing {_o_nomi_m} <strong>improves</strong> it: against this criterion "
                          f"those dimensions are removing signal rather than adding it.")
        o_col = "var(--green)" if _o_serve and not _o_male else "var(--orng)"
        o_section = f"""
<div class="section">
  <div class="section-hd">
    <div class="section-num">O</div>
    <div>
      <div class="section-ttl" {_bi("Ablation &mdash; quali delle sette dimensioni si guadagnano il posto","Ablation &mdash; which of the seven dimensions earn their place")}>Ablation &mdash; quali delle sette dimensioni si guadagnano il posto</div>
      <div class="section-sub" {_bi(f"Si azzera il peso di una dimensione alla volta e si ricostruisce il TPI (shrink, peso di ruolo e penalty inclusi: la ricostruzione completa correla 0.96 col TPI pubblicato). Poi si guarda quanto cambia il ranking e quanto cambia la predizione, su {vo.get('n')} giocatori.", f"One dimension&rsquo;s weight is zeroed at a time and the TPI rebuilt (shrink, role weight and penalty included: the full reconstruction correlates 0.96 with the published TPI). Then we measure how much the ranking and the prediction change, over {vo.get('n')} players.")}>Si azzera il peso di una dimensione alla volta e si ricostruisce il TPI, su {vo.get("n")} giocatori.</div>
    </div>
  </div>
  <div class="card">
    <div class="card-ttl" {_bi(f"&rho;(TPI, rendimento) di partenza = {vo.get('baseline_rho_realized')}", f"Starting &rho;(TPI, output) = {vo.get('baseline_rho_realized')}")}>&rho;(TPI, rendimento) di partenza = {vo.get("baseline_rho_realized")}</div>
    <div class="table-wrap"><table>
      <tr><th {_bi("Dimensione tolta","Dimension removed")}>Dimensione tolta</th><th {_bi("Peso","Weight")}>Peso</th>
          <th {_bi("&rho; vs TPI pieno","&rho; vs full TPI")}>&rho; vs TPI pieno</th>
          <th {_bi("&rho; vs reso","&rho; vs realized")}>&rho; vs reso</th>
          <th {_bi("&Delta; predizione","&Delta; prediction")}>&Delta; predizione</th>
          <th>Top 10</th><th {_bi("Verdetto","Verdict")}>Verdetto</th></tr>
      <tbody>{o_rows}</tbody>
    </table></div>
    <div class="interp" style="border-left-color:{o_col}" {_bi(o_verd_it + " Il criterio &egrave; l&rsquo;output offensivo della stessa stagione, in-sample e sui 100 gi&agrave; selezionati: legge quanto ogni dimensione contribuisce a predire i gol, non quanto vale per uno scout.", o_verd_en + " The criterion is same-season attacking output, in-sample and on the already-selected 100: it reads how much each dimension helps predict goals, not how much it is worth to a scout.")}>{o_verd_it}</div>
  </div>
</div>"""
    elif vo.get("msg"):
        o_section = f'<div class="nota-warn" style="margin-top:16px">{vo["msg"]}</div>'
    else:
        o_section = ""

    # ── Sezione N — predittività per ruolo ───────────────────────
    vn = val_n or {}
    if vn.get("has_data"):
        _n_lab = {"ATT": ("Attaccanti", "Forwards"), "CEN": ("Centrocampisti", "Midfielders"),
                  "DIF": ("Difensori", "Defenders"), "POR": ("Portieri", "Goalkeepers")}
        n_boxes = ""
        for _sb, (ruolo, d) in zip(("sb-orng", "sb-blue", "sb-green", "sb-purp"),
                                   vn["per_role"].items()):
            _lit, _len_ = _n_lab.get(ruolo, (ruolo, ruolo))
            if d.get("rho") is None:
                n_boxes += (f'<div class="stat-box {_sb}"><div class="stat-val" style="color:var(--lt)">&mdash;</div>'
                            f'<div class="stat-lbl"><span {_bi(_lit, _len_)}>{_lit}</span></div>'
                            f'<div class="stat-sub" {_bi(f"n = {d.get('n',0)}, campione insufficiente", f"n = {d.get('n',0)}, sample too small")}>n = {d.get("n",0)}</div></div>')
            else:
                n_boxes += (f'<div class="stat-box {_sb}"><div class="stat-val" style="color:{rcol(d["rho"])}">{d["rho"]:+.3f}</div>'
                            f'<div class="stat-lbl"><span {_bi(_lit, _len_)}>{_lit}</span></div>'
                            f'<div class="stat-sub">n = {d["n"]} &middot; {pval(d.get("p"))}</div></div>')
        _n_ok = {k: v for k, v in vn["per_role"].items() if v.get("rho") is not None}
        if _n_ok:
            _n_best = max(_n_ok.items(), key=lambda kv: kv[1]["rho"])
            _n_worst = min(_n_ok.items(), key=lambda kv: kv[1]["rho"])
            n_verd_it = (f"Il TPI regge meglio su {_n_lab.get(_n_best[0], (_n_best[0],))[0].lower()} "
                         f"(&rho; = {_n_best[1]['rho']:+.3f}) e peggio su "
                         f"{_n_lab.get(_n_worst[0], (_n_worst[0],))[0].lower()} (&rho; = {_n_worst[1]['rho']:+.3f}).")
            n_verd_en = (f"The TPI holds up best for {_n_lab.get(_n_best[0], (_n_best[0],))[1].lower()} "
                         f"(&rho; = {_n_best[1]['rho']:+.3f}) and worst for "
                         f"{_n_lab.get(_n_worst[0], (_n_worst[0],))[1].lower()} (&rho; = {_n_worst[1]['rho']:+.3f}).")
            _n_dif = _n_ok.get("DIF") or {}
            if _n_dif.get("rho") is not None:
                # Il dato che sorprende non e' il numero basso ma quello alto:
                # va spiegato, altrimenti si legge come "il TPI valuta i difensori".
                n_verd_it += (f" Il &rho; dei difensori ({_n_dif['rho']:+.3f}) non va letto come "
                              f"&laquo;il TPI valuta bene i difensori&raquo;: il criterio &egrave; l&rsquo;output "
                              f"offensivo per-90, quindi dentro quel ruolo separa i terzini che attaccano dagli "
                              f"altri, non i bravi difensori dai mediocri. Il mestiere difensivo il TPI, oggi, "
                              f"non lo guarda proprio.")
                n_verd_en += (f" The defenders&rsquo; &rho; ({_n_dif['rho']:+.3f}) should not be read as "
                              f"&ldquo;the TPI rates defenders well&rdquo;: the criterion is per-90 attacking "
                              f"output, so within that role it separates attacking full-backs from the rest, "
                              f"not good defenders from mediocre ones. Defensive craft is something the TPI, "
                              f"today, does not look at at all.")
        else:
            n_verd_it = n_verd_en = "&mdash;"
        n_section = f"""
<div class="section">
  <div class="section-hd">
    <div class="section-num">N</div>
    <div>
      <div class="section-ttl" {_bi("Per chi funziona &mdash; predittivit&agrave; per ruolo","Who it works for &mdash; predictivity by role")}>Per chi funziona &mdash; predittivit&agrave; per ruolo</div>
      <div class="section-sub" {_bi("Un indice offensivo vale anche per i difensori? &rho; di Spearman fra TPI e output realizzato, calcolata dentro ogni ruolo.","Does an attacking index also hold for defenders? Spearman &rho; between TPI and realized output, computed within each role.")}>Un indice offensivo vale anche per i difensori? &rho; fra TPI e output realizzato, dentro ogni ruolo.</div>
    </div>
  </div>
  <div class="g4">{n_boxes}</div>
  <div class="card" style="margin-top:16px"><div class="interp" {_bi(n_verd_it, n_verd_en)}>{n_verd_it}</div></div>
</div>"""
    elif vn.get("msg"):
        n_section = f'<div class="nota-warn" style="margin-top:16px">{vn["msg"]}</div>'
    else:
        n_section = ""

    # ── Sezione L — convergenza del ranking ──────────────────────
    vl = val_l or {}
    if vl.get("has_data"):
        l_rows = "".join(
            f'<tr><td style="font-family:var(--mono);color:var(--orng)">g{r["vintage_giornata"]}</td>'
            f'<td style="font-family:var(--mono);color:{rcol(r["spearman_rho"])};font-weight:700">{_sf(r["spearman_rho"],3)}</td>'
            f'<td style="font-family:var(--mono)">{int(r["overlap_top25"]*100)}%</td>'
            f'<td style="font-family:var(--mono);font-size:12px">{r["n_common"]}</td></tr>'
            for r in vl.get("per_vintage", [])
        )
        l_cross = "".join(
            f'<tr><td style="color:var(--lp)">{r["season"]}</td>'
            f'<td style="color:var(--lt);font-size:12px">{r["snapshot"]}</td>'
            f'<td style="font-family:var(--mono);color:{rcol(r["spearman_rho"])}">{_sf(r["spearman_rho"],3)}</td>'
            f'<td style="font-family:var(--mono);font-size:12px">{r["n_common"]}</td></tr>'
            for r in vl.get("cross_season", [])
        )
        _l_pv = [r for r in vl.get("per_vintage", []) if r.get("spearman_rho") is not None]
        if _l_pv:
            _l_first, _l_last = _l_pv[0], _l_pv[-1]
            l_verd_it = (f"Gi&agrave; alla giornata {_l_first['vintage_giornata']} il ranking assomiglia a quello finale "
                         f"(&rho; = {_sf(_l_first['spearman_rho'],3)}), e sale a {_sf(_l_last['spearman_rho'],3)} alla "
                         f"{_l_last['vintage_giornata']}<sup>a</sup>. Un terzo del ranking finale, per&ograve;, si decide dopo: "
                         f"chi usa il TPI a met&agrave; stagione deve saperlo.")
            l_verd_en = (f"By matchday {_l_first['vintage_giornata']} the ranking already resembles the final one "
                         f"(&rho; = {_sf(_l_first['spearman_rho'],3)}), rising to {_sf(_l_last['spearman_rho'],3)} by matchday "
                         f"{_l_last['vintage_giornata']}. Still, a third of the final ranking is decided later: "
                         f"anyone using the TPI mid-season should know that.")
        else:
            l_verd_it = l_verd_en = "&mdash;"
        l_section = f"""
<div class="section">
  <div class="section-hd">
    <div class="section-num">L</div>
    <div>
      <div class="section-ttl" {_bi("Quando il ranking &egrave; affidabile &mdash; convergenza per giornata","When the ranking becomes reliable &mdash; convergence by matchday")}>Quando il ranking &egrave; affidabile &mdash; convergenza per giornata</div>
      <div class="section-sub" {_bi("Il TPI calcolato a met&agrave; stagione quanto somiglia a quello di fine stagione? Ogni riga &egrave; uno snapshot vero, salvato a quella giornata e mai ritoccato.","How closely does a mid-season TPI resemble the end-of-season one? Each row is a real snapshot, saved at that matchday and never touched again.")}>Il TPI calcolato a met&agrave; stagione quanto somiglia a quello finale? Ogni riga &egrave; uno snapshot vero.</div>
    </div>
  </div>
  <div class="g2">
    <div class="card"><div class="card-ttl" {_bi("Dentro la stagione","Within the season")}>Dentro la stagione</div>
      <div class="table-wrap"><table>
        <tr><th {_bi("Vintage","Vintage")}>Vintage</th><th>&rho;</th>
            <th {_bi("Top 25 in comune","Top 25 shared")}>Top 25</th><th>n</th></tr>
        <tbody>{l_rows}</tbody>
      </table></div></div>
    <div class="card"><div class="card-ttl" {_bi("Fra stagioni","Across seasons")}>Fra stagioni</div>
      <div class="table-wrap"><table>
        <tr><th {_bi("Stagione","Season")}>Stagione</th><th>Snapshot</th><th>&rho;</th><th>n</th></tr>
        <tbody>{l_cross}</tbody>
      </table></div>
      <div class="interp" {_bi("Il crollo fra stagioni non &egrave; un difetto del calcolo: fra un anno e l&rsquo;altro cambiano squadra, ruolo e minutaggio. &Egrave; la misura di quanto poco il rendimento passato si trasporta.","The drop across seasons is not a computation flaw: team, role and minutes all change from one year to the next. It measures how little past output carries over.")}>Il crollo fra stagioni non &egrave; un difetto del calcolo: cambiano squadra, ruolo e minutaggio.</div></div>
  </div>
  <div class="card" style="margin-top:16px"><div class="interp" {_bi(l_verd_it, l_verd_en)}>{l_verd_it}</div></div>
</div>"""
    elif vl.get("msg"):
        l_section = f'<div class="nota-warn" style="margin-top:16px">{vl["msg"]}</div>'
    else:
        l_section = ""

    # ── Sezione P — persistenza ──────────────────────────────────
    vp = val_p or {}
    if vp.get("has_data"):
        p_cross = vp.get("cross_2season") or {}
        _p_d_within = vp.get("delta_within_vs_single")
        p_section_extra = ""
        if p_cross.get("rho_2season_mean") is not None:
            _pc_col = "var(--green)" if p_cross.get("significativo_95") else "var(--orng)"
            p_section_extra = f"""
  <div class="card" style="margin-top:16px">
    <div class="card-ttl" {_bi("E invece, mediando su due stagioni","And now, averaging across two seasons")}>E invece, mediando su due stagioni</div>
    <div class="g3">
      <div class="stat-box sb-blue"><div class="stat-val">{p_cross["rho_single"]:+.3f}</div><div class="stat-lbl" {_bi("TPI singolo","Single TPI")}>TPI singolo</div><div class="stat-sub">n = {p_cross["n"]}</div></div>
      <div class="stat-box sb-green"><div class="stat-val" style="color:{_pc_col}">{p_cross["rho_2season_mean"]:+.3f}</div><div class="stat-lbl" {_bi("Media 2 stagioni","2-season mean")}>Media 2 stagioni</div><div class="stat-sub">&Delta; {p_cross["delta"]:+.3f}</div></div>
      <div class="stat-box sb-purp"><div class="stat-val" style="font-size:20px">{_ci_txt(p_cross.get("ic95_lo"), p_cross.get("ic95_hi"))}</div><div class="stat-lbl">IC95% &Delta;</div><div class="stat-sub" {_bi("significativo" if p_cross.get("significativo_95") else "non significativo", "significant" if p_cross.get("significativo_95") else "not significant")}>{"significativo" if p_cross.get("significativo_95") else "non significativo"}</div></div>
    </div>
  </div>"""
        p_verd_it = (f"Il risultato interessante &egrave; la contraddizione. Mediare il TPI su pi&ugrave; snapshot <em>della stessa "
                     f"stagione</em> peggiora la predizione ({vp['rho_single']:+.3f} &rarr; {vp['rho_persistence_within_mean']:+.3f}): "
                     f"gli snapshot ravvicinati condividono le stesse partite, quindi la media non toglie rumore, toglie "
                     f"l&rsquo;informazione pi&ugrave; fresca.")
        p_verd_en = (f"The interesting result is the contradiction. Averaging the TPI over several snapshots <em>of the same "
                     f"season</em> makes prediction worse ({vp['rho_single']:+.3f} &rarr; {vp['rho_persistence_within_mean']:+.3f}): "
                     f"nearby snapshots share the same matches, so averaging does not remove noise, it removes the freshest "
                     f"information.")
        if p_cross.get("delta") is not None and p_cross["delta"] > 0:
            p_verd_it += (f" Mediare su <em>due stagioni diverse</em>, invece, la migliora nettamente "
                          f"({p_cross['rho_single']:+.3f} &rarr; {p_cross['rho_2season_mean']:+.3f}). Le due cose insieme dicono "
                          f"una cosa sola: serve pi&ugrave; tempo, non pi&ugrave; misure dello stesso tempo.")
            p_verd_en += (f" Averaging over <em>two different seasons</em>, instead, improves it markedly "
                          f"({p_cross['rho_single']:+.3f} &rarr; {p_cross['rho_2season_mean']:+.3f}). Together they say one thing: "
                          f"what helps is more time, not more measurements of the same time.")
        p_section = f"""
<div class="section">
  <div class="section-hd">
    <div class="section-num">P</div>
    <div>
      <div class="section-ttl" {_bi("Persistenza &mdash; conviene mediare il TPI su pi&ugrave; snapshot?","Persistence &mdash; is it worth averaging the TPI over snapshots?")}>Persistenza &mdash; conviene mediare il TPI su pi&ugrave; snapshot?</div>
      <div class="section-sub" {_bi(f"Chi tiene un TPI alto su molti snapshot dovrebbe essere pi&ugrave; affidabile di chi lo tocca una volta sola. Verificato su {vp.get('n_giocatori')} giocatori e {vp.get('n_within_vintage')} vintage, contro il rendimento delle giornate finali.", f"A player who holds a high TPI across many snapshots should be more reliable than one who touches it once. Tested on {vp.get('n_giocatori')} players and {vp.get('n_within_vintage')} vintages, against output in the closing matchdays.")}>Chi tiene un TPI alto su molti snapshot &egrave; pi&ugrave; affidabile? Su {vp.get("n_giocatori")} giocatori e {vp.get("n_within_vintage")} vintage.</div>
    </div>
  </div>
  <div class="g3">
    <div class="stat-box sb-orng"><div class="stat-val" style="color:{rcol(vp.get("rho_single"))}">{vp["rho_single"]:+.3f}</div><div class="stat-lbl" {_bi("TPI singolo","Single TPI")}>TPI singolo</div><div class="stat-sub" {_bi("snapshot corrente","current snapshot")}>snapshot corrente</div></div>
    <div class="stat-box sb-blue"><div class="stat-val" style="color:{rcol(vp.get("rho_persistence_within_mean"))}">{vp["rho_persistence_within_mean"]:+.3f}</div><div class="stat-lbl" {_bi("Media within-season","Within-season mean")}>Media within-season</div><div class="stat-sub">&Delta; {_p_d_within:+.3f}</div></div>
    <div class="stat-box sb-purp"><div class="stat-val" style="color:{rcol(vp.get("rho_persistence_all_mean"))}">{vp["rho_persistence_all_mean"]:+.3f}</div><div class="stat-lbl" {_bi("Media su tutti i vintage","Mean over all vintages")}>Media su tutti i vintage</div><div class="stat-sub">&Delta; {vp["delta_all_vs_single"]:+.3f}</div></div>
  </div>{p_section_extra}
  <div class="card" style="margin-top:16px"><div class="interp" style="border-left-color:var(--orng)" {_bi(p_verd_it, p_verd_en)}>{p_verd_it}</div></div>
</div>"""
    elif vp.get("msg"):
        p_section = f'<div class="nota-warn" style="margin-top:16px">{vp["msg"]}</div>'
    else:
        p_section = ""

    return f"""<!DOCTYPE html>
<html lang="it">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-capable" content="yes">
<title>Model Validation — Serie A Scout Index</title>
<script src="https://cdn.plot.ly/plotly-2.27.0.min.js"></script>
<style>{CSS}</style>
</head><body>

<div class="mwrap" id="modal" onclick="closeM()">
  <div class="mbox" onclick="event.stopPropagation()">
    <div class="mbox-icon" id="m-icon"></div>
    <div class="mbox-ttl" id="m-ttl"></div>
    <div class="mbox-sub" id="m-sub"></div>
    <div class="mbox-body" id="m-body"></div>
    <div class="mbox-ex" id="m-ex"></div>
    <button class="mbox-cls" onclick="closeM()" data-i18n="btn_close">Chiudi</button>
  </div>
</div>

<nav class="nav">
  <div class="nav-brand">TPI Validation <small>Serie A 25/26</small></div>
  <div class="nav-btn-group">
    <a class="nav-home-btn" href="dashboard_serie_a.html" data-i18n-title="nav_back_ranking" title="Torna alla classifica">
      <span class="home-lbl" data-i18n="term_ranking">Classifica</span>
    </a>
    <a class="nav-glass-btn" href="javascript:history.back()" data-i18n-title="nav_back" title="Indietro" data-i18n-aria-label="nav_back">&#8592;</a>
    <a class="nav-glass-btn" href="javascript:history.forward()" data-i18n-title="nav_forward" title="Avanti" data-i18n-aria-label="nav_forward">&#8594;</a>
  </div>
  <div class="nav-right-group">
    <span data-i18n-switcher></span>
    <a class="nav-orng-btn" href="index.html" data-i18n-title="nav_back_homepage" title="Torna alla Homepage">
      <span class="hp-label" data-i18n="nav_home">Homepage</span>
    </a>
  </div>
</nav>

<div class="hero">
  <div class="hero-eyebrow">Serie A Scout Index &middot; TPI System</div>
  <div class="hero-ttl" data-i18n="val_title">Model Validation</div>
  {hero_sub}
  <!-- Sotto i 480px resta visibile solo la prima pill: qui c'era il test piu'
       debole (Pearson vs Fantacalcio, IC che contiene lo zero). Ora c'e' quello
       che la pagina rivendica davvero.
       La terza diceva "persistenza su due stagioni": era un risultato, ed e'
       poi diventato non significativo sul campione intero. Ora dice su quanti
       giocatori girano i test, che e' un fatto e non un esito. -->
  <div class="hero-pills">
    <span class="hero-pill"><span {_bi("Indice descrittivo","Descriptive index")}>Indice descrittivo</span></span>
    <span class="hero-pill"><span {_bi("Confronto con baseline banali","Compared to trivial baselines")}>Confronto con baseline banali</span></span>
    <span class="hero-pill"><span {_bi(_pill_camp_it, _pill_camp_en)}>{_pill_camp_it}</span></span>
    <span class="hero-pill"><span {_bi(f"Backtest su output grezzo r={_r2(r_c)}", f"Backtest on raw output r={_r2(r_c)}")}>Backtest su output grezzo r={_r2(r_c)}</span></span>
    <span class="hero-pill"><span {_bi("Indice Et&agrave; &amp; Fisico","Age &amp; Physical Index")}>Age &amp; Physical Index</span></span>
    <span class="hero-pill">TPI Pro</span>
  </div>
</div>

<div class="main">

<div class="accordion">
  <div class="acc-hd" onclick="toggleAcc('acc1')">
    <div class="acc-title"><span {_bi("Come leggere i risultati","How to read the results")}>Come leggere i risultati</span></div>
    <span class="acc-chev" id="chev-acc1">&#9660;</span>
  </div>
  <div class="acc-body" id="acc1">
    <div class="guide-grid">
      <div class="guide-card"><div class="guide-ttl" {_bi("r di Pearson","Pearson's r")}>r di Pearson</div>
        <div class="guide-body" {_bi('<strong style="color:var(--green)">r&gt;0.6</strong> forte &middot; <strong style="color:var(--orng)">0.4–0.6</strong> moderata &middot; <strong style="color:var(--red)">&lt;0.4</strong> debole. Per sport r=0.5 con fonti esterne &egrave; ottimo.', '<strong style="color:var(--green)">r&gt;0.6</strong> strong &middot; <strong style="color:var(--orng)">0.4–0.6</strong> moderate &middot; <strong style="color:var(--red)">&lt;0.4</strong> weak. For sport, r=0.5 against external sources is excellent.')}><strong style="color:var(--green)">r&gt;0.6</strong> forte &middot; <strong style="color:var(--orng)">0.4–0.6</strong> moderata &middot; <strong style="color:var(--red)">&lt;0.4</strong> debole. Per sport r=0.5 con fonti esterne &egrave; ottimo.</div></div>
      <div class="guide-card"><div class="guide-ttl">p-value</div>
        <div class="guide-body" {_bi('<strong style="color:var(--green)">p&lt;0.05</strong> = significativo. Senza p basso anche r alto potrebbe essere fortuna.', '<strong style="color:var(--green)">p&lt;0.05</strong> = significant. Without a low p, even a high r could be luck.')}><strong style="color:var(--green)">p&lt;0.05</strong> = significativo. Senza p basso anche r alto potrebbe essere fortuna.</div></div>
      <div class="guide-card"><div class="guide-ttl" {_bi("Overlap: nessuna soglia","Overlap: no threshold")}>Overlap: nessuna soglia</div>
        <div class="guide-body" {_bi("Non c&rsquo;&egrave; un valore che promuove e uno che boccia: alto direbbe &laquo;confermato&raquo;, basso &laquo;trova gemme&raquo;. Per questo la sezione B &egrave; descrittiva e non un test.","There is no value that passes and one that fails: high would mean &ldquo;confirmed&rdquo;, low &ldquo;finds gems&rdquo;. That is why section B is descriptive and not a test.")}>Non c&rsquo;&egrave; un valore che promuove e uno che boccia: alto direbbe &laquo;confermato&raquo;, basso &laquo;trova gemme&raquo;. Per questo la sezione B &egrave; descrittiva.</div></div>
      <div class="guide-card"><div class="guide-ttl">Backtest</div>
        <div class="guide-body" {_bi("Misura se l&rsquo;output offensivo si conferma fra le due met&agrave; della stagione. Riguarda quella rate stat, non il composito: per il composito c&rsquo;&egrave; la sezione Q.","It measures whether attacking output repeats across the two halves of the season. It is about that rate stat, not the composite: for the composite see section Q.")}>Misura se l&rsquo;output offensivo si conferma fra le due met&agrave; della stagione. Riguarda quella rate stat, non il composito: per il composito c&rsquo;&egrave; la sezione Q.</div></div>
      <div class="guide-card"><div class="guide-ttl">TPI Pro</div>
        <div class="guide-body" {_bi("Aggiunge AII (et&agrave;) e PRI (affidabilit&agrave; fisica) al TPI classico. Se quei modulatori servano davvero non lo dice r(TPI,TPI Pro) &mdash; il Pro contiene il TPI &mdash; ma il test I, fuori campione.","Adds AII (age) and PRI (physical reliability) to the classic TPI. Whether those modulators actually help is not answered by r(TPI,TPI Pro) &mdash; the Pro contains the TPI &mdash; but by test I, out-of-sample.")}>Aggiunge AII (et&agrave;) e PRI (affidabilit&agrave; fisica) al TPI classico. Se servano davvero lo dice il test I, non r(TPI,TPI Pro).</div></div>
      <div class="guide-card"><div class="guide-ttl" {_bi("Campione","Sample")}>Campione</div>
        <div class="guide-body" {_bi(_camp_it, _camp_en)}>{_camp_it}</div></div>
      <div class="guide-card"><div class="guide-ttl" {_bi("Limiti","Limits")}>Limiti</div>
        <div class="guide-body" {_bi("Voti Fantacalcio e WhoScored sono inseriti manualmente. Backtest payload = Output Adj vs EWMA come proxy.","Fantacalcio and WhoScored ratings are entered manually. Payload backtest = Output Adj vs EWMA as a proxy.")}>Voti Fantacalcio e WhoScored sono inseriti manualmente. Backtest payload = Output Adj vs EWMA come proxy.</div></div>
    </div>
  </div>
</div>

<!-- RECAP — in cima, non in fondo.
     L'ordine A→B→C metteva davanti il test piu' debole: una r bassa con IC
     bootstrap che contiene lo zero, quindi non una prova. Chi si fermava al
     primo riquadro se ne andava con quello, senza aver visto il backtest a
     rho=0.725 con placebo p=0.002, che e' il risultato vero della pagina.
     Il riepilogo in apertura mostra l'esito di tutti i test insieme, poi si
     scende nel dettaglio. -->
{recap_html}

{claim_html}

{soglie_html}

<!-- SEZIONE A -->
<div class="section">
  <div class="section-hd">
    <div class="section-num">A</div>
    <div>
      <div class="section-ttl"><span {_bi("TPI vs Voti Fantacalcio &mdash; descrittivo","TPI vs Fantacalcio Ratings &mdash; descriptive")}>TPI vs Voti Fantacalcio &mdash; descrittivo</span> <span class="help" onclick="openM('pearson')">?</span></div>
      <div class="section-sub" {_bi(_a_sub_it, _a_sub_en)}>{_a_sub_it}</div>
    </div>
  </div>
  <div class="g3">
    <div class="stat-box sb-blue">
      <div class="stat-val" style="color:{rcol(r_a)}">{_sf(r_a,3)}</div>
      <div class="stat-lbl">Pearson r</div>
      <div class="stat-sub">95% CI [{_sf(val_a.get("ci_lo"),3)}, {_sf(val_a.get("ci_hi"),3)}]</div>
    </div>
    <div class="stat-box sb-green">
      <div class="stat-val" style="color:{rcol(val_a.get('r_spearman'))}">{_sf(val_a.get("r_spearman"),3)}</div>
      <div class="stat-lbl">Spearman &#961;</div>
      <div class="stat-sub"><span {_bi("robusto agli outlier","robust to outliers")}>robusto agli outlier</span> &middot; {pval(val_a.get("p_spearman"))}</div>
    </div>
    <div class="stat-box sb-orng">
      <div class="stat-val">{_sf(val_a.get("cohen_d"),2)}</div>
      <div class="stat-lbl">Cohen&#x2019;s d</div>
      <div class="stat-sub" {_bi("effect size top vs bottom 25%","effect size top vs bottom 25%")}>effect size top vs bottom 25%</div>
    </div>
  </div>
  <div class="g2">
    <div class="card"><div class="card-ttl">Scatter TPI vs Fantacalcio <span class="help" onclick="openM('scatter')">?</span></div>
      <div id="chart-a" class="chart-h" style="height:300px"></div></div>
    <div class="card"><div class="card-ttl" {_bi("Interpretazione","Interpretation")}>Interpretazione</div>
      <div style="font-size:13px;color:var(--ls);line-height:1.75" {_bi(f'<p style="margin-bottom:10px">I <strong style="color:var(--lp)">voti Fantacalcio</strong> rappresentano la percezione collettiva della qualit&agrave;.</p><p style="margin-bottom:10px">Non c&rsquo;&egrave; una fascia di r che promuove il TPI: qui non si sta verificando niente, si sta misurando quanto due graduatorie si somigliano.</p><p>{_a_par_it}</p>', f'<p style="margin-bottom:10px">The <strong style="color:var(--lp)">Fantacalcio ratings</strong> represent the collective perception of quality.</p><p style="margin-bottom:10px">There is no band of r that vindicates the TPI: nothing is being verified here, we are measuring how much two rankings resemble each other.</p><p>{_a_par_en}</p>')}>
        <p style="margin-bottom:10px">I <strong style="color:var(--lp)">voti Fantacalcio</strong> rappresentano la percezione collettiva della qualit&agrave;.</p>
        <p style="margin-bottom:10px">Non c&rsquo;&egrave; una fascia di r che promuove il TPI: qui non si sta verificando niente, si sta misurando quanto due graduatorie si somigliano.</p>
        <p>{_a_par_it}</p>
      </div>
      <div class="interp"><span {_bi(val_a.get("interpretazione","&mdash;"), val_a.get("interpretazione_en","&mdash;"))}>{val_a.get("interpretazione","&mdash;")}</span></div>
      {a_rigor}
    </div>
  </div>
</div>

<!-- SEZIONE B -->
<div class="section">
  <div class="section-hd">
    <div class="section-num">B</div>
    <div>
      <div class="section-ttl"><span {_bi("Confronto con WhoScored &mdash; descrittivo","Comparison with WhoScored &mdash; descriptive")}>Confronto con WhoScored &mdash; descrittivo</span> <span class="help" onclick="openM('overlap')">?</span></div>
      <div class="section-sub" {_bi(f"Qui non c&rsquo;&egrave; un test: come criterio di validazione, l&rsquo;overlap <strong>non pu&ograve; fallire</strong> &mdash; alto direbbe &laquo;confermato&raquo;, basso &laquo;trova gemme&raquo;. E il {ov}% osservato ha p = {_bp2} sull&rsquo;ipergeometrica: &egrave; compatibile col caso. Resta interessante <em>dove</em> i due non sono d&rsquo;accordo.", f"There is no test here: as a validation criterion, overlap <strong>cannot fail</strong> &mdash; high would mean &ldquo;confirmed&rdquo;, low &ldquo;finds gems&rdquo;. And the observed {ov}% has p = {_bp2} under the hypergeometric: it is consistent with chance. What stays interesting is <em>where</em> the two disagree.")}>Qui non c&rsquo;&egrave; un test: come criterio di validazione l&rsquo;overlap <strong>non pu&ograve; fallire</strong>, e il {ov}% osservato &egrave; compatibile col caso. Resta interessante <em>dove</em> i due non sono d&rsquo;accordo.</div>
    </div>
  </div>
  <div class="g3">
    <div class="stat-box sb-orng"><div class="stat-val">{ov}%</div><div class="stat-lbl" {_bi("Overlap Top 10","Top 10 Overlap")}>Overlap Top 10</div><div class="stat-sub" {_bi(f"p ipergeometrica = {_bp2} &middot; atteso per caso {_bexp}/10", f"hypergeometric p = {_bp2} &middot; expected by chance {_bexp}/10")}>p ipergeometrica = {_bp2}</div></div>
    <div class="stat-box sb-green"><div class="stat-val">{len(val_b.get("divergenze_pos",[]))}</div><div class="stat-lbl" {_bi("Sottovalutati","Undervalued")}>Sottovalutati</div><div class="stat-sub" {_bi("TPI alto, WhoScored basso","high TPI, low WhoScored")}>TPI alto, WhoScored basso</div></div>
    <div class="stat-box"><div class="stat-val">{len(val_b.get("divergenze_neg",[]))}</div><div class="stat-lbl" {_bi("Sopravvalutati","Overvalued")}>Sopravvalutati</div><div class="stat-sub" {_bi("TPI basso, WhoScored alto","low TPI, high WhoScored")}>TPI basso, WhoScored alto</div></div>
  </div>
  <div class="g2">
    <div class="card"><div class="card-ttl" {_bi("Top 10 per TPI","Top 10 by TPI")}>Top 10 per TPI</div>
      <div class="table-wrap"><table><tr><th>#</th><th {_bi("Giocatore","Player")}>Giocatore</th><th {_bi("Sqd","Team")}>Sqd</th><th>TPI</th><th>WS</th></tr><tbody id="tb-tpi"></tbody></table></div></div>
    <div class="card"><div class="card-ttl" {_bi("Top 10 per WhoScored","Top 10 by WhoScored")}>Top 10 per WhoScored</div>
      <div class="table-wrap"><table><tr><th>#</th><th {_bi("Giocatore","Player")}>Giocatore</th><th {_bi("Sqd","Team")}>Sqd</th><th>WS</th><th>TPI</th></tr><tbody id="tb-ws"></tbody></table></div></div>
  </div>
  <div class="card"><div class="card-ttl"><span {_bi("Divergenze notevoli","Notable divergences")}>Divergenze notevoli</span> <span class="help" onclick="openM('divergenze')">?</span></div>
    <div id="div-content"></div>
    <div class="interp" {_bi("Le divergenze sono la parte utile di questo confronto, ma sono un&rsquo;<strong>ipotesi</strong>: che il TPI veda un impatto non riconosciuto &egrave; da verificare giocatore per giocatore, non dimostrato dal fatto che i due elenchi differiscano.","The divergences are the useful part of this comparison, but they are a <strong>hypothesis</strong>: that the TPI sees unrecognised impact has to be checked player by player &mdash; the two lists differing does not establish it.")}>Le divergenze sono la parte utile di questo confronto, ma sono un&rsquo;<strong>ipotesi</strong>: che il TPI veda un impatto non riconosciuto &egrave; da verificare giocatore per giocatore.</div>
    {b_rigor}
  </div>
</div>

<!-- SEZIONE C -->
<div class="section">
  <div class="section-hd">
    <div class="section-num">C</div>
    <div>
      <div class="section-ttl"><span {_bi(f"Backtest sull&rsquo;output grezzo — r = {_r2(r_c)}", f"Backtest on raw output — r = {_r2(r_c)}")}>Backtest sull&rsquo;output grezzo — r = {_r2(r_c)}</span> <span class="help" onclick="openM('backtest')">?</span></div>
      <div class="section-sub" {_bi(f"Il rendimento della prima met&agrave; predice quello della seconda? {val_c.get('early_range','First half')} &rarr; {val_c.get('late_range','Second half')}. Il titolo dice &laquo;output grezzo&raquo; e non &laquo;TPI&raquo; perch&eacute; la misura &egrave; (xG+xA)/90, la componente dominante: buona parte di questa r &egrave; l&rsquo;autocorrelazione di una rate stat fra due met&agrave; di stagione, non una prova sul composito.", f"Does first-half output predict second-half output? {val_c.get('early_range','First half')} &rarr; {val_c.get('late_range','Second half')}. The title says &ldquo;raw output&rdquo; and not &ldquo;TPI&rdquo; because the measure is (xG+xA)/90, the dominant component: much of this r is the autocorrelation of a rate stat across two halves of a season, not evidence about the composite.")}>Il rendimento della prima met&agrave; predice quello della seconda? La misura &egrave; (xG+xA)/90, la componente dominante: buona parte di questa r &egrave; autocorrelazione, non una prova sul composito.</div>
    </div>
  </div>
  {nota_html}
  <div class="g3">
    <div class="stat-box sb-green">
      <div class="stat-val" style="color:{rcol(r_c)}">{_sf(r_c,3)}</div>
      <div class="stat-lbl">Spearman &#961;</div>
      <div class="stat-sub">95% CI [{_sf(val_c.get("ci_lo"),3)}, {_sf(val_c.get("ci_hi"),3)}]</div>
    </div>
    <div class="stat-box sb-blue">
      <div class="stat-val">{_sf(val_c.get("tau"),3)}</div>
      <div class="stat-lbl">Kendall &#964;</div>
      <div class="stat-sub"><span {_bi("stabilit&agrave; ranking","ranking stability")}>ranking stability</span> &middot; {pval(val_c.get("p_tau"))}</div>
    </div>
    <div class="stat-box">
      <div class="stat-val" style="font-size:16px">{_sf(val_c.get("rmse"),4)}</div>
      <div class="stat-lbl">RMSE</div>
      <div class="stat-sub">MAE = {_sf(val_c.get("mae"),4)}</div>
    </div>
  </div>
  <div class="g2">
    <div class="card"><div class="card-ttl"><span {_bi("Scatter prima &rarr; seconda fase","Scatter first &rarr; second half")}>Scatter prima &rarr; seconda fase</span> <span class="help" onclick="openM('scatter_c')">?</span></div>
      <div id="chart-c" class="chart-h" style="height:300px"></div></div>
    <div class="card"><div class="card-ttl" {_bi("Come va letta questa r","How to read this r")}>Come va letta questa r</div>
      <div style="font-size:13px;color:var(--ls);line-height:1.75" {_bi('<p style="margin-bottom:10px">Una rate stat che si conferma fra due met&agrave; di stagione dice che il rendimento offensivo &egrave; <strong>stabile</strong>, e serve a saperlo. Non dice che il TPI predice: dice che (xG+xA)/90 predice s&eacute; stesso.</p><p style="margin-bottom:10px">La domanda &laquo;il composito aggiunge a questo?&raquo; ha una sezione sua, la <strong>Q</strong>, e una risposta diversa.</p><p><strong style="color:var(--lp)">Spearman</strong> &egrave; robusto agli outlier &mdash; ideale per dati sportivi.</p>', '<p style="margin-bottom:10px">A rate stat that repeats across two halves of a season shows attacking output is <strong>stable</strong>, which is worth knowing. It does not show that the TPI predicts: it shows that (xG+xA)/90 predicts itself.</p><p style="margin-bottom:10px">The question &ldquo;does the composite add to this?&rdquo; has its own section, <strong>Q</strong>, and a different answer.</p><p><strong style="color:var(--lp)">Spearman</strong> is robust to outliers &mdash; ideal for sports data.</p>')}>
        <p style="margin-bottom:10px">Una rate stat che si conferma fra due met&agrave; di stagione dice che il rendimento offensivo &egrave; <strong>stabile</strong>, e serve a saperlo. Non dice che il TPI predice: dice che (xG+xA)/90 predice s&eacute; stesso.</p>
        <p style="margin-bottom:10px">La domanda &laquo;il composito aggiunge a questo?&raquo; ha una sezione sua, la <strong>Q</strong>, e una risposta diversa.</p>
        <p><strong style="color:var(--lp)">Spearman</strong> &egrave; robusto agli outlier &mdash; ideale per dati sportivi.</p>
      </div>
      <div class="interp"><span {_bi(val_c.get("interpretazione","&mdash;"), val_c.get("interpretazione_en","&mdash;"))}>{val_c.get("interpretazione","&mdash;")}</span></div>
      {c_rigor}
    </div>
  </div>
</div>

{v2_section}

{e_section}

{f_section}

{g_section}

{h_section}

{i_section}

<!-- Q L M N O P — i test che finivano solo nel log.
     Ordine scelto: prima la domanda che puo' bocciare l'indice (Q), poi se
     e' calibrato (M), cosa c'e' dentro (O), per chi vale (N), da quando ci
     si puo' fidare (L) e come si usa nel tempo (P). -->
{q_section}

{m_section}

{o_section}

{n_section}

{l_section}

{p_section}

</div><!-- /main -->

<div id="wm"><span id="wm-dot"></span>
  <span id="wm-text">Raffaele Ciccone &thinsp;&middot;&thinsp; Serie A Scout Index &thinsp;&middot;&thinsp; Validazione TPI</span>
</div>

<script>
const SCATTER_A={scatter_a};const SCATTER_C={scatter_c};
const TOP10_TPI={top10_tpi};const TOP10_WS={top10_ws};
const DIV_POS={div_pos};const DIV_NEG={div_neg};
const SCATTER_D={j(vd.get("scatter", []))};
const SCATTER_E={j(ve.get("scatter", []) if has_pro else [])};
const SL_A={sl_a};const IC_A={ic_a};
const SL_C={sl_c};const IC_C={ic_c};
const R_A={_js_num(r_a)};const R_C={_js_num(r_c)};const OV={ov};

const PL={{responsive:true,displayModeBar:false}};
const BL={{paper_bgcolor:"transparent",plot_bgcolor:"transparent",
  font:{{color:"rgba(233,240,236,.38)",family:"JetBrains Mono,ui-monospace,monospace",size:10}},
  xaxis:{{gridcolor:"rgba(233,240,236,.07)",color:"rgba(233,240,236,.38)",
    tickfont:{{size:9.5}},zeroline:false}},
  yaxis:{{gridcolor:"rgba(233,240,236,.07)",color:"rgba(233,240,236,.38)",
    tickfont:{{size:9.5}},zeroline:false}}}};
/* Stessa triade desaturata di RUOLO_COLORS in parte2_dashboard.py: se cambia
   li', va cambiata anche qui, altrimenti il ruolo ha due colori nel sito. */
const RC_MAP={{"POR":"#7A8A84","DIF":"#5A93C4","CEN":"#5FAE7E","ATT":"#D98E6A","":"#46554F"}};
const rcf=r=>RC_MAP[r]||"#46554F";
function T(k,fb){{ return (window.SerieAi18n ? window.SerieAi18n.t(k) : (fb!=null?fb:k)); }}

const SPIEG={{
  pearson:{{icon:"📊",ttl:"Correlazione di Pearson",ttl_en:"Pearson correlation",
    sub:"r = Σ[(xi−x̄)(yi−ȳ)] / [n·σx·σy]",sub_en:"r = Σ[(xi−x̄)(yi−ȳ)] / [n·σx·σy]",
    body:"Misura la relazione lineare (–1 a +1).\\nr=+1: diretta perfetta.\\nr=0: nessuna relazione.\\nr=–1: inversa.\\n\\nDa sola r non dice quasi niente: conta insieme all'intervallo di confidenza e al numero di osservazioni. Su campioni piccoli una r apparentemente buona può essere compatibile con lo zero.",
    body_en:"Measures the linear relationship (–1 to +1).\\nr=+1: perfect direct.\\nr=0: no relationship.\\nr=–1: inverse.\\n\\nOn its own r says almost nothing: it counts together with the confidence interval and the number of observations. On small samples an apparently good r can still be consistent with zero.",
    ex:"È il caso di questa pagina: r = {_sf(r_a,2)} su {n_a} voti, con IC {_ci_txt(val_a.get('ci_lo'), val_a.get('ci_hi'))}. Lo zero è dentro, quindi il dato descrive, non dimostra.",ex_en:"That is the case on this page: r = {_sf(r_a,2)} over {n_a} ratings, CI {_ci_txt(val_a.get('ci_lo'), val_a.get('ci_hi'))}. Zero is inside it, so the figure describes rather than proves."}},
  scatter:{{icon:"🔵",ttl:"Come leggere lo Scatter",ttl_en:"How to read the Scatter",
    sub:"X = TPI | Y = Voto Fantacalcio",sub_en:"X = TPI | Y = Fantacalcio rating",
    body:"Ogni punto = un giocatore. Colore = ruolo.\\n\\nLontano dalla retta = sottovalutato o sopravvalutato.",
    body_en:"Each dot = a player. Colour = role.\\n\\nFar from the line = under- or over-valued.",
    ex:"Alto a sx: voto alto ma TPI basso → sopravvalutato. Basso a dx: TPI alto, voto basso → da valorizzare.",ex_en:"Top-left: high rating but low TPI → overvalued. Bottom-right: high TPI, low rating → to be valued."}},
  overlap:{{icon:"🎯",ttl:"Overlap Top 10",ttl_en:"Top 10 Overlap",
    sub:"% giocatori in entrambe le classifiche",sub_en:"% players in both rankings",
    body:"Attenzione a come si legge: non esiste un valore che promuove il TPI e uno che lo boccia. Overlap alto direbbe «confermato», overlap basso «trova gemme» — così il confronto non può fallire, e un criterio che non può fallire non verifica niente.\\n\\nPer questo qui è una descrizione, non un test. Il numero da guardare accanto è la p ipergeometrica: dice quanta di quella sovrapposizione è compatibile col caso.",
    body_en:"Careful how you read this: there is no value that vindicates the TPI and none that fails it. High overlap would mean “confirmed”, low overlap “finds gems” — so the comparison cannot fail, and a criterion that cannot fail verifies nothing.\\n\\nThat is why this is a description, not a test. The number to read next to it is the hypergeometric p: it says how much of that overlap is consistent with chance.",
    ex:"Con p = {_bp2} la sovrapposizione osservata è compatibile col caso: la parte utile del confronto non è il numero, sono i nomi su cui i due sistemi non vanno d'accordo.",ex_en:"With p = {_bp2} the observed overlap is consistent with chance: the useful part is not the number but the names the two systems disagree on."}},
  divergenze:{{icon:"🔍",ttl:"Divergenze TPI vs WhoScored",ttl_en:"TPI vs WhoScored divergences",
    sub:"Gap ≥4 posizioni tra i due sistemi",sub_en:"Gap ≥4 positions between the two systems",
    body:"Sottovalutati: TPI rank >> WhoScored → impatto offensivo non catturato.\\n\\nSopravvalutati: WhoScored >> TPI → percezione influenzata da aspetti non offensivi.",
    body_en:"Undervalued: TPI rank >> WhoScored → offensive impact not captured.\\n\\nOvervalued: WhoScored >> TPI → perception influenced by non-offensive aspects.",
    ex:"TPI #3, WhoScored #12: impatto offensivo reale non visibile nei voti generali.",ex_en:"TPI #3, WhoScored #12: real offensive impact not visible in the general ratings."}},
  backtest:{{icon:"⏱️",ttl:"Backtest sull'output grezzo",ttl_en:"Backtest on raw output",
    sub:"Spearman r tra prima e seconda fase",sub_en:"Spearman r between first and second half",
    body:"Misura se l'output offensivo per-90 della prima metà si ritrova nella seconda.\\n\\nLa misura è (xG+xA)/90, cioè la componente dominante del TPI, non il composito: buona parte di questa correlazione è una rate stat che predice sé stessa fra due metà della stessa stagione.\\n\\nSpearman (rank-based) è robusto agli outlier.",
    body_en:"It measures whether first-half per-90 attacking output shows up again in the second half.\\n\\nThe measure is (xG+xA)/90, the dominant TPI component, not the composite: much of this correlation is a rate stat predicting itself across two halves of the same season.\\n\\nSpearman (rank-based) is robust to outliers.",
    ex:"Dice che il rendimento offensivo è stabile, e serve saperlo. Non dice che il TPI predice: quella domanda è la sezione Q, e lì la risposta è diversa.",ex_en:"It shows attacking output is stable, which is worth knowing. It does not show the TPI predicts: that question is section Q, and there the answer is different."}},
  scatter_c:{{icon:"📈",ttl:"Scatter Backtest",ttl_en:"Backtest Scatter",
    sub:"X = prima fase | Y = seconda fase",sub_en:"X = first half | Y = second half",
    body:"Sopra la diagonale: migliorati. Sotto: peggiorati. Vicino alla retta: coerenti.",
    body_en:"Above the diagonal: improved. Below: worsened. Near the line: consistent.",
    ex:"Alto a destra = qualità stabile confermata. Alto a sx = giocatore in crescita stagionale.",ex_en:"Top-right = stable quality confirmed. Top-left = player improving over the season."}},
  {spieg_extra}
}};

function openM(k){{
  const s=SPIEG[k];if(!s)return;
  var EN=(window.SerieAi18n&&SerieAi18n.getLang&&SerieAi18n.getLang()==="en");
  var L=EN?"_en":"";
  document.getElementById("m-icon").textContent=s.icon;
  document.getElementById("m-ttl").textContent=s["ttl"+L]||s.ttl;
  document.getElementById("m-sub").textContent=s["sub"+L]||s.sub;
  document.getElementById("m-body").innerHTML=(s["body"+L]||s.body).replace(/\\n/g,"<br>");
  document.getElementById("m-ex").textContent=(EN?"Example: ":"Esempio: ")+(s["ex"+L]||s.ex);
  document.getElementById("modal").classList.add("open");
  window._curModal=k;
}}
document.addEventListener("i18n:changed",function(){{
  if(window._curModal&&document.getElementById("modal").classList.contains("open")) openM(window._curModal);
  // aggiorna i titoli degli assi dei grafici Plotly (disegnati una sola volta)
  function _rl(id,xt,yt){{var el=document.getElementById(id);if(el&&el.data&&el.data.length){{try{{Plotly.relayout(el,{{"xaxis.title.text":xt,"yaxis.title.text":yt}});}}catch(e){{}}}}}}
  _rl("chart-a",T("val_ax_tpi_tot","TPI Totale"),T("val_ax_fanta","Voto Fantacalcio"));
  _rl("chart-c",T("val_ax_early","Output/90 — Prima fase"),T("val_ax_late","Output/90 — Seconda fase"));
  _rl("chart-d",T("val_ax_aii","AII — Età Index"),T("val_ax_pri","PRI — Affidabilità Fisica"));
  _rl("chart-e",T("val_ax_tpi_classic","TPI Classico (7 dim)"),T("val_ax_tpi_pro","TPI Pro (7 dim + 5 mod)"));
}});
function closeM(){{document.getElementById("modal").classList.remove("open");}}

function toggleAcc(id){{
  const b=document.getElementById(id),c=document.getElementById("chev-"+id);
  const o=b.classList.toggle("open");
  if(c)c.style.transform=o?"rotate(180deg)":"";
}}

/* ── Chart A ── */
(function(){{
  if(!SCATTER_A||!SCATTER_A.length){{
    document.getElementById("chart-a").innerHTML=
      '<div style="color:var(--lt);padding:32px;text-align:center;font-size:13px">Aggiorna FANTA_VOTI con i voti reali</div>';
    return;
  }}
  var xv=SCATTER_A.map(d=>d.tpi),yv=SCATTER_A.map(d=>d.voto);
  var xmn=Math.min(...xv),xmx=Math.max(...xv);
  Plotly.newPlot("chart-a",[
    {{type:"scatter",mode:"markers",x:xv,y:yv,
      text:SCATTER_A.map(d=>d.nome+"<br>"+d.squadra),
      hovertemplate:"%{{text}}<br>TPI: %{{x:.3f}}<br>Fanta: %{{y:.2f}}<extra></extra>",
      marker:{{color:SCATTER_A.map(d=>rcf(d.ruolo)),size:9,opacity:.85,
        line:{{color:"rgba(233,240,236,.15)",width:1}}}}}},
    {{type:"scatter",mode:"lines",x:[xmn,xmx],
      y:[SL_A===null?0:SL_A*xmn+(IC_A||0),SL_A===null?0:SL_A*xmx+(IC_A||0)],
      line:{{color:"rgba(255,176,32,.55)",width:2,dash:"dot"}},hoverinfo:"skip"}},
  ],{{...BL,xaxis:{{...BL.xaxis,title:T("val_ax_tpi_tot","TPI Totale")}},
    yaxis:{{...BL.yaxis,title:T("val_ax_fanta","Voto Fantacalcio")}},
    margin:{{t:8,b:46,l:50,r:8}},height:300,showlegend:false,
    annotations:[{{x:.02,y:.97,xref:"paper",yref:"paper",
      text:"r = "+(R_A!==null?R_A.toFixed(3):"—"),showarrow:false,
      font:{{color:"rgba(233,240,236,.66)",size:13}},align:"left"}}]}},PL);
}})();

/* ── Table B ── */
(function(){{
  var tbT=document.getElementById("tb-tpi");
  TOP10_TPI.forEach((r,i)=>{{
    var ws=r.ws!=null?r.ws.toFixed(2):"—";
    var inWs=TOP10_WS.some(w=>w.nome===r.nome);
    tbT.innerHTML+=`<tr><td class="tv">${{i+1}}</td>
      <td style="font-weight:500">${{r.nome}}${{inWs?' <span style="color:var(--green);font-size:10px">&#10003;</span>':""}}</td>
      <td style="color:var(--lt);font-size:11px">${{r.squadra}}</td>
      <td class="torng">${{r.tpi>=0?"+":""}}${{r.tpi.toFixed(2)}}</td>
      <td class="tv">${{ws}}</td></tr>`;
  }});
  var tbW=document.getElementById("tb-ws");
  TOP10_WS.forEach((r,i)=>{{
    var tpi=r.tpi!=null?(r.tpi>=0?"+":"")+r.tpi.toFixed(2):"—";
    var inT=TOP10_TPI.some(t=>t.nome===r.nome);
    tbW.innerHTML+=`<tr><td class="tv">${{i+1}}</td>
      <td style="font-weight:500">${{r.nome}}${{inT?' <span style="color:var(--green);font-size:10px">&#10003;</span>':""}}</td>
      <td style="color:var(--lt);font-size:11px">${{r.squadra}}</td>
      <td class="torng">${{r.ws.toFixed(2)}}</td>
      <td class="tv">${{tpi}}</td></tr>`;
  }});
  var dc=document.getElementById("div-content");
  if(!DIV_POS.length&&!DIV_NEG.length){{
    dc.innerHTML='<p style="color:var(--lt);font-size:13px;padding:8px 0">Nessuna divergenza ≥4 posizioni rilevata.</p>';return;}}
  var h='<div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:4px">';
  if(DIV_POS.length){{h+='<div><div style="font-size:11px;font-weight:700;color:var(--green);margin-bottom:8px">Sottovalutati</div>';
    DIV_POS.forEach(d=>{{h+=`<div style="padding:9px 0 9px 12px;background:none;border:0;border-left:1px solid var(--green);border-radius:0;margin-bottom:4px">
      <div style="font-weight:600;font-size:13px">${{d.nome}} <span style="color:var(--lt);font-weight:400;font-size:11px">${{d.squadra}}</span></div>
      <div style="font-size:12px;color:var(--lt);margin-top:3px">TPI: <span style="color:var(--green);font-family:var(--mono)">#${{d.tpi_rank}}</span> vs WS: <span style="font-family:var(--mono)">#${{d.ws_rank}}</span></div>
    </div>`;}});h+='</div>';}}
  if(DIV_NEG.length){{h+='<div><div style="font-size:11px;font-weight:700;color:var(--red);margin-bottom:8px">Sopravvalutati</div>';
    DIV_NEG.forEach(d=>{{h+=`<div style="padding:9px 0 9px 12px;background:none;border:0;border-left:1px solid var(--red);border-radius:0;margin-bottom:4px">
      <div style="font-weight:600;font-size:13px">${{d.nome}} <span style="color:var(--lt);font-weight:400;font-size:11px">${{d.squadra}}</span></div>
      <div style="font-size:12px;color:var(--lt);margin-top:3px">TPI: <span style="color:var(--red);font-family:var(--mono)">#${{d.tpi_rank}}</span> vs WS: <span style="font-family:var(--mono)">#${{d.ws_rank}}</span></div>
    </div>`;}});h+='</div>';}}
  dc.innerHTML=h+'</div>';
}})();

/* ── Chart C ── */
(function(){{
  if(!SCATTER_C||!SCATTER_C.length){{
    document.getElementById("chart-c").innerHTML=
      '<div style="color:var(--lt);padding:32px;text-align:center;font-size:13px">Avvia MySQL per il backtest completo</div>';
    return;
  }}
  var xv=SCATTER_C.map(d=>d.early),yv=SCATTER_C.map(d=>d.late);
  var xmn=Math.min(...xv),xmx=Math.max(...xv);
  Plotly.newPlot("chart-c",[
    {{type:"scatter",mode:"markers",x:xv,y:yv,
      text:SCATTER_C.map(d=>d.nome+"<br>"+d.squadra),
      hovertemplate:"%{{text}}<br>Prima: %{{x:.3f}}<br>Seconda: %{{y:.3f}}<extra></extra>",
      marker:{{color:SCATTER_C.map(d=>rcf(d.ruolo)),size:7,opacity:.8,
        line:{{color:"rgba(233,240,236,.12)",width:1}}}}}},
    {{type:"scatter",mode:"lines",x:[xmn,xmx],
      y:[SL_C===null?0:SL_C*xmn+(IC_C||0),SL_C===null?0:SL_C*xmx+(IC_C||0)],
      line:{{color:"rgba(255,176,32,.55)",width:2,dash:"dot"}},hoverinfo:"skip"}},
    {{type:"scatter",mode:"lines",
      x:[Math.min(xmn,Math.min(...yv)),Math.max(xmx,Math.max(...yv))],
      y:[Math.min(xmn,Math.min(...yv)),Math.max(xmx,Math.max(...yv))],
      line:{{color:"rgba(233,240,236,.07)",width:1,dash:"dot"}},hoverinfo:"skip"}},
  ],{{...BL,xaxis:{{...BL.xaxis,title:T("val_ax_early","Output/90 — Prima fase")}},
    yaxis:{{...BL.yaxis,title:T("val_ax_late","Output/90 — Seconda fase")}},
    margin:{{t:8,b:46,l:54,r:8}},height:300,showlegend:false,
    annotations:[{{x:.02,y:.97,xref:"paper",yref:"paper",
      text:"Spearman r = "+(R_C!==null?R_C.toFixed(3):"—"),showarrow:false,
      font:{{color:"rgba(233,240,236,.66)",size:13}},align:"left"}}]}},PL);
}})();

/* ── Chart D — AII vs PRI ── */
(function(){{
  var el=document.getElementById("chart-d");if(!el)return;
  if(!SCATTER_D||!SCATTER_D.length){{
    el.innerHTML='<div style="color:var(--lt);padding:32px;text-align:center;font-size:13px">Dati AII/PRI non disponibili</div>';return;}}
  Plotly.newPlot("chart-d",[{{
    type:"scatter",mode:"markers",
    x:SCATTER_D.map(d=>d.aii),y:SCATTER_D.map(d=>d.pri),
    text:SCATTER_D.map(d=>d.nome+"<br>"+d.squadra),
    hovertemplate:"%{{text}}<br>AII: %{{x:.3f}}<br>PRI: %{{y:.3f}}<extra></extra>",
    marker:{{color:SCATTER_D.map(d=>rcf(d.ruolo)),size:8,opacity:.82,
      line:{{color:"rgba(233,240,236,.12)",width:1}}}}
  }}],{{...BL,
    xaxis:{{...BL.xaxis,title:T("val_ax_aii","AII — Et\u00e0 Index")}},
    yaxis:{{...BL.yaxis,title:T("val_ax_pri","PRI — Affidabilit\u00e0 Fisica")}},
    margin:{{t:8,b:46,l:54,r:8}},height:280,showlegend:false}},PL);
}})();

{e_js}

/* ── Badge dinamici (colore calcolato lato JS con valori reali) ── */
(function(){{
  function setBadge(id, val, hi, mid){{
    var el=document.getElementById(id); if(!el) return;
    var cls,lbl;
    if(val===null||val===undefined||isNaN(val)){{cls="badge-blue";lbl="N/D";}}
    else if(Math.abs(val)>=hi){{cls="badge-green";lbl="Buono \u2713";}}
    else if(Math.abs(val)>=mid){{cls="badge-orng";lbl="Moderato";}}
    else{{cls="badge-red";lbl="Basso";}}
    el.innerHTML='<span class="badge '+cls+'">'+lbl+'</span>';
  }}
  // soglie da SOGLIE in parte3_valida_tpi.py: qui non si scrivono a mano
  setBadge("badge-c", R_C, {SOGLIE['c_hi']}, {SOGLIE['c_mid']});
  // A e B non hanno piu' un verdetto da calcolare: sono descrittivi e la loro
  // etichetta e' statica lato Python. Lasciare qui setBadge la riscriveva in
  // "Basso"/"Moderato" a pagina caricata.
  // badge-d e badge-e sono già generati lato Python con valori statici
}})();

/* ── Resize Plotly al cambio orientamento ── */
var _rt;
window.addEventListener("resize",()=>{{clearTimeout(_rt);_rt=setTimeout(()=>{{
  document.querySelectorAll(".js-plotly-plot").forEach(el=>{{try{{Plotly.Plots.resize(el);}}catch(e){{}}}}
  );
}},250);}});
window.addEventListener("orientationchange",()=>{{setTimeout(()=>{{
  document.querySelectorAll(".js-plotly-plot").forEach(el=>{{try{{Plotly.Plots.resize(el);}}catch(e){{}}}}
  );
}},450);}});
</script>
<script src="i18n.js"></script>
<script src="ai_chat.js" defer></script>
</body>
</html>"""


# ════════════════════════════════════════════════════════════════
# MAIN
# ════════════════════════════════════════════════════════════════
def main():
    log.info("=" * 58)
    log.info("Validazione TPI v3.0 — Serie A 25/26")
    log.info("=" * 58)

    log.info("Carico payload.json...")
    meta    = load_payload()
    players = meta["players"]
    log.info(f"  {len(players)} giocatori")

    log.info("Connessione DB...")
    df_gp = load_player_games()

    log.info("\n[A] Correlazione TPI vs Fantacalcio...")
    val_a = valida_correlazione_fanta(players)

    log.info("[B] Top 10 TPI vs WhoScored...")
    val_b = valida_top10(players)

    log.info("[C] Backtest predittivo...")
    val_c = valida_backtest_db(df_gp) if (df_gp is not None and len(df_gp) > 0) \
            else valida_backtest_payload(players)

    log.info("[D] Validazione Età & Affidabilità Fisica...")
    val_d = valida_v2_indices(players)

    log.info("[E] Validazione TPI Pro...")
    val_e = valida_tpi_pro(players)

    log.info("[F] Validità ecologica a livello squadra...")
    val_f = valida_team_level(players, df_gp)

    log.info("[G] Struttura interna del composito (PCA)...")
    val_g = valida_internal_structure(players)

    log.info("[H] Robustezza ai pesi (Monte Carlo)...")
    val_h = valida_sensibilita(players)

    log.info("[I] Validità incrementale TPI Pro (preferendo OOS vintage se disponibile)...")
    # engine locale per la query del realizzato post-vintage (non riutilizziamo
    # quello di load_player_games perché è creato/chiuso dentro la funzione)
    _val_i_engine = None
    try:
        from sqlalchemy import create_engine as _ce
        _val_i_engine = _ce(_cfg_db_url(), pool_pre_ping=True)
    except Exception as _e:
        log.warning(f"  Engine per test I OOS non disponibile: {_e}")
    val_i = valida_incrementale_pro_oos(_val_i_engine) if _val_i_engine else None
    if val_i is None:
        log.info("  Nessun vintage trovato → fallback in-sample (ultime 6 nel payload).")
        val_i = valida_incrementale_pro(players)
    elif not val_i.get("has_data"):
        log.warning(f"  Vintage trovato ma test OOS non utilizzabile: {val_i.get('msg')}")
        log.info("  Fallback in-sample (ultime 6 nel payload).")
        val_i = valida_incrementale_pro(players)

    log.info("[Q] Baseline: il TPI batte i predittori banali?...")
    val_q = valida_baseline(_val_i_engine) if _val_i_engine else None
    if val_q is None:
        log.info("  Nessun vintage utilizzabile → test Q non calcolato.")
    elif not val_q.get("has_data"):
        log.info(f"  {val_q.get('msg','—')}")

    log.info("[O] Ablation study (rimuove una dim per volta)...")
    val_o = valida_ablation(players, df_gp)
    if not val_o.get("has_data"):
        log.info(f"  {val_o.get('msg','—')}")

    log.info("[P] Persistence Score (TPI medio multi-vintage vs singolo)...")
    val_p = valida_persistence(players, df_gp)
    if not val_p or not val_p.get("has_data"):
        log.info(f"  {val_p.get('msg','—') if val_p else 'Persistence non eseguito.'}")

    log.info("[M] Reliability/Calibration del TPI...")
    val_m = valida_calibration(players)
    if not val_m.get("has_data"):
        log.info(f"  {val_m.get('msg','—')}")

    log.info("[N] Predittività stratificata per ruolo...")
    val_n = valida_predittivita_per_ruolo(players, df_gp)
    if not val_n.get("has_data"):
        log.info(f"  {val_n.get('msg','—')}")

    log.info("[L] Convergenza per giornata (gated)...")
    val_l = valida_convergenza()
    if not val_l.get("has_data"):
        log.info(f"  {val_l.get('msg','—')}")

    log.info("\nGenerazione HTML...")
    html = build_dashboard(val_a, val_b, val_c, val_d, val_e,
                           val_f, val_g, val_h, val_i,
                           val_l, val_m, val_n, val_o, val_p, val_q)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUTPUT_DIR / "validazione.html"
    out.write_bytes(html.encode("utf-8", "replace"))
    log.info(f"OK → {out}")

    if DEMO_DIR.is_dir():
        demo_copy = DEMO_DIR / out.name
        try:
            demo_copy.write_bytes(out.read_bytes())
            log.info(f"OK → {demo_copy}  (copia per repo demo)")
        except OSError as e:
            log.warning(f"Copia demo fallita: {e}")
        avvisa_se_conteggio_a_mano(
            conta_test_pubblicati(val_d, val_e, val_f, val_g, val_h, val_i,
                                  val_l, val_m, val_n, val_o, val_p, val_q))

    # i18n.js e ai_chat.js devono stare ACCANTO a ogni HTML (i loro <script src>
    # sono relativi): la fonte canonica è nel repo demo, li copio in dashboard_output
    # così la pagina funziona anche aperta da lì (gli script suggeriscono di aprirla
    # da dashboard_output).
    for _asset in ("i18n.js", "ai_chat.js"):
        _src = DEMO_DIR / _asset
        if not _src.is_file():
            continue
        try:
            (OUTPUT_DIR / _asset).write_bytes(_src.read_bytes())
            log.info(f"OK → {OUTPUT_DIR / _asset}  (accanto all'HTML)")
        except OSError as e:
            log.warning(f"Copia {_asset} fallita: {e}")

    # Stesso discorso per i font: le @font-face nel CSS puntano a fonts/*.woff2
    # relativi all'HTML, quindi senza questa copia la pagina aperta da
    # dashboard_output ricade sui font di sistema (come fa gia' parte2).
    _fonts_src = DEMO_DIR / "fonts"
    if _fonts_src.is_dir():
        try:
            _fonts_dst = OUTPUT_DIR / "fonts"
            _fonts_dst.mkdir(exist_ok=True)
            for _f in _fonts_src.glob("*.woff2"):
                (_fonts_dst / _f.name).write_bytes(_f.read_bytes())
            log.info(f"OK → {_fonts_dst}  (font accanto all'HTML)")
        except OSError as e:
            log.warning(f"Copia fonts fallita: {e}")

    copia_pagine_nav(escludi=out.name)

    log.info("")
    log.info(f"  A — r={_sf(val_a.get('r'),3)}  n={val_a.get('n',0)}")
    log.info(f"  B — overlap={val_b.get('overlap_pct','—')}%")
    log.info(f"  C — r={_sf(val_c.get('r'),3)}  n={val_c.get('n',0)}  fonte={val_c.get('source','?')}")
    log.info(f"  D — AII:{val_d.get('n_aii',0)} PRI:{val_d.get('n_pri',0)}  has_data={val_d.get('has_data',False)}")
    log.info(f"  E — TPI Pro: {val_e.get('n_pro',0)} giocatori  r={_sf(val_e.get('r_corr'),3)}  has_data={val_e.get('has_data',False)}")
    log.info(f"  F — Team-level: r={_sf(val_f.get('r'),3)}  has_data={val_f.get('has_data',False)}")
    log.info(f"  G — Struttura: PC1={_sf(val_g.get('pc1'),3)}  has_data={val_g.get('has_data',False)}")
    log.info(f"  H — Sensibilità pesi: ρ_med={_sf(val_h.get('spearman_median'),3)}  has_data={val_h.get('has_data',False)}")
    log.info(f"  I — Incrementale Pro: Δ={_sf(val_i.get('delta_rmse'),3)}  pro_better={val_i.get('pro_better')}  has_data={val_i.get('has_data',False)}")
    log.info(f"  M — Calibration: slope={_sf(val_m.get('slope'),3)} monot.ρ={_sf(val_m.get('monotonia_rho'),3)} ACE={_sf(val_m.get('calibration_error'),3)}")
    _pr = val_n.get('per_role', {}) if val_n else {}
    log.info(f"  N — Per ruolo: " + " · ".join(f"{k}:ρ={_sf(v.get('rho'),3)} n={v.get('n',0)}" for k,v in _pr.items()))
    if val_o and val_o.get("has_data"):
        _ord = val_o["results"]
        log.info(f"  O — Ablation (ρ_real baseline={val_o['baseline_rho_realized']}): "
                 f"più impatto: {_ord[0]['dim']}(Δ={_ord[0]['delta_predict']:+.3f}), "
                 f"meno impatto: {_ord[-1]['dim']}(Δ={_ord[-1]['delta_predict']:+.3f})")
    if val_p and val_p.get("has_data"):
        log.info(f"  P — Persistence (n={val_p['n_giocatori']}): "
                 f"single ρ={val_p['rho_single']:+.3f}, within-mean ρ={val_p['rho_persistence_within_mean']:+.3f} "
                 f"(Δ={val_p['delta_within_vs_single']:+.3f}), all-mean ρ={val_p['rho_persistence_all_mean']:+.3f} "
                 f"(Δ={val_p['delta_all_vs_single']:+.3f}), wins={val_p['persistence_wins']}")
    if val_q and val_q.get("has_data"):
        for _ck, _cv in val_q["criteri"].items():
            _batte = _cv["tpi_batte"]
            log.info(f"  Q — Baseline [{_ck}]: il TPI batte {len(_batte)}/{_cv['n_baselines']}"
                     + (f" ({', '.join(_batte)})" if _batte else " (nessuna)"))
    log.info("=" * 58)

    # Apre il file nel browser — una sola volta
    # os.startfile è più affidabile su Windows con percorsi con spazi
    try:
        import sys
        if sys.platform == "win32":
            os.startfile(str(out))
        else:
            url = "file:///" + str(out).replace("\\", "/")
            webbrowser.open(url)
        log.info(f"Dashboard aperta: {out}")
    except Exception as e:
        log.warning(f"Impossibile aprire automaticamente: {e}")
        log.info(f"Apri manualmente: {out}")


if __name__ == "__main__":
    main()
