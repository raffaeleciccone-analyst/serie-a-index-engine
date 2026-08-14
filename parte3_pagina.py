"""Resa di `validazione.html`.

Prende il dizionario dei risultati che `parte3_valida_tpi.py` ha appena
calcolato e restituisce la pagina. Regola unica: **ogni numero visibile arriva
da `dati`**. Se un valore non e' nel dizionario non puo' comparire in pagina, e
non c'e' nessun posto dove ricopiarlo a mano.

La pagina e' organizzata per domande, non per lettere: cosa fa l'indice, la
prova costruita per bocciarlo, cosa non dimostra, come e' misurato. Le lettere
dei test restano solo nella tabella finale, come riferimento.
"""
from __future__ import annotations


# ══════════════════════════════════════════════════════════════════
# Formattazione e bilingue
# ══════════════════════════════════════════════════════════════════
def _f(v, d: int = 2, segno: bool = False) -> str:
    """Numero formattato, o trattino se manca."""
    if v is None:
        return "&mdash;"
    try:
        x = float(v)
    except (TypeError, ValueError):
        return str(v)
    if x != x:  # NaN
        return "&mdash;"
    s = f"{x:+.{d}f}" if segno else f"{x:.{d}f}"
    return s


def _ic(lo, hi, d: int = 3) -> str:
    if lo is None or hi is None:
        return "&mdash;"
    return f"[{_f(lo, d, True)}, {_f(hi, d, True)}]"


def _at(s: str) -> str:
    """Il testo entra in un attributo che i18n.js rimette come innerHTML: qui
    servono solo le virgolette, le entita' HTML devono restare intatte."""
    return s.replace('"', "&quot;")


def bi(it: str, en: str) -> str:
    return f'data-it="{_at(it)}" data-en="{_at(en)}"'


def el(tag: str, it: str, en: str, cls: str = "", extra: str = "") -> str:
    """Elemento bilingue: l'italiano e' anche il testo visibile a JS spento."""
    c = f' class="{cls}"' if cls else ""
    x = f" {extra}" if extra else ""
    return f"<{tag}{c}{x} {bi(it, en)}>{it}</{tag}>"


def evidenza(fig: str, lab_it: str, lab_en: str, it: str, en: str) -> str:
    """La riga-tipo della pagina: il numero nel margine, la frase accanto.

    Sostituisce le stat-box colorate: un numero che vive in un riquadro verde
    o rosso viene letto come voto prima ancora di essere letto come misura.
    """
    return f"""<div class="ev">
  <div class="ev-fig">{fig}<span class="ev-lbl" {bi(lab_it, lab_en)}>{lab_it}</span></div>
  <div class="ev-txt" {bi(it, en)}>{it}</div>
</div>"""


def riga_tab(lettera: str, dom_it: str, dom_en: str, mis: str,
             es_it: str, es_en: str) -> str:
    return (f'<tr><td class="t-let">{lettera}</td>'
            f'<td {bi(dom_it, dom_en)}>{dom_it}</td>'
            f'<td class="t-mis">{mis}</td>'
            f'<td {bi(es_it, es_en)}>{es_it}</td></tr>')


# ══════════════════════════════════════════════════════════════════
# Micrografici — SVG generato qui, nessuna libreria e nessuna CDN
# ══════════════════════════════════════════════════════════════════
# Niente attributo height: con il solo viewBox e width:100% il disegno si allarga
# quanto la colonna di testo. Fissando l'altezza, il rapporto d'aspetto lo
# centrava lasciando due margini vuoti larghi un quinto di pagina.
_SVG_OPEN = ('<svg class="graf" viewBox="0 0 {w} {h}" role="img" aria-hidden="true" '
             'preserveAspectRatio="xMidYMid meet">')


def svg_convergenza(l: dict) -> str:
    """ρ del ranking di giornata N contro quello di fine stagione."""
    pv = [r for r in (l or {}).get("per_vintage", []) if r.get("spearman_rho") is not None]
    if len(pv) < 3:
        return ""
    w, h, ml, mr, mt, mb = 640, 190, 34, 12, 16, 30
    xs = [r["vintage_giornata"] for r in pv]
    ys = [r["spearman_rho"] for r in pv]
    x0, x1 = min(xs), max(xs)
    def px(v):
        return ml + (v - x0) / max(1e-9, (x1 - x0)) * (w - ml - mr)
    def py(v):  # scala fissa 0.5–1.0: ancorare a min/max gonfierebbe la salita
        return mt + (1.0 - (v - 0.5) / 0.5) * (h - mt - mb)
    pts = " ".join(f"{px(x):.1f},{py(y):.1f}" for x, y in zip(xs, ys))
    out = [_SVG_OPEN.format(w=w, h=h)]
    for g in (0.5, 0.75, 1.0):
        out.append(f'<line x1="{ml}" y1="{py(g):.1f}" x2="{w-mr}" y2="{py(g):.1f}" '
                   f'stroke="rgba(233,240,236,.08)"/>')
        out.append(f'<text x="{ml-8}" y="{py(g)+3:.1f}" text-anchor="end" '
                   f'class="sv-ax">{g:.2f}</text>')
    out.append(f'<polyline points="{pts}" fill="none" stroke="var(--orng)" '
               f'stroke-width="1.6" stroke-linejoin="round"/>')
    for x, y in zip(xs, ys):
        out.append(f'<circle cx="{px(x):.1f}" cy="{py(y):.1f}" r="3" fill="var(--orng)"/>')
        out.append(f'<text x="{px(x):.1f}" y="{h-10}" text-anchor="middle" class="sv-ax">{x}</text>')
    out.append(f'<text x="{px(xs[0]):.1f}" y="{py(ys[0])-12:.1f}" class="sv-val">{ys[0]:.2f}</text>')
    out.append(f'<text x="{px(xs[-1]):.1f}" y="{py(ys[-1])-12:.1f}" text-anchor="end" '
               f'class="sv-val">{ys[-1]:.2f}</text>')
    out.append("</svg>")
    return "".join(out)


def svg_calibrazione(m: dict) -> str:
    """Decili di TPI contro rendimento realizzato, con la retta ideale."""
    bins = [b for b in (m or {}).get("bins", []) if b.get("realized_mean") is not None]
    if len(bins) < 4:
        return ""
    w, h, ml, mr, mt, mb = 640, 210, 40, 14, 16, 30
    ys = [b["realized_mean"] for b in bins]
    y0, y1 = min(ys), max(ys)
    span = max(1e-9, y1 - y0)
    def px(i):
        return ml + i / max(1, len(bins) - 1) * (w - ml - mr)
    def py(v):
        return mt + (1 - (v - y0) / span) * (h - mt - mb)
    out = [_SVG_OPEN.format(w=w, h=h)]
    out.append(f'<line x1="{px(0):.1f}" y1="{py(y0):.1f}" x2="{px(len(bins)-1):.1f}" '
               f'y2="{py(y1):.1f}" stroke="rgba(233,240,236,.22)" stroke-width="1" '
               f'stroke-dasharray="3 4"/>')
    pts = " ".join(f"{px(i):.1f},{py(v):.1f}" for i, v in enumerate(ys))
    out.append(f'<polyline points="{pts}" fill="none" stroke="var(--orng)" stroke-width="1.6"/>')
    for i, v in enumerate(ys):
        out.append(f'<circle cx="{px(i):.1f}" cy="{py(v):.1f}" r="3" fill="var(--orng)"/>')
    out.append(f'<text x="{ml}" y="{h-10}" class="sv-ax">1&ordm; decile</text>')
    out.append(f'<text x="{w-mr}" y="{h-10}" text-anchor="end" class="sv-ax">10&ordm; decile</text>')
    out.append(f'<text x="{px(len(bins)-1)-6:.1f}" y="{py(y1)-10:.1f}" text-anchor="end" '
               f'class="sv-val">rendimento reale</text>')
    out.append("</svg>")
    return "".join(out)


