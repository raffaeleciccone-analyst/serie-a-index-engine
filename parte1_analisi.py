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
from functools import lru_cache
import sys
import warnings
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

from config import SEASON_CORRENTE  # stagione pubblicata dal sito
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
    # True = z dentro il ruolo (default storico). False = z su tutta la lega:
    # il punteggio torna confrontabile fra ruoli, ma la classifica la occupano
    # gli attaccanti. Vedi _z_by_role.
    z_per_ruolo: bool = True

    offensive_role_weight: dict[str, float] = field(default_factory=lambda: {
        "ATT": 1.00, "CEN": 0.85, "DIF": 0.55, "POR": 0.20,
    })
    # Quanto la confidence (minuti+completezza) regredisce il TPI verso la media
    # di ruolo. floor=0.35 → anche a confidence 0 si tiene il 35% del segnale.
    confidence_floor: float = 0.35

    # Pesi del TPI (rev 2026-06-01): rebilanciati su feedback utente "diamo
    # più peso ai gol sul numero di gol di squadra" + ablation study (Test O).
    #
    # Cambiamenti chiave:
    #   - centralita 0.09 → 0.18 (raddoppiata): premia chi pesa nella produzione
    #     della sua squadra. Misura (xG_giocatore + xA_giocatore) / xG_squadra,
    #     con Bayesian shrinkage. Usa xA (expected) quindi un giocatore che fa
    #     un ottimo passaggio non viene penalizzato se il compagno sbaglia.
    #   - boost_ratio 0.05 → 0.02 (ridotto): ablation l'ha indicato come
    #     dimensione meno impattante (Δ predittivo minore). Mantenuto > 0
    #     per non perdere segnale "team con/senza" sui giocatori con campione adeguato.
    #   - output_adj 0.34 → 0.32, buildup 0.11 → 0.10, consistenza 0.09 → 0.07,
    #     form 0.12 → 0.11: leggera riduzione per compensare l'aumento centralità.
    # Somma = 1.0. Re-normalizzati sui dim effettivamente disponibili.
    tpi_weights: dict[str, float] = field(default_factory=lambda: {
        "output_adj":  0.32,   # qualità: xG+xA/90 SOS-adj — segnale dominante
        "buildup_adj": 0.10,   # coinvolgimento nella manovra (xGBuildup, no tiro/assist)
        "centralita":  0.18,   # quota produzione squadra (RADDOPPIATA — feedback utente)
        "boost_ratio": 0.02,   # team con/senza (ridotto su ablation)
        "consistenza": 0.07,   # regolarità intra-stagione
        "finishing":   0.20,   # gol vs npxG (conversione)
        "form":        0.11,   # EWMA xG+xA/90 (trend recente)
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
    # Forma recente: numero di ultime apparizioni da considerare
    recent_window: int = 6

    # ── Nuovi indici v2 ──────────────────────────────────────
    # Age Impact Index (AII) v3 — scout-oriented: premia chi sta ENTRANDO nel prime
    age_peak: float = 23.0          # picco scout: entrata nel prime, non prime consolidato
    age_sigma: float = 3.5          # dispersione gaussiana (più stretta = picco più definito)
    age_freshness_start: float = 27.0  # età oltre la quale freshness inizia a calare
    age_growth_start: float = 18.0  # età sotto la quale growth potential = 1.0
    age_exp_cap: float = 10.0       # mantenuto per retrocompatibilità (non usato in v3)

    # Physical Reliability Index (PRI)
    pri_min_partite: int = 8        # min partite disponibili per calcolare PRI
    pri_inj_penalty: float = 0.15   # penalità per ogni infortunio (-15%)
    pri_severity_cap: float = 90.0  # giorni out che portano severity a 0

    # TPI esteso (include AII + PRI)
    include_age_in_tpi_ext: bool = True
    include_physical_in_tpi_ext: bool = True

    # Pesi del TPI Pro (TPI_ext) — versione AGE-AWARE (rev 2026-06-01).
    #
    # Risposta al feedback "se hai pochi infortuni e hai determinati numeri
    # prima del prime sei un potenziale; nel prime sei buono ma più normale;
    # oltre il prime sei un veterano affidabile". I pesi del Pro cambiano per
    # categoria età così le 3 categorie sono valutate per quello che sono:
    #
    # PROSPETTO (<23): EMI boostato (skill vs età), AII alto (scout peak 23)
    # PRIME (23-29): pesi standard, equilibrio tra qualità e modulatori
    # VETERANO (≥29): PRI boostato (premia affidabilità), AII ridotto (non
    #   penalizza chi è oltre il peak se è affidabile). Mkhitaryan PRI 0.85
    #   con questi pesi recupera il segnale "veterano affidabile".
    #
    # Ogni profilo somma a 1.0. Re-normalizzati sui modulatori disponibili.
    tpi_pro_weights_age: dict[str, dict[str, float]] = field(default_factory=lambda: {
        "prospetto": {  # <23 anni
            "tpi":                    0.55,
            "z_eta_index":            0.15,
            "z_affidabilita_fisica":  0.05,
            "z_ctx_stability":        0.07,
            "z_form_trend":           0.05,
            "z_early_momentum":       0.13,  # boost: skill vs età
        },
        "prime": {  # 23-28 anni
            "tpi":                    0.65,
            "z_eta_index":            0.10,
            "z_affidabilita_fisica":  0.07,
            "z_ctx_stability":        0.07,
            "z_form_trend":           0.05,
            "z_early_momentum":       0.06,
        },
        "veterano": {  # ≥29 anni
            "tpi":                    0.60,
            "z_eta_index":            0.04,  # ridotto: non penalizza il veterano oltre peak
            "z_affidabilita_fisica":  0.18,  # boostato: premia affidabilità
            "z_ctx_stability":        0.08,
            "z_form_trend":           0.05,
            "z_early_momentum":       0.05,
        },
    })
    # Pesi backward-compat (default = pesi PRIME, usati se eta mancante)
    tpi_pro_weights: dict[str, float] = field(default_factory=lambda: {
        "tpi":                    0.65,
        "z_eta_index":            0.10,
        "z_affidabilita_fisica":  0.07,
        "z_ctx_stability":        0.07,
        "z_form_trend":           0.05,
        "z_early_momentum":       0.06,
    })
    # Soglie età per le 3 categorie
    prospetto_max_age: float = 23.0  # <23 = prospetto
    veterano_min_age: float = 29.0   # ≥29 = veterano

    # Output
    # 100 e' la dimensione della dashboard pubblicata, non una scelta
    # statistica: con 0 il payload contiene TUTTI i qualificati. Serve ai
    # vintage della validazione, dove tagliare in alto attenua da solo le
    # correlazioni (si confrontano solo i migliori fra loro). Vedi --top-n.
    top_n_payload: int = 100
    top_n_ai: int = 20
    output_dir: str = ""

    # Colonne DB (auto-rilevate se None)
    sgl_xg_col: str | None = None
    sgl_xg_avv_col: str | None = None
    sgl_ruolo_col: str = "ruolo"

    # Override ruoli — ULTIMA PAROLA (vince su Understat e DB).
    # Default = posizione reale Understat (derive_understat_roles). Aggiungi qui
    # SOLO le eccezioni che vuoi forzare a mano (Understat manca o sbaglia).
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
        "Matteo Politano": "ATT",   # ala Napoli schierata wing-back (Understat: DMR)
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
DIMS = ["output_adj", "buildup_adj", "centralita", "boost_ratio", "consistenza"]

# Mappa posizione Understat → bucket-ruolo.
# DC/DL/DR = difensori puri; DML/DMR = wing-back (AMBIGUO → risolto per profilo);
# DMC/M* = mediani/mezzali (CEN); AM*/FW* = trequartisti/ali/punte (ATT).
_POS_BUCKET = {
    "GK": "POR",
    "DC": "DIF", "DL": "DIF", "DR": "DIF",
    "DML": "WB", "DMR": "WB",
    "DMC": "CEN", "MC": "CEN", "ML": "CEN", "MR": "CEN",
    "AMC": "ATT", "AML": "ATT", "AMR": "ATT",
    "FW": "ATT", "FWL": "ATT", "FWR": "ATT",
}


@lru_cache(maxsize=1)
def _understat_posmin() -> dict[str, dict[str, int]]:
    """Minuti per posizione, giocatore per giocatore, dai JSON Understat in
    cache. Estratti una volta sola: la lettura della cache è la parte lenta, e
    da questi minuti si ricavano poi tutte le soglie che servono."""
    import glob as _g, json as _j, html as _h, collections as _c

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
                    posmin[_role_key(info.get("player"))][pos] += t
    return {k: dict(v) for k, v in posmin.items()}


def derive_understat_roles(min_minutes: int = 200) -> dict[str, str]:
    """Ruolo per giocatore dalle posizioni Understat realmente giocate (dai JSON
    in cache). Somma i minuti per bucket-ruolo e prende il maggiore. I minuti da
    wing-back (DML/DMR) sono assegnati a CEN se il giocatore è prevalentemente
    centrale/offensivo (mezzala schierata esterna, es. McKennie), altrimenti a
    DIF (terzino fluidificante, es. Estupiñán). Gestisce i giocatori versatili
    meglio della posizione singola modale.

    `min_minutes` alto = ruolo affidabile, ma lascia scoperte le riserve. Con
    la soglia a 1 il ruolo è quello di poche apparizioni: buono come ripiego
    per chi altrimenti resterebbe senza — un dodicesimo portiere con 90 minuti
    in tutta la stagione va comunque riconosciuto come portiere.
    """
    import collections as _c

    posmin = {k: _c.Counter(v) for k, v in _understat_posmin().items()}
    roles: dict[str, str] = {}
    for nm, cnt in posmin.items():
        if sum(cnt.values()) < min_minutes:
            continue
        bucket: _c.Counter = _c.Counter()
        for pos, mins in cnt.items():
            b = _POS_BUCKET.get(pos)
            if b:
                bucket[b] += mins
        # risolvi i minuti da wing-back (WB): vanno a CEN solo se il giocatore ha
        # almeno tanti minuti centrali/offensivi quanti da fascia (= è davvero un
        # mediano schierato esterno, es. McKennie). Altrimenti è un terzino/wing-back
        # puro → DIF (es. Wesley 93% fascia, Dimarco, Cambiaso).
        wb = bucket.pop("WB", 0)
        if wb:
            central = bucket.get("CEN", 0) + bucket.get("ATT", 0)
            if central >= wb:
                bucket["CEN"] += wb
            else:
                bucket["DIF"] += wb
        if bucket:
            roles[nm] = bucket.most_common(1)[0][0]
    return roles


# Nome leggibile di ogni dimensione del TPI. Sta qui, accanto ai pesi, perche'
# da qui esce il blocco `metodo` del payload: il sito e l'assistente devono
# leggere l'elenco delle dimensioni dal motore, non riscriverselo a mano. Il
# dataset dell'assistente ne elencava sei, con dentro l'AII (che e' un
# modulatore del Pro) e senza finishing (peso 0.20).
DIM_NOMI = {
    "output_adj":  ("Output offensivo", "Attacking output", "(xG + xA) / 90 / SOS"),
    "finishing":   ("Finalizzazione", "Finishing", "(gol - xG) / radice(xG), rigori esclusi"),
    "centralita":  ("Centralita offensiva", "Attacking centrality",
                    "(xG + xA del giocatore) / xG della squadra"),
    "form":        ("Forma recente", "Recent form", "EWMA dell'output per-90"),
    "buildup_adj": ("Buildup", "Buildup", "xGBuildup / 90 / SOS, senza tiro ne' assist"),
    "consistenza": ("Consistenza", "Consistency", "media / (media + deviazione standard)"),
    "boost_ratio": ("Effetto squadra", "Team effect", "log-ratio xG con lui / senza lui, shrinkato"),
}

MOD_PRO_NOMI = {
    "aii": ("Indice eta (AII)", "Age index (AII)"),
    "pri": ("Affidabilita fisica (PRI)", "Physical reliability (PRI)"),
    "ctx_stab": ("Stabilita fra i contesti", "Cross-context stability"),
    "form_trend": ("Direzione della forma", "Form direction"),
    "early_momentum": ("Momentum iniziale", "Early momentum"),
}


def blocco_metodo(cfg: Any) -> dict:
    """Come e' costruito il punteggio, preso dalla configurazione che gira.

    Serve a chi consuma il payload senza leggere il codice — la guida, e
    soprattutto il dataset dell'assistente, che prima riportava a mano sei
    dimensioni, il picco d'eta a 27 anni (qui e' 23) e una consistenza
    calcolata con l'IQR, rimossa da mesi.
    """
    pesi = dict(cfg.tpi_weights)
    dims = [
        {
            "chiave": k,
            "nome_it": DIM_NOMI.get(k, (k, k, ""))[0],
            "nome_en": DIM_NOMI.get(k, (k, k, ""))[1],
            "formula": DIM_NOMI.get(k, (k, k, ""))[2],
            "peso": round(float(v), 4),
        }
        for k, v in sorted(pesi.items(), key=lambda kv: -kv[1])
    ]
    return {
        "dimensioni": dims,
        "modulatori_pro": [
            {"chiave": k, "nome_it": it, "nome_en": en}
            for k, (it, en) in MOD_PRO_NOMI.items()
        ],
        "contesti": ["totale", "casa", "trasferta", "vs_top6", "vs_forti"],
        "z": {
            "dentro_il_ruolo": bool(cfg.z_per_ruolo),
            "winsor_pct": 0.05,
            "clamp_sigma": 3.0,
            "nota_it": ("Gli z-score si calcolano dentro il ruolo: 0.00 e' il giocatore "
                        "medio del SUO ruolo, non della lega."
                        if cfg.z_per_ruolo else
                        "Gli z-score si calcolano su tutta la lega."),
        },
        "consistenza_formula": "media / (media + deviazione standard) dell'output per-90",
        "eta": {"picco": float(cfg.age_peak), "sigma": float(cfg.age_sigma)},
        "shrinkage": {
            "output_prior_minuti": float(cfg.output_prior_minutes),
            "confidence_floor": float(cfg.confidence_floor),
        },
        "peso_offensivo_per_ruolo": {k: float(v) for k, v in cfg.offensive_role_weight.items()},
        "ewma_alpha": float(cfg.ewma_alpha),
        "portieri": "esclusi dall'indice, che misura l'impatto offensivo",
    }


def _role_key(s: Any) -> str:
    """Nome ridotto a chiave confrontabile: via accenti ed entità HTML, tutto
    minuscolo. Serve ad agganciare i nomi Understat a quelli dell'anagrafica."""
    import unicodedata as _u, html as _h

    return (
        _u.normalize("NFKD", _h.unescape(str(s or "")))
        .encode("ascii", "ignore")
        .decode()
        .lower()
        .strip()
    )


def apply_role_pipeline(
    df: pd.DataFrame,
    us_roles: dict[str, str],
    cfg: Any,
    etichetta: str = "",
    ripiego: dict[str, str] | None = None,
) -> pd.DataFrame:
    """Ruolo definitivo su un dataframe con colonna `ruolo` e una di nome.

    Gerarchia:  1) DB (base)  2) posizione reale Understat (default oggettivo)
                3) override manuale (ULTIMA PAROLA).

    Girava solo su df_pa. Il roster — che è il blocco pubblicato come rosa —
    restava con il ruolo grezzo del DB, e i due si contraddicevano nella stessa
    build: Alex Meret usciva DIF nella rosa del Napoli e POR fra i qualificati.
    """
    if df.empty or "ruolo" not in df.columns:
        return df
    cols = [c for c in ("nome_anagrafico", "giocatore", "nome") if c in df.columns]
    if not cols:
        return df
    tag = f" [{etichetta}]" if etichetta else ""

    # 2) Understat = default per tutti i giocatori con posizione affidabile
    if us_roles:
        applied = 0
        keymap = df[cols[0]].map(_role_key)
        for idx, k in keymap.items():
            if k in us_roles and df.at[idx, "ruolo"] != us_roles[k]:
                df.at[idx, "ruolo"] = us_roles[k]
                applied += 1
        log.info(f"Ruolo Understat (posizione reale){tag}: {applied} correzioni")

    # 2-bis) Ripiego per chi resta senza ruolo: posizione Understat presa anche
    # su pochi minuti. Non entra nell'analisi (chi ha cosi' pochi minuti non si
    # qualifica), ma decide se uno finisce nella rosa pubblicata e con quale
    # etichetta — ed e' l'unico modo di riconoscere il dodicesimo portiere, che
    # in anagrafica ha il ruolo vuoto e nella rosa compariva senza ruolo.
    if ripiego:
        vuoti = df["ruolo"].isna() | df["ruolo"].astype(str).str.strip().isin(["", "nan", "None"])
        riempiti = 0
        if vuoti.any():
            keymap = df.loc[vuoti, cols[0]].map(_role_key)
            for idx, k in keymap.items():
                if k in ripiego:
                    df.at[idx, "ruolo"] = ripiego[k]
                    riempiti += 1
        log.info(f"Ruolo di ripiego (pochi minuti){tag}: {riempiti} su {int(vuoti.sum())} senza ruolo")

    # 3) Override manuale = ultima parola (vince su Understat e DB)
    overridden = 0
    for nome, ruolo_corretto in (getattr(cfg, "ruolo_override", None) or {}).items():
        for col in cols:
            mask = df[col] == nome
            if not mask.any():
                mask = df[col].astype(str).str.lower() == nome.lower()
            if mask.any():
                df.loc[mask, "ruolo"] = ruolo_corretto
                overridden += int(mask.sum())
                break
    log.info(f"Override manuale (ultima parola){tag}: {overridden} giocatori")
    return df


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

    def __init__(self, engine: Engine, season: str | None = None):
        """Costruisce il layer DB.
        season: se valorizzato (es. "2024-25"), tutte le query temporali
        filtrano per `calendario.season=season`. Se None, comportamento
        legacy (nessun filtro — assume DB monostagione o default '2025-26').
        """
        self.engine = engine
        self.season = season
        self._verify_schema()
        if season:
            self._verify_season_column()

    def _verify_season_column(self) -> None:
        """Verifica che il DDL multi-stagione sia stato applicato."""
        with self.engine.connect() as conn:
            r = conn.execute(text(
                "SHOW COLUMNS FROM calendario LIKE 'season'"
            )).fetchone()
        if not r:
            raise RuntimeError(
                f"Filtro season='{self.season}' richiesto ma colonna `calendario.season` "
                "non esiste. Esegui prima setup_multi_season.sql."
            )
        # Verifica che esistano dati per quella stagione (interpolazione safe:
        # self.season è valore controllato dal CLI, non input utente arbitrario)
        with self.engine.connect() as conn:
            n = conn.execute(text(
                f"SELECT COUNT(*) FROM calendario WHERE season = '{self.season}'"
            )).fetchone()[0]
        if n == 0:
            raise RuntimeError(
                f"Nessuna partita in DB per season='{self.season}'. "
                f"Esegui prima il backfill: python backfill_24_25_completo.py"
            )
        log.info(f"Season filter attivo: '{self.season}' ({n} partite in calendario)")

    def _season_where(self, alias: str = "cal") -> str:
        """Restituisce frammento SQL `AND <alias>.season = '<season>'` o stringa vuota.
        L'alias deve riferirsi a `calendario`. Usa string interpolation perché
        season è validata in _verify_season_column (no SQL injection risk).
        """
        return f" AND {alias}.season = '{self.season}'" if self.season else ""

    def _verify_schema(self) -> None:
        required = [
            "giocatori", "squadre", "calendario",
            "giocatore_partita",
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
        try:
            sw = self._season_where("cal")
            df = pd.read_sql(
                f"""
                SELECT a.squadra_id        AS squadra_id,
                       AVG(b.xg)           AS xg_concessi
                FROM   squadra_calendario a
                JOIN   squadra_calendario b
                       ON b.calendario_id = a.calendario_id
                      AND b.squadra_id   <> a.squadra_id
                JOIN   calendario cal
                       ON cal.id = a.calendario_id
                WHERE  1=1{sw}
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
        # `giocatore_partita`. Per multi-stagione: aggregato filtrato per
        # calendario.season; il JOIN con `squadre` usa la squadra ATTUALE
        # del giocatore (anagrafica), che può differire da quella della stagione
        # storica — ma è solo per displaying. La logica di squadra-per-partita
        # è gestita via load_player_games e squadra_calendario filtrati.
        sw = self._season_where("cal")
        df = pd.read_sql(
            f"""
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
                SELECT gp.giocatore_id,
                       SUM(gp.minuti)               AS minuti,
                       COUNT(DISTINCT gp.calendario_id) AS partite,
                       SUM(gp.goal)                 AS goal,
                       SUM(gp.xg)                   AS xg,
                       SUM(gp.xa)                   AS xa
                FROM giocatore_partita gp
                JOIN calendario cal ON cal.id = gp.calendario_id
                WHERE gp.minuti > 0{sw}
                GROUP BY gp.giocatore_id
            ) agg ON agg.giocatore_id = g.id
            """,
            self.engine,
        )
        df["squadra_id"] = pd.to_numeric(df["squadra_id"], errors="coerce").astype("Int64")
        df["giocatore_id"] = pd.to_numeric(df["giocatore_id"], errors="coerce").astype("Int64")
        return df

    def load_squad_game_log(self) -> pd.DataFrame:
        sw = self._season_where("cal")
        df = pd.read_sql(
            f"""
            SELECT
                sgl.*,
                cal.giornata
            FROM t_squadra_game_log sgl
            JOIN calendario cal ON cal.id = sgl.calendario_id
            WHERE 1=1{sw}
            """,
            self.engine,
        )
        for col in ("squadra_id", "avversario_id"):
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce").astype("Int64")
        return df

    def load_player_games(self, xg_col: str, xg_avv_col: str) -> pd.DataFrame:
        sw = self._season_where("cal")
        df = pd.read_sql(
            f"""
            SELECT
                gp.giocatore_id,
                g.squadra_id          AS squadra_id,
                gp.calendario_id,
                cal.giornata,
                cal.data              AS data,
                sgl.ruolo             AS ruolo_gp,
                gp.minuti,
                gp.goal,
                COALESCE(gp.npg,  gp.goal) AS npg_ind,
                COALESCE(gp.npxg, gp.xg)   AS xg_ind,
                gp.xa                 AS xa_ind,
                COALESCE(gp.xg_buildup, 0) AS buildup_ind,
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
            -- I portieri restano fuori dall'indice (e' un indice di impatto
            -- OFFENSIVO), ma l'esclusione NON si fa qui: `g.ruolo` e' proprio
            -- il campo inaffidabile che ci ha dato Meret difensore e Idzes
            -- portiere. Si filtra in main(), dopo aver risolto i ruoli sulle
            -- posizioni Understat.
            WHERE 1=1{sw}
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
        """La rosa per squadra, con i minuti giocati nella stagione corrente.

        Era l'unica query del motore che non passava per `_season_where`, e non
        agganciava nemmeno `calendario`: sommava i minuti di tutte le stagioni
        presenti in DB. Con due stagioni caricate ne usciva Ndicka a 6391
        minuti contro un massimo teorico di 3420, ed erano elencati come "minuti
        insufficienti" giocatori che ne avevano quattromila. Da qui venivano
        anche i giocatori che non sono piu' in rosa: erano quelli dell'anno
        prima.
        """
        sw = self._season_where("cal")
        try:
            return pd.read_sql(
                f"""
                SELECT
                    g.id          AS giocatore_id,
                    TRIM(CASE
                        WHEN g.cognome IS NULL OR TRIM(g.cognome) = '' THEN g.nome
                        WHEN LOWER(g.nome) LIKE LOWER(CONCAT('%%', g.cognome, '%%')) THEN g.nome
                        ELSE CONCAT_WS(' ', NULLIF(TRIM(g.nome), ''), NULLIF(TRIM(g.cognome), ''))
                    END) AS giocatore,
                    g.ruolo,
                    sq.nome       AS squadra,
                    COALESCE(agg.minuti, 0) AS minuti
                FROM   giocatori g
                JOIN   squadre sq ON sq.id = g.squadra_id
                LEFT JOIN (
                    SELECT gp.giocatore_id, SUM(gp.minuti) AS minuti
                    FROM giocatore_partita gp
                    JOIN calendario cal ON cal.id = gp.calendario_id
                    WHERE gp.minuti > 0{sw}
                    GROUP BY gp.giocatore_id
                ) agg ON agg.giocatore_id = g.id
                -- Chi non ha giocato un minuto in questa stagione non ha niente
                -- da dire in una pagina di scouting, e finiva in elenco solo
                -- perche' l'anagrafica lo tiene ancora in quella squadra: era
                -- il caso dei giocatori ceduti l'estate prima.
                WHERE  agg.minuti > 0   -- i portieri escono dopo, sul ruolo risolto
                ORDER  BY sq.nome, g.ruolo, ISNULL(agg.minuti), agg.minuti DESC
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
        # MAI sotto la barra del 20% stagione: prendi il MAX, non il min.
        # (il vecchio min() abbassava la soglia al 30° percentile → ammetteva
        #  più giocatori a basso minutaggio.)
        min_full = max(base_full, p30)
        log.info(
            f"Soglia adattiva: base={base_full}', p30={p30}' → soglia={min_full}'"
        )
    else:
        min_full = base_full

    min_winter = base_winter

    # Acquisto invernale = PRIMA apparizione nella finestra di mercato di gennaio
    # (1 gen – 5 feb), per DATA reale. Esclude i debutti di fine stagione
    # (mar/apr/mag: giovani, riserve, rientri) che NON sono acquisti invernali —
    # il vecchio "first_giornata > soglia" li flaggava tutti come winter.
    winter_ids: set[int] = set()
    if "data" in df_gp.columns:
        first_date = (
            df_gp[df_gp["minuti"] > 0]
            .groupby("giocatore_id")["data"]
            .min()
        )
        for gid, d in first_date.items():
            ts = pd.Timestamp(d)
            if pd.notna(ts) and (ts.month == 1 or (ts.month == 2 and ts.day <= 5)):
                winter_ids.add(int(gid))
    winter_threshold = 0  # non più basato su giornata

    log.info(
        f"Giornate: {n_giornate} | Soglia titolari: {min_full}' | "
        f"Soglia invernale: {min_winter}' | Acquisti invernali (finestra gennaio): {len(winter_ids)}"
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

    # Buildup: coinvolgimento nella manovra (xGBuildup, esclude tiro+assist del
    # giocatore → ortogonale a output_adj). Per-90, SOS-adjusted come l'output.
    buildup_tot = float(df_con["buildup_ind"].fillna(0).sum()) if "buildup_ind" in df_con.columns else 0.0
    buildup_p90 = buildup_tot / min_tot * 90
    buildup_adj = buildup_p90 / sos_ctx if sos_ctx > 0 else buildup_p90

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
    if len(out_pg) >= 6 and out_pg.mean() > 0:
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
        "buildup_adj": _r(buildup_adj),
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
# ══════════════════════════════════════════════════════════════════
# Difese solide: fonte esterna
# ══════════════════════════════════════════════════════════════════
DIFESE_ESTERNE = Path(__file__).parent / "dati_esterni" / "xg_concessi_SA_2025-26.json"


def carica_difese_esterne(sq_name_map: dict[int, str]) -> pd.Series | None:
    """xG concessi per squadra da Sportmonks, se il file c'e'.

    Il contesto "difese solide" vuole le squadre che concedono meno xG. La
    nostra `t_squadra_game_log` per il 2025-26 ne copre 28 giornate su 38, e su
    mezza stagione in meno la classifica cambia davvero: a stagione intera il
    Bologna entra fra le sei piu' solide e il Milan esce, passando da terzo a
    ottavo. Il file lo produce `estrai_xg_concessi_hexi.py` leggendo il feed
    Sportmonks di heXI, che le ha tutte e 38.

    Se il file manca si torna alla SOS interna, che e' la stessa misura su meno
    partite: il motore non si ferma per una fonte esterna assente.
    """
    if not DIFESE_ESTERNE.is_file():
        log.info("Difese solide: fonte esterna assente, uso la SOS interna")
        return None
    try:
        dati = json.loads(DIFESE_ESTERNE.read_text(encoding="utf-8"))["squadre"]
    except Exception as e:
        log.warning(f"Difese solide: fonte esterna illeggibile ({e}), uso la SOS interna")
        return None
    per_nome = {n: v["xg_concessi_pg"] for n, v in dati.items()}
    per_id = {sid: per_nome[nome] for sid, nome in sq_name_map.items() if nome in per_nome}
    mancanti = sorted(set(per_nome) - {sq_name_map.get(s) for s in per_id})
    if mancanti:
        log.warning(f"Difese solide: {len(mancanti)} squadre del feed senza id nostro: {mancanti}")
    if len(per_id) < 20:
        log.warning(f"Difese solide: solo {len(per_id)} squadre agganciate, uso la SOS interna")
        return None
    log.info(f"Difese solide: xG concessi da Sportmonks su {len(per_id)} squadre")
    return pd.Series(per_id)


def compute_all_contexts(
    df_pa: pd.DataFrame,
    df_gp: pd.DataFrame,
    df_sgl: pd.DataFrame,
    sos_map: dict[int, float],
    top6_ids: set[int],
    sos_per_sq: pd.Series,
    xg_col: str,
    cfg: Config,
    difese_per_sq: pd.Series | None = None,
) -> dict[int, dict]:
    all_ctx: dict[int, dict] = {}
    # Solo per scegliere le difese solide: la SOS vera resta quella che pesa
    # l'output, e non si tocca.
    sos_per_sq_dict = (difese_per_sq if difese_per_sq is not None else sos_per_sq).to_dict()

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

        # ── Forma recente: ultime N apparizioni (gioco aperto, no rigori) ──
        rec = grp_s.tail(cfg.recent_window)
        r_min = float(rec["minuti"].sum())
        r_npg = float(rec["npg_ind"].fillna(0).sum()) if "npg_ind" in rec.columns else float(rec["goal"].fillna(0).sum())
        r_npxg = float(rec["xg_ind"].fillna(0).sum())
        r_xa = float(rec["xa_ind"].fillna(0).sum())
        r_n = int((rec["minuti"] > 0).sum())
        r_out90 = round((r_npxg + r_xa) / r_min * 90, 3) if r_min > 0 else None
        # baseline stagione (stesso indicatore)
        s_min = float(grp_s["minuti"].sum())
        s_npxg = float(grp_s["xg_ind"].fillna(0).sum())
        s_xa = float(grp_s["xa_ind"].fillna(0).sum())
        s_out90 = (s_npxg + s_xa) / s_min * 90 if s_min > 0 else 0.0
        out_ratio = round(r_out90 / s_out90, 2) if (r_out90 is not None and s_out90 > 0) else None
        # etichetta forma: combina creazione (out_ratio) e realizzo recente
        label = None
        if r_n >= 3:
            drought = (r_npxg >= 1.0 and r_npg == 0)        # crea ma non segna
            if drought or (out_ratio is not None and out_ratio < 0.70):
                label = "cold"
            elif out_ratio is not None and out_ratio > 1.30 and r_npg >= max(1, 0.7 * r_npxg):
                label = "hot"
            else:
                label = "stable"

        form_rows.append({
            "giocatore_id": gid,
            "form_ewma": round(ewma_s[-1], 4) if ewma_s else None,
            "form_trend": trend,
            "recent_n": r_n,
            "recent_min": int(r_min),
            "recent_goal": int(float(rec["goal"].fillna(0).sum())),
            "recent_npg": int(r_npg),
            "recent_npxg": round(r_npxg, 2),
            "recent_out90": r_out90,
            "recent_ratio": out_ratio,
            "recent_label": label,
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
    Consistenza = mean / (mean + std) dell'output per-90 sulle gare giocate.
    Bounded [0,1], sempre definita (se mean+std>0), con varianza reale:
      - alto  → contributo regolare partita dopo partita
      - basso → produzione "a sprazzi" (1-2 gare grosse, tante a zero)
    Sostituisce la vecchia 1−IQR/mediana, che su una distribuzione di output
    per-90 zero-inflated collassava a pochi valori (~tutti 0) → dimensione morta.
    """
    g = out_pg.dropna()
    if len(g) < 6:
        return None
    mu = float(g.mean())
    sd = float(g.std())
    if mu + sd <= 1e-9:
        return None
    return round(mu / (mu + sd), 4)


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
    Age Impact Index (AII) v3 — scout-oriented [0, 1].

    Premia chi sta ENTRANDO nel prime (22–25), non chi è già nel prime consolidato
    (26–29). Risposta diretta a feedback scout: "il giocatore già nel prime ha
    valore minore — quello in ascesa ha più upside".

    Componenti:
      55% ScoutPeak       — Gaussiana centrata a age_peak=23, σ=3.5
      25% GrowthPotential — bonus 1.0 a 18 → 0 a peak, decay lineare
      20% Freshness       — 1.0 fino a age_freshness_start=27, poi −6%/anno

    Pattern AII v3 con i parametri attuali (peak=23, sigma=3.5, growth_start=18,
    freshness_start=27, pesi 0.55/0.25/0.20):
      18→0.65 · 20→0.73 · 22→0.78 · 23→0.75 · 24→0.73 · 25→0.67 · 27→0.49 ·
      30→0.24 · 35→0.11

    Il massimo del composito NON cade a 23 ma a ~21.8 (0.779): a 23 il bonus
    GrowthPotential si è già azzerato, quindi la somma perde prima di quanto la
    sola gaussiana farebbe pensare. È il comportamento voluto — l'indice premia
    chi sta entrando nel prime — ma va detto, perché age_peak=23 si legge come
    se il picco fosse lì.

    Questi valori sono calcolati dalla funzione, non scritti a mano: il
    docstring dichiarava 18→0.58 · 22→0.75 · 27→0.58 e un confronto con una v2
    che il codice non contiene più. La curva pubblicata nella guida usa i valori
    veri, quindi era il commento a essere rimasto indietro.

    Returns None se età non disponibile o fuori range [15, 45].
    """
    if eta is None or not (15 <= eta <= 45):
        return None

    # 1. Scout peak: gaussiana centrata su age_peak (23) con σ stretto (3.5)
    scout_peak = _math.exp(-0.5 * ((eta - cfg.age_peak) / cfg.age_sigma) ** 2)

    # 2. Growth potential: bonus lineare per i giovani in ascesa, 0 dopo peak
    if eta >= cfg.age_peak:
        growth = 0.0
    else:
        span = max(1.0, cfg.age_peak - cfg.age_growth_start)
        growth = max(0.0, min(1.0, (cfg.age_peak - eta) / span))

    # 3. Physical freshness: 1.0 fino a freshness_start (27), poi declina del 6%/anno
    if eta <= cfg.age_freshness_start:
        freshness = 1.0
    else:
        freshness = max(0.0, 1.0 - (eta - cfg.age_freshness_start) * 0.06)

    return round(0.55 * scout_peak + 0.25 * growth + 0.20 * freshness, 4)


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
    # lunghezza reale = giornate giocate da una squadra (38), non il max label (40)
    if len(df_gp) > 0 and "squadra_id" in df_gp.columns:
        n_giornate_tot = int(df_gp.groupby("squadra_id")["giornata"].nunique().max())
    else:
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
def righe_payload(df_pa, cfg: Config):
    """Le righe che entrano nel payload: le prime N, o tutte se top_n_payload <= 0.

    Il taglio in alto e' una scelta di prodotto — quanti giocatori mostra la
    dashboard — non un criterio statistico. Nei vintage che alimentano la
    validazione va tolto: confrontare fra loro solo i primi 100 restringe la
    varianza e attenua ogni correlazione, cosi' i test misurano la selezione
    invece dell'indice.
    """
    return df_pa if cfg.top_n_payload <= 0 else df_pa.head(cfg.top_n_payload)


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

    for _, row in righe_payload(df_pa, cfg).iterrows():
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
            "avg_min_partita": safe_json(row.get("avg_min_per_partita")),
            "k_subst_mult": safe_json(row.get("k_subst_mult")),
            "disponibilita_rel": safe_json(row.get("disponibilita_rel")),
            "disponibilita_penalty": safe_json(row.get("disponibilita_penalty")),
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
            "recent": {
                "n":      safe_json(row.get("recent_n")),
                "min":    safe_json(row.get("recent_min")),
                "goal":   safe_json(row.get("recent_goal")),
                "npg":    safe_json(row.get("recent_npg")),
                "npxg":   safe_json(row.get("recent_npxg")),
                "out90":  safe_json(row.get("recent_out90")),
                "ratio":  safe_json(row.get("recent_ratio")),
                "label":  row.get("recent_label"),
            },
            "trend": tr,
            # ── Nuovi indici v2 ──────────────────────
            "tpi_ext": {ctx: safe_json(row.get(f"TPI_ext_{ctx}")) for ctx in CONTESTI},
            "physical": {
                "eta":               safe_json(row.get("eta")),
                "eta_cat":           row.get("eta_cat", "prime"),
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
            "z_buildup":     safe_json(row.get("z_totale_buildup_adj")),
            "z_centralita":  safe_json(row.get("z_totale_centralita")),
            "z_boost":       safe_json(row.get("z_totale_boost_ratio")),
            "z_consistenza": safe_json(row.get("z_totale_consistenza")),
            "z_finishing":   safe_json(row.get("z_finishing")),
            "z_form":        safe_json(row.get("z_form")),
            "z_aii":         safe_json(row.get("z_eta_index")),
            "z_pri":         safe_json(row.get("z_affidabilita_fisica")),
            "z_ctx_stab":    safe_json(row.get("z_ctx_stability")),
            "z_form_trend":  safe_json(row.get("z_form_trend")),
            "z_early_momentum": safe_json(row.get("z_early_momentum")),
            "early_momentum": safe_json(row.get("early_momentum")),
            "ctx_stability": safe_json(row.get("ctx_stability")),
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


def _compute_sos_from_sgl(df_sgl: pd.DataFrame, xg_col: str = "xg") -> dict[int, float]:
    """SOS = xG concessi medi (normalizzato lega=1.0) calcolato da df_sgl filtrato.
    Replica di DatabaseLayer.load_sos_map() ma agnostico al DB (per snapshot vintage).
    """
    a = df_sgl[["squadra_id", "calendario_id", "avversario_id"]].copy()
    b = df_sgl[["squadra_id", "calendario_id", xg_col]].rename(
        columns={"squadra_id": "avversario_id", xg_col: "_xg_avv_fatto"})
    merged = a.merge(b, on=["calendario_id", "avversario_id"], how="inner")
    by_sq = merged.groupby("squadra_id")["_xg_avv_fatto"].mean()
    league_avg = float(by_sq.mean())
    if league_avg <= 0 or pd.isna(league_avg):
        return {int(sid): 1.0 for sid in df_sgl["squadra_id"].dropna().unique()}
    return {int(k): float(v) / league_avg for k, v in by_sq.items()
            if not pd.isna(v)}


def main(max_giornata: int | None = None,
         season: str | None = SEASON_CORRENTE) -> None:
    """season=None significa NESSUN filtro, cioe' tutte le stagioni aggregate.
    Non e' piu' il default: con due stagioni in DB produceva una classifica
    che non era di nessuna delle due, e finiva in payload.json senza che
    niente lo segnalasse."""
    log.info("=" * 58)
    label = f"Serie A v4.2"
    if season:
        label += f"  [season={season}]"
    if max_giornata is not None:
        label += f"  [vintage g≤{max_giornata}]"
    log.info(f"PARTE 1 — Analisi {label}")
    log.info("=" * 58)

    engine = create_engine(CFG.db_url)
    db = DatabaseLayer(engine, season=season)

    # ── Caricamento dati base ──────────────────────────────────
    sos_map = db.load_sos_map()
    sos_per_sq = pd.Series(sos_map)

    xg_col, xg_avv_col = db.detect_xg_columns()
    CFG.sgl_xg_col = xg_col
    CFG.sgl_xg_avv_col = xg_avv_col

    df_pa = db.load_players_analytics()
    df_sgl = db.load_squad_game_log()
    df_gp_raw = db.load_player_games(xg_col, xg_avv_col)

    # ── Vintage filter: limita ai dati ≤ max_giornata (per backtest OOS) ──
    if max_giornata is not None:
        _before = (len(df_gp_raw), len(df_sgl))
        df_gp_raw = df_gp_raw[df_gp_raw["giornata"] <= max_giornata].copy()
        df_sgl = df_sgl[df_sgl["giornata"] <= max_giornata].copy()
        log.info(f"Vintage g≤{max_giornata}: gp {_before[0]}→{len(df_gp_raw)}, "
                 f"sgl {_before[1]}→{len(df_sgl)}")
        # Ricalcola SOS dai dati filtrati (era stagionale)
        sos_map = _compute_sos_from_sgl(df_sgl, xg_col=xg_avv_col)
        sos_per_sq = pd.Series(sos_map)
        # Ricalcola aggregati df_pa (erano stagionali da load_players_analytics)
        _aggr = (df_gp_raw[df_gp_raw["minuti"] > 0]
                 .groupby("giocatore_id")
                 .agg(minuti=("minuti", "sum"),
                      partite=("calendario_id", "nunique"),
                      goal=("goal", "sum"),
                      xg=("xg_ind", "sum"),
                      xa=("xa_ind", "sum"))
                 .reset_index())
        _keep = [c for c in ["giocatore_id", "ruolo", "squadra_id", "squadra",
                             "nome_anagrafico", "giocatore"] if c in df_pa.columns]
        df_pa = df_pa[_keep].drop(columns=[c for c in ("minuti","partite","goal","xg","xa")
                                            if c in df_pa.columns], errors="ignore")
        df_pa = df_pa.merge(_aggr, on="giocatore_id", how="inner")
        log.info(f"Vintage g≤{max_giornata}: df_pa ricalcolato su {len(df_pa)} giocatori")

    # ── Ruoli: gerarchia di precedenza ─────────────────────────
    #   1) DB (base)  2) posizione reale Understat (default oggettivo)
    #   3) override manuale (ULTIMA PAROLA — correzioni deliberate dell'esperto)
    us_roles = derive_understat_roles()
    us_ripiego = derive_understat_roles(min_minutes=1)
    df_pa = apply_role_pipeline(df_pa, us_roles, CFG, "analisi", ripiego=us_ripiego)

    # 4) I portieri escono ADESSO, non nella query. Filtrarli su `g.ruolo`
    #    voleva dire filtrarli sul campo che sappiamo sbagliato: Meret, che in
    #    anagrafica è DIF, passava il filtro e restava fra i qualificati con
    #    TPI None (329° su 329); Idzes, che è un difensore etichettato POR,
    #    veniva buttato via insieme ai portieri veri. Dopo la risoluzione il
    #    ruolo è quello giocato davvero, e l'esclusione colpisce chi deve.
    _por = df_pa["ruolo"].astype(str).str.upper().eq("POR")
    if _por.any():
        _ids_por = {int(x) for x in df_pa.loc[_por, "giocatore_id"]}
        _nomi = ", ".join(
            str(n) for n in df_pa.loc[_por, "giocatore"].head(5)
        ) if "giocatore" in df_pa.columns else ""
        log.info(
            f"Portieri esclusi dopo la risoluzione dei ruoli: {int(_por.sum())}"
            + (f" (es. {_nomi})" if _nomi else "")
        )
        df_pa = df_pa[~_por].copy().reset_index(drop=True)
        df_gp_raw = df_gp_raw[
            ~df_gp_raw["giocatore_id"].astype("Int64").isin(_ids_por)
        ].copy()

    # ── SOS per partita ────────────────────────────────────────
    df_gp_raw["sos_avv"] = df_gp_raw["avversario_id"].map(sos_map).astype(float)
    df_gp_raw["peso_sos"] = (1.0 / df_gp_raw["sos_avv"].replace(0, np.nan)).clip(upper=10.0)

    # ── Soglie minuti ──────────────────────────────────────────
    # n_giornate = lunghezza reale stagione = giornate giocate da una squadra (38),
    # NON il max label delle giornate (può arrivare a 40 per i turni extra creati
    # dai rinvii, che però ogni squadra "salta"). Usare il max label gonfierebbe
    # soglie minuti e finestra invernale.
    _per_team_rounds = df_gp_raw.groupby("squadra_id")["giornata"].nunique()
    n_giornate = int(_per_team_rounds.max()) if len(_per_team_rounds) else int(df_gp_raw["giornata"].nunique())
    min_full, min_winter, winter_threshold, winter_ids = compute_minute_thresholds(
        df_gp_raw, n_giornate, CFG
    )

    # Stringi i winter: escludi i flaggati con ruolo NULL (giovani/fringe senza
    # posizione Understat) — non sono "acquisti", sono esordienti di contorno.
    _defined = set(
        int(g) for g, r in zip(df_pa["giocatore_id"], df_pa["ruolo"])
        if pd.notna(r) and str(r).strip() != ""
    )
    _before = len(winter_ids)
    winter_ids = {g for g in winter_ids if g in _defined}
    if _before != len(winter_ids):
        log.info(f"Winter ristretti a ruolo definito: {_before} → {len(winter_ids)}")

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
    if max_giornata is not None or season is not None:
        # Vintage o multi-stagione: ricostruisci classifica da df_sgl filtrato
        # (bypass v_classifica che non ha colonna season)
        _pts = df_sgl.groupby("squadra_id")["punti"].sum().reset_index()
        _pts = _pts.sort_values("punti", ascending=False).reset_index(drop=True)
        _pts["posizione"] = range(1, len(_pts) + 1)
        _pts["squadra_id"] = pd.to_numeric(_pts["squadra_id"], errors="coerce").astype("Int64")
        df_class = _pts[["squadra_id", "punti", "posizione"]]
    else:
        df_class = db.load_classification()
    top6_ids = compute_top6(df_class, df_sgl, CFG.n_top6_class)
    log.info(f"Top {CFG.n_top6_class}: {top6_ids}")

    # ── Calcolo 5 contesti ────────────────────────────────────
    sq_name_map = db.load_squad_names()
    difese_per_sq = carica_difese_esterne(sq_name_map)
    all_ctx = compute_all_contexts(
        df_pa, df_gp, df_sgl, sos_map, top6_ids,
        sos_per_sq, xg_col, CFG, difese_per_sq
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
    #
    # FIX SUBENTRO (2026-05-31): K è DINAMICO sull'avg_min_per_partita. Un
    # subentrante che gioca 45'/partita ha p90 estrapolato 2× rispetto a un
    # titolare → bias sistematico (Ferguson/Esposito problem). Per loro K
    # cresce (~1000-1500), il loro p90 regredisce di più verso la media-ruolo.
    # Per i titolari (75-90 min/partita) K resta il base 600 (no penalità).
    avg_min_per_partita = (
        pd.to_numeric(df_pa["minuti"], errors="coerce") /
        pd.to_numeric(df_pa["partite"], errors="coerce").replace(0, np.nan)
    ).fillna(45.0)
    # Fattore moltiplicativo K per avg_min: avg=75 → 1.0, avg=45 → 1.67, avg=30 → 2.5 (cap)
    K_subst_mult = (75.0 / avg_min_per_partita.clip(30.0, 90.0)).clip(1.0, 2.5)

    # Fattore moltiplicativo K per PARTITE COUNT: 30 partite → 1.0, 15 → 2.0, 8 → 3.0 (cap).
    # Risposta esplicita a "1 gol/partita su 30 ≠ 1 gol/partita su 5": pochi sample
    # rendono il p90 INSTABILE, va regredito di più verso la media.
    partite_count = pd.to_numeric(df_pa["partite"], errors="coerce").fillna(8).clip(lower=1)
    K_partite_mult = (30.0 / partite_count).clip(1.0, 3.0)

    # K finale = MAX dei due fattori, capped a 3.0 (no over-shrinkage).
    # Es: 15 partite × 50' avg → K_subst=1.5, K_part=2.0 → K_mult=2.0
    #     6 partite × 90' avg → K_subst=1.0, K_part=3.0 → K_mult=3.0
    K_subst_mult = pd.Series(
        np.maximum(K_subst_mult.values, K_partite_mult.values),
        index=df_pa.index
    ).clip(1.0, 3.0)

    df_pa["avg_min_per_partita"] = avg_min_per_partita.round(1)
    df_pa["k_subst_mult"] = K_subst_mult.round(3)
    _heavy_subst = (avg_min_per_partita < 60).sum()
    _few_games = (partite_count < 20).sum()
    log.info(f"  FIX subentro+partite: K dinamico, {_heavy_subst} con avg<60', "
             f"{_few_games} con <20 partite (media K_mult={K_subst_mult.mean():.2f})")

    K_out_base = float(CFG.output_prior_minutes)
    for ctx in CONTESTI:
        mcol = f"{ctx}_min_tot"
        for col in (f"{ctx}_output_adj", f"{ctx}_buildup_adj"):  # entrambi per-90
            for ruolo in df_pa["ruolo"].dropna().unique():
                rmask = df_pa["ruolo"] == ruolo
                raw = pd.to_numeric(df_pa.loc[rmask, col], errors="coerce")
                prior = raw.mean()
                if pd.isna(prior):
                    continue
                m = pd.to_numeric(df_pa.loc[rmask, mcol], errors="coerce").fillna(0.0)
                # K dinamico per ogni giocatore
                K_dyn = K_out_base * K_subst_mult.loc[rmask].values
                shrunk = (m * raw.fillna(prior) + K_dyn * prior) / (m + K_dyn)
                df_pa.loc[rmask, col] = np.where(raw.notna(), shrunk, np.nan)

    # ── FIX 3: Z-score PER RUOLO (offensivo, role-relative) ──────
    # ogni dimensione è standardizzata dentro il proprio ruolo: un difensore è
    # confrontato con i difensori, non con gli attaccanti. Resta un indice
    # offensivo, ma "relativo all'aspettativa di ruolo".
    def _z_by_role(value_col: str, fb_col: str | None = None) -> pd.Series:
        """Standardizza dentro il ruolo, o su tutta la lega se z_per_ruolo=False.

        Dentro il ruolo un difensore e' confrontato con i difensori, e serve a
        far emergere il terzino che attacca. Il prezzo e' che il numero non e'
        piu' confrontabile fra ruoli: e' un percentile intra-ruolo, che poi
        `offensive_role_weight` riscala a mano per rimetterli in una colonna
        sola. Su tutta la lega il numero torna confrontabile, ma la classifica
        la occupano gli attaccanti e i difensori interessanti spariscono.
        Il flag serve a misurare la differenza, non a nascondere la scelta.
        """
        # coercizione a float: colonne con molti None (es. boost) sarebbero object
        # e pandas 3.0 rifiuta l'assegnazione in una serie float.
        vals = pd.to_numeric(df_pa[value_col], errors="coerce")
        fb = pd.to_numeric(df_pa[fb_col], errors="coerce") if fb_col else None
        out = pd.Series(np.nan, index=df_pa.index, dtype="float64")
        if not CFG.z_per_ruolo:
            tutti = pd.Series(True, index=df_pa.index)
            return pd.to_numeric(z_series(vals, tutti, fb, min_ref=6), errors="coerce")
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
    df_pa["z_form_trend"] = _z_by_role("form_trend")

    # AII/PRI: indici trasversali → z-score per ruolo anche loro
    if "eta_index" not in df_pa.columns:
        df_pa["eta_index"] = np.nan
    if "affidabilita_fisica" not in df_pa.columns:
        df_pa["affidabilita_fisica"] = np.nan
    df_pa["z_eta_index"]           = _z_by_role("eta_index")
    df_pa["z_affidabilita_fisica"] = _z_by_role("affidabilita_fisica")

    # ── Guardia anti-dimensione-morta ────────────────────────
    # Se una dimensione con peso >0 ha varianza ~nulla nel contesto 'totale',
    # non ordina nulla (caso consistenza vecchia: 12% di peso su zeri). Lo segnala
    # forte così non passa inosservato.
    _zc_map = {"finishing": "z_finishing", "form": "z_form"}
    for _dim, _w in CFG.tpi_weights.items():
        if _w <= 0:
            continue
        _zc = _zc_map.get(_dim, f"z_totale_{_dim}")
        if _zc in df_pa.columns:
            _sd = pd.to_numeric(df_pa[_zc], errors="coerce").std()
            if pd.isna(_sd) or _sd < 0.05:
                log.error(f"DIMENSIONE MORTA: '{_dim}' (peso {_w}) ha std≈0 ({_sd}) "
                          f"→ non contribuisce al ranking, ripara o rimuovi il peso")
            else:
                log.info(f"  dim '{_dim}' (peso {_w}): std={_sd:.2f} ok")

    # ── TPI per contesto — MEDIA PESATA (qualità > uso) ──────
    # output_adj domina; centralità/boost (uso, gonfiabili su squadre deboli)
    # pesano meno; finishing (gol vs xG) penalizza chi non converte.
    # I pesi sono re-normalizzati sui dim presenti (boost/finishing possono
    # mancare) così la scala resta confrontabile tra giocatori.
    W = CFG.tpi_weights
    # dim per-contesto = tutte le chiavi-peso tranne quelle trasversali ('finishing'
    # e 'form'), che usano lo stesso z su tutti i contesti.
    _ctx_dims = [k for k in W if k not in ("finishing", "form")]
    def _weighted_tpi(ctx: str) -> pd.Series:
        num = pd.Series(0.0, index=df_pa.index)
        den = pd.Series(0.0, index=df_pa.index)
        for dim in _ctx_dims:
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
        # form: stesso z su tutti i contesti (trend recente — EWMA xG+xA/90)
        # chi non incide da N gare scende; chi è continuo o in crescita sale.
        zfm = df_pa.get("z_form")
        if zfm is not None and W.get("form", 0) > 0:
            validfm = zfm.notna()
            num = num + np.where(validfm, zfm.fillna(0.0) * W["form"], 0.0)
            den = den + np.where(validfm, W["form"], 0.0)
        return pd.Series(np.where(den > 0, num / den, np.nan), index=df_pa.index)

    for ctx in CONTESTI:
        df_pa[f"TPI_{ctx}"] = _weighted_tpi(ctx)

    # ── TPI Confidence v2 — affidabilità della stima su 4 fattori ────────
    # v1 considerava solo minuti totali + dim disponibili. Mancava esplicito:
    #   - COUNT delle partite (15 partite × 60' ≠ 30 partite × 80' come stima)
    #   - INTENSITÀ per partita (subentro vs titolare → p90 diverso)
    # v2 aggiunge entrambi come fattori separati nella geometric mean:
    #
    #   tpi_confidence = (min_conf × partite_conf × intensita_conf × dim_conf) ^ 0.25
    #
    # Esempio Donyell Malen (1499', 18 partite, 83.3'/gara, tutte dim):
    #   v1: √(0.52 × 1.0) = 0.72
    #   v2: (0.52 × 0.60 × 0.93 × 1.0)^0.25 = 0.74  (~simile, perché alto avg)
    # Esempio Berisha (893', 13 partite, 68.7'/gara, tutte dim):
    #   v1: √(0.31 × 1.0) = 0.55
    #   v2: (0.31 × 0.43 × 0.76 × 1.0)^0.25 = 0.59  (~simile)
    # L'effetto è maggiore sui giocatori con partite MOLTO basse o avg MOLTO basso.
    max_min = df_pa["minuti"].quantile(0.95).clip(1)
    min_confidence = (df_pa["minuti"] / max_min).clip(0, 1)

    # Partite: full conf a 30 partite, floor 0.3 (sotto è già un caso limite)
    partite_confidence = (
        pd.to_numeric(df_pa["partite"], errors="coerce").fillna(0) / 30.0
    ).clip(0.3, 1.0)

    # Intensità: avg_min/90 con floor 0.4 (puro subentrante ~30'/gara non azzera)
    intensita_confidence = (
        pd.to_numeric(df_pa["avg_min_per_partita"], errors="coerce").fillna(45) / 90.0
    ).clip(0.4, 1.0)

    dim_cols = [f"z_totale_{d}" for d in DIMS]
    n_dims_available = df_pa[dim_cols].notna().sum(axis=1)
    dim_confidence = (n_dims_available / len(DIMS)).clip(0, 1)

    # Geometric mean a 4 fattori (era sqrt a 2). Esponente 0.25 ⇒ ogni fattore conta uguale.
    df_pa["tpi_confidence"] = np.power(
        min_confidence * partite_confidence * intensita_confidence * dim_confidence,
        0.25
    ).round(3)
    log.info(f"  Confidence v2 (4 fattori): media={df_pa['tpi_confidence'].mean():.3f}, "
             f"<0.5: {(df_pa['tpi_confidence'] < 0.5).sum()}/{len(df_pa)}")

    # ── Cross-context stability: chi rende uguale in tutti i 5 contesti ──
    # std bassa sui 5 TPI per contesto = giocatore "tutto terreno". Invertiamo
    # il segno (alto = stabile) e z-scoriamo per ruolo. Ortogonale al livello
    # del TPI: due ATT con TPI=+1 possono avere stabilità diversa.
    _ctx_tpi_cols = [f"TPI_{c}" for c in CONTESTI]
    df_pa["ctx_stability"] = -df_pa[_ctx_tpi_cols].astype(float).std(axis=1)
    df_pa["z_ctx_stability"] = _z_by_role("ctx_stability")

    # ── Early Momentum Index (EMI): residuo TPI vs aspettativa-età ─────────
    # Risposta al limite "AII v3 satura sui 21-22enni": AII pura non discrimina
    # i prospetti (std 0.005 a 21-22 anni), il Pro non sa scegliere tra Nico Paz
    # e Pisilli. EMI guarda CHI PERFORMA AL DI SOPRA DELLA SUA ETÀ: regressione
    # lineare TPI ~ età sul sample <= peak+5 (stima la curva attesa per età),
    # poi residuo positivo = giocatore OVER l'aspettativa per la sua età.
    # Calcolato solo per giovani < peak+1 (target scout); NaN altrimenti.
    _eta_vals = pd.to_numeric(df_pa.get("eta", pd.Series(np.nan, index=df_pa.index)),
                              errors="coerce")
    _tpi_vals = pd.to_numeric(df_pa["TPI_totale"], errors="coerce")
    _fit_mask = (_eta_vals <= CFG.age_peak + 5.0) & _eta_vals.notna() & _tpi_vals.notna()
    df_pa["early_momentum"] = np.nan
    if int(_fit_mask.sum()) >= 8:
        _e = _eta_vals[_fit_mask].values
        _t = _tpi_vals[_fit_mask].values
        if np.std(_e) > 0:
            # Curva attesa per età (lineare): slope dovrebbe essere ~ -0.05 to +0.05
            _slope, _intercept = np.polyfit(_e, _t, deg=1)
            _young_mask = (_eta_vals < CFG.age_peak + 1.0) & _eta_vals.notna() & _tpi_vals.notna()
            _expected = _slope * _eta_vals + _intercept
            df_pa.loc[_young_mask, "early_momentum"] = (
                _tpi_vals[_young_mask] - _expected[_young_mask]
            )
            log.info(f"  EMI: curva età y={_slope:+.3f}·età{_intercept:+.3f}, "
                     f"calcolato su {int(_young_mask.sum())} giovani (<{CFG.age_peak+1:.0f})")
    df_pa["z_early_momentum"] = _z_by_role("early_momentum")

    # ── TPI_ext per contesto: TPI base + 5 modulatori (AGE-AWARE) ─────
    # Pesi cambiano per CATEGORIA ETÀ (PROSPETTO/PRIME/VETERANO) — risposta
    # al feedback "veterano affidabile è una categoria positiva, non un demerito".
    # Configurazione in CFG.tpi_pro_weights_age (3 set di pesi, 1 per categoria).
    eta_series = pd.to_numeric(df_pa.get("eta"), errors="coerce")
    def _classify_eta(e):
        if pd.isna(e):
            return "prime"  # default se età mancante
        if e < CFG.prospetto_max_age:
            return "prospetto"
        if e >= CFG.veterano_min_age:
            return "veterano"
        return "prime"
    df_pa["eta_cat"] = eta_series.map(_classify_eta)
    log.info(f"  TPI Pro age-aware: {df_pa['eta_cat'].value_counts().to_dict()}")

    # Pre-calcolo vettori di pesi per ogni giocatore in base alla sua categoria
    AGE_W = CFG.tpi_pro_weights_age
    def _w_for_dim(dim_key: str) -> np.ndarray:
        return df_pa["eta_cat"].map(lambda c: AGE_W[c].get(dim_key, 0.0)).values

    w_tpi = _w_for_dim("tpi")
    w_modulators = {
        "z_eta_index":           _w_for_dim("z_eta_index"),
        "z_affidabilita_fisica": _w_for_dim("z_affidabilita_fisica"),
        "z_ctx_stability":       _w_for_dim("z_ctx_stability"),
        "z_form_trend":          _w_for_dim("z_form_trend"),
        "z_early_momentum":      _w_for_dim("z_early_momentum"),
    }

    for ctx in CONTESTI:
        z_base = df_pa[f"TPI_{ctx}"].astype(float)
        num = z_base.fillna(0.0) * w_tpi
        den = np.where(z_base.notna(), w_tpi, 0.0)
        for zcol, w_vec in w_modulators.items():
            if zcol not in df_pa.columns:
                continue
            z = pd.to_numeric(df_pa[zcol], errors="coerce")
            valid = z.notna() & (w_vec > 0)
            num = num + np.where(valid, z.fillna(0.0) * w_vec, 0.0)
            den = den + np.where(valid, w_vec, 0.0)
        df_pa[f"TPI_ext_{ctx}"] = pd.Series(
            np.where(den > 0, num / den, z_base), index=df_pa.index
        )

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

    # ── Penalty disponibilità WINTER-AWARE: fare tante partite è MERIT diretto ──
    # Risposta esplicita al feedback "1 gol/partita su 30 ≠ 1 gol/partita su 5".
    # Calcoliamo disponibilità RELATIVA al periodo di presenza del giocatore:
    #   titolare:        partite_giocate / 38 (stagione intera)
    #   winter signing:  partite_giocate / partite_disp (≈19 per chi arriva a gennaio)
    # Questo gestisce correttamente Malen (winter, 18/20=90% → no penalty) vs
    # De Bruyne (titolare, 18/38=47% → penalty) vs Berisha (titolare, 13/38=34% → max penalty).
    #
    # Penalty soft: 1.0 a disp ≥ 0.70, declina lineare fino a 0.85 a disp ≤ 0.30.
    if "partite_disponibili" in df_pa.columns:
        disponibilita_rel = (
            pd.to_numeric(df_pa["partite"], errors="coerce") /
            pd.to_numeric(df_pa["partite_disponibili"], errors="coerce").replace(0, np.nan)
        ).clip(0.0, 1.0).fillna(1.0)
        disponibilita_penalty = (
            (disponibilita_rel.clip(0.30, 0.70) - 0.30) / 0.40 * 0.15 + 0.85
        )
        df_pa["disponibilita_rel"] = disponibilita_rel.round(3)
        df_pa["disponibilita_penalty"] = disponibilita_penalty.round(3)
        _penalized = (disponibilita_penalty < 0.99).sum()
        log.info(f"  Penalty disponibilità WINTER-AWARE: {_penalized} giocatori penalizzati, "
                 f"min penalty={disponibilita_penalty.min():.3f}, media={disponibilita_penalty.mean():.3f}")
        for ctx in CONTESTI:
            for base in (f"TPI_{ctx}", f"TPI_ext_{ctx}"):
                df_pa[base] = (df_pa[base] * disponibilita_penalty).round(4)

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
    for _, row in righe_payload(df_pa, CFG).iterrows():
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
    top6_names = [clean_str(sq_name_map.get(sid, str(sid))) for sid in sorted(top6_ids)]
    _dif = difese_per_sq if difese_per_sq is not None else sos_per_sq
    forti_ids = set(int(x) for x in _dif.nsmallest(CFG.n_top_difese).index)
    forti_names = [clean_str(sq_name_map.get(sid, str(sid))) for sid in sorted(forti_ids)]

    log.info(f"Top 6: {top6_names}")
    log.info(f"Difese solide: {forti_names}")

    # ── Roster completo ────────────────────────────────────────
    df_roster = db.load_roster()
    if not df_roster.empty:
        # Stessa gerarchia dell'analisi, sullo stesso dizionario Understat: la
        # rosa pubblicata e la classifica devono dire lo stesso ruolo per la
        # stessa persona. Prima qui girava solo l'override manuale, e il resto
        # della rosa usciva con il ruolo grezzo dell'anagrafica: 237 difensori
        # su 456, i portieri sparsi fra DIF e ATT.
        df_roster = apply_role_pipeline(
            df_roster, us_roles, CFG, "roster", ripiego=us_ripiego
        )
        _por_r = df_roster["ruolo"].astype(str).str.upper().eq("POR")
        if _por_r.any():
            log.info(f"Portieri fuori dal roster: {int(_por_r.sum())}")
            df_roster = df_roster[~_por_r].copy().reset_index(drop=True)

    analyzed_ids = {entry["id"] for entry in payload}
    roster_list = [
        {
            "id": int(r.get("giocatore_id") or 0),
            "nome": clean_str(r.get("nome_anagrafico") or r.get("giocatore") or "—"),
            "squadra": clean_str(r["squadra"]),
            # `or ""` non bastava: un NaN di pandas e' vero, e finiva nel JSON
            # come il letterale NaN — JSON non valido, e "NaN" scritto in
            # chiaro accanto al nome nella rosa. Chi non ha un ruolo
            # risolvibile (fringe con pochi minuti, nessuna posizione
            # Understat) esce con la stringa vuota, e la pagina scrive "—".
            "ruolo": ("" if pd.isna(r["ruolo"]) else clean_str(r["ruolo"] or "")),
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
        "metodo": blocco_metodo(CFG),
    }

    # `payload.json` E' la stagione corrente: e' il file che parte2 e parte3
    # leggono ed e' quello che finisce pubblicato. Quindi la stagione corrente
    # non mette suffisso, e i vintage restano `payload_g{N}.json` — parte3 li
    # cerca con quel glob esatto, un nome diverso e il backtest smette di
    # trovarli. Le altre stagioni tengono il loro nome esplicito, e la modalita'
    # senza filtro ne prende uno tutto suo: aggrega piu' stagioni insieme e non
    # deve poter essere scambiata per il payload del sito.
    suffix_parts = []
    if season is None:
        suffix_parts.append("tutte-le-stagioni")
    elif season != SEASON_CORRENTE:
        suffix_parts.append(season)
    if max_giornata is not None:
        suffix_parts.append(f"g{max_giornata}")
    elif CFG.top_n_payload <= 0:
        # Senza il taglio in alto questo non e' piu' il payload del sito: la
        # dashboard pubblicata mostra i primi 100. Prende un nome suo perche'
        # non deve poter finire pubblicato per sbaglio — parte3 lo preferisce
        # a payload.json per girare i test su tutti i qualificati.
        suffix_parts.append("full")
    suffix = "_" + "_".join(suffix_parts) if suffix_parts else ""
    payload_path = os.path.join(CFG.output_dir, f"payload{suffix}.json")
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
    csv_path = os.path.join(CFG.output_dir, f"summary_stats{suffix}.csv")
    df_pa[csv_cols].to_csv(csv_path, index=False)
    log.info(f"✓ CSV: {csv_path}")
    if max_giornata is None:
        log.info("→ Ora esegui: python parte2_dashboard.py")
    log.info("=" * 58)


if __name__ == "__main__":
    import argparse as _argp
    _ap = _argp.ArgumentParser(description="Motore di analisi Serie A — multi-stagione ready")
    _ap.add_argument("--max-giornata", type=int, default=None,
                     help="Limita ai dati a giornata ≤ N (per snapshot vintage / backtest OOS). "
                          "Output → payload_g{N}.json")
    _ap.add_argument("--z-lega", action="store_true",
                    help="Standardizza sulla lega invece che dentro il ruolo. "
                         "Esperimento: il punteggio torna confrontabile fra ruoli, "
                         "ma la classifica la occupano gli attaccanti.")
    _ap.add_argument("--season", type=str, default=SEASON_CORRENTE,
                     help=f"Tag stagione DB. Default: '{SEASON_CORRENTE}' (config.SEASON_CORRENTE), "
                          "che scrive payload.json — il file che parte2/parte3 leggono e che "
                          "viene pubblicato. Un'altra stagione (es. '2024-25') scrive "
                          "payload_{season}.json. REQUISITO: `calendario.season` valorizzato "
                          "(setup_multi_season.sql + backfill_24_25_completo.py).")
    _ap.add_argument("--tutte-le-stagioni", action="store_true",
                     help="Nessun filtro: aggrega TUTTE le stagioni in DB. Era il comportamento "
                          "di default finche' il DB ne conteneva una sola; ora che ce ne sono due "
                          "produce una classifica che non e' di nessuna stagione. Scrive "
                          "payload_tutte-le-stagioni.json, mai payload.json.")
    _ap.add_argument("--top-n", type=int, default=None,
                     help="Quanti giocatori nel payload. 0 = tutti i qualificati. "
                          f"Default: {Config.top_n_payload}, cioe' la dimensione della "
                          "dashboard pubblicata. Usa 0 per i vintage della validazione: "
                          "tagliare in alto attenua da solo le correlazioni.")
    _args = _ap.parse_args()
    # Il flag arriva prima di main() perche' CFG e' gia' istanziato a modulo:
    # e' l'unico punto dove cambiarlo vale sia per build_payload sia per i
    # trend precomputati, che devono coprire gli stessi giocatori.
    if _args.top_n is not None:
        CFG.top_n_payload = _args.top_n
        log.info(f"top_n_payload = {_args.top_n}"
                 + (" (tutti i qualificati)" if _args.top_n <= 0 else ""))
    CFG.z_per_ruolo = not getattr(_args, "z_lega", False)
    main(max_giornata=_args.max_giornata,
         season=None if _args.tutte_le_stagioni else _args.season)
