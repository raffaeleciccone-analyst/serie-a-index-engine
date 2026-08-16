"""Genera `index.html`, la porta d'ingresso del sito.

Legge `payload.json` (la classifica) e `validazione_dati.json` (i risultati
delle verifiche) e scrive la pagina. Stessa regola delle altre: **ogni numero
visibile arriva dai dati**. Prima la homepage era scritta a mano con dentro
otto giocatori e i loro punteggi, che si aggiornavano lanciando uno script e
incollando il risultato: bastava dimenticarsene una volta e la vetrina del sito
mostrava una classifica vecchia.

Uso:  python pagina_home.py
"""
from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from pagina_stile import _f, bi, el, evidenza, guscio

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("pagina_home")

BASE_DIR = Path(__file__).parent
OUTPUT_DIR = BASE_DIR / "dashboard_output"
DEMO_DIR = Path(os.environ.get("SERIE_A_DEMO_DIR", BASE_DIR.parent / "serie-a-scout-demo"))

# Le due dimensioni che spiegano *perche'* un giocatore e' in cima: il totale da
# solo dice la posizione e non il motivo.
DIM = {"output_adj": ("output", "output"), "centralita": ("centralit&agrave;", "centrality"),
       "boost": ("boost", "boost"), "consistenza": ("consistenza", "consistency"),
       "conv": ("G/xG", "G/xG")}
N_CLASSIFICA = 8


# ══════════════════════════════════════════════════════════════════
# Blocchi
# ══════════════════════════════════════════════════════════════════
def _hero(pay: dict, val: dict) -> str:
    meta = (val or {}).get("meta", {})
    n_gio = pay.get("n_giocatori")
    cifre = [(str(n_gio), "giocatori qualificati", "qualified players"),
             ("7", "dimensioni nel punteggio", "dimensions in the score")]
    if meta.get("n_verifiche"):
        cifre.append((str(meta["n_verifiche"]), "verifiche pubblicate", "checks published"))
    box = "".join(f'<div class="cifra"><b>{v}</b><span {bi(li, le)}>{li}</span></div>'
                  for v, li, le in cifre)
    # Il confronto e' DENTRO il ruolo, e va detto qui e non in fondo alla
    # validazione: e' la differenza fra "quanto incide" e "quanto incide per uno
    # che gioca li'". Misurato: standardizzando sulla lega la top 10 diventa
    # dieci attaccanti e spariscono Dimarco, Cambiaso, Wesley, McTominay - cioe'
    # i nomi per cui uno scout aprirebbe il sito.
    lede_it = (f"Un indice che ordina i giocatori di Serie A per <strong>quanto incidono in "
               f"attacco rispetto al proprio ruolo</strong>: cos&igrave; un terzino che spinge "
               f"non sparisce dietro i centravanti. xG e xA corretti per la difficolt&agrave; "
               f"dell&rsquo;avversario, sette dimensioni, una graduatoria sola. "
               f"&Egrave; descrittivo &mdash; ordina, non predice &mdash; e le verifiche "
               f"dicono anche dove perde.")
    lede_en = (f"An index that ranks Serie A players by <strong>how much they contribute in "
               f"attack relative to their own role</strong>: so an attacking full-back does not "
               f"vanish behind the strikers. xG and xA adjusted for opponent difficulty, seven "
               f"dimensions, one ranking. It is descriptive &mdash; it ranks, it does not "
               f"predict &mdash; and the checks also say where it loses.")
    # Le tre cifre escono dalla colonna di testo e diventano il terzo figlio
    # della griglia: sotto i 1500px restano dove stavano (grid-column 2, sotto
    # il sommario), sopra vanno nella fascia vuota a destra. Il markup e' uno
    # solo perche' due varianti dello stesso eroe divergerebbero al primo
    # ritocco, come divergevano le tre nav.
    return f"""<header class="hero riga">
  <div></div>
  <div>
  <div class="eyebrow">Raffaele Ciccone &middot; Football analytics</div>
  <h1 {bi("Serie A<br><em>Scout Index</em>", "Serie A<br><em>Scout Index</em>")}>Serie A<br><em>Scout Index</em></h1>
  <p class="lede" {bi(lede_it, lede_en)}>{lede_it}</p>
  </div>
  <div class="cifre">{box}</div>
</header>"""