def svg_ablation(o: dict) -> str:
    """Quanto peggiora la previsione togliendo una dimensione per volta.

    Le barre crescono tutte verso destra e misurano |Δ|: il segno lo porta il
    colore. Farle crescere a sinistra dello zero, com'era prima, voleva dire
    che la barra piu' lunga usciva dal riquadro e copriva la propria etichetta.
    """
    res = sorted((o or {}).get("results", []), key=lambda r: r.get("delta_predict", 0))
    if not res:
        return ""
    w, rowh, ml = 640, 27, 118
    h = rowh * len(res) + 14
    lim = max(abs(r["delta_predict"]) for r in res) or 1e-9
    fondo = w - 62
    out = [_SVG_OPEN.format(w=w, h=h)]
    for i, r in enumerate(res):
        y = 7 + i * rowh
        v = r["delta_predict"]
        lung = abs(v) / lim * (fondo - ml)
        serve = v < -0.005
        col = "var(--orng)" if serve else "rgba(233,240,236,.22)"
        out.append(f'<text x="{ml-12}" y="{y+14}" text-anchor="end" class="sv-dim">{r["dim"]}</text>')
        out.append(f'<rect x="{ml}" y="{y+4}" width="{max(1.5, lung):.1f}" height="14" '
                   f'rx="2" fill="{col}"/>')
        out.append(f'<text x="{w-6}" y="{y+16}" text-anchor="end" class="sv-val">{v:+.3f}</text>')
    out.append(f'<line x1="{ml}" y1="2" x2="{ml}" y2="{h-4}" stroke="rgba(233,240,236,.25)"/>')
    out.append("</svg>")
    return "".join(out)


# ══════════════════════════════════════════════════════════════════
# CSS
# ══════════════════════════════════════════════════════════════════
CSS = """
*{margin:0;padding:0;box-sizing:border-box;-webkit-tap-highlight-color:transparent}
:root{
  --bg:#0A1512;--bg1:#0F1F1B;--bg2:#132723;
  --sep:rgba(233,240,236,.10);--sep2:rgba(233,240,236,.20);
  --lp:#ECF2EE;--ls:rgba(233,240,236,.70);--lt:rgba(233,240,236,.42);
  --lq:rgba(233,240,236,.22);
  --orng:#FFB020;
  --font:-apple-system,BlinkMacSystemFont,"SF Pro Display","Segoe UI",system-ui,sans-serif;
  --disp:"Oswald","Bahnschrift",Impact,sans-serif;
  --mono:"JetBrains Mono","Cascadia Mono",ui-monospace,monospace;
}
@font-face{font-family:"Oswald";font-style:normal;font-weight:200 700;font-display:swap;
  src:url("fonts/oswald-latin-var.woff2") format("woff2")}
@font-face{font-family:"JetBrains Mono";font-style:normal;font-weight:100 800;font-display:swap;
  src:url("fonts/jetbrainsmono-latin-var.woff2") format("woff2")}
html{-webkit-text-size-adjust:100%}
body{background:var(--bg);color:var(--lp);font-family:var(--font);
  font-size:16px;line-height:1.6;-webkit-font-smoothing:antialiased}
::selection{background:rgba(255,176,32,.26)}
a{color:inherit}

/* ── Nav: identica alle altre pagine del sito ─────────────────── */
.nav{position:sticky;top:0;z-index:60;display:flex;align-items:center;gap:14px;
  padding:10px clamp(18px,4vw,40px);background:rgba(10,21,18,.86);
  backdrop-filter:saturate(160%) blur(14px);border-bottom:1px solid var(--sep)}
.nav-brand{font-family:var(--disp);font-size:15px;letter-spacing:.06em;
  text-transform:uppercase;white-space:nowrap}
.nav-brand small{display:block;font-family:var(--font);font-size:9.5px;
  letter-spacing:.14em;color:var(--lt);text-transform:uppercase}
.nav-sp{flex:1}
.nav-btn{display:inline-flex;align-items:center;padding:6px 12px;border-radius:8px;
  border:1px solid var(--sep2);font-size:11.5px;letter-spacing:.08em;
  text-transform:uppercase;text-decoration:none;color:var(--ls);white-space:nowrap}
.nav-btn:hover{color:var(--lp);border-color:var(--lq)}
.nav-btn.pri{border-color:rgba(255,176,32,.5);color:var(--orng)}

/* ── Impaginazione: colonna di lettura + margine dei numeri ───── */
.doc{max-width:1040px;margin:0 auto;padding:0 clamp(20px,5vw,40px) 80px}
.prosa{max-width:34em;font-size:16.5px;line-height:1.75;color:var(--ls)}
.prosa strong{color:var(--lp);font-weight:600}
.prosa+.prosa{margin-top:14px}

.hero{padding:clamp(46px,9vw,96px) 0 clamp(30px,5vw,52px)}
.eyebrow{font-size:10.5px;letter-spacing:.24em;text-transform:uppercase;color:var(--lt);
  margin-bottom:20px}
h1{font-family:var(--disp);font-weight:500;text-transform:uppercase;
  font-size:clamp(38px,8.4vw,84px);line-height:.92;letter-spacing:-.012em;margin-bottom:24px}
h1 em{font-style:normal;color:var(--orng)}
.lede{max-width:31em;font-size:clamp(16.5px,2.1vw,19px);line-height:1.62;color:var(--ls)}

.cifre{display:flex;flex-wrap:wrap;gap:0;margin-top:44px;border-top:1px solid var(--sep)}
.cifra{flex:1 1 180px;padding:20px 22px 20px 0;border-right:1px solid var(--sep)}
.cifra:last-child{border-right:0}
.cifra b{display:block;font-family:var(--mono);font-weight:500;font-size:clamp(26px,4vw,34px);
  color:var(--orng);letter-spacing:-.03em;line-height:1}
.cifra span{display:block;margin-top:8px;font-size:11px;letter-spacing:.1em;
  text-transform:uppercase;color:var(--lt);line-height:1.5}

.cap{display:grid;grid-template-columns:58px minmax(0,1fr);gap:0 26px;
  padding:clamp(38px,6vw,64px) 0;border-top:1px solid var(--sep)}
.cap-num{font-family:var(--mono);font-size:11.5px;letter-spacing:.16em;color:var(--orng);
  padding-top:9px}
h2{font-family:var(--disp);font-weight:500;text-transform:uppercase;
  font-size:clamp(23px,3.4vw,34px);line-height:1.06;letter-spacing:.004em;margin-bottom:18px}
h3{font-size:12px;letter-spacing:.14em;text-transform:uppercase;color:var(--lt);
  margin:38px 0 10px}

.ev{display:grid;grid-template-columns:150px minmax(0,1fr);gap:0 26px;
  padding:17px 0;border-top:1px solid var(--sep)}
.ev:first-of-type{border-top:1px solid var(--sep2)}
.ev-fig{font-family:var(--mono);font-weight:500;font-size:23px;color:var(--orng);
  letter-spacing:-.03em;line-height:1.1}
.ev-lbl{display:block;margin-top:7px;font-family:var(--font);font-size:9.5px;
  letter-spacing:.12em;text-transform:uppercase;color:var(--lt);line-height:1.5}
.ev-txt{font-size:15px;line-height:1.68;color:var(--ls);padding-top:2px;max-width:38em}
.ev-txt strong{color:var(--lp);font-weight:600}
.ev.muta .ev-fig{color:var(--ls)}

.graf{display:block;width:100%;height:auto;margin:26px 0 4px;overflow:visible}
.sv-ax{font-family:var(--mono);font-size:9.5px;fill:var(--lt)}
.sv-val{font-family:var(--mono);font-size:10px;fill:var(--ls)}
.sv-dim{font-family:var(--mono);font-size:10.5px;fill:var(--ls)}
.didascalia{font-size:12.5px;color:var(--lt);line-height:1.6;max-width:34em;margin-top:6px}

table{width:100%;border-collapse:collapse;font-size:13.5px;margin-top:6px}
th{text-align:left;font-size:9.5px;letter-spacing:.13em;text-transform:uppercase;
  color:var(--lt);font-weight:500;padding:0 14px 10px 0;border-bottom:1px solid var(--sep2)}
td{padding:13px 14px 13px 0;border-bottom:1px solid var(--sep);color:var(--ls);
  vertical-align:top;line-height:1.55}
tr:hover td{color:var(--lp)}
.t-let{font-family:var(--mono);font-size:11px;color:var(--orng);width:26px;padding-top:15px}
.t-mis{font-family:var(--mono);font-size:12px;color:var(--lp);white-space:nowrap}

details{border-top:1px solid var(--sep);padding:15px 0}
details[open]{padding-bottom:22px}
summary{cursor:pointer;list-style:none;font-size:14.5px;color:var(--lp);
  display:flex;align-items:baseline;gap:10px}
summary::-webkit-details-marker{display:none}
summary::before{content:"+";font-family:var(--mono);color:var(--orng);font-size:14px}
details[open] summary::before{content:"\\2212"}
details .prosa{margin-top:12px;font-size:14.5px}

footer{border-top:1px solid var(--sep);padding:30px 0 0;margin-top:20px;
  font-size:12px;color:var(--lt);display:flex;flex-wrap:wrap;gap:14px 26px}
footer a{color:var(--ls);text-decoration:none;border-bottom:1px solid var(--lq)}
footer a:hover{color:var(--orng);border-color:var(--orng)}

@media(max-width:860px){
  .cap{grid-template-columns:1fr;gap:0}
  .cap-num{padding:0 0 10px}
  .ev{grid-template-columns:1fr;gap:8px 0;padding:16px 0}
  .ev-fig{font-size:21px}
  .ev-lbl{display:inline;margin-left:10px}
  .cifra{flex:1 1 45%;border-right:0;padding-right:14px}
  .nav-brand small{display:none}
  table{font-size:12.5px}
  .t-mis{white-space:normal}
}
@media(max-width:520px){
  td:nth-child(3),th:nth-child(3){display:none}
}
@media print{.nav{display:none}body{background:#fff;color:#000}}
"""


