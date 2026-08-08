"""
parte2_dashboard.py — Serie A 25/26 | Dashboard HTML v4.2
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

OUTPUT_DIR  = _DIR / "dashboard_output"
PAYLOAD_PATH = OUTPUT_DIR / "payload.json"
HTML_OUT   = OUTPUT_DIR / "dashboard_serie_a.html"

# Il CSS sta in un file suo invece che dentro la stringa del template: 934 righe
# scritte dentro un r-string Python non hanno evidenziazione, non si possono
# lintare e producono diff illeggibili. Viene riletto a ogni run e inlinato nel
# segnaposto __STYLE__, quindi l'HTML prodotto resta un unico file autoportante.
CSS_PATH   = _DIR / "assets" / "dashboard.css"

# Cartella del repo pubblico GitHub Pages: se esiste, ricevo una copia
# automatica della dashboard. Senza questa copia, "homepage.html" linkato
# dalla dashboard non viene trovato (sta solo nel repo demo).
# Default: cartella sorella `serie-a-scout-demo` (es. Desktop/serie-a-scout-demo
# quando questo script vive in Desktop/serie-a-scout-index). Override con
# l'env var SERIE_A_DEMO_DIR se il repo è altrove.
DEMO_DIR   = Path(os.environ.get("SERIE_A_DEMO_DIR", _DIR.parent / "serie-a-scout-demo"))


# ════════════════════════════════════════════════════════════════
# 2. COSTANTI UI
# ════════════════════════════════════════════════════════════════
# Triade desaturata, allineata ai token --blue/--green/--clay di assets/dashboard.css.
# ATT non e' piu' #ff9f0a: quell'ambra ora e' riservata al segnale "attivo /
# questo e' il valore", e usarla anche per un ruolo la svuotava di significato.
RUOLO_COLORS = {
  "POR": "#7A8A84",
  "DIF": "#5A93C4",
  "CEN": "#5FAE7E",
  "ATT": "#D98E6A",
  "":  "#46554F",
}

RUOLO_LABELS = {
  "POR": "Portiere",
  "DIF": "Difensore",
  "CEN": "Centrocampista",
  "ATT": "Attaccante",
}

CTX_LABELS = {
  "totale":  "Totale",
  "casa":   "Casa",
  "trasferta": "Trasferta",
  "vs_top6":  "vs Top 6",
  "vs_forti": "Difese Solide",
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
    "formula": "Media pesata z-score: output 0.32 · buildup 0.10 · centralità 0.18 · boost 0.02 · consistenza 0.07 · finishing 0.20 · forma 0.11 × penalty disponibilità winter-aware",
    "logica": (
      "Ogni dimensione viene standardizzata come z-score rispetto alla "
      "distribuzione di ruolo della lega (un difensore è confrontato con "
      "i difensori, non con gli attaccanti). "
      "TPI = media PESATA dei 7 z-score; la qualità (output_adj) domina; "
      "finishing e forma recente pesano un terzo del totale così chi non "
      "incide da diverse giornate scende anche con buoni totali stagionali. "
      "\n\n"
      "ROBUSTEZZA: 4 fix per evitare il bias 'pochi sample = stima rumorosa':\n"
      "• Shrinkage Bayes con K dinamico = max(75/avg_min, 30/partite) — chi "
      " gioca poco per gara o ha poche partite regredisce di più verso media-ruolo.\n"
      "• Confidence v2 a 4 fattori (minuti × partite × intensità × dim) — usata "
      " per il shrinkage finale verso media-ruolo.\n"
      "• Penalty disponibilità WINTER-AWARE: TPI × clip(disponibilità_rel, 0.85, 1.0) "
      " dove disponibilità = partite_giocate/partite_disponibili. Per i winter "
      " signing 'disponibili' = partite dalla finestra di arrivo. "
      " Effetto: Malen (winter, 18/20=90%) intatto, De Bruyne (18/38=47%) penalty 0.91, "
      " Berisha (13/38=34%) penalty 0.85 cap.\n"
      "• Re-normalizzazione pesi sulle dim effettivamente disponibili (se boost N/D)."
      "\n\n"
      "Lo z-score misura quante deviazioni standard sopra/sotto la media-ruolo. "
      "0 = media ruolo, +1 = top 16%, +2 = top 2.5%."
    ),
    "esempio": (
      "TPI +1.72: 1.72 dev std sopra la media del ruolo — top 5% della lega. "
      "TPI 0.0 = media ruolo. TPI −1.0 = sotto il 16% del ruolo. "
      "Esempi reali post-fix: Lautaro (30/38) penalty 1.0 → TPI intatto. "
      "Donyell Malen (winter signing, 18/20=90% disp) → penalty 1.0 → TPI intatto al #2. "
      "De Bruyne (titolare, 18/38=47%) → penalty 0.91, scende dal #10 al #13. "
      "Berisha (titolare, 13/38=34%) → penalty 0.85, scende dal #27 al #35."
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
<meta http-equiv="Content-Security-Policy" content="default-src 'self'; script-src 'self' https://cdn.plot.ly 'unsafe-inline'; style-src 'self' https://cdn.jsdelivr.net 'unsafe-inline'; font-src 'self'; img-src 'self' data:; connect-src 'self' https://*.workers.dev; frame-ancestors 'none'; base-uri 'self'; form-action 'none'; object-src 'none'">
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
<script src="ai_chat.js" defer></script>
<style>
__STYLE__
</style>
</head>
<body>

<!-- Error banner -->
<div id="err-banner" style="display:none;position:fixed;bottom:20px;left:20px;right:20px;
 background:var(--red);color:#fff;padding:13px 16px;border-radius:12px;font-size:13px;
 z-index:9999;box-shadow:0 8px 24px rgba(0,0,0,.6)">
 <strong data-i18n="dash_js_error">Errore JS:</strong><span id="err-msg"></span>
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
   <span class="home-label" data-i18n="term_ranking">Classifica</span>
  </button>
  <button class="nav-glass-btn" id="nav-back-btn" onclick="histBack()" data-i18n-title="nav_back" data-i18n-aria-label="nav_back" title="Indietro" disabled>&#8592;</button>
  <button class="nav-glass-btn" id="nav-fwd-btn" onclick="histForward()" data-i18n-title="nav_forward" data-i18n-aria-label="nav_forward" title="Avanti" disabled>&#8594;</button>
 </div>
 <div class="nav-right-group" style="display:flex;align-items:center;gap:8px">
  <span data-i18n-switcher></span>
  <a class="nav-orng-btn" href="index.html" data-i18n-title="nav_back_homepage" title="Torna alla Homepage">
   <span class="hp-label" data-i18n="nav_home">Homepage</span>
  </a>
  <a class="nav-purp-btn" href="validazione.html" data-i18n-title="nav_validation" title="Validazione TPI">
   <span class="val-label" data-i18n="nav_validation">Validazione</span>
  </a>
  <a class="nav-pro-btn" href="dashboard_pro.html" title="TPI Pro — indice age-aware con i cinque modulatori scout">
   <span class="pro-label">TPI Pro</span>
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
 <div class="hero-grid">
  <div class="hero-left">
   <h1 class="hero-ttl"><span data-it="__N_GIO__ giocatori." data-en="__N_GIO__ players.">__N_GIO__ giocatori.</span><br>
     <span data-it="__N_PUB__ stanno" data-en="__N_PUB__ are">__N_PUB__ stanno</span> <em data-it="qui" data-en="here">qui</em>.</h1>
   <p class="hero-dek" data-it="Il TPI &egrave; uno z-score: <b>0.00 &egrave; il giocatore medio</b> della Serie A. Sette dimensioni indipendenti, corrette per la forza degli avversari e stabilizzate con shrinkage bayesiano." data-en="TPI is a z-score: <b>0.00 is the average player</b> in Serie A. Seven independent dimensions, corrected for opponent strength and stabilised with Bayesian shrinkage.">Il TPI &egrave; uno z-score: <b>0.00 &egrave; il giocatore medio</b>
     della Serie A. Sette dimensioni indipendenti, corrette per la forza degli avversari
     e stabilizzate con shrinkage bayesiano.
     <span class="help" onclick="openM('TPI')">?</span></p>
   <div class="hero-facts">
    <div class="hf"><div class="hf-n">__N_GIO__</div><div class="hf-l" data-it="Giocatori" data-en="Players">Giocatori</div></div>
    <div class="hf"><div class="hf-n">__N_GIOR__</div><div class="hf-l" data-it="Giornate" data-en="Matchdays">Giornate</div></div>
    <div class="hf"><div class="hf-n">6</div><div class="hf-l" data-it="Dimensioni" data-en="Dimensions">Dimensioni</div></div>
   </div>
  </div>
  <div class="hero-right">
   <div class="hero-cap" data-it="Curva di riferimento z ~ N(0,1) &middot; in ambra la fetta di classifica pubblicata"
        data-en="Reference curve z ~ N(0,1) &middot; in amber, the published slice of the ranking">
     Curva di riferimento z ~ N(0,1) &middot; in ambra la fetta di classifica pubblicata</div>
   <svg id="hero-curve" width="100%" height="196" viewBox="0 0 760 196"
        preserveAspectRatio="none" role="img" aria-label="Distribuzione del TPI"></svg>
   <div class="hero-axis"><span>&minus;3&sigma;</span><span>&minus;2&sigma;</span><span>&minus;1&sigma;</span>
     <span data-it="MEDIA" data-en="MEAN">MEDIA</span><span>+1&sigma;</span><span>+2&sigma;</span><span>+3&sigma;</span></div>
  </div>
  </div>
 </div>

 <div class="team-strip" id="team-strip"></div>

 <!-- ══════════════════════════════════
    TPI PRO SHOWCASE SECTION
 ══════════════════════════════════ -->
 <div id="tpi-pro-section">
  <div class="tpp-hdr">
   <!-- Badge, titolo e pulsante su una riga: erano tre righe con mezzo
        rettangolo vuoto a destra. -->
   <div class="tpp-row">
    <div class="tpp-badge" data-i18n="dash_pro_badge">Novit&agrave; &mdash; TPI Pro</div>
    <div class="tpp-ttl" data-i18n="dash_pro_ttl">TPI Pro: TPI base + 5 modulatori scout</div>
    <button class="tpp-collapse-btn" id="tpp-toggle" onclick="toggleTppSection()"
     >&#9660; <span data-i18n="dash_show_pro">Mostra TPI Pro</span></button>
   </div>
   <div class="tpp-sub tpp-sub-collapsible" data-i18n-html="dash_pro_body">
    Il <strong>TPI classico</strong> usa 7 dimensioni offensive (output, buildup, centralità,
    boost, consistenza, finishing, forma recente). Il <strong>TPI Pro</strong> aggiunge
    5 modulatori scout: <span style="color:var(--teal)">Età Index (AII)</span>,
    <span style="color:var(--purp)">Affidabilità Fisica (PRI)</span>, stabilità
    cross-contesto, trend forma ed EMI.
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
    <div class="tpp-dim tpp-dim-aii">AII &mdash; Age Impact Index
     <span style="font-size:10px;font-weight:400;margin-left:4px;color:rgba(90,200,250,.6)" data-i18n="dash_pro_gauss">Gaussiana picco 27 anni</span></div>
    <div class="tpp-dim tpp-dim-pri">PRI &mdash; Physical Reliability
     <span style="font-size:10px;font-weight:400;margin-left:4px;color:rgba(191,90,242,.6)" data-i18n="dash_pro_avail">Disponibilit&agrave; + infortuni + gravit&agrave;</span></div>
    <div class="tpp-dim tpp-dim-tpi">TPI Classic
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
  <button class="mpill on" data-m="tpi" onclick="selMetric(this)"><span data-i18n="dash_chip_tpi">TPI</span></button>
  <button class="mpill" data-m="prospect" onclick="selMetric(this)"><span data-i18n="dash_chip_prospect">Giovani &#x2605;</span></button>
  <button class="mpill" data-m="out" onclick="selMetric(this)"><span data-i18n="dash_chip_output">Output</span></button>
  <button class="mpill" data-m="cen" onclick="selMetric(this)"><span data-i18n="dash_chip_cen">Centralit&agrave;</span></button>
  <button class="mpill" data-m="boo" onclick="selMetric(this)"><span data-i18n="dash_chip_boo">Boost</span></button>
  <button class="mpill" data-m="con" onclick="selMetric(this)"><span data-i18n="dash_chip_con">Consistenza</span></button>
  <button class="mpill" data-m="conv" onclick="selMetric(this)"><span data-i18n="dash_chip_conv">G/xG</span></button>
  <span class="ctrl-lbl" data-it="Ruolo" data-en="Role">Ruolo</span>
  <button class="rpill" data-r="ATT" onclick="selRole(this)" data-i18n="dash_role_fwd_s">ATT</button>
  <button class="rpill" data-r="CEN" onclick="selRole(this)" data-i18n="dash_role_mid_s">CEN</button>
  <button class="rpill" data-r="DIF" onclick="selRole(this)" data-i18n="dash_role_def_s">DIF</button>
  <span class="ctrl-lbl" data-it="Forma" data-en="Form">Forma</span>
  <button class="rpill" data-f="hot" onclick="selForm(this)" title="Solo in forma"><span data-i18n="dash_filter_hot">In forma</span></button>
  <button class="rpill" data-f="cold" onclick="selForm(this)" title="Solo in calo"><span data-i18n="dash_filter_cold">In calo</span></button>
  <span class="ctrl-lbl" data-it="Cerca" data-en="Find">Cerca</span>
  <div class="sq-wrap" id="sq-wrap">
   <button class="sq-btn" id="sq-btn" onclick="toggleSqFpk()">
    <span id="sq-lbl" data-i18n="dash_filter_team">Squadra</span>
    <span class="sq-chevron">&#9660;</span>
   </button>
  </div>
  <div class="sq-wrap" id="fpk-wrap">
   <button class="sq-btn" id="fpk-btn" onclick="toggleFpk()">
    <span id="fpk-lbl" data-i18n="term_player">Giocatore</span>
    <span class="sq-chevron">&#9660;</span>
   </button>
  </div>
  <div class="ctrl-div"></div>
  <button class="cpill" onclick="selMetric(document.querySelector('.mpill[data-m=tpi]'));showCompare()">
   <span data-i18n="dash_btn_compare">Confronta</span>
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
   <span style="font-size:14px;color:var(--red)">&#x2715;</span><span data-i18n="dash_remove_filter">Rimuovi filtro</span>
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
   <span style="font-size:15px;font-weight:700;letter-spacing:-.3px" data-i18n="dash_meth_calc">Metodologia e Calcoli</span>
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
  <button class="tab-btn on" data-tab="ov"  onclick="swTab(this)" data-i18n="dash_overview">Panoramica</button>
  <button class="tab-btn"  data-tab="conv" onclick="swTab(this)" data-i18n="dash_tab_conv">Goals vs xG</button>
  <button class="tab-btn"  data-tab="tr"  onclick="swTab(this)" data-i18n="dash_tab_trend">Trend xG</button>
  <button class="tab-btn"  data-tab="radar" onclick="swTab(this)" data-i18n="dash_tab_radar">Radar</button>
  <button class="tab-btn"  data-tab="cmp"  onclick="swTab(this)" data-i18n="dash_compare">Confronta</button>
  <button class="tab-btn"  data-tab="meth" onclick="swTab(this)" data-i18n="dash_methodology">Metodologia</button>
 </div>

 <div id="p-ov"  class="panel on"></div>
 <div id="p-conv" class="panel"></div>
 <div id="p-tr"  class="panel"></div>
 <div id="p-radar" class="panel"></div>

 <div id="p-cmp" class="panel">
  <div style="display:flex;gap:8px;padding:0 0 10px;flex-wrap:wrap;align-items:center">
   <span style="font-size:11px;color:var(--lt)" data-i18n="dash_filter_form">Filtra per forma:</span>
   <button class="rpill" data-cf="hot" onclick="selCmpForm(this)"><span data-i18n="dash_filter_hot">In forma</span></button>
   <button class="rpill" data-cf="cold" onclick="selCmpForm(this)"><span data-i18n="dash_filter_cold">In calo</span></button>
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
    Il TPI misura l&rsquo;impatto offensivo reale attraverso 7 dimensioni ortogonali.
    Bayesian shrinkage stabilizza le stime. SOS-weighting normalizza la difficoltà.</p>
   <div id="meth-content"></div>
  </div>
 </div>
</div>

<!-- ═══ JAVASCRIPT ═══ -->
<script>
/* ── Dati iniettati dal Python ── */
const DATA  = __DATA_JS__;
const RC   = __RC_JS__;
const RL   = __RL_JS__;
/* nome-ruolo localizzato: usa i18n se disponibile, fallback a RL (italiano) */
const _ROLE_KEY={POR:"dash_role_full_POR",DIF:"dash_role_full_DIF",CEN:"dash_role_full_CEN",ATT:"dash_role_full_ATT"};
function roleName(code){ return T(_ROLE_KEY[code], RL[code]||code); }
/* Badge forma recente (ultime N gare): caldo / freddo. Tooltip coi numeri. */
function formBadge(p){
 const r=p&&p.recent; if(!r||!r.label) return "";
 if(r.label==="stable") return "";
 const ico=r.label==="hot"?T("dash_filter_hot","In forma"):T("dash_filter_cold","In calo");
 const tip=esc("Forma ultime "+(r.n||0)+" gare: "+(r.goal||0)+" gol, npxG "+(r.npxg||0)+", out/90 "+(r.out90||0)+" ("+Math.round((r.ratio||0)*100)+"% della stagione)");
 return ' <span title="'+tip+'" class="form-tag form-'+r.label+'">'+ico+'</span>';
}

/* Profilo a sei dimensioni: e' cio' che riempie lo spazio morto in mezzo alla
  riga, e soprattutto e' la ragione per cui la riga vale la pena guardarla.
  Ogni barra e' uno z-score sulla stessa scala, sopra o sotto la media di ruolo:
  scorrendo la colonna si legge la FORMA di un giocatore, non solo il suo posto.
  Un difensore tutto ambra come Dimarco e un Mkhitaryan che crolla sull'eta si
  distinguono a colpo d'occhio, senza aprire nessun pannello. */
const PROF_DIMS=[["z_output","OUT"],["z_buildup","BLD"],["z_centralita","CEN"],
         ["z_consistenza","CNS"],["z_aii","ETÀ"],["z_pri","FIS"]];
function profStrip(p){
 let bars="",labs="";
 for(const [k,lab] of PROF_DIMS){
  const v=p[k];
  if(typeof v!=="number"||!isFinite(v)){
   /* boost e' spesso null: minuti "senza" insufficienti per un confronto
     onesto. Casella vuota, non zero — sarebbero due cose diverse. */
   bars+='<i></i>';
  }else{
   const h=Math.min(Math.abs(v)/3,1)*50;
   const st=v>=0?"bottom:50%;height:"+h+"%":"top:50%;height:"+h+"%";
   bars+='<i><span class="'+(v>=0?"pf-up":"pf-dn")+'" style="'+st+'"></span></i>';
  }
  labs+='<u>'+lab+'</u>';
 }
 return '<div class="lb-prof" title="'+esc(T("dash_prof_tip",
  "Sei z-score sulla stessa scala: verso l’alto sopra la media di ruolo, verso il basso sotto"))
  +'"><div class="pf-bars">'+bars+'</div><div class="pf-labs">'+labs+'</div></div>';
}

/* Percentile sui 381 analizzati, non sui 100 del payload: rank.n_total porta il
  totale vero. La barra parte da 50 e non da 0 perche' in classifica sono tutti
  sopra la mediana, e su 0-100 verrebbero cento barre identiche. */
function pctCell(p){
 const r=p&&p.rank, tot=r&&r.n_total, pos=r&&r.TPI;
 if(!tot||!pos) return '<div class="lb-pct"><span class="pf-na">&mdash;</span></div>';
 const pc=(tot-pos)/tot*100;
 return '<div class="lb-pct">'
  +'<span class="pf-pn">'+pc.toFixed(1)+'&deg;</span>'
  +'<div class="pf-pbar"><b style="width:'+Math.max(0,(pc-50)/50*100).toFixed(1)+'%"></b></div></div>';
}
const CTX_L = __CTX_L_JS__;
const SPIEG = __SPIEG_JS__;
const TOP6  = __TOP6_JS__;
const FORTI = __FORTI_JS__;
const TEAMS = __TEAMS_JS__;
const ROSTER = __ROSTER_JS__;
const TPI_PRO_SHOWCASE = __TPI_PRO_JS__;
const CTXS = Object.keys(CTX_L);
const NTOP = __N_TOP_DIF__;
const NMIN = 4;

/* ── Stato ── */
let CUR=null, CTX="totale", TAB="ov", PQ="", PR="";
let ACTIVE_TEAMS=new Set(), CUR_METRIC="tpi", VIEW="home", COMPARE_POOL=[];
let FORM_FILTER=""; /* "" tutti | "hot" solo caldi | "cold" solo in calo */

/* ── Plotly base ── */
const PL={responsive:true,displayModeBar:false};
const BL={paper_bgcolor:"transparent",plot_bgcolor:"transparent",
 font:{color:"rgba(235,235,245,.28)",family:"-apple-system,sans-serif"},
 xaxis:{gridcolor:"rgba(255,255,255,.05)",color:"rgba(235,235,245,.28)",tickfont:{size:10},zeroline:false},
 yaxis:{gridcolor:"rgba(255,255,255,.05)",color:"rgba(235,235,245,.28)",tickfont:{size:10},zeroline:false}};

/* ── Utils ── */
const fv=(v,d=2)=>(v==null)?"\u2014":(+v).toFixed(d);
const fvs=(v,d=2)=>(v==null)?"\u2014":(v>=0?"+":"")+v.toFixed(d);
const cz=v=>(v==null)?"var(--lt)":v>=0?"var(--orng)":"var(--teal)";
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
 const icons={};
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
  const top=s.slice(0,3).map(p=>'<b>'+dispNm(p)+'</b><span style="font-family:var(--mono);font-size:10px;color:var(--lt)">'+(p.tpi.totale!=null?(p.tpi.totale>=0?"+":"")+p.tpi.totale.toFixed(2):"\u2014")+'</span>').join(" &middot; ");
  return'<div class="ts-card"><div class="ts-nm">'+esc(team)+'</div>'
   +'<div class="ts-kpis"><div class="tsk"><div class="tsk-v" style="color:'+tc+'">'+avgTpi.toFixed(2)+'</div><div class="tsk-l">TPI medio</div></div>'
   +'<div class="tsk"><div class="tsk-v" style="color:var(--blue)">'+avgOut.toFixed(3)+'</div><div class="tsk-l">Output</div></div></div>'
   +'<div class="ts-top">'+top+(extra.length?' <span style="color:var(--lq)">+'+extra.length+' altri</span>':"")+'</div></div>';
 }).join("");
}

/* ════════════════════════════════════════════════════════════
  TPI PRO SHOWCASE
════════════════════════════════════════════════════════════ */
let _tppVisible = false; /* default NASCOSTO */

function toggleTppSection(){
 const coll = document.getElementById("tpp-collapsible");
 const btn = document.getElementById("tpp-toggle");
 _tppVisible = !_tppVisible;
 if(coll) coll.style.display = _tppVisible ? "block" : "none";
  const sub=document.querySelector(".tpp-sub-collapsible");
  if(sub) sub.style.display = _tppVisible ? "block" : "none";
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
 const barLeft = v => { const b = zToBar(v); return b < 50 ? b+"%" : "50%"; };
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

    const aiiVal  = p.aii != null ? p.aii.toFixed(2) : "\u2014";
    const priVal  = p.pri != null ? p.pri.toFixed(2) : "\u2014";
    const aiiPct  = p.aii != null ? Math.round(p.aii * 100) : 0;
    const priPct  = p.pri != null ? Math.round(p.pri * 100) : 0;
    const etaTxt  = p.eta != null ? p.eta + " anni" : "";
    const aiiColor = p.aii != null ? ratioColor(p.aii) : "var(--lt)";
    const priColor = p.pri != null ? ratioColor(p.pri) : "var(--lt)";
    const rk    = p.rank_tpi_pro != null ? "#"+p.rank_tpi_pro : "\u2014";

    html += '<div class="tpp-card" onclick="if(DATA.find(x=>x.id==='+p.id+'))pick('+p.id+')">'
     +deltaBadge
     +'<div class="tpp-card-nm">'+esc(p.nome)+'</div>'
     +'<div class="tpp-card-sub">'+esc(p.squadra)
     +(etaTxt ? ' &middot; '+etaTxt : '')
     +' &middot; <span style="font-family:var(--mono);font-size:10px;color:var(--purp)">'+rk+' TPI Pro</span>'
     +'</div>'
     +'<div class="tpp-bars">'+tpiBar+tpiProBar+'</div>'
     +'<div class="tpp-kpis">'
     + '<div class="tpp-kpi">'
     +  '<div class="tpp-kpi-lbl" style="color:var(--teal)">AII &mdash; Et&agrave;</div>'
     +  '<div class="tpp-kpi-val" style="color:'+aiiColor+'">'+aiiVal+'</div>'
     +  '<div class="tpp-kpi-bar"><div class="tpp-kpi-bar-f" style="width:'+aiiPct+'%;background:var(--teal)60"></div></div>'
     + '</div>'
     + '<div class="tpp-kpi">'
     +  '<div class="tpp-kpi-lbl" style="color:var(--purp)">PRI &mdash; Fisico</div>'
     +  '<div class="tpp-kpi-val" style="color:'+priColor+'">'+priVal+'</div>'
     +  '<div class="tpp-kpi-bar"><div class="tpp-kpi-bar-f" style="width:'+priPct+'%;background:var(--purp)60"></div></div>'
     + '</div>'
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
 tpi:   {ttl:"TPI Totale", ttlKey:"dash_m_tpi", help:"TPI",     get:p=>p.tpi.totale,          fmt:v=>(v>=0?"+":"")+v.toFixed(2)},
 prospect: {
  ttl:"Giovani \u2605 — Prospect Score", ttlKey:"dash_m_prospect",
  help:"TPI",
  get:p=>{
   /* Prospect Score = TPI × AII (solo giocatori ≤ 24 anni)
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
 out: {ttl:"Output Offensivo Adj / 90'", ttlKey:"dash_m_out", help:"output_adj", get:p=>p.ctx?.totale?.output_adj,  fmt:v=>v.toFixed(3)},
 cen: {ttl:"Centralit\u00e0 Offensiva", ttlKey:"dash_m_cen",  help:"centralita", get:p=>p.ctx?.totale?.centralita,   fmt:v=>v.toFixed(1)+"%"},
 boo: {ttl:"Team Boost Ratio", ttlKey:"dash_m_boo",     help:"boost_ratio", get:p=>p.ctx?.totale?.boost_ratio,  fmt:v=>v.toFixed(2)+"\u00d7"},
 con: {ttl:"Consistenza", ttlKey:"dash_m_con",        help:"consistenza", get:p=>p.ctx?.totale?.consistenza,  fmt:v=>v.toFixed(3)},
 conv: {ttl:"G / xG \u2014 Conversion", ttlKey:"dash_m_conv", help:"conv_ratio", get:p=>p.conv?.conv_ratio,      fmt:v=>v.toFixed(2)},
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
  return'<div class="lb-row'+(i<3?' lb-top':'')+'">'
   +'<span class="lb-rank">'+(i+1)+'</span>'
   +'<div class="lb-dot" style="background:'+rc+'"></div>'
   +'<div class="lb-info">'
    +'<div class="lb-nm" title="'+esc(p.nome)+'">'+dn+wb+formBadge(p)+extra+'</div>'
    +'<div class="lb-team">'+esc(p.squadra)+' &middot; '+esc(roleName(p.ruolo))+'</div>'
   +'</div>'
   +profStrip(p)
   +pctCell(p)
   +'<div class="lb-bar-wrap"><div class="lb-bar-fill" style="width:'+barW+'%"></div></div>'
   +'<span class="lb-val">'+m.fmt(v)+'</span>'
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
    +'<div class="roster-hdr">'+esc(T("dash_roster","Resto della rosa"))+' '
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
 av.textContent=p.ruolo==="ATT"?"":p.ruolo==="DIF"?"":p.ruolo==="CEN"?"":"";
 const wb=p.is_winter?' <span class="tag tag-snow" title="Acquisto invernale: soglia minuti ridotta ('+p.first_giornata+'ª gg)">❄️ dal gg '+p.first_giornata+'</span>':"";
 document.getElementById("h-nm").innerHTML=esc(p.nome)+wb;
 document.getElementById("h-sub").innerHTML=esc(p.squadra)+" · "+(+p.minuti||0)+"' · SOS "+fv(p.kpi.sos);
 // ── Forma recente (ultime N gare) ──
 const hf=document.getElementById("h-form"), r=p.recent||{};
 if(hf){
  if(r.n>=3){
   const col=r.label==="hot"?"var(--green)":r.label==="cold"?"var(--red)":"var(--lt)";
   const ico=r.label==="hot"?"":r.label==="cold"?"":"";
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
  '<span class="tag tag-role" style="color:'+rc+'">'+esc(roleName(p.ruolo))+'</span>'
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
  {k:"z_output",   lbl:"Output Adj/90", col:"var(--ls)"},
  {k:"z_centralita", lbl:T("dash_chip_cen","Centralità"),   col:"var(--ls)"},
  {k:"z_boost",   lbl:"Team Boost",   col:"var(--ls)"},
  {k:"z_consistenza",lbl:T("dash_chip_con","Consistenza"),  col:"var(--ls)"},
 ];
 if(p.z_aii!=null)   dims.push({k:"z_aii", lbl:T("dash_aii_age","AII — Età"), col:"var(--ls)"});
 if(p.z_pri!=null)   dims.push({k:"z_pri", lbl:T("dash_pri_phys","PRI — Fisico"),col:"#ff6b9d"});

 /* Opzioni per il secondo giocatore */
 const opts=DATA.filter(x=>x.id!==id).map(x=>`<option value="${+x.id}">${esc(x.nome||x.cognome)} (${esc(x.squadra)})</option>`).join("");

 const mbox=document.getElementById("m-b");
 const mt =document.getElementById("m-t");
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
  {lbl:T("dash_m_tpi","TPI Totale"),  v1:sf(p1.tpi?.totale),   v2:sf(p2.tpi?.totale),   col:"var(--ls)"},
  {lbl:"Output Adj/90", v1:sf(p1.z_output),     v2:sf(p2.z_output),     col:"var(--blue)"},
  {lbl:T("dash_chip_cen","Centralità"),  v1:sf(p1.z_centralita),   v2:sf(p2.z_centralita),   col:"var(--ls)"},
  {lbl:"Team Boost",  v1:sf(p1.z_boost),      v2:sf(p2.z_boost),     col:"var(--ls)"},
  {lbl:T("dash_chip_con","Consistenza"),  v1:sf(p1.z_consistenza),   v2:sf(p2.z_consistenza),  col:"var(--ls)"},
 ];
 if(p1.tpi_ext?.totale!=null) dims.push({lbl:"TPI Pro", v1:sf(p1.tpi_ext?.totale), v2:sf(p2.tpi_ext?.totale), col:"var(--ls)"});
 if(p1.z_aii!=null) dims.push({lbl:T("dash_aii_age","AII — Età"),  v1:sf(p1.z_aii), v2:sf(p2.z_aii), col:"var(--ls)"});
 if(p1.z_pri!=null) dims.push({lbl:T("dash_pri_phys","PRI — Fisico"), v1:sf(p1.z_pri), v2:sf(p2.z_pri), col:"#ff6b9d"});

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
  if(tpi>=1.5)out+=dn+" d'élite: TPI "+tpi.toFixed(2)+" (top "+(pct||"?")+"% della lega). ";
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
  {k:"output_adj",l:"Output / 90'",cls:"m1",rk:r.output_adj,col:"var(--ls)",fmt:v=>v!=null?v.toFixed(3):"—"},
  {k:"centralita",l:"Centralità",cls:"m2",rk:r.centralita,col:"var(--ls)",fmt:v=>v!=null?v.toFixed(1)+"%":"—"},
  {k:"boost_ratio",l:"Team Boost",cls:"m3",rk:r.boost,col:"var(--ls)",fmt:v=>v!=null?v.toFixed(2)+"×":"N/D"},
  {k:"consistenza",l:"Consistenza",cls:"m4",rk:r.consistenza,col:"var(--ls)",fmt:v=>v!=null?v.toFixed(3):"—"},
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
  +'<div class="cv-pills"><div class="cvp"><b>'+(cv.goal_tot||0)+'</b></div>'
  +'<div class="cvp">xG <b>'+fv(cv.xg_tot,2)+'</b></div>'
  +'<div class="cvp"><b>'+(gmx!=null?(gmx>=0?"+":"")+gmx.toFixed(2):"—")+'</b> G−xG</div>'
  +'<div class="cvp"><b>'+fv(cv.goal_p90)+'</b>/90</div></div></div></div>';
 const aiBody=p.ai?p.ai:autoSynth(p,ctx);
 const aiCard='<div class="ai-card"><div class="ai-hd"><div class="ai-badge">Analisi</div></div><div class="ai-body">'+aiBody+'</div></div>';

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
  +'<div style="font-size:12px;color:var(--lt);margin-bottom:12px;line-height:1.8">'+esc(T("dash_with_player","Con il giocatore"))+' &nbsp;·&nbsp; '+esc(T("dash_vs_strong_def","vs Difese Solide"))+' ('+FORTI.join(", ")+') &nbsp;·&nbsp; &#x25A0; '+esc(T("dash_vs_weak_def","vs Difese Deboli"))+ns+'</div>'
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
 document.getElementById("p-ov").innerHTML  =buildOvHTML(p,CTX);
 document.getElementById("p-conv").innerHTML =buildConvHTML(p);
 document.getElementById("p-tr").innerHTML  =buildTrHTML(p);
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
   marker:{color:vv.map((v,i)=>nd[i]?"rgba(233,240,236,.08)":v>=0?"rgba(255,176,32,.85)":"rgba(111,180,196,.85)"),line:{width:0}},
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
   {polar:{bgcolor:"rgba(0,0,0,0)",radialaxis:{visible:true,range:[-3,3],tickvals:[-3,-2,-1,0,1,2,3],
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
let CMP_FORM=""; /* filtro forma per i dropdown confronto: "" | "hot" | "cold" */
function fillCmpSelects(){
 const pool=DATA.filter(p=>!CMP_FORM||(p.recent&&p.recent.label===CMP_FORM));
 ["cs1","cs2"].forEach((id,i)=>{
  const s=document.getElementById(id);if(!s)return;
  const prev=s.value;
  s.innerHTML="";
  pool.forEach(p=>{
   const o=document.createElement("option"),t=p.tpi.totale;
   const ico=p.recent&&p.recent.label==="hot"?" ":p.recent&&p.recent.label==="cold"?" ":"";
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
 ],{polar:{bgcolor:"rgba(0,0,0,0)",radialaxis:{visible:true,range:[-3,3],tickvals:[-3,-2,-1,0,1,2,3],tickfont:{size:9,color:"rgba(235,235,245,.2)"},gridcolor:"rgba(255,255,255,.07)"},angularaxis:{tickfont:{size:11,color:"rgba(235,235,245,.42)"},gridcolor:"rgba(255,255,255,.07)"}},margin:{t:18,b:36,l:26,r:26},height:300,paper_bgcolor:"transparent",plot_bgcolor:"transparent",font:{color:"rgba(235,235,245,.35)",family:"-apple-system"},legend:{orientation:"h",y:-.1,font:{size:10},bgcolor:"transparent"}},PL);
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
 const icons={};
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

  /* Eroe: la curva di riferimento z~N(0,1) con sopra TUTTI i giocatori del
     payload, non solo i primi dodici. E' legittima perche' il TPI e' uno
     z-score standardizzato per costruzione. I segni stanno tutti a destra
     della media perche' il payload pubblico contiene solo la testa della
     classifica: la parentesi in basso lo dice a chi guarda, senza costringerlo
     a leggere la didascalia. */
  function drawHero(){
    const box=document.getElementById("hero-curve");
    if(!box||typeof DATA==="undefined") return;
    const HERO_N_TOT=__N_GIO__;
    const EN=(document.documentElement.lang||"it").slice(0,2)==="en";
    const W=760,H=176,PAD=20,A=-3.2,B=3.2,X=v=>(v-A)/(B-A)*W,pdf=x=>Math.exp(-x*x/2);
    /* Serve anche il NOME, non solo il valore: trattini anonimi nella coda
       non dicono niente a chi guarda. */
    const all=DATA.filter(p=>p&&p.tpi&&typeof p.tpi.totale==="number"&&isFinite(p.tpi.totale))
                  .map(p=>({v:p.tpi.totale,n:p.nome}))
                  .sort((a,b)=>b.v-a.v);
    if(!all.length) return;
    const lo=all[all.length-1].v;
    let d="M 0 "+H;
    for(let i=0;i<=220;i++){const x=A+(B-A)*i/220; d+=" L "+X(x)+" "+(H-pdf(x)*(H-PAD));}
    let g='<defs><linearGradient id="hg" x1="0" y1="0" x2="0" y2="1">'
        + '<stop offset="0" stop-color="rgba(233,240,236,.14)"/>'
        + '<stop offset="1" stop-color="rgba(233,240,236,.015)"/></linearGradient></defs>'
        + '<path d="'+d+' L '+W+' '+H+' Z" fill="url(#hg)" stroke="rgba(233,240,236,.38)" stroke-width="1"/>';
    for(let k=-3;k<=3;k++) g+='<line x1="'+X(k)+'" y1="'+H+'" x2="'+X(k)+'" y2="'+(H-5)+'" stroke="rgba(233,240,236,.2)"/>';
    g+='<line x1="'+X(0)+'" y1="'+H+'" x2="'+X(0)+'" y2="'+(H-pdf(0)*(H-PAD))+'" stroke="rgba(233,240,236,.28)" stroke-dasharray="2 3"/>';
    const cog=function(nm){var q=nm.split(" ");return q[q.length-1];};
    const sgn=function(v){return (v>=0?"+":"")+v.toFixed(2);};
    /* La classifica pubblica e' i primi cento per TPI, quindi "pubblicato"
       equivale a "sopra +0.22": non serve un segno per giocatore, serve una
       soglia. Riempiamo in ambra la fetta a destra del taglio e lasciamo grigio
       il resto. Cento tacche dicevano la stessa cosa sporcando l'asse. */
    var cut=X(lo);
    var dc="M "+cut+" "+H;
    for(let i=0;i<=160;i++){const x=lo+(B-lo)*i/160; dc+=" L "+X(x)+" "+(H-pdf(x)*(H-PAD));}
    g+='<path d="'+dc+' L '+W+' '+H+' Z" fill="rgba(255,176,32,.16)"/>'
     + '<line x1="'+cut+'" y1="'+H+'" x2="'+cut+'" y2="'+(H-pdf(lo)*(H-PAD))+'" '
     + 'stroke="rgba(255,176,32,.55)" stroke-width="1"/>'
     + '<text x="'+(cut-4)+'" y="'+(H-8)+'" text-anchor="end" fill="rgba(255,176,32,.7)" '
     + 'font-family="JetBrains Mono, monospace" font-size="9">'+sgn(lo)+'</text>';
    /* I primi tre col nome. Chi cade a meno di 6px dal precedente finisce nello
       stesso gruppo: a questa scala +1.38 e +1.39 sono lo stesso punto, e due
       pallini appiccicati fingevano una distanza che non c'e'. Un marcatore
       solo, due etichette. */
    var groups=[];
    all.slice(0,3).forEach(function(o){
      var x=X(o.v),last=groups[groups.length-1];
      if(last && Math.abs(x-last.x)<6) last.items.push(o);
      else groups.push({x:x,y:H-pdf(o.v)*(H-PAD),items:[o]});
    });
    var li=0;
    groups.forEach(function(gr){
      g+='<g><title>'+gr.items.map(function(o){return cog(o.n)+" "+sgn(o.v);}).join(" &middot; ")+'</title>'
       + '<line x1="'+gr.x+'" y1="'+H+'" x2="'+gr.x+'" y2="'+gr.y+'" stroke="#FFB020" stroke-width="1.3"/>'
       + '<circle cx="'+gr.x+'" cy="'+gr.y+'" r="3" fill="#FFB020"/></g>';
      gr.items.forEach(function(o){
        var ly=gr.y-10-li*14; li++;
        g+='<line x1="'+gr.x+'" y1="'+(gr.y-4)+'" x2="'+gr.x+'" y2="'+(ly+3)+'" stroke="rgba(255,176,32,.45)" stroke-width="1"/>'
         + '<text x="'+(gr.x+5)+'" y="'+ly+'" fill="#FFB020" font-family="JetBrains Mono, monospace" '
         + 'font-size="10">'+cog(o.n)+' '+sgn(o.v)+'</text>';
      });
    });
    /* Le due aree, dette a parole. Risponde a colpo d'occhio a "non c'e'
       nessuno sotto la media?". */
    var ty=192;
    g+='<text x="'+((cut+W)/2)+'" y="'+ty+'" text-anchor="middle" fill="rgba(255,176,32,.85)" '
     + 'font-family="JetBrains Mono, monospace" font-size="9.5" letter-spacing=".5">'
     + (EN?('THE '+all.length+' PUBLISHED'):('I '+all.length+' PUBBLICATI'))+'</text>'
     + '<text x="'+(cut/2)+'" y="'+ty+'" text-anchor="middle" fill="rgba(233,240,236,.34)" '
     + 'font-family="JetBrains Mono, monospace" font-size="9.5" letter-spacing=".5">'
     + (EN?('THE OTHER '+(HERO_N_TOT-all.length)+' ARE NOT HERE')
          :('GLI ALTRI '+(HERO_N_TOT-all.length)+' NON SONO QUI'))+'</text>';
    box.innerHTML=g;
  }
  drawHero();
  document.addEventListener("i18n:changed", drawHero);

  </script>
<!-- ── Watermark ── -->
<div id="wm">
 <span id="wm-dot"></span>
 <span id="wm-text">Raffaele Ciccone &thinsp;&middot;&thinsp; Serie A Scout Index &thinsp;&middot;&thinsp; 2025&thinsp;/&thinsp;26</span>
</div>
<style>
#wm{
 position:fixed;bottom:0;left:0;right:0;
 display:flex;align-items:center;justify-content:center;gap:8px;
 padding:7px 12px;
 background:var(--bg);
 border:0;
 border-top:1px solid var(--sep);
 border-radius:0;
 box-shadow:none;
 z-index:800;pointer-events:none;
 transition:opacity .3s;
}
#wm:hover{opacity:.4}
#wm-dot{
 width:3px;height:10px;border-radius:1px;
 background:var(--orng);
 box-shadow:none;
 flex-shrink:0;
}
#wm-text{
 font-family:var(--mono);
 font-size:9px;font-weight:400;letter-spacing:.14em;text-transform:uppercase;
 color:var(--lq);white-space:nowrap;
}
@media(max-width:768px){
 #wm{display:none}
}
</style>

</body>
</html>"""