def _porte(pay: dict, val: dict) -> str:
    """I quattro tasti d'ingresso, subito sotto il titolo.

    Prima le altre pagine si raggiungevano solo dalle voci piccole della nav,
    da qualche link di coda dentro la prosa e da un capitolo in fondo alla
    homepage: chi arrivava qui doveva leggere tutto per scoprire che il sito
    ha altre quattro pagine. Sono l'unico blocco in scatola della pagina, ed
    e' voluto: qui non si legge, si sceglie dove andare.
    """
    n_gio = pay.get("n_giocatori")
    # Quanti ne pubblica davvero la dashboard, non quanti ne qualifica il
    # motore: il payload ne dichiara 381 e ne porta 100, e la homepage
    # prometteva i 381. Su un sito che vende onesta' era l'unica pagina
    # che diceva il falso.
    n_pub = len(pay.get("players") or [])
    n_ver = ((val or {}).get("meta") or {}).get("n_verifiche")
    d_cla = (f"I primi {n_pub} dei {n_gio} qualificati, con filtri e confronto"
             if n_pub and n_gio else "La classifica, con filtri e confronto")
    d_cla_en = (f"The top {n_pub} of {n_gio} qualified, with filters and head-to-head"
                if n_pub and n_gio else "The ranking, with filters and head-to-head")
    d_val = (f"Le {n_ver} verifiche, compresa quella che perde"
             if n_ver else "Le verifiche, compresa quella che perde")
    d_val_en = (f"The {n_ver} checks, including the one it loses"
                if n_ver else "The checks, including the one it loses")
    voci = [
        ("dashboard_serie_a.html", "Classifica", "Ranking", d_cla, d_cla_en, True),
        ("validazione.html", "Validazione", "Validation", d_val, d_val_en, False),
        ("guida_completa.html", "Metodo", "Method",
         "Le formule, una per una, con un esempio calcolato",
         "The formulas, one by one, with a worked example", False),
        ("dashboard_pro.html", "TPI Pro", "TPI Pro",
         "L&rsquo;indice con i cinque modulatori scout",
         "The index with the five scout modulators", False),
    ]
    carte = "".join(f"""<a class="porta{' pri' if pri else ''}" href="{h}">
    <span class="porta-t" {bi(t_it, t_en)}>{t_it}</span>
    <span class="porta-d" {bi(d_it, d_en)}>{d_it}</span>
  </a>""" for h, t_it, t_en, d_it, d_en, pri in voci)
    return f"""<nav class="porte riga" aria-label="Le pagine del sito">
  <div class="porte-lbl" {bi("Vai a", "Go to")}>Vai a</div>
  <div class="porte-g">{carte}</div>
</nav>"""


def _classifica(pay: dict) -> str:
    players = sorted(pay.get("players", []),
                     key=lambda p: -(p.get("tpi") or {}).get("totale", -99))[:N_CLASSIFICA]
    if not players:
        return ""
    top = players[0]["tpi"]["totale"]
    righe = []
    for i, p in enumerate(players, 1):
        r = p.get("rank") or {}
        best = sorted(((k, v) for k, v in r.items() if k in DIM and isinstance(v, int)),
                      key=lambda kv: kv[1])[:2]
        perche_it = " &middot; ".join(f"#{v} {DIM[k][0]}" for k, v in best)
        perche_en = " &middot; ".join(f"#{v} {DIM[k][1]}" for k, v in best)
        larg = max(6.0, p["tpi"]["totale"] / top * 100)
        righe.append(f"""<a class="cl-row" href="dashboard_serie_a.html">
  <span class="cl-n">{i:02d}</span>
  <span class="cl-id"><b>{p['nome']}</b><small>{p['squadra']}</small>
    <em {bi(perche_it, perche_en)}>{perche_it}</em></span>
  <span class="cl-bar"><i style="width:{larg:.0f}%"></i></span>
  <span class="cl-v">{p['tpi']['totale']:+.2f}</span>
</a>""")
    resto = len(pay.get("players") or []) - len(players)
    coda_it = f"e altri {resto} giocatori nella dashboard"
    coda_en = f"and {resto} more players in the dashboard"
    p_it = ("I primi otto per TPI totale. Sotto ogni nome ci sono le due dimensioni in cui "
            "quel giocatore sta pi&ugrave; in alto: dicono <em>perch&eacute;</em> &egrave; l&igrave;, "
            "che il punteggio da solo non dice.")
    p_en = ("The top eight by total TPI. Under each name are the two dimensions where that "
            "player ranks highest: they say <em>why</em> he is there, which the score alone "
            "does not.")
    return f"""<section class="cap riga">
  <div class="cap-num">01</div>
  <div>
    {el("h2", "Chi c&rsquo;&egrave; in cima adesso", "Who is on top right now")}
    <p class="prosa" {bi(p_it, p_en)}>{p_it}</p>
    <div class="clas">{"".join(righe)}</div>
    <a class="oltre" href="dashboard_serie_a.html" {bi(coda_it, coda_en)}>{coda_it}</a>
  </div>
</section>"""