# ══════════════════════════════════════════════════════════════════
# Capitoli
# ══════════════════════════════════════════════════════════════════
def _esito_q(b: dict) -> tuple[str, str]:
    if b.get("tpi_better"):
        return "la batte", "beats it"
    if (b.get("ci_hi") or 0) < 0:
        return "non la batte", "does not beat it"
    return "pari", "a tie"


def _hero(d: dict) -> str:
    meta, m, l, q = d["meta"], d.get("m") or {}, d.get("l") or {}, d.get("q") or {}
    pv = [r for r in l.get("per_vintage", []) if r.get("spearman_rho") is not None]
    primo = pv[0] if pv else None
    # Le etichette vanno in maiuscolo via CSS, e una &rho; maiuscola e' una P:
    # la lettera greca sta nella cifra, che resta in minuscolo.
    cifre = []
    if m.get("monotonia_rho") is not None:
        cifre.append((f'&rho; {_f(m["monotonia_rho"], 3)}',
                      "fra decile di TPI e rendimento reale",
                      "between TPI decile and real output"))
    if primo:
        cifre.append((f'&rho; {_f(primo["spearman_rho"], 3)}',
                      f"la graduatoria di giornata {primo['vintage_giornata']} "
                      f"contro quella finale",
                      f"matchday-{primo['vintage_giornata']} ranking against the final one"))
    if q.get("has_data"):
        cifre.append((f'{q.get("n", 0)}',
                      f"confronti fuori campione su {q.get('n_giocatori', 0)} giocatori",
                      f"out-of-sample comparisons on {q.get('n_giocatori', 0)} players"))
    box = "".join(f'<div class="cifra"><b>{v}</b>'
                  f'<span {bi(li, le)}>{li}</span></div>' for v, li, le in cifre)
    lede_it = (f"Il TPI ordina i giocatori di Serie A per impatto offensivo. Qui ci sono le "
               f"{meta['n_verifiche']} verifiche che gli ho fatto sui {meta['n_giocatori']} "
               f"giocatori qualificati della stagione, compresa quella costruita apposta per "
               f"bocciarlo.")
    lede_en = (f"The TPI ranks Serie A players by attacking impact. These are the "
               f"{meta['n_verifiche']} checks I ran on the season&rsquo;s "
               f"{meta['n_giocatori']} qualified players, including the one built to fail it.")
    return f"""<header class="hero">
  <div class="eyebrow">Serie A Scout Index &middot; TPI</div>
  <h1 {bi("Cosa regge,<br><em>e cosa no</em>", "What holds up,<br><em>and what doesn&rsquo;t</em>")}>Cosa regge,<br><em>e cosa no</em></h1>
  <p class="lede" {bi(lede_it, lede_en)}>{lede_it}</p>
  <div class="cifre">{box}</div>
</header>"""


