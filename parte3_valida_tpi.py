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
DEMO_DIR   = BASE_DIR / "serie-a-scout-demo"

sys.path.insert(0, str(BASE_DIR))
from config import db_url as _cfg_db_url  # carica .env + fail-fast

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
def load_payload() -> dict:
    if not PAYLOAD.exists():
        raise FileNotFoundError(
            f"payload.json non trovato in {PAYLOAD}\n"
            "Esegui prima: python parte1_analisi.py"
        )
    with open(PAYLOAD, encoding="utf-8") as f:
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
def _interp_corr(r: float) -> str:
    ar = abs(r)
    if ar >= 0.70: return "Correlazione forte (r≥0.7) — il TPI è molto coerente con la percezione fantasy."
    if ar >= 0.50: return "Correlazione moderata (r=0.5–0.7) — risultato ideale: misura qualità reale con prospettiva diversa."
    if ar >= 0.30: return "Correlazione debole (r=0.3–0.5) — il TPI identifica aspetti diversi dal voto Fantacalcio."
    return "Correlazione bassa (r<0.3) — quasi ortogonale al voto fantasy. Valuta componenti difensive."


def valida_correlazione_fanta(players: list) -> dict:
    rows = []
    for p in players:
        nome = p["nome"]
        tpi  = p["tpi"].get("totale")
        voto = FANTA_VOTI.get(nome)
        if tpi is not None and voto is not None:
            rows.append({"nome": nome, "squadra": p["squadra"],
                         "ruolo": p["ruolo"], "tpi": tpi, "voto": voto})
    if len(rows) < 5:
        log.warning(f"  Solo {len(rows)} match in FANTA_VOTI.")
        return {"r": None, "p": None, "n": len(rows), "data": rows,
                "slope": None, "intercept": None,
                "interpretazione": "Dati insufficienti"}
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

    log.info(f"  Pearson r={r:.3f} [{ci_lo:.3f},{ci_hi:.3f}], Spearman ρ={r_sp:.3f}, p={p_val:.4f}, Cohen's d={cohen_d:.2f}")
    return {
        "r": round(float(r), 4), "p": round(float(p_val), 4),
        "r_spearman": round(float(r_sp), 4), "p_spearman": round(float(p_sp), 4),
        "ci_lo": round(ci_lo, 3), "ci_hi": round(ci_hi, 3),
        "cohen_d": round(cohen_d, 3),
        "n": len(df), "slope": round(float(sl), 4), "intercept": round(float(ic), 4),
        "data": rows, "interpretazione": _interp_corr(r),
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

    div_pos, div_neg = [], []
    for i, p in enumerate(sorted_tpi):
        ws = WHOSCORED.get(p["nome"])
        if not ws:
            continue
        ws_rank  = next((j + 1 for j, r in enumerate(ws_rows) if r["nome"] == p["nome"]), None)
        tpi_rank = i + 1
        if ws_rank and (ws_rank - tpi_rank) >= 4:
            div_pos.append({"nome": p["nome"], "squadra": p["squadra"],
                            "tpi_rank": tpi_rank, "ws_rank": ws_rank,
                            "tpi": round(p["tpi"]["totale"], 3), "ws": ws})
        elif ws_rank and (tpi_rank - ws_rank) >= 4:
            div_neg.append({"nome": p["nome"], "squadra": p["squadra"],
                            "tpi_rank": tpi_rank, "ws_rank": ws_rank,
                            "tpi": round(p["tpi"]["totale"], 3), "ws": ws})

    log.info(f"  Overlap: {overlap_pct}% ({len(overlap)}/10)")
    return {
        "overlap_pct": overlap_pct,
        "overlap_names": list(overlap),
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
def _interp_backtest(r) -> str:
    if r is None: return "Dati insufficienti."
    if r >= 0.65: return "Forte stabilità predittiva (r≥0.65) — il TPI cattura qualità stabile oltre il rumore."
    if r >= 0.45: return "Buona stabilità predittiva (r=0.45–0.65) — segnale reale con varianza residua attesa."
    if r >= 0.30: return "Stabilità moderata (r=0.3–0.45) — potere predittivo parziale."
    return "Stabilità bassa (r<0.3) — sensibile alla forma del momento. Considera di aumentare K_base."


def valida_backtest_db(df_gp: pd.DataFrame) -> dict:
    all_gg = sorted(df_gp["giornata"].unique())
    if len(all_gg) < 8:
        return {"r": None, "p": None, "n": 0, "scatter": [], "source": "db",
                "slope": None, "intercept": None,
                "early_range": "—", "late_range": "—",
                "interpretazione": "Troppo poche giornate"}
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
                "interpretazione": "Pochi giocatori in comune"}

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
    log.info(f"  Spearman ρ={r:.3f} [{ci_lo:.3f},{ci_hi:.3f}], Kendall τ={tau:.3f}, RMSE={rmse:.4f}")
    return {
        "r": round(float(r), 4), "p": round(float(p_val), 4),
        "r_pearson": round(float(r_pearson), 4),
        "tau": round(float(tau), 4), "p_tau": round(float(p_tau), 4),
        "ci_lo": round(ci_lo, 3), "ci_hi": round(ci_hi, 3),
        "rmse": round(rmse, 4), "mae": round(mae, 4),
        "n": len(common),
        "slope": round(float(sl), 4), "intercept": round(float(ic), 4),
        "early_range": f"gg {early_gg[0]}-{early_gg[-1]}",
        "late_range":  f"gg {late_gg[0]}-{late_gg[-1]}",
        "scatter": scatter, "source": "db",
        "interpretazione": _interp_backtest(r),
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
                "interpretazione": "Dati insufficienti"}
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
        "interpretazione": _interp_backtest(r),
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
# TEMPLATE CSS
# ════════════════════════════════════════════════════════════════
CSS = """
:root{
  --bg:#000;--bg2:#1c1c1e;--bg3:#2c2c2e;
  --sep:rgba(255,255,255,.10);--sep2:rgba(255,255,255,.18);
  --lp:#fff;--ls:rgba(235,235,245,.62);--lt:rgba(235,235,245,.32);
  --blue:#0a84ff;--green:#30d158;--orng:#ff9f0a;
  --red:#ff453a;--purp:#bf5af2;--teal:#5ac8fa;--indig:#5e5ce6;
  --r:16px;--rsm:12px;--rxs:8px;
  --font:-apple-system,BlinkMacSystemFont,"SF Pro Display","Helvetica Neue",sans-serif;
  --mono:"SF Mono","Cascadia Code","Fira Code",monospace;
  --gl-border:rgba(255,255,255,.16);--gl-edge:rgba(255,255,255,.28);
  --gl-blur:saturate(220%) blur(36px);
}
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
html{scroll-behavior:smooth}
body{font-family:var(--font);background:var(--bg);color:var(--lp);
  font-size:15px;line-height:1.47;-webkit-font-smoothing:antialiased;overflow-x:hidden}
::-webkit-scrollbar{width:3px}::-webkit-scrollbar-thumb{background:var(--bg3);border-radius:2px}

/* Nav */
.nav{position:sticky;top:0;z-index:400;height:52px;
  background:rgba(10,10,12,.72);backdrop-filter:var(--gl-blur);
  -webkit-backdrop-filter:var(--gl-blur);border-bottom:1px solid var(--gl-border);
  box-shadow:0 1px 0 var(--gl-edge);display:flex;align-items:center;gap:10px;padding:0 20px}
.nav-brand{font-size:16px;font-weight:700;letter-spacing:-.5px;white-space:nowrap;flex-shrink:0}
.nav-brand small{font-size:12px;font-weight:400;color:var(--lt);margin-left:6px}
.nav-glass-btn{
  display:inline-flex;align-items:center;justify-content:center;
  width:30px;height:30px;background:rgba(255,255,255,.06);
  backdrop-filter:saturate(180%) blur(20px);-webkit-backdrop-filter:saturate(180%) blur(20px);
  border:1px solid rgba(255,255,255,.14);border-top-color:rgba(255,255,255,.24);
  border-radius:9px;font-size:14px;color:rgba(235,235,245,.62);cursor:pointer;
  box-shadow:0 2px 8px rgba(0,0,0,.3),inset 0 1px 0 rgba(255,255,255,.08);
  transition:all .18s cubic-bezier(.4,0,.2,1);font-family:var(--font);text-decoration:none;flex-shrink:0}
.nav-glass-btn:hover{background:rgba(255,255,255,.11);color:#fff;
  border-top-color:rgba(255,255,255,.36);transform:translateY(-1px);
  box-shadow:0 3px 14px rgba(0,0,0,.4),inset 0 1px 0 rgba(255,255,255,.12)}
.nav-glass-btn:active{transform:translateY(0);opacity:.8}
.nav-glass-btn.home-btn{width:auto;padding:0 12px;gap:5px;font-size:12px;font-weight:600}
.nav-switch-btn{
  display:inline-flex;align-items:center;gap:6px;height:30px;padding:0 12px;
  background:rgba(10,132,255,.08);
  backdrop-filter:saturate(180%) blur(20px);-webkit-backdrop-filter:saturate(180%) blur(20px);
  border:1px solid rgba(10,132,255,.22);border-top-color:rgba(10,132,255,.35);
  border-radius:9px;font:12px/1 var(--font);font-weight:600;color:var(--blue);
  text-decoration:none;cursor:pointer;flex-shrink:0;white-space:nowrap;
  box-shadow:0 2px 8px rgba(0,0,0,.3),inset 0 1px 0 rgba(255,255,255,.07);
  transition:all .18s cubic-bezier(.4,0,.2,1)}
.nav-switch-btn:hover{background:rgba(10,132,255,.15);color:#4da3ff;transform:translateY(-1px)}
.nav-switch-dot{width:5px;height:5px;border-radius:50%;background:var(--blue);
  box-shadow:0 0 5px rgba(10,132,255,.7);flex-shrink:0}
.nav-btn-group{display:flex;align-items:center;gap:5px;margin-left:8px}
.nav-right-group{display:flex;align-items:center;gap:8px;margin-left:auto}

/* Bottoni nav unificati — stesso stile della dashboard */
.nav-home-btn{
  display:inline-flex;align-items:center;gap:6px;height:30px;padding:0 13px;
  border-radius:9px;font:12px var(--font);font-weight:700;cursor:pointer;
  background:rgba(10,132,255,.1);border:1px solid rgba(10,132,255,.3);
  border-top-color:rgba(10,132,255,.45);color:var(--blue);
  box-shadow:0 2px 8px rgba(10,132,255,.15),inset 0 1px 0 rgba(255,255,255,.08);
  transition:all .18s;white-space:nowrap;text-decoration:none;flex-shrink:0}
.nav-home-btn:hover{background:rgba(10,132,255,.18);transform:translateY(-1px)}
.nav-orng-btn{
  display:inline-flex;align-items:center;gap:6px;height:30px;padding:0 13px;
  border-radius:9px;font:12px var(--font);font-weight:700;text-decoration:none;
  background:rgba(255,159,10,.12);border:1px solid rgba(255,159,10,.35);
  border-top-color:rgba(255,159,10,.5);color:var(--orng);
  box-shadow:0 2px 8px rgba(255,159,10,.15),inset 0 1px 0 rgba(255,255,255,.07);
  transition:all .18s;white-space:nowrap;flex-shrink:0}
.nav-orng-btn:hover{background:rgba(255,159,10,.2);transform:translateY(-1px)}

@media(max-width:768px){
  .nav-brand{display:none}
  .nav{justify-content:flex-start}
  .nav-btn-group{margin-left:0}
  .nav-right-group{margin-left:auto}
  .hp-label,.home-lbl{display:none}
  .nav-home-btn,.nav-orng-btn{padding:0 9px;gap:4px}
}
@media(max-width:480px){
  .nav{height:46px;padding:0 8px;gap:5px}
  .nav-glass-btn{width:28px;height:28px}
  .nav-home-btn,.nav-orng-btn{height:28px;padding:0 8px}
}

/* Hero */
.hero{padding:36px 20px 28px;
  background:linear-gradient(180deg,rgba(10,132,255,.06) 0%,transparent 100%);
  border-bottom:1px solid var(--sep)}
.hero-ttl{font-size:clamp(22px,4vw,34px);font-weight:800;letter-spacing:-1.5px;margin-bottom:8px}
.hero-sub{font-size:14px;color:var(--lt);max-width:640px;line-height:1.6;margin-bottom:16px}
.hero-pills{display:flex;gap:8px;flex-wrap:wrap}
.hero-pill{padding:5px 12px;border-radius:20px;font-size:12px;font-weight:600;
  background:rgba(255,255,255,.05);border:1px solid var(--gl-border);color:var(--ls)}

/* Card */
.card{background:linear-gradient(160deg,rgba(255,255,255,.07) 0%,rgba(255,255,255,.03) 100%);
  border-radius:var(--r);border:1px solid var(--gl-border);border-top-color:var(--gl-edge);
  box-shadow:0 4px 20px rgba(0,0,0,.45),inset 0 1px 0 rgba(255,255,255,.08);padding:18px}
.card-ttl{font-size:11px;font-weight:700;color:var(--lt);text-transform:uppercase;
  letter-spacing:.7px;margin-bottom:14px;display:flex;align-items:center;gap:6px}

/* Layout */
.main{max-width:1100px;margin:0 auto;padding:20px}
.section{margin-bottom:48px}
.section-hd{display:flex;align-items:flex-start;gap:14px;margin-bottom:18px}
.section-num{width:32px;height:32px;border-radius:50%;display:flex;align-items:center;
  justify-content:center;font-size:14px;font-weight:700;color:#fff;flex-shrink:0;margin-top:2px}
.section-ttl{font-size:19px;font-weight:700;letter-spacing:-.4px}
.section-sub{font-size:13px;color:var(--lt);margin-top:3px;line-height:1.5}
.g2{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-bottom:14px}
.g3{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-bottom:14px}
.g4{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-bottom:14px}

/* Stat box */
.stat-box{background:linear-gradient(150deg,rgba(255,255,255,.06),rgba(255,255,255,.02));
  border-radius:var(--rsm);border:1px solid var(--gl-border);border-top-color:var(--gl-edge);
  padding:16px;text-align:center;position:relative;overflow:hidden}
.stat-box::before{content:"";position:absolute;top:0;left:0;right:0;height:2px}
.sb-blue::before{background:linear-gradient(90deg,var(--blue),var(--indig))}
.sb-green::before{background:linear-gradient(90deg,var(--green),var(--teal))}
.sb-orng::before{background:linear-gradient(90deg,var(--orng),var(--red))}
.sb-purp::before{background:linear-gradient(90deg,var(--purp),var(--indig))}
.sb-teal::before{background:linear-gradient(90deg,var(--teal),var(--blue))}
.stat-val{font-size:36px;font-weight:800;letter-spacing:-2px;line-height:1;font-family:var(--mono)}
.stat-lbl{font-size:10px;color:var(--lt);text-transform:uppercase;letter-spacing:.6px;margin-top:6px}
.stat-sub{font-size:11px;color:var(--ls);margin-top:3px;line-height:1.4}

/* Help / Modal */
.help{display:inline-flex;align-items:center;justify-content:center;
  width:16px;height:16px;border-radius:50%;background:rgba(255,255,255,.09);
  border:1px solid var(--sep);font-size:9px;color:var(--lt);cursor:pointer;
  transition:all .15s;flex-shrink:0;vertical-align:middle;margin-left:4px}
.help:hover{background:var(--blue);color:#fff;border-color:var(--blue)}
.mwrap{position:fixed;inset:0;z-index:600;background:rgba(0,0,0,.75);
  backdrop-filter:blur(16px);-webkit-backdrop-filter:blur(16px);
  display:none;align-items:center;justify-content:center;padding:16px}
.mwrap.open{display:flex}
.mbox{background:rgba(22,22,24,.95);backdrop-filter:saturate(220%) blur(48px);
  -webkit-backdrop-filter:saturate(220%) blur(48px);
  border:1px solid var(--gl-border);border-top-color:var(--gl-edge);
  box-shadow:0 32px 80px rgba(0,0,0,.9),inset 0 1px 0 rgba(255,255,255,.12);
  border-radius:20px;padding:26px;max-width:520px;width:100%}
.mbox-icon{font-size:26px;margin-bottom:10px}
.mbox-ttl{font-size:19px;font-weight:700;letter-spacing:-.4px;margin-bottom:5px}
.mbox-sub{font-size:12px;color:var(--lt);margin-bottom:14px;
  font-family:var(--mono);background:rgba(255,255,255,.04);
  border:1px solid var(--sep);border-radius:var(--rxs);padding:7px 11px}
.mbox-body{font-size:13px;color:var(--ls);line-height:1.75;margin-bottom:10px}
.mbox-ex{font-size:12px;color:var(--lt);border-left:3px solid var(--blue);
  padding:8px 14px;line-height:1.6;background:rgba(10,132,255,.04);
  border-radius:0 var(--rxs) var(--rxs) 0}
.mbox-cls{margin-top:18px;width:100%;padding:12px;background:rgba(255,255,255,.07);
  border:1px solid var(--sep);border-radius:var(--rsm);color:var(--lp);
  font:14px var(--font);cursor:pointer;transition:background .15s}
.mbox-cls:hover{background:rgba(255,255,255,.11)}

/* Table */
.interp{background:rgba(255,255,255,.03);border:1px solid var(--sep);
  border-left:3px solid var(--blue);border-radius:0 var(--rsm) var(--rsm) 0;
  padding:12px 16px;font-size:13px;color:var(--ls);line-height:1.65;margin-top:12px}
.nota-warn{background:rgba(255,159,10,.06);border:1px solid rgba(255,159,10,.2);
  border-radius:var(--rsm);padding:10px 14px;font-size:12px;color:var(--orng);
  line-height:1.6;margin-bottom:14px}
.table-wrap{overflow-x:auto;-webkit-overflow-scrolling:touch;margin-top:4px}
table{width:100%;border-collapse:collapse;font-size:13px;min-width:320px}
th{text-align:left;padding:8px 10px;font-size:10px;font-weight:700;color:var(--lt);
  text-transform:uppercase;letter-spacing:.6px;border-bottom:1px solid var(--sep);white-space:nowrap}
td{padding:8px 10px;border-bottom:1px solid rgba(255,255,255,.04);color:var(--ls)}
tr:hover td{background:rgba(255,255,255,.02)}
.tv{font-family:var(--mono);font-weight:600;color:var(--lp)}
.torng{color:var(--orng);font-weight:600;font-family:var(--mono)}

/* Badges */
.badge{display:inline-flex;align-items:center;gap:4px;padding:3px 9px;
  border-radius:7px;font-size:11px;font-weight:600}
.badge-green{background:rgba(48,209,88,.1);color:var(--green);border:1px solid rgba(48,209,88,.25)}
.badge-orng{background:rgba(255,159,10,.1);color:var(--orng);border:1px solid rgba(255,159,10,.25)}
.badge-red{background:rgba(255,69,58,.08);color:var(--red);border:1px solid rgba(255,69,58,.2)}
.badge-blue{background:rgba(10,132,255,.1);color:var(--blue);border:1px solid rgba(10,132,255,.25)}
.badge-purp{background:rgba(191,90,242,.1);color:var(--purp);border:1px solid rgba(191,90,242,.25)}
.badge-teal{background:rgba(90,200,250,.1);color:var(--teal);border:1px solid rgba(90,200,250,.25)}

/* Accordion */
.accordion{border:1px solid var(--gl-border);border-radius:var(--r);overflow:hidden;margin-bottom:28px}
.acc-hd{padding:14px 18px;display:flex;align-items:center;justify-content:space-between;
  cursor:pointer;background:rgba(255,255,255,.03);transition:background .15s}
.acc-hd:hover{background:rgba(255,255,255,.05)}
.acc-title{font-size:14px;font-weight:600;display:flex;align-items:center;gap:8px}
.acc-chev{font-size:11px;color:var(--lt);transition:transform .2s;flex-shrink:0}
.acc-body{display:none;padding:18px;border-top:1px solid var(--sep)}
.acc-body.open{display:block}
.guide-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:10px}
.guide-card{background:rgba(255,255,255,.03);border:1px solid var(--sep);
  border-radius:var(--rsm);padding:13px}
.guide-icon{font-size:18px;margin-bottom:7px}
.guide-ttl{font-size:13px;font-weight:600;margin-bottom:5px}
.guide-body{font-size:12px;color:var(--lt);line-height:1.65}

/* Recap layout */
.recap-outer{display:flex;gap:20px;align-items:flex-start;flex-wrap:wrap;margin-top:20px}
.recap-left{flex:1;min-width:260px;
  background:linear-gradient(135deg,rgba(10,132,255,.06),rgba(94,92,230,.03));
  border:1px solid rgba(10,132,255,.18);border-radius:var(--r);padding:20px}
.recap-right{width:260px;flex-shrink:0;
  background:linear-gradient(135deg,rgba(191,90,242,.10),rgba(94,92,230,.06));
  border:2px solid rgba(191,90,242,.35);border-radius:var(--r);padding:20px;
  position:relative;overflow:hidden}
.recap-right::before{content:"";position:absolute;top:0;left:0;right:0;height:3px;
  background:linear-gradient(90deg,var(--purp),var(--indig))}
.rc-grid{display:grid;grid-template-columns:1fr 1fr;gap:10px}
.rc-card{background:rgba(255,255,255,.03);border:1px solid var(--sep);
  border-radius:var(--rsm);padding:11px 13px}
.rc-lbl{font-size:11px;color:var(--lt);margin-bottom:7px;line-height:1.4}
.rc-sub{font-size:11px;color:var(--ls);margin-top:5px}
@media(max-width:720px){
  .recap-outer{flex-direction:column}
  .recap-right{width:100%}
  .rc-grid{grid-template-columns:1fr}
}
@media(max-width:480px){
  .rc-grid{grid-template-columns:1fr 1fr}
}

/* Mover rows */
.mover-row{display:flex;align-items:center;gap:10px;padding:9px 12px;
  border-radius:10px;margin-bottom:6px;
  background:rgba(255,255,255,.03);border:1px solid rgba(255,255,255,.07)}
.mover-delta{font-size:13px;font-weight:800;font-family:var(--mono);
  width:44px;text-align:center;flex-shrink:0}
.mover-info{flex:1;min-width:0}
.mover-nm{font-size:13px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.mover-sub{font-size:11px;color:var(--lt);margin-top:1px}
.mover-scores{display:flex;gap:8px;flex-shrink:0}
.mover-s{text-align:center;font-family:var(--mono);font-size:11px}
.mover-s-lbl{font-size:9px;color:var(--lt);text-transform:uppercase;letter-spacing:.4px}

/* Watermark */
#wm{position:fixed;bottom:14px;left:50%;transform:translateX(-50%);
  display:flex;align-items:center;gap:7px;padding:5px 14px 5px 10px;
  background:rgba(255,255,255,.04);backdrop-filter:saturate(180%) blur(20px);
  -webkit-backdrop-filter:saturate(180%) blur(20px);
  border:1px solid rgba(255,255,255,.09);border-top-color:rgba(255,255,255,.14);
  border-radius:20px;z-index:800;pointer-events:none}
#wm-dot{width:6px;height:6px;border-radius:50%;background:var(--blue);
  box-shadow:0 0 6px rgba(10,132,255,.6);flex-shrink:0}
#wm-text{font-size:10px;font-weight:500;letter-spacing:.3px;
  color:rgba(235,235,245,.28);white-space:nowrap}

/* ── RESPONSIVE ── */
@media (max-width: 900px) {
  .g4{grid-template-columns:1fr 1fr}
  .guide-grid{grid-template-columns:1fr 1fr}
}
@media (max-width: 720px) {
  .nav{padding:0 12px;height:48px}
  .nav-brand small{display:none}
  .nav-right-group{gap:5px}
  .nav-switch-btn span:not(.nav-switch-dot){display:none}
  .nav-switch-btn{padding:0 8px}
  .hero{padding:22px 14px 18px}
  .hero-ttl{font-size:22px}
  .hero-sub{font-size:13px}
  .hero-pill{font-size:11px;padding:4px 9px}
  .main{padding:14px}
  .g2{grid-template-columns:1fr}
  .g3{grid-template-columns:1fr 1fr}
  .g4{grid-template-columns:1fr 1fr}
  .guide-grid{grid-template-columns:1fr 1fr}
  .stat-val{font-size:28px}
  .section-ttl{font-size:17px}
  .card{padding:14px}
  .recap-card > .g3{grid-template-columns:1fr}
  .mover-row{flex-wrap:wrap;gap:6px}
  .mover-scores{width:100%;justify-content:flex-end}
}
@media (max-width: 480px) {
  .nav{padding:0 10px;gap:5px;height:46px}
  .nav-brand{font-size:13px}
  .nav-glass-btn.home-btn{padding:0 8px}
  .hero{padding:16px 12px 14px}
  .hero-ttl{font-size:19px;letter-spacing:-1px}
  .hero-pill{display:none}
  .hero-pills .hero-pill:first-child{display:flex}
  .main{padding:10px}
  .section{margin-bottom:32px}
  .section-hd{gap:10px}
  .section-num{width:28px;height:28px;font-size:12px}
  .section-ttl{font-size:16px}
  .g2,.g3,.g4{grid-template-columns:1fr}
  .guide-grid{grid-template-columns:1fr}
  .stat-val{font-size:26px}
  .stat-lbl{font-size:9px}
  .acc-hd{padding:12px 14px}
  .acc-title{font-size:13px}
  .acc-body{padding:12px}
  .card{padding:12px}
  .card-ttl{font-size:10px}
  table{font-size:12px}
  th{padding:6px 8px;font-size:9px}
  td{padding:7px 8px}
  .mbox{padding:18px 14px;border-radius:16px}
  .mbox-ttl{font-size:17px}
  .mbox-body{font-size:12px}
  .recap-card{padding:14px}
  .mover-nm{font-size:12px}
  .mover-delta{width:36px;font-size:12px}
}
/* Fix autozoom input iOS */
@media (max-width: 768px){input,select,textarea{font-size:16px !important}}
/* Resize Plotly */
@media (max-width:480px){
  .chart-h{height:240px !important}
}
"""


# ════════════════════════════════════════════════════════════════
# BUILD DASHBOARD HTML
# ════════════════════════════════════════════════════════════════
def build_dashboard(val_a: dict, val_b: dict, val_c: dict,
                    val_d: dict | None = None, val_e: dict | None = None) -> str:

    def pval(p) -> str:
        if p is None: return ""
        if p < 0.001: return "p &lt; 0.001 &#10003;&#10003;&#10003;"
        if p < 0.01:  return f"p = {p:.3f} &#10003;&#10003;"
        if p < 0.05:  return f"p = {p:.3f} &#10003;"
        return f"p = {p:.3f} (non significativo)"

    def rcol(r) -> str:
        if r is None: return "var(--lt)"
        return "var(--green)" if abs(r) >= 0.6 else "var(--orng)" if abs(r) >= 0.4 else "var(--red)"

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
    r2_a      = _sf(r_a ** 2 if r_a else None, 3)

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
                f'<td style="font-family:var(--mono);color:var(--teal);font-weight:700">{aii_s}</td>'
                f'<td style="font-family:var(--mono);font-size:12px">{eta_s}aa</td>'
                f'<td style="font-family:var(--mono)">{tpi_s}</td></tr>'
            )
        return h

    scatter_d_js = j(vd.get("scatter", []))

    if has_v2:
        v2_section = f"""
<div class="section">
  <div class="section-hd">
    <div class="section-num" style="background:var(--teal)">D</div>
    <div>
      <div class="section-ttl">Age Index &amp; Physical Reliability <span class="help" onclick="openM('v2')">?</span></div>
      <div class="section-sub">Valida AII (Et&agrave; Index) e PRI (Physical Reliability Index).</div>
    </div>
  </div>
  <div class="g3">
    <div class="stat-box sb-teal"><div class="stat-val" style="color:var(--teal)">{n_aii}</div><div class="stat-lbl">Giocatori con AII</div><div class="stat-sub">r(AII,TPI) = {_sf(aii_r,3)}</div></div>
    <div class="stat-box sb-purp"><div class="stat-val" style="color:var(--purp)">{n_pri}</div><div class="stat-lbl">Giocatori con PRI</div><div class="stat-sub">r(TPI_ext,TPI) = {_sf(ext_r,3)}</div></div>
    <div class="stat-box sb-blue"><div class="stat-val" style="color:var(--teal)">{_sf(eta_mean,1)}</div><div class="stat-lbl">Et&agrave; media lega</div><div class="stat-sub">anni ATT+CEN qualificati</div></div>
  </div>
  <div class="g2">
    <div class="card"><div class="card-ttl">Top 5 Affidabilit&agrave; Fisica (PRI)</div>
      <div class="table-wrap"><table><tr><th>Giocatore</th><th>Squadra</th><th>PRI</th><th>Infortuni</th></tr>
      <tbody>{v2_pri_rows(top_pri, "var(--green)")}</tbody></table></div></div>
    <div class="card"><div class="card-ttl">Bottom 5 &mdash; Pi&ugrave; Fragili</div>
      <div class="table-wrap"><table><tr><th>Giocatore</th><th>Squadra</th><th>PRI</th><th>Infortuni</th></tr>
      <tbody>{v2_pri_rows(bot_pri, "var(--red)")}</tbody></table></div></div>
  </div>
  <div class="g2">
    <div class="card"><div class="card-ttl">Top 5 Et&agrave; Index (AII)</div>
      <div class="table-wrap"><table><tr><th>Giocatore</th><th>Squadra</th><th>AII</th><th>Et&agrave;</th><th>TPI</th></tr>
      <tbody>{v2_aii_rows(top_aii)}</tbody></table>
      <div class="interp">&#128161; AII alto + TPI in crescita = profilo scout ideale per investimento a lungo termine.</div></div>
    <div class="card"><div class="card-ttl">Scatter AII vs PRI <span class="help" onclick="openM('scatter_d')">?</span></div>
      <div id="chart-d" class="chart-h" style="height:280px"></div></div>
  </div>
</div>"""
    elif v2_msg:
        v2_section = f'<div class="nota-warn" style="margin-top:20px">&#9888; {v2_msg}</div>'
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
                <div class="mover-sub">{m['squadra']} &middot; {m['ruolo']}</div>
              </div>
              <div class="mover-scores">
                <div class="mover-s"><div class="mover-s-lbl">TPI</div><div style="color:var(--orng);font-family:var(--mono);font-size:11px;font-weight:700">{tpi_s}</div></div>
                <div class="mover-s"><div class="mover-s-lbl">PRO</div><div style="color:var(--purp);font-family:var(--mono);font-size:11px;font-weight:700">{pro_s}</div></div>
                <div class="mover-s"><div class="mover-s-lbl">AII</div><div style="color:var(--teal);font-family:var(--mono);font-size:11px">{aii_s}</div></div>
                <div class="mover-s"><div class="mover-s-lbl">PRI</div><div style="color:var(--purp);font-family:var(--mono);font-size:11px">{pri_s}</div></div>
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
    <div class="section-num" style="background:var(--purp)">E</div>
    <div>
      <div class="section-ttl">TPI Pro Validation — 6-Dimension Model <span class="help" onclick="openM('tpi_pro')">?</span></div>
      <div class="section-sub">Confronto ranking TPI classico vs TPI Pro (6 dimensioni). Chi guadagna/perde posizioni?</div>
    </div>
  </div>
  <div class="g4">
    <div class="stat-box sb-purp"><div class="stat-val" style="color:var(--purp)">{n_pro}</div><div class="stat-lbl">Giocatori TPI Pro</div><div class="stat-sub">con AII + PRI</div></div>
    <div class="stat-box sb-blue"><div class="stat-val" style="color:{rcol(r_pro)}">{_sf(r_pro,3)}</div><div class="stat-lbl">r(TPI,TPI Pro)</div><div class="stat-sub">{pval(p_pro)}</div></div>
    <div class="stat-box sb-green"><div class="stat-val" style="color:var(--green)">{n_dp}</div><div class="stat-lbl">Salgono &gt;2 pos</div><div class="stat-sub">valorizzati da AII/PRI</div></div>
    <div class="stat-box" style="border-color:rgba(255,69,58,.2)"><div class="stat-val" style="color:var(--red)">{n_dn}</div><div class="stat-lbl">Scendono &gt;2 pos</div><div class="stat-sub">penalizzati da AII/PRI</div></div>
  </div>
  <div class="g2">
    <div class="card"><div class="card-ttl">Scatter TPI vs TPI Pro <span class="help" onclick="openM('scatter_e')">?</span></div>
      <div id="chart-e" class="chart-h" style="height:300px"></div></div>
    <div class="card"><div class="card-ttl">Interpretazione</div>
      <div style="font-size:13px;color:var(--ls);line-height:1.75">
        <p style="margin-bottom:10px">Un <strong style="color:var(--purp)">r elevato</strong> (es. 0.85+) indica che TPI Pro &egrave; coerente con TPI classico — aggiunge informazione senza stravolgere la classifica.</p>
        <p style="margin-bottom:10px">Le <strong style="color:var(--green)">salite</strong> identificano giocatori giovani e fisicamente affidabili che il TPI classico sottovaluta.</p>
        <p>Le <strong style="color:var(--red)">discese</strong> segnalano veterani o giocatori fragili che il TPI classico sovrastima.</p>
      </div>
      <div class="interp">&#128161; r(TPI,TPI Pro) ideale: 0.80&ndash;0.95. Troppo basso = AII/PRI distorcono. Troppo alto = non aggiungono nulla di nuovo.</div>
    </div>
  </div>
  <div class="g2">
    <div class="card">
      <div class="card-ttl" style="color:var(--green)">&#9650; Chi sale con TPI Pro (top 8)</div>
      <div id="movers-up">{mover_rows(top_sal, "var(--green)", "▲")}</div>
    </div>
    <div class="card">
      <div class="card-ttl" style="color:var(--red)">&#9660; Chi scende con TPI Pro (top 8)</div>
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
  var rc={{"ATT":"#ff9f0a","CEN":"#30d158","DIF":"#0a84ff","":"#48484a"}};
  Plotly.newPlot("chart-e",[
    {{type:"scatter",mode:"markers",x:xv,y:yv,
      text:sc.map(d=>d.nome+"<br>"+d.squadra),
      hovertemplate:"%{{text}}<br>TPI: %{{x:.3f}}<br>TPI Pro: %{{y:.3f}}<extra></extra>",
      marker:{{color:sc.map(d=>rc[d.ruolo]||"#636366"),size:8,opacity:.82,
        line:{{color:"rgba(255,255,255,.12)",width:1}}}}}},
    {{type:"scatter",mode:"lines",
      x:[xmin,xmax],y:[sl*xmin+ic,sl*xmax+ic],
      line:{{color:"rgba(191,90,242,.55)",width:2,dash:"dot"}},hoverinfo:"skip"}},
    {{type:"scatter",mode:"lines",
      x:[Math.min(xmin,Math.min(...yv)),Math.max(xmax,Math.max(...yv))],
      y:[Math.min(xmin,Math.min(...yv)),Math.max(xmax,Math.max(...yv))],
      line:{{color:"rgba(255,255,255,.07)",width:1,dash:"dot"}},hoverinfo:"skip"}},
  ],{{...BL,
    xaxis:{{...BL.xaxis,title:"TPI Classico (4 dim)"}},
    yaxis:{{...BL.yaxis,title:"TPI Pro (6 dim)"}},
    margin:{{t:8,b:46,l:54,r:8}},height:300,showlegend:false,
    annotations:[{{x:.02,y:.97,xref:"paper",yref:"paper",
      text:"r = {_sf(r_pro,3)}",showarrow:false,
      font:{{color:"rgba(235,235,245,.6)",size:13}},align:"left"}}]}},PL);
}})();"""
    elif pro_msg:
        e_section = f'<div class="nota-warn" style="margin-top:16px">&#9888; {pro_msg}</div>'
        e_js = ""
    else:
        e_section = ""
        e_js = ""

    # ── Badge recap ───────────────────────────────────────────────
    # Costruiamo le 4 card standard (A B C D)
    def _badge_html(badge_id: str, label: str, val, hi: float, mid: float,
                    sub: str, color_override: str = "") -> str:
        if val is None:
            cls, lbl = "badge-blue", "N/D"
        elif abs(val) >= hi:
            cls, lbl = "badge-green", "Buono &#10003;"
        elif abs(val) >= mid:
            cls, lbl = "badge-orng", "Moderato"
        else:
            cls, lbl = "badge-red", "Basso"
        accent = f"border-left:3px solid {color_override};" if color_override else ""
        return f"""<div class="rc-card" style="{accent}">
          <div class="rc-lbl">{label}</div>
          <div id="{badge_id}"><span class="badge {cls}">{lbl}</span></div>
          <div class="rc-sub">{sub}</div>
        </div>"""

    recap_abcd = (
        _badge_html("badge-a", "A &mdash; Correlazione Fantacalcio",
                    r_a, 0.6, 0.4,
                    f"r = {_sf(r_a,3)} &middot; n = {n_a}") +
        _badge_html("badge-b", "B &mdash; Overlap Top 10 WhoScored",
                    (ov / 100) if ov else None, 0.7, 0.5,
                    f"{ov}% coincidenza") +
        _badge_html("badge-c", "C &mdash; Backtest Predittivo",
                    r_c, 0.5, 0.3,
                    f"r = {_sf(r_c,3)} &middot; n = {n_c}")
    )

    # D — solo se ha dati
    if has_v2:
        recap_abcd += _badge_html(
            "badge-d", "D &mdash; Et&agrave; &amp; Fisico",
            vd.get("aii_tpi_r"), 0.5, 0.3,
            f"AII:{n_aii} &middot; PRI:{n_pri} &middot; r={_sf(vd.get('aii_tpi_r'),3)}",
            "var(--teal)"
        )

    # E — pannello separato in evidenza (sempre mostrato, anche se no dati)
    if has_pro:
        r_pro_v   = ve.get("r_corr")
        pro_cls   = "badge-green" if (r_pro_v or 0) >= 0.80 else "badge-orng" if (r_pro_v or 0) >= 0.70 else "badge-red"
        pro_lbl   = "Ottimo &#10003;" if (r_pro_v or 0) >= 0.80 else "Buono" if (r_pro_v or 0) >= 0.70 else "Coerente"
        e_recap_inner = f"""
          <div style="display:flex;align-items:center;gap:8px;margin-bottom:10px">
            <span class="badge {pro_cls}">{pro_lbl}</span>
            <span style="font-size:11px;color:var(--lt)">r(TPI, TPI Pro)</span>
          </div>
          <div style="font-size:22px;font-weight:800;letter-spacing:-1px;
                      font-family:var(--mono);color:var(--purp);line-height:1;margin-bottom:6px">
            {_sf(r_pro_v,3)}
          </div>
          <div style="font-size:11px;color:var(--lt);margin-bottom:12px">
            {ve.get("n_pro",0)} giocatori con AII+PRI
          </div>
          <div style="display:flex;gap:8px;flex-wrap:wrap">
            <div style="flex:1;min-width:60px;background:rgba(48,209,88,.06);
                        border:1px solid rgba(48,209,88,.2);border-radius:8px;
                        padding:7px 10px;text-align:center">
              <div style="font-size:16px;font-weight:800;color:var(--green);
                          font-family:var(--mono)">{ve.get("n_delta_pos",0)}</div>
              <div style="font-size:9px;color:var(--lt);text-transform:uppercase;
                          letter-spacing:.5px;margin-top:2px">Salgono</div>
            </div>
            <div style="flex:1;min-width:60px;background:rgba(255,69,58,.05);
                        border:1px solid rgba(255,69,58,.18);border-radius:8px;
                        padding:7px 10px;text-align:center">
              <div style="font-size:16px;font-weight:800;color:var(--red);
                          font-family:var(--mono)">{ve.get("n_delta_neg",0)}</div>
              <div style="font-size:9px;color:var(--lt);text-transform:uppercase;
                          letter-spacing:.5px;margin-top:2px">Scendono</div>
            </div>
          </div>"""
    else:
        e_recap_inner = f"""
          <div style="font-size:12px;color:var(--lt);line-height:1.6">
            Popola <code style="font-family:var(--mono);color:var(--purp)">t_infortuni</code>
            e <code style="font-family:var(--mono);color:var(--teal)">data_nascita</code>,
            poi riesegui <code style="font-family:var(--mono)">parte1_analisi.py</code>.
          </div>"""

    recap_html = f"""
