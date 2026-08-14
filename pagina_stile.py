"""Impianto grafico condiviso dalle pagine del sito.

Stile, aiutanti bilingui e guscio HTML stanno qui e non nelle singole pagine:
due copie dello stesso CSS finiscono sempre per divergere, ed e' esattamente
il difetto che questo progetto passa il tempo a togliere dai numeri.
"""
from __future__ import annotations


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
    return f"""<div class="ev riga">
  <div class="ev-fig">{fig}<span class="ev-lbl" {bi(lab_it, lab_en)}>{lab_it}</span></div>
  <div class="ev-txt" {bi(it, en)}>{it}</div>
</div>"""


# ══ Micrografici: apertura SVG comune ══
_SVG_OPEN = ('<svg class="graf" viewBox="0 0 {w} {h}" role="img" aria-hidden="true" '
             'preserveAspectRatio="xMidYMid meet">')


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

/* ── Impaginazione ──────────────────────────────────────────────
   Due bordi sinistri in tutta la pagina e non uno di piu':
     · colonna di margine (--marg): numeri, etichette, marcatori
     · colonna di testo: titoli, prosa, frasi, didascalie, tabella
   Prima le scritte cadevano su quattro rientri diversi — hero a filo
   pagina, capitoli a +84, frasi a +176, voci del metodo a +108 — e da
   fuori si legge come sciatteria, non come gerarchia. */
:root{--marg:150px;--gutter:26px}
.doc{max-width:1040px;margin:0 auto;padding:0 clamp(20px,5vw,40px) 80px}
.riga{display:grid;grid-template-columns:var(--marg) minmax(0,1fr);gap:0 var(--gutter)}
.prosa{max-width:36em;font-size:16.5px;line-height:1.75;color:var(--ls)}
.prosa strong{color:var(--lp);font-weight:600}
.prosa+.prosa{margin-top:14px}
.prosa.chiusa{margin-top:30px;padding-top:22px;border-top:1px solid var(--sep2);
  font-size:17px;color:var(--lp)}

.hero{padding:clamp(46px,9vw,96px) 0 clamp(30px,5vw,52px);align-items:start}
.eyebrow{font-size:10.5px;letter-spacing:.24em;text-transform:uppercase;color:var(--lt);
  margin-bottom:20px}
h1{font-family:var(--disp);font-weight:500;text-transform:uppercase;
  font-size:clamp(38px,8.4vw,84px);line-height:.92;letter-spacing:-.012em;margin-bottom:24px}
h1 em{font-style:normal;color:var(--orng)}
.lede{max-width:31em;font-size:clamp(16.5px,2.1vw,19px);line-height:1.62;color:var(--ls)}
.lede strong{color:var(--lp);font-weight:600}
.lede.sec{margin-top:16px;font-size:15px;color:var(--lt);max-width:33em}

/* Niente piu' righe verticali fra le cifre: il testo della colonna dopo le
   toccava, e con il ritorno a capo restava un bordo appeso a meta' riga. */
.cifre{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:0 34px;
  margin-top:44px;padding-top:22px;border-top:1px solid var(--sep)}
.cifra{min-width:0}
.cifra b{display:block;font-family:var(--mono);font-weight:500;font-size:clamp(26px,4vw,34px);
  color:var(--orng);letter-spacing:-.03em;line-height:1}
.cifra span{display:block;margin-top:8px;font-size:11px;letter-spacing:.1em;
  text-transform:uppercase;color:var(--lt);line-height:1.5}

.cap{padding:clamp(38px,6vw,64px) 0;border-top:1px solid var(--sep)}
.cap-num{font-family:var(--mono);font-size:11.5px;letter-spacing:.16em;color:var(--orng);
  padding-top:9px}
h2{font-family:var(--disp);font-weight:500;text-transform:uppercase;
  font-size:clamp(23px,3.4vw,34px);line-height:1.06;letter-spacing:.004em;margin-bottom:18px}