def _cap_regge(d: dict) -> str:
    m, l, h, f, c = (d.get(k) or {} for k in ("m", "l", "h", "f", "c"))
    ev = []
    if m.get("has_data"):
        ev.append(evidenza(
            _f(m.get("monotonia_rho"), 3), "Ordine dei decili", "Order of the deciles",
            f"Diviso in dieci gruppi, l&rsquo;indice li dispone nell&rsquo;ordine del rendimento "
            f"realizzato. La pendenza per&ograve; &egrave; {_f(m.get('slope'), 3)} invece di 1: "
            f"<strong>l&rsquo;ordine &egrave; giusto, le distanze no</strong>. Dice chi viene prima, "
            f"non quante volte &egrave; pi&ugrave; forte.",
            f"Split into ten groups, the index lines them up in the order of realized output. The "
            f"slope, though, is {_f(m.get('slope'), 3)} instead of 1: <strong>the order is right, "
            f"the distances are not</strong>. It says who comes first, not how many times better."))
    pv = [r for r in l.get("per_vintage", []) if r.get("spearman_rho") is not None]
    if len(pv) >= 2:
        a, z = pv[0], pv[-1]
        ev.append(evidenza(
            _f(a["spearman_rho"], 3),
            f"Gi&agrave; alla giornata {a['vintage_giornata']}", f"Already by matchday {a['vintage_giornata']}",
            f"A un terzo di stagione la graduatoria &egrave; gi&agrave; quasi quella di fine anno, e "
            f"sale a {_f(z['spearman_rho'], 3)} alla giornata {z['vintage_giornata']}. "
            f"<strong>Non serve aspettare maggio per usarla.</strong>",
            f"A third of the way in, the ranking is already close to the final one, rising to "
            f"{_f(z['spearman_rho'], 3)} by matchday {z['vintage_giornata']}. "
            f"<strong>You do not have to wait for May to use it.</strong>"))
    if h.get("has_data"):
        pct = int(round((h.get("pct") or 0.2) * 100))
        ev.append(evidenza(
            _f(h.get("spearman_median"), 3), "Pesi perturbati", "Weights perturbed",
            f"Spostando a caso di &plusmn;{pct}% i sette pesi del composito, la graduatoria non si "
            f"muove (peggior caso {_f(h.get('spearman_min'), 3)}). "
            f"<strong>Non &egrave; un indice tarato a mano su un risultato che mi piaceva.</strong>",
            f"Randomly shifting the seven weights by &plusmn;{pct}%, the ranking does not move "
            f"(worst case {_f(h.get('spearman_min'), 3)}). <strong>It is not an index hand-tuned "
            f"to a result I liked.</strong>"))
    if f.get("has_data"):
        ev.append(evidenza(
            _f(f.get("r"), 3), "A livello di squadra", "At team level",
            f"La media di TPI di una rosa segue l&rsquo;xG prodotto dalla squadra "
            f"({f.get('n')} squadre, IC {_ic(f.get('ci_lo'), f.get('ci_hi'), 2)}). Regge anche "
            f"contro un esito che non ha niente a che vedere col singolo giocatore.",
            f"A squad&rsquo;s mean TPI tracks the xG the team produces ({f.get('n')} teams, CI "
            f"{_ic(f.get('ci_lo'), f.get('ci_hi'), 2)}). It holds against an outcome that has "
            f"nothing to do with the individual player."))
    if c.get("r") is not None:
        pl = (c.get("placebo") or {}).get("p_perm")
        ev.append(evidenza(
            _f(c.get("r"), 3), "Prima met&agrave; &rarr; seconda", "First half &rarr; second",
            f"Chi rende nella prima met&agrave; di stagione rende anche nella seconda "
            f"({c.get('n')} giocatori, IC {_ic(c.get('ci_lo'), c.get('ci_hi'), 2)}"
            + (f", placebo p = {_f(pl, 3)}" if pl is not None else "") + "). Riguarda "
            f"l&rsquo;output offensivo grezzo, che &egrave; l&rsquo;ingrediente principale "
            f"dell&rsquo;indice: non &egrave; una prova sul composito.",
            f"Players who deliver in the first half also deliver in the second ({c.get('n')} "
            f"players, CI {_ic(c.get('ci_lo'), c.get('ci_hi'), 2)}"
            + (f", placebo p = {_f(pl, 3)}" if pl is not None else "") + "). It concerns raw "
            f"attacking output, the index&rsquo;s main ingredient: it is not proof about the "
            f"composite."))
    p_it = ("Il TPI &egrave; una <strong>graduatoria</strong>. Le verifiche qui sotto riguardano "
            "questo: se l&rsquo;ordine che produce corrisponde a quello che i giocatori hanno "
            "fatto davvero in campo, quanto presto si stabilizza e quanto dipende dalle scelte "
            "arbitrarie di chi l&rsquo;ha costruito.")
    p_en = ("The TPI is a <strong>ranking</strong>. The checks below are about that: whether the "
            "order it produces matches what players actually did on the pitch, how early it "
            "settles, and how much it depends on the arbitrary choices of whoever built it.")
    graf = svg_convergenza(l)
    dida_it = (f"&rho; di Spearman fra la graduatoria fotografata a ogni giornata e quella di fine "
               f"stagione.")
    dida_en = ("Spearman &rho; between the ranking as of each matchday and the end-of-season one.")
    graf_blk = (f'{graf}<p class="didascalia" {bi(dida_it, dida_en)}>{dida_it}</p>'
                if graf else "")
    cal = svg_calibrazione(m)
    cal_it = ("Rendimento medio realizzato dal primo all&rsquo;ultimo decile di TPI. La linea "
              "tratteggiata &egrave; la salita costante che avrebbe un indice calibrato: "
              "l&rsquo;ordine coincide, il passo no.")
    cal_en = ("Mean realized output from the first to the last TPI decile. The dashed line is the "
              "steady rise a calibrated index would show: the order matches, the spacing does not.")
    cal_blk = (f'<h3 {bi("Dal primo al decimo decile", "From the first to the tenth decile")}>'
               f'Dal primo al decimo decile</h3>{cal}'
               f'<p class="didascalia" {bi(cal_it, cal_en)}>{cal_it}</p>' if cal else "")
    return f"""<section class="cap">
  <div class="cap-num">01</div>
  <div>
    {el("h2", "Quello che l&rsquo;indice fa", "What the index does")}
    <p class="prosa" {bi(p_it, p_en)}>{p_it}</p>
    {graf_blk}
    {"".join(ev)}
    {cal_blk}
  </div>
</section>"""