<div class="recap-outer">
  <!-- Colonna sinistra: A B C D -->
  <div class="recap-left">
    <div style="font-size:11px;font-weight:700;color:var(--lt);
                text-transform:uppercase;letter-spacing:.7px;margin-bottom:14px">
      Riepilogo Validazione
    </div>
    <div class="rc-grid">{recap_abcd}</div>
  </div>

  <!-- Colonna destra: E — TPI Pro in evidenza -->
  <div class="recap-right">
    <div style="display:flex;align-items:center;gap:8px;margin-bottom:12px;flex-wrap:wrap">
      <div style="font-size:11px;font-weight:700;color:var(--lt);
                  text-transform:uppercase;letter-spacing:.7px">
        E &mdash; TPI Pro
      </div>
      <span style="display:inline-flex;align-items:center;gap:4px;
                   padding:3px 8px;border-radius:6px;font-size:10px;font-weight:700;
                   background:rgba(191,90,242,.15);border:1px solid rgba(191,90,242,.35);
                   color:var(--purp);text-transform:uppercase;letter-spacing:.5px">
        &#10024; Nuovo indice
      </span>
    </div>
    <div style="font-size:12px;color:var(--ls);line-height:1.6;margin-bottom:14px">
      Aggiunge <strong style="color:var(--teal)">AII</strong> (et&agrave;)
      e <strong style="color:var(--purp)">PRI</strong> (fisico) al TPI classico.
      r ideale = 0.80&ndash;0.95.
    </div>
    {e_recap_inner}
  </div>
