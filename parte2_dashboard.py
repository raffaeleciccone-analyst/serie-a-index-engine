"""
parte2_dashboard.py  —  Serie A 25/26  |  Dashboard HTML  v4.2
==============================================================
Genera dashboard_output/dashboard_serie_a.html da payload.json

Novità v4.2:
  · Mostra nome completo (p.nome) ovunque nella UI
    al posto di shortNm() ovunque nella UI
  · shortNm() mantenuta come fallback per compatibilità payload vecchi
  · Ricerca full-text su nome completo, squadra, ruolo
  · Hero e leaderboard/picker mostrano il nome completo

Miglioramenti v4 rispetto alla versione precedente:
  · Architettura più modulare: separazione netta dati / template / output
  · Nota esplicita nella UI per acquisti invernali (soglia dimezzata)
  · Leaderboard senza limite hardcoded TOP 15 (usa tutti i giocatori filtrati)
  · Badge boost_ratio con spiegazione "dati insufficienti" quando None
  · Contesto vs_forti con etichetta corretta ("difese solide per xG concessi")
  · Radar clampato a ±3 con avviso visivo
  · Aggiunto pannello "Stagione" con statistiche di lega aggregate

Uso:
  python parte2_dashboard.py
  python parte2_dashboard.py --payload /custom/path/payload.json
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("dashboard_builder")


# ════════════════════════════════════════════════════════════════
# 1. CONFIGURAZIONE
# ════════════════════════════════════════════════════════════════
try:
    _DIR = Path(__file__).parent
except NameError:
    _DIR = Path(".").resolve()

OUTPUT_DIR   = _DIR / "dashboard_output"
PAYLOAD_PATH = OUTPUT_DIR / "payload.json"
HTML_OUT     = OUTPUT_DIR / "dashboard_serie_a.html"

# Cartella del repo pubblico GitHub Pages: se esiste, ricevo una copia
# automatica della dashboard. Senza questa copia, "homepage.html" linkato
# dalla dashboard non viene trovato (sta solo nel repo demo).
# Default: cartella sorella `serie-a-scout-demo` (es. Desktop/serie-a-scout-demo
# quando questo script vive in Desktop/serie-a-scout-index). Override con
# l'env var SERIE_A_DEMO_DIR se il repo è altrove.
DEMO_DIR     = Path(os.environ.get("SERIE_A_DEMO_DIR", _DIR.parent / "serie-a-scout-demo"))


# ════════════════════════════════════════════════════════════════
# 2. COSTANTI UI
# ════════════════════════════════════════════════════════════════
RUOLO_COLORS = {
    "POR": "#636366",
    "DIF": "#0a84ff",
    "CEN": "#30d158",
    "ATT": "#ff9f0a",
    "":   "#48484a",
}

RUOLO_LABELS = {
    "POR": "Portiere",
    "DIF": "Difensore",
    "CEN": "Centrocampista",
    "ATT": "Attaccante",
}

CTX_LABELS = {
    "totale":    "Totale",
    "casa":      "Casa",
    "trasferta": "Trasferta",
    "vs_top6":   "vs Top 6",
    "vs_forti":  "Difese Solide",
}

SPIEGAZIONI = {
    "output_adj": {
        "titolo": "Output Offensivo Adj / 90'",
        "formula": "(xG + xA) / min × 90 / SOS",
        "logica": (
            "xG (expected goals) è la probabilità che un tiro diventi goal, "
            "calcolata da Opta in base a posizione, angolo e tipo di tiro. "
            "xA (expected assists) è la stessa logica applicata ai passaggi. "
            "Normalizziamo per 90 minuti per confrontare giocatori con minutaggi diversi. "
            "Poi dividiamo per il SOS (Strength of Schedule) — la difficoltà media "
            "degli avversari: SOS < 1 = avversari forti, SOS > 1 = avversari deboli. "
            "Dividere per SOS abbassa l'output di chi ha giocato contro difese deboli "
            "e alza quello di chi ha affrontato difese solide."
        ),
        "esempio": (
            "Output grezzo 0.45/90 con SOS 0.92 (avversari forti) → output adj 0.49. "
            "Stesso output grezzo con SOS 1.20 (avversari deboli) → output adj 0.37. "
            "Il secondo giocatore sembra produttivo ma contro squadre più deboli."
        ),
    },
    "centralita": {
        "titolo": "Centralità Offensiva %",
        "formula": "(xG_ind + xA_ind + K×prior) / (xG_team + K) × 100",
        "logica": (
            "Misura quanta parte della produzione offensiva della squadra "
            "passa per questo giocatore. "
            "Usa il Bayesian shrinkage: invece di fidarsi ciecamente dei dati "
            "osservati, mescola i dati reali con il prior (media ATT+CEN della lega). "
            "K è il peso dato al prior — con K=12, un giocatore con poche partite "
            "viene tirato verso la media di lega. "
            "Con molte partite i dati reali prevalgono sul prior. "
            "Questo evita di sopravvalutare giocatori con 2-3 partite eccezionali."
        ),
        "esempio": (
            "Centralità 22%: ogni 5 xG della squadra, 1.1 passano dalle sue giocate. "
            "Con K=12 e solo 3 partite: se il giocatore mostra 45% ma la media di lega "
            "è 20%, la stima bayesiana scende a circa 25% — più affidabile con pochi dati."
        ),
    },
    "boost_ratio": {
        "titolo": "Team Boost Ratio",
        "formula": "media_SOS_pond(xG_con) / media_SOS_pond(xG_senza)",
        "logica": (
            "Confronta xG della squadra CON vs SENZA il giocatore. "
            "Media ponderata per SOS (1/SOS): le partite contro difese solide "
            "pesano di più, evitando distorsioni da calendario favorevole. "
            "Richiede almeno 3 partite senza il giocatore per essere calcolato."
        ),
        "esempio": (
            "Boost 1.28: la squadra crea il 28% di xG in più quando è titolare. "
            "N/D = meno di 3 partite senza di lui disponibili."
        ),
    },
    "consistenza": {
        "titolo": "Consistenza",
        "formula": "1 − IQR / (mediana + ε) dell'output xG+xA/90 per partita",
        "logica": (
            "1 − CV trasforma il coefficiente di variazione: "
            "1.0 = stesso livello ogni partita, "
            "0.0 = totalmente imprevedibile. "
            "Richiede almeno 5 partite nel contesto."
        ),
        "esempio": (
            "Consistenza 0.78: variabilità settimanale = 22% della sua media. "
            "Sotto 0.5 = alto rischio prestazione."
        ),
    },
    "TPI": {
        "titolo": "TPI — Top Player Impact Index",
        "formula": "Media z-score (output_adj, centralità, boost, consistenza)",
        "logica": (
            "Ogni dimensione viene standardizzata come z-score rispetto alla "
            "distribuzione ATT+CEN della lega in quel contesto. "
            "TPI = media dei 4 z-score. "
            "0 = media lega, +1 = top 16%, +2 = top 2.5%. "
            "Se boost non disponibile (meno di 3 partite senza il giocatore), "
            "la media è calcolata sulle 3 dimensioni rimanenti. "
            "Lo z-score misura quante deviazioni standard un giocatore si trova "
            "sopra o sotto la media: z=(x−μ)/σ. "
            "Un TPI di +2 significa che solo il 2.5% della lega fa meglio."
        ),
        "esempio": (
            "TPI +1.72: 1.72 deviazioni standard sopra la media degli attaccanti — "
            "top 5% della lega. "
            "TPI 0.0: esattamente nella media. "
            "TPI −1.0: sotto il 16% della lega in termini di impatto offensivo."
        ),
    },
    "conv_ratio": {
        "titolo": "G/xG — Conversion Ratio",
        "formula": "Goal segnati / xG accumulati",
        "logica": (
            "Misura l'efficienza realizzativa pura, indipendente dal TPI. "
            "Il TPI usa xG+xA (creazione di occasioni), non i goal effettivi: "
            "questa metrica è ortogonale — un giocatore può avere TPI alto "
            "(crea molto) e G/xG basso (non finalizza) o viceversa. "
            "G/xG > 1 può indicare: abilità realizzativa sopra media, "
            "o fortuna temporanea (tiri che entrano dai pali). "
            "Nel lungo periodo G/xG tende a regredire verso 1.0. "
            "Richiede xG ≥ 0.5 per essere statisticamente significativo."
        ),
        "esempio": (
            "G/xG 1.35: segna il 35% in più delle aspettative — ottimo finalizzatore "
            "o fase di forma eccezionale. "
            "G/xG 0.65: spreca molte occasioni — possibile regressione futura. "
            "G/xG 1.0: esattamente in linea con le aspettative xG."
        ),
    },
}


# ════════════════════════════════════════════════════════════════
# 3. CARICAMENTO PAYLOAD
# ════════════════════════════════════════════════════════════════
def load_payload(path: Path) -> dict:
    if not path.exists():
        found = list(Path(_DIR).rglob("payload.json"))
        if not found:
            log.error("payload.json non trovato. Esegui prima parte1_analisi.py")
            sys.exit(1)
        path = found[0]
        log.info(f"payload.json trovato in: {path}")

    with open(path, encoding="utf-8") as fh:
        meta = json.load(fh)

    required_keys = ["players", "n_giornate", "n_giocatori"]
    for k in required_keys:
        if k not in meta:
            log.error(f"payload.json non valido: chiave mancante '{k}'")
            sys.exit(1)


    log.info(
        f"Payload caricato: {len(meta['players'])} giocatori, "
        f"{meta.get('n_giornate')} giornate"
    )
    return meta


def clean(s) -> str:
    if not isinstance(s, str):
        return str(s) if s is not None else ""
    return s.encode("utf-8", errors="replace").decode("utf-8")


def deep_clean(obj):
    if isinstance(obj, str):
        return clean(obj)
    if isinstance(obj, dict):
        return {k: deep_clean(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [deep_clean(v) for v in obj]
    return obj


# ════════════════════════════════════════════════════════════════
# 4. TEMPLATE HTML
# ════════════════════════════════════════════════════════════════
HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="it">
<head>
<meta charset="UTF-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'self'; script-src 'self' https://cdn.plot.ly 'unsafe-inline'; style-src 'self' https://cdn.jsdelivr.net 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'none'; object-src 'none'">
<meta http-equiv="X-Content-Type-Options" content="nosniff">
<meta name="referrer" content="strict-origin-when-cross-origin">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<title>Serie A Scout Index — Data-driven Player Ranking Model</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css"
      integrity="sha384-QWTKZyjpPEjISv5WaRU9OFeRpok6YctnYmDr5pNlyT2bRjXh0JMhjY6hW+ALEwIH"
      crossorigin="anonymous" referrerpolicy="no-referrer">
<script src="https://cdn.plot.ly/plotly-2.27.0.min.js"
        integrity="sha384-Hl48Kq2HifOWdXEjMsKo6qxqvRLTYqIGbvlENBmkHAxZKIGCXv43H6W1jA671RzC"
        crossorigin="anonymous" referrerpolicy="no-referrer"></script>
<script src="i18n.js"></script>
<style>
/* ═══════════════════════════════════════
   DESIGN SYSTEM — Apple HIG Dark + Liquid Glass
═══════════════════════════════════════ */
:root {
  --bg:#000; --bg1:#111113; --bg2:#1c1c1e; --bg3:#2c2c2e; --bg4:#3a3a3c;
  --sep:rgba(255,255,255,.10); --sep2:rgba(255,255,255,.18);
  --lp:#fff; --ls:rgba(235,235,245,.62); --lt:rgba(235,235,245,.32); --lq:rgba(235,235,245,.18);
  --blue:#0a84ff; --green:#30d158; --orng:#ff9f0a;
  --red:#ff453a; --purp:#bf5af2; --teal:#5ac8fa; --indig:#5e5ce6;
  --r:16px; --rsm:12px; --rxs:8px;
  --font:-apple-system,BlinkMacSystemFont,"SF Pro Display","Helvetica Neue",sans-serif;
  --mono:"SF Mono","Cascadia Code","Fira Code",monospace;
  --gl-bg:rgba(255,255,255,.06); --gl-border:rgba(255,255,255,.16);
  --gl-edge:rgba(255,255,255,.28); --gl-blur:saturate(220%) blur(36px);
  --gl-shadow:0 8px 32px rgba(0,0,0,.55),0 2px 8px rgba(0,0,0,.35),inset 0 1px 0 rgba(255,255,255,.12);
}
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
html{scroll-behavior:smooth}
body{font-family:var(--font);background:var(--bg);color:var(--lp);font-size:15px;
  line-height:1.47;-webkit-font-smoothing:antialiased;overflow-x:hidden}
::-webkit-scrollbar{width:3px;height:3px}
::-webkit-scrollbar-thumb{background:var(--bg3);border-radius:2px}
.row{--bs-gutter-x:12px;--bs-gutter-y:12px}

/* ── Liquid Glass materials ── */
.nav{position:sticky;top:0;z-index:400;height:52px;
  background:rgba(10,10,12,.72);backdrop-filter:var(--gl-blur);
  -webkit-backdrop-filter:var(--gl-blur);border-bottom:1px solid var(--gl-border);
  box-shadow:0 1px 0 var(--gl-edge),0 4px 24px rgba(0,0,0,.4);
  display:flex;align-items:center;gap:10px;padding:0 20px}
.card{background:linear-gradient(160deg,rgba(255,255,255,.07) 0%,rgba(255,255,255,.03) 100%);
  backdrop-filter:saturate(180%) blur(16px);-webkit-backdrop-filter:saturate(180%) blur(16px);
  border-radius:var(--r);border:1px solid var(--gl-border);border-top-color:var(--gl-edge);
  box-shadow:0 4px 20px rgba(0,0,0,.45),inset 0 1px 0 rgba(255,255,255,.08);padding:16px}
.glass-pop{background:rgba(20,20,22,.88);backdrop-filter:saturate(240%) blur(52px);
  -webkit-backdrop-filter:saturate(240%) blur(52px);border:1px solid var(--gl-border);
  border-top-color:var(--gl-edge);box-shadow:var(--gl-shadow)}
.mbox{background:rgba(22,22,24,.92);backdrop-filter:saturate(220%) blur(48px);
  -webkit-backdrop-filter:saturate(220%) blur(48px);border:1px solid var(--gl-border);
  border-top-color:var(--gl-edge);
  box-shadow:0 32px 80px rgba(0,0,0,.9),inset 0 1px 0 rgba(255,255,255,.12);
  border-radius:20px;padding:26px;max-width:480px;width:92%}
.mtile{background:linear-gradient(150deg,rgba(255,255,255,.06) 0%,rgba(255,255,255,.02) 100%);
  border-radius:var(--rsm);border:1px solid var(--gl-border);
  border-top-color:rgba(255,255,255,.2);
  box-shadow:0 2px 12px rgba(0,0,0,.4),inset 0 1px 0 rgba(255,255,255,.07);
  padding:14px;position:relative;overflow:hidden}
.mtile::before{content:"";position:absolute;top:0;left:0;right:0;height:2px}
.m1::before{background:linear-gradient(90deg,var(--blue),var(--indig))}
.m2::before{background:linear-gradient(90deg,var(--green),var(--teal))}
.m3::before{background:linear-gradient(90deg,var(--orng),var(--red))}
.m4::before{background:linear-gradient(90deg,var(--purp),var(--indig))}
.ctx-bar,.tabs{background:rgba(12,12,14,.8);backdrop-filter:saturate(160%) blur(20px);
  -webkit-backdrop-filter:saturate(160%) blur(20px);border-bottom:1px solid var(--gl-border);
  display:flex;overflow-x:auto;scrollbar-width:none;padding:0 20px}
.ctx-bar::-webkit-scrollbar,.tabs::-webkit-scrollbar{display:none}
.hero{background:linear-gradient(180deg,rgba(28,28,32,.9) 0%,rgba(18,18,20,.95) 100%);
  backdrop-filter:saturate(160%) blur(20px);-webkit-backdrop-filter:saturate(160%) blur(20px);
  border-bottom:1px solid var(--gl-border);padding:14px 20px;
  display:flex;align-items:center;gap:14px;flex-wrap:wrap}
.hero.hidden{display:none}
.back-bar{display:none;padding:8px 20px;background:rgba(12,12,14,.75);
  backdrop-filter:saturate(160%) blur(16px);-webkit-backdrop-filter:saturate(160%) blur(16px);
  border-bottom:1px solid var(--gl-border);align-items:center;gap:10px}
.back-bar.on{display:flex}

/* ── Nav buttons (back / fwd / home / switch) ── */
.nav-glass-btn{
  display:inline-flex;align-items:center;justify-content:center;
  width:30px;height:30px;
  background:rgba(255,255,255,.06);
  backdrop-filter:saturate(180%) blur(20px);
  -webkit-backdrop-filter:saturate(180%) blur(20px);
  border:1px solid rgba(255,255,255,.14);
  border-top-color:rgba(255,255,255,.24);
  border-radius:9px;
  font-size:14px;color:rgba(235,235,245,.62);
  cursor:pointer;
  box-shadow:0 2px 8px rgba(0,0,0,.3),inset 0 1px 0 rgba(255,255,255,.08);
  transition:all .18s cubic-bezier(.4,0,.2,1);
  font-family:var(--font);
  flex-shrink:0;
  text-decoration:none;
}
.nav-glass-btn:hover:not(:disabled){
  background:rgba(255,255,255,.11);color:#fff;
  border-top-color:rgba(255,255,255,.36);
  box-shadow:0 3px 14px rgba(0,0,0,.4),inset 0 1px 0 rgba(255,255,255,.12);
  transform:translateY(-1px);
}
.nav-glass-btn:active:not(:disabled){transform:translateY(0);opacity:.8}
.nav-glass-btn:disabled{opacity:.28;cursor:not-allowed;transform:none}
.nav-glass-btn.home-btn{width:auto;padding:0 12px;gap:5px;font-size:12px;font-weight:600}
.nav-switch-btn{
  display:inline-flex;align-items:center;gap:6px;height:30px;padding:0 12px;
  background:rgba(191,90,242,.08);
  backdrop-filter:saturate(180%) blur(20px);
  -webkit-backdrop-filter:saturate(180%) blur(20px);
  border:1px solid rgba(191,90,242,.22);
  border-top-color:rgba(191,90,242,.35);
  border-radius:9px;
  font:12px/1 var(--font);font-weight:600;
  color:var(--purp);
  text-decoration:none;cursor:pointer;
  box-shadow:0 2px 8px rgba(0,0,0,.3),inset 0 1px 0 rgba(255,255,255,.07);
  transition:all .18s cubic-bezier(.4,0,.2,1);
  flex-shrink:0;
  white-space:nowrap;
}
.nav-switch-btn:hover{
  background:rgba(191,90,242,.14);
  border-color:rgba(191,90,242,.4);
  border-top-color:rgba(191,90,242,.55);
  color:#d18ef8;
  box-shadow:0 3px 14px rgba(0,0,0,.4),inset 0 1px 0 rgba(255,255,255,.1);
  transform:translateY(-1px);
}
.nav-switch-dot{
  width:5px;height:5px;border-radius:50%;
  background:var(--purp);
  box-shadow:0 0 5px rgba(191,90,242,.7);
  flex-shrink:0;
}
.nav-btn-group{display:flex;align-items:center;gap:5px;margin-left:10px}
.nav-right-group{display:flex;align-items:center;gap:8px;margin-left:auto}

/* ── Nav ── */
.nav-brand{font-size:16px;font-weight:700;letter-spacing:-.5px;white-space:nowrap;flex-shrink:0}
.nav-brand small{font-size:12px;font-weight:400;color:var(--lt);margin-left:5px}
.nav-chip{font-size:11px;color:var(--lt);white-space:nowrap;flex-shrink:0;margin-left:auto}

/* ── Shared button / popover ── */
.sq-wrap{position:relative;flex-shrink:0}
.sq-btn{display:flex;align-items:center;gap:6px;height:34px;font:12px var(--font);
  font-weight:600;color:var(--ls);
  background:rgba(255,255,255,.06);
  backdrop-filter:saturate(180%) blur(20px);-webkit-backdrop-filter:saturate(180%) blur(20px);
  border:1px solid rgba(255,255,255,.14);border-top-color:rgba(255,255,255,.24);
  border-radius:10px;padding:0 11px;cursor:pointer;white-space:nowrap;
  box-shadow:0 2px 10px rgba(0,0,0,.32),inset 0 1px 0 rgba(255,255,255,.09);
  transition:all .18s cubic-bezier(.4,0,.2,1)}
.sq-btn:hover{background:rgba(255,255,255,.10);color:var(--lp);
  border-top-color:rgba(255,255,255,.36);transform:translateY(-1px);
  box-shadow:0 4px 16px rgba(0,0,0,.42),inset 0 1px 0 rgba(255,255,255,.12)}
.sq-btn:active{transform:translateY(0);opacity:.85}
.sq-btn.active{color:var(--lp);background:rgba(255,255,255,.11);
  border-color:rgba(255,255,255,.28);border-top-color:rgba(255,255,255,.38)}
.sq-chevron{font-size:9px;opacity:.5;transition:transform .2s}
.sq-btn.open .sq-chevron{transform:rotate(180deg);opacity:.9}
@keyframes sqIn{from{opacity:0;transform:translateY(-6px) scale(.97)}to{opacity:1;transform:none}}

/* ── Control bar — Liquid Glass ── */
.ctrl-bar{display:flex;align-items:center;gap:7px;padding:10px 16px 8px;
  overflow-x:auto;scrollbar-width:none;flex-wrap:nowrap;
  -webkit-overflow-scrolling:touch;border-bottom:1px solid rgba(255,255,255,.07);
  background:rgba(8,8,10,.55);
  backdrop-filter:saturate(200%) blur(32px);
  -webkit-backdrop-filter:saturate(200%) blur(32px)}
.ctrl-bar::-webkit-scrollbar{display:none}
.ctrl-div{width:1px;height:20px;background:rgba(255,255,255,.1);flex-shrink:0;margin:0 1px}

/* Pill base — liquid glass */
.mpill{display:flex;align-items:center;gap:5px;padding:0 12px;height:33px;border-radius:12px;
  background:linear-gradient(160deg,rgba(255,255,255,.10),rgba(255,255,255,.04));
  backdrop-filter:saturate(180%) blur(20px);-webkit-backdrop-filter:saturate(180%) blur(20px);
  border:1px solid rgba(255,255,255,.13);border-top-color:rgba(255,255,255,.26);
  color:rgba(235,235,245,.55);font:12px var(--font);font-weight:600;cursor:pointer;
  white-space:nowrap;transition:all .18s cubic-bezier(.4,0,.2,1);flex-shrink:0;
  box-shadow:0 2px 10px rgba(0,0,0,.35),inset 0 1px 0 rgba(255,255,255,.10)}
.mpill:hover{background:linear-gradient(160deg,rgba(255,255,255,.16),rgba(255,255,255,.07));
  color:rgba(235,235,245,.85);border-top-color:rgba(255,255,255,.38);transform:translateY(-1px);
  box-shadow:0 5px 18px rgba(0,0,0,.42),inset 0 1px 0 rgba(255,255,255,.14)}
.mpill:active{transform:translateY(0);opacity:.85}
.mpill.on{background:linear-gradient(160deg,rgba(255,255,255,.18),rgba(255,255,255,.08));
  color:#fff;border-color:rgba(255,255,255,.30);border-top-color:rgba(255,255,255,.50);
  box-shadow:0 3px 16px rgba(255,255,255,.10),inset 0 1px 0 rgba(255,255,255,.18)}

/* Pill ruolo */
.rpill{display:flex;align-items:center;height:33px;padding:0 11px;border-radius:12px;
  background:linear-gradient(160deg,rgba(255,255,255,.09),rgba(255,255,255,.03));
  backdrop-filter:saturate(180%) blur(20px);-webkit-backdrop-filter:saturate(180%) blur(20px);
  border:1px solid rgba(255,255,255,.12);border-top-color:rgba(255,255,255,.24);
  color:rgba(235,235,245,.5);font:11px var(--font);font-weight:700;cursor:pointer;
  white-space:nowrap;transition:all .18s cubic-bezier(.4,0,.2,1);flex-shrink:0;
  box-shadow:0 2px 8px rgba(0,0,0,.32),inset 0 1px 0 rgba(255,255,255,.09)}
.rpill:hover{background:linear-gradient(160deg,rgba(255,255,255,.15),rgba(255,255,255,.06));
  color:rgba(235,235,245,.85);transform:translateY(-1px);
  box-shadow:0 5px 16px rgba(0,0,0,.4),inset 0 1px 0 rgba(255,255,255,.13)}
.rpill:active{transform:translateY(0);opacity:.85}
.rpill[data-r="ATT"].on{background:linear-gradient(160deg,rgba(255,159,10,.18),rgba(255,159,10,.07));
  color:var(--orng);border-color:rgba(255,159,10,.38);border-top-color:rgba(255,159,10,.55);
  box-shadow:0 3px 14px rgba(255,159,10,.18),inset 0 1px 0 rgba(255,255,255,.1)}
.rpill[data-r="CEN"].on{background:linear-gradient(160deg,rgba(48,209,88,.16),rgba(48,209,88,.06));
  color:var(--green);border-color:rgba(48,209,88,.38);border-top-color:rgba(48,209,88,.55);
  box-shadow:0 3px 14px rgba(48,209,88,.15),inset 0 1px 0 rgba(255,255,255,.1)}
.rpill[data-r="DIF"].on{background:linear-gradient(160deg,rgba(10,132,255,.18),rgba(10,132,255,.07));
  color:var(--blue);border-color:rgba(10,132,255,.40);border-top-color:rgba(10,132,255,.58);
  box-shadow:0 3px 14px rgba(10,132,255,.18),inset 0 1px 0 rgba(255,255,255,.1)}

/* Pill Confronta — speciale */
.cpill{display:flex;align-items:center;gap:5px;padding:0 12px;height:33px;border-radius:12px;
  background:linear-gradient(160deg,rgba(94,92,230,.18),rgba(94,92,230,.07));
  backdrop-filter:saturate(180%) blur(20px);-webkit-backdrop-filter:saturate(180%) blur(20px);
  border:1px solid rgba(94,92,230,.32);border-top-color:rgba(94,92,230,.50);
  color:var(--indig);font:12px var(--font);font-weight:700;cursor:pointer;
  white-space:nowrap;transition:all .18s cubic-bezier(.4,0,.2,1);flex-shrink:0;
  box-shadow:0 2px 10px rgba(94,92,230,.18),inset 0 1px 0 rgba(255,255,255,.10)}
.cpill:hover{background:linear-gradient(160deg,rgba(94,92,230,.26),rgba(94,92,230,.12));
  transform:translateY(-1px);box-shadow:0 5px 18px rgba(94,92,230,.28)}
.cpill:active{transform:translateY(0);opacity:.85}
.lb-wrap{padding:12px 20px 20px}
.lb-hdr{display:flex;align-items:center;gap:6px;margin-bottom:10px}
.lb-ttl{font-size:13px;font-weight:700;color:var(--ls)}
.lb-sub{font-size:11px;color:var(--lt);margin-left:auto}

/* ── Home ── */
#view-home{display:block} #view-player{display:none}
.home-hdr{padding:28px 20px 20px;border-bottom:1px solid var(--sep);
  background:linear-gradient(180deg,rgba(10,132,255,.04) 0%,transparent 100%)}
.home-ttl{font-size:clamp(22px,3.5vw,30px);font-weight:800;letter-spacing:-1.2px}
.home-sub{font-size:13px;color:var(--lt);margin-top:4px}
.home-sub b{color:var(--blue);font-weight:600}
.team-strip{display:none;padding:12px 20px;gap:10px;flex-wrap:wrap;
  border-bottom:1px solid var(--sep);
  background:linear-gradient(180deg,rgba(255,255,255,.025) 0%,transparent 100%)}
.team-strip.on{display:flex}
.ts-card{background:linear-gradient(150deg,rgba(255,255,255,.06) 0%,rgba(255,255,255,.02) 100%);
  border:1px solid var(--gl-border);border-top-color:rgba(255,255,255,.2);
  border-radius:var(--rsm);box-shadow:0 2px 12px rgba(0,0,0,.35),inset 0 1px 0 rgba(255,255,255,.06);
  padding:12px 14px;min-width:160px;flex:1;max-width:220px}
.ts-nm{font-size:13px;font-weight:700;margin-bottom:7px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.ts-kpis{display:flex;gap:14px;margin-bottom:7px}
.tsk{display:flex;flex-direction:column}
.tsk-v{font-size:18px;font-weight:800;letter-spacing:-.8px;line-height:1}
.tsk-l{font-size:9px;color:var(--lt);text-transform:uppercase;letter-spacing:.5px;margin-top:2px}
.ts-top{font-size:11px;color:var(--lt);line-height:1.8}
.ts-top b{color:var(--ls);font-weight:500}

/* ── Back / Hero ── */
.back-btn{
  display:inline-flex;align-items:center;gap:6px;height:32px;
  font:13px var(--font);font-weight:600;
  color:rgba(235,235,245,.72);
  background:rgba(255,255,255,.07);
  backdrop-filter:saturate(180%) blur(20px);
  -webkit-backdrop-filter:saturate(180%) blur(20px);
  border:1px solid rgba(255,255,255,.14);
  border-top-color:rgba(255,255,255,.24);
  border-radius:12px;padding:0 14px;
  cursor:pointer;
  box-shadow:0 2px 10px rgba(0,0,0,.3),inset 0 1px 0 rgba(255,255,255,.08);
  transition:all .18s cubic-bezier(.4,0,.2,1);
}
.back-btn:hover{
  background:rgba(255,255,255,.11);
  color:#fff;
  border-color:rgba(255,255,255,.22);
  border-top-color:rgba(255,255,255,.36);
  box-shadow:0 4px 16px rgba(0,0,0,.4),inset 0 1px 0 rgba(255,255,255,.12);
  transform:translateY(-1px);
}
.back-btn:active{transform:translateY(0);opacity:.85;}
.back-cur{font-size:13px;color:var(--lt);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.hero-av{width:44px;height:44px;border-radius:50%;flex-shrink:0;display:flex;
  align-items:center;justify-content:center;font-size:19px;
  box-shadow:inset 0 0 0 1.5px rgba(255,255,255,.1)}
.hero-inf{flex:1;min-width:0}
.hero-nm{font-size:19px;font-weight:700;letter-spacing:-.5px;white-space:nowrap;
  overflow:hidden;text-overflow:ellipsis;display:flex;align-items:center;gap:7px}
.hero-sub{font-size:12px;color:var(--lt);margin-top:2px}
.hero-tags{display:flex;gap:6px;flex-wrap:wrap;flex-shrink:0}
.tag{padding:4px 9px;border-radius:8px;font-size:11px;font-weight:600;white-space:nowrap}
.tag-role{color:#fff}
.tag-rank{background:rgba(255,159,10,.1);color:var(--orng);border:1px solid rgba(255,159,10,.25)}
.tag-up{background:rgba(48,209,88,.1);color:var(--green);border:1px solid rgba(48,209,88,.25)}
.tag-dn{background:rgba(255,69,58,.08);color:var(--red);border:1px solid rgba(255,69,58,.2)}
.tag-flat{background:rgba(255,255,255,.05);color:var(--ls);border:1px solid var(--sep)}
.tag-snow{background:rgba(90,200,250,.1);color:var(--teal);border:1px solid rgba(90,200,250,.25)}
.tag-warn{background:rgba(255,159,10,.1);color:var(--orng);border:1px solid rgba(255,159,10,.2);font-size:10px}
.hero-tpi{text-align:right;flex-shrink:0}
.hero-tpi-lbl{font-size:10px;font-weight:600;color:var(--lt);text-transform:uppercase;letter-spacing:.8px}
.hero-tpi-val{font-size:32px;font-weight:800;letter-spacing:-1.5px;line-height:1;margin-top:2px}

/* ── Ctx / Tabs ── */
.ctx-btn,.tab-btn{padding:10px 14px;font-size:13px;font-weight:500;color:var(--lt);
  white-space:nowrap;cursor:pointer;border:none;background:none;
  border-bottom:2px solid transparent;font-family:var(--font);transition:color .16s,border-color .16s}
.ctx-btn:hover,.tab-btn:hover{color:var(--ls)}
.ctx-btn.on,.tab-btn.on{color:var(--lp);border-bottom-color:var(--blue)}
.ctx-n{font-size:10px;font-family:var(--mono);background:rgba(255,255,255,.07);
  border-radius:6px;padding:1px 5px;margin-left:3px}
.ctx-n.warn{background:rgba(255,159,10,.14);color:var(--orng)}
.panel{display:none;padding:20px} .panel.on{display:block}

/* ── Layout / Grid ── */
.g2{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:12px}
.g4{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-bottom:12px}
@media(max-width:860px){.g4{grid-template-columns:repeat(2,1fr)}}
@media(max-width:560px){.g2{grid-template-columns:1fr}}
.card-ttl{font-size:11px;font-weight:700;color:var(--lt);text-transform:uppercase;
  letter-spacing:.7px;margin-bottom:14px;display:flex;align-items:center;gap:5px}
.card-ttl span{margin-left:auto}

/* ── Metric tiles ── */
.mtile-lbl{font-size:10px;font-weight:700;color:var(--lt);text-transform:uppercase;
  letter-spacing:.6px;margin-bottom:4px;display:flex;align-items:center;gap:3px}
.mtile-val{font-size:26px;font-weight:700;letter-spacing:-1px;line-height:1;color:var(--lp);margin-bottom:4px}
.mtile-z{font-size:12px;font-weight:600;font-family:var(--mono);margin-bottom:5px}
.mbar{height:3px;background:rgba(255,255,255,.07);border-radius:2px;position:relative;overflow:hidden;margin-bottom:6px}
.mbar-f{position:absolute;height:100%;border-radius:2px;transition:width .5s cubic-bezier(.4,0,.2,1),left .5s cubic-bezier(.4,0,.2,1)}
.mtile-rk{font-size:10px;color:var(--lt)} .mtile-rk b{color:var(--orng)}
.nd-badge{font-size:10px;color:var(--lt);background:rgba(255,255,255,.05);
  border:1px solid var(--sep);border-radius:6px;padding:2px 7px;margin-top:4px;display:inline-block}

/* ── Conversion hero ── */
.cv-hero{display:flex;align-items:flex-start;gap:14px;padding:14px;
  border-radius:var(--rsm);background:rgba(255,255,255,.03);border:1px solid var(--sep)}
.cv-big{font-size:48px;font-weight:800;letter-spacing:-3px;line-height:1;flex-shrink:0}
.cv-big.over{color:var(--green)} .cv-big.under{color:var(--red)} .cv-big.flat{color:var(--lp)}
.cv-det{flex:1;min-width:0}
.cv-verd{font-size:13px;color:var(--ls);line-height:1.55;margin-bottom:9px}
.cv-pills{display:flex;flex-wrap:wrap;gap:5px}
.cvp{background:rgba(255,255,255,.04);border:1px solid var(--sep);
  border-radius:var(--rxs);padding:5px 9px;font-size:12px;color:var(--lt)}
.cvp b{color:var(--lp);font-family:var(--mono)}

/* ── Z-score bars ── */
.zlist{display:flex;flex-direction:column;gap:8px}
.zrow{display:flex;align-items:center;gap:9px}
.znm{width:130px;font-size:12px;color:var(--ls);flex-shrink:0;display:flex;align-items:center;gap:3px}
.ztrk{flex:1;height:5px;background:rgba(255,255,255,.06);border-radius:3px;position:relative;overflow:hidden}
.zsp{position:absolute;left:50%;top:0;width:1px;height:100%;background:rgba(255,255,255,.14)}
.zf{position:absolute;height:100%;border-radius:3px;transition:all .4s cubic-bezier(.4,0,.2,1)}
.zval{width:38px;text-align:right;font-size:12px;font-weight:600;font-family:var(--mono)}

/* ── Stat pills ── */
.spills{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:9px}
.spill{background:rgba(255,255,255,.04);border:1px solid var(--sep);border-radius:var(--rxs);
  padding:7px 11px;display:flex;flex-direction:column;align-items:center;min-width:66px}
.spill-lbl{font-size:9px;font-weight:700;color:var(--lt);text-transform:uppercase;
  letter-spacing:.5px;margin-bottom:3px}
.spill-val{font-size:14px;font-weight:700;color:var(--lp);font-family:var(--mono)}
.spill.pos{border-color:rgba(48,209,88,.22)} .spill.pos .spill-val{color:var(--green)}
.spill.neg{border-color:rgba(255,69,58,.22)} .spill.neg .spill-val{color:var(--red)}
.spill.over{border-color:rgba(255,159,10,.22)} .spill.over .spill-val{color:var(--orng)}

/* ── Ctx info bar ── */
.ctx-inf{display:flex;align-items:center;gap:7px;flex-wrap:wrap;
  padding:8px 12px;margin-bottom:14px;background:rgba(255,255,255,.03);
  border:1px solid var(--sep);border-radius:var(--rsm);font-size:12px;color:var(--lt)}
.ctx-inf .ok{color:var(--green);font-weight:600}
.ctx-inf .warn{color:var(--orng);font-weight:600}

/* ── AI / Analisi card ── */
.ai-card{background:linear-gradient(135deg,rgba(94,92,230,.07),rgba(191,90,242,.04));
  border:1px solid rgba(94,92,230,.18);border-radius:var(--r);padding:16px}
.ai-hd{display:flex;align-items:center;gap:7px;margin-bottom:9px}
.ai-badge{font-size:10px;font-weight:700;letter-spacing:.6px;text-transform:uppercase;
  color:var(--purp);background:rgba(191,90,242,.1);border:1px solid rgba(191,90,242,.22);
  padding:3px 8px;border-radius:6px}
.ai-body{font-size:13px;line-height:1.65;color:var(--ls)}

/* ── Metodologia ── */
.meth-grid{display:grid;grid-template-columns:repeat(2,1fr);gap:12px}
@media(max-width:700px){.meth-grid{grid-template-columns:1fr}}
.meth-card{background:linear-gradient(150deg,rgba(255,255,255,.06) 0%,rgba(255,255,255,.02) 100%);
  border-radius:var(--r);border:1px solid var(--gl-border);
  border-top-color:var(--gl-edge);padding:16px}
.meth-hd{display:flex;align-items:center;gap:10px;margin-bottom:12px}
.meth-icon{width:36px;height:36px;border-radius:var(--rsm);display:flex;
  align-items:center;justify-content:center;font-size:18px;flex-shrink:0}
.meth-nm{font-size:15px;font-weight:600}
.meth-formula{font-family:var(--mono);font-size:12px;background:rgba(255,255,255,.04);
  border:1px solid var(--sep);border-radius:var(--rxs);padding:8px 12px;
  margin-bottom:9px;color:var(--teal);line-height:1.5}
.meth-logic{font-size:13px;color:var(--ls);line-height:1.65;margin-bottom:9px}
.meth-ex{font-size:12px;color:var(--lt);border-left:2px solid var(--blue);
  padding:7px 12px;line-height:1.5}

/* ── Compare ── */
.cmp-sel{flex:1;min-width:180px;font-size:13px;padding:8px 12px;background:var(--bg2);
  border:1px solid var(--sep);border-radius:var(--rsm);color:var(--lp);font-family:var(--font)}

/* ── Modal ── */
.mwrap{position:fixed;inset:0;z-index:600;background:rgba(0,0,0,.7);
  backdrop-filter:blur(12px);-webkit-backdrop-filter:blur(12px);
  display:none;align-items:center;justify-content:center}
.mwrap.open{display:flex}
.mbox-ttl{font-size:19px;font-weight:700;letter-spacing:-.4px;margin-bottom:4px}
.mbox-form{font-family:var(--mono);font-size:12px;background:rgba(255,255,255,.04);
  border:1px solid var(--sep);border-radius:var(--rxs);padding:8px 12px;
  margin:12px 0;color:var(--teal)}
.mbox-body{font-size:13px;color:var(--ls);line-height:1.7;margin-bottom:8px}
.mbox-ex{font-size:12px;color:var(--lt);border-left:2px solid var(--blue);
  padding:8px 12px;line-height:1.55}
.mbox-cls{margin-top:18px;width:100%;padding:11px;
  background:rgba(255,255,255,.07);
  backdrop-filter:saturate(160%) blur(16px);-webkit-backdrop-filter:saturate(160%) blur(16px);
  border:1px solid rgba(255,255,255,.12);border-top-color:rgba(255,255,255,.22);
  border-radius:var(--rsm);color:var(--lp);font:14px var(--font);cursor:pointer;
  box-shadow:0 2px 8px rgba(0,0,0,.25),inset 0 1px 0 rgba(255,255,255,.08);
  transition:all .18s cubic-bezier(.4,0,.2,1)}
.mbox-cls:hover{background:rgba(255,255,255,.12);transform:translateY(-1px);
  box-shadow:0 4px 14px rgba(0,0,0,.35),inset 0 1px 0 rgba(255,255,255,.12)}
.mbox-cls:active{transform:translateY(0);opacity:.85}

/* ── Floating picker ── */
.fpk-box{position:fixed;width:300px;max-height:420px;overflow:hidden;
  border-radius:16px;background:rgba(18,18,20,.85);backdrop-filter:saturate(240%) blur(52px);
  -webkit-backdrop-filter:saturate(240%) blur(52px);border:1px solid var(--gl-border);
  border-top-color:var(--gl-edge);
  box-shadow:0 24px 64px rgba(0,0,0,.85),0 4px 16px rgba(0,0,0,.5),inset 0 1px 0 rgba(255,255,255,.1);
  display:none;z-index:700;animation:sqIn .18s cubic-bezier(.4,0,.2,1)}
.fpk-box.open{display:flex;flex-direction:column}
.fpk-box::after{content:"";position:absolute;bottom:0;left:0;right:0;height:48px;
  background:linear-gradient(transparent,rgba(18,18,20,.95));
  pointer-events:none;border-radius:0 0 16px 16px;z-index:2}
.fpk-search{display:flex;align-items:center;gap:7px;flex-shrink:0;
  margin:8px;padding:0 10px;background:rgba(255,255,255,.08);
  border:1px solid rgba(255,255,255,.14);border-top-color:rgba(255,255,255,.22);
  border-radius:10px;box-shadow:inset 0 1px 3px rgba(0,0,0,.3)}
.fpk-search-ico{color:var(--lt);font-size:14px;flex-shrink:0}
.fpk-search-inp{flex:1;background:transparent;border:none;outline:none;
  font:14px var(--font);color:var(--lp);padding:9px 0}
.fpk-search-inp::placeholder{color:var(--lt)}
.fpk-list{overflow-y:auto;flex:1;padding:0 6px 40px;scrollbar-width:thin;
  -webkit-overflow-scrolling:touch}
.fpk-list::-webkit-scrollbar{width:3px}
.fpk-list::-webkit-scrollbar-thumb{background:var(--bg3);border-radius:2px}
.fpk-item{display:flex;align-items:center;gap:10px;padding:10px;
  border-radius:10px;cursor:pointer;transition:background .1s;min-height:44px}
.fpk-item:hover{background:rgba(255,255,255,.06)} .fpk-item.sel{background:rgba(10,132,255,.1)}
.fpk-dot{width:8px;height:8px;border-radius:50%;flex-shrink:0}
.fpk-bd{flex:1;min-width:0}
.fpk-nm{font-size:14px;font-weight:500;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.fpk-sub{font-size:11px;color:var(--lt);margin-top:1px}
.fpk-rt{display:flex;align-items:center;gap:5px;flex-shrink:0}
.fpk-tpi{font-size:12px;font-weight:700;font-family:var(--mono)}
.fpk-tpi.pos{color:var(--green)} .fpk-tpi.neg{color:var(--red)}
.fpk-sep{height:1px;background:var(--sep);margin:4px 8px}
.fpk-role-grp{font-size:10px;font-weight:700;color:var(--lt);text-transform:uppercase;
  letter-spacing:.7px;padding:10px 10px 4px}
.fpk-empty{padding:24px;text-align:center;font-size:13px;color:var(--lt)}
.sq-fpk-box{position:fixed;z-index:700;width:250px;max-height:380px;overflow-y:auto;
  border-radius:16px;padding:6px;display:none;background:rgba(18,18,20,.88);
  backdrop-filter:saturate(240%) blur(52px);-webkit-backdrop-filter:saturate(240%) blur(52px);
  border:1px solid var(--gl-border);border-top-color:var(--gl-edge);
  box-shadow:0 24px 64px rgba(0,0,0,.85),0 4px 16px rgba(0,0,0,.5),inset 0 1px 0 rgba(255,255,255,.1);
  animation:sqIn .18s cubic-bezier(.4,0,.2,1);
  -webkit-overflow-scrolling:touch}
.sq-fpk-box.open{display:block}
.sq-fpk-box::after{content:"";position:absolute;bottom:0;left:0;right:0;height:36px;
  background:linear-gradient(transparent,rgba(18,18,20,.96));
  pointer-events:none;border-radius:0 0 16px 16px;z-index:2}
.sq-panel-hd{font:10px/1 var(--font);font-weight:700;color:var(--lt);
  text-transform:uppercase;letter-spacing:.8px;
  padding:7px 10px 10px;border-bottom:1px solid var(--sep);margin-bottom:4px}
.sq-row{display:flex;align-items:center;justify-content:space-between;
  padding:9px 10px;border-radius:10px;cursor:pointer;min-height:40px;transition:background .1s}
.sq-row:hover{background:rgba(255,255,255,.05)} .sq-row.on{background:rgba(10,132,255,.08)}
.sq-row-nm{font-size:13px;color:var(--ls)} .sq-row.on .sq-row-nm{color:var(--lp);font-weight:500}
.sq-chk{width:20px;height:20px;border-radius:50%;flex-shrink:0;
  border:1.5px solid rgba(255,255,255,.18);
  display:flex;align-items:center;justify-content:center;font-size:11px;transition:all .15s}
.sq-row.on .sq-chk{background:var(--blue);border-color:var(--blue);color:#fff}
.sq-reset{width:100%;padding:9px 10px;background:transparent;border:none;border-radius:10px;
  color:var(--lt);font:12px var(--font);cursor:pointer;text-align:left;
  display:flex;align-items:center;gap:7px;min-height:38px;transition:background .1s}
.sq-reset:hover{background:rgba(255,255,255,.05);color:var(--ls)}
.sq-panel-sep{height:1px;background:var(--sep);margin:4px 0}

/* ── Leaderboard ── */
.lb-list{display:flex;flex-direction:column;gap:4px}
.lb-row{display:flex;align-items:center;gap:10px;padding:10px 12px;
  border-radius:12px;cursor:default;
  background:linear-gradient(135deg,rgba(255,255,255,.045) 0%,rgba(255,255,255,.015) 100%);
  border:1px solid var(--sep);border-top-color:rgba(255,255,255,.12);transition:background .15s}
.lb-row:hover{background:linear-gradient(135deg,rgba(255,255,255,.07) 0%,rgba(255,255,255,.03) 100%)}
.lb-rank{font-size:11px;font-weight:700;color:var(--lt);font-family:var(--mono);
  width:22px;text-align:right;flex-shrink:0}
.lb-dot{width:9px;height:9px;border-radius:50%;flex-shrink:0}
.lb-info{flex:1;min-width:0}
.lb-nm{font-size:14px;font-weight:600;white-space:nowrap;overflow:hidden;
  text-overflow:ellipsis;display:flex;align-items:center;gap:5px}
.lb-team{font-size:11px;color:var(--lt);margin-top:1px}
.lb-bar-wrap{width:120px;height:5px;background:rgba(255,255,255,.07);
  border-radius:3px;overflow:hidden;flex-shrink:0}
.lb-bar-fill{height:100%;border-radius:3px;transition:width .4s cubic-bezier(.4,0,.2,1)}
.lb-val{font-size:13px;font-weight:700;font-family:var(--mono);width:52px;text-align:right;flex-shrink:0}
.lb-actions{display:flex;gap:5px;flex-shrink:0}
.lb-btn{display:inline-flex;align-items:center;gap:4px;height:28px;padding:0 10px;
  border-radius:8px;font:11px var(--font);font-weight:600;cursor:pointer;
  transition:all .15s;white-space:nowrap;border:1px solid transparent}
.lb-btn-prof{color:var(--blue);
  background:rgba(10,132,255,.08);
  backdrop-filter:saturate(160%) blur(12px);-webkit-backdrop-filter:saturate(160%) blur(12px);
  border-color:rgba(10,132,255,.22);border-top-color:rgba(10,132,255,.35)}
.lb-btn-prof:hover{background:rgba(10,132,255,.16);transform:translateY(-1px);
  box-shadow:0 3px 12px rgba(10,132,255,.2),inset 0 1px 0 rgba(255,255,255,.08)}
.lb-btn-prof:active{transform:translateY(0)}
.lb-btn-cmp{color:var(--ls);
  background:rgba(255,255,255,.05);
  backdrop-filter:saturate(160%) blur(12px);-webkit-backdrop-filter:saturate(160%) blur(12px);
  border-color:rgba(255,255,255,.12);border-top-color:rgba(255,255,255,.20)}
.lb-btn-cmp:hover{color:var(--lp);background:rgba(255,255,255,.10);transform:translateY(-1px);
  box-shadow:0 3px 12px rgba(0,0,0,.3),inset 0 1px 0 rgba(255,255,255,.08)}
.lb-btn-cmp:active{transform:translateY(0)}
.lb-btn-cmp.in-pool{color:var(--green);background:rgba(48,209,88,.08);
  border-color:rgba(48,209,88,.25);border-top-color:rgba(48,209,88,.40)}

/* ── Roster ── */
.roster-section{margin-top:16px}
.roster-hdr{font-size:11px;font-weight:700;color:var(--lt);text-transform:uppercase;
  letter-spacing:.7px;padding:10px 0 8px;display:flex;align-items:center;gap:8px}
.roster-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:6px}
.roster-card{display:flex;align-items:center;gap:8px;padding:9px 11px;border-radius:10px;
  background:rgba(255,255,255,.03);border:1px solid var(--sep);opacity:.6}
.roster-nm{font-size:13px;font-weight:500;flex:1;min-width:0;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.roster-min{font-size:10px;color:var(--lt);font-family:var(--mono);flex-shrink:0}

/* ── Compare pool bar ── */
.cmp-pool-bar{position:fixed;bottom:20px;left:50%;transform:translateX(-50%);
  display:none;align-items:center;gap:8px;padding:10px 16px;border-radius:16px;z-index:700;
  background:rgba(22,22,24,.94);backdrop-filter:saturate(220%) blur(40px);
  -webkit-backdrop-filter:saturate(220%) blur(40px);border:1px solid var(--gl-edge);
  box-shadow:0 12px 40px rgba(0,0,0,.7),inset 0 1px 0 rgba(255,255,255,.1);
  max-width:calc(100vw - 32px);animation:sqIn .18s cubic-bezier(.4,0,.2,1)}
.cmp-pool-bar.on{display:flex}
.cmp-pool-lbl{font-size:12px;font-weight:600;color:var(--ls);white-space:nowrap}
.cmp-pool-chips{display:flex;gap:5px;flex-wrap:wrap}
.cmp-chip{display:flex;align-items:center;gap:5px;padding:4px 9px;
  border-radius:8px;background:rgba(255,255,255,.07);border:1px solid var(--sep);
  font-size:12px;font-weight:500;cursor:pointer;transition:background .15s}
.cmp-chip:hover{background:rgba(255,255,255,.12)}
.cmp-chip-x{font-size:11px;color:var(--lt);margin-left:2px}
.cmp-pool-go{display:inline-flex;align-items:center;gap:5px;height:34px;padding:0 14px;
  border-radius:10px;font:13px var(--font);font-weight:700;cursor:pointer;
  color:#fff;
  background:linear-gradient(160deg,rgba(30,150,255,.95),rgba(10,132,255,.9));
  backdrop-filter:saturate(180%) blur(12px);-webkit-backdrop-filter:saturate(180%) blur(12px);
  border:1px solid rgba(10,132,255,.5);border-top-color:rgba(80,180,255,.6);
  box-shadow:0 3px 14px rgba(10,132,255,.4),inset 0 1px 0 rgba(255,255,255,.2);
  transition:all .18s cubic-bezier(.4,0,.2,1);white-space:nowrap}
.cmp-pool-go:hover{background:linear-gradient(160deg,rgba(40,160,255,.98),rgba(20,142,255,.95));
  transform:translateY(-1px);box-shadow:0 5px 20px rgba(10,132,255,.5),inset 0 1px 0 rgba(255,255,255,.25)}
.cmp-pool-go:active{transform:translateY(0);opacity:.9}
.cmp-pool-clr{height:34px;padding:0 11px;border-radius:10px;font:12px var(--font);
  font-weight:600;cursor:pointer;color:var(--lt);
  background:rgba(255,255,255,.06);
  backdrop-filter:saturate(160%) blur(12px);-webkit-backdrop-filter:saturate(160%) blur(12px);
  border:1px solid rgba(255,255,255,.12);border-top-color:rgba(255,255,255,.20);
  box-shadow:0 2px 8px rgba(0,0,0,.25),inset 0 1px 0 rgba(255,255,255,.07);
  transition:all .18s cubic-bezier(.4,0,.2,1)}
.cmp-pool-clr:hover{color:var(--red);border-color:rgba(255,69,58,.3);
  background:rgba(255,69,58,.06);transform:translateY(-1px)}
.cmp-pool-clr:active{transform:translateY(0)}

/* ── Help ── */
.help{display:inline-flex;align-items:center;justify-content:center;
  width:14px;height:14px;border-radius:50%;background:rgba(255,255,255,.09);
  border:1px solid var(--sep);font-size:9px;color:var(--lt);cursor:pointer;
  transition:all .15s;flex-shrink:0;margin-left:3px;vertical-align:middle}
.help:hover{background:var(--blue);color:#fff;border-color:var(--blue)}

/* ════════════════════════════════════════════════════════════
   RESPONSIVE — Tablet portrait (≤ 768px) e Phone (≤ 480px)
════════════════════════════════════════════════════════════ */

/* ── Tablet portrait ≤ 768px ── */
@media (max-width: 768px) {

  /* Nav: comprimi */
  .nav{padding:0 12px;gap:6px;height:48px}
  .nav-brand{font-size:14px}
  .nav-brand small{display:none}
  .nav-chip{display:none}
  .nav-btn-group{margin-left:6px;gap:4px}
  .nav-right-group{gap:5px;flex-shrink:0}
  .hp-label{display:none}
  .val-label{display:none}
  .nav-switch-btn{padding:0 8px;min-width:30px}

  /* Back bar */
  .back-bar{padding:6px 12px}
  .back-cur{font-size:11px;max-width:180px}

  /* Home header */
  .home-hdr{padding:18px 14px 14px}
  .home-ttl{font-size:22px}

  /* Team strip: scroll orizzontale invece di wrap */
  .team-strip{flex-wrap:nowrap;overflow-x:auto;padding:10px 12px;
    -webkit-overflow-scrolling:touch;scrollbar-width:none}
  .team-strip::-webkit-scrollbar{display:none}
  .ts-card{min-width:150px;max-width:170px;flex-shrink:0}

  /* Control bar: touch targets più grandi */
  .ctrl-bar{padding:10px 12px 8px;gap:6px}
  .mpill{height:34px;padding:0 10px;font-size:11px}
  .rpill{height:34px;padding:0 9px}

  /* Leaderboard: comprimi barra e valore */
  .lb-wrap{padding:10px 12px 16px}
  .lb-row{padding:9px 10px;gap:8px}
  .lb-bar-wrap{width:70px}
  .lb-val{width:44px;font-size:12px}
  .lb-btn{height:26px;padding:0 8px;font-size:10px}

  /* Hero player */
  .hero{padding:12px 14px;gap:10px}
  .hero-av{width:38px;height:38px;font-size:16px}
  .hero-nm{font-size:16px}
  .hero-tpi-val{font-size:26px}

  /* Tab bar: testo più piccolo */
  .tab-btn,.ctx-btn{padding:9px 10px;font-size:12px}

  /* Pannello */
  .panel{padding:14px}

  /* Griglie */
  .g2{grid-template-columns:1fr 1fr}
  .g4{grid-template-columns:1fr 1fr}

  /* Metric tiles */
  .mtile-val{font-size:22px}

  /* Compare selects */
  .cmp-sel{min-width:130px}

  /* Metodologia griglia: 1 col */
  .meth-grid{grid-template-columns:1fr}

  /* TPI Pro section */
  .tpp-hdr{padding:16px 14px 0}
  .tpp-dims{padding:10px 14px 0;gap:6px}
  .tpp-body{padding:12px 14px 16px}
  .tpp-cols{grid-template-columns:1fr 1fr}
  .tpp-collapse-btn{font-size:11px;height:28px;padding:0 9px}
  .tpp-ttl{font-size:18px}
  .tpp-formula{font-size:11px;overflow-x:auto;white-space:nowrap}
  .tpp-dim{font-size:11px;padding:4px 8px}
  /* Fix overflow card */
  #tpi-pro-section{overflow:hidden;width:100%}
  .tpp-card{overflow:hidden}

  /* Floating picker: più largo */
  .fpk-box{width:calc(100vw - 24px);left:12px !important}
  .sq-fpk-box{width:calc(100vw - 24px);left:12px !important}

  /* Chart Plotly: altezza ridotta */
  #c-meth-bar{height:180px !important}
}

/* ── Phone portrait ≤ 480px ── */
@media (max-width: 480px) {

  /* Nav ancora più compatta */
  .nav{padding:0 10px;gap:4px;height:46px}
  .nav-brand{font-size:13px}
  .nav-glass-btn.home-btn .home-label{display:none}
  .nav-glass-btn.home-btn{padding:0 8px}
  /* Su mobile nascondi testo "Homepage" nel pulsante arancione, lascia solo icona */
  .nav-right-group a:first-child .hp-label{display:none}

  /* Home header: nascondi logo SVG su mobile, lascia solo testo */
  .home-hdr{padding:14px 12px 12px}
  .home-hdr svg{display:none}
  .home-hdr > div{flex-direction:column;gap:6px}
  .home-hdr > div > div:last-child{text-align:left;margin-top:0}

  /* TPI Pro: fix overflow formula e testo tagliato */
  #tpi-pro-section{overflow:hidden;width:100%}
  .tpp-formula{overflow-x:auto;white-space:nowrap;-webkit-overflow-scrolling:touch}
  .tpp-sub{font-size:12px;word-break:break-word}
  .tpp-ttl{font-size:15px;word-break:break-word}

  /* Control bar: scroll orizzontale senza wrap */
  .ctrl-bar{padding:8px 10px 6px;gap:5px}
  .mpill{height:32px;padding:0 9px;font-size:11px}
  .rpill{height:32px;padding:0 8px;font-size:11px}
  .ctrl-div{display:none} /* nasconde i separatori verticali */

  /* Leaderboard righe: layout compatto */
  .lb-wrap{padding:8px 10px 14px}
  .lb-row{padding:8px 10px;gap:6px;border-radius:10px}
  .lb-rank{font-size:10px;width:18px}
  .lb-dot{width:7px;height:7px}
  .lb-nm{font-size:13px}
  .lb-team{font-size:10px}
  .lb-bar-wrap{display:none}        /* nasconde barra grafica — troppo stretta */
  .lb-val{font-size:12px;width:40px}
  .lb-actions{gap:4px}
  .lb-btn{height:26px;padding:0 7px;font-size:10px}
  /* "Confronto" → "+" su phone */
  .lb-btn-cmp .cmp-full{display:none}
  .lb-btn-cmp::before{content:"\2295"}

  /* Hero player */
  .hero{padding:10px 12px;gap:8px}
  .hero-av{width:34px;height:34px;font-size:14px}
  .hero-nm{font-size:15px;gap:5px}
  .hero-sub{font-size:11px}
  .hero-tags{gap:4px}
  .tag{font-size:10px;padding:3px 7px}
  .hero-tpi-lbl{font-size:9px}
  .hero-tpi-val{font-size:24px}

  /* Tab e ctx bar */
  .tab-btn,.ctx-btn{padding:8px 9px;font-size:11px}

  /* Pannello padding ridotto */
  .panel{padding:10px 10px 16px}

  /* Griglie: tutto in colonna singola */
  .g2{grid-template-columns:1fr}
  .g4{grid-template-columns:1fr 1fr}

  /* Tile metriche */
  .mtile{padding:11px}
  .mtile-val{font-size:20px}
  .mtile-lbl{font-size:9px}

  /* Card padding */
  .card{padding:12px}
  .card-ttl{font-size:10px;margin-bottom:10px}

  /* Compare selects: full width */
  .cmp-sel{width:100%;min-width:unset}
  #p-cmp > div:first-child{flex-direction:column}

  /* Modal */
  .mbox{padding:20px 16px;border-radius:16px}

  /* Z-list labels */
  .znm{width:100px;font-size:11px}
  .zval{font-size:11px;width:32px}

  /* Stat pills */
  .spill{min-width:58px;padding:6px 8px}
  .spill-lbl{font-size:8px}
  .spill-val{font-size:13px}

  /* Conversion hero */
  .cv-big{font-size:38px}
  .cvp{font-size:11px;padding:4px 7px}

  /* TPI Pro section: 1 colonna */
  .tpp-hdr{padding:14px 12px 0}
  .tpp-dims{padding:8px 12px 0;gap:5px}
  .tpp-body{padding:10px 12px 14px}
  .tpp-cols{grid-template-columns:1fr}
  .tpp-ttl{font-size:16px}
  .tpp-sub{font-size:12px}
  .tpp-formula{font-size:10px;padding:5px 10px;gap:5px}
  .tpp-dim{font-size:10px;padding:3px 7px}
  .tpp-dim span{display:none}
  /* Nascondi VISIBILE su phone — sezione TPI Pro è ingombrante */
  .tpp-collapse-btn{display:inline-flex;font-size:10px;height:26px;padding:0 8px}
  .tpp-card{padding:11px 12px}
  .tpp-card-nm{font-size:13px;white-space:normal;overflow:visible;text-overflow:unset}
  .tpp-card-sub{font-size:10px}
  .tpp-bar-lbl{font-size:9px;width:38px}
  .tpp-bar-val{font-size:10px;width:32px}
  .tpp-kpi-val{font-size:14px}
  .tpp-kpi{padding:5px 7px}
  .tpp-delta{font-size:10px;padding:2px 5px}

  /* Roster grid */
  .roster-grid{grid-template-columns:1fr 1fr}

  /* Picker full width */
  .fpk-box{width:calc(100vw - 20px);left:10px !important;right:10px}
  .sq-fpk-box{width:calc(100vw - 20px);left:10px !important}

  /* Compare pool bar */
  .cmp-pool-bar{padding:8px 10px;gap:8px;flex-wrap:wrap}
  .cmp-pool-go{height:32px;font-size:12px}
  .cmp-pool-clr{height:32px}

  /* Plotly charts: altezza ridotta per non occupare schermo intero */
  #c-meth-bar{height:160px !important}
  #cmp-radar{height:220px !important}
  #cmp-bars{height:220px !important}
  #cmp-ctx{height:180px !important}
  #cmp-conv{height:160px !important}
}

/* ── Utility: prevent zoom su input iOS ── */
@media (max-width: 768px) {
  input, select, textarea {
    font-size: 16px !important;  /* evita autozoom iOS Safari */
  }
  .fpk-search-inp{font-size:16px !important}
}


/* ════════════════════════════════════════
   TPI PRO SHOWCASE — sezione hero home
════════════════════════════════════════ */
#tpi-pro-section{
  border-bottom:1px solid var(--sep);
  background:linear-gradient(180deg,rgba(191,90,242,.04) 0%,rgba(94,92,230,.03) 50%,transparent 100%);
  position:relative;overflow:hidden}
#tpi-pro-section::before{
  content:"";position:absolute;inset:0;
  background:radial-gradient(ellipse 70% 60% at 50% -10%,rgba(191,90,242,.08),transparent 70%);
  pointer-events:none}
.tpp-hdr{padding:28px 24px 0;position:relative}
.tpp-badge{display:inline-flex;align-items:center;gap:6px;padding:4px 10px;border-radius:8px;
  font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.6px;
  background:rgba(191,90,242,.12);border:1px solid rgba(191,90,242,.28);color:var(--purp);margin-bottom:12px}
.tpp-ttl{font-size:22px;font-weight:800;letter-spacing:-.6px;line-height:1.15;margin-bottom:8px}
.tpp-sub{font-size:13px;color:var(--ls);line-height:1.7;max-width:640px}
.tpp-formula{display:inline-flex;align-items:center;gap:8px;margin-top:10px;padding:7px 13px;
  background:rgba(255,255,255,.04);border:1px solid var(--sep);border-radius:var(--rsm);
  font-size:12px;font-family:var(--mono);color:var(--teal);flex-wrap:wrap}
.tpp-formula .sep{color:var(--lt)}
.tpp-collapse-btn{height:30px;padding:0 12px;border-radius:8px;font:11px var(--font);
  font-weight:700;cursor:pointer;
  background:rgba(191,90,242,.08);
  border:1px solid rgba(191,90,242,.28);
  border-top-color:rgba(191,90,242,.4);
  color:var(--purp);
  box-shadow:0 2px 8px rgba(191,90,242,.1),inset 0 1px 0 rgba(255,255,255,.06);
  transition:all .18s;white-space:nowrap;letter-spacing:.3px;text-transform:uppercase}
.tpp-collapse-btn:hover{background:rgba(191,90,242,.15);transform:translateY(-1px)}
.tpp-hdr-extra{padding:0 20px 0}
.tpp-dims{display:flex;gap:10px;padding:14px 20px 0;flex-wrap:wrap;position:relative}
.tpp-dim{display:flex;align-items:center;gap:6px;padding:5px 10px;border-radius:8px;font-size:12px;font-weight:600}
.tpp-dim-aii{background:rgba(90,200,250,.08);border:1px solid rgba(90,200,250,.2);color:var(--teal)}
.tpp-dim-pri{background:rgba(191,90,242,.08);border:1px solid rgba(191,90,242,.2);color:var(--purp)}
.tpp-dim-tpi{background:rgba(255,159,10,.08);border:1px solid rgba(255,159,10,.2);color:var(--orng)}
.tpp-body{padding:16px 20px 20px;position:relative}
/* Griglia 3 colonne ruolo */
.tpp-cols{display:grid;grid-template-columns:repeat(3,1fr);gap:14px}
@media(max-width:860px){.tpp-cols{grid-template-columns:1fr 1fr}}
@media(max-width:560px){.tpp-cols{grid-template-columns:1fr}}
.tpp-col-hdr{font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.8px;
  margin-bottom:10px;display:flex;align-items:center;gap:6px}
.tpp-col-hdr .dot{width:8px;height:8px;border-radius:50%}
/* Carte giocatori */
.tpp-card{background:linear-gradient(150deg,rgba(255,255,255,.055) 0%,rgba(255,255,255,.018) 100%);
  border:1px solid var(--gl-border);border-top-color:rgba(255,255,255,.2);
  border-radius:var(--rsm);padding:13px 14px;margin-bottom:10px;position:relative;overflow:hidden;
  transition:border-color .18s,box-shadow .18s;cursor:pointer}
.tpp-card:hover{border-color:rgba(191,90,242,.4);box-shadow:0 4px 20px rgba(191,90,242,.1)}
.tpp-card:last-child{margin-bottom:0}
.tpp-card-nm{font-size:14px;font-weight:700;letter-spacing:-.3px;margin-bottom:2px;
  white-space:normal;overflow:visible}
.tpp-card-sub{font-size:11px;color:var(--lt);margin-bottom:11px}
/* Barre TPI vs TPI Pro */
.tpp-bars{display:flex;flex-direction:column;gap:6px;margin-bottom:11px}
.tpp-bar-row{display:flex;align-items:center;gap:8px}
.tpp-bar-lbl{font-size:10px;font-weight:700;color:var(--lt);width:48px;flex-shrink:0;text-transform:uppercase;letter-spacing:.4px}
.tpp-bar-lbl.pro{color:var(--purp)}
.tpp-bar-track{flex:1;height:6px;background:rgba(255,255,255,.06);border-radius:3px;overflow:hidden;position:relative}
.tpp-bar-zero{position:absolute;left:50%;top:0;width:1px;height:100%;background:rgba(255,255,255,.15)}
.tpp-bar-fill{height:100%;border-radius:3px;position:absolute;transition:width .5s cubic-bezier(.4,0,.2,1)}
.tpp-bar-val{font-size:11px;font-weight:700;font-family:var(--mono);width:38px;text-align:right;flex-shrink:0}
/* Badge AII + PRI */
.tpp-kpis{display:flex;gap:6px;flex-wrap:wrap}
.tpp-kpi{flex:1;min-width:70px;background:rgba(255,255,255,.03);border:1px solid var(--sep);
  border-radius:var(--rxs);padding:6px 9px}
.tpp-kpi-lbl{font-size:9px;font-weight:700;text-transform:uppercase;letter-spacing:.5px;color:var(--lt);margin-bottom:3px}
.tpp-kpi-val{font-size:16px;font-weight:800;letter-spacing:-.5px;font-family:var(--mono);line-height:1}
.tpp-kpi-bar{height:2px;background:rgba(255,255,255,.06);border-radius:1px;margin-top:4px;overflow:hidden}
.tpp-kpi-bar-f{height:100%;border-radius:1px}
/* Delta rank badge */
.tpp-delta{position:absolute;top:10px;right:10px;font-size:11px;font-weight:700;
  padding:2px 7px;border-radius:6px;font-family:var(--mono)}
.tpp-delta.up{background:rgba(48,209,88,.1);color:var(--green);border:1px solid rgba(48,209,88,.2)}
.tpp-delta.dn{background:rgba(255,69,58,.08);color:var(--red);border:1px solid rgba(255,69,58,.15)}
.tpp-delta.eq{background:rgba(255,255,255,.05);color:var(--lt);border:1px solid var(--sep)}
/* Empty state */
.tpp-empty{padding:32px 20px;text-align:center;color:var(--lt);font-size:13px;line-height:1.7}
.tpp-empty strong{color:var(--orng)}

/* ── Bottoni nav unificati ── */
.nav-home-btn{
  display:inline-flex;align-items:center;gap:6px;height:30px;padding:0 13px;
  border-radius:9px;font:12px var(--font);font-weight:700;cursor:pointer;
  background:rgba(10,132,255,.1);border:1px solid rgba(10,132,255,.3);
  border-top-color:rgba(10,132,255,.45);color:var(--blue);
  box-shadow:0 2px 8px rgba(10,132,255,.15),inset 0 1px 0 rgba(255,255,255,.08);
  transition:all .18s;white-space:nowrap;flex-shrink:0}
.nav-home-btn:hover{background:rgba(10,132,255,.18);transform:translateY(-1px)}
.nav-orng-btn{
  display:inline-flex;align-items:center;gap:6px;height:30px;padding:0 13px;
  border-radius:9px;font:12px var(--font);font-weight:700;text-decoration:none;
  background:rgba(255,159,10,.12);border:1px solid rgba(255,159,10,.35);
  border-top-color:rgba(255,159,10,.5);color:var(--orng);
  box-shadow:0 2px 8px rgba(255,159,10,.15),inset 0 1px 0 rgba(255,255,255,.07);
  transition:all .18s;white-space:nowrap;flex-shrink:0}
.nav-orng-btn:hover{background:rgba(255,159,10,.2);transform:translateY(-1px)}
.nav-purp-btn{
  display:inline-flex;align-items:center;gap:6px;height:30px;padding:0 13px;
  border-radius:9px;font:12px var(--font);font-weight:700;text-decoration:none;
  background:rgba(191,90,242,.1);border:1px solid rgba(191,90,242,.3);
  border-top-color:rgba(191,90,242,.45);color:var(--purp);
  box-shadow:0 2px 8px rgba(191,90,242,.12),inset 0 1px 0 rgba(255,255,255,.07);
  transition:all .18s;white-space:nowrap;flex-shrink:0}
.nav-purp-btn:hover{background:rgba(191,90,242,.18);transform:translateY(-1px)}

/* Mobile: brand nascosto, tutto su una riga */
@media(max-width:768px){
  .nav-brand{display:none}
  .nav{justify-content:flex-start}
  .nav-btn-group{margin-left:0}
  .nav-right-group{margin-left:auto}
  .hp-label,.val-label,.home-label{display:none}
  .nav-home-btn,.nav-orng-btn,.nav-purp-btn{padding:0 9px;gap:4px}
}
@media(max-width:480px){
  .nav{height:46px;padding:0 8px;gap:5px}
  .nav-glass-btn{width:28px;height:28px}
  .nav-home-btn,.nav-orng-btn,.nav-purp-btn{height:28px;padding:0 8px}
}
</style>
</head>
<body>

<!-- Error banner -->
<div id="err-banner" style="display:none;position:fixed;bottom:20px;left:20px;right:20px;
  background:var(--red);color:#fff;padding:13px 16px;border-radius:12px;font-size:13px;
  z-index:9999;box-shadow:0 8px 24px rgba(0,0,0,.6)">
  <strong data-i18n="dash_js_error">Errore JS:</strong> <span id="err-msg"></span>
  <span style="float:right;cursor:pointer;opacity:.7" onclick="this.parentElement.style.display='none'">&times;</span>
</div>
<script>
window.onerror=function(m,s,l){
  var b=document.getElementById('err-banner');
  if(b){document.getElementById('err-msg').textContent=m+' (r.~'+l+')';b.style.display='block';}
  return false;
};
</script>

<!-- Modal -->
<div class="mwrap" id="modal" onclick="closeM()">
  <div class="mbox" onclick="event.stopPropagation()">
    <div class="mbox-ttl" id="m-t"></div>
    <div class="mbox-form" id="m-f"></div>
    <div class="mbox-body" id="m-b"></div>
    <div class="mbox-ex" id="m-e"></div>
    <button class="mbox-cls" onclick="closeM()" data-i18n="btn_close">Chiudi</button>
  </div>
</div>

<!-- NAV -->
<nav class="nav">
  <div class="nav-brand">Serie A 25/26 <small>Scout&nbsp;Index</small></div>
  <div class="nav-btn-group">
    <button class="nav-home-btn" onclick="showHome()" data-i18n-title="nav_back_ranking" title="Torna alla classifica">
      &#127942; <span class="home-label" data-i18n="term_ranking">Classifica</span>
    </button>
    <button class="nav-glass-btn" id="nav-back-btn" onclick="histBack()" data-i18n-title="nav_back" data-i18n-aria-label="nav_back" title="Indietro" disabled>&#8592;</button>
    <button class="nav-glass-btn" id="nav-fwd-btn"  onclick="histForward()" data-i18n-title="nav_forward" data-i18n-aria-label="nav_forward" title="Avanti" disabled>&#8594;</button>
  </div>
  <div class="nav-right-group" style="display:flex;align-items:center;gap:8px">
    <span data-i18n-switcher></span>
    <a class="nav-orng-btn" href="homepage.html" data-i18n-title="nav_back_homepage" title="Torna alla Homepage">
      &#127968; <span class="hp-label" data-i18n="nav_home">Homepage</span>
    </a>
    <a class="nav-purp-btn" href="validazione.html" data-i18n-title="nav_validation" title="Validazione TPI">
      &#128202; <span class="val-label" data-i18n="nav_validation">Validazione</span>
    </a>
  </div>
</nav>

<!-- Back bar -->
<div class="back-bar" id="back-bar">
  <button class="back-btn" id="back-btn-main" onclick="histBack()">&#8592; <span id="back-label" data-i18n="term_ranking">Classifiche</span></button>
  <span class="back-cur" id="back-cur"></span>
</div>

<!-- ═══ HOME ═══ -->
<div id="view-home">
  <div class="home-hdr">
    <div style="display:flex;align-items:flex-start;justify-content:space-between;gap:12px;flex-wrap:wrap">
      <div>
        <div class="home-ttl">Serie A Scout Index
          <span class="help" onclick="openM('TPI')" style="font-size:13px;width:18px;height:18px;margin-left:6px">?</span>
        </div>
        <div style="font-size:11px;color:var(--purp);font-weight:600;letter-spacing:.3px;
          text-transform:uppercase;margin-top:2px;margin-bottom:6px" data-i18n="hp_tagline">
          Data-driven Player Ranking Model
        </div>
        <div class="home-sub" id="home-sub">
          <b>__N_GIO__</b> players ranked &middot; __N_GIOR__ matchdays &middot; 6 independent KPIs
          <span class="help" onclick="toggleMeth()" style="margin-left:4px" title="Methodology">?</span>
        </div>
      </div>
      <!-- Logo TPI estetico -->
      <div style="flex-shrink:0;margin-top:2px">
        <svg width="64" height="64" viewBox="0 0 64 64" fill="none" xmlns="http://www.w3.org/2000/svg">
          <defs>
            <radialGradient id="bg-grd" cx="50%" cy="50%" r="50%">
              <stop offset="0%" stop-color="rgba(255,159,10,.18)"/>
              <stop offset="100%" stop-color="rgba(255,159,10,.04)"/>
            </radialGradient>
            <linearGradient id="arc-grd" x1="0%" y1="0%" x2="100%" y2="100%">
              <stop offset="0%" stop-color="#ff9f0a"/>
              <stop offset="100%" stop-color="#ff6b00"/>
            </linearGradient>
            <linearGradient id="bar-grd" x1="0%" y1="0%" x2="0%" y2="100%">
              <stop offset="0%" stop-color="#ff9f0a"/>
              <stop offset="100%" stop-color="rgba(255,159,10,.3)"/>
            </linearGradient>
          </defs>
          <!-- Sfondo cerchio -->
          <circle cx="32" cy="32" r="30" fill="url(#bg-grd)" stroke="rgba(255,159,10,.25)" stroke-width="1"/>
          <!-- Arco esterno decorativo -->
          <path d="M 32 6 A 26 26 0 0 1 55 45" stroke="url(#arc-grd)" stroke-width="2.5" stroke-linecap="round" fill="none" opacity=".6"/>
          <!-- Barre grafico -->
          <rect x="14" y="38" width="6" height="12" rx="2" fill="url(#bar-grd)" opacity=".5"/>
          <rect x="22" y="30" width="6" height="20" rx="2" fill="url(#bar-grd)" opacity=".7"/>
          <rect x="30" y="22" width="6" height="28" rx="2" fill="url(#bar-grd)" opacity=".9"/>
          <rect x="38" y="26" width="6" height="24" rx="2" fill="url(#bar-grd)" opacity=".75"/>
          <!-- Punto picco -->
          <circle cx="33" cy="20" r="3" fill="#ff9f0a" opacity=".9"/>
          <circle cx="33" cy="20" r="5" fill="rgba(255,159,10,.2)"/>
          <!-- Lettera T -->
          <text x="32" y="58" text-anchor="middle" font-family="-apple-system,sans-serif"
            font-size="7" font-weight="800" fill="rgba(255,159,10,.5)" letter-spacing="1">TPI</text>
        </svg>
      </div>
    </div>
  </div>

  <div class="team-strip" id="team-strip"></div>

  <!-- ══════════════════════════════════
       TPI PRO SHOWCASE SECTION
  ══════════════════════════════════ -->
  <div id="tpi-pro-section">
    <div class="tpp-hdr">
      <!-- Pulsante in alto a destra, posizione assoluta -->
      <button class="tpp-collapse-btn" id="tpp-toggle" onclick="toggleTppSection()"
        style="position:absolute;top:24px;right:20px">&#9660; <span data-i18n="dash_show_pro">Mostra TPI Pro</span></button>
      <div class="tpp-badge" data-i18n="dash_pro_badge">&#x2728; Novit&agrave; &mdash; TPI Pro</div>
      <div class="tpp-ttl" data-i18n="dash_pro_ttl">TPI Pro: 6 Dimensioni di Analisi</div>
      <div class="tpp-sub" data-i18n-html="dash_pro_body">
        Il <strong>TPI classico</strong> usa 4 dimensioni offensiva (output, centralità, boost, consistenza).
        Il <strong>TPI Pro</strong> aggiunge <span style="color:var(--teal)">Età Index (AII)</span>
        e <span style="color:var(--purp)">Affidabilità Fisica (PRI)</span> —
        due indici indipendenti che cambiano la valutazione per scouting a lungo termine.
      </div>
    </div>
    <!-- Nascosto di default: formula, dims, cards -->
    <div id="tpp-collapsible" style="display:none">
      <div class="tpp-hdr-extra">
        <div class="tpp-formula">
          <span>TPI Pro</span>
          <span class="sep">=</span>
          <span style="color:var(--orng)">TPI classic</span>
          <span class="sep">+</span>
          <span style="color:var(--teal)">z(AII)</span>
          <span class="sep">+</span>
          <span style="color:var(--purp)">z(PRI)</span>
          <span class="sep">&mdash;</span>
          <span data-i18n="dash_pro_mean6">media 6 z-score</span>
        </div>
      </div>
      <div class="tpp-dims">
        <div class="tpp-dim tpp-dim-aii">&#x2B50; AII &mdash; Age Impact Index
          <span style="font-size:10px;font-weight:400;margin-left:4px;color:rgba(90,200,250,.6)" data-i18n="dash_pro_gauss">Gaussiana picco 27 anni</span></div>
        <div class="tpp-dim tpp-dim-pri">&#x1F4AA; PRI &mdash; Physical Reliability
          <span style="font-size:10px;font-weight:400;margin-left:4px;color:rgba(191,90,242,.6)" data-i18n="dash_pro_avail">Disponibilit&agrave; + infortuni + gravit&agrave;</span></div>
        <div class="tpp-dim tpp-dim-tpi">&#x1F3C6; TPI Classic
          <span style="font-size:10px;font-weight:400;margin-left:4px;color:rgba(255,159,10,.6)" data-i18n="dash_pro_dims">Output &middot; Centralit&agrave; &middot; Boost &middot; Consistenza</span></div>
      </div>
      <div class="tpp-body" id="tpp-body">
        <div id="tpp-content">
          <!-- Popolato da buildTpiProSection() -->
        </div>
      </div>
    </div>
  </div>

  <!-- Control bar -->
  <div class="ctrl-bar" id="ctrl-bar">
    <button class="mpill on" data-m="tpi"  onclick="selMetric(this)"><span>&#x1F3C6;</span> <span data-i18n="dash_chip_tpi">TPI</span></button>
    <button class="mpill" data-m="prospect" onclick="selMetric(this)"><span>&#x1F331;</span> <span data-i18n="dash_chip_prospect">Giovani &#x2605;</span></button>
    <button class="mpill" data-m="out"  onclick="selMetric(this)"><span>&#x26A1;</span> <span data-i18n="dash_chip_output">Output</span></button>
    <button class="mpill" data-m="cen"  onclick="selMetric(this)"><span>&#x1F3AF;</span> <span data-i18n="dash_chip_cen">Centralit&agrave;</span></button>
    <button class="mpill" data-m="boo"  onclick="selMetric(this)"><span>&#x1F4C8;</span> <span data-i18n="dash_chip_boo">Boost</span></button>
    <button class="mpill" data-m="con"  onclick="selMetric(this)"><span>&#x1F4CA;</span> <span data-i18n="dash_chip_con">Consistenza</span></button>
    <button class="mpill" data-m="conv" onclick="selMetric(this)"><span>&#x26BD;</span> <span data-i18n="dash_chip_conv">G/xG</span></button>
    <div class="ctrl-div"></div>
    <button class="rpill" data-r="ATT" onclick="selRole(this)" data-i18n="dash_role_fwd_s">ATT</button>
    <button class="rpill" data-r="CEN" onclick="selRole(this)" data-i18n="dash_role_mid_s">CEN</button>
    <button class="rpill" data-r="DIF" onclick="selRole(this)" data-i18n="dash_role_def_s">DIF</button>
    <div class="ctrl-div"></div>
    <button class="rpill" data-f="hot"  onclick="selForm(this)" title="Solo in forma">🔥 <span data-i18n="dash_filter_hot">Caldi</span></button>
    <button class="rpill" data-f="cold" onclick="selForm(this)" title="Solo in calo">🧊 <span data-i18n="dash_filter_cold">In calo</span></button>
    <div class="ctrl-div"></div>
    <div class="sq-wrap" id="sq-wrap">
      <button class="sq-btn" id="sq-btn" onclick="toggleSqFpk()">
        &#x1F6E1;&ensp;<span id="sq-lbl" data-i18n="dash_filter_team">Squadra</span>
        <span class="sq-chevron">&#9660;</span>
      </button>
    </div>
    <div class="sq-wrap" id="fpk-wrap">
      <button class="sq-btn" id="fpk-btn" onclick="toggleFpk()">
        &#x2315;&ensp;<span id="fpk-lbl" data-i18n="term_player">Giocatore</span>
        <span class="sq-chevron">&#9660;</span>
      </button>
    </div>
    <div class="ctrl-div"></div>
    <button class="cpill" onclick="selMetric(document.querySelector('.mpill[data-m=tpi]'));showCompare()">
      &#x1F504; <span data-i18n="dash_btn_compare">Confronta</span>
    </button>
  </div>

  <!-- Floating pickers -->
  <div class="fpk-box" id="fpk-box">
    <div class="fpk-search">
      <span class="fpk-search-ico">&#x2315;</span>
      <input class="fpk-search-inp" id="pi" type="text"
             placeholder="Nome, squadra o ruolo&hellip;" data-i18n-placeholder="dash_search_np"
             autocomplete="off" spellcheck="false">
    </div>
    <div class="fpk-list" id="pd"></div>
  </div>

  <div class="sq-fpk-box" id="sq-fpk-box">
    <div class="sq-panel-hd" style="position:relative;z-index:3" data-i18n="dash_filter_by_team">Filtra per squadra</div>
    <div id="sq-items" style="position:relative;z-index:3"></div>
    <div class="sq-panel-sep" style="position:relative;z-index:3"></div>
    <button class="sq-reset" style="position:relative;z-index:3" onclick="resetTeams()">
      <span style="font-size:14px;color:var(--red)">&#x2715;</span> <span data-i18n="dash_remove_filter">Rimuovi filtro</span>
    </button>
  </div>

  <!-- Leaderboard -->
  <div class="lb-wrap">
    <div class="lb-hdr">
      <span class="lb-ttl" id="lb-ttl">TPI Totale</span>
      <span class="help" id="lb-help" onclick="openM('TPI')" style="margin-left:4px">?</span>
      <span class="lb-sub" id="lb-sub"></span>
    </div>
    <div id="lb-chart"></div>
    <div id="lb-roster"></div>
    <div style="margin-top:10px;font-size:11px;color:var(--lt);display:flex;align-items:center;gap:12px;flex-wrap:wrap">
      <div>
        <span style="color:var(--orng)">&#x25A0;</span>&thinsp;ATT&ensp;
        <span style="color:var(--green)">&#x25A0;</span>&thinsp;CEN&ensp;
        <span style="color:var(--blue)">&#x25A0;</span>&thinsp;DIF
      </div>
      <div style="color:var(--lt)" data-i18n="dash_winter_legend">&#x2744; = acquisto invernale (soglia minuti ridotta)</div>
    </div>
  </div>
</div>

<!-- Metodologia collassabile (home) -->
<div id="meth-home">
  <div style="padding:14px 20px;display:flex;align-items:center;justify-content:space-between;cursor:pointer"
       onclick="toggleMeth()">
    <div style="display:flex;align-items:center;gap:8px">
      <span style="font-size:15px;font-weight:700;letter-spacing:-.3px" data-i18n="dash_meth_calc">&#x1F4D0; Metodologia e Calcoli</span>
      <span style="font-size:11px;color:var(--lt)" data-i18n="dash_meth_sub">Come funziona il TPI</span>
    </div>
    <span id="meth-chevron" style="font-size:12px;color:var(--lt);transition:transform .2s">&#9660;</span>
  </div>
  <div id="meth-home-body">
    <div id="meth-home-content"></div>
  </div>
</div>

<!-- Compare pool bar -->
<div class="cmp-pool-bar" id="cmp-pool-bar">
  <span class="cmp-pool-lbl">&#x2295; <span data-i18n="dash_compare">Confronta</span>:</span>
  <div class="cmp-pool-chips" id="cmp-chips"></div>
  <button class="cmp-pool-go" onclick="goCompare()">&#x21C4; <span data-i18n="dash_btn_compare">Confronta</span></button>
  <button class="cmp-pool-clr" onclick="clearPool()">&#x2715;</button>
</div>

<!-- ═══ PLAYER VIEW ═══ -->
<div id="view-player">
  <div class="hero hidden" id="hero">
    <div class="hero-av" id="h-av"></div>
    <div class="hero-inf">
      <div class="hero-nm" id="h-nm"></div>
      <div class="hero-sub" id="h-sub"></div>
      <div class="hero-form" id="h-form" style="font-size:11px;margin-top:3px"></div>
    </div>
    <div class="hero-tags" id="h-tags"></div>
    <div class="hero-tpi">
      <div class="hero-tpi-lbl">TPI</div>
      <div class="hero-tpi-val" id="h-tpi"></div>
    </div>
  </div>

  <div class="ctx-bar" id="ctx-bar"></div>
  <div class="tabs">
    <button class="tab-btn on" data-tab="ov"    onclick="swTab(this)" data-i18n="dash_overview">Panoramica</button>
    <button class="tab-btn"    data-tab="conv"  onclick="swTab(this)" data-i18n="dash_tab_conv">Goals vs xG</button>
    <button class="tab-btn"    data-tab="tr"    onclick="swTab(this)" data-i18n="dash_tab_trend">Trend xG</button>
    <button class="tab-btn"    data-tab="radar" onclick="swTab(this)" data-i18n="dash_tab_radar">Radar</button>
    <button class="tab-btn"    data-tab="cmp"   onclick="swTab(this)" data-i18n="dash_compare">Confronta</button>
    <button class="tab-btn"    data-tab="meth"  onclick="swTab(this)" data-i18n="dash_methodology">Metodologia</button>
  </div>

  <div id="p-ov"    class="panel on"></div>
  <div id="p-conv"  class="panel"></div>
  <div id="p-tr"    class="panel"></div>
  <div id="p-radar" class="panel"></div>

  <div id="p-cmp" class="panel">
    <div style="display:flex;gap:8px;padding:0 0 10px;flex-wrap:wrap;align-items:center">
      <span style="font-size:11px;color:var(--lt)" data-i18n="dash_filter_form">Filtra per forma:</span>
      <button class="rpill" data-cf="hot"  onclick="selCmpForm(this)">🔥 <span data-i18n="dash_filter_hot">Caldi</span></button>
      <button class="rpill" data-cf="cold" onclick="selCmpForm(this)">🧊 <span data-i18n="dash_filter_cold">In calo</span></button>
    </div>
    <div style="display:flex;gap:10px;padding:0 0 16px;flex-wrap:wrap">
      <select class="cmp-sel" id="cs1" onchange="drawCmp()"></select>
      <span style="color:var(--lt);font-size:13px;align-self:center;font-weight:500">vs</span>
      <select class="cmp-sel" id="cs2" onchange="drawCmp()"></select>
    </div>
    <div class="g2">
      <div class="card"><div class="card-ttl" data-i18n="dash_radar">Radar TPI</div><div id="cmp-radar" style="height:300px"></div></div>
      <div class="card"><div class="card-ttl" data-i18n="dash_zdim">Z-score dimensioni</div><div id="cmp-bars" style="height:300px"></div></div>
    </div>
    <div class="card" style="margin-top:12px">
      <div class="card-ttl" data-i18n="dash_ctx5">TPI nei 5 contesti</div><div id="cmp-ctx" style="height:220px"></div>
    </div>
    <div class="card" style="margin-top:12px">
      <div class="card-ttl" data-i18n="dash_goals_vs_xg_cmp">Goals vs xG a confronto</div><div id="cmp-conv" style="height:200px"></div>
    </div>
  </div>

  <div id="p-meth" class="panel">
    <div style="max-width:860px;margin:0 auto">
      <h2 style="font-size:22px;font-weight:700;letter-spacing:-.5px;margin-bottom:6px" data-i18n="dash_meth_title">Metodologia e Calcoli</h2>
      <p style="font-size:14px;color:var(--ls);line-height:1.6;max-width:640px;margin-bottom:20px" data-i18n="dash_meth_intro">
        Il TPI misura l&rsquo;impatto offensivo reale attraverso 4 dimensioni ortogonali.
        Bayesian shrinkage stabilizza le stime. SOS-weighting normalizza la difficoltà.</p>
      <div id="meth-content"></div>
    </div>
  </div>
</div>

<!-- ═══ JAVASCRIPT ═══ -->
<script>
/* ── Dati iniettati dal Python ── */
const DATA   = __DATA_JS__;
const RC     = __RC_JS__;
const RL     = __RL_JS__;
/* nome-ruolo localizzato: usa i18n se disponibile, fallback a RL (italiano) */
const _ROLE_KEY={POR:"dash_role_full_POR",DIF:"dash_role_full_DIF",CEN:"dash_role_full_CEN",ATT:"dash_role_full_ATT"};
function roleName(code){ return T(_ROLE_KEY[code], RL[code]||code); }
/* Badge forma recente (ultime N gare): 🔥 caldo / 🧊 freddo. Tooltip coi numeri. */
function formBadge(p){
  const r=p&&p.recent; if(!r||!r.label) return "";
  if(r.label==="stable") return "";
  const ico=r.label==="hot"?"🔥":"🧊";
  const tip=esc("Forma ultime "+(r.n||0)+" gare: "+(r.goal||0)+" gol, npxG "+(r.npxg||0)+", out/90 "+(r.out90||0)+" ("+Math.round((r.ratio||0)*100)+"% della stagione)");
  return ' <span title="'+tip+'" style="font-size:11px;cursor:help">'+ico+'</span>';
}
const CTX_L  = __CTX_L_JS__;
const SPIEG  = __SPIEG_JS__;
const TOP6   = __TOP6_JS__;
const FORTI  = __FORTI_JS__;
const TEAMS  = __TEAMS_JS__;
const ROSTER = __ROSTER_JS__;
const TPI_PRO_SHOWCASE = __TPI_PRO_JS__;
const CTXS  = Object.keys(CTX_L);
const NTOP  = __N_TOP_DIF__;
const NMIN  = 4;

/* ── Stato ── */
let CUR=null, CTX="totale", TAB="ov", PQ="", PR="";
let ACTIVE_TEAMS=new Set(), CUR_METRIC="tpi", VIEW="home", COMPARE_POOL=[];
let FORM_FILTER="";  /* "" tutti | "hot" solo caldi | "cold" solo in calo */

/* ── Plotly base ── */
const PL={responsive:true,displayModeBar:false};
const BL={paper_bgcolor:"transparent",plot_bgcolor:"transparent",
  font:{color:"rgba(235,235,245,.28)",family:"-apple-system,sans-serif"},
  xaxis:{gridcolor:"rgba(255,255,255,.05)",color:"rgba(235,235,245,.28)",tickfont:{size:10},zeroline:false},
  yaxis:{gridcolor:"rgba(255,255,255,.05)",color:"rgba(235,235,245,.28)",tickfont:{size:10},zeroline:false}};

/* ── Utils ── */
const fv=(v,d=2)=>(v==null)?"\u2014":(+v).toFixed(d);
const fvs=(v,d=2)=>(v==null)?"\u2014":(v>=0?"+":"")+v.toFixed(d);
const cz=v=>(v==null)?"var(--lt)":v>=0?"var(--green)":"var(--red)";
function rgb(h){try{const c=h.replace("#","");return parseInt(c.slice(0,2),16)+","+parseInt(c.slice(2,4),16)+","+parseInt(c.slice(4,6),16);}catch{return"10,132,255";}}
function hb(k){return'<span class="help" onclick="event.stopPropagation();openM(\x27'+k+'\x27)">?</span>';}

/* HTML-escape per dati esterni (V2 XSS hardening) — usa ovunque in innerHTML */
const _ESC_MAP={"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;","`":"&#96;"};
function esc(s){return String(s==null?"":s).replace(/[&<>"'`]/g,c=>_ESC_MAP[c]);}

/* i18n: T(key) — usa il dizionario condiviso se caricato, altrimenti torna il
   fallback italiano passato come 2° argomento (così nessun render mostra la key) */
function T(k,fb){ return (window.SerieAi18n ? window.SerieAi18n.t(k) : (fb!=null?fb:k)); }
const _CTX_KEYS={totale:"term_total",casa:"term_home",trasferta:"term_away",vs_top6:"term_vs_top6",vs_forti:"term_vs_strong"};
function CTXL(c){ return _CTX_KEYS[c] ? T(_CTX_KEYS[c], (CTX_L&&CTX_L[c])||c) : ((CTX_L&&CTX_L[c])||c); }

/* Restituisce il nome completo del giocatore (escapato) */
function dispNm(p){return esc(p&&p.nome?p.nome:"");}

/* ── Modal ── */
function openM(k){
  const s=SPIEG[k];if(!s)return;
  document.getElementById("m-t").textContent=s.titolo;
  document.getElementById("m-f").textContent=s.formula;
  document.getElementById("m-b").textContent=s.logica;
  document.getElementById("m-e").textContent="Esempio: "+s.esempio;
  document.getElementById("modal").classList.add("open");
}
function closeM(){
  const m=document.getElementById("modal");
  m.classList.remove("open");
  m.style.display="none";
}

/* ── Metodologia home ── */
function toggleMeth(){
  const body=document.getElementById("meth-home-body");
  const chev=document.getElementById("meth-chevron");
  const isOpen=body.style.display!=="none"&&body.style.display!=="";
  body.style.display=isOpen?"none":"block";
  if(chev)chev.style.transform=isOpen?"":"rotate(180deg)";
  if(!isOpen){
    document.getElementById("meth-home").scrollIntoView({behavior:"smooth",block:"start"});
    renderMethCards("meth-home-content");
  }
}
function renderMethCards(targetId){
  const el=document.getElementById(targetId);if(!el||el.innerHTML)return;
  const ibg={TPI:"rgba(255,159,10,.12)",output_adj:"rgba(10,132,255,.12)",
    centralita:"rgba(48,209,88,.12)",boost_ratio:"rgba(255,69,58,.12)",
    consistenza:"rgba(191,90,242,.12)",conv_ratio:"rgba(90,200,250,.12)"};
  const icons={TPI:"&#x1F3C6;",output_adj:"&#x26A1;",centralita:"&#x1F3AF;",
    boost_ratio:"&#x1F4C8;",consistenza:"&#x1F4CA;",conv_ratio:"&#x26BD;"};
  let h='<div class="meth-grid">';
  ["TPI","output_adj","centralita","boost_ratio","consistenza","conv_ratio"].forEach(k=>{
    const s=SPIEG[k];if(!s)return;
    h+='<div class="meth-card"><div class="meth-hd"><div class="meth-icon" style="background:'
      +(ibg[k]||"rgba(255,255,255,.07)")+'">'+(icons[k]||"?")+'</div>'
      +'<div class="meth-nm">'+s.titolo+'</div></div>'
      +'<div class="meth-formula">'+s.formula+'</div>'
      +'<div class="meth-logic">'+s.logica+'</div>'
      +'<div class="meth-ex">'+s.esempio+'</div></div>';
  });
  el.innerHTML=h+'</div>';
}

/* ── Squad filter ── */
(function initSqItems(){
  const el=document.getElementById("sq-items");
  el.innerHTML=TEAMS.map(function(t,i){
    var id="sqt-"+t.replace(/[\s']/g,"_");
    return'<div class="sq-row" id="'+id+'" onclick="toggleTeamIdx('+i+')">'
      +'<span class="sq-row-nm">'+t+'</span>'
      +'<span class="sq-chk">&#x2713;</span></div>';
  }).join("");
})();

function positionSqFpk(){
  const btn=document.getElementById("sq-btn"),box=document.getElementById("sq-fpk-box");
  if(!btn||!box)return;
  if(window.innerWidth<=768){
    box.style.left="8px";
    box.style.right="8px";
    box.style.width="calc(100vw - 16px)";
    box.style.top=(btn.getBoundingClientRect().bottom+6)+"px";
    box.style.maxHeight=(window.innerHeight*0.6)+"px";
  } else {
    const r=btn.getBoundingClientRect();
    box.style.top=(r.bottom+8)+"px";
    box.style.left=Math.max(8,Math.min(r.left,window.innerWidth-258))+"px";
    box.style.width="250px";
    box.style.maxHeight="380px";
  }
}
function toggleSqFpk(){
  const box=document.getElementById("sq-fpk-box"),btn=document.getElementById("sq-btn");
  const open=box.classList.toggle("open");
  btn.classList.toggle("open",open);
  document.body.style.overflow = open ? "hidden" : "";
  if(open)positionSqFpk();
}
function closeSqPanel(){
  document.getElementById("sq-fpk-box")?.classList.remove("open");
  document.getElementById("sq-btn")?.classList.remove("open");
  document.body.style.overflow = "";
}
function toggleSqFpk(){
  const box=document.getElementById("sq-fpk-box"),btn=document.getElementById("sq-btn");
  const open=box.classList.toggle("open");
  btn.classList.toggle("open",open);
  if(open)positionSqFpk();
}
function closeSqPanel(){
  document.getElementById("sq-fpk-box")?.classList.remove("open");
  document.getElementById("sq-btn")?.classList.remove("open");
}
function toggleTeamIdx(i){toggleTeam(TEAMS[i]);}
function toggleTeam(name){
  ACTIVE_TEAMS.has(name)?ACTIVE_TEAMS.delete(name):ACTIVE_TEAMS.add(name);
  applyTeamFilter();
}
function resetTeams(){ACTIVE_TEAMS.clear();closeSqPanel();applyTeamFilter();}
function applyTeamFilter(){
  TEAMS.forEach(t=>{
    document.getElementById("sqt-"+t.replace(/[\s']/g,"_"))?.classList.toggle("on",ACTIVE_TEAMS.has(t));
  });
  const n=ACTIVE_TEAMS.size;
  document.getElementById("sq-lbl").textContent=n===0?"Tutte":n===1?[...ACTIVE_TEAMS][0]:n+" squadre";
  document.getElementById("sq-btn").classList.toggle("active",n>0);
  buildDrop();
  if(VIEW==="home"){buildLeaderboard();buildTeamStrip();}
  updateHomeSub();
}
function getFiltered(){return ACTIVE_TEAMS.size===0?DATA:DATA.filter(p=>ACTIVE_TEAMS.has(p.squadra));}
function updateHomeSub(){
  const el=document.getElementById("home-sub");if(!el)return;
  const fd=getFiltered();
  if(ACTIVE_TEAMS.size===0)
    el.innerHTML='<b>'+fd.length+'</b> '+T("dash_players_ranked","players ranked")+' &middot; __N_GIOR__ '+T("dash_matchdays","matchdays")+' &middot; 6 '+T("dash_indep_kpis","independent KPIs")
      +' <span class="help" onclick="toggleMeth()" style="margin-left:4px" title="Methodology">?</span>';
  else
    el.innerHTML='<b style="color:var(--blue)">'+(ACTIVE_TEAMS.size===1?esc([...ACTIVE_TEAMS][0]):ACTIVE_TEAMS.size+" "+T("dash_teams_word","squadre"))
      +'</b> &mdash; <b>'+fd.length+'</b> '+T("dash_players_analyzed","giocatori analizzati");
}

function buildTeamStrip(){
  const strip=document.getElementById("team-strip");
  if(ACTIVE_TEAMS.size===0){strip.classList.remove("on");return;}
  strip.classList.add("on");
  strip.innerHTML=[...ACTIVE_TEAMS].map(team=>{
    const players=DATA.filter(p=>p.squadra===team);
    const s=[...players].sort((a,b)=>(b.tpi.totale||0)-(a.tpi.totale||0));
    const avgTpi=players.reduce((x,p)=>x+(p.tpi.totale||0),0)/(players.length||1);
    const avgOut=players.reduce((x,p)=>x+(p.ctx?.totale?.output_adj||0),0)/(players.length||1);
    const tc=avgTpi>=0.5?"var(--green)":avgTpi>=0?"var(--orng)":"var(--red)";
    const analyzedIds=new Set(players.map(p=>p.id));
    const extra=(ROSTER||[]).filter(r=>r.squadra===team&&!analyzedIds.has(r.id)&&r.ruolo!=="POR");
    const top=s.slice(0,3).map(p=>'<b>'+dispNm(p)+'</b> <span style="font-family:var(--mono);font-size:10px;color:var(--lt)">'+(p.tpi.totale!=null?(p.tpi.totale>=0?"+":"")+p.tpi.totale.toFixed(2):"\u2014")+'</span>').join(" &middot; ");
    return'<div class="ts-card"><div class="ts-nm">'+esc(team)+'</div>'
      +'<div class="ts-kpis"><div class="tsk"><div class="tsk-v" style="color:'+tc+'">'+avgTpi.toFixed(2)+'</div><div class="tsk-l">TPI medio</div></div>'
      +'<div class="tsk"><div class="tsk-v" style="color:var(--blue)">'+avgOut.toFixed(3)+'</div><div class="tsk-l">Output</div></div></div>'
      +'<div class="ts-top">'+top+(extra.length?' <span style="color:var(--lq)">+'+extra.length+' altri</span>':"")+'</div></div>';
  }).join("");
}

/* ════════════════════════════════════════════════════════════
   TPI PRO SHOWCASE
════════════════════════════════════════════════════════════ */
let _tppVisible = false;  /* default NASCOSTO */

function toggleTppSection(){
  const coll = document.getElementById("tpp-collapsible");
  const btn  = document.getElementById("tpp-toggle");
  _tppVisible = !_tppVisible;
  if(coll) coll.style.display = _tppVisible ? "block" : "none";
  if(btn){
    btn.innerHTML = _tppVisible
      ? "&#9650; " + esc(T("dash_hide_pro","Nascondi TPI Pro"))
      : "&#9660; " + esc(T("dash_show_pro","Mostra TPI Pro"));
    btn.style.background = _tppVisible
      ? "rgba(191,90,242,.15)"
      : "rgba(191,90,242,.08)";
  }
}

function buildTpiProSection(){
  const el = document.getElementById("tpp-content");
  if(!el) return;

  if(!TPI_PRO_SHOWCASE || TPI_PRO_SHOWCASE.length === 0){
    el.innerHTML = '<div class="tpp-empty">'
      +'<strong>Dati TPI Pro non disponibili</strong><br>'
      +'Popola <code style="font-family:var(--mono);color:var(--purp)">t_infortuni</code> '
      +'e assicurati che <code style="font-family:var(--mono);color:var(--teal)">data_nascita</code> '
      +'sia presente in <code style="font-family:var(--mono)">giocatori</code>, '
      +'poi riesegui <code style="font-family:var(--mono)">parte1_analisi.py</code>.</div>';
    return;
  }

  const roles = ["ATT","CEN","DIF"];
  const roleLabel = {"ATT":"Attaccanti","CEN":"Centrocampisti","DIF":"Difensori"};
  const roleLabelKey = {"ATT":"dash_role_fwd_pl","CEN":"dash_role_mid_pl","DIF":"dash_role_def_pl"};
  const groups = {};
  roles.forEach(r => { groups[r] = TPI_PRO_SHOWCASE.filter(p => p.ruolo === r); });

  const zToBar = v => (v == null) ? 50 : Math.max(0, Math.min(100, 50 + (Math.max(-3, Math.min(3, v)) / 3) * 50));
  const barLeft  = v => { const b = zToBar(v); return b < 50 ? b+"%" : "50%"; };
  const barWidth = v => { const b = zToBar(v); return Math.abs(b - 50)+"%"; };
  const fvs = v => (v == null) ? "\u2014" : (v >= 0 ? "+" : "") + v.toFixed(2);
  const ratioColor = v => v >= 0.75 ? "var(--green)" : v >= 0.50 ? "var(--orng)" : "var(--red)";

  let html = '<div class="tpp-cols">';

  roles.forEach(role => {
    const rc = RC[role] || "#636366";
    const players = groups[role] || [];
    html += '<div>';
    html += '<div class="tpp-col-hdr">'
      +'<div class="dot" style="background:'+rc+'"></div>'
      +esc(T(roleLabelKey[role], roleLabel[role]))
      +' <span style="font-size:10px;color:var(--lt);font-weight:400">top 2 TPI Pro</span>'
      +'</div>';

    if(!players.length){
      html += '<div style="font-size:12px;color:var(--lt);padding:12px 0">'+esc(T("dash_no_role_data","Nessun dato per questo ruolo."))+'</div>';
    } else {
      players.forEach(p => {
        /* Delta rank badge */
        let deltaBadge = "";
        if(p.delta_rank != null){
          const cls = p.delta_rank > 0 ? "up" : p.delta_rank < 0 ? "dn" : "eq";
          const arrow = p.delta_rank > 0 ? "\u25B2" : p.delta_rank < 0 ? "\u25BC" : "\u25CF";
          const label = p.delta_rank > 0 ? "+"+p.delta_rank+" pos" : p.delta_rank < 0 ? p.delta_rank+" pos" : "=";
          deltaBadge = '<div class="tpp-delta '+cls+'">'+arrow+" "+label+'</div>';
        }

        /* Barre doppia TPI vs TPI Pro */
        const tpiBar =
          '<div class="tpp-bar-row">'
          +'<span class="tpp-bar-lbl">TPI</span>'
          +'<div class="tpp-bar-track"><div class="tpp-bar-zero"></div>'
          +'<div class="tpp-bar-fill" style="left:'+barLeft(p.tpi)+';width:'+barWidth(p.tpi)+';background:rgba(255,159,10,.65)"></div></div>'
          +'<span class="tpp-bar-val" style="color:var(--orng)">'+fvs(p.tpi)+'</span>'
          +'</div>';
        const tpiProBar =
          '<div class="tpp-bar-row">'
          +'<span class="tpp-bar-lbl pro">PRO</span>'
          +'<div class="tpp-bar-track"><div class="tpp-bar-zero"></div>'
          +'<div class="tpp-bar-fill" style="left:'+barLeft(p.tpi_pro)+';width:'+barWidth(p.tpi_pro)+';background:var(--purp)"></div></div>'
          +'<span class="tpp-bar-val" style="color:var(--purp)">'+fvs(p.tpi_pro)+'</span>'
          +'</div>';

        const aiiVal   = p.aii != null ? p.aii.toFixed(2) : "\u2014";
        const priVal   = p.pri != null ? p.pri.toFixed(2) : "\u2014";
        const aiiPct   = p.aii != null ? Math.round(p.aii * 100) : 0;
        const priPct   = p.pri != null ? Math.round(p.pri * 100) : 0;
        const etaTxt   = p.eta != null ? p.eta + " anni" : "";
        const aiiColor = p.aii != null ? ratioColor(p.aii) : "var(--lt)";
        const priColor = p.pri != null ? ratioColor(p.pri) : "var(--lt)";
        const rk       = p.rank_tpi_pro != null ? "#"+p.rank_tpi_pro : "\u2014";

        html += '<div class="tpp-card" onclick="if(DATA.find(x=>x.id==='+p.id+'))pick('+p.id+')">'
          +deltaBadge
          +'<div class="tpp-card-nm">'+esc(p.nome)+'</div>'
          +'<div class="tpp-card-sub">'+esc(p.squadra)
          +(etaTxt ? ' &middot; '+etaTxt : '')
          +' &middot; <span style="font-family:var(--mono);font-size:10px;color:var(--purp)">'+rk+' TPI Pro</span>'
          +'</div>'
          +'<div class="tpp-bars">'+tpiBar+tpiProBar+'</div>'
          +'<div class="tpp-kpis">'
          +  '<div class="tpp-kpi">'
          +    '<div class="tpp-kpi-lbl" style="color:var(--teal)">&#x2B50; AII &mdash; Et&agrave;</div>'
          +    '<div class="tpp-kpi-val" style="color:'+aiiColor+'">'+aiiVal+'</div>'
          +    '<div class="tpp-kpi-bar"><div class="tpp-kpi-bar-f" style="width:'+aiiPct+'%;background:var(--teal)60"></div></div>'
          +  '</div>'
          +  '<div class="tpp-kpi">'
          +    '<div class="tpp-kpi-lbl" style="color:var(--purp)">&#x1F4AA; PRI &mdash; Fisico</div>'
          +    '<div class="tpp-kpi-val" style="color:'+priColor+'">'+priVal+'</div>'
          +    '<div class="tpp-kpi-bar"><div class="tpp-kpi-bar-f" style="width:'+priPct+'%;background:var(--purp)60"></div></div>'
          +  '</div>'
          +'</div>'
          +'</div>';
      });
    }
    html += '</div>';
  });

  html += '</div>';

  /* Legenda / nota metodologica */
  html += '<div style="margin-top:14px;padding:10px 14px;background:rgba(255,255,255,.02);'
    +'border:1px solid var(--sep);border-left:3px solid var(--purp);'
    +'border-radius:0 var(--rsm) var(--rsm) 0;font-size:12px;color:var(--lt);line-height:1.7">'
    +'<strong style="color:var(--lp)">Come leggere:</strong> '
    +'<span style="color:var(--green)">\u25B2 +N pos</span> = sale in classifica con TPI Pro rispetto al TPI classico. '
    +'<span style="color:var(--red)">\u25BC</span> = scende. '
    +'Le barre mostrano z-score (centro = media lega). '
    +'<strong style="color:var(--teal)">AII</strong> = qualit\u00e0 del ciclo anagrafico (0\u20131). '
    +'<strong style="color:var(--purp)">PRI</strong> = affidabilit\u00e0 fisica storica (0\u20131). '
    +'Clicca su un giocatore per aprire il profilo completo.'
    +'</div>';

  el.innerHTML = html;
}

/* ── Leaderboard ── */
const METRICS_CFG={
  tpi:      {ttl:"TPI Totale", ttlKey:"dash_m_tpi", help:"TPI",         get:p=>p.tpi.totale,                    fmt:v=>(v>=0?"+":"")+v.toFixed(2)},
  prospect: {
    ttl:"Giovani \u2605 — Prospect Score", ttlKey:"dash_m_prospect",
    help:"TPI",
    get:p=>{
      /* Prospect Score = TPI × AII  (solo giocatori ≤ 24 anni)
         eta e eta_index sono in p.physical */
      if(!p.tpi.totale) return null;
      const eta = p.physical?.eta ?? null;
      if(eta === null || eta > 24) return null;
      const aii = p.physical?.eta_index ?? null;
      let ageFactor;
      if(aii != null){
        ageFactor = aii;
      } else {
        /* approssimazione gaussiana picco 27, sigma 4.5 */
        ageFactor = Math.exp(-0.5 * Math.pow((eta - 27) / 4.5, 2));
      }
      return p.tpi.totale * ageFactor;
    },
    fmt:v=>(v>=0?"+":"")+v.toFixed(2),
    note:() => T("dash_prospect_note","Solo giocatori \u226424 anni. Score = TPI \u00d7 AII (Age Impact Index). Premia chi ha alto impatto gi\u00e0 in giovane et\u00e0."),
    rowExtra: p => {
      const eta = p.physical?.eta ?? null;
      const aii = p.physical?.eta_index ?? null;
      const aiiTxt = aii != null ? aii.toFixed(2) : "\u2014";
      const etaColor = eta <= 20 ? "var(--teal)" : eta <= 22 ? "var(--green)" : "var(--orng)";
      return eta != null
        ? '<span style="font-size:10px;font-family:var(--mono);color:'+etaColor+';background:rgba(255,255,255,.05);border:1px solid rgba(255,255,255,.1);border-radius:5px;padding:1px 6px;margin-left:4px">'+eta+'a</span>'
          +'<span style="font-size:10px;font-family:var(--mono);color:rgba(90,200,250,.7);background:rgba(90,200,250,.06);border:1px solid rgba(90,200,250,.15);border-radius:5px;padding:1px 6px;margin-left:3px">AII '+aiiTxt+'</span>'
        : '';
    },
  },
  out:  {ttl:"Output Offensivo Adj / 90'", ttlKey:"dash_m_out", help:"output_adj", get:p=>p.ctx?.totale?.output_adj,    fmt:v=>v.toFixed(3)},
  cen:  {ttl:"Centralit\u00e0 Offensiva", ttlKey:"dash_m_cen",   help:"centralita",  get:p=>p.ctx?.totale?.centralita,     fmt:v=>v.toFixed(1)+"%"},
  boo:  {ttl:"Team Boost Ratio", ttlKey:"dash_m_boo",          help:"boost_ratio", get:p=>p.ctx?.totale?.boost_ratio,    fmt:v=>v.toFixed(2)+"\u00d7"},
  con:  {ttl:"Consistenza", ttlKey:"dash_m_con",               help:"consistenza", get:p=>p.ctx?.totale?.consistenza,    fmt:v=>v.toFixed(3)},
  conv: {ttl:"G / xG \u2014 Conversion", ttlKey:"dash_m_conv",  help:"conv_ratio",  get:p=>p.conv?.conv_ratio,            fmt:v=>v.toFixed(2)},
};
function selMetric(el){
  document.querySelectorAll(".mpill").forEach(b=>b.classList.remove("on"));
  el.classList.add("on");CUR_METRIC=el.dataset.m;buildLeaderboard();
}
function buildLeaderboard(){
  const m=METRICS_CFG[CUR_METRIC];
  const fd=getFiltered().filter(p=>p.ruolo!=="POR"&&(!PR||p.ruolo===PR)&&(!FORM_FILTER||(p.recent&&p.recent.label===FORM_FILTER)));
  document.getElementById("lb-ttl").textContent=T(m.ttlKey, m.ttl);
  document.getElementById("lb-help").onclick=()=>openM(m.help);
  const teamSub=ACTIVE_TEAMS.size>0?" — "+[...ACTIVE_TEAMS].join(", "):"";
  const roleSub=PR?" · "+T("dash_only","Solo")+" "+T(_ROLE_KEY[PR],PR):"";
  document.getElementById("lb-sub").textContent=teamSub+roleSub;

  /* Nota metodologica sotto titolo per prospect */
  let noteEl = document.getElementById("lb-note");
  if(!noteEl){
    noteEl = document.createElement("div");
    noteEl.id = "lb-note";
    noteEl.style.cssText = "font-size:12px;color:var(--lt);padding:4px 0 10px;line-height:1.55;display:none";
    const hdr = document.getElementById("lb-ttl")?.parentElement;
    if(hdr) hdr.after(noteEl);
  }
  const _noteVal = (typeof m.note === "function") ? m.note() : m.note;
  if(_noteVal){ noteEl.textContent = _noteVal; noteEl.style.display = "block"; }
  else { noteEl.style.display = "none"; }

  const sorted=fd.map(p=>({p,v:m.get(p)})).filter(x=>x.v!=null&&isFinite(x.v)).sort((a,b)=>b.v-a.v);
  const el=document.getElementById("lb-chart");
  const elR=document.getElementById("lb-roster");
  if(!sorted.length){
    el.innerHTML='<div style="color:var(--lt);padding:40px 0;text-align:center;font-size:13px">'+esc(T("dash_no_filter_data","Nessun dato disponibile per questo filtro"))+'</div>';
    if(elR)elR.innerHTML="";return;
  }
  const maxV=sorted[0].v||1;
  el.innerHTML='<div class="lb-list">'+sorted.map(({p,v},i)=>{
    const rc=RC[p.ruolo]||"#636366";
    const inPool=COMPARE_POOL.includes(p.id);
    const wb=p.is_winter?' <span title="Acquisto invernale — soglia minuti ridotta" style="font-size:11px">&#x2744;</span>':"";
    const barW=Math.max(2,Math.round(v/maxV*100));
    /* dispNm() → cognome (o "Iniz. Cognome" se duplicato) */
    const dn=dispNm(p);
    const extra = m.rowExtra ? m.rowExtra(p) : "";
    return'<div class="lb-row">'
      +'<span class="lb-rank">'+(i+1)+'</span>'
      +'<div class="lb-dot" style="background:'+rc+'"></div>'
      +'<div class="lb-info">'
        +'<div class="lb-nm" title="'+esc(p.nome)+'">'+dn+wb+formBadge(p)+extra+'</div>'
        +'<div class="lb-team">'+esc(p.squadra)+' &middot; '+esc(roleName(p.ruolo))+'</div>'
      +'</div>'
      +'<div class="lb-bar-wrap"><div class="lb-bar-fill" style="width:'+barW+'%;background:'+rc+'80"></div></div>'
      +'<span class="lb-val" style="color:'+rc+'">'+m.fmt(v)+'</span>'
      +'<div class="lb-actions">'
        +'<button class="lb-btn lb-btn-prof" onclick="pick('+p.id+')">&#x2192; '+esc(T("dash_btn_profile","Profilo"))+'</button>'
        +'<button class="lb-btn lb-btn-cmp" id="cmpbtn-'+p.id+'" onclick="showDiff('+p.id+')">&#x2696; '+esc(T("dash_btn_diff","Scarto"))+'</button>'
      +'</div></div>';
  }).join("")+'</div>';

  // Roster non analizzati
  if(elR&&ACTIVE_TEAMS.size>0&&ROSTER&&ROSTER.length){
    const analyzedIds=new Set(sorted.map(x=>x.p.id));
    const unanalyzed=ROSTER.filter(r=>!analyzedIds.has(r.id)&&r.ruolo!=="POR"&&(!PR||r.ruolo===PR)&&ACTIVE_TEAMS.has(r.squadra));
    if(unanalyzed.length){
      elR.innerHTML='<div class="roster-section">'
        +'<div class="roster-hdr">&#x1F465; '+esc(T("dash_roster","Resto della rosa"))+' '
        +'<span style="font-size:11px;color:var(--lt);font-weight:400">'+unanalyzed.length+' '+esc(T("dash_roster_note","giocatori (minuti insufficienti per TPI)"))+'</span></div>'
        +'<div class="roster-grid">'+unanalyzed.map(function(r){
          var rc2=RC[r.ruolo]||"#636366";
          var dn2=esc(r.nome);
          return'<div class="roster-card"><div class="lb-dot" style="background:'+rc2+'"></div>'
            +'<div style="flex:1;min-width:0"><div class="roster-nm" title="'+esc(r.nome)+'">'+dn2+'</div>'
            +'<div style="font-size:10px;color:var(--lt)">'+esc(r.squadra)+' &middot; '+esc(roleName(r.ruolo))+'</div></div>'
            +'<span class="roster-min">'+(+r.minuti>0?(+r.minuti)+"min":"—")+'</span></div>';
        }).join('')+'</div></div>';
    }else elR.innerHTML="";
  }else if(elR)elR.innerHTML="";
}

/* ── Compare pool ── */
function togglePool(id){
  const idx=COMPARE_POOL.indexOf(id);
  if(idx>=0)COMPARE_POOL.splice(idx,1);
  else{if(COMPARE_POOL.length>=4){alert(T("dash_max_compare","Massimo 4 giocatori."));return;}COMPARE_POOL.push(id);}
  updatePoolBar();
  const btn=document.getElementById("cmpbtn-"+id);
  if(btn){const inP=COMPARE_POOL.includes(id);btn.className="lb-btn lb-btn-cmp"+(inP?" in-pool":"");btn.textContent=inP?"\u2714 Confronto":"\u2295 Confronto";}
}
function updatePoolBar(){
  const bar=document.getElementById("cmp-pool-bar"),chips=document.getElementById("cmp-chips");
  if(!bar||!chips)return;
  if(COMPARE_POOL.length<2){bar.classList.remove("on");return;}
  bar.classList.add("on");
  chips.innerHTML=COMPARE_POOL.map(id=>{const p=DATA.find(x=>x.id===id);if(!p)return"";return'<div class="cmp-chip" onclick="togglePool('+id+')">'+dispNm(p)+'<span class="cmp-chip-x">&#x2715;</span></div>';}).join("");
}
function clearPool(){COMPARE_POOL=[];updatePoolBar();buildLeaderboard();}
function goCompare(){
  if(COMPARE_POOL.length<2)return;
  const s1=document.getElementById("cs1"),s2=document.getElementById("cs2");
  if(s1)s1.value=COMPARE_POOL[0];if(s2)s2.value=COMPARE_POOL[1];
  pick(COMPARE_POOL[0]);
  setTimeout(()=>{document.querySelectorAll(".tab-btn").forEach(b=>b.classList.toggle("on",b.dataset.tab==="cmp"));document.querySelectorAll(".panel").forEach(p=>p.classList.remove("on"));document.getElementById("p-cmp").classList.add("on");TAB="cmp";drawCmp();},120);
}

/* ── View switching ── */
/* ── Navigation History ── */
let NAV_HISTORY=[];
let NAV_FUTURE=[];

function navPush(state){
  NAV_HISTORY.push(state);
  NAV_FUTURE=[];
  _updateNavButtons();
}

function _updateNavButtons(){
  const bar=document.getElementById("back-bar");
  const lbl=document.getElementById("back-label");
  const btnB=document.getElementById("nav-back-btn");
  const btnF=document.getElementById("nav-fwd-btn");
  const hasBack=NAV_HISTORY.length>0;
  const hasFwd=NAV_FUTURE.length>0;
  if(btnB){btnB.disabled=!hasBack;}
  if(btnF){btnF.disabled=!hasFwd;}
  if(!bar||!lbl)return;
  if(hasBack){
    bar.classList.add("on");
    lbl.textContent=NAV_HISTORY[NAV_HISTORY.length-1].label||"Indietro";
  } else {
    bar.classList.remove("on");
    lbl.textContent="Classifiche";
  }
}

function _restoreState(state){
  _updateNavButtons();
  if(state.view==="home"){
    VIEW="home";
    document.getElementById("view-home").style.display="block";
    document.getElementById("view-player").style.display="none";
    document.getElementById("back-bar").classList.remove("on");
    if(state.metric){CUR_METRIC=state.metric;document.querySelectorAll(".mpill").forEach(b=>b.classList.toggle("on",b.dataset.m===state.metric));}
    if(state.role!==undefined){PR=state.role;document.querySelectorAll(".rpill").forEach(b=>b.classList.toggle("on",b.dataset.r===state.role));}
    if(state.teams){ACTIVE_TEAMS=new Set(state.teams);applyTeamFilter();}
    setTimeout(()=>{buildLeaderboard();buildTeamStrip();},80);
  } else if(state.view==="player"&&state.playerId){
    const p=DATA.find(x=>x.id===state.playerId);
    if(!p){showHome();return;}
    VIEW="player";
    document.getElementById("view-home").style.display="none";
    document.getElementById("view-player").style.display="block";
    CUR=p;CTX=state.ctx||"totale";TAB=state.tab||"ov";
    document.getElementById("back-cur").textContent=p.nome+" — "+p.squadra;
    document.querySelectorAll(".ctx-btn").forEach(b=>b.classList.toggle("on",b.dataset.ctx===CTX));
    document.querySelectorAll(".tab-btn").forEach(b=>b.classList.toggle("on",b.dataset.tab===TAB));
    document.querySelectorAll(".panel").forEach(pn=>pn.classList.toggle("on",pn.id==="p-"+TAB));
    updateHero(p);updateCtxBar(p);renderAll(p);
  }
}

function histBack(){
  if(NAV_HISTORY.length===0){showHome();return;}
  // Save current state to future
  const cur=_captureCurrentState();
  NAV_FUTURE.push(cur);
  const prev=NAV_HISTORY.pop();
  _restoreState(prev);
}

function histForward(){
  if(NAV_FUTURE.length===0)return;
  const cur=_captureCurrentState();
  NAV_HISTORY.push(cur);
  const next=NAV_FUTURE.pop();
  _restoreState(next);
}

function _captureCurrentState(){
  if(VIEW==="home"){
    return {view:"home",label:"Classifiche",metric:CUR_METRIC,role:PR,teams:[...ACTIVE_TEAMS]};
  } else {
    return {view:"player",label:CUR?CUR.nome:"",playerId:CUR?CUR.id:null,ctx:CTX,tab:TAB};
  }
}

function showHome(){
  VIEW="home";NAV_HISTORY=[];NAV_FUTURE=[];
  document.getElementById("view-home").style.display="block";
  document.getElementById("view-player").style.display="none";
  document.getElementById("back-bar").classList.remove("on");
  _updateNavButtons();
  setTimeout(()=>{buildLeaderboard();buildTeamStrip();},80);
}
function showPlayer(){
  VIEW="player";
  document.getElementById("view-home").style.display="none";
  document.getElementById("view-player").style.display="block";
  _updateNavButtons();
}

/* ── Player picker ── */
const pi=document.getElementById("pi"),pd=document.getElementById("pd");
function positionFpk(){
  const btn=document.getElementById("fpk-btn"),box=document.getElementById("fpk-box");
  if(!btn||!box)return;
  if(window.innerWidth<=768){
    box.style.left="8px";
    box.style.right="8px";
    box.style.width="calc(100vw - 16px)";
    box.style.top=(btn.getBoundingClientRect().bottom+6)+"px";
    box.style.maxHeight=(window.innerHeight*0.65)+"px";
  } else {
    const r=btn.getBoundingClientRect();
    box.style.top=(r.bottom+8)+"px";
    box.style.left=Math.max(8,r.right-300)+"px";
    box.style.width="300px";
    box.style.maxHeight="420px";
  }
}
function toggleFpk(){
  const box=document.getElementById("fpk-box"),btn=document.getElementById("fpk-btn");
  const open=box.classList.toggle("open");
  btn.classList.toggle("open",open);
  document.body.style.overflow = open ? "hidden" : "";
  if(open){positionFpk();setTimeout(()=>pi.focus(),50);}
}
function closeFpk(){
  document.getElementById("fpk-box")?.classList.remove("open");
  document.getElementById("fpk-btn")?.classList.remove("open");
  document.body.style.overflow = "";
}
document.addEventListener("click",e=>{
  if(!document.getElementById("fpk-wrap")?.contains(e.target)&&!document.getElementById("fpk-box")?.contains(e.target))closeFpk();
  if(!document.getElementById("sq-wrap")?.contains(e.target)&&!document.getElementById("sq-fpk-box")?.contains(e.target))closeSqPanel();
});
window.addEventListener("resize",()=>{
  if(document.getElementById("fpk-box")?.classList.contains("open"))positionFpk();
  if(document.getElementById("sq-fpk-box")?.classList.contains("open"))positionSqFpk();
});
pi.addEventListener("input",()=>{PQ=pi.value.toLowerCase();buildDrop();});
function selRole(el){const wasOn=el.classList.contains("on");document.querySelectorAll(".rpill[data-r]").forEach(b=>b.classList.remove("on"));if(!wasOn){el.classList.add("on");PR=el.dataset.r;}else PR="";buildDrop();buildLeaderboard();}
function selForm(el){const wasOn=el.classList.contains("on");document.querySelectorAll(".rpill[data-f]").forEach(b=>b.classList.remove("on"));if(!wasOn){el.classList.add("on");FORM_FILTER=el.dataset.f;}else FORM_FILTER="";buildDrop();buildLeaderboard();}

function buildDrop(){
  const base=ACTIVE_TEAMS.size>0?DATA.filter(p=>ACTIVE_TEAMS.has(p.squadra)):DATA;
  /* Ricerca su nome completo, squadra, ruolo */
  const analyzed=base.filter(p=>(!PR||p.ruolo===PR)&&(!PQ||
    p.nome.toLowerCase().includes(PQ)||
    p.squadra.toLowerCase().includes(PQ)));
  let rosterExtra=[];
  if((ACTIVE_TEAMS.size>0||PQ)&&ROSTER&&ROSTER.length){
    const aIds=new Set(analyzed.map(p=>p.id));
    rosterExtra=ROSTER.filter(r=>!aIds.has(r.id)&&r.ruolo!=="POR"&&(!PR||r.ruolo===PR)
      &&(ACTIVE_TEAMS.size===0||ACTIVE_TEAMS.has(r.squadra))
      &&(!PQ||r.nome.toLowerCase().includes(PQ)||r.squadra.toLowerCase().includes(PQ)));
  }
  if(!analyzed.length&&!rosterExtra.length){pd.innerHTML='<div class="fpk-empty">'+esc(T("msg_no_results","Nessun risultato"))+'</div>';return;}
  let html=analyzed.map(p=>{
    const rc=RC[p.ruolo]||"#636366",t=p.tpi.totale;
    const ts=(t==null)?"—":(t>=0?"+":"")+t.toFixed(2);
    const tc=(t==null)?"":t>=0?"pos":"neg";
    const ft=p.form.trend,ar=ft>.10?"&#9650;":ft<-.10?"&#9660;":"&#8594;";
    const ac=ft>.10?"var(--green)":ft<-.10?"var(--red)":"var(--lt)";
    const cr=p.conv?.conv_ratio;
    const wb=p.is_winter?' <span title="Acquisto invernale" style="font-size:11px">&#x2744;</span>':"";
    const dn=dispNm(p);
    return'<div class="fpk-item'+(CUR&&CUR.id===p.id?" sel":"")+'" onclick="pick('+p.id+')">'
      +'<div class="fpk-dot" style="background:'+rc+'"></div>'
      +'<div class="fpk-bd">'
        +'<div class="fpk-nm" title="'+esc(p.nome)+'">'+dn+wb+'</div>'
        +'<div class="fpk-sub">'+esc(p.squadra)+' &middot; '+esc(roleName(p.ruolo))+(cr?' &middot; G/xG '+cr.toFixed(2):'')+'</div>'
      +'</div>'
      +'<div class="fpk-rt"><div class="fpk-tpi '+tc+'">'+ts+'</div><span style="color:'+ac+'">'+ar+'</span></div></div>';
  }).join("");
  if(rosterExtra.length)html+='<div class="fpk-sep"></div><div class="fpk-role-grp">'+esc(T("dash_not_analyzed","Non analizzati (minuti insufficienti)"))+'</div>'
    +rosterExtra.map(r=>{
      var dn2=r.nome;
      return'<div class="fpk-item" style="opacity:.5"><div class="fpk-dot" style="background:'+(RC[r.ruolo]||"#636366")+'"></div>'
        +'<div class="fpk-bd"><div class="fpk-nm" title="'+r.nome+'">'+dn2+'</div>'
        +'<div class="fpk-sub">'+r.squadra+' &middot; '+(RL[r.ruolo]||r.ruolo)+' &middot; '+r.minuti+'\' min</div></div>'
        +'<div class="fpk-rt" style="font-size:10px;color:var(--lt)">n/a</div></div>';
    }).join("");
  pd.innerHTML=html;
}

function pick(id){
  const p=DATA.find(x=>x.id==id);if(!p)return;
  // Salva stato corrente prima di navigare
  if(VIEW==="home"){
    navPush({view:"home",label:"Classifiche",metric:CUR_METRIC,role:PR,teams:[...ACTIVE_TEAMS]});
  } else if(VIEW==="player"&&CUR){
    navPush({view:"player",label:CUR.nome,playerId:CUR.id,ctx:CTX,tab:TAB});
  }
  CUR=p;pi.value="";PQ="";closeFpk();closeSqPanel();showPlayer();
  document.getElementById("back-cur").textContent=p.nome+" — "+p.squadra;
  document.querySelectorAll(".tab-btn").forEach((b,i)=>b.classList.toggle("on",i===0));
  document.querySelectorAll(".panel").forEach((pn,i)=>pn.classList.toggle("on",i===0));
  TAB="ov";updateHero(p);updateCtxBar(p);renderAll(p);
}

/* ── Hero ── */
function updateHero(p){
  document.getElementById("hero").classList.remove("hidden");
  const rc=RC[p.ruolo]||"#636366",rb=rgb(rc);
  const av=document.getElementById("h-av");
  av.style.background="rgba("+rb+",.14)";
  av.textContent=p.ruolo==="ATT"?"⚽":p.ruolo==="DIF"?"🛡":p.ruolo==="CEN"?"🔄":"🥊";
  const wb=p.is_winter?' <span class="tag tag-snow" title="Acquisto invernale: soglia minuti ridotta ('+p.first_giornata+'ª gg)">❄️ dal gg '+p.first_giornata+'</span>':"";
  document.getElementById("h-nm").innerHTML=esc(p.nome)+wb;
  document.getElementById("h-sub").innerHTML=esc(p.squadra)+" · "+(+p.minuti||0)+"' · SOS "+fv(p.kpi.sos);
  // ── Forma recente (ultime N gare) ──
  const hf=document.getElementById("h-form"), r=p.recent||{};
  if(hf){
    if(r.n>=3){
      const col=r.label==="hot"?"var(--green)":r.label==="cold"?"var(--red)":"var(--lt)";
      const ico=r.label==="hot"?"🔥 ":r.label==="cold"?"🧊 ":"";
      const lbl=r.label==="hot"?T("dash_form_hot","in forma"):r.label==="cold"?T("dash_form_cold","in calo"):T("dash_form_stable","stabile");
      hf.innerHTML='<span style="color:'+col+';font-weight:600">'+ico+T("dash_form","Forma")+' '+lbl+'</span>'
        +'<span style="color:var(--lt)"> · ultime '+r.n+': '+(r.goal||0)+' '+T("dash_goals_short","gol")
        +' · '+(r.npxg||0)+' npxG · out/90 '+(r.out90!=null?r.out90:"—")
        +' ('+Math.round((r.ratio||0)*100)+'% '+T("dash_vs_season","vs stagione")+')</span>';
    } else { hf.innerHTML=''; }
  }
  const rk=p.rank||{},ft=p.form.trend,tpi=p.tpi.totale;
  const tv=document.getElementById("h-tpi");
  tv.textContent=(tpi!=null)?(tpi>=0?"+":"")+tpi.toFixed(2):"—";
  tv.style.color=(tpi==null)?"var(--lt)":tpi>=0?"var(--orng)":"var(--red)";
  const fc=ft>.10?"tag-up":ft<-.10?"tag-dn":"tag-flat";
  const fa=ft>.10?"▲":ft<-.10?"▼":"→";
  document.getElementById("h-tags").innerHTML=
    '<span class="tag tag-role" style="background:'+rc+'88;border-color:'+rc+'55">'+esc(roleName(p.ruolo))+'</span>'
    +(rk.TPI?'<span class="tag tag-rank">#'+rk.TPI+' / '+rk.n_total+'</span>':"")
    +'<span class="tag '+fc+'">Form '+fa+' '+(ft!=null?(ft>=0?"+":"")+((ft*100).toFixed(0))+"%":"—")+'</span>';
}

/* ── Ctx bar & Tabs ── */
(function initCtxBar(){
  const bar=document.getElementById("ctx-bar");
  CTXS.forEach(ctx=>{
    const b=document.createElement("button");
    b.className="ctx-btn"+(ctx==="totale"?" on":"");
    b.dataset.ctx=ctx;b.onclick=()=>swCtx(ctx);bar.appendChild(b);
  });
})();
function updateCtxBar(p){
  document.querySelectorAll(".ctx-btn").forEach(b=>{
    const ctx=b.dataset.ctx,d=p&&p.ctx[ctx],n=d?(d.n_app||0):0,w=n>0&&n<NMIN;
    b.innerHTML=CTXL(ctx)+(n>0?'<span class="ctx-n'+(w?" warn":"")+'">'+n+"g</span>":"");
  });
}
function swCtx(ctx){
  if(CTX!==ctx&&CUR){
    navPush({view:"player",label:CTXL(CTX)||CTX,playerId:CUR.id,ctx:CTX,tab:TAB});
  }
  CTX=ctx;document.querySelectorAll(".ctx-btn").forEach(b=>b.classList.toggle("on",b.dataset.ctx===ctx));
  if(CUR)renderAll(CUR);
}
function swTab(el){
  const name=el.dataset.tab;
  const TAB_LABELS={"ov":T("dash_overview","Panoramica"),"conv":T("dash_tab_conv","Goals vs xG"),"tr":T("dash_tab_trend","Trend xG"),"radar":T("dash_tab_radar","Radar"),"cmp":T("dash_compare","Confronta"),"meth":T("dash_methodology","Metodologia")};
  if(TAB!==name&&CUR){
    navPush({view:"player",label:TAB_LABELS[TAB]||TAB,playerId:CUR.id,ctx:CTX,tab:TAB});
  }
  document.querySelectorAll(".tab-btn").forEach(b=>b.classList.remove("on"));
  document.querySelectorAll(".panel").forEach(p=>p.classList.remove("on"));
  el.classList.add("on");document.getElementById("p-"+name).classList.add("on");
  TAB=name;
  if(name==="meth"){buildMeth();return;}
  if(CUR){renderPanelContent(CUR);setTimeout(()=>{drawCharts(CUR);if(name==="cmp")drawCmp();},60);}
}

function showCompare(){
  /* Apre direttamente l'interfaccia confronta con i due dropdown */
  document.getElementById("view-home").style.display="none";
  document.getElementById("view-player").style.display="block";
  document.querySelectorAll(".tab-btn").forEach(b=>b.classList.toggle("on",b.dataset.tab==="cmp"));
  document.querySelectorAll(".panel").forEach(p=>p.classList.remove("on"));
  document.getElementById("p-cmp").classList.add("on");
  TAB="cmp"; CUR=CUR||DATA[0];
  drawCmp();
}

function showDiff(id){
  const p=DATA.find(x=>x.id===id); if(!p) return;
  /* Apre modal con scarto dimensionale vs un secondo giocatore scelto */
  const dims=[
    {k:"z_output",     lbl:"Output Adj/90", col:"var(--blue)"},
    {k:"z_centralita", lbl:T("dash_chip_cen","Centralità"),     col:"var(--green)"},
    {k:"z_boost",      lbl:"Team Boost",     col:"var(--orng)"},
    {k:"z_consistenza",lbl:T("dash_chip_con","Consistenza"),    col:"var(--purp)"},
  ];
  if(p.z_aii!=null)     dims.push({k:"z_aii", lbl:T("dash_aii_age","AII — Età"),  col:"var(--teal)"});
  if(p.z_pri!=null)     dims.push({k:"z_pri", lbl:T("dash_pri_phys","PRI — Fisico"),col:"#ff6b9d"});

  /* Opzioni per il secondo giocatore */
  const opts=DATA.filter(x=>x.id!==id).map(x=>`<option value="${+x.id}">${esc(x.nome||x.cognome)} (${esc(x.squadra)})</option>`).join("");

  const mbox=document.getElementById("m-b");
  const mt  =document.getElementById("m-t");
  mt.textContent=T("dash_diff_modal","Δ Differenziale")+" — "+(p.nome||p.cognome);

  mbox.innerHTML=`
    <div style="margin-bottom:14px;display:flex;align-items:center;gap:10px;flex-wrap:wrap">
      <span style="font-size:12px;color:var(--lt);font-weight:600">${esc(T("dash_compare_with","Confronta con"))}:</span>
      <select id="diff-sel" style="flex:1;min-width:180px;
        background:rgba(30,30,35,1);
        border:1px solid rgba(255,255,255,.18);border-radius:9px;
        color:#fff;font:13px var(--font);padding:7px 10px;outline:none;
        -webkit-appearance:auto"
        onchange="renderDiff(${id},this.value)">
        ${opts}
      </select>
    </div>
    <div id="diff-rows"></div>
    <div style="font-size:10px;color:var(--lt);margin-top:14px;line-height:1.6">
      ${esc(T("dash_z_explain","Valori in z-score (σ dalla media lega)."))} <strong style="color:var(--green)">${esc(T("dash_advantage","vantaggio"))}</strong> /
      <strong style="color:var(--red)">${esc(T("dash_disadvantage","svantaggio"))}</strong>.
    </div>`;

  document.getElementById("modal").style.display="flex";

  /* Render immediato con primo giocatore della lista */
  const firstId=parseInt(document.getElementById("diff-sel").value);
  renderDiff(id,firstId);
}

function renderDiff(id1,id2){
  id2=parseInt(id2);
  const p1=DATA.find(x=>x.id===id1), p2=DATA.find(x=>x.id===id2);
  if(!p1||!p2) return;

  const sf=(v,d=2)=>{
    const n=parseFloat(v);
    return isNaN(n)?null:n;
  };
  const fmt=(v,d=2)=>{
    const n=sf(v);
    return n==null?"—":(n>=0?"+":"")+n.toFixed(d);
  };

  const dims=[
    {lbl:T("dash_m_tpi","TPI Totale"),    v1:sf(p1.tpi?.totale),      v2:sf(p2.tpi?.totale),      col:"var(--orng)"},
    {lbl:"Output Adj/90", v1:sf(p1.z_output),          v2:sf(p2.z_output),         col:"var(--blue)"},
    {lbl:T("dash_chip_cen","Centralità"),    v1:sf(p1.z_centralita),      v2:sf(p2.z_centralita),     col:"var(--green)"},
    {lbl:"Team Boost",    v1:sf(p1.z_boost),            v2:sf(p2.z_boost),          col:"var(--teal)"},
    {lbl:T("dash_chip_con","Consistenza"),   v1:sf(p1.z_consistenza),     v2:sf(p2.z_consistenza),    col:"var(--purp)"},
  ];
  if(p1.tpi_ext?.totale!=null) dims.push({lbl:"TPI Pro", v1:sf(p1.tpi_ext?.totale), v2:sf(p2.tpi_ext?.totale), col:"var(--purp)"});
  if(p1.z_aii!=null) dims.push({lbl:T("dash_aii_age","AII — Età"),    v1:sf(p1.z_aii), v2:sf(p2.z_aii), col:"var(--teal)"});
  if(p1.z_pri!=null) dims.push({lbl:T("dash_pri_phys","PRI — Fisico"),  v1:sf(p1.z_pri), v2:sf(p2.z_pri), col:"#ff6b9d"});

  const n1=p1.nome||p1.cognome, n2=p2.nome||p2.cognome;

  let html=`<div style="display:grid;grid-template-columns:1fr auto auto auto;gap:0;
    border:1px solid rgba(255,255,255,.1);border-radius:12px;overflow:hidden;font-size:12px">
    <div style="padding:8px 12px;font-weight:700;color:var(--lt);background:rgba(255,255,255,.05);font-size:10px;text-transform:uppercase;letter-spacing:.5px">${esc(T("dash_dimension","Dimensione"))}</div>
    <div style="padding:8px 10px;font-weight:700;color:var(--lp);background:rgba(255,255,255,.05);font-size:10px;text-align:right;white-space:nowrap;max-width:90px;overflow:hidden;text-overflow:ellipsis">${n1}</div>
    <div style="padding:8px 10px;font-weight:700;color:var(--ls);background:rgba(255,255,255,.05);font-size:10px;text-align:right;white-space:nowrap;max-width:90px;overflow:hidden;text-overflow:ellipsis">${n2}</div>
    <div style="padding:8px 10px;font-weight:700;color:var(--lt);background:rgba(255,255,255,.05);font-size:10px;text-align:right">${esc(T("dash_btn_diff","Scarto"))}</div>`;

  dims.forEach((d,i)=>{
    const delta=(d.v1!=null&&d.v2!=null)?(d.v1-d.v2):null;
    const bg=i%2===0?"rgba(255,255,255,.02)":"transparent";
    const dcol=delta==null?"var(--lt)":delta>0.05?"var(--green)":delta<-0.05?"var(--red)":"var(--lt)";
    const dsym=delta==null?"—":delta>0.05?"▲ "+delta.toFixed(2):delta<-0.05?"▼ "+delta.toFixed(2):"≈ "+delta.toFixed(2);
    html+=`<div style="padding:9px 12px;background:${bg};color:${d.col};font-weight:600">${d.lbl}</div>
      <div style="padding:9px 10px;background:${bg};color:var(--lp);text-align:right;font-family:var(--mono)">${fmt(d.v1)}</div>
      <div style="padding:9px 10px;background:${bg};color:var(--ls);text-align:right;font-family:var(--mono)">${fmt(d.v2)}</div>
      <div style="padding:9px 10px;background:${bg};color:${dcol};text-align:right;font-family:var(--mono);font-weight:700">${dsym}</div>`;
  });
  html+="</div>";
  document.getElementById("diff-rows").innerHTML=html;
}

/* ── HTML builders ── */
function minibar(z,col){if(z==null)return"";const p=Math.min(Math.max((z+3)/6*100,0),100),w=Math.abs(p-50).toFixed(1);return'<div class="mbar"><div class="mbar-f" style="left:'+(z>=0?"50%":p+'%')+';width:'+w+'%;background:'+col+'"></div></div>';}
function zrow(lbl,z,key){const col=cz(z);let trk='<div class="ztrk"><div class="zsp"></div>';if(z!=null){const p=Math.min(Math.max((z+3)/6*100,0),100),w=Math.abs(p-50).toFixed(1);trk+='<div class="zf" style="left:'+(z>=0?"50%":p+'%')+';width:'+w+'%;background:'+col+'"></div>';}trk+='</div>';return'<div class="zrow"><div class="znm">'+lbl+(key?hb(key):"")+'</div>'+trk+'<div class="zval" style="color:'+col+'">'+fvs(z)+'</div></div>';}

function autoSynth(p,ctx){
  const d=p.ctx["totale"]||{},tpi=p.tpi.totale,r=p.rank||{},cv=p.conv||{},ft=p.form.trend;
  const dn=dispNm(p);
  let out="";
  if(p.is_winter)out+="⚠️ Acquisto invernale (dal gg "+p.first_giornata+"): stime con shrinkage rafforzato. ";
  if(tpi!=null){const pct=r.TPI&&r.n_total?Math.round((1-r.TPI/r.n_total)*100):null;
    if(tpi>=1.5)out+=dn+" d'élite: TPI "+tpi.toFixed(2)+" (top "+(pct||"?")+"%  della lega). ";
    else if(tpi>=0.5)out+=dn+": impatto offensivo positivo, TPI "+tpi.toFixed(2)+". ";
    else if(tpi>=0)out+=dn+" in linea con la media (TPI "+tpi.toFixed(2)+"). ";
    else out+=dn+": impatto offensivo sotto media (TPI "+tpi.toFixed(2)+"). ";}
  const dims=[{k:"z_output_adj",l:"nell'output offensivo"},{k:"z_centralita",l:"nella centralità"},{k:"z_boost_ratio",l:"nell'impatto squadra"},{k:"z_consistenza",l:"nella consistenza"}].filter(x=>d[x.k]!=null);
  if(dims.length){const best=dims.reduce((a,b)=>d[a.k]>d[b.k]?a:b),worst=dims.reduce((a,b)=>d[a.k]<d[b.k]?a:b);
    if(d[best.k]>0.5)out+="Eccelle "+best.l+" (z="+d[best.k].toFixed(2)+"). ";
    if(d[worst.k]<-0.3)out+="Margine "+worst.l+" (z="+d[worst.k].toFixed(2)+"). ";}
  if(cv.conv_ratio!=null){if(cv.conv_ratio>=1.15)out+="Ottimo finalizzatore G/xG "+cv.conv_ratio.toFixed(2)+". ";else if(cv.conv_ratio<=0.75)out+="Spreca occasioni (G/xG "+cv.conv_ratio.toFixed(2)+"): regressione attesa. ";}
  const t6=p.tpi.vs_top6;
  if(tpi!=null&&t6!=null){const gap=t6-tpi;if(gap<-0.5)out+="Calo contro le big (vs Top 6: "+t6.toFixed(2)+"): limite tattico. ";else if(gap>0.3)out+="Giocatore da grande partita (vs Top 6: "+t6.toFixed(2)+"). ";}
  if(ft!=null){if(ft>.15)out+="Forma in crescita (+"+((ft*100).toFixed(0))+"%).";else if(ft<-.15)out+="Forma in calo ("+((ft*100).toFixed(0))+"%) — monitorare.";}
  return out||"Dati insufficienti per una sintesi completa.";
}

function buildOvHTML(p,ctx){
  const d=p.ctx[ctx]||{},n=d.n_app||0,r=p.rank||{},tpi=p.tpi[ctx];
  const dn=dispNm(p);
  let cm;
  if(n===0)cm='<span class="warn">⚠ Nessuna partita</span>';
  else if(n<NMIN)cm='<span class="warn">⚠ Campione piccolo ('+n+'p — soglia '+NMIN+')</span>';
  else cm='<span class="ok">'+n+' partite</span>';
  const ctxInf='<div class="ctx-inf">'
    +'<span>Contesto: <strong style="color:var(--lp)">'+CTXL(ctx)+'</strong></span>'
    +(ctx==="vs_forti"?'<span>&middot;</span><span style="color:var(--lt)">Difese top '+NTOP+' per xG concessi: '+FORTI.join(", ")+'</span>':'')
    +(ctx==="vs_top6"&&TOP6.length?'<span>&middot;</span><span style="color:var(--lt)">Top 6: '+TOP6.join(", ")+'</span>':"")
    +'<span>&middot;</span>'+cm
    +(d.sos_ctx!=null?'<span>&middot;</span><span>SOS <strong style="color:var(--lp);font-family:var(--mono)">'+d.sos_ctx.toFixed(3)+'</strong></span>':"")
    +(p.is_winter?'<span>&middot;</span><span class="tag-warn">❄ Invernale — K bayesiano aumentato, stima conservativa</span>':"")
    +'</div>';
  const dims=[
    {k:"output_adj",l:"Output / 90'",cls:"m1",rk:r.output_adj,col:"var(--blue)",fmt:v=>v!=null?v.toFixed(3):"—"},
    {k:"centralita",l:"Centralità",cls:"m2",rk:r.centralita,col:"var(--green)",fmt:v=>v!=null?v.toFixed(1)+"%":"—"},
    {k:"boost_ratio",l:"Team Boost",cls:"m3",rk:r.boost,col:"var(--orng)",fmt:v=>v!=null?v.toFixed(2)+"×":"N/D"},
    {k:"consistenza",l:"Consistenza",cls:"m4",rk:r.consistenza,col:"var(--purp)",fmt:v=>v!=null?v.toFixed(3):"—"},
  ];
  const tiles=dims.map(dim=>{
    const v=d[dim.k],z=d["z_"+dim.k],isNd=(dim.k==="boost_ratio"&&v==null);
    return'<div class="mtile '+dim.cls+'">'
      +'<div class="mtile-lbl">'+dim.l+hb(dim.k)+'</div>'
      +'<div class="mtile-val">'+dim.fmt(v)+'</div>'
      +(isNd?'<div class="nd-badge">N/D — serve ≥3 partite senza il giocatore</div>'
        :'<div class="mtile-z" style="color:'+cz(z)+'">'+(z!=null?fvs(z,2)+" σ":"—")+'</div>'
          +minibar(z,dim.col))
      +(dim.rk&&r.n_total&&!isNd?'<div class="mtile-rk">Rank <b>#'+dim.rk+'</b> / '+r.n_total+'</div>':"")
      +'</div>';
  }).join("");
  const cv=p.conv||{},crv=cv.conv_ratio,gmx=cv.goal_minus_xg;
  const crCls=(crv==null)?"flat":crv>=1.15?"over":crv<=0.85?"under":"flat";
  let verd;if(crv==null)verd=T("dash_conv_insuf","Dati insufficienti (xG < 0.5)");else if(crv>=1.15)verd=T("dash_conv_over","Finalizzatore sopra media")+" — +"+((crv-1)*100).toFixed(0)+"% vs xG";else if(crv<=0.85)verd=T("dash_conv_under","Spreca le occasioni")+" — −"+((1-crv)*100).toFixed(0)+"% vs xG";else verd=T("dash_conv_inline","In linea con le aspettative xG");
  const convSnip='<div class="cv-hero">'
    +'<div class="cv-big '+crCls+'">'+(crv!=null?crv.toFixed(2):"—")+'</div>'
    +'<div class="cv-det"><div class="cv-verd">'+verd+'</div>'
    +'<div class="cv-pills"><div class="cvp">⚽ <b>'+(cv.goal_tot||0)+'</b></div>'
    +'<div class="cvp">xG <b>'+fv(cv.xg_tot,2)+'</b></div>'
    +'<div class="cvp"><b>'+(gmx!=null?(gmx>=0?"+":"")+gmx.toFixed(2):"—")+'</b> G−xG</div>'
    +'<div class="cvp">⚡ <b>'+fv(cv.goal_p90)+'</b>/90</div></div></div></div>';
  const aiBody=p.ai?p.ai:autoSynth(p,ctx);
  const aiCard='<div class="ai-card"><div class="ai-hd"><div class="ai-badge">✶ Analisi</div></div><div class="ai-body">'+aiBody+'</div></div>';

  // ── Nuovi indici v2 (AII + PRI) ──────────────────────────────
  const ph=p.physical||{};
  const aii=ph.eta_index,pri=ph.affidabilita,eta=ph.eta;
  const aiiStr=aii!=null?aii.toFixed(2):"—";
  const priStr=pri!=null?pri.toFixed(2):"—";
  const etaStr=eta!=null?eta.toFixed(1)+" aa":"—";
  const aiiCol=aii==null?"var(--lt)":aii>=0.75?"var(--green)":aii>=0.55?"var(--orng)":"var(--red)";
  const priCol=pri==null?"var(--lt)":pri>=0.75?"var(--green)":pri>=0.55?"var(--orng)":"var(--red)";
  const tpiExt=p.tpi_ext?p.tpi_ext[ctx]:null;
  const v2block=(aii!=null||pri!=null)?
    '<div class="card" style="margin-top:12px;border-color:rgba(90,200,250,.2)">'
    +'<div class="card-ttl" style="color:var(--teal)">Età & Affidabilità Fisica</div>'
    +'<div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:10px">'
    +'<div class="mtile" style="background:rgba(90,200,250,.06);border-color:rgba(90,200,250,.2)">'
      +'<div class="mtile-lbl">Età Index (AII)</div>'
      +'<div class="mtile-val" style="color:'+aiiCol+'">'+aiiStr+'</div>'
      +'<div style="font-size:10px;color:var(--lt);margin-top:3px">Età: '+etaStr+'</div>'
      +(ph.z_eta!=null?'<div class="mtile-z" style="color:'+cz(ph.z_eta)+'">'+fvs(ph.z_eta,2)+" σ</div>":"")
    +'</div>'
    +'<div class="mtile" style="background:rgba(191,90,242,.06);border-color:rgba(191,90,242,.2)">'
      +'<div class="mtile-lbl">Affidabilità Fisica</div>'
      +'<div class="mtile-val" style="color:'+priCol+'">'+priStr+'</div>'
      +'<div style="font-size:10px;color:var(--lt);margin-top:3px">'
        +(ph.n_infortuni!=null?ph.n_infortuni+' infort. · '+(ph.giorni_out||0)+'gg out':'Nessun dato')
      +'</div>'
      +(ph.z_affidabilita!=null?'<div class="mtile-z" style="color:'+cz(ph.z_affidabilita)+'">'+fvs(ph.z_affidabilita,2)+" σ</div>":"")
    +'</div>'
    +'<div class="mtile" style="background:rgba(255,159,10,.06);border-color:rgba(255,159,10,.2)">'
      +'<div class="mtile-lbl">TPI Esteso</div>'
      +'<div class="mtile-val" style="color:var(--orng)">'+(tpiExt!=null?(tpiExt>=0?"+":"")+tpiExt.toFixed(2):"—")+'</div>'
      +'<div style="font-size:10px;color:var(--lt);margin-top:3px">TPI + Età + Fisica</div>'
    +'</div>'
    +'</div>'
    +'<div style="font-size:11px;color:var(--lt);margin-top:8px">'+esc(T("dash_v2_req","⚠ Richiede data_nascita in DB (Età) e t_infortuni compilata (Affidabilità)"))+'</div>'
    +'</div>'
    :"";
  return ctxInf+'<div class="g4">'+tiles+'</div>'
    +'<div class="g2">'
      +'<div class="card"><div class="card-ttl">'+esc(T("dash_zscore_off","Z-score offensivo"))+' '+hb("TPI")
        +'<span style="font-family:var(--mono);font-size:14px;color:var(--orng);font-weight:700">TPI '
        +(tpi!=null?(tpi>=0?"+":"")+tpi.toFixed(2):"—")+'</span></div>'
        +'<div class="zlist" style="margin-bottom:10px">'
        +zrow(T("dash_zr_output","Output adj/90"),d.z_output_adj,"output_adj")
        +zrow(T("dash_chip_cen","Centralità"),d.z_centralita,"centralita")
        +zrow(T("dash_zr_boost","Team boost"),d.z_boost_ratio,"boost_ratio")
        +zrow(T("dash_chip_con","Consistenza"),d.z_consistenza,"consistenza")
        +zrow("Finishing Q",p.kpi.z_finishing)
        +'</div><div style="font-size:11px;color:var(--lt)">'+esc(T("dash_ov_zcap","0σ = media ATT+CEN Serie A | clamped ±3σ"))+'</div></div>'
      +'<div style="display:flex;flex-direction:column;gap:12px">'+convSnip+aiCard+'</div>'
    +'</div>'
    +'<div class="card"><div class="card-ttl">'+esc(T("dash_ctx5","TPI nei 5 contesti"))+'</div>'
      +'<div id="c-ctx" style="height:160px"></div>'
      +'<div style="font-size:11px;color:var(--lt);margin-top:5px">'+esc(T("dash_ctx_graycap","Grigio = meno partite del minimo (dati insufficienti)"))+'</div></div>'
    +v2block
    +'<div class="card" style="margin-top:12px">'
      +'<div class="card-ttl">'+esc(T("dash_form_match","Form — xG+xA/90 per partita (EWMA α=0.3)"))
        +'<span style="font-size:12px;color:var(--ls);font-family:var(--mono)">Trend: <span style="color:'+(p.form.trend>.10?"var(--green)":p.form.trend<-.10?"var(--red)":"var(--lt)")+';font-weight:700">'+(p.form.trend!=null?(p.form.trend>=0?"+":"")+((p.form.trend*100).toFixed(0))+"%":"—")+'</span></span></div>'
      +'<div id="c-form" style="height:180px"></div>'
      +(p.form.g&&p.form.g.length<5?'<div style="font-size:11px;color:var(--orng);margin-top:4px">'+esc(T("dash_form_few","⚠ Meno di 5 partite: trend non calcolato"))+'</div>':"")
    +'</div>';
}

function buildConvHTML(p){
  const cv=p.conv||{},crv=cv.conv_ratio,gmx=cv.goal_minus_xg;
  const crCls=(crv==null)?"flat":crv>=1.15?"over":crv<=0.85?"under":"flat";
  let verd;if(crv==null)verd=T("dash_conv_insuf","Dati insufficienti (xG < 0.5)");else if(crv>=1.15)verd=T("dash_conv_over","Finalizzatore sopra media")+" — +"+((crv-1)*100).toFixed(0)+"% vs xG";else if(crv<=0.85)verd=T("dash_conv_under","Spreca le occasioni")+" — −"+((1-crv)*100).toFixed(0)+"% vs xG";else verd=T("dash_conv_inline","In linea con le aspettative xG");
  return'<div class="g2"><div><div class="card"><div class="card-ttl">Conversion Ratio G/xG '+hb("conv_ratio")+'</div>'
    +'<div style="display:flex;align-items:flex-start;gap:16px;margin-bottom:14px">'
      +'<div class="cv-big '+crCls+'" style="font-size:56px">'+(crv!=null?crv.toFixed(2):"—")+'</div>'
      +'<div><div style="font-size:13px;color:var(--ls);line-height:1.55;margin-bottom:10px">'+verd+'</div>'
        +'<div class="zlist">'+zrow("G/xG z-score",cv.z_conv,"conv_ratio")+zrow("Finishing Q",cv.z_finishing)+'</div></div></div>'
    +'<div class="spills">'
      +'<div class="spill"><div class="spill-lbl">Goal</div><div class="spill-val">'+(cv.goal_tot||0)+'</div></div>'
      +'<div class="spill"><div class="spill-lbl">xG tot</div><div class="spill-val">'+fv(cv.xg_tot,2)+'</div></div>'
      +'<div class="spill"><div class="spill-lbl">Goal/90</div><div class="spill-val">'+fv(cv.goal_p90)+'</div></div>'
      +'<div class="spill"><div class="spill-lbl">xG/90</div><div class="spill-val">'+fv(cv.xg_p90_conv)+'</div></div>'
      +'<div class="spill '+crCls+'"><div class="spill-lbl">G/xG</div><div class="spill-val">'+fv(crv,2)+'</div></div>'
      +'<div class="spill '+(cv.conv_trend>.1?"pos":cv.conv_trend<-.1?"neg":"")+'"><div class="spill-lbl">Trend</div><div class="spill-val">'+(cv.conv_trend!=null?(cv.conv_trend>=0?"+":"")+((cv.conv_trend*100).toFixed(0))+"%":"—")+'</div></div>'
      +'<div class="spill '+(gmx>=0?"pos":"neg")+'"><div class="spill-lbl">G−xG</div><div class="spill-val">'+(gmx!=null?(gmx>=0?"+":"")+gmx.toFixed(2):"—")+'</div></div>'
    +'</div></div></div>'
    +'<div><div class="card"><div class="card-ttl">'+esc(T("dash_goals_vs_xg_match","Goal vs xG per partita"))+'</div><div id="c-cpg" style="height:230px"></div></div>'
    +'<div class="card" style="margin-top:12px"><div class="card-ttl">'+esc(T("dash_season_cumul","Cumulativo stagionale"))+'</div><div id="c-ccum" style="height:190px"></div>'
    +'<div style="font-size:11px;color:var(--lt);margin-top:5px">'+esc(T("dash_overperf_cap","Sopra la tratteggiata = sovra-performance vs xG"))+'</div></div></div></div>';
}
function buildTrHTML(p){
  const ns=p.trend&&p.trend.n_senza>0?' &middot; <strong style="color:var(--lp)">'+p.trend.n_senza+'</strong> '+esc(T("dash_matches_without","partite senza")):"";
  return'<div class="card">'
    +'<div class="card-ttl">'+esc(T("dash_team_xg_gw","xG squadra per giornata (con / senza / difese solide)"))+'</div>'
    +'<div style="font-size:12px;color:var(--lt);margin-bottom:12px;line-height:1.8">🔵 '+esc(T("dash_with_player","Con il giocatore"))+' &nbsp;·&nbsp; 🔴 '+esc(T("dash_vs_strong_def","vs Difese Solide"))+' ('+FORTI.join(", ")+') &nbsp;·&nbsp; 🟢 '+esc(T("dash_vs_weak_def","vs Difese Deboli"))+ns+'</div>'
    +'<div id="c-tr" style="height:360px"></div>'
    +'<div style="font-size:11px;color:var(--lt);margin-top:9px">'+esc(T("dash_trend_caption","Linea tratteggiata = xG medio nelle partite senza il giocatore."))+'</div></div>';
}
function buildRadarHTML(p){
  const dn=dispNm(p);
  return'<div class="card">'
    +'<div class="card-ttl">'+esc(T("dash_off_profile","Profilo offensivo"))+' — '+dn+' ('+CTXL(CTX)+')</div>'
    +'<div id="c-radar" style="height:400px"></div>'
    +'<div style="font-size:11px;color:var(--lt);margin-top:5px">'+esc(T("dash_radar_caption","Ogni asse = z-score vs media ATT+CEN &middot; range ±3σ (clampato) &middot; 0 = media lega"))+'</div></div>';
}

function renderPanelContent(p){
  document.getElementById("p-ov").innerHTML    =buildOvHTML(p,CTX);
  document.getElementById("p-conv").innerHTML  =buildConvHTML(p);
  document.getElementById("p-tr").innerHTML    =buildTrHTML(p);
  document.getElementById("p-radar").innerHTML =buildRadarHTML(p);
}
function renderAll(p){
  renderPanelContent(p);updateCtxBar(p);
  setTimeout(()=>{drawCharts(p);if(TAB==="cmp")drawCmp();if(TAB==="meth")buildMeth();},60);
}

/* ── Charts ── */
function drawCharts(p){
  if(!p)return;
  const rc=RC[p.ruolo]||"#0a84ff",rb=rgb(rc);
  const dn=dispNm(p);
  // Form
  const fe=document.getElementById("c-form");
  if(fe&&p.form.g&&p.form.g.length>0){
    Plotly.newPlot(fe,[
      {x:p.form.g,y:p.form.out,name:T("dash_ch_output_match","Output/partita"),mode:"lines+markers",
        line:{color:"rgba(255,255,255,.09)",width:1},marker:{size:3,color:"rgba(255,255,255,.18)"}},
      {x:p.form.g,y:p.form.ewma_s,name:"EWMA",mode:"lines",
        line:{color:rc,width:2.5},fill:"tozeroy",fillcolor:"rgba("+rb+",.07)"},
    ],{...BL,margin:{t:4,b:28,l:34,r:4},height:180,
      xaxis:{...BL.xaxis,dtick:1},legend:{orientation:"h",y:-.38,font:{size:10},bgcolor:"transparent"}},PL);
  }
  // TPI 5 contesti
  const ce=document.getElementById("c-ctx");
  if(ce){
    const cn=Object.keys(CTX_L),vv=cn.map(c=>p.tpi[c]);
    const nd=cn.map(c=>!p.ctx[c]||(p.ctx[c].n_app||0)<NMIN);
    Plotly.newPlot(ce,[{type:"bar",x:cn.map(c=>CTXL(c)),y:vv,
      marker:{color:vv.map((v,i)=>nd[i]?"rgba(255,255,255,.06)":v>=0?"rgba(48,209,88,.78)":"rgba(255,69,58,.72)"),line:{width:0}},
      text:vv.map((v,i)=>nd[i]?"n/d":(v!=null?(v>=0?"+":"")+v.toFixed(2):"—")),
      textposition:"outside",textfont:{size:11,color:"rgba(235,235,245,.65)"},
    }],{...BL,margin:{t:22,b:26,l:34,r:8},height:160,
      yaxis:{...BL.yaxis,zeroline:true,zerolinecolor:"rgba(255,255,255,.12)"},showlegend:false},PL);
  }
  // Trend
  const te=document.getElementById("c-tr");
  if(te&&p.trend&&p.trend.g&&p.trend.g.length>0){
    const tr=p.trend,trs=[];
    if(tr.con&&tr.con.some(v=>v!=null))trs.push({x:tr.g,y:tr.con,name:T("dash_ch_with","Con")+" "+dn,mode:"lines+markers",line:{color:"#0a84ff",width:2.5},marker:{size:5},connectgaps:true});
    if(tr.forti&&tr.forti.some(v=>v!=null))trs.push({x:tr.g,y:tr.forti,name:T("dash_vs_strong_def","vs Difese Solide"),mode:"lines+markers",line:{color:"#ff453a",width:2},marker:{size:6,symbol:"diamond"},connectgaps:true});
    if(tr.deboli&&tr.deboli.some(v=>v!=null))trs.push({x:tr.g,y:tr.deboli,name:T("dash_vs_weak_def","vs Difese Deboli"),mode:"lines+markers",line:{color:"#30d158",width:2},marker:{size:5},connectgaps:true});
    if(tr.senza&&tr.senza.some(v=>v!=null))trs.push({x:tr.g,y:tr.senza,name:T("dash_ch_without","Senza")+(tr.media_senza?" (avg "+tr.media_senza.toFixed(2)+")":""),mode:"lines+markers",line:{color:"rgba(255,255,255,.3)",width:1.5,dash:"dot"},marker:{size:4,color:"rgba(255,255,255,.18)"},connectgaps:true});
    if(tr.media_con)trs.push({x:tr.g,y:Array(tr.g.length).fill(tr.media_con),name:T("dash_ch_avg_with","Media con")+" ("+tr.media_con.toFixed(2)+")",mode:"lines",hoverinfo:"skip",line:{color:"rgba(10,132,255,.28)",width:1,dash:"dash"}});
    Plotly.newPlot(te,trs,{...BL,margin:{t:8,b:46,l:42,r:8},height:360,
      xaxis:{...BL.xaxis,title:T("dash_ch_matchday","Giornata"),dtick:1},yaxis:{...BL.yaxis,title:T("dash_ch_team_xg","xG squadra")},
      legend:{orientation:"h",y:-.2,font:{size:10},bgcolor:"transparent"}},PL);
  }
  // Radar
  const re=document.getElementById("c-radar");
  if(re){
    const d=p.ctx[CTX]||{},dims=[T("dash_ch_output_adj","Output adj"),T("dash_chip_cen","Centralità"),T("dash_ch_team_boost","Team Boost"),T("dash_chip_con","Consistenza")];
    const zv=[d.z_output_adj,d.z_centralita,d.z_boost_ratio,d.z_consistenza].map(v=>v==null?0:Math.max(-3,Math.min(3,v)));
    Plotly.newPlot(re,[{type:"scatterpolar",r:[...zv,zv[0]],theta:[...dims,dims[0]],fill:"toself",
      fillcolor:"rgba("+rb+",.14)",line:{color:rc,width:2.5},name:dn}],
      {polar:{radialaxis:{visible:true,range:[-3,3],tickvals:[-3,-2,-1,0,1,2,3],
        tickfont:{size:9,color:"rgba(235,235,245,.2)"},gridcolor:"rgba(255,255,255,.07)"},
        angularaxis:{tickfont:{size:12,color:"rgba(235,235,245,.45)"},gridcolor:"rgba(255,255,255,.07)"}},
      margin:{t:18,b:18,l:36,r:36},height:400,
      paper_bgcolor:"transparent",plot_bgcolor:"transparent",
      font:{color:"rgba(235,235,245,.35)",family:"-apple-system"},showlegend:false},PL);
  }
  // Conv per partita
  const cvp=document.getElementById("c-cpg");
  if(cvp&&p.conv&&p.conv.giornate&&p.conv.giornate.length>0){
    const cv=p.conv;
    Plotly.newPlot(cvp,[
      {type:"bar",name:"xG",x:cv.giornate,y:cv.xg_pg,marker:{color:"rgba(255,255,255,.11)"},hovertemplate:"%{x}gg: %{y:.2f} xG<extra></extra>"},
      {type:"bar",name:T("dash_ch_goals","Goal"),x:cv.giornate,y:cv.goal_pg,marker:{color:"rgba("+rb+",.73)"},hovertemplate:"%{x}gg: %{y} "+T("dash_ch_goals_low","goal")+"<extra></extra>"},
    ],{...BL,barmode:"overlay",margin:{t:4,b:32,l:32,r:4},height:230,
      xaxis:{...BL.xaxis,dtick:1},legend:{orientation:"h",y:-.28,font:{size:10},bgcolor:"transparent"}},PL);
  }
  // Conv cumulativo
  const cvc=document.getElementById("c-ccum");
  if(cvc&&p.conv&&p.conv.giornate&&p.conv.giornate.length>0){
    const cv=p.conv;
    Plotly.newPlot(cvc,[
      {type:"scatter",name:T("dash_ch_xg_cum","xG cumulativo"),x:cv.giornate,y:cv.xg_cum,mode:"lines",line:{color:"rgba(255,255,255,.22)",width:2,dash:"dot"}},
      {type:"scatter",name:T("dash_ch_goal_cum","Goal cumulativi"),x:cv.giornate,y:cv.goal_cum,mode:"lines+markers",line:{color:rc,width:2.5},marker:{size:4},fill:"tonexty",fillcolor:"rgba("+rb+",.07)"},
    ],{...BL,margin:{t:4,b:32,l:32,r:4},height:190,
      xaxis:{...BL.xaxis,dtick:1},legend:{orientation:"h",y:-.3,font:{size:10},bgcolor:"transparent"}},PL);
  }
}

/* ── Compare ── */
let CMP_FORM="";  /* filtro forma per i dropdown confronto: "" | "hot" | "cold" */
function fillCmpSelects(){
  const pool=DATA.filter(p=>!CMP_FORM||(p.recent&&p.recent.label===CMP_FORM));
  ["cs1","cs2"].forEach((id,i)=>{
    const s=document.getElementById(id);if(!s)return;
    const prev=s.value;
    s.innerHTML="";
    pool.forEach(p=>{
      const o=document.createElement("option"),t=p.tpi.totale;
      const ico=p.recent&&p.recent.label==="hot"?" 🔥":p.recent&&p.recent.label==="cold"?" 🧊":"";
      o.value=p.id;
      o.textContent=dispNm(p)+(p.is_winter?" ❄":"")+ico+" — "+p.squadra+" [TPI "+(t==null?"—":(t>=0?"+":"")+t.toFixed(2))+"]";
      s.appendChild(o);
    });
    // mantieni la selezione precedente se ancora presente, altrimenti default
    if(prev && pool.some(p=>String(p.id)===String(prev))) s.value=prev;
    else if(pool.length>i) s.value=pool[i].id;
  });
}
function selCmpForm(el){
  const wasOn=el.classList.contains("on");
  document.querySelectorAll(".rpill[data-cf]").forEach(b=>b.classList.remove("on"));
  CMP_FORM = wasOn ? "" : (el.classList.add("on"), el.dataset.cf);
  fillCmpSelects();
  drawCmp();
}
fillCmpSelects();
function drawCmp(){
  const p1=DATA.find(x=>x.id==document.getElementById("cs1").value);
  const p2=DATA.find(x=>x.id==document.getElementById("cs2").value);
  if(!p1||!p2)return;
  const d1=p1.ctx[CTX]||{},d2=p2.ctx[CTX]||{};
  const rc1=RC[p1.ruolo]||"#0a84ff",rc2=RC[p2.ruolo]||"#ff9f0a";
  const r1=rgb(rc1),r2=rgb(rc2);
  /* cognomi per legende nei grafici */
  const n1=dispNm(p1),n2=dispNm(p2);
  const mt=v=>(v!=null?(v>=0?"+":"")+v.toFixed(2):"—");
  const dims=[T("dash_ch_output_adj","Output adj"),T("dash_chip_cen","Centralità"),T("dash_ch_team_boost","Team Boost"),T("dash_chip_con","Consistenza")];
  const zv=d=>[d.z_output_adj,d.z_centralita,d.z_boost_ratio,d.z_consistenza].map(v=>v==null?0:Math.max(-3,Math.min(3,v)));
  Plotly.newPlot("cmp-radar",[
    {type:"scatterpolar",r:[...zv(d1),zv(d1)[0]],theta:[...dims,dims[0]],fill:"toself",fillcolor:"rgba("+r1+",.13)",line:{color:rc1,width:2},name:n1},
    {type:"scatterpolar",r:[...zv(d2),zv(d2)[0]],theta:[...dims,dims[0]],fill:"toself",fillcolor:"rgba("+r2+",.11)",line:{color:rc2,width:2},name:n2},
  ],{polar:{radialaxis:{visible:true,range:[-3,3],tickvals:[-3,-2,-1,0,1,2,3],tickfont:{size:9,color:"rgba(235,235,245,.2)"},gridcolor:"rgba(255,255,255,.07)"},angularaxis:{tickfont:{size:11,color:"rgba(235,235,245,.42)"},gridcolor:"rgba(255,255,255,.07)"}},margin:{t:18,b:36,l:26,r:26},height:300,paper_bgcolor:"transparent",plot_bgcolor:"transparent",font:{color:"rgba(235,235,245,.35)",family:"-apple-system"},legend:{orientation:"h",y:-.1,font:{size:10},bgcolor:"transparent"}},PL);
  const mets=[{l:"Output adj",v1:d1.z_output_adj,v2:d2.z_output_adj},{l:"Centralità",v1:d1.z_centralita,v2:d2.z_centralita},{l:"Team Boost",v1:d1.z_boost_ratio,v2:d2.z_boost_ratio},{l:"Consistenza",v1:d1.z_consistenza,v2:d2.z_consistenza},{l:"Finishing Q",v1:p1.kpi.z_finishing,v2:p2.kpi.z_finishing},{l:"G/xG",v1:p1.conv?.z_conv,v2:p2.conv?.z_conv}];
  Plotly.newPlot("cmp-bars",[
    {type:"bar",name:n1,x:mets.map(m=>m.l),y:mets.map(m=>m.v1),marker:{color:rc1,opacity:.8},text:mets.map(m=>mt(m.v1)),textposition:"outside",textfont:{size:9,color:"rgba(235,235,245,.65)"}},
    {type:"bar",name:n2,x:mets.map(m=>m.l),y:mets.map(m=>m.v2),marker:{color:rc2,opacity:.8},text:mets.map(m=>mt(m.v2)),textposition:"outside",textfont:{size:9,color:"rgba(235,235,245,.65)"}},
  ],{...BL,barmode:"group",margin:{t:8,b:52,l:32,r:8},height:300,yaxis:{...BL.yaxis,zeroline:true,zerolinecolor:"rgba(255,255,255,.12)"},legend:{orientation:"h",y:-.22,font:{size:10},bgcolor:"transparent"}},PL);
  const cn=Object.keys(CTX_L);
  Plotly.newPlot("cmp-ctx",[
    {type:"bar",name:n1,x:cn.map(c=>CTXL(c)),y:cn.map(c=>p1.tpi[c]),marker:{color:rc1,opacity:.8},text:cn.map(c=>{const v=p1.tpi[c];return v!=null?(v>=0?"+":"")+v.toFixed(2):"—";}),textposition:"outside",textfont:{size:9,color:"rgba(235,235,245,.65)"}},
    {type:"bar",name:n2,x:cn.map(c=>CTXL(c)),y:cn.map(c=>p2.tpi[c]),marker:{color:rc2,opacity:.8},text:cn.map(c=>{const v=p2.tpi[c];return v!=null?(v>=0?"+":"")+v.toFixed(2):"—";}),textposition:"outside",textfont:{size:9,color:"rgba(235,235,245,.65)"}},
  ],{...BL,barmode:"group",margin:{t:8,b:34,l:32,r:8},height:220,yaxis:{...BL.yaxis,zeroline:true,zerolinecolor:"rgba(255,255,255,.12)"},legend:{orientation:"h",y:-.2,font:{size:10},bgcolor:"transparent"}},PL);
  const cr=[{l:"G/xG",v1:p1.conv?.conv_ratio,v2:p2.conv?.conv_ratio},{l:"Goal/90",v1:p1.conv?.goal_p90,v2:p2.conv?.goal_p90},{l:"xG/90",v1:p1.conv?.xg_p90_conv,v2:p2.conv?.xg_p90_conv},{l:"Finish Q",v1:p1.conv?.finishing_q,v2:p2.conv?.finishing_q}];
  Plotly.newPlot("cmp-conv",[
    {type:"bar",name:n1,x:cr.map(m=>m.l),y:cr.map(m=>m.v1),marker:{color:rc1,opacity:.8},text:cr.map(m=>fv(m.v1,2)),textposition:"outside",textfont:{size:9,color:"rgba(235,235,245,.65)"}},
    {type:"bar",name:n2,x:cr.map(m=>m.l),y:cr.map(m=>m.v2),marker:{color:rc2,opacity:.8},text:cr.map(m=>fv(m.v2,2)),textposition:"outside",textfont:{size:9,color:"rgba(235,235,245,.65)"}},
  ],{...BL,barmode:"group",margin:{t:8,b:34,l:32,r:8},height:200,yaxis:{...BL.yaxis,zeroline:true,zerolinecolor:"rgba(255,255,255,.12)"},legend:{orientation:"h",y:-.22,font:{size:10},bgcolor:"transparent"}},PL);
}

/* ── Metodologia (tab player) ── */
function buildMeth(){
  const ibg={TPI:"rgba(255,159,10,.12)",output_adj:"rgba(10,132,255,.12)",centralita:"rgba(48,209,88,.12)",boost_ratio:"rgba(255,69,58,.12)",consistenza:"rgba(191,90,242,.12)",conv_ratio:"rgba(90,200,250,.12)"};
  const icons={TPI:"&#x1F3C6;",output_adj:"&#x26A1;",centralita:"&#x1F3AF;",boost_ratio:"&#x1F4C8;",consistenza:"&#x1F4CA;",conv_ratio:"&#x26BD;"};
  let h='<div class="meth-grid">';
  ["TPI","output_adj","centralita","boost_ratio","consistenza","conv_ratio"].forEach(k=>{
    const s=SPIEG[k];if(!s)return;
    h+='<div class="meth-card"><div class="meth-hd"><div class="meth-icon" style="background:'+(ibg[k]||"rgba(255,255,255,.07)")+'">'+(icons[k]||"?")+'</div><div class="meth-nm">'+s.titolo+'</div></div><div class="meth-formula">'+s.formula+'</div><div class="meth-logic">'+s.logica+'</div><div class="meth-ex">'+s.esempio+'</div></div>';
  });
  h+='</div><div class="card" style="margin-top:16px;max-width:860px"><div class="card-ttl">'+esc(T("dash_zdim_player","Z-score dimensioni per il giocatore selezionato (Totale)"))+'</div><div id="c-meth-bar" style="height:220px"></div></div>';
  document.getElementById("meth-content").innerHTML=h;
  if(CUR){
    const d=CUR.ctx["totale"]||{},dims=[T("dash_ch_output_adj","Output adj"),T("dash_chip_cen","Centralità"),T("dash_ch_team_boost","Team Boost"),T("dash_chip_con","Consistenza")],cols=["var(--blue)","var(--green)","var(--orng)","var(--purp)"];
    const zv=[d.z_output_adj,d.z_centralita,d.z_boost_ratio,d.z_consistenza].map(v=>v==null?null:Math.max(-3,Math.min(3,v)));
    Plotly.newPlot("c-meth-bar",[{type:"bar",orientation:"h",x:zv,y:dims,
      marker:{color:zv.map((v,i)=>v==null?"rgba(255,255,255,.07)":cols[i])},
      text:zv.map(v=>v==null?"n/d":(v>=0?"+":"")+v.toFixed(2)+" σ"),textposition:"outside",
      textfont:{size:11,color:"rgba(235,235,245,.55)"}}],
      {...BL,margin:{t:8,b:28,l:96,r:56},height:220,
        xaxis:{...BL.xaxis,range:[-3.5,3.5],tickvals:[-3,-2,-1,0,1,2,3],ticktext:["-3σ","-2σ","-1σ","0","+1σ","+2σ","+3σ"]},
        yaxis:{...BL.yaxis,autorange:"reversed"},showlegend:false,
        shapes:[{type:"line",x0:0,x1:0,y0:-.5,y1:3.5,line:{color:"rgba(255,255,255,.15)",width:1,dash:"dot"}}]},PL);
  }
}

/* ── Init ── */
buildDrop();
showHome();
buildLeaderboard();
buildTpiProSection();

/* ── i18n: re-render del contenuto dinamico al cambio lingua ── */
document.addEventListener("i18n:changed", () => {
  try {
    buildDrop();
    updateHomeSub();
    buildLeaderboard();
    buildTeamStrip();
    buildTpiProSection();
    /* se un profilo è aperto, rigenera i suoi pannelli */
    if (typeof CUR !== "undefined" && CUR && document.getElementById("view-player")
        && document.getElementById("view-player").style.display !== "none") {
      renderAll(CUR);
    }
  } catch (e) { /* non bloccare lo switch lingua per un errore di render */ }
});

/* ── Resize / orientamento: ridisegna grafici Plotly ── */
let _resizeTimer;
window.addEventListener("resize", () => {
  clearTimeout(_resizeTimer);
  _resizeTimer = setTimeout(() => {
    // Ridisegna tutti i grafici Plotly visibili
    document.querySelectorAll(".js-plotly-plot").forEach(el => {
      try { Plotly.Plots.resize(el); } catch(e) {}
    });
  }, 200);
});
// Evento specifico per cambio orientamento su mobile
window.addEventListener("orientationchange", () => {
  setTimeout(() => {
    document.querySelectorAll(".js-plotly-plot").forEach(el => {
      try { Plotly.Plots.resize(el); } catch(e) {}
    });
  }, 400);
});
</script>
<!-- ── Watermark ── -->
<div id="wm">
  <span id="wm-dot"></span>
  <span id="wm-text">Raffaele Ciccone &thinsp;&middot;&thinsp; Serie A Scout Index &thinsp;&middot;&thinsp; 2025&thinsp;/&thinsp;26</span>
</div>
<style>
#wm{
  position:fixed;bottom:14px;left:50%;transform:translateX(-50%);
  display:flex;align-items:center;gap:7px;
  padding:5px 14px 5px 10px;
  background:rgba(255,255,255,.04);
  backdrop-filter:saturate(180%) blur(20px);
  -webkit-backdrop-filter:saturate(180%) blur(20px);
  border:1px solid rgba(255,255,255,.09);
  border-top-color:rgba(255,255,255,.14);
  border-radius:20px;
  box-shadow:0 2px 12px rgba(0,0,0,.35),inset 0 1px 0 rgba(255,255,255,.06);
  z-index:800;pointer-events:none;
  transition:opacity .3s;
}
#wm:hover{opacity:.4}
#wm-dot{
  width:6px;height:6px;border-radius:50%;
  background:var(--blue);
  box-shadow:0 0 6px rgba(10,132,255,.6);
  flex-shrink:0;
}
#wm-text{
  font-family:-apple-system,BlinkMacSystemFont,"SF Pro Text","Helvetica Neue",sans-serif;
  font-size:10px;font-weight:500;letter-spacing:.3px;
  color:rgba(235,235,245,.28);white-space:nowrap;
}
@media(max-width:768px){
  #wm{display:none}
  .home-hdr svg{width:44px;height:44px}
}
@media(max-width:480px){
  #wm{display:none}
  .home-hdr svg{display:none}
}
</style>

</body>
</html>"""