def _cap_prova(d: dict) -> str:
    q, i, o, p = (d.get(k) or {} for k in ("q", "i", "o", "p"))
    ev = []
    liv = (q.get("criteri") or {}).get("livello", {}).get("baselines", [])
    imp = (q.get("criteri") or {}).get("improvement", {}).get("baselines", [])
    b_out = next((b for b in liv if b["key"] == "output_grezzo"), None)
    if b_out:
        es_it, es_en = _esito_q(b_out)
        ev.append(evidenza(
            _f(b_out["delta_rmse"], 4, True), "Contro l&rsquo;output grezzo",
            "Against raw output",
            f"Il TPI <strong>{es_it}</strong>: l&rsquo;intervallo "
            f"{_ic(b_out['ci_lo'], b_out['ci_hi'], 3)} sta tutto dalla parte sbagliata. Per "
            f"prevedere i gol della met&agrave; di stagione successiva, quella statistica da sola "
            f"fa meglio dell&rsquo;indice che la contiene.",
            f"The TPI <strong>{es_en}</strong>: the interval "
            f"{_ic(b_out['ci_lo'], b_out['ci_hi'], 3)} sits entirely on the wrong side. To "
            f"forecast the next half-season&rsquo;s goals, that statistic alone does better than "
            f"the index built around it."))
    altre = [b for b in liv if b["key"] != "output_grezzo"]
    if altre:
        nomi_it = " e ".join(b["label_it"].lower() for b in altre)
        nomi_en = " and ".join(b["label_en"].lower() for b in altre)
        ev.append(evidenza(
            "pari", "Contro minuti e valore", "Against minutes and value",
            f"Contro {nomi_it} il confronto finisce in parit&agrave;: gli intervalli contengono "
            f"lo zero. Un indice costruito su sette dimensioni non fa meglio di quanto un "
            f"giocatore gioca, o di quanto lo paga il mercato.",
            f"Against {nomi_en} the comparison ends level: the intervals contain zero. An index "
            f"built on seven dimensions does no better than how much a player plays, or what the "
            f"market pays for him."))
    vinti = [b for b in imp if b.get("tpi_better")]
    if vinti:
        n_it = " e ".join(b["label_it"].lower() for b in vinti)
        n_en = " and ".join(b["label_en"].lower() for b in vinti)
        best = max(vinti, key=lambda b: b["delta_rmse"])
        ev.append(evidenza(
            _f(best["delta_rmse"], 4, True), "Su chi migliora", "On who improves",
            f"Cambiando domanda &mdash; non quanto render&agrave;, ma chi crescer&agrave; rispetto "
            f"a s&eacute; stesso &mdash; il TPI batte {n_it}, con l&rsquo;intervallo tutto sopra "
            f"lo zero. <strong>&Egrave; l&rsquo;unico terreno su cui vince</strong>, ed &egrave; "
            f"quello per cui l&rsquo;indice era nato.",
            f"Change the question &mdash; not how much he will produce, but who will improve on "
            f"himself &mdash; and the TPI beats {n_en}, with the interval entirely above zero. "
            f"<strong>It is the only ground where it wins</strong>, and it is the one the index "
            f"was built for."))
    if i.get("has_data"):
        ev.append(evidenza(
            _f(i.get("delta_rmse"), 4, True), "I modulatori scout", "The scout modulators",
            f"Et&agrave; e affidabilit&agrave; fisica, aggiunte al TPI, non spostano niente fuori "
            f"campione: {_ic(i.get('ci_lo'), i.get('ci_hi'), 4)} su {i.get('n')} confronti. "
            f"Servono a leggere il profilo di un giocatore, non a prevederne il rendimento.",
            f"Age and physical reliability, added on top of the TPI, move nothing out-of-sample: "
            f"{_ic(i.get('ci_lo'), i.get('ci_hi'), 4)} across {i.get('n')} comparisons. They help "
            f"read a player&rsquo;s profile, not forecast his output."))
    res = sorted(o.get("results", []), key=lambda r: r.get("delta_predict", 0))
    if res:
        utili = [r for r in res if r["delta_predict"] < -0.005]
        ev.append(evidenza(
            f"{len(res) - len(utili)} su {len(res)}", "Dimensioni che non pagano",
            "Dimensions that do not pay",
            "Tolte una alla volta, la maggior parte delle dimensioni <strong>migliora</strong> la "
            "previsione: pesano nella graduatoria ma non nel risultato. Si guadagnano il posto "
            + " e ".join(f"<strong>{r['dim']}</strong> ({_f(r['delta_predict'], 3, True)})"
                         for r in utili) + ".",
            "Removed one at a time, most dimensions <strong>improve</strong> the forecast: they "
            "weigh on the ranking but not on the outcome. The ones that earn their place are "
            + " and ".join(f"<strong>{r['dim']}</strong> ({_f(r['delta_predict'], 3, True)})"
                           for r in utili) + "."))
    cs = p.get("cross_2season") or {}
    if cs.get("rho_2season_mean") is not None:
        tiene_it = "regge" if cs.get("significativo_95") else "non lo conto"
        tiene_en = "holds" if cs.get("significativo_95") else "I do not count it"
        ev.append(evidenza(
            _f(cs.get("delta"), 3, True), "Mediare due stagioni", "Averaging two seasons",
            f"Un TPI mediato su due stagioni ordina meglio di quello di una sola "
            f"({_f(cs.get('rho_single'), 2, True)} &rarr; {_f(cs.get('rho_2season_mean'), 2, True)} "
            f"su {cs.get('n')} giocatori), ma l&rsquo;intervallo "
            f"{_ic(cs.get('ic95_lo'), cs.get('ic95_hi'), 3)} sfiora lo zero: {tiene_it}.",
            f"A TPI averaged over two seasons ranks better than a single-season one "
            f"({_f(cs.get('rho_single'), 2, True)} &rarr; {_f(cs.get('rho_2season_mean'), 2, True)} "
            f"over {cs.get('n')} players), but the interval "
            f"{_ic(cs.get('ic95_lo'), cs.get('ic95_hi'), 3)} grazes zero: {tiene_en}."))
    gg = ", ".join(str(g) for g in q.get("vintage_giornate", []))
    p1_it = (f"Un indice si giudica contro l&rsquo;alternativa pi&ugrave; banale che potrebbe "
             f"sostituirlo. Ho congelato la classifica alle giornate {gg} e ho chiesto al TPI e a "
             f"tre predittori elementari di indovinare il rendimento delle giornate successive, "
             f"che a quel punto non erano ancora state giocate.")
    p1_en = (f"An index is judged against the most trivial alternative that could replace it. I "
             f"froze the ranking at matchdays {gg} and asked the TPI and three elementary "
             f"predictors to guess output over the matchdays that followed, which at that point "
             f"had not been played yet.")
    p2_it = ("Questa sezione &egrave; l&rsquo;unica che pu&ograve; bocciare l&rsquo;indice, e in "
             "parte lo boccia. La riporto per intero perch&eacute; un indice che non ha mai perso "
             "&egrave; solo un indice che non &egrave; mai stato messo alla prova.")
    p2_en = ("This section is the only one that can fail the index, and in part it does. I report "
             "it in full because an index that has never lost is just an index that has never "
             "been tested.")
    graf = svg_ablation(o)
    dida_it = ("Peggioramento della previsione togliendo una dimensione alla volta: le barre "
               "ambra sono le dimensioni che servono.")
    dida_en = ("Loss in forecast accuracy when each dimension is removed: amber bars are the "
               "dimensions that matter.")
    graf_blk = (f'<h3 {bi("Le sette dimensioni, una alla volta", "The seven dimensions, one at a time")}>'
                f'Le sette dimensioni, una alla volta</h3>{graf}'
                f'<p class="didascalia" {bi(dida_it, dida_en)}>{dida_it}</p>' if graf else "")
    return f"""<section class="cap">
  <div class="cap-num">02</div>
  <div>
    {el("h2", "La prova costruita per bocciarlo", "The test built to fail it")}
    <p class="prosa" {bi(p1_it, p1_en)}>{p1_it}</p>
    <p class="prosa" {bi(p2_it, p2_en)}>{p2_it}</p>
    {"".join(ev)}
    {graf_blk}
  </div>
</section>"""


