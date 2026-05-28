"""
parte1_analisi.py  —  Serie A 25/26  |  Motore di analisi  v4.2
================================================================
Legge il DB MySQL, calcola tutte le metriche, salva:
  dashboard_output/payload.json        ← input per parte2_dashboard.py
  dashboard_output/summary_stats.csv   ← export tabellare

Novità v4.2:
  · Nomi completi (nome + cognome) nella dashboard
    iniziale+cognome quando il cognome è duplicato tra i giocatori qualificati
    (es. due "Esposito" → "C. Esposito" / "F. Esposito")

Miglioramenti v4 rispetto alla versione precedente:
  · Logging strutturato (sostituisce print)
  · Type hints e docstring complete
  · Operazioni vettorizzate (no df.apply row-by-row dove possibile)
  · K bayesiano riparametrato (prior gerarchico corretto)
  · Boost ratio stratificato per home/away
  · Conversione G/xG anche per contesto casa/trasferta
  · Soglia minuti adattiva basata su distribuzione lega
  · Query SQL ottimizzate con indici suggeriti
  · Gestione eccezioni robusta su ogni sezione
  · Schema DB verificato all'avvio

Dipendenze:
  pip install pandas numpy sqlalchemy pymysql
"""

from __future__ import annotations

import json
import logging
import os
import sys
import warnings

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

from config import db_url as _cfg_db_url  # carica .env + fail-fast su DB_PASSWORD
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

warnings.filterwarnings("ignore", category=FutureWarning)

# ════════════════════════════════════════════════════════════════
# 0. LOGGING
# ════════════════════════════════════════════════════════════════
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("serie_a_analytics")


# ════════════════════════════════════════════════════════════════
# 1. CONFIGURAZIONE
# ════════════════════════════════════════════════════════════════
@dataclass
class Config:
    """Tutti i parametri del sistema in un unico posto."""

    # Database — credenziali da .env via config.py (fail-fast)
    db_url: str = field(default_factory=_cfg_db_url)

    # API (opzionale per narrativa AI)
    anthropic_api_key: str = ""

    # Bayesian shrinkage
    k_base: float = 12.0
    k_winter_multiplier: float = 1.8

    # Shrinkage minuti su output_adj (empirical Bayes verso media-ruolo)
    # K in minuti: m=K → 50% shrinkage. ~600' ≈ 7 partite.
    output_prior_minutes: float = 600.0

    # Boost (team xG con/senza): metrica debole e confondente.
    # log-ratio shrinkato verso neutro pesato sul campione "senza".
    boost_min_senza: int = 6      # sotto questa soglia → boost neutro (None)
    boost_shrink_k: float = 8.0   # n=K → 50% del segnale; più dati = più segnale
    # Peso della fase offensiva per ruolo: quanto "conta" l'offensiva nel TPI.
    # ATT pieno, DIF/POR ridotto → un difensore bravo offensivamente emerge tra
    # i difensori ma non supera gli attaccanti (resta un indice OFFENSIVO).
    offensive_role_weight: dict[str, float] = field(default_factory=lambda: {
        "ATT": 1.00, "CEN": 0.85, "DIF": 0.55, "POR": 0.20,
    })
    # Quanto la confidence (minuti+completezza) regredisce il TPI verso la media
    # di ruolo. floor=0.35 → anche a confidence 0 si tiene il 35% del segnale.
    confidence_floor: float = 0.35

    # Pesi del TPI: la QUALITÀ (output_adj, xG+xA/90 SOS-adj) domina; uso/contesto
    # squadra (centralità, boost) pesano meno perché gonfiabili su squadre deboli;
    # la finalizzazione (gol vs xG) entra con peso piccolo così chi non converte
    # scende. Somma = 1.0. Re-normalizzati sui dim effettivamente disponibili.
    tpi_weights: dict[str, float] = field(default_factory=lambda: {
        "output_adj":  0.42,
        "centralita":  0.17,
        "boost_ratio": 0.13,
        "consistenza": 0.13,
        "finishing":   0.15,
    })

    # Contesti & soglie
    n_top_difese: int = 6
    n_top6_class: int = 6
    min_appearances_context: int = 4
    min_appearances_form: int = 5

    # Soglia minuti
    min_minutes_pct: float = 0.20
    min_minutes_pct_winter: float = 0.12
    # frazione della stagione oltre la quale il debutto è considerato "invernale"
    # 0.55 = ~G19 su 36 (post break invernale, finestra mercato gennaio)
    winter_debut_fraction: float = 0.55
    use_adaptive_threshold: bool = True

    # EWMA
    ewma_alpha: float = 0.30

    # ── Nuovi indici v2 ──────────────────────────────────────
    # Age Impact Index (AII)
    age_peak: float = 27.0          # età di picco prestativo
    age_sigma: float = 4.5          # dispersione curva gaussiana
    age_exp_cap: float = 10.0       # anni carriera per experience = 1.0

    # Physical Reliability Index (PRI)
    pri_min_partite: int = 8        # min partite disponibili per calcolare PRI
    pri_inj_penalty: float = 0.15   # penalità per ogni infortunio (-15%)
    pri_severity_cap: float = 90.0  # giorni out che portano severity a 0

    # TPI esteso (include AII + PRI)
    include_age_in_tpi_ext: bool = True
    include_physical_in_tpi_ext: bool = True

    # Output
    top_n_payload: int = 100
    top_n_ai: int = 20
    output_dir: str = ""

    # Colonne DB (auto-rilevate se None)
    sgl_xg_col: str | None = None
    sgl_xg_avv_col: str | None = None
    sgl_ruolo_col: str = "ruolo"

    # Override ruoli — SOLO ECCEZIONI (giocatori senza posizione Understat).
    # Fonte primaria = posizione reale Understat (derive_understat_roles, precedenza
    # finale). Aggiungi qui SOLO se Understat manca o sbaglia per un giocatore.
    ruolo_override: dict[str, str] = field(default_factory=lambda: {
        "Branimir Mlacic": "POR",
        "Daniel Denoon": "POR",
        "Daniele Padelli": "POR",
        "Daniele Sommariva": "POR",
        "David Okereke": "POR",
        "Eddy Kouadio": "POR",
        "Elseid Hysaj": "POR",
        "Filippo Rinaldi": "POR",
        "Matteo Darmian": "POR",
        "Matteo Palma": "POR",
        "Mattia Viti": "POR",
        "Pietro Terracciano": "POR",
        "Samuel Iling-Junior": "POR",
        "Adam Masina": "DIF",
        "Alessandro Di Pardo": "DIF",
        "Benjamin Cremaschi": "DIF",
        "Benjamin Pavard": "DIF",
        "Faustino Anjorin": "DIF",
        "Guillermo Maipan": "DIF",
        "Hernani": "DIF",
        "Jeremy Sarmiento": "DIF",
        "Juan Cabal": "DIF",
        "Leo Ostigard": "DIF",
        "Leon Bailey": "DIF",
        "Malthe Hojholt": "DIF",
        "Mathias Lovik": "DIF",
        "Niccolo Fortini": "DIF",
        "Nicholas Pierini": "DIF",
        "Oliver Sorensen": "DIF",
        "Pervis Estupinian": "DIF",
        "Pervis Estupiñán": "DIF",
        "Petar Ratkov": "DIF",
        "Sandro Kulenovic": "DIF",
        "Thorir Helgason": "DIF",
        "Torbjorn Heggem": "DIF",
        "Albert Gronbaek": "CEN",
        "Albert Grønbæk": "CEN",
        "Alex Sala": "CEN",
        "Bryan Zaragoza": "CEN",
        "Daniel Boloca": "CEN",
        "Edon Zhegrova": "CEN",
        "Fallou Cham": "CEN",
        "Idrissa Gueye": "CEN",
        "Lorenzo Venturino": "CEN",
        "Mikayil Faye": "CEN",
        "Oier Zarraga": "CEN",
        "Pasquale Mazzocchi": "CEN",
        "Rui Modesto": "CEN",
        "Alieu Njie": "ATT",
        "Andrea Belotti": "ATT",
        "Artem Dovbyk": "ATT",
        "Ciro Immobile": "ATT",
        "Edin Dzeko": "ATT",
        "Edoardo Iannoni": "ATT",
        "Faris Moumbagna": "ATT",
        "Iker Bravo": "ATT",
        "Juan Cuadrado": "ATT",
        "Konan NDri": "ATT",
        "Leonardo Pavoletti": "ATT",
        "Lorran": "ATT",
        "Luca Moro": "ATT",
        "MBala Nzola": "ATT",
        "Maxwel Cornet": "ATT",
        "Niclas Fullkrug": "ATT",
        "Nicolae Stanciu": "ATT",
        "Nikola Studic": "ATT",
        "Rasmus Hojlund": "ATT",
        "Robinio Vaz": "ATT",
        "Vasilije Adzic": "ATT",
        "Zito": "ATT",
    })


CFG = Config()
CFG.output_dir = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "dashboard_output"
)
os.makedirs(CFG.output_dir, exist_ok=True)

CONTESTI = ["totale", "casa", "trasferta", "vs_top6", "vs_forti"]
DIMS = ["output_adj", "centralita", "boost_ratio", "consistenza"]

# Mappa posizione Understat → ruolo (per linea di campo).
# Wing-back (DML/DMR) → DIF; mediani/mezzali (DMC/M*) → CEN;
# ali e trequartisti (AM*/FW*) → ATT. Più affidabile dell'override manuale.
_UNDERSTAT_POS_ROLE = {
    "GK": "POR",
    "DC": "DIF", "DL": "DIF", "DR": "DIF", "DML": "DIF", "DMR": "DIF",
    "DMC": "CEN", "MC": "CEN", "ML": "CEN", "MR": "CEN",
    "AMC": "ATT", "AML": "ATT", "AMR": "ATT",
    "FW": "ATT", "FWL": "ATT", "FWR": "ATT",
}


def derive_understat_roles(min_minutes: int = 200) -> dict[str, str]:
    """Ruolo per giocatore dalla posizione Understat con più minuti (dai JSON
    grezzi in cache). Chiave = nome normalizzato. Più robusto dell'override
    manuale perché basato sulla posizione realmente giocata."""
    import glob as _g, json as _j, html as _h, unicodedata as _u, collections as _c

    def _nm(s):
        return _u.normalize("NFKD", _h.unescape(str(s or ""))).encode("ascii", "ignore").decode().lower().strip()

    cache_dir = os.path.join(os.path.expanduser("~/soccerdata"), "data", "Understat")
    posmin: dict[str, _c.Counter] = _c.defaultdict(_c.Counter)
    for mf in _g.glob(os.path.join(cache_dir, "match_*.json")):
        try:
            md = _j.load(open(mf, encoding="utf-8"))
        except Exception:
            continue
        for side in ("h", "a"):
            for _pid, info in md.get("rosters", {}).get(side, {}).items():
                pos = info.get("position")
                t = int(info.get("time", 0) or 0)
                if pos and pos != "Sub" and t > 0:
                    posmin[_nm(info.get("player"))][pos] += t
    roles: dict[str, str] = {}
    for nm, cnt in posmin.items():
        if sum(cnt.values()) < min_minutes:
            continue
        top_pos = cnt.most_common(1)[0][0]
        r = _UNDERSTAT_POS_ROLE.get(top_pos)
        if r:
            roles[nm] = r
    return roles