# ════════════════════════════════════════════════════════════════
# 5. GENERAZIONE HTML
# ════════════════════════════════════════════════════════════════
def inject_data(template: str, meta: dict) -> str:
    payload     = meta["players"]
    n_top_dif   = meta.get("n_top_difese", 6)
    n_gio       = meta.get("n_giocatori", len(payload))
    n_gior      = meta.get("n_giornate", "?")
    top6_names  = meta.get("top6_names", [])
    forti_names = meta.get("forti_names", [])
    roster      = meta.get("roster", [])
    teams       = sorted(set(clean(p["squadra"]) for p in payload))

    def jsdump(obj) -> str:
        return json.dumps(deep_clean(obj), ensure_ascii=True)

    replacements = {
        "__DATA_JS__":      jsdump(payload),
        "__RC_JS__":        jsdump(RUOLO_COLORS),
        "__RL_JS__":        jsdump(RUOLO_LABELS),
        "__CTX_L_JS__":     jsdump(CTX_LABELS),
        "__SPIEG_JS__":     jsdump(SPIEGAZIONI),
        "__TOP6_JS__":      jsdump([clean(n) for n in top6_names]),
        "__FORTI_JS__":     jsdump([clean(n) for n in forti_names]),
        "__TEAMS_JS__":     jsdump(teams),
        "__ROSTER_JS__":    jsdump(roster),
        "__TPI_PRO_JS__":   jsdump(meta.get("tpi_pro_showcase", [])),
        "__N_TOP_DIF__":    str(n_top_dif),
        "__N_GIO__":        str(n_gio),
        "__N_GIOR__":       str(n_gior),
    }

    result = template
    for placeholder, value in replacements.items():
        result = result.replace(placeholder, value)

    remaining = [p for p in replacements if p in result]
    if remaining:
        log.warning(f"Placeholder non sostituiti: {remaining}")

    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Serie A 25/26 — Dashboard HTML Generator"
    )
    parser.add_argument(
        "--payload", type=str, default=None,
        help="Percorso custom del payload.json"
    )
    parser.add_argument(
        "--output", type=str, default=None,
        help="Percorso custom dell'HTML di output"
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    log.info("=" * 60)
    log.info("PARTE 2 — Dashboard HTML v4.2  Serie A 25/26")
    log.info("=" * 60)

    payload_path = Path(args.payload) if args.payload else PAYLOAD_PATH
    html_out     = Path(args.output)  if args.output  else HTML_OUT

    meta = load_payload(payload_path)

    log.info("Iniezione dati nel template...")
    html = inject_data(HTML_TEMPLATE, meta)

    html_out.parent.mkdir(parents=True, exist_ok=True)
    with open(html_out, "wb") as fh:
        fh.write(html.encode("utf-8", errors="replace"))

    size_kb = html_out.stat().st_size // 1024
    log.info(f"✓ Dashboard: {html_out}  ({size_kb} KB)")
    log.info(f"  Apri: file:///{str(html_out).replace(chr(92), '/')}")

    if DEMO_DIR.is_dir() and html_out.name == HTML_OUT.name:
        demo_copy = DEMO_DIR / html_out.name
        try:
            demo_copy.write_bytes(html_out.read_bytes())
            log.info(f"✓ Copia demo: {demo_copy}")
        except OSError as e:
            log.warning(f"Copia demo fallita: {e}")

    # i18n.js deve stare ACCANTO all'HTML (lo <script src="i18n.js"> è relativo):
    # lo copio dal repo demo (fonte canonica) in dashboard_output, così la pagina
    # aperta da lì non perde le traduzioni (404 su i18n.js = mix di lingue).
    _i18n_src = DEMO_DIR / "i18n.js"
    if _i18n_src.is_file() and html_out.name == HTML_OUT.name:
        try:
            (OUTPUT_DIR / "i18n.js").write_bytes(_i18n_src.read_bytes())
            log.info(f"✓ i18n.js → {OUTPUT_DIR / 'i18n.js'}")
        except OSError as e:
            log.warning(f"Copia i18n.js fallita: {e}")

    log.info("=" * 60)


if __name__ == "__main__":
    main()