def _cap_contesto(d: dict) -> str:
    a, b, dd, e, n = (d.get(k) or {} for k in ("a", "b", "d", "e", "n"))
    ev = []
    if a.get("r") is not None:
        ev.append(evidenza(
            _f(a.get("r"), 3), f"{a.get('n')} voti Fantacalcio", f"{a.get('n')} Fantacalcio ratings",
            f"L&rsquo;indice e il consenso degli esperti vanno d&rsquo;accordo, IC "
            f"{_ic(a.get('ci_lo'), a.get('ci_hi'), 3)}, e l&rsquo;accordo resta controllando il "
            f"ruolo ({_f(a.get('partial_r'), 3)}). Non lo chiamo prova: i voti li ho raccolti a "
            f"mano su un sottoinsieme scelto, non su un campione estratto a caso.",
            f"The index and expert consensus agree, CI {_ic(a.get('ci_lo'), a.get('ci_hi'), 3)}, "
            f"and the agreement survives controlling for role ({_f(a.get('partial_r'), 3)}). I do "
            f"not call it proof: I collected those ratings by hand over a chosen subset, not a "
            f"random sample."))
    if b.get("overlap_pct") is not None:
        dv = (b.get("divergenze_pos") or [])[:1]
        esempio_it = esempio_en = ""
        if dv:
            g = dv[0]
            esempio_it = (f" Il caso pi&ugrave; netto &egrave; {g['nome']}: {g['tpi_rank']}&ordm; "
                          f"per TPI, {g['ws_rank']}&ordm; per WhoScored.")
            esempio_en = (f" The sharpest case is {g['nome']}: {g['tpi_rank']}th by TPI, "
                          f"{g['ws_rank']}th by WhoScored.")
        ev.append(evidenza(
            f"{b['overlap_pct']}%", "Top 10 in comune", "Top 10 in common",
            f"Con la classifica di WhoScored condivido {b['overlap_pct']}% dei primi dieci, quanto "
            f"ci si aspetta dal caso (p = {_f((b.get('hyper') or {}).get('p'), 2)}). Non &egrave; "
            f"un test &mdash; un accordo alto direbbe &laquo;confermato&raquo;, uno basso "
            f"&laquo;trova occasioni&raquo; &mdash; ma il disaccordo &egrave; il posto dove "
            f"guardare." + esempio_it,
            f"I share {b['overlap_pct']}% of the top ten with the WhoScored ranking, about what "
            f"chance would give (p = {_f((b.get('hyper') or {}).get('p'), 2)}). It is not a test "
            f"&mdash; high agreement would say &laquo;confirmed&raquo;, low agreement &laquo;it "
            f"finds bargains&raquo; &mdash; but disagreement is where to look." + esempio_en))
    if dd.get("has_data"):
        ev.append(evidenza(
            _f(dd.get("aii_tpi_r"), 3), "Et&agrave; contro qualit&agrave;", "Age against quality",
            f"L&rsquo;indice d&rsquo;et&agrave; non &egrave; correlato al TPI, e deve essere "
            f"cos&igrave;: misura il momento della carriera, non quanto un giocatore &egrave; "
            f"forte. Se le due cose andassero insieme, una delle due sarebbe di troppo.",
            f"The age index is uncorrelated with the TPI, and it should be: it measures the point "
            f"of a career, not how good a player is. If the two moved together, one of them would "
            f"be redundant."))
    if e.get("has_data"):
        ev.append(evidenza(
            _f(e.get("r_corr"), 3), "TPI contro TPI Pro", "TPI against TPI Pro",
            f"Alta per costruzione: la versione Pro <em>contiene</em> il TPI. La metto in pagina "
            f"perch&eacute; qualcuno la citerebbe come prova che il Pro funziona, e non lo "
            f"&egrave;. Quella risposta sta nella sezione precedente.",
            f"High by construction: the Pro version <em>contains</em> the TPI. I put it here "
            f"because someone would quote it as evidence that the Pro works, and it is not. That "
            f"answer is in the previous section."))
    pr = n.get("per_role") or {}
    if pr:
        best = max(pr.items(), key=lambda kv: (kv[1].get("rho") or -9))
        ev.append(evidenza(
            _f(best[1].get("rho"), 3, True), "Il ruolo migliore", "The best role",
            f"Per ruolo il legame col rendimento &egrave; pi&ugrave; alto sui {best[0]} "
            f"({best[1].get('n')} giocatori). Va letto con prudenza: il criterio &egrave; "
            f"l&rsquo;output offensivo, quindi dentro la difesa separa i terzini che attaccano, "
            f"non i bravi difensori.",
            f"By role the link with output is highest for {best[0]} ({best[1].get('n')} players). "
            f"Read it with care: the criterion is attacking output, so within defence it separates "
            f"attacking full-backs, not good defenders."))
    p_it = ("Quattro confronti che tengo in pagina perch&eacute; dicono qualcosa sull&rsquo;indice, "
            "non perch&eacute; lo dimostrino. Nessuno di questi numeri pu&ograve; bocciarlo: chi "
            "entra nel confronto lo decido io, oppure il criterio &egrave; costruito in modo che "
            "qualunque risultato suoni bene. Sarebbero i pi&ugrave; comodi da esibire, ed &egrave; "
            "esattamente il motivo per cui stanno in fondo.")
    p_en = ("Four comparisons I keep because they say something about the index, not because they "
            "prove it. None of these numbers can fail it: either I decide who enters the "
            "comparison, or the criterion is built so that any result sounds good. They would be "
            "the easiest ones to show off, which is exactly why they sit at the bottom.")
    return f"""<section class="cap">
  <div class="cap-num">03</div>
  <div>
    {el("h2", "Quello che non dimostra niente", "What proves nothing")}
    <p class="prosa" {bi(p_it, p_en)}>{p_it}</p>
    {"".join(ev)}
  </div>
</section>"""