h3{font-size:12px;letter-spacing:.14em;text-transform:uppercase;color:var(--lt);
  margin:38px 0 10px}

.ev{padding:17px 0;border-top:1px solid var(--sep)}
.ev:first-of-type{border-top:1px solid var(--sep2)}
.ev-fig{font-family:var(--mono);font-weight:500;font-size:23px;color:var(--orng);
  letter-spacing:-.03em;line-height:1.1}
.ev-lbl{display:block;margin-top:7px;font-family:var(--font);font-size:9.5px;
  letter-spacing:.12em;text-transform:uppercase;color:var(--lt);line-height:1.5}
.ev-txt{font-size:15px;line-height:1.68;color:var(--ls);padding-top:2px;max-width:36em}
/* Nel capitolo delle conseguenze il margine porta un titolo, non una cifra:
   stesso impianto, font di testo perche' una parola in mono si legge peggio. */
.ev-fig.ev-txtfig{font-family:var(--font);font-size:14.5px;font-weight:600;
  color:var(--lp);line-height:1.45;padding-top:3px}
.ev-txt strong{color:var(--lp);font-weight:600}
.ev.muta .ev-fig{color:var(--ls)}

/* L'unico elenco di giocatori della pagina: nome a sinistra, le due posizioni
   a destra, una riga sottile a separarli. */
.nomi{list-style:none;margin:2px 0 0;max-width:36em}
.nomi li{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:0 18px;align-items:baseline;
  padding:9px 0;border-bottom:1px solid var(--sep);font-size:14.5px;color:var(--ls)}
.nomi li:last-child{border-bottom:0}
.nomi li.hd{padding:0 0 6px;border-bottom:1px solid var(--sep2)}
.nomi li.hd .pos{font-family:var(--font);font-size:9.5px;letter-spacing:.12em;
  text-transform:uppercase}
.nomi b{font-weight:600;color:var(--lp)}
.nomi small{margin-left:9px;font-size:11.5px;color:var(--lt)}
.nomi .pos{font-family:var(--mono);font-size:12.5px;color:var(--lt);white-space:nowrap}
.nomi+.didascalia{margin-top:12px}

.graf{display:block;width:100%;height:auto;margin:26px 0 4px;overflow:visible}
.sv-ax{font-family:var(--mono);font-size:9.5px;fill:var(--lt)}
.sv-val{font-family:var(--mono);font-size:10px;fill:var(--ls)}
.sv-dim{font-family:var(--mono);font-size:10.5px;fill:var(--ls)}
.didascalia{font-size:12.5px;color:var(--lt);line-height:1.6;max-width:36em;margin-top:6px}

table{width:100%;border-collapse:collapse;font-size:13.5px;margin-top:6px}
th{text-align:left;font-size:9.5px;letter-spacing:.13em;text-transform:uppercase;
  color:var(--lt);font-weight:500;padding:0 14px 10px 0;border-bottom:1px solid var(--sep2)}
td{padding:13px 14px 13px 0;border-bottom:1px solid var(--sep);color:var(--ls);
  vertical-align:top;line-height:1.55}
tr:hover td{color:var(--lp)}
.t-let{font-family:var(--mono);font-size:11px;color:var(--orng);width:26px;padding-top:15px}
.t-mis{font-family:var(--mono);font-size:12px;color:var(--lp);white-space:nowrap}

/* Il marcatore sta nella colonna di margine, cosi' il titolo del blocco e il
   testo che si apre sotto partono dallo stesso bordo di tutto il resto. */
details{border-top:1px solid var(--sep);padding:15px 0}
details[open]{padding-bottom:22px}
/* Titolo nella colonna di margine e testo in quella di lettura, come le voci
   del capitolo precedente: erano l'unico blocco con un rientro suo. */
summary{cursor:pointer;list-style:none;font-size:14.5px;font-weight:600;color:var(--lp);
  line-height:1.45}