# ════════════════════════════════════════════════════════════════
# 2. UTILITÀ GENERALI
# ════════════════════════════════════════════════════════════════
def safe_json(v: Any, decimals: int = 4) -> Any:
    """
    Converte qualsiasi valore in un tipo JSON-serializzabile.
    Gestisce: None, pd.NA, np.nan, np.integer, np.floating, pandas Int64.
    """
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.floating,)):
        return None if np.isnan(v) else round(float(v), decimals)
    if isinstance(v, bool):
        return v
    if isinstance(v, int):
        return v
    if isinstance(v, float):
        return None if (v != v) else round(v, decimals)
    if isinstance(v, (list, tuple)):
        return [safe_json(x, decimals) for x in v]
    try:
        f = float(v)
        return None if (f != f) else round(f, decimals)
    except (TypeError, ValueError):
        return str(v)


def clean_str(s: Any) -> Any:
    """Rimuove surrogati Unicode da stringhe DB."""
    if not isinstance(s, str):
        return s
    return s.encode("utf-8", errors="replace").decode("utf-8")


def deep_clean(obj: Any) -> Any:
    """Applica clean_str ricorsivamente a dict/list."""
    if isinstance(obj, str):
        return clean_str(obj)
    if isinstance(obj, dict):
        return {k: deep_clean(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [deep_clean(v) for v in obj]
    return obj


def col_detect(df: pd.DataFrame, *candidates: str) -> str | None:
    """
    Restituisce il nome della prima colonna trovata tra i candidati.
    Ricerca case-insensitive come fallback.
    """
    lower_map = {c.lower(): c for c in df.columns}
    for name in candidates:
        if name in df.columns:
            return name
        if name.lower() in lower_map:
            return lower_map[name.lower()]
    return None


# ════════════════════════════════════════════════════════════════
# 3. LAYER DATABASE
# ════════════════════════════════════════════════════════════════
class DatabaseLayer:
    """
    Centralizza tutte le operazioni sul database.
    Espone metodi tipizzati invece di query sparse nel codice.
    """

    def __init__(self, engine: Engine):
        self.engine = engine
        self._verify_schema()

    def _verify_schema(self) -> None:
        required = [
            "giocatori", "squadre", "calendario",
            "giocatore_partita", "t_player_analytics",
            "t_squadra_game_log", "squadra_calendario",
        ]
        with self.engine.connect() as conn:
            existing = {
                row[0]
                for row in conn.execute(text("SHOW TABLES"))
            }
        missing = set(required) - existing
        if missing:
            raise RuntimeError(
                f"Tabelle mancanti nel database: {missing}\n"
                "Esegui prima lo script di setup del DB."
            )
        log.info("Schema DB verificato: tutte le tabelle essenziali presenti")

    def load_sos_map(self) -> dict[int, float]:
        # SOS = solidità difensiva dell'avversario = xG REALMENTE concessi a
        # stagione (media su tutte le gare), normalizzata a media-lega = 1.0.
        # NB: NON usiamo più t_sos_squadre — conteneva l'xG OFFENSIVO mislabeled
        # come "subiti" (corr +0.98 con xG fatti), che invertiva l'aggiustamento:
        # gonfiava l'output contro le difese deboli e marcava come "difese solide"
        # le PEGGIORI. Qui ricostruiamo il dato corretto dai match.
        try:
            df = pd.read_sql(
                """
                SELECT a.squadra_id        AS squadra_id,
                       AVG(b.xg)           AS xg_concessi
                FROM   squadra_calendario a
                JOIN   squadra_calendario b
                       ON b.calendario_id = a.calendario_id
                      AND b.squadra_id   <> a.squadra_id
                GROUP BY a.squadra_id
                """,
                self.engine,
            )
            if df.empty or df["xg_concessi"].isna().all():
                return self._fallback_sos()
            league_avg = float(df["xg_concessi"].mean())
            if league_avg <= 0:
                return self._fallback_sos()
            result = {
                int(r["squadra_id"]): float(r["xg_concessi"]) / league_avg
                for _, r in df.iterrows()
                if not pd.isna(r["xg_concessi"])
            }
            log.info(
                f"SOS (xG concessi reali, norm. lega=1.0): {len(result)} squadre"
            )
            return result
        except Exception as e:
            log.warning(f"SOS reale non calcolabile: {e} → SOS = 1.0")
            return self._fallback_sos()

    def _fallback_sos(self) -> dict[int, float]:
        try:
            df = pd.read_sql("SELECT id FROM squadre", self.engine)
            return {int(r["id"]): 1.0 for _, r in df.iterrows()}
        except Exception:
            return {}

    def load_players_analytics(self) -> pd.DataFrame:
        # Base costruita direttamente da `giocatori` + aggregati freschi di
        # `giocatore_partita` (NON da t_player_analytics, che era uno snapshot
        # stantio: minuti sbagliati e ~15 titolari mancanti dal ranking).
        # Così ogni giocatore con minuti reali è incluso, con minutaggio corretto.
        df = pd.read_sql(
            """
            SELECT
                g.id                       AS giocatore_id,
                g.ruolo,
                g.squadra_id,
                sq.nome                    AS squadra,
                COALESCE(agg.minuti, 0)    AS minuti,
                COALESCE(agg.partite, 0)   AS partite,
                COALESCE(agg.goal, 0)      AS goal,
                agg.xg,
                agg.xa,
                TRIM(CASE
                    WHEN g.cognome IS NULL OR TRIM(g.cognome) = '' THEN g.nome
                    WHEN LOWER(g.nome) LIKE LOWER(CONCAT('%%', g.cognome, '%%')) THEN g.nome
                    ELSE CONCAT_WS(' ', NULLIF(TRIM(g.nome), ''), NULLIF(TRIM(g.cognome), ''))
                END) AS nome_anagrafico,
                TRIM(CASE
                    WHEN g.cognome IS NULL OR TRIM(g.cognome) = '' THEN g.nome
                    WHEN LOWER(g.nome) LIKE LOWER(CONCAT('%%', g.cognome, '%%')) THEN g.nome
                    ELSE CONCAT_WS(' ', NULLIF(TRIM(g.nome), ''), NULLIF(TRIM(g.cognome), ''))
                END) AS giocatore
            FROM giocatori g
            LEFT JOIN squadre sq ON sq.id = g.squadra_id
            JOIN (
                SELECT giocatore_id,
                       SUM(minuti)               AS minuti,
                       COUNT(DISTINCT calendario_id) AS partite,
                       SUM(goal)                 AS goal,
                       SUM(xg)                   AS xg,
                       SUM(xa)                   AS xa
                FROM giocatore_partita
                WHERE minuti > 0
                GROUP BY giocatore_id
            ) agg ON agg.giocatore_id = g.id
            """,
            self.engine,
        )
        df["squadra_id"] = pd.to_numeric(df["squadra_id"], errors="coerce").astype("Int64")
        df["giocatore_id"] = pd.to_numeric(df["giocatore_id"], errors="coerce").astype("Int64")
        return df

    def load_squad_game_log(self) -> pd.DataFrame:
        df = pd.read_sql(
            """
            SELECT
                sgl.*,
                cal.giornata
            FROM t_squadra_game_log sgl
            JOIN calendario cal ON cal.id = sgl.calendario_id
            """,
            self.engine,
        )
        for col in ("squadra_id", "avversario_id"):
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce").astype("Int64")
        return df

    def load_player_games(self, xg_col: str, xg_avv_col: str) -> pd.DataFrame:
        df = pd.read_sql(
            f"""
            SELECT
                gp.giocatore_id,
                g.squadra_id          AS squadra_id,
                gp.calendario_id,
                cal.giornata,
                sgl.ruolo             AS ruolo_gp,
                gp.minuti,
                gp.goal,
                COALESCE(gp.npg,  gp.goal) AS npg_ind,
                COALESCE(gp.npxg, gp.xg)   AS xg_ind,
                gp.xa                 AS xa_ind,
                sc.xg                 AS xg_team,
                sgl.avversario_id,
                sgl.{xg_avv_col}      AS xg_avversario
            FROM      giocatore_partita  gp
            JOIN      giocatori           g   ON  g.id             = gp.giocatore_id
            JOIN      calendario          cal ON  cal.id           = gp.calendario_id
            JOIN      squadra_calendario  sc  ON  sc.squadra_id    = g.squadra_id
                                              AND sc.calendario_id  = gp.calendario_id
            LEFT JOIN t_squadra_game_log  sgl ON  sgl.squadra_id   = g.squadra_id
                                              AND sgl.calendario_id = gp.calendario_id
            """,
            self.engine,
        )
        for col in ("giocatore_id", "squadra_id", "avversario_id"):
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce").astype("Int64")
        return df

    def load_classification(self) -> pd.DataFrame | None:
        try:
            df = pd.read_sql("SELECT * FROM v_classifica", self.engine)
            result = self._parse_classification(df)
            if result is not None:
                log.info("Classifica caricata da v_classifica")
                return result
        except Exception as e:
            log.debug(f"v_classifica non disponibile: {e}")

        try:
            sgl = pd.read_sql(
                "SELECT squadra_id, punti FROM t_squadra_game_log", self.engine
            )
            pts = sgl.groupby("squadra_id")["punti"].sum().reset_index()
            pts = pts.sort_values("punti", ascending=False).reset_index(drop=True)
            pts["posizione"] = range(1, len(pts) + 1)
            pts["squadra_id"] = pd.to_numeric(pts["squadra_id"], errors="coerce").astype("Int64")
            log.info("Classifica ricostruita da punti SGL")
            return pts[["squadra_id", "punti", "posizione"]]
        except Exception as e:
            log.debug(f"Classifica L2 fallita: {e}")

        log.warning("Classifica non disponibile — contesto vs_top6 degradato")
        return None

    def _parse_classification(self, df: pd.DataFrame) -> pd.DataFrame | None:
        EXCLUDE = {"g", "v", "p", "s", "gf", "gs", "dr", "pt", "xg"}
        pts_col = next(
            (c for c in df.columns if c.lower() in ("pt", "pts", "punti", "points", "pti")),
            None,
        )
        dr_col = col_detect(df, "dr", "diff_reti", "gd")

        sq_col = None
        for c in df.columns:
            if c.lower() in ("squadra_id", "team_id", "id_squadra"):
                sq_col = c
                break
        if sq_col is None:
            for c in df.columns:
                if c.lower() in EXCLUDE:
                    continue
                s = pd.to_numeric(df[c], errors="coerce").dropna()
                if len(s) > 0 and 1 <= s.min() < 10_000:
                    sq_col = c
                    break

        if sq_col is None:
            nome_col = next(
                (c for c in df.columns if c.lower() in ("squadra", "team", "nome")),
                None,
            )
            if nome_col and pts_col:
                try:
                    df_sq = pd.read_sql("SELECT id, nome FROM squadre", self.engine)
                    df = df.merge(df_sq, left_on=nome_col, right_on="nome", how="inner")
                    sq_col = "id"
                except Exception:
                    pass

        if sq_col is None or pts_col is None:
            return None

        sort_cols = [pts_col] + ([dr_col] if dr_col else [])
        df = df.sort_values(sort_cols, ascending=False).reset_index(drop=True)
        df["posizione"] = range(1, len(df) + 1)
        result = df[[sq_col, pts_col, "posizione"]].rename(
            columns={sq_col: "squadra_id", pts_col: "punti"}
        )
        result["squadra_id"] = pd.to_numeric(
            result["squadra_id"], errors="coerce"
        ).astype("Int64")
        return result

    def load_squad_names(self) -> dict[int, str]:
        try:
            df = pd.read_sql("SELECT id, nome FROM squadre", self.engine)
            return {int(r["id"]): str(r["nome"]) for _, r in df.iterrows()}
        except Exception:
            return {}

    def load_roster(self) -> pd.DataFrame:
        try:
            return pd.read_sql(
                """
                SELECT
                    g.id          AS giocatore_id,
                    TRIM(CASE
                        WHEN g.cognome IS NULL OR TRIM(g.cognome) = '' THEN g.nome
                        WHEN LOWER(g.nome) LIKE LOWER(CONCAT('%%', g.cognome, '%%')) THEN g.nome
                        ELSE CONCAT_WS(' ', NULLIF(TRIM(g.nome), ''), NULLIF(TRIM(g.cognome), ''))
                    END) AS giocatore,
                    g.ruolo,
                    sq.nome       AS squadra,
                    COALESCE(pa.minuti, 0) AS minuti
                FROM   giocatori g
                JOIN   squadre sq ON sq.id = g.squadra_id
                LEFT JOIN t_player_analytics pa ON pa.giocatore_id = g.id
                WHERE  g.ruolo != 'POR'
                ORDER  BY sq.nome, g.ruolo, ISNULL(pa.minuti), pa.minuti DESC
                """,
                self.engine,
            )
        except Exception as e:
            log.warning(f"Roster non caricabile: {e}")
            return pd.DataFrame()

    def detect_xg_columns(self) -> tuple[str, str]:
        schema = pd.read_sql(
            "SELECT * FROM t_squadra_game_log LIMIT 1", self.engine
        )
        xg = col_detect(schema, "xg", "xg_squadra", "goal_exp", "expected_goals") or "xg"
        xg_avv = col_detect(schema, "xg_avversario", "xg_avv", "xg_concessi", "xa") or "xg_avversario"
        log.info(f"Colonne xG rilevate: '{xg}', '{xg_avv}'")
        return xg, xg_avv


# ════════════════════════════════════════════════════════════════
# 4. CLASSIFICHE E TOP-N
# ════════════════════════════════════════════════════════════════
def compute_top6(
    df_class: pd.DataFrame | None,
    df_sgl: pd.DataFrame,
    n: int,
) -> set[int]:
    if df_class is not None and len(df_class) >= n:
        ids = set(
            int(x)
            for x in df_class[df_class["posizione"] <= n]["squadra_id"].dropna()
        )
        if len(ids) >= n:
            return ids

    all_ids = sorted(int(x) for x in df_sgl["squadra_id"].dropna().unique())
    log.warning(f"Classifica incompleta: top {n} per ID ({all_ids[:n]})")
    return set(all_ids[:n])


# ════════════════════════════════════════════════════════════════
# 5. SOGLIA MINUTI ADATTIVA
# ════════════════════════════════════════════════════════════════
def compute_minute_thresholds(
    df_gp: pd.DataFrame,
    n_giornate: int,
    cfg: Config,
) -> tuple[int, int, int, set[int]]:
    all_minutes = (
        df_gp[df_gp["minuti"] > 0]
        .groupby("giocatore_id")["minuti"]
        .sum()
    )

    base_full = int(n_giornate * 90 * cfg.min_minutes_pct)
    base_winter = int(n_giornate * 90 * cfg.min_minutes_pct_winter)

    if cfg.use_adaptive_threshold and len(all_minutes) > 10:
        p30 = int(np.percentile(all_minutes, 30))
        min_full = min(base_full, p30)
        log.info(
            f"Soglia adattiva: base={base_full}', p30={p30}' → soglia={min_full}'"
        )
    else:
        min_full = base_full

    min_winter = base_winter
    winter_threshold = int(n_giornate * cfg.winter_debut_fraction)

    first_giornata = (
        df_gp[df_gp["minuti"] > 0]
        .groupby("giocatore_id")["giornata"]
        .min()
    )
    winter_ids = {
        int(gid)
        for gid, fg in first_giornata.items()
        if fg > winter_threshold
    }

    log.info(
        f"Giornate: {n_giornate} | Soglia titolari: {min_full}' | "
        f"Soglia invernale: {min_winter}' | Acquisti invernali: {len(winter_ids)}"
    )
    return min_full, min_winter, winter_threshold, winter_ids


def filter_qualified_players(
    df_pa: pd.DataFrame,
    df_gp: pd.DataFrame,
    min_full: int,
    min_winter: int,
    winter_ids: set[int],
) -> pd.DataFrame:
    minutes_per_player = (
        df_gp[df_gp["minuti"] > 0]
        .groupby("giocatore_id")["minuti"]
        .sum()
        .rename("minuti_effettivi")
    )
    df = df_pa.copy()
    df = df.merge(
        minutes_per_player.reset_index(), on="giocatore_id", how="left"
    )
    df["minuti_effettivi"] = df["minuti_effettivi"].fillna(0)
    df["is_winter"] = df["giocatore_id"].isin(winter_ids)

    threshold = np.where(df["is_winter"], min_winter, min_full)
    mask = df["minuti_effettivi"] >= threshold
    return df[mask].copy().reset_index(drop=True)


# ════════════════════════════════════════════════════════════════
# 6. CALCOLO DIMENSIONI TPI
# ════════════════════════════════════════════════════════════════
def _weighted_mean(values: pd.Series, weights: pd.Series) -> float | None:
    valid = weights.notna() & values.notna()
    if valid.sum() == 0:
        return None
    w = weights[valid].values
    v = values[valid].values
    if w.sum() == 0:
        return float(v.mean())
    return float(np.average(v, weights=w))


def compute_dimensions(
    df_con: pd.DataFrame,
    df_senza: pd.DataFrame,
    n_tot: int,
    sos_map: dict[int, float],
    xg_col: str,
    cfg: Config,
    k_mult: float = 1.0,
    ruolo_gp: str | None = None,
) -> dict | None:
    if len(df_con) < cfg.min_appearances_context:
        return None

    n_ctx = len(df_con)
    k_ctx = min(
        cfg.k_base * k_mult * (n_tot / max(n_ctx, 1)),
        cfg.k_base * k_mult * 8.0,
    )

    min_tot = float(df_con["minuti"].sum())
    xg_tot = float(df_con["xg_ind"].fillna(0).sum())
    xa_tot = float(df_con["xa_ind"].fillna(0).sum())

    if min_tot <= 0:
        return None

    output_p90 = (xg_tot + xa_tot) / min_tot * 90
    sos_vals = df_con["sos_avv"].fillna(1.0)
    sos_ctx = float(sos_vals.mean()) if len(sos_vals) > 0 else 1.0
    output_adj = output_p90 / sos_ctx if sos_ctx > 0 else output_p90

    xg_team_tot = float(df_con["xg_team"].fillna(0).sum())

    w_con = (1.0 / sos_vals.replace(0, np.nan)).clip(upper=10.0).fillna(1.0)
    xg_con_w = _weighted_mean(df_con["xg_team"].fillna(0), w_con)
    boost_ratio = None

    # Boost = log-ratio (xG squadra con/senza) shrinkato verso neutro (0),
    # pesato sul numero di gare "senza": n/(n+K). Simmetrico — si muove solo
    # con evidenza ben campionata, in qualsiasi direzione. Sotto la soglia
    # minima resta None (neutro) e il TPI si re-normalizza sulle altre dim.
    if df_senza is not None and xg_col in df_senza.columns:
        df_s = df_senza.copy()
        if ruolo_gp and "ruolo" in df_s.columns:
            df_s = df_s[df_s["ruolo"] == ruolo_gp]
        n_senza = len(df_s)
        if n_senza >= cfg.boost_min_senza:
            df_s["_sos"] = df_s["avversario_id"].map(sos_map).fillna(1.0)
            df_s["_w"] = (1.0 / df_s["_sos"].replace(0, np.nan)).clip(upper=10.0).fillna(1.0)
            xg_s_w = _weighted_mean(df_s[xg_col].fillna(0), df_s["_w"])
            if xg_con_w and xg_s_w and xg_con_w > 0 and xg_s_w > 0:
                lr = float(np.log(xg_con_w / xg_s_w))
                shrink = n_senza / (n_senza + cfg.boost_shrink_k)
                boost_ratio = round(float(np.exp(lr * shrink)), 4)

    out_pg = (
        (df_con["xg_ind"].fillna(0) + df_con["xa_ind"].fillna(0))
        / df_con["minuti"].replace(0, np.nan) * 90
    ).dropna()
    consistenza = None
    if len(out_pg) >= 5 and out_pg.mean() > 0:
        consistenza = consistenza_robusta(out_pg)

    sorted_gp = df_con.sort_values("giornata")
    out_ord = (
        (sorted_gp["xg_ind"].fillna(0) + sorted_gp["xa_ind"].fillna(0))
        / sorted_gp["minuti"].replace(0, np.nan) * 90
    ).fillna(0).tolist()

    ewma = out_ord[0] if out_ord else None
    for v in out_ord[1:]:
        ewma = cfg.ewma_alpha * v + (1 - cfg.ewma_alpha) * ewma

    def _r(v: Any, d: int = 4) -> Any:
        return round(float(v), d) if (v is not None and not pd.isna(v)) else None

    return {
        "output_p90": _r(output_p90),
        "output_adj": _r(output_adj),
        "xg_tot": _r(xg_tot),
        "xa_tot": _r(xa_tot),
        "xg_team_tot": _r(xg_team_tot),
        "min_tot": int(min_tot),
        "n_partite": int(n_ctx),
        "k_ctx": round(k_ctx, 2),
        "sos_ctx": _r(sos_ctx),
        "boost_ratio": boost_ratio,
        "consistenza": consistenza,
        "form_ewma": _r(ewma),
    }


# ════════════════════════════════════════════════════════════════
# 7. CALCOLO PER TUTTI I GIOCATORI × 5 CONTESTI
# ════════════════════════════════════════════════════════════════
def compute_all_contexts(
    df_pa: pd.DataFrame,
    df_gp: pd.DataFrame,
    df_sgl: pd.DataFrame,
    sos_map: dict[int, float],
    top6_ids: set[int],
    sos_per_sq: pd.Series,
    xg_col: str,
    cfg: Config,
) -> dict[int, dict]:
    all_ctx: dict[int, dict] = {}
    sos_per_sq_dict = sos_per_sq.to_dict()

    for _, prow in df_pa.iterrows():
        gid = int(prow["giocatore_id"])
        sq_id = int(prow["squadra_id"])

        gp_all = df_gp[
            (df_gp["giocatore_id"] == gid) & (df_gp["minuti"] > 0)
        ].copy()
        n_tot = len(gp_all)
        if n_tot == 0:
            continue

        is_winter = bool(prow.get("is_winter", False))
        k_mult = cfg.k_winter_multiplier if is_winter else 1.0

        cal_con = set(gp_all["calendario_id"])
        sq_log = df_sgl[df_sgl["squadra_id"] == sq_id].copy()
        sq_senza = sq_log[~sq_log["calendario_id"].isin(cal_con)]

        sos_no_sq = {k: v for k, v in sos_per_sq_dict.items() if k != sq_id}
        top_dif_ids = set(sorted(sos_no_sq, key=sos_no_sq.get)[:cfg.n_top_difese])
        top6_pers = top6_ids - {sq_id}

        has_ruolo_sgl = cfg.sgl_ruolo_col in sq_senza.columns
        has_avv_sgl = "avversario_id" in sq_senza.columns

        filtri = {
            "totale":    gp_all,
            "casa":      gp_all[gp_all["ruolo_gp"] == "casa"],
            "trasferta": gp_all[gp_all["ruolo_gp"] == "trasferta"],
            "vs_top6":   gp_all[gp_all["avversario_id"].isin(top6_pers)],
            "vs_forti":  gp_all[gp_all["avversario_id"].isin(top_dif_ids)],
        }

        senza = {
            "totale":    sq_senza,
            "casa":      sq_senza[sq_senza[cfg.sgl_ruolo_col].eq("casa")] if has_ruolo_sgl else sq_senza,
            "trasferta": sq_senza[sq_senza[cfg.sgl_ruolo_col].eq("trasferta")] if has_ruolo_sgl else sq_senza,
            "vs_top6":   sq_senza[sq_senza["avversario_id"].isin(top6_pers)] if has_avv_sgl else sq_senza.iloc[:0],
            "vs_forti":  sq_senza[sq_senza["avversario_id"].isin(top_dif_ids)] if has_avv_sgl else sq_senza.iloc[:0],
        }

        all_ctx[gid] = {}
        for ctx in CONTESTI:
            ruolo_strat = ctx if ctx in ("casa", "trasferta") else None
            all_ctx[gid][ctx] = compute_dimensions(
                filtri[ctx],
                senza[ctx],
                n_tot,
                sos_map,
                xg_col,
                cfg,
                k_mult=k_mult,
                ruolo_gp=ruolo_strat,
            )

    log.info(f"Contesti calcolati per {len(all_ctx)} giocatori")
    return all_ctx


# ════════════════════════════════════════════════════════════════
# 8. PRIOR BAYESIANO PER CENTRALITÀ
# ════════════════════════════════════════════════════════════════
def compute_bayesian_priors(
    df_pa: pd.DataFrame,
    all_ctx: dict[int, dict],
    ref_roles: set[str] = frozenset(["ATT", "CEN"]),
) -> dict[str, float]:
    prior_ctx: dict[str, float] = {}
    ref_gids = set(
        int(row["giocatore_id"])
        for _, row in df_pa.iterrows()
        if str(row.get("ruolo", "")) in ref_roles
    )

    for ctx in CONTESTI:
        xg_n = xa_n = xgt_d = 0.0
        for gid in ref_gids:
            d = (all_ctx.get(gid) or {}).get(ctx)
            if d:
                xg_n += d.get("xg_tot") or 0
                xa_n += d.get("xa_tot") or 0
                xgt_d += d.get("xg_team_tot") or 0
        prior_ctx[ctx] = (xg_n + xa_n) / xgt_d if xgt_d > 0 else 0.15
        log.info(f"Prior centralità [{ctx:12s}]: {prior_ctx[ctx]:.4f}")

    return prior_ctx


def apply_bayesian_centrality(
    all_ctx: dict[int, dict],
    prior_ctx: dict[str, float],
) -> None:
    for gid, ctx_res in all_ctx.items():
        for ctx, d in ctx_res.items():
            if d is None:
                continue
            prior = prior_ctx.get(ctx, 0.15)
            k = d["k_ctx"]
            num = (d.get("xg_tot") or 0) + (d.get("xa_tot") or 0)
            den = d.get("xg_team_tot") or 0
            d["centralita"] = (
                round((num + prior * k) / (den + k) * 100, 4) if den > 0 else None
            )


# ════════════════════════════════════════════════════════════════
# 9. GOALS vs xG — CONVERSION METRICS
# ════════════════════════════════════════════════════════════════
def compute_conversion_metrics(
    df_gp: pd.DataFrame,
) -> tuple[pd.DataFrame, dict[int, dict]]:
    conv_rows: list[dict] = []
    conv_detail: dict[int, dict] = {}

    for gid, grp in df_gp[df_gp["minuti"] > 0].groupby("giocatore_id"):
        gid = int(gid)
        xg_tot = float(grp["xg_ind"].fillna(0).sum())          # npxG (no rigori)
        goal_tot = float(grp["npg_ind"].fillna(0).sum())        # gol no-rigore
        min_tot = float(grp["minuti"].sum())

        conv_ratio = round(goal_tot / xg_tot, 3) if xg_tot >= 0.5 else None
        fq = round((goal_tot - xg_tot) / np.sqrt(xg_tot), 4) if xg_tot > 0 else None
        goal_p90 = round(goal_tot / min_tot * 90, 3) if min_tot > 0 else None
        xg_p90 = round(xg_tot / min_tot * 90, 3) if min_tot > 0 else None
        goal_minus_xg = round(goal_tot - xg_tot, 3)

        conv_trend = None
        grp_s = grp.sort_values("giornata")
        if len(grp_s) >= 8:
            half = len(grp_s) // 2
            xg_1h = float(grp_s.iloc[:half]["xg_ind"].fillna(0).sum())
            g_1h = float(grp_s.iloc[:half]["npg_ind"].fillna(0).sum())
            xg_2h = float(grp_s.iloc[half:]["xg_ind"].fillna(0).sum())
            g_2h = float(grp_s.iloc[half:]["npg_ind"].fillna(0).sum())
            cr_1h = (g_1h / xg_1h) if xg_1h >= 0.5 else None
            cr_2h = (g_2h / xg_2h) if xg_2h >= 0.5 else None
            if cr_1h and cr_2h and cr_1h > 0:
                conv_trend = round(cr_2h / cr_1h - 1, 3)

        grp_s = grp_s.copy()
        grp_s["_gcum"] = grp_s["npg_ind"].fillna(0).cumsum()
        grp_s["_xcum"] = grp_s["xg_ind"].fillna(0).cumsum()

        def _ints(s: pd.Series) -> list:
            return [None if pd.isna(x) else int(x) for x in s]

        def _flts(s: pd.Series) -> list:
            return [None if pd.isna(x) else round(float(x), 3) for x in s]

        conv_detail[gid] = {
            "g_giornate": _ints(grp_s["giornata"]),
            "g_goal_pg": _ints(grp_s["npg_ind"].fillna(0)),
            "g_xg_pg": _flts(grp_s["xg_ind"].fillna(0)),
            "g_goal_cum": _ints(grp_s["_gcum"]),
            "g_xg_cum": _flts(grp_s["_xcum"]),
        }

        conv_rows.append({
            "giocatore_id": gid,
            "goal_tot": int(goal_tot),
            "xg_tot_conv": round(xg_tot, 3),
            "goal_p90": goal_p90,
            "xg_p90": xg_p90,
            "conv_ratio": conv_ratio,
            "finishing_quality": fq,
            "goal_minus_xg": goal_minus_xg,
            "conv_trend": conv_trend,
        })

    return pd.DataFrame(conv_rows), conv_detail


# ════════════════════════════════════════════════════════════════
# 10. FORM EWMA
# ════════════════════════════════════════════════════════════════
def compute_form_metrics(
    df_gp: pd.DataFrame,
    cfg: Config,
) -> tuple[pd.DataFrame, dict[int, dict]]:
    form_rows: list[dict] = []
    form_detail: dict[int, dict] = {}

    for gid, grp in df_gp[df_gp["minuti"] > 0].groupby("giocatore_id"):
        gid = int(gid)
        grp_s = grp.sort_values("giornata")
        out_pg = (
            (grp_s["xg_ind"].fillna(0) + grp_s["xa_ind"].fillna(0))
            / grp_s["minuti"].replace(0, np.nan) * 90
        ).fillna(0).tolist()
        giornate = [None if pd.isna(x) else int(x) for x in grp_s["giornata"]]

        if len(out_pg) >= cfg.min_appearances_form:
            ewma, ewma_s = out_pg[0], [round(out_pg[0], 4)]
            for v in out_pg[1:]:
                ewma = cfg.ewma_alpha * v + (1 - cfg.ewma_alpha) * ewma
                ewma_s.append(round(ewma, 4))
            media = float(np.mean(out_pg))
            trend = round(ewma_s[-1] / media - 1, 3) if media > 0 else None
        else:
            ewma_s = [round(v, 4) for v in out_pg]
            trend = None

        form_detail[gid] = {
            "form_g": giornate,
            "form_out": [round(v, 4) for v in out_pg],
            "form_ewma_s": ewma_s,
        }
        form_rows.append({
            "giocatore_id": gid,
            "form_ewma": round(ewma_s[-1], 4) if ewma_s else None,
            "form_trend": trend,
        })

    return pd.DataFrame(form_rows), form_detail


# ════════════════════════════════════════════════════════════════
# 11. KPI AGGIUNTIVI
# ════════════════════════════════════════════════════════════════
def compute_player_kpis(
    df_pa: pd.DataFrame,
    df_gp: pd.DataFrame,
    df_sgl: pd.DataFrame,
    sos_map: dict[int, float],
    xg_col: str,
) -> pd.DataFrame:
    xa_agg = (
        df_gp[df_gp["minuti"] > 0]
        .groupby("giocatore_id")
        .agg(xa_sum=("xa_ind", "sum"), min_sum=("minuti", "sum"))
        .eval("xa_p90 = xa_sum / min_sum * 90")
        .reset_index()[["giocatore_id", "xa_p90"]]
    )

    sos_agg = (
        df_gp[df_gp["minuti"] > 0]
        .groupby("giocatore_id")["sos_avv"]
        .mean()
        .reset_index()
        .rename(columns={"sos_avv": "sos"})
    )

    xg_con_agg = (
        df_gp[df_gp["minuti"] > 0]
        .groupby("giocatore_id")["xg_team"]
        .mean()
        .reset_index()
        .rename(columns={"xg_team": "xg_squadra_con"})
    )

    sgl_by_sq: dict[int, pd.DataFrame] = {}
    if xg_col in df_sgl.columns:
        for sq_id, grp in df_sgl.groupby("squadra_id"):
            sgl_by_sq[int(sq_id)] = grp

    xg_senza_rows: list[dict] = []
    cal_con_by_gid = (
        df_gp[df_gp["minuti"] > 0]
        .groupby("giocatore_id")["calendario_id"]
        .apply(set)
        .to_dict()
    )

    for _, row in df_pa.iterrows():
        gid = int(row["giocatore_id"])
        sq_id = int(row["squadra_id"])
        cal_con = cal_con_by_gid.get(gid, set())
        sq_log = sgl_by_sq.get(sq_id, pd.DataFrame())
        if len(sq_log) > 0:
            senza = sq_log[~sq_log["calendario_id"].isin(cal_con)]
            xg_senza = float(senza[xg_col].mean()) if len(senza) > 0 else None
        else:
            xg_senza = None
        xg_senza_rows.append({"giocatore_id": gid, "xg_squadra_senza": xg_senza})

    df_kpi = df_pa[["giocatore_id"]].copy()
    for df_add in [xa_agg, sos_agg, xg_con_agg, pd.DataFrame(xg_senza_rows)]:
        df_kpi = df_kpi.merge(df_add, on="giocatore_id", how="left")

    return df_kpi


# ════════════════════════════════════════════════════════════════
# 12. Z-SCORE PER CONTESTO E DIMENSIONE
# ════════════════════════════════════════════════════════════════
def z_series(
    vals: pd.Series,
    mask: pd.Series,
    fallback: pd.Series | None = None,
    min_ref: int = 8,
    winsorize_pct: float = 0.05,
) -> pd.Series:
    """
    Z-score robusto con Winsorization prima della standardizzazione.
    Winsorize taglia il top/bottom 5% prima di calcolare μ e σ,
    rendendo il modello resistente agli outlier estremi.
    Clip finale a ±3 per prevenire leverage eccessivo.
    """
    ref = vals[mask].dropna()
    if len(ref) < min_ref and fallback is not None:
        ref = fallback[mask].dropna()
    if len(ref) < 3:
        return pd.Series(np.nan, index=vals.index)
    # Winsorize: clamp i valori all'[p5, p95] prima di calcolare μ/σ
    lo = ref.quantile(winsorize_pct)
    hi = ref.quantile(1.0 - winsorize_pct)
    ref_w = ref.clip(lo, hi)
    mu, sigma = ref_w.mean(), ref_w.std()
    if sigma > 0:
        return ((vals - mu) / sigma).clip(-3, 3)
    return pd.Series(0.0, index=vals.index)


def consistenza_robusta(out_pg: pd.Series) -> float | None:
    """
    Consistenza basata su IQR invece di CV classico (1-σ/μ).
    Il CV classico è instabile quando μ → 0 e sensibile agli outlier.
    IQR-based: 1 - (Q75-Q25) / (mediana + ε) — robusto e interpretabile.
    Fallback al CV classico se IQR=0 (distribuzione puntiforme).
    """
    if len(out_pg) < 5 or out_pg.median() <= 0:
        return None
    q25, q75 = out_pg.quantile(0.25), out_pg.quantile(0.75)
    iqr = q75 - q25
    med = out_pg.median()
    if iqr == 0:
        # Distribuzione molto consistente → consistenza alta
        cv = out_pg.std() / (out_pg.mean() + 1e-9)
        return max(0.0, round(1.0 - cv, 4))
    robust_cv = iqr / (med + 1e-9)
    return max(0.0, round(1.0 - robust_cv, 4))


# ════════════════════════════════════════════════════════════════
# 13. TREND xG SQUADRA
# ════════════════════════════════════════════════════════════════
def get_trend_xg(
    gid: int,
    sq_id: int,
    df_gp: pd.DataFrame,
    df_sgl: pd.DataFrame,
    sos_map: dict[int, float],
    xg_col: str,
    cfg: Config,
) -> dict:
    sq_id = int(sq_id)
    pg = df_gp[
        (df_gp["giocatore_id"] == gid) & (df_gp["minuti"] > 0)
    ].sort_values("giornata")

    if len(pg) == 0:
        return {}

    sos_no = {k: v for k, v in sos_map.items() if k != sq_id}
    top_ids = set(sorted(sos_no, key=sos_no.get)[:cfg.n_top_difese])
    weak_ids = set(sorted(sos_no, key=sos_no.get, reverse=True)[:cfg.n_top_difese])

    rows = []
    for _, r in pg.iterrows():
        avv = int(r["avversario_id"]) if not pd.isna(r["avversario_id"]) else None
        rows.append({
            "g": int(r["giornata"]),
            "xg": float(r["xg_team"]) if not pd.isna(r["xg_team"]) else None,
            "f": (avv in top_ids) if avv is not None else False,
            "d": (avv in weak_ids) if avv is not None else False,
        })

    df_t = pd.DataFrame(rows)
    by_g = df_t.groupby("g")["xg"].mean()
    by_f = df_t[df_t["f"]].groupby("g")["xg"].mean()
    by_d = df_t[df_t["d"]].groupby("g")["xg"].mean()

    cal_con = set(pg["calendario_id"])
    sq_senza = df_sgl[df_sgl["squadra_id"] == sq_id]
    sq_senza = sq_senza[~sq_senza["calendario_id"].isin(cal_con)]
    by_senza = pd.Series(dtype=float)
    if xg_col in sq_senza.columns and len(sq_senza) > 0:
        by_senza = sq_senza.groupby("giornata")[xg_col].mean()

    gg_all = sorted(
        set(int(x) for x in by_g.index)
        | set(int(x) for x in by_senza.index)
    )

    def sl(s: pd.Series) -> list:
        return [
            None if (v is None or (isinstance(v, float) and pd.isna(v)))
            else round(float(v), 3)
            for v in s.reindex(gg_all)
        ]

    return {
        "g": gg_all,
        "con": sl(by_g),
        "forti": sl(by_f),
        "deboli": sl(by_d),
        "senza": sl(by_senza),
        "media_con": round(float(by_g.mean()), 3) if len(by_g) > 0 else None,
        "media_senza": round(float(by_senza.mean()), 3) if len(by_senza) > 0 else None,
        "n_senza": int(len(sq_senza)),
    }


# ════════════════════════════════════════════════════════════════
# 13b. NUOVI INDICI v2 — ETA INDEX & AFFIDABILITÀ FISICA
# ════════════════════════════════════════════════════════════════
import math as _math

def compute_age_index(eta: float | None, cfg: Config) -> float | None:
    """
    Age Impact Index (AII) — indice composito [0, 1].

    Componenti:
      50% PeakProximity  — Gaussiana centrata a peak_age (default 27)
      30% ExperienceFactor — cresce con gli anni di carriera, satura a 1.0
      20% Freshness      — 1.0 fino a peak_age, poi declina del 5%/anno

    Returns None se età non disponibile o fuori range [15, 45].
    """
    if eta is None or not (15 <= eta <= 45):
        return None

    # 1. Peak proximity (Gaussian)
    peak_score = _math.exp(-0.5 * ((eta - cfg.age_peak) / cfg.age_sigma) ** 2)

    # 2. Experience factor (proxy: anni da inizio carriera professionale ~17)
    experience = min(1.0, max(0.0, (eta - 17.0) / cfg.age_exp_cap))

    # 3. Physical freshness (declina dopo peak_age)
    if eta <= cfg.age_peak:
        freshness = 1.0
    else:
        freshness = max(0.0, 1.0 - (eta - cfg.age_peak) * 0.05)

    return round(0.50 * peak_score + 0.30 * experience + 0.20 * freshness, 4)


def compute_physical_reliability(
    partite_giocate: int,
    partite_disponibili: int,
    n_infortuni: int,
    giorni_out: int,
    cfg: Config,
) -> float | None:
    """
    Physical Reliability Index (PRI) — indice composito [0, 1].

    Componenti:
      50% Disponibilità  — partite_giocate / partite_disponibili
      30% InjuryFree     — 1.0 − min(n_infortuni, 5) × pri_inj_penalty  ← cap a 5
      20% SeverityOk     — 1.0 − giorni_out / pri_severity_cap

    Correzione bias: partite_disponibili arriva già corretto dalla funzione chiamante.
    Returns None se partite_disponibili < pri_min_partite.
    """
    if partite_disponibili < cfg.pri_min_partite:
        return None

    disponibilita = min(1.0, partite_giocate / max(partite_disponibili, 1))
    # Cap a 5 infortuni: oltre quel numero la penalità è già massima (0.0)
    injury_free   = max(0.0, 1.0 - min(n_infortuni, int(1.0 / cfg.pri_inj_penalty)) * cfg.pri_inj_penalty)
    severity_ok   = max(0.0, 1.0 - min(giorni_out / cfg.pri_severity_cap, 1.0))

    return round(
        0.50 * disponibilita + 0.30 * injury_free + 0.20 * severity_ok,
        4
    )


def load_age_physical_data(engine, df_pa: pd.DataFrame, df_gp: pd.DataFrame, cfg: Config) -> pd.DataFrame:
    """
    Carica/calcola AII e PRI per tutti i giocatori qualificati.

    Fonti:
    - AII: data_nascita da giocatori.data_nascita
    - PRI: t_infortuni (se presente) + n_partite dal calendario

    Restituisce DataFrame con colonne: giocatore_id, eta, eta_index,
    partite_disponibili, n_infortuni, giorni_out, affidabilita_fisica
    """
    import datetime

    rows: list[dict] = []
    oggi = datetime.date.today()

    # ── Carica date di nascita ────────────────────────────────
    try:
        df_birth = pd.read_sql(
            "SELECT id AS giocatore_id, data_nascita FROM giocatori WHERE data_nascita IS NOT NULL",
            engine,
        )
        birth_map = {
            int(r["giocatore_id"]): r["data_nascita"]
            for _, r in df_birth.iterrows()
            if r["data_nascita"] is not None
        }
    except Exception as e:
        log.warning(f"data_nascita non disponibile: {e}")
        birth_map = {}

    # ── Carica infortuni ──────────────────────────────────────
    inj_map: dict[int, dict] = {}
    try:
        df_inj = pd.read_sql(
            "SELECT giocatore_id, COUNT(*) AS n_inj, SUM(COALESCE(giorni_out,0)) AS gg_out "
            "FROM t_infortuni GROUP BY giocatore_id",
            engine,
        )
        for _, r in df_inj.iterrows():
            inj_map[int(r["giocatore_id"])] = {
                "n": int(r["n_inj"]),
                "gg": int(r["gg_out"] or 0),
            }
    except Exception as e:
        log.debug(f"t_infortuni non presente o vuota: {e}")

    # ── Partite disponibili per giocatore ─────────────────────
    # FIX bias: usiamo n_giornate_tot per titolari e aggiustiamo solo per invernali.
    # NON usiamo first_g perché un giocatore infortunato a inizio stagione avrebbe
    # first_g alta → partite_disp bassa → disponibilità artificialmente alta.
    n_giornate_tot = int(df_gp["giornata"].nunique()) if len(df_gp) > 0 else 0
    max_g = int(df_gp["giornata"].max()) if len(df_gp) > 0 else n_giornate_tot

    # Prima giornata *nella rosa* (non prima giocata)
    first_g_giocata = (
        df_gp[df_gp["minuti"] > 0]
        .groupby("giocatore_id")["giornata"]
        .min()
        .to_dict()
    )

    # Per invernali (is_winter=True): partiamo dalla prima presenza reale
    # Per titolari: usiamo n_giornate_tot (disponibili da GG1)
    winter_ids_set = set(df_pa[df_pa["is_winter"] == True]["giocatore_id"].astype(int).tolist())

    giocate_map = (
        df_gp[df_gp["minuti"] > 0]
        .groupby("giocatore_id")["giornata"]
        .count()
        .to_dict()
    )

    # ── Calcola per ogni giocatore qualificato ────────────────
    for _, prow in df_pa.iterrows():
        gid = int(prow["giocatore_id"])

        # AII
        eta: float | None = None
        if gid in birth_map:
            try:
                bd = birth_map[gid]
                if hasattr(bd, "year"):
                    eta = round((oggi - bd.date() if hasattr(bd, "date") else oggi - bd).days / 365.25, 2)
                else:
                    import datetime as _dt
                    if isinstance(bd, str):
                        bd = _dt.date.fromisoformat(bd[:10])
                    eta = round((oggi - bd).days / 365.25, 2)
            except Exception:
                pass

        aii = compute_age_index(eta, cfg)

        # PRI
        # Partite disponibili: titolari = n_giornate_tot, invernali = dalla prima presenza
        if gid in winter_ids_set:
            fg = int(first_g_giocata.get(gid, max_g))
            partite_disp = max(0, max_g - fg + 1)
        else:
            partite_disp = n_giornate_tot
        partite_gioc = int(giocate_map.get(gid, 0))
        inj = inj_map.get(gid, {"n": 0, "gg": 0})
        pri = compute_physical_reliability(
            partite_gioc, partite_disp,
            inj["n"], inj["gg"], cfg
        )

        rows.append({
            "giocatore_id":      gid,
            "eta":               eta,
            "eta_index":         aii,
            "partite_disponibili": partite_disp,
            "partite_giocate":   partite_gioc,
            "n_infortuni":       inj["n"],
            "giorni_out":        inj["gg"],
            "affidabilita_fisica": pri,
        })

    result = pd.DataFrame(rows)
    log.info(
        f"AII calcolato per {result['eta_index'].notna().sum()}/{len(result)} giocatori "
        f"(date nascita: {len(birth_map)})"
    )
    log.info(
        f"PRI calcolato per {result['affidabilita_fisica'].notna().sum()}/{len(result)} giocatori "
        f"(infortuni registrati: {len(inj_map)})"
    )
    return result


# ════════════════════════════════════════════════════════════════
# 14. NARRATIVA AI (opzionale)
# ════════════════════════════════════════════════════════════════
def genera_narrativa(row: pd.Series, api_key: str) -> str:
    if not api_key:
        return ""
    try:
        import urllib.request

        ft = row.get("form_trend")
        ftx = (
            f"in crescita ({ft:+.0%})" if ft and ft > 0.10
            else f"in calo ({ft:+.0%})" if ft and ft < -0.10
            else "stabile"
        )
        fq = row.get("finishing_quality")
        fqx = (
            f"sopra media FQ={fq:.2f}" if fq and fq > 0.5
            else f"spreca occasioni FQ={fq:.2f}" if fq and fq < -0.5
            else "nella norma"
        )
        cr = row.get("conv_ratio")
        crx = f"G/xG={cr:.2f}" if cr else "G/xG n.d."

        nome_display = row.get("giocatore", "")

        prompt = f"""Analista calcio quantitativo. Analisi concisa in 3 frasi:

{nome_display} ({row['squadra']}, {row.get('ruolo','')})
TPI totale: {row['TPI_totale']:.2f} | casa: {row.get('TPI_casa','?')} | trasferta: {row.get('TPI_trasferta','?')}
TPI vs top6: {row.get('TPI_vs_top6','?')} | vs difese solide: {row.get('TPI_vs_forti','?')}
Output adj/90: {row.get('totale_output_adj','?')} | Centralità: {row.get('totale_centralita','?')}%
Team boost: {row.get('totale_boost_ratio','?')}x | Consistenza: {row.get('totale_consistenza','?')}
Conversione: {crx} | Finishing: {fqx} | Form: {ftx}

Frase 1: punto di forza principale (con numeri specifici)
Frase 2: analisi conversione G/xG e implicazioni
Frase 3: raccomandazione pratica per staff tecnico/ds
Solo italiano. Solo testo."""

        body = json.dumps({
            "model": "claude-sonnet-4-20250514",
            "max_tokens": 280,
            "messages": [{"role": "user", "content": prompt}],
        }).encode()
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
            },
        )
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read())["content"][0]["text"].strip()
    except Exception as e:
        log.warning(f"AI error ({row.get('giocatore','?')}): {e}")
        return ""