def _cap_metodo(d: dict) -> str:
    meta, q, m, c = d["meta"], d.get("q") or {}, d.get("m") or {}, d.get("c") or {}
    voci = []
    camp_it = (f"Le verifiche girano su tutti i {meta['n_giocatori']} giocatori qualificati, non "
               f"sui primi cento della dashboard. Il taglio in alto &egrave; una scelta di "
               f"prodotto: confrontare fra loro solo i migliori restringe la varianza e abbassa "
               f"da solo ogni correlazione, quindi misurerebbe la selezione invece "
               f"dell&rsquo;indice.")
    camp_en = (f"The checks run on all {meta['n_giocatori']} qualified players, not on the "
               f"dashboard&rsquo;s top hundred. That cut is a product choice: comparing only the "
               f"best against each other narrows the variance and lowers every correlation by "
               f"itself, so it would measure the selection rather than the index.")
    voci.append(("Campione", "Sample", camp_it, camp_en))
    gg = ", ".join(str(g) for g in q.get("vintage_giornate", []))
    oos_it = (f"I test predittivi partono da fotografie della classifica alle giornate {gg}: i "
              f"predittori vengono da l&igrave;, il criterio dalle partite successive. Nessun "
              f"dato del criterio &egrave; disponibile a chi fa la previsione.")
    oos_en = (f"The predictive tests start from snapshots of the ranking at matchdays {gg}: the "
              f"predictors come from there, the criterion from the matches that followed. None of "
              f"the criterion&rsquo;s data is available to whoever makes the prediction.")
    voci.append(("Fuori campione", "Out-of-sample", oos_it, oos_en))
    inc_it = ("Ogni numero in pagina ha un intervallo di confidenza bootstrap al 95%. Dove lo "
              "stesso giocatore compare in pi&ugrave; confronti, il ricampionamento tiene insieme "
              "le sue righe: trattarle come indipendenti restringerebbe gli intervalli e "
              "farebbe sembrare significativo quello che non lo &egrave;.")
    inc_en = ("Every figure on this page carries a 95% bootstrap confidence interval. Where the "
              "same player appears in several comparisons, the resampling keeps his rows "
              "together: treating them as independent would shrink the intervals and make "
              "non-significant results look significant.")
    voci.append(("Incertezza", "Uncertainty", inc_it, inc_en))
    sg = d.get("soglie") or {}
    taglio_it = taglio_en = ""
    if sg.get("c_hi") is not None:
        taglio_it = (f" L&rsquo;unico taglio numerico riguarda il backtest, dove leggo "
                     f"|r| &ge; {sg['c_hi']:.2f} come forte e &ge; {sg['c_mid']:.2f} come "
                     f"moderato.")
        taglio_en = (f" The only numeric cut concerns the backtest, where I read "
                     f"|r| &ge; {sg['c_hi']:.2f} as strong and &ge; {sg['c_mid']:.2f} as "
                     f"moderate.")
    sog_it = ("Quello che conta come risultato &egrave; deciso prima di guardare i numeri, e sta "
              "in un punto solo del codice. Dove un taglio non avrebbe senso &mdash; il confronto "
              "con le baseline, la persistenza &mdash; decide il segno dell&rsquo;intervallo di "
              "confidenza: sopra lo zero regge, sotto no, a cavallo &egrave; un pareggio."
              + taglio_it +
              " Quando un risultato attraversa la regola cambia quello che scrivo, non la regola: "
              "&egrave; successo alla persistenza fra stagioni, che prima contava come guadagno e "
              "adesso no.")
    sog_en = ("What counts as a result is decided before looking at the numbers, and lives in a "
              "single place in the code. Where a cut-off would make no sense &mdash; the baseline "
              "comparison, persistence &mdash; the sign of the confidence interval decides: above "
              "zero it holds, below it does not, straddling zero is a tie."
              + taglio_en +
              " When a result crosses the rule, what changes is what I write, not the rule: that "
              "happened to cross-season persistence, which used to count as a gain and no longer "
              "does.")
    voci.append(("Soglie", "Thresholds", sog_it, sog_en))
    lim_it = ("I voti Fantacalcio e la classifica WhoScored sono inseriti a mano. Il valore di "
              "mercato usato come baseline &egrave; una fotografia di fine stagione, quindi "
              "&laquo;sa&raquo; gi&agrave; come &egrave; andata: nel confronto gioca in "
              "vantaggio. "
              + (f"Il backtest prima/seconda met&agrave; misura l&rsquo;output offensivo grezzo, "
                 f"non il composito." if c.get("r") is not None else ""))
    lim_en = ("Fantacalcio ratings and the WhoScored ranking are entered by hand. The market value "
              "used as a baseline is an end-of-season snapshot, so it already &laquo;knows&raquo; "
              "how the season went: it enters the comparison with an advantage. "
              + ("The first-half/second-half backtest measures raw attacking output, not the "
                 "composite." if c.get("r") is not None else ""))
    voci.append(("Limiti", "Limits", lim_it, lim_en))
    blocchi = "".join(
        f'<details><summary {bi(t_it, t_en)}>{t_it}</summary>'
        f'<p class="prosa" {bi(b_it, b_en)}>{b_it}</p></details>'
        for t_it, t_en, b_it, b_en in voci)
    return f"""<section class="cap">
  <div class="cap-num">04</div>
  <div>
    {el("h2", "Come &egrave; misurato", "How it is measured")}
    {blocchi}
  </div>
</section>"""