def _costruzione(pay: dict) -> str:
    ctx = len((pay.get("players") or [{}])[0].get("tpi", {})) or 5
    ev = [
        evidenza("7", "Dimensioni", "Dimensions",
                 "Output, buildup, centralit&agrave;, boost, consistenza, finishing e forma "
                 "recente. Ognuna &egrave; uno z-score winsorizzato, cos&igrave; una partita "
                 "fuori scala non trascina la graduatoria.",
                 "Output, buildup, centrality, boost, consistency, finishing and recent form. "
                 "Each is a winsorised z-score, so a single off-the-charts match does not drag "
                 "the ranking."),
        evidenza("SOS", "Difficolt&agrave; dell&rsquo;avversario", "Opponent difficulty",
                 "Ogni prestazione &egrave; pesata per la forza di chi c&rsquo;era "
                 "dall&rsquo;altra parte: segnare al Como e segnare all&rsquo;Inter non valgono "
                 "uguale.",
                 "Every performance is weighted by the strength of the opposition: scoring "
                 "against Como and scoring against Inter are not worth the same."),
        evidenza("Bayes", "Chi ha giocato poco", "Players with few minutes",
                 "Uno shrinkage tira i punteggi verso la media del ruolo in proporzione ai "
                 "minuti: cos&igrave; chi ha fatto due partite buone non finisce davanti a chi "
                 "regge da trenta.",
                 "A shrinkage pulls scores towards the role average in proportion to minutes "
                 "played: two good matches do not put a player ahead of someone who has "
                 "delivered for thirty."),
        evidenza(str(ctx), "Contesti", "Contexts",
                 "Totale, casa, trasferta, contro le prime sei e contro le difese pi&ugrave; "
                 "solide. Lo stesso giocatore ha cinque punteggi, e la differenza fra loro "
                 "&egrave; informazione.",
                 "Overall, home, away, against the top six and against the tightest defences. "
                 "The same player gets five scores, and the gap between them is information."),
        evidenza("+5", "Modulatori scout", "Scout modulators",
                 "Il <strong>TPI Pro</strong> aggiunge et&agrave;, tenuta fisica, stabilit&agrave; "
                 "fra contesti, direzione della forma e produzione sopra l&rsquo;attesa. Serve a "
                 "leggere il profilo di un giocatore, non a prevederne il rendimento.",
                 "The <strong>TPI Pro</strong> adds age, physical durability, cross-context "
                 "stability, form direction and above-expectation production. It helps read a "
                 "player&rsquo;s profile, not forecast his output."),
    ]
    p_it = ("Il punteggio non &egrave; una media di statistiche grezze. Ogni pezzo risponde a "
            "un problema preciso di chi guarda i numeri del calcio.")
    p_en = ("The score is not an average of raw statistics. Each piece answers a specific "
            "problem you hit when you look at football numbers.")
    return f"""<section class="cap riga">
  <div class="cap-num">02</div>
  <div>
    {el("h2", "Come &egrave; costruito", "How it is built")}
    <p class="prosa" {bi(p_it, p_en)}>{p_it}</p>
    <div class="ev-g">{"".join(ev)}</div>
    <a class="oltre" href="guida_completa.html" {bi("Le formule, una per una", "The formulas, one by one")}>Le formule, una per una</a>
  </div>
</section>"""