# ════════════════════════════════════════════════════════════════
# 15. BUILD PAYLOAD JSON
# ════════════════════════════════════════════════════════════════
def build_payload(
    df_pa: pd.DataFrame,
    all_ctx: dict[int, dict],
    conv_detail: dict[int, dict],
    form_detail: dict[int, dict],
    trend_cache: dict[int, dict],
    cfg: Config,
) -> list[dict]:
    """Costruisce la lista di oggetti JSON per la dashboard."""
    n_total = len(df_pa)
    payload: list[dict] = []

    for _, row in df_pa.head(cfg.top_n_payload).iterrows():
        gid = int(row["giocatore_id"])
        fd = form_detail.get(gid, {"form_g": [], "form_out": [], "form_ewma_s": []})
        cd = conv_detail.get(gid, {
            "g_giornate": [], "g_goal_pg": [], "g_xg_pg": [],
            "g_goal_cum": [], "g_xg_cum": [],
        })
        tr = trend_cache.get(gid, {})

        def ctx_entry(ctx: str) -> dict:
            d = (all_ctx.get(gid) or {}).get(ctx) or {}
            out: dict = {}
            for dim in DIMS:
                out[dim] = safe_json(row.get(f"{ctx}_{dim}"))
                out[f"z_{dim}"] = safe_json(row.get(f"z_{ctx}_{dim}"))
            out["TPI"] = safe_json(row.get(f"TPI_{ctx}"))
            out["n_app"] = int(d.get("n_partite", 0))
            out["k_ctx"] = safe_json(d.get("k_ctx"))
            out["sos_ctx"] = safe_json(d.get("sos_ctx"))
            return out

        rk = {
            "TPI": int(row.get("rank_TPI", 0) or 0),
            "output_adj": int(row.get("rank_output_adj", 0) or 0),
            "centralita": int(row.get("rank_centralita", 0) or 0),
            "boost": int(row.get("rank_boost", 0) or 0),
            "consistenza": int(row.get("rank_consistenza", 0) or 0),
            "conv": int(row.get("rank_conv", 0) or 0),
            "n_total": n_total,
        }

        entry = {
            "id": gid,
            # nome completo (per tooltip, confronto, ricerca)
            "nome": clean_str(row.get("nome_anagrafico") or row.get("giocatore") or f"#{gid}"),
            "squadra": clean_str(row["squadra"]),
            "ruolo": clean_str(row.get("ruolo") or ""),
            "minuti": safe_json(row["minuti"]),
            "is_winter": bool(row.get("is_winter", False)),
            "first_giornata": int(row.get("first_giornata", 1)),
            "tpi": {ctx: safe_json(row.get(f"TPI_{ctx}")) for ctx in CONTESTI},
            "ctx": {ctx: ctx_entry(ctx) for ctx in CONTESTI},
            "kpi": {
                "xg_p90": safe_json(row.get("xg_p90")),
                "xa_p90": safe_json(row.get("xa_p90")),
                "goal_p90": safe_json(row.get("goal_p90")),
                "sos": safe_json(row.get("sos")),
                "xg_con": safe_json(row.get("xg_squadra_con")),
                "xg_senza": safe_json(row.get("xg_squadra_senza")),
                "finishing": safe_json(row.get("finishing_quality")),
                "z_finishing": safe_json(row.get("z_finishing")),
            },
            "conv": {
                "goal_tot": safe_json(row.get("goal_tot")),
                "xg_tot": safe_json(row.get("xg_tot_conv")),
                "conv_ratio": safe_json(row.get("conv_ratio")),
                "z_conv": safe_json(row.get("z_conv_ratio")),
                "finishing_q": safe_json(row.get("finishing_quality")),
                "z_finishing": safe_json(row.get("z_finishing")),
                "goal_minus_xg": safe_json(row.get("goal_minus_xg")),
                "conv_trend": safe_json(row.get("conv_trend")),
                "goal_p90": safe_json(row.get("goal_p90")),
                "xg_p90_conv": safe_json(row.get("xg_p90")),
                "giornate": cd.get("g_giornate", []),
                "goal_pg": safe_json(cd.get("g_goal_pg", [])),
                "xg_pg": safe_json(cd.get("g_xg_pg", [])),
                "goal_cum": safe_json(cd.get("g_goal_cum", [])),
                "xg_cum": safe_json(cd.get("g_xg_cum", [])),
            },
            "rank": rk,
            "form": {
                "ewma":   safe_json(row.get("form_ewma")),
                "trend":  safe_json(row.get("form_trend")),
                "g":      fd["form_g"],
                "out":    safe_json(fd["form_out"]),
                "ewma_s": safe_json(fd["form_ewma_s"]),
            },
            "trend": tr,
            # ── Nuovi indici v2 ──────────────────────
            "tpi_ext": {ctx: safe_json(row.get(f"TPI_ext_{ctx}")) for ctx in CONTESTI},
            "physical": {
                "eta":               safe_json(row.get("eta")),
                "eta_index":         safe_json(row.get("eta_index")),
                "z_eta":             safe_json(row.get("z_eta_index")),
                "partite_disp":      safe_json(row.get("partite_disponibili")),
                "partite_gioc":      safe_json(row.get("partite_giocate")),
                "n_infortuni":       safe_json(row.get("n_infortuni")),
                "giorni_out":        safe_json(row.get("giorni_out")),
                "affidabilita":      safe_json(row.get("affidabilita_fisica")),
                "z_affidabilita":    safe_json(row.get("z_affidabilita_fisica")),
            },
            "ai": "",
            # ── Z-score dimensioni (per Differenziale) ──────
            "z_output":      safe_json(row.get("z_totale_output_adj")),
            "z_centralita":  safe_json(row.get("z_totale_centralita")),
            "z_boost":       safe_json(row.get("z_totale_boost_ratio")),
            "z_consistenza": safe_json(row.get("z_totale_consistenza")),
            "z_aii":         safe_json(row.get("z_eta_index")),
            "z_pri":         safe_json(row.get("z_affidabilita_fisica")),
            # ── Confidence score (0-1) ──────────────────────
            "confidence":    safe_json(row.get("tpi_confidence")),
        }
        payload.append(entry)

    return payload