summary::-webkit-details-marker{display:none}
summary::before{content:"+ ";font-family:var(--mono);color:var(--orng);font-size:14px}
details[open] summary::before{content:"\\2212 "}
details.riga>summary{grid-column:1}
details.riga>.prosa{grid-column:2;margin-top:0}
details .prosa{font-size:14.5px}

footer{border-top:1px solid var(--sep);padding:30px 0 0;margin-top:20px;
  font-size:12px;color:var(--lt)}
footer>div{display:flex;flex-wrap:wrap;gap:14px 26px}
footer a{color:var(--ls);text-decoration:none;border-bottom:1px solid var(--lq)}
footer a:hover{color:var(--orng);border-color:var(--orng)}

@media(max-width:900px){
  /* Sotto questa larghezza la colonna di margine mangerebbe la riga di testo:
     tutto torna in colonna unica, e i due bordi diventano uno solo. */
  .riga{grid-template-columns:minmax(0,1fr);gap:0}
  .cap-num{padding:0 0 10px}
  .ev{padding:16px 0}
  .ev-fig{font-size:21px}
  .ev-lbl{display:inline;margin-left:10px}
  .ev-fig.ev-txtfig{margin-bottom:6px}
  .cifre{grid-template-columns:repeat(2,minmax(0,1fr));gap:22px 30px}
  details.riga>summary,details.riga>.prosa{grid-column:auto}
  details .prosa{margin-top:10px}
  .nav-brand small{display:none}
  table{font-size:12.5px}
  .t-mis{white-space:normal}
}
@media(max-width:520px){
  .cifre{grid-template-columns:minmax(0,1fr)}
  td:nth-child(3),th:nth-child(3){display:none}
}
@media print{.nav{display:none}body{background:#fff;color:#000}}
"""


# ══════════════════════════════════════════════════════════════════
# Guscio: testata, navigazione, chiusura
# ══════════════════════════════════════════════════════════════════
def nav(pagina: str) -> str:
    """Barra in cima. `pagina` e' la voce da NON mostrare: e' quella corrente."""
    voci = [("dashboard_serie_a.html", "Classifica", "Ranking"),
            ("validazione.html", "Validazione", "Validation"),
            ("guida_completa.html", "Metodo", "Method"),
            ("dashboard_pro.html", "TPI Pro", "TPI Pro")]
    link = "".join(
        f'<a class="nav-btn" href="{h}" {bi(it, en)}>{it}</a>'
        for h, it, en in voci if h != pagina)
    home = ("" if pagina == "index.html"
            else '<a class="nav-btn pri" href="index.html">Homepage</a>')
    return f"""<nav class="nav">
  <div class="nav-brand">TPI <small>Serie A 25/26</small></div>
  <div class="nav-sp"></div>
  <span data-i18n-switcher></span>
  {link}
  {home}
</nav>"""


def footer(voci: list[tuple[str, str, str]]) -> str:
    link = "".join(f'<a href="{h}" {bi(it, en)}>{it}</a>' for h, it, en in voci)
    return f"""<footer class="riga">
  <div></div>
  <div>
    <span>Raffaele Ciccone &middot; Serie A Scout Index</span>
    {link}
  </div>
</footer>"""


def guscio(titolo: str, desc_it: str, desc_en: str, pagina: str, corpo: str,
           voci_footer: list[tuple[str, str, str]]) -> str:
    """La pagina completa: stesso <head>, stessa nav, stesso piede per tutte."""
    return f"""<!DOCTYPE html>
<html lang="it">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>{titolo}</title>
<meta name="description" {bi(desc_it, desc_en)}>
<style>{CSS}</style>
</head>
<body>
{nav(pagina)}
<main class="doc">
{corpo}
{footer(voci_footer)}
</main>
<script src="i18n.js"></script>
<script src="ai_chat.js" defer></script>
</body>
</html>
"""