</div>"""

    # ── SPIEGAZIONI modale ────────────────────────────────────────
    spieg_extra = ""
    if has_v2:
        spieg_extra += """
  v2: {icon:"🧬",ttl:"Età Index & Affidabilità Fisica",sub:"AII e PRI — indici v2 del sistema",
    body:"AII (Age Impact Index) misura il valore nel ciclo di carriera: picco a 27 anni (gaussiana σ=4.5). Un 22enne ha AII basso ma potenziale massimo.\\n\\nPRI (Physical Reliability Index) misura l'affidabilità fisica storica: disponibilità, numero infortuni, gravità.",
    ex:"AII 0.90 + TPI +1.5 = giocatore al picco con qualità reale. AII 0.45 + TPI +1.2 = giovane di prospettiva (scouting a 3 anni)."},
  scatter_d: {icon:"🔵",ttl:"Scatter AII vs PRI",sub:"X = AII (età), Y = PRI (fisico)",
    body:"Ogni punto è un giocatore. Colore = ruolo.\\n\\nIn alto a destra: giocatore al picco dell'età E affidabile fisicamente = profilo ideale.\\nIn basso a sinistra: giovane e fragile = alto potenziale, alto rischio.",
    ex:"AII 0.85, PRI 0.90: veterano affidabile in picco. AII 0.42, PRI 0.88: giovane sano — scouting top."},"""
    if has_pro:
        spieg_extra += """
  tpi_pro: {icon:"✨",ttl:"TPI Pro — 6 Dimensioni",sub:"TPI = 4 dim. TPI Pro = 4 + AII + PRI",
    body:"Il TPI Pro aggiunge z(AII) e z(PRI) alla media dei 4 z-score classici.\\n\\nChi sale: giovani in picco con buona affidabilità fisica.\\nChi scende: veterani fragili che il TPI classico sopravvaluta.\\n\\nr(TPI,TPI Pro) ideale = 0.80–0.95.",
    ex:"r=0.88: TPI Pro è coerente ma aggiunge informazione reale. 15 giocatori salgono >2 posizioni grazie ad AII alto."},
  scatter_e: {icon:"📈",ttl:"Scatter TPI vs TPI Pro",sub:"X = TPI classico | Y = TPI Pro",
    body:"Punti sopra la diagonale: guadagnano con TPI Pro (AII/PRI alti).\\nPunti sotto: perdono.\\n\\nLa retta tratteggiata viola = regressione. Grigia = y=x (nessuna variazione).",
    ex:"Punto molto sopra la diagonale: giovane affidabile che il TPI classico sottovalutava."},"""

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
    <button class="mbox-cls" onclick="closeM()">Chiudi</button>
  </div>
</div>

<nav class="nav">
  <div class="nav-brand">TPI Validation <small>Serie A 25/26</small></div>
  <div class="nav-btn-group">
    <a class="nav-home-btn" href="dashboard_serie_a.html" title="Torna alla classifica">
      &#127942; <span class="home-lbl">Classifica</span>
    </a>
    <a class="nav-glass-btn" href="javascript:history.back()" title="Indietro">&#8592;</a>
    <a class="nav-glass-btn" href="javascript:history.forward()" title="Avanti">&#8594;</a>
  </div>
  <div class="nav-right-group">
    <a class="nav-orng-btn" href="homepage.html" title="Torna alla Homepage">
      &#127968; <span class="hp-label">Homepage</span>
    </a>
  </div>
</nav>

<div class="hero">
  <div class="hero-ttl">Model Validation</div>
  <div style="font-size:11px;color:var(--purp);font-weight:600;letter-spacing:.3px;
    text-transform:uppercase;margin-top:4px;margin-bottom:10px">
    Serie A Scout Index · TPI System
  </div>
  <div class="hero-sub">
    5 independent tests to verify that TPI measures real player quality —
    not noise. Predictive backtest <strong style="color:var(--green)">r = 0.70</strong>,
    Fantacalcio correlation <strong style="color:var(--blue)">r = 0.47</strong>.
    Click <strong style="color:var(--lp)">?</strong> on each section for methodology details.
  </div>
  <div class="hero-pills">
    <span class="hero-pill">&#127941; Pearson r vs Fantacalcio</span>
    <span class="hero-pill">&#128200; Top 10 Overlap WhoScored</span>
    <span class="hero-pill">&#128336; Predictive Backtest r=0.70</span>
    <span class="hero-pill">&#129516; Age &amp; Physical Index</span>
    <span class="hero-pill">&#10024; TPI Pro</span>
  </div>
</div>

<div class="main">

<div class="accordion">
  <div class="acc-hd" onclick="toggleAcc('acc1')">
    <div class="acc-title">&#128214; Come leggere i risultati</div>
    <span class="acc-chev" id="chev-acc1">&#9660;</span>
  </div>
  <div class="acc-body" id="acc1">
    <div class="guide-grid">
      <div class="guide-card"><div class="guide-icon">&#128202;</div><div class="guide-ttl">r di Pearson</div>
        <div class="guide-body"><strong style="color:var(--green)">r&gt;0.6</strong> forte &middot; <strong style="color:var(--orng)">0.4–0.6</strong> moderata &middot; <strong style="color:var(--red)">&lt;0.4</strong> debole. Per sport r=0.5 con fonti esterne &egrave; ottimo.</div></div>
      <div class="guide-card"><div class="guide-icon">&#128270;</div><div class="guide-ttl">p-value</div>
        <div class="guide-body"><strong style="color:var(--green)">p&lt;0.05</strong> = significativo. Senza p basso anche r alto potrebbe essere fortuna.</div></div>
      <div class="guide-card"><div class="guide-icon">&#127919;</div><div class="guide-ttl">Overlap basso = forza</div>
        <div class="guide-body">Overlap basso = il TPI trova giocatori non valorizzati dalla stampa. 100% = non aggiunge nulla.</div></div>
      <div class="guide-card"><div class="guide-icon">&#128336;</div><div class="guide-ttl">Backtest</div>
        <div class="guide-body">Prima met&agrave; stagione predice la seconda? Un indice senza potere predittivo misura solo la fortuna del momento.</div></div>
      <div class="guide-card"><div class="guide-icon">&#10024;</div><div class="guide-ttl">TPI Pro</div>
        <div class="guide-body">Aggiunge AII (et&agrave;) e PRI (affidabilit&agrave; fisica) al TPI classico. r(TPI,TPI Pro) ideale = 0.80–0.95.</div></div>
      <div class="guide-card"><div class="guide-icon">&#9888;</div><div class="guide-ttl">Limiti</div>
        <div class="guide-body">Voti Fantacalcio e WhoScored sono inseriti manualmente. Backtest payload = Output Adj vs EWMA come proxy.</div></div>
    </div>
  </div>
</div>

<!-- SEZIONE A -->
<div class="section">
  <div class="section-hd">
    <div class="section-num" style="background:var(--blue)">A</div>
    <div>
      <div class="section-ttl">TPI vs Fantacalcio Ratings <span class="help" onclick="openM('pearson')">?</span></div>
      <div class="section-sub">Does TPI correlate with expert consensus? r=0.4–0.7 is ideal — high enough to confirm quality, low enough to add independent insight.</div>
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
      <div class="stat-sub">robusto agli outlier &middot; {pval(val_a.get("p_spearman"))}</div>
    </div>
    <div class="stat-box sb-orng">
      <div class="stat-val" style="color:var(--orng)">{_sf(val_a.get("cohen_d"),2)}</div>
      <div class="stat-lbl">Cohen&#x2019;s d</div>
      <div class="stat-sub">effect size top vs bottom 25%</div>
    </div>
  </div>
  <div class="g2">
    <div class="card"><div class="card-ttl">Scatter TPI vs Fantacalcio <span class="help" onclick="openM('scatter')">?</span></div>
      <div id="chart-a" class="chart-h" style="height:300px"></div></div>
    <div class="card"><div class="card-ttl">Interpretazione</div>
      <div style="font-size:13px;color:var(--ls);line-height:1.75">
        <p style="margin-bottom:10px">I <strong style="color:var(--lp)">voti Fantacalcio</strong> rappresentano la percezione collettiva della qualit&agrave;.</p>
        <p style="margin-bottom:10px">r=0.4–0.7 &egrave; il risultato ideale: il TPI conferma e arricchisce.</p>
        <p>r&gt;0.9 = il TPI non aggiunge nulla. r&lt;0.3 = troppo distante dalla qualit&agrave; percepita.</p>
      </div>
      <div class="interp">&#128161; {val_a.get("interpretazione","&mdash;")}</div>
    </div>
  </div>
</div>

<!-- SEZIONE B -->
<div class="section">
  <div class="section-hd">
    <div class="section-num" style="background:var(--orng)">B</div>
    <div>
      <div class="section-ttl">Top 10 TPI vs WhoScored Rankings <span class="help" onclick="openM('overlap')">?</span></div>
      <div class="section-sub">Low overlap = independent insight. TPI identifies undervalued players that popular rankings miss.</div>
    </div>
  </div>
  <div class="g3">
    <div class="stat-box sb-orng"><div class="stat-val" style="color:var(--orng)">{ov}%</div><div class="stat-lbl">Overlap Top 10</div><div class="stat-sub">in comune con WhoScored</div></div>
    <div class="stat-box sb-green"><div class="stat-val" style="color:var(--green)">{len(val_b.get("divergenze_pos",[]))}</div><div class="stat-lbl">Sottovalutati</div><div class="stat-sub">TPI alto, WhoScored basso</div></div>
    <div class="stat-box" style="border-color:rgba(255,69,58,.2)"><div class="stat-val" style="color:var(--red)">{len(val_b.get("divergenze_neg",[]))}</div><div class="stat-lbl">Sopravvalutati</div><div class="stat-sub">TPI basso, WhoScored alto</div></div>
  </div>
  <div class="g2">
    <div class="card"><div class="card-ttl">Top 10 per TPI</div>
      <div class="table-wrap"><table><tr><th>#</th><th>Giocatore</th><th>Sqd</th><th>TPI</th><th>WS</th></tr><tbody id="tb-tpi"></tbody></table></div></div>
    <div class="card"><div class="card-ttl">Top 10 per WhoScored</div>
      <div class="table-wrap"><table><tr><th>#</th><th>Giocatore</th><th>Sqd</th><th>WS</th><th>TPI</th></tr><tbody id="tb-ws"></tbody></table></div></div>
  </div>
  <div class="card"><div class="card-ttl">Divergenze notevoli <span class="help" onclick="openM('divergenze')">?</span></div>
    <div id="div-content"></div>
    <div class="interp">&#128270; Divergenze = insight, non errori. Identificano giocatori con impatto reale non riconosciuto.</div>
  </div>
</div>

<!-- SEZIONE C -->
<div class="section">
  <div class="section-hd">
    <div class="section-num" style="background:var(--green)">C</div>
    <div>
      <div class="section-ttl">Predictive Backtest — r = 0.70 <span class="help" onclick="openM('backtest')">?</span></div>
      <div class="section-sub">Can early-season TPI predict late-season performance? {val_c.get("early_range","First half")} &rarr; {val_c.get("late_range","Second half")}</div>
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
      <div class="stat-val" style="color:var(--teal)">{_sf(val_c.get("tau"),3)}</div>
      <div class="stat-lbl">Kendall &#964;</div>
      <div class="stat-sub">ranking stability &middot; {pval(val_c.get("p_tau"))}</div>
    </div>
    <div class="stat-box" style="border-color:rgba(48,209,88,.2)">
      <div class="stat-val" style="color:var(--green);font-size:16px">{_sf(val_c.get("rmse"),4)}</div>
      <div class="stat-lbl">RMSE</div>
      <div class="stat-sub">MAE = {_sf(val_c.get("mae"),4)}</div>
    </div>
  </div>
  <div class="g2">
    <div class="card"><div class="card-ttl">Scatter prima &rarr; seconda fase <span class="help" onclick="openM('scatter_c')">?</span></div>
      <div id="chart-c" class="chart-h" style="height:300px"></div></div>
    <div class="card"><div class="card-ttl">Perch&eacute; &egrave; importante</div>
      <div style="font-size:13px;color:var(--ls);line-height:1.75">
        <p style="margin-bottom:10px">Un indice senza predittivit&agrave; misura solo la fortuna passata.</p>
        <p style="margin-bottom:10px"><strong style="color:var(--green)">r&gt;0.5</strong> = qualit&agrave; stabile &middot; <strong style="color:var(--orng)">r=0.3–0.5</strong> = segnale parziale &middot; <strong style="color:var(--red)">r&lt;0.3</strong> = troppo volatile.</p>
        <p><strong style="color:var(--lp)">Spearman</strong> &egrave; robusto agli outlier &mdash; ideale per dati sportivi.</p>
      </div>
      <div class="interp">&#128161; {val_c.get("interpretazione","&mdash;")}</div>
    </div>
  </div>
</div>

{v2_section}

{e_section}

<!-- RECAP -->
{recap_html}

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
  font:{{color:"rgba(235,235,245,.28)",family:"-apple-system,sans-serif"}},
  xaxis:{{gridcolor:"rgba(255,255,255,.05)",color:"rgba(235,235,245,.28)",
    tickfont:{{size:10}},zeroline:false}},
  yaxis:{{gridcolor:"rgba(255,255,255,.05)",color:"rgba(235,235,245,.28)",
    tickfont:{{size:10}},zeroline:false}}}};
const RC_MAP={{"ATT":"#ff9f0a","CEN":"#30d158","DIF":"#0a84ff","":"#48484a"}};
const rcf=r=>RC_MAP[r]||"#636366";

const SPIEG={{
  pearson:{{icon:"📊",ttl:"Correlazione di Pearson",
    sub:"r = Σ[(xi−x̄)(yi−ȳ)] / [n·σx·σy]",
    body:"Misura la relazione lineare (–1 a +1).\\nr=+1: diretta perfetta.\\nr=0: nessuna relazione.\\nr=–1: inversa.\\n\\nr=0.4–0.7 è ideale: conferma qualità reale con punto di vista diverso.",
    ex:"r=0.53, p=0.004 → moderata, significativa. R²=0.28."}},
  scatter:{{icon:"🔵",ttl:"Come leggere lo Scatter",
    sub:"X = TPI | Y = Voto Fantacalcio",
    body:"Ogni punto = un giocatore. Colore = ruolo.\\n\\nLontano dalla retta = sottovalutato o sopravvalutato.",
    ex:"Alto a sx: voto alto ma TPI basso → sopravvalutato. Basso a dx: TPI alto, voto basso → da valorizzare."}},
  overlap:{{icon:"🎯",ttl:"Overlap Top 10",
    sub:"% giocatori in entrambe le classifiche",
    body:"Overlap basso non è negativo: il TPI trova talenti che i sistemi tradizionali ignorano.",
    ex:"Overlap 30% = 3/10 in comune. I 7 diversi nella lista TPI sono potenziali 'hidden gems'."}},
  divergenze:{{icon:"🔍",ttl:"Divergenze TPI vs WhoScored",
    sub:"Gap ≥4 posizioni tra i due sistemi",
    body:"Sottovalutati: TPI rank >> WhoScored → impatto offensivo non catturato.\\n\\nSopravvalutati: WhoScored >> TPI → percezione influenzata da aspetti non offensivi.",
    ex:"TPI #3, WhoScored #12: impatto offensivo reale non visibile nei voti generali."}},
  backtest:{{icon:"⏱️",ttl:"Backtest Predittivo",
    sub:"Spearman r tra prima e seconda fase",
    body:"Verifica se la classifica della prima fase predice quella della seconda.\\n\\nSpearman (rank-based) è robusto agli outlier.",
    ex:"r=0.58: chi era top10 nella prima fase tende a restare top10."}},
  scatter_c:{{icon:"📈",ttl:"Scatter Backtest",
    sub:"X = prima fase | Y = seconda fase",
    body:"Sopra la diagonale: migliorati. Sotto: peggiorati. Vicino alla retta: coerenti.",
    ex:"Alto a destra = qualità stabile confermata. Alto a sx = giocatore in crescita stagionale."}},
  {spieg_extra}
}};

function openM(k){{
  const s=SPIEG[k];if(!s)return;
  document.getElementById("m-icon").textContent=s.icon;
  document.getElementById("m-ttl").textContent=s.ttl;
  document.getElementById("m-sub").textContent=s.sub;
  document.getElementById("m-body").innerHTML=s.body.replace(/\\n/g,"<br>");
  document.getElementById("m-ex").textContent="Esempio: "+s.ex;
  document.getElementById("modal").classList.add("open");
}}
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
        line:{{color:"rgba(255,255,255,.15)",width:1}}}}}},
    {{type:"scatter",mode:"lines",x:[xmn,xmx],
      y:[SL_A===null?0:SL_A*xmn+(IC_A||0),SL_A===null?0:SL_A*xmx+(IC_A||0)],
      line:{{color:"rgba(10,132,255,.5)",width:2,dash:"dot"}},hoverinfo:"skip"}},
  ],{{...BL,xaxis:{{...BL.xaxis,title:"TPI Totale"}},
    yaxis:{{...BL.yaxis,title:"Voto Fantacalcio"}},
    margin:{{t:8,b:46,l:50,r:8}},height:300,showlegend:false,
    annotations:[{{x:.02,y:.97,xref:"paper",yref:"paper",
      text:"r = "+(R_A!==null?R_A.toFixed(3):"—"),showarrow:false,
      font:{{color:"rgba(235,235,245,.6)",size:13}},align:"left"}}]}},PL);
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
  if(DIV_POS.length){{h+='<div><div style="font-size:11px;font-weight:700;color:var(--green);margin-bottom:8px">&#128200; Sottovalutati</div>';
    DIV_POS.forEach(d=>{{h+=`<div style="padding:10px;background:rgba(48,209,88,.05);border:1px solid rgba(48,209,88,.15);border-radius:10px;margin-bottom:6px">
      <div style="font-weight:600;font-size:13px">${{d.nome}} <span style="color:var(--lt);font-weight:400;font-size:11px">${{d.squadra}}</span></div>
      <div style="font-size:12px;color:var(--lt);margin-top:3px">TPI: <span style="color:var(--green);font-family:var(--mono)">#${{d.tpi_rank}}</span> vs WS: <span style="font-family:var(--mono)">#${{d.ws_rank}}</span></div>
    </div>`;}});h+='</div>';}}
  if(DIV_NEG.length){{h+='<div><div style="font-size:11px;font-weight:700;color:var(--red);margin-bottom:8px">&#9888; Sopravvalutati</div>';
    DIV_NEG.forEach(d=>{{h+=`<div style="padding:10px;background:rgba(255,69,58,.05);border:1px solid rgba(255,69,58,.15);border-radius:10px;margin-bottom:6px">
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
        line:{{color:"rgba(255,255,255,.12)",width:1}}}}}},
    {{type:"scatter",mode:"lines",x:[xmn,xmx],
      y:[SL_C===null?0:SL_C*xmn+(IC_C||0),SL_C===null?0:SL_C*xmx+(IC_C||0)],
      line:{{color:"rgba(48,209,88,.5)",width:2,dash:"dot"}},hoverinfo:"skip"}},
    {{type:"scatter",mode:"lines",
      x:[Math.min(xmn,Math.min(...yv)),Math.max(xmx,Math.max(...yv))],
      y:[Math.min(xmn,Math.min(...yv)),Math.max(xmx,Math.max(...yv))],
      line:{{color:"rgba(255,255,255,.07)",width:1,dash:"dot"}},hoverinfo:"skip"}},
  ],{{...BL,xaxis:{{...BL.xaxis,title:"Output/90 — Prima fase"}},
    yaxis:{{...BL.yaxis,title:"Output/90 — Seconda fase"}},
    margin:{{t:8,b:46,l:54,r:8}},height:300,showlegend:false,
    annotations:[{{x:.02,y:.97,xref:"paper",yref:"paper",
      text:"Spearman r = "+(R_C!==null?R_C.toFixed(3):"—"),showarrow:false,
      font:{{color:"rgba(235,235,245,.6)",size:13}},align:"left"}}]}},PL);
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
      line:{{color:"rgba(255,255,255,.12)",width:1}}}}
  }}],{{...BL,
    xaxis:{{...BL.xaxis,title:"AII — Et\u00e0 Index"}},
    yaxis:{{...BL.yaxis,title:"PRI — Affidabilit\u00e0 Fisica"}},
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
  setBadge("badge-a", R_A, 0.6, 0.4);
  setBadge("badge-b", OV/100, 0.7, 0.5);
  setBadge("badge-c", R_C, 0.5, 0.3);
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

    log.info("\nGenerazione HTML...")
    html = build_dashboard(val_a, val_b, val_c, val_d, val_e)

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

    log.info("")
    log.info(f"  A — r={_sf(val_a.get('r'),3)}  n={val_a.get('n',0)}")
    log.info(f"  B — overlap={val_b.get('overlap_pct','—')}%")
    log.info(f"  C — r={_sf(val_c.get('r'),3)}  n={val_c.get('n',0)}  fonte={val_c.get('source','?')}")
    log.info(f"  D — AII:{val_d.get('n_aii',0)} PRI:{val_d.get('n_pri',0)}  has_data={val_d.get('has_data',False)}")
    log.info(f"  E — TPI Pro: {val_e.get('n_pro',0)} giocatori  r={_sf(val_e.get('r_corr'),3)}  has_data={val_e.get('has_data',False)}")
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