# ════════════════════════════════════════════════════════════════
# 16. MAIN — ORCHESTRAZIONE
# ════════════════════════════════════════════════════════════════
class SafeEncoder(json.JSONEncoder):
    def default(self, obj: Any) -> Any:
        if isinstance(obj, np.integer):
            return int(obj)
        if isinstance(obj, np.floating):
            return None if np.isnan(obj) else float(obj)
        if isinstance(obj, np.ndarray):
            return obj.tolist()
        if isinstance(obj, np.bool_):
            return bool(obj)
        try:
            if pd.isna(obj):
                return None
        except (TypeError, ValueError):
            pass
        return super().default(obj)


def main() -> None:
    log.info("=" * 58)
    log.info("PARTE 1 — Analisi dati Serie A 25/26  v4.2")
    log.info("=" * 58)

    engine = create_engine(CFG.db_url)
    db = DatabaseLayer(engine)

    # ── Caricamento dati base ──────────────────────────────────
    sos_map = db.load_sos_map()
    sos_per_sq = pd.Series(sos_map)

    xg_col, xg_avv_col = db.detect_xg_columns()
    CFG.sgl_xg_col = xg_col
    CFG.sgl_xg_avv_col = xg_avv_col

    df_pa = db.load_players_analytics()
    df_sgl = db.load_squad_game_log()
    df_gp_raw = db.load_player_games(xg_col, xg_avv_col)

    # ── Override ruoli ─────────────────────────────────────────
    nome_col = "giocatore" if "giocatore" in df_pa.columns else df_pa.columns[0]
    overridden = 0
    for nome, ruolo_corretto in CFG.ruolo_override.items():
        # Prova prima su nome_anagrafico (CONCAT nome+cognome), poi su giocatore
        for col in ["nome_anagrafico", nome_col]:
            if col not in df_pa.columns:
                continue
            mask = df_pa[col] == nome
            if not mask.any():
                mask = df_pa[col].str.lower() == nome.lower()
            if mask.any():
                df_pa.loc[mask, "ruolo"] = ruolo_corretto
                overridden += mask.sum()
                break
    log.info(f"Override ruoli manuale applicato: {overridden} giocatori")

    # ── Ruolo da posizione Understat — PRECEDENZA FINALE ───────
    # Sovrascrive sia il DB sia l'override manuale dove c'è dato Understat
    # affidabile (≥200'): è la posizione realmente giocata, non una stima.
    import unicodedata as _ud2, html as _h2
    def _nm2(s):
        return _ud2.normalize("NFKD", _h2.unescape(str(s or ""))).encode("ascii", "ignore").decode().lower().strip()
    us_roles = derive_understat_roles()
    if us_roles:
        applied = 0
        for col in ("nome_anagrafico", nome_col):
            if col not in df_pa.columns:
                continue
            keymap = df_pa[col].map(_nm2)
            for idx, k in keymap.items():
                if k in us_roles and df_pa.at[idx, "ruolo"] != us_roles[k]:
                    df_pa.at[idx, "ruolo"] = us_roles[k]
                    applied += 1
            break  # basta la prima colonna disponibile
        log.info(f"Ruolo Understat (posizione reale) applicato: {applied} correzioni")

    # ── SOS per partita ────────────────────────────────────────
    df_gp_raw["sos_avv"] = df_gp_raw["avversario_id"].map(sos_map).astype(float)
    df_gp_raw["peso_sos"] = (1.0 / df_gp_raw["sos_avv"].replace(0, np.nan)).clip(upper=10.0)

    # ── Soglie minuti ──────────────────────────────────────────
    n_giornate = int(df_gp_raw["giornata"].nunique())
    min_full, min_winter, winter_threshold, winter_ids = compute_minute_thresholds(
        df_gp_raw, n_giornate, CFG
    )

    first_g = (
        df_gp_raw[df_gp_raw["minuti"] > 0]
        .groupby("giocatore_id")["giornata"]
        .min()
    )
    df_pa["is_winter"] = df_pa["giocatore_id"].isin(winter_ids)
    df_pa["first_giornata"] = df_pa["giocatore_id"].map(
        lambda x: int(first_g.get(int(x), 1))
    )

    # ── Filtra giocatori qualificati ───────────────────────────
    df_pa = filter_qualified_players(
        df_pa, df_gp_raw, min_full, min_winter, winter_ids
    )
    gids = set(int(x) for x in df_pa["giocatore_id"])
    df_gp = df_gp_raw[df_gp_raw["giocatore_id"].isin(gids)].copy()
    log.info(
        f"Giocatori qualificati: {len(df_pa)} "
        f"({df_pa['is_winter'].sum()} invernali)"
    )

    # ── Classifica e top6 ─────────────────────────────────────
    df_class = db.load_classification()
    top6_ids = compute_top6(df_class, df_sgl, CFG.n_top6_class)
    log.info(f"Top {CFG.n_top6_class}: {top6_ids}")

    # ── Calcolo 5 contesti ────────────────────────────────────
    all_ctx = compute_all_contexts(
        df_pa, df_gp, df_sgl, sos_map, top6_ids,
        sos_per_sq, xg_col, CFG
    )

    # ── Prior bayesiano centralità ─────────────────────────────
    prior_ctx = compute_bayesian_priors(df_pa, all_ctx)
    apply_bayesian_centrality(all_ctx, prior_ctx)

    # ── Conversion metrics ────────────────────────────────────
    log.info("Calcolo Goals vs xG...")
    df_conv, conv_detail = compute_conversion_metrics(df_gp)
    df_pa = df_pa.merge(df_conv, on="giocatore_id", how="left")

    # ── Form EWMA ─────────────────────────────────────────────
    log.info("Calcolo form EWMA...")
    df_form, form_detail = compute_form_metrics(df_gp, CFG)
    df_pa = df_pa.merge(df_form, on="giocatore_id", how="left")

    # ── KPI addizionali ───────────────────────────────────────
    log.info("Calcolo KPI aggiuntivi...")
    df_kpi = compute_player_kpis(df_pa, df_gp, df_sgl, sos_map, xg_col)
    df_pa = df_pa.merge(df_kpi, on="giocatore_id", how="left")

    # ── Nuovi indici v2: AII + PRI ────────────────────────────
    log.info("Calcolo Età Index (AII) e Affidabilità Fisica (PRI)...")
    df_physical = load_age_physical_data(engine, df_pa, df_gp, CFG)
    df_pa = df_pa.merge(df_physical, on="giocatore_id", how="left")

    # ── Dimensioni per contesto → colonne df_pa ───────────────
    for ctx in CONTESTI:
        for dim in DIMS:
            df_pa[f"{ctx}_{dim}"] = df_pa["giocatore_id"].map(
                lambda gid, c=ctx, d=dim: (
                    (all_ctx.get(int(gid)) or {}).get(c) or {}
                ).get(d)
            )
        # minuti del contesto (per lo shrinkage dell'output)
        df_pa[f"{ctx}_min_tot"] = df_pa["giocatore_id"].map(
            lambda gid, c=ctx: ((all_ctx.get(int(gid)) or {}).get(c) or {}).get("min_tot")
        )

    # ── FIX 1: Shrinkage minuti su output_adj (empirical Bayes) ──
    # output regredisce verso la media di ruolo del contesto in base ai minuti:
    #   out_shrunk = (m·out + K·prior_ruolo) / (m + K)
    # un per-90 caldo su pochi minuti viene tirato verso la media; chi gioca
    # tanto resta vicino al suo valore reale.
    K_out = float(CFG.output_prior_minutes)
    for ctx in CONTESTI:
        col = f"{ctx}_output_adj"
        mcol = f"{ctx}_min_tot"
        for ruolo in df_pa["ruolo"].dropna().unique():
            rmask = df_pa["ruolo"] == ruolo
            raw = pd.to_numeric(df_pa.loc[rmask, col], errors="coerce")
            prior = raw.mean()
            if pd.isna(prior):
                continue
            m = pd.to_numeric(df_pa.loc[rmask, mcol], errors="coerce").fillna(0.0)
            shrunk = (m * raw.fillna(prior) + K_out * prior) / (m + K_out)
            df_pa.loc[rmask, col] = np.where(raw.notna(), shrunk, np.nan)

    # ── FIX 3: Z-score PER RUOLO (offensivo, role-relative) ──────
    # ogni dimensione è standardizzata dentro il proprio ruolo: un difensore è
    # confrontato con i difensori, non con gli attaccanti. Resta un indice
    # offensivo, ma "relativo all'aspettativa di ruolo".
    def _z_by_role(value_col: str, fb_col: str | None = None) -> pd.Series:
        # coercizione a float: colonne con molti None (es. boost) sarebbero object
        # e pandas 3.0 rifiuta l'assegnazione in una serie float.
        vals = pd.to_numeric(df_pa[value_col], errors="coerce")
        fb = pd.to_numeric(df_pa[fb_col], errors="coerce") if fb_col else None
        out = pd.Series(np.nan, index=df_pa.index, dtype="float64")
        for ruolo in df_pa["ruolo"].dropna().unique():
            rmask = df_pa["ruolo"] == ruolo
            z = z_series(vals, rmask, fb, min_ref=6)
            out.loc[rmask] = pd.to_numeric(z.loc[rmask], errors="coerce")
        return out

    for ctx in CONTESTI:
        for dim in DIMS:
            fb_col = f"totale_{dim}" if ctx != "totale" else None
            df_pa[f"z_{ctx}_{dim}"] = _z_by_role(f"{ctx}_{dim}", fb_col)

    df_pa["z_finishing"] = _z_by_role("finishing_quality")
    df_pa["z_conv_ratio"] = _z_by_role("conv_ratio")
    df_pa["z_form"] = _z_by_role("form_ewma")

    # AII/PRI: indici trasversali → z-score per ruolo anche loro
    if "eta_index" not in df_pa.columns:
        df_pa["eta_index"] = np.nan
    if "affidabilita_fisica" not in df_pa.columns:
        df_pa["affidabilita_fisica"] = np.nan
    df_pa["z_eta_index"]           = _z_by_role("eta_index")
    df_pa["z_affidabilita_fisica"] = _z_by_role("affidabilita_fisica")

    # ── TPI per contesto — MEDIA PESATA (qualità > uso) ──────
    # output_adj domina; centralità/boost (uso, gonfiabili su squadre deboli)
    # pesano meno; finishing (gol vs xG) penalizza chi non converte.
    # I pesi sono re-normalizzati sui dim presenti (boost/finishing possono
    # mancare) così la scala resta confrontabile tra giocatori.
    W = CFG.tpi_weights
    def _weighted_tpi(ctx: str) -> pd.Series:
        num = pd.Series(0.0, index=df_pa.index)
        den = pd.Series(0.0, index=df_pa.index)
        for dim in ("output_adj", "centralita", "boost_ratio", "consistenza"):
            z = df_pa[f"z_{ctx}_{dim}"]
            w = W[dim]
            valid = z.notna()
            num = num + np.where(valid, z.fillna(0.0) * w, 0.0)
            den = den + np.where(valid, w, 0.0)
        # finishing: stesso valore su tutti i contesti (skill stagionale)
        zf = df_pa.get("z_finishing")
        if zf is not None:
            validf = zf.notna()
            num = num + np.where(validf, zf.fillna(0.0) * W["finishing"], 0.0)
            den = den + np.where(validf, W["finishing"], 0.0)
        return pd.Series(np.where(den > 0, num / den, np.nan), index=df_pa.index)

    for ctx in CONTESTI:
        df_pa[f"TPI_{ctx}"] = _weighted_tpi(ctx)

    # ── TPI Confidence — misura di affidabilità della stima ──
    # Basata su: quante dimensioni sono disponibili e quanti minuti
    # Alta confidence = tutte le dimensioni presenti + minuti elevati
    max_min = df_pa["minuti"].quantile(0.95).clip(1)
    min_confidence = (df_pa["minuti"] / max_min).clip(0, 1)

    dim_cols = [f"z_totale_{d}" for d in DIMS]
    n_dims_available = df_pa[dim_cols].notna().sum(axis=1)
    dim_confidence = (n_dims_available / len(DIMS)).clip(0, 1)

    # Confidence = media geometrica di stabilità minuti e completezza dimensioni
    df_pa["tpi_confidence"] = (
        np.sqrt(min_confidence * dim_confidence)
    ).round(3)

    # ── TPI_ext per contesto: base pesata + AII/PRI ──────────
    for ctx in CONTESTI:
        z_base = df_pa[f"TPI_{ctx}"].astype(float)
        ext_cols = []
        if CFG.include_age_in_tpi_ext and df_pa["z_eta_index"].notna().any():
            ext_cols.append(df_pa["z_eta_index"])
        if CFG.include_physical_in_tpi_ext and df_pa["z_affidabilita_fisica"].notna().any():
            ext_cols.append(df_pa["z_affidabilita_fisica"])
        if ext_cols:
            df_pa[f"TPI_ext_{ctx}"] = pd.concat([z_base.rename("b")] + [s.rename(f"e{i}") for i,s in enumerate(ext_cols)], axis=1).mean(axis=1)
        else:
            df_pa[f"TPI_ext_{ctx}"] = z_base

    # ── FIX 2 + peso-ruolo: confidence nel sort + importanza offensiva ───────
    # 1) shrink verso la media-ruolo in base alla confidence (minuti+completezza):
    #    chi ha pochi dati regredisce verso la media del suo ruolo.
    # 2) scala per il peso offensivo del ruolo: l'offensiva conta di più per un
    #    ATT che per un DIF → il TPI resta un indice OFFENSIVO role-aware.
    role_w = df_pa["ruolo"].map(CFG.offensive_role_weight).fillna(0.5).astype(float)
    shrink_factor = (CFG.confidence_floor
                     + (1.0 - CFG.confidence_floor) * df_pa["tpi_confidence"].fillna(0.0))
    for ctx in CONTESTI:
        for base in (f"TPI_{ctx}", f"TPI_ext_{ctx}"):
            raw = df_pa[base].astype(float)
            role_mean = raw.groupby(df_pa["ruolo"]).transform("mean")
            shrunk = role_mean + shrink_factor * (raw - role_mean)
            df_pa[base] = (role_w * shrunk).round(4)

    df_pa = df_pa.sort_values("TPI_totale", ascending=False).reset_index(drop=True)

    # ── Rank ──────────────────────────────────────────────────
    n_total = len(df_pa)
    rank_map = {
        "rank_TPI":         "TPI_totale",
        "rank_TPI_ext":     "TPI_ext_totale",
        "rank_output_adj":  "totale_output_adj",
        "rank_centralita":  "totale_centralita",
        "rank_boost":       "totale_boost_ratio",
        "rank_consistenza": "totale_consistenza",
        "rank_conv":        "conv_ratio",
        "rank_eta":         "eta_index",
        "rank_affidabilita":"affidabilita_fisica",
    }
    for rank_col, src_col in rank_map.items():
        df_pa[rank_col] = (
            df_pa[src_col].rank(ascending=False, na_option="bottom").astype(int)
        )

    # Top 10 log
    log.info("Top 10 TPI totale:")
    for i, (_, r) in enumerate(df_pa.head(10).iterrows()):
        cr = r.get("conv_ratio")
        crs = f"G/xG={cr:.2f}" if cr and not pd.isna(cr) else "G/xG=—"
        disp = r["giocatore"]
        log.info(
            f"  {i+1:2d}. TPI {r['TPI_totale']:+.3f}  "
            f"{str(disp):22s}  {str(r['squadra']):18s}  "
            f"{(str(r.get('ruolo')) if r.get('ruolo') and not pd.isna(r.get('ruolo')) else '?'):3s}  {crs}"
        )

    # ── Trend xG squadra (precomputato) ───────────────────────
    log.info("Precomputo trend xG squadra...")
    trend_cache: dict[int, dict] = {}
    for _, row in df_pa.head(CFG.top_n_payload).iterrows():
        gid = int(row["giocatore_id"])
        sq_id = int(row["squadra_id"])
        trend_cache[gid] = get_trend_xg(
            gid, sq_id, df_gp, df_sgl, sos_map, xg_col, CFG
        )
    log.info(f"Trend precomputati: {len(trend_cache)} giocatori")

    # ── Narrativa AI ──────────────────────────────────────────
    payload = build_payload(df_pa, all_ctx, conv_detail, form_detail, trend_cache, CFG)

    if CFG.anthropic_api_key:
        log.info(f"Generazione narrativa AI ({CFG.top_n_ai} giocatori)...")
        for i, entry in enumerate(payload[:CFG.top_n_ai]):
            row = df_pa[df_pa["giocatore_id"] == entry["id"]].iloc[0]
            entry["ai"] = genera_narrativa(row, CFG.anthropic_api_key)
            if (i + 1) % 5 == 0:
                log.info(f"  {i+1}/{CFG.top_n_ai} narrative generate")

    # ── Nomi squadre per metadata ──────────────────────────────
    sq_name_map = db.load_squad_names()
    top6_names = [clean_str(sq_name_map.get(sid, str(sid))) for sid in sorted(top6_ids)]
    forti_ids = set(int(x) for x in sos_per_sq.nsmallest(CFG.n_top_difese).index)
    forti_names = [clean_str(sq_name_map.get(sid, str(sid))) for sid in sorted(forti_ids)]

    log.info(f"Top 6: {top6_names}")
    log.info(f"Difese solide: {forti_names}")

    # ── Roster completo ────────────────────────────────────────
    df_roster = db.load_roster()
    if not df_roster.empty and CFG.ruolo_override:
        nc = "giocatore"
        for nome, ruolo_c in CFG.ruolo_override.items():
            mask = df_roster[nc] == nome
            if not mask.any():
                mask = df_roster[nc].str.lower() == nome.lower()
            df_roster.loc[mask, "ruolo"] = ruolo_c

    analyzed_ids = {entry["id"] for entry in payload}
    roster_list = [
        {
            "id": int(r.get("giocatore_id") or 0),
            "nome": clean_str(r.get("nome_anagrafico") or r.get("giocatore") or "—"),
            "squadra": clean_str(r["squadra"]),
            "ruolo": clean_str(r["ruolo"] or ""),
            "minuti": int(r["minuti"]),
            "is_analyzed": int(r.get("giocatore_id") or 0) in analyzed_ids,
        }
        for _, r in df_roster.iterrows()
    ]
    log.info(
        f"Roster: {len(roster_list)} giocatori "
        f"({sum(r['is_analyzed'] for r in roster_list)} analizzati)"
    )

    # ── TPI Pro Showcase — 2 per ruolo (ATT/CEN/DIF) ─────────────
    # Seleziona i top-2 per TPI_ext_totale in ogni ruolo.
    # Usato dalla dashboard per la sezione introduttiva "Cosa aggiunge TPI Pro".
    # Richiede che AII e PRI siano stati calcolati (eta_index + affidabilita_fisica).
    log.info("Calcolo TPI Pro showcase (6 giocatori demo)...")
    tpi_pro_showcase: list[dict] = []
    has_ext_data = (
        "TPI_ext_totale" in df_pa.columns
        and "eta_index" in df_pa.columns
        and "affidabilita_fisica" in df_pa.columns
        and df_pa["TPI_ext_totale"].notna().sum() >= 6
    )
    if has_ext_data:
        for ruolo_target in ["ATT", "CEN", "DIF"]:
            mask_ext = (
                (df_pa["ruolo"] == ruolo_target)
                & df_pa["TPI_ext_totale"].notna()
                & df_pa["eta_index"].notna()
                & df_pa["affidabilita_fisica"].notna()
            )
            top2 = df_pa[mask_ext].nlargest(2, "TPI_ext_totale")
            for _, row in top2.iterrows():
                def _f(col: str) -> float | None:
                    v = row.get(col)
                    return round(float(v), 3) if v is not None and pd.notna(v) else None
                def _i(col: str) -> int | None:
                    v = row.get(col)
                    return int(v) if v is not None and pd.notna(v) else None

                rank_tpi     = _i("rank_TPI")
                rank_tpi_pro = _i("rank_TPI_ext")
                delta = (rank_tpi - rank_tpi_pro) if (rank_tpi is not None and rank_tpi_pro is not None) else None

                tpi_pro_showcase.append({
                    "id":           _i("giocatore_id"),
                    "nome":         clean_str(row.get("nome_anagrafico") or row["giocatore"]),
                    "squadra":      clean_str(row["squadra"]),
                    "ruolo":        clean_str(row["ruolo"]),
                    "tpi":          _f("TPI_totale"),
                    "tpi_pro":      _f("TPI_ext_totale"),
                    "aii":          _f("eta_index"),
                    "pri":          _f("affidabilita_fisica"),
                    "eta":          _i("eta"),
                    "z_aii":        _f("z_eta_index"),
                    "z_pri":        _f("z_affidabilita_fisica"),
                    "rank_tpi":     rank_tpi,
                    "rank_tpi_pro": rank_tpi_pro,
                    "delta_rank":   delta,
                    # Dimensioni base (per mini-radar nella card)
                    "z_output":     _f("z_totale_output_adj"),
                    "z_centralita": _f("z_totale_centralita"),
                    "z_boost":      _f("z_totale_boost_ratio"),
                    "z_consistenza":_f("z_totale_consistenza"),
                })
        log.info(f"TPI Pro showcase: {len(tpi_pro_showcase)} giocatori ({', '.join(p['nome'] for p in tpi_pro_showcase)})")
    else:
        log.warning(
            "TPI Pro showcase vuoto: mancano eta_index/affidabilita_fisica nel DB. "
            "Popola t_infortuni e assicurati che data_nascita sia presente in giocatori."
        )

    # ── Salvataggio payload JSON ───────────────────────────────
    payload_out = {
        "n_giornate": n_giornate,
        "n_giocatori": n_total,
        "n_top_difese": CFG.n_top_difese,
        "top6_ids": list(top6_ids),
        "top6_names": top6_names,
        "forti_names": forti_names,
        "players": payload,
        "roster": roster_list,
        "tpi_pro_showcase": tpi_pro_showcase,   # ← NUOVO: showcase TPI Pro
    }

    payload_path = os.path.join(CFG.output_dir, "payload.json")
    payload_bytes = json.dumps(
        deep_clean(payload_out),
        ensure_ascii=True,
        indent=2,
        cls=SafeEncoder,
    ).encode("ascii")

    with open(payload_path, "wb") as f:
        f.write(payload_bytes)
    log.info(f"✓ Payload: {payload_path}")

    # ── Salvataggio CSV ───────────────────────────────────────
    csv_cols = [
        c for c in [
            "giocatore", "squadra", "ruolo", "minuti",
            "TPI_totale", "TPI_casa", "TPI_trasferta", "TPI_vs_top6", "TPI_vs_forti",
            "TPI_ext_totale",
            "totale_output_adj", "totale_centralita", "totale_boost_ratio", "totale_consistenza",
            "casa_output_adj", "trasferta_output_adj", "vs_top6_output_adj", "vs_forti_output_adj",
            "goal_tot", "xg_tot_conv", "conv_ratio", "finishing_quality", "goal_minus_xg",
            "xg_p90", "xa_p90", "sos", "form_ewma", "form_trend",
            "eta", "eta_index", "partite_disponibili", "partite_giocate",
            "n_infortuni", "giorni_out", "affidabilita_fisica",
            "rank_TPI", "rank_TPI_ext", "rank_output_adj", "rank_conv",
            "rank_eta", "rank_affidabilita",
            "z_totale_output_adj", "z_totale_centralita",
            "z_totale_boost_ratio", "z_totale_consistenza",
            "z_conv_ratio", "z_finishing", "z_eta_index", "z_affidabilita_fisica",
        ]
        if c in df_pa.columns
    ]
    csv_path = os.path.join(CFG.output_dir, "summary_stats.csv")
    df_pa[csv_cols].to_csv(csv_path, index=False)
    log.info(f"✓ CSV: {csv_path}")
    log.info("→ Ora esegui: python parte2_dashboard.py")
    log.info("=" * 58)


if __name__ == "__main__":
    main()