def _quanto_regge(val: dict) -> str:
    if not val:
        return ""
    m, l, q = (val.get(k) or {} for k in ("m", "l", "q"))
    ev = []
    if m.get("monotonia_rho") is not None:
        ev.append(evidenza(
            f'&rho; {_f(m["monotonia_rho"], 3)}', "L&rsquo;ordine tiene", "The order holds",
            "Diviso in dieci gruppi, l&rsquo;indice li dispone nell&rsquo;ordine del rendimento "
            "realizzato in campo.",
            "Split into ten groups, the index lines them up in the order of output actually "
            "produced on the pitch."))
    pv = [r for r in l.get("per_vintage", []) if r.get("spearman_rho") is not None]
    if pv:
        ev.append(evidenza(
            f'&rho; {_f(pv[0]["spearman_rho"], 3)}',
            f"Gi&agrave; alla giornata {pv[0]['vintage_giornata']}",
            f"Already by matchday {pv[0]['vintage_giornata']}",
            "A met&agrave; stagione la graduatoria &egrave; gi&agrave; quasi quella di fine "
            "anno: non serve aspettare maggio per usarla.",
            "Halfway through the season the ranking is already close to the final one: you do "
            "not have to wait for May to use it."))
    liv = (q.get("criteri") or {}).get("livello", {}).get("baselines", [])
    b_out = next((b for b in liv if b["key"] == "output_grezzo"), None)
    if b_out is not None and not b_out.get("tpi_better"):
        ev.append(evidenza(
            _f(b_out["delta_rmse"], 4, True), "E dove perde", "And where it loses",
            f"Sul rendimento <em>futuro</em>, contro l&rsquo;output grezzo per-90, il TPI "
            f"<strong>non vince</strong> ({q.get('n')} confronti fuori campione). Sta scritto "
            f"nella pagina delle verifiche, con l&rsquo;intervallo di confidenza accanto.",
            f"On <em>future</em> output, against raw per-90 output, the TPI <strong>does not "
            f"win</strong> ({q.get('n')} out-of-sample comparisons). It is written on the "
            f"validation page, with the confidence interval next to it."))
    if not ev:
        return ""
    p_it = ("Un indice si giudica da quello che regge quando lo si mette alla prova, non da "
            "quanto suona bene. Le verifiche sono pubblicate per intero, comprese quelle "
            "costruite per bocciarlo.")
    p_en = ("An index is judged by what survives testing, not by how good it sounds. The checks "
            "are published in full, including the ones built to fail it.")
    return f"""<section class="cap riga">
  <div class="cap-num">03</div>
  <div>
    {el("h2", "Quanto regge", "How well it holds")}
    <p class="prosa" {bi(p_it, p_en)}>{p_it}</p>
    <div class="ev-g">{"".join(ev)}</div>
    <a class="oltre" href="validazione.html" {bi("Tutte le verifiche, come sono uscite", "Every check, exactly as it came out")}>Tutte le verifiche, come sono uscite</a>
  </div>
</section>"""


# ══════════════════════════════════════════════════════════════════
# Pagina
# ══════════════════════════════════════════════════════════════════
CSS_EXTRA = """
/* Classifica in apertura: numero, nome, barra, punteggio. La barra e' larga in
   proporzione al primo, e serve a far vedere le distanze senza leggerle. */
.clas{margin-top:6px;border-top:1px solid var(--sep2)}
.cl-row{display:grid;grid-template-columns:30px minmax(0,1fr) 110px 62px;gap:0 16px;
  align-items:center;padding:11px 0;border-bottom:1px solid var(--sep);
  text-decoration:none;color:inherit}
.cl-row:hover{background:rgba(255,176,32,.045)}
.cl-n{font-family:var(--mono);font-size:11.5px;color:var(--lt)}
.cl-id b{font-weight:600;color:var(--lp);font-size:15px}
.cl-id small{margin-left:9px;font-size:11.5px;color:var(--lt)}
.cl-id em{display:block;font-style:normal;font-size:11.5px;color:var(--lt);margin-top:3px}
.cl-bar{height:4px;background:rgba(233,240,236,.08);border-radius:2px;overflow:hidden}
.cl-bar i{display:block;height:100%;background:var(--orng);opacity:.75}
.cl-v{font-family:var(--mono);font-size:14px;color:var(--orng);text-align:right}
.oltre{display:inline-block;margin-top:22px;font-size:13.5px;color:var(--ls);
  text-decoration:none;border-bottom:1px solid var(--lq);padding-bottom:2px}
.oltre:hover{color:var(--orng);border-color:var(--orng)}
.oltre::after{content:" \\2192"}

/* I quattro tasti d'ingresso. Sono le uniche scatole del sito: il resto delle
   pagine mette i numeri sul filo del testo apposta, ma un tasto deve avere un
   bordo per farsi riconoscere come tasto. Restano dentro le due colonne di
   tutto il resto — l'etichetta nel margine, i tasti nella colonna di lettura.

   L'eroe qui sotto si stringe di una trentina di pixel rispetto alle altre
   pagine, e solo qui: con l'aria di prima i tasti cadevano sul bordo basso di
   un portatile da 900px, cioe' esattamente dove non li vede chi arriva. */
.hero{padding-bottom:clamp(22px,3vw,30px)}
.cifre{margin-top:32px;padding-top:18px}
.porte{padding:0 0 clamp(34px,5.5vw,50px);align-items:start}
.porte-lbl{font-size:10.5px;letter-spacing:.24em;text-transform:uppercase;
  color:var(--lt);padding-top:15px}
.porte-g{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px}
.porta{display:flex;flex-direction:column;gap:7px;min-height:98px;
  padding:14px 14px 13px;border:1px solid var(--sep2);border-radius:10px;
  background:var(--bg1);text-decoration:none;color:inherit;
  transition:border-color .16s,background .16s}
.porta:hover{border-color:rgba(255,176,32,.55);background:var(--bg2)}
.porta-t{display:flex;align-items:baseline;justify-content:space-between;gap:8px;
  font-family:var(--disp);font-weight:500;font-size:17.5px;text-transform:uppercase;
  letter-spacing:.02em;line-height:1;color:var(--lp)}
.porta-t::after{content:"\\2192";font-family:var(--font);font-size:13px;font-weight:400;
  color:var(--orng);opacity:.42;transition:opacity .16s}
.porta:hover .porta-t::after{opacity:1}
.porta-d{font-size:12px;line-height:1.5;color:var(--lt)}
/* La classifica e' quello che il visitatore e' venuto a vedere: e' l'unico dei
   quattro a portare l'ambra, come la voce Pro nella nav. */
.porta.pri{border-color:rgba(255,176,32,.42)}
.porta.pri .porta-t{color:var(--orng)}
.porta.pri .porta-t::after{opacity:.8}

@media(max-width:900px){
  .cl-row{grid-template-columns:26px minmax(0,1fr) 56px;gap:0 12px}
  .cl-bar{display:none}
  .porte-g{grid-template-columns:repeat(2,minmax(0,1fr))}
  .porte-lbl{padding:0 0 10px}
}
@media(max-width:460px){
  .porte-g{grid-template-columns:minmax(0,1fr)}
  .porta{min-height:0}
}

/* Sopra i 1500px il grosso lo fa pagina_stile.CSS, che allarga il contenitore,
   sposta le cifre a fianco del titolo e manda le voci dei capitoli su due
   colonne: quello vale per tutte e tre le pagine di testo. Qui restano i due
   pezzi che esistono solo in homepage. */
@media(min-width:1500px){
  /* I tasti respirano invece di limitarsi a diventare piu' larghi. */
  .porta{min-height:112px;padding:17px 17px 16px;gap:9px}
  .porta-t{font-size:19px}
  .porta-d{font-size:12.5px}

  /* Nella classifica lo spazio in piu' va alla barra, che e' l'unica cosa
     della riga che con piu' larghezza dice davvero di piu': e' lunga in
     proporzione al primo, e serve a far vedere le distanze senza leggerle. */
  .cl-row{grid-template-columns:34px minmax(0,1fr) 300px 74px;gap:0 22px}
  .cl-bar{height:5px}
}
"""