# ════════════════════════════════════════════════════════════════
# 5. GENERAZIONE HTML
# ════════════════════════════════════════════════════════════════
def inject_data(template: str, meta: dict) -> str:
  payload   = meta["players"]
  n_top_dif  = meta.get("n_top_difese", 6)
  n_gio    = meta.get("n_giocatori", len(payload))
  n_gior   = meta.get("n_giornate", "?")
  top6_names = meta.get("top6_names", [])
  forti_names = meta.get("forti_names", [])
  roster   = meta.get("roster", [])
  teams    = sorted(set(clean(p["squadra"]) for p in payload))

  def jsdump(obj) -> str:
    return json.dumps(deep_clean(obj), ensure_ascii=True)

  replacements = {
    "__STYLE__":    CSS_PATH.read_text(encoding="utf-8").rstrip("\n"),
    "__DATA_JS__":   jsdump(payload),
    "__RC_JS__":    jsdump(RUOLO_COLORS),
    "__RL_JS__":    jsdump(RUOLO_LABELS),
    "__CTX_L_JS__":   jsdump(CTX_LABELS),
    "__SPIEG_JS__":   jsdump(SPIEGAZIONI),
    "__TOP6_JS__":   jsdump([clean(n) for n in top6_names]),
    "__FORTI_JS__":   jsdump([clean(n) for n in forti_names]),
    "__TEAMS_JS__":   jsdump(teams),
    "__ROSTER_JS__":  jsdump(roster),
    "__TPI_PRO_JS__":  jsdump(meta.get("tpi_pro_showcase", [])),
    "__N_TOP_DIF__":  str(n_top_dif),
    "__N_GIO__":    str(n_gio),
    "__N_GIOR__":    str(n_gior),
    # Quanti giocatori finiscono davvero nell'HTML: e' il numero che l'eroe
    # deve dichiarare, non n_gio (il payload pubblico e' solo la testa).
    "__N_PUB__":    str(len(payload)),
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
  log.info("PARTE 2 — Dashboard HTML v4.2 Serie A 25/26")
  log.info("=" * 60)

  payload_path = Path(args.payload) if args.payload else PAYLOAD_PATH
  html_out   = Path(args.output) if args.output else HTML_OUT

  meta = load_payload(payload_path)

  log.info("Iniezione dati nel template...")
  html = inject_data(HTML_TEMPLATE, meta)

  html_out.parent.mkdir(parents=True, exist_ok=True)
  with open(html_out, "wb") as fh:
    fh.write(html.encode("utf-8", errors="replace"))

  size_kb = html_out.stat().st_size // 1024
  log.info(f"✓ Dashboard: {html_out} ({size_kb} KB)")
  log.info(f" Apri: file:///{str(html_out).replace(chr(92), '/')}")

  if DEMO_DIR.is_dir() and html_out.name == HTML_OUT.name:
    demo_copy = DEMO_DIR / html_out.name
    try:
      demo_copy.write_bytes(html_out.read_bytes())
      log.info(f"✓ Copia demo: {demo_copy}")
    except OSError as e:
      log.warning(f"Copia demo fallita: {e}")

  # i18n.js e ai_chat.js devono stare ACCANTO all'HTML (i loro <script src>
  # sono relativi): li copio dal repo demo (fonte canonica) in dashboard_output,
  # così la pagina aperta da lì non perde né le traduzioni (404 su i18n.js =
  # mix di lingue) né l'assistente AI.
  if html_out.name == HTML_OUT.name:
    for _asset in ("i18n.js", "ai_chat.js"):
      _src = DEMO_DIR / _asset
      if not _src.is_file():
        continue
      try:
        (OUTPUT_DIR / _asset).write_bytes(_src.read_bytes())
        log.info(f"✓ {_asset} → {OUTPUT_DIR / _asset}")
      except OSError as e:
        log.warning(f"Copia {_asset} fallita: {e}")

    # Stesso discorso per i font: le @font-face nel CSS puntano a fonts/*.woff2
    # con path relativo. Senza questa copia la dashboard aperta da
    # dashboard_output ricade sui fallback di sistema.
    _fonts_src = DEMO_DIR / "fonts"
    if _fonts_src.is_dir():
      try:
        _fonts_dst = OUTPUT_DIR / "fonts"
        _fonts_dst.mkdir(exist_ok=True)
        for _f in _fonts_src.glob("*.woff2"):
          (_fonts_dst / _f.name).write_bytes(_f.read_bytes())
        log.info(f"✓ fonts/ → {_fonts_dst}")
      except OSError as e:
        log.warning(f"Copia fonts fallita: {e}")

  # Payload JSON copiati nel repo demo: necessari per dashboard_pro.html
  # che li carica via fetch (a differenza della dashboard pubblica che li
  # embedda nell'HTML). Copio sia il payload corrente che eventuali backfill.
  if DEMO_DIR.is_dir():
    payload_files = ["payload.json", "payload_2024-25.json"]
    for fname in payload_files:
      src = OUTPUT_DIR / fname
      if src.is_file():
        try:
          (DEMO_DIR / fname).write_bytes(src.read_bytes())
          log.info(f"✓ Payload demo: {DEMO_DIR / fname}")
        except OSError as e:
          log.warning(f"Copia {fname} fallita: {e}")

  log.info("=" * 60)


if __name__ == "__main__":
  main()