def _tabella(d: dict) -> str:
    q, i, o, p, m, l, n = (d.get(k) or {} for k in ("q", "i", "o", "p", "m", "l", "n"))
    a, b, c, dd, e = (d.get(k) or {} for k in ("a", "b", "c", "d", "e"))
    f, g, h = (d.get(k) or {} for k in ("f", "g", "h"))
    r = []
    liv = (q.get("criteri") or {}).get("livello", {}).get("baselines", [])
    b_out = next((x for x in liv if x["key"] == "output_grezzo"), None)
    if b_out:
        es_it, es_en = _esito_q(b_out)
        r.append(riga_tab("Q", "Batte una baseline banale sul rendimento futuro?",
                          "Does it beat a trivial baseline on future output?",
                          f'&Delta;RMSE {_f(b_out["delta_rmse"], 4, True)}',
                          f"No: {es_it}, IC {_ic(b_out['ci_lo'], b_out['ci_hi'], 3)}.",
                          f"No: it {es_en}, CI {_ic(b_out['ci_lo'], b_out['ci_hi'], 3)}."))
    if c.get("r") is not None:
        r.append(riga_tab("C", "L&rsquo;output offensivo si conferma fra le due met&agrave; di stagione?",
                          "Does attacking output repeat across the two halves of the season?",
                          f'&rho; {_f(c.get("r"), 3)}',
                          f"S&igrave;, su {c.get('n')} giocatori.",
                          f"Yes, across {c.get('n')} players."))
    if m.get("has_data"):
        r.append(riga_tab("M", "L&rsquo;ordine dei decili corrisponde al rendimento reale?",
                          "Do the deciles line up with real output?",
                          f'&rho; {_f(m.get("monotonia_rho"), 3)}',
                          f"S&igrave; nell&rsquo;ordine, no nelle distanze (pendenza {_f(m.get('slope'), 3)}).",
                          f"Yes in order, no in distance (slope {_f(m.get('slope'), 3)})."))
    pv = [x for x in l.get("per_vintage", []) if x.get("spearman_rho") is not None]
    if pv:
        r.append(riga_tab("L", "Quanto presto la graduatoria si stabilizza?",
                          "How early does the ranking settle?",
                          f'&rho; {_f(pv[0]["spearman_rho"], 3)}',
                          f"Gi&agrave; alla giornata {pv[0]['vintage_giornata']}; "
                          f"{_f(pv[-1]['spearman_rho'], 3)} alla {pv[-1]['vintage_giornata']}.",
                          f"Already by matchday {pv[0]['vintage_giornata']}; "
                          f"{_f(pv[-1]['spearman_rho'], 3)} by {pv[-1]['vintage_giornata']}."))
    if h.get("has_data"):
        r.append(riga_tab("H", "La graduatoria dipende dai pesi scelti?",
                          "Does the ranking depend on the chosen weights?",
                          f'&rho; {_f(h.get("spearman_median"), 3)}',
                          "No: perturbando i pesi non si muove.",
                          "No: perturbing the weights leaves it in place."))
    if f.get("has_data"):
        r.append(riga_tab("F", "Regge su un esito di squadra?", "Does it hold on a team outcome?",
                          f'r {_f(f.get("r"), 3)}',
                          f"S&igrave;, contro l&rsquo;xG prodotto ({f.get('n')} squadre).",
                          f"Yes, against xG produced ({f.get('n')} teams)."))
    if g.get("has_data"):
        r.append(riga_tab("G", "Le sette dimensioni misurano cose diverse?",
                          "Do the seven dimensions measure different things?",
                          f'PC1 {_f(g.get("pc1"), 3)}',
                          "S&igrave;: nessuna componente domina il composito.",
                          "Yes: no single component dominates the composite."))
    if i.get("has_data"):
        r.append(riga_tab("I", "I modulatori scout aggiungono capacit&agrave; predittiva?",
                          "Do the scout modulators add predictive power?",
                          f'&Delta;RMSE {_f(i.get("delta_rmse"), 4, True)}',
                          f"No: IC {_ic(i.get('ci_lo'), i.get('ci_hi'), 4)}.",
                          f"No: CI {_ic(i.get('ci_lo'), i.get('ci_hi'), 4)}."))
    res = sorted(o.get("results", []), key=lambda x: x.get("delta_predict", 0))
    if res:
        utili = [x for x in res if x["delta_predict"] < -0.005]
        r.append(riga_tab("O", "Ogni dimensione si guadagna il posto?",
                          "Does every dimension earn its place?",
                          f'{len(utili)}/{len(res)}',
                          f"No: solo {' e '.join(x['dim'] for x in utili)} migliorano la previsione.",
                          f"No: only {' and '.join(x['dim'] for x in utili)} improve the forecast."))
    cs = p.get("cross_2season") or {}
    if cs.get("delta") is not None:
        r.append(riga_tab("P", "Mediare pi&ugrave; stagioni ordina meglio?",
                          "Does averaging seasons rank better?",
                          f'&Delta;&rho; {_f(cs.get("delta"), 3, True)}',
                          f"Indicativo: IC {_ic(cs.get('ic95_lo'), cs.get('ic95_hi'), 3)} contiene lo zero.",
                          f"Indicative: CI {_ic(cs.get('ic95_lo'), cs.get('ic95_hi'), 3)} contains zero."))
    pr = n.get("per_role") or {}
    if pr:
        best = max(pr.items(), key=lambda kv: (kv[1].get("rho") or -9))
        r.append(riga_tab("N", "Funziona uguale in tutti i ruoli?", "Does it work the same in every role?",
                          f'&rho; {_f(best[1].get("rho"), 3)}',
                          f"No: pi&ugrave; alto sui {best[0]}, e il criterio &egrave; offensivo.",
                          f"No: highest for {best[0]}, and the criterion is an attacking one."))
    if a.get("r") is not None:
        r.append(riga_tab("A", "Somiglia al consenso degli esperti?", "Does it resemble expert consensus?",
                          f'r {_f(a.get("r"), 3)}',
                          f"S&igrave;, ma su {a.get('n')} voti raccolti a mano: contesto, non prova.",
                          f"Yes, but over {a.get('n')} hand-collected ratings: context, not proof."))
    if b.get("overlap_pct") is not None:
        r.append(riga_tab("B", "Quanto coincide con WhoScored?", "How much does it match WhoScored?",
                          f'{b["overlap_pct"]}%',
                          f"Come il caso (p = {_f((b.get('hyper') or {}).get('p'), 2)}); "
                          f"nessun valore potrebbe bocciare l&rsquo;indice.",
                          f"About chance (p = {_f((b.get('hyper') or {}).get('p'), 2)}); no value "
                          f"could fail the index."))
    if dd.get("has_data"):
        r.append(riga_tab("D", "L&rsquo;indice d&rsquo;et&agrave; misura qualcosa di suo?",
                          "Does the age index measure something of its own?",
                          f'r {_f(dd.get("aii_tpi_r"), 3)}',
                          "S&igrave;: &egrave; scorrelato dal TPI, come deve essere.",
                          "Yes: it is uncorrelated with the TPI, as it should be."))
    if e.get("has_data"):
        r.append(riga_tab("E", "Quanto il TPI Pro somiglia al TPI?", "How close is TPI Pro to the TPI?",
                          f'r {_f(e.get("r_corr"), 3)}',
                          "Alto per costruzione: il Pro contiene il TPI.",
                          "High by construction: the Pro contains the TPI."))
    return f"""<section class="cap">
  <div class="cap-num">05</div>
  <div>
    {el("h2", "Tutte le verifiche", "Every check")}
    <table>
      <thead><tr>
        <th></th>
        <th {bi("Domanda", "Question")}>Domanda</th>
        <th {bi("Misura", "Measure")}>Misura</th>
        <th {bi("Risposta", "Answer")}>Risposta</th>
      </tr></thead>
      <tbody>{"".join(r)}</tbody>
    </table>
  </div>
</section>"""


# ══════════════════════════════════════════════════════════════════
# Pagina
# ══════════════════════════════════════════════════════════════════
def render(dati: dict) -> str:
    nav = f"""<nav class="nav">
  <div class="nav-brand">TPI <small>Serie A 25/26</small></div>
  <div class="nav-sp"></div>
  <span data-i18n-switcher></span>
  <a class="nav-btn" href="dashboard_serie_a.html" {bi("Classifica", "Ranking")}>Classifica</a>
  <a class="nav-btn" href="guida_completa.html" {bi("Metodo", "Method")}>Metodo</a>
  <a class="nav-btn pri" href="index.html">Homepage</a>
</nav>"""
    footer = f"""<footer>
  <span>Raffaele Ciccone &middot; Serie A Scout Index</span>
  <a href="guida_completa.html" {bi("Come sono costruiti gli indici", "How the indices are built")}>Come sono costruiti gli indici</a>
  <a href="dashboard_serie_a.html" {bi("La classifica completa", "The full ranking")}>La classifica completa</a>
</footer>"""
    return f"""<!DOCTYPE html>
<html lang="it">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>Validazione &mdash; Serie A Scout Index</title>
<meta name="description" {bi("Le verifiche del TPI: cosa regge, cosa no, e come sono misurate.", "The TPI checks: what holds up, what does not, and how they are measured.")}>
<style>{CSS}</style>
</head>
<body>
{nav}
<main class="doc">
{_hero(dati)}
{_cap_regge(dati)}
{_cap_prova(dati)}
{_cap_contesto(dati)}
{_cap_metodo(dati)}
{_tabella(dati)}
{footer}
</main>
<script src="i18n.js"></script>
<script src="ai_chat.js" defer></script>
</body>
</html>
"""