def render(pay: dict, val: dict | None) -> str:
    corpo = "\n".join(x for x in (_hero(pay, val or {}), _porte(pay, val or {}),
                                  _classifica(pay), _costruzione(pay),
                                  _quanto_regge(val or {})) if x)
    html = guscio(
        "Serie A Scout Index &mdash; Raffaele Ciccone",
        "Un indice descrittivo che ordina i giocatori di Serie A per impatto offensivo, "
        "con tutte le verifiche pubblicate.",
        "A descriptive index ranking Serie A players by attacking impact, with every check "
        "published.",
        "index.html", corpo,
        [("validazione.html", "Come &egrave; stato verificato", "How it was checked"),
         ("guida_completa.html", "Come &egrave; costruito", "How it is built")])
    return html.replace("</style>", CSS_EXTRA + "</style>")


def main() -> None:
    pay_path = OUTPUT_DIR / "payload.json"
    if not pay_path.is_file():
        pay_path = DEMO_DIR / "payload.json"
    if not pay_path.is_file():
        log.error("payload.json non trovato: esegui prima parte1_analisi.py")
        return
    pay = json.loads(pay_path.read_text(encoding="utf-8"))
    val_path = OUTPUT_DIR / "validazione_dati.json"
    val = json.loads(val_path.read_text(encoding="utf-8")) if val_path.is_file() else None
    if val is None:
        log.warning("validazione_dati.json assente: la homepage esce senza i numeri "
                    "delle verifiche. Esegui parte3_valida_tpi.py.")
    html = render(pay, val)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUTPUT_DIR / "index.html"
    out.write_text(html, encoding="utf-8")
    log.info(f"OK → {out}  ({len(html)//1024} KB)")
    if DEMO_DIR.is_dir():
        (DEMO_DIR / "index.html").write_text(html, encoding="utf-8")
        log.info(f"OK → {DEMO_DIR / 'index.html'}  (copia per repo demo)")


if __name__ == "__main__":
    main()
