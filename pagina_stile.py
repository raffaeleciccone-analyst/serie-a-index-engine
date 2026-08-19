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


def cali_decili(m: dict) -> list[int]:
    """I decili che scendono sotto il precedente nel grafico di calibrazione.

    Serve a non scrivere "l'ordine tiene" quando in un punto non tiene: la
    frase era fissa su tutte e tre le pagine, ed era vera solo finche' la
    monotonia misurava 1.000. Restituisce il numero d'ordine dei decili bassi
    (2 = il secondo e' sotto il primo).
    """
    ys = [b["realized_mean"] for b in (m or {}).get("bins", [])
          if b.get("realized_mean") is not None]
    return [i + 2 for i in range(len(ys) - 1) if ys[i + 1] < ys[i]]


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
  /* --lt e' il colore di tutto cio' che etichetta: occhielli, etichette delle
     cifre, h3, didascalie, intestazioni di tabella, voci di nav non attive e
     il testo degli assi dei grafici. A .42 dava 3.70:1 sul fondo — sotto la
     soglia AA di 4.5 — e quasi sempre su corpi fra 9 e 12px, cioe' piccolo e
     poco contrastato insieme. A .60 sale a 6.30:1 e resta comunque
     nettamente subordinato al bianco pieno, che sta a 16.07:1. */
  --lp:#ECF2EE;--ls:rgba(233,240,236,.70);--lt:rgba(233,240,236,.60);
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

/* ── Nav: una sola per tutte e cinque le pagine ──────────────────
   Prima ce n'erano tre. Qui pastiglie bordate in maiuscolo, sulle due
   dashboard link piatti con la sottolineatura ambra, e marchi diversi
   ("TPI / Serie A 25/26" contro "Serie A 25/26 / Scout Index" contro
   "Serie A Scout / TPI Pro"): passando da una pagina all'altra sembrava
   di cambiare sito. Vince l'impianto delle dashboard, perche' fa una
   cosa che le pastiglie non facevano — dire dove sei invece di
   nascondere la voce corrente. Le stesse regole stanno in
   assets/dashboard.css per le pagine che non passano di qui. */
.nav{position:sticky;top:0;z-index:60;height:56px;display:flex;align-items:center;gap:14px;
  padding:0 clamp(18px,4vw,40px);background:rgba(10,21,18,.86);
  backdrop-filter:saturate(160%) blur(14px);border-bottom:1px solid var(--sep)}
.nav-brand{display:flex;align-items:center;gap:10px;flex-shrink:0;
  font-family:var(--disp);font-size:19px;font-weight:600;letter-spacing:.4px;
  text-transform:uppercase;white-space:nowrap;text-decoration:none;color:var(--lp)}
.nav-brand small{font-family:var(--mono);font-size:var(--t-lab);font-weight:400;
  letter-spacing:.16em;color:var(--lt);padding-left:10px;
  border-left:1px solid var(--sep)}
/* Sotto i 560px il marchio esce per fare posto alle cinque voci, e la barra
   restava una fila di link nudi con IT/EN in testa: la cosa meno importante nel
   posto piu' importante, e nessun segno di chi sia il sito. Al suo posto entra
   il monogramma — le tre barre della favicon — e la scelta lingua scivola in
   fondo. */
.nav-mark{display:none;align-items:center;flex-shrink:0;color:var(--orng);
  text-decoration:none;line-height:0}
.nav-sp{flex:1}
/* Le voci scorrono invece di andare a capo: restano cinque su una riga sola a
   qualunque larghezza, e la barra resta alta uguale. Le soglie a cui la riga
   si stringe stanno in fondo al foglio. */
.nav-links{display:flex;align-items:center;gap:16px;min-width:0;
  overflow-x:auto;scrollbar-width:none}
.nav-links::-webkit-scrollbar{display:none}
.nav-link{position:relative;display:inline-flex;align-items:center;height:34px;
  padding:0 2px;font-size:12px;font-weight:600;letter-spacing:.02em;
  color:var(--lt);text-decoration:none;white-space:nowrap;flex-shrink:0;
  transition:color .16s}
.nav-link:hover{color:var(--lp)}
.nav-link::after{content:"";position:absolute;left:0;right:0;bottom:7px;height:1.5px;
  background:var(--orng);opacity:0;transition:opacity .16s}
.nav-link:hover::after,.nav-link.on::after{opacity:1}
.nav-link.on{color:var(--lp)}
/* L'unico accento della fila: il TPI Pro e' l'altro indice, non un'altra
   sezione di questo. */
.nav-link.pro{color:var(--orng)}
.nav-link.pro:hover{color:#FFC85A}

/* ── Impaginazione ──────────────────────────────────────────────
   Due bordi sinistri in tutta la pagina e non uno di piu':
     · colonna di margine (--marg): numeri, etichette, marcatori
     · colonna di testo: titoli, prosa, frasi, didascalie, tabella
   Prima le scritte cadevano su quattro rientri diversi — hero a filo
   pagina, capitoli a +84, frasi a +176, voci del metodo a +108 — e da
   fuori si legge come sciatteria, non come gerarchia. */
/* La scala tipografica sotto i 15px era undici corpi diversi in sei pixel
   (14.5, 14, 13.5, 12.5, 12, 11.5, 11, 10.5, 10, 9.5, 9): si legge come
   incoerenza, non come gerarchia. Restano tre gradini, piu' il corpo:
     16.5  prosa        15  testo secondario
     13    didascalie e tabelle      11  etichette e sigle
   Chi ha bisogno di un quarto gradino ha un problema di struttura, non di CSS. */
:root{--marg:150px;--gutter:26px;
  --t-corpo:16.5px;--t-sec:15px;--t-cap:13px;--t-lab:11px}
.doc{max-width:1040px;margin:0 auto;padding:0 clamp(20px,5vw,40px) 80px}
.riga{display:grid;grid-template-columns:var(--marg) minmax(0,1fr);gap:0 var(--gutter)}
.prosa{max-width:36em;font-size:var(--t-corpo);line-height:1.75;color:var(--ls)}
.prosa strong{color:var(--lp);font-weight:600}
.prosa+.prosa{margin-top:14px}
.prosa.chiusa{margin-top:30px;padding-top:22px;border-top:1px solid var(--sep2);
  font-size:17px;color:var(--lp)}

.hero{padding:clamp(46px,9vw,96px) 0 clamp(30px,5vw,52px);align-items:start}
.eyebrow{font-size:var(--t-lab);letter-spacing:.24em;text-transform:uppercase;color:var(--lt);
  margin-bottom:20px}
h1{font-family:var(--disp);font-weight:500;text-transform:uppercase;
  font-size:clamp(38px,8.4vw,84px);line-height:.92;letter-spacing:-.012em;margin-bottom:24px}
h1 em{font-style:normal;color:var(--orng)}
.lede{max-width:31em;font-size:clamp(16.5px,2.1vw,19px);line-height:1.62;color:var(--ls)}
.lede strong{color:var(--lp);font-weight:600}
.lede.sec{margin-top:16px;font-size:var(--t-sec);color:var(--lt);max-width:33em}

/* Niente piu' righe verticali fra le cifre: il testo della colonna dopo le
   toccava, e con il ritorno a capo restava un bordo appeso a meta' riga. */
.cifre{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:0 34px;
  margin-top:44px;padding-top:22px;border-top:1px solid var(--sep)}
.cifra{min-width:0}
.cifra b{display:block;font-family:var(--mono);font-weight:500;font-size:clamp(26px,4vw,34px);
  color:var(--orng);letter-spacing:-.03em;line-height:1}
.cifra span{display:block;margin-top:8px;font-size:var(--t-lab);letter-spacing:.1em;
  text-transform:uppercase;color:var(--lt);line-height:1.5}
/* Le cifre sono il terzo figlio della griglia dell'eroe, ma finche' le colonne
   sono due restano dove stavano: in fondo alla colonna di lettura. */
.hero .cifre{grid-column:2}

.cap{padding:clamp(38px,6vw,64px) 0;border-top:1px solid var(--sep)}
.cap-num{font-family:var(--mono);font-size:var(--t-lab);letter-spacing:.16em;color:var(--orng);
  padding-top:9px}
h2{font-family:var(--disp);font-weight:500;text-transform:uppercase;
  font-size:clamp(23px,3.4vw,34px);line-height:1.06;letter-spacing:.004em;margin-bottom:18px}
/* h3 era 12px maiuscoletto, cioe' PIU' PICCOLO del corpo: non un livello, una
   etichetta. Con un solo gradino di titolazione il capitolo con la tabella da
   sedici righe pesava quanto l'argomento portante di quello prima. Adesso e' un
   sotto-capitolo vero, e sta fra i 34 del capitolo e i 16.5 della prosa. */
h3{font-size:24px;font-family:var(--disp);font-weight:600;letter-spacing:.002em;
  line-height:1.16;color:var(--lp);text-transform:none;margin:34px 0 12px}

.ev{padding:17px 0;border-top:1px solid var(--sep)}
.ev:first-of-type{border-top:1px solid var(--sep2)}
.ev-fig{font-family:var(--mono);font-weight:500;font-size:23px;color:var(--orng);
  letter-spacing:-.03em;line-height:1.1}
.ev-lbl{display:block;margin-top:7px;font-family:var(--font);font-size:var(--t-lab);
  letter-spacing:.12em;text-transform:uppercase;color:var(--lt);line-height:1.5}
.ev-txt{font-size:var(--t-sec);line-height:1.68;color:var(--ls);padding-top:2px;max-width:36em}
/* Nel capitolo delle conseguenze il margine porta un titolo, non una cifra:
   stesso impianto, font di testo perche' una parola in mono si legge peggio. */
.ev-fig.ev-txtfig{font-family:var(--font);font-size:var(--t-sec);font-weight:600;
  color:var(--lp);line-height:1.45;padding-top:3px}
.ev-txt strong{color:var(--lp);font-weight:600}
.ev.muta .ev-fig{color:var(--ls)}

/* L'unico elenco di giocatori della pagina: nome a sinistra, le due posizioni
   a destra, una riga sottile a separarli. */
.nomi{list-style:none;margin:2px 0 0;max-width:36em}
.nomi li{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:0 18px;align-items:baseline;
  padding:9px 0;border-bottom:1px solid var(--sep);font-size:var(--t-sec);color:var(--ls)}
.nomi li:last-child{border-bottom:0}
.nomi li.hd{padding:0 0 6px;border-bottom:1px solid var(--sep2)}
.nomi li.hd .pos{font-family:var(--font);font-size:var(--t-lab);letter-spacing:.12em;
  text-transform:uppercase}
.nomi b{font-weight:600;color:var(--lp)}
.nomi small{margin-left:9px;font-size:var(--t-lab);color:var(--lt)}
.nomi .pos{font-family:var(--mono);font-size:var(--t-cap);color:var(--lt);white-space:nowrap}
.nomi+.didascalia{margin-top:12px}

/* Il testo dentro un SVG con viewBox scala col disegno: le stesse etichette da
   9.5px venivano rese a 5.2px su un telefono da 390px e a 16.1px su un monitor
   da 1920 — piu' grandi della didascalia da 12.5px che spiega il grafico. Una
   tipografia che varia di tre volte non e' una tipografia.
   Due mosse: il disegno smette di ingrandirsi oltre 760px (in alto il fattore
   si ferma a 1.19), e sotto quella soglia le etichette crescono di quanto il
   disegno rimpicciolisce. Il risultato e' ~11px renderizzati a ogni larghezza,
   misurati, invece di 5.2 su telefono. */
:root{--sv-scala:1}
@media(max-width:780px){:root{--sv-scala:1.21}}
@media(max-width:600px){:root{--sv-scala:1.47}}
@media(max-width:460px){:root{--sv-scala:2.05}}
.graf{display:block;width:100%;max-width:760px;height:auto;margin:26px 0 4px;
  overflow:visible}
.sv-ax{font-family:var(--mono);font-size:calc(9.5px * var(--sv-scala));fill:var(--lt)}
.sv-val{font-family:var(--mono);font-size:calc(10px * var(--sv-scala));fill:var(--ls)}
/* Il nome della dimensione ha una colonna di larghezza fissa: se crescesse
   come le altre etichette, su telefono uscirebbe dal riquadro. Cresce fino a
   meta' strada e li' si ferma. */
.sv-dim{font-family:var(--mono);font-size:calc(10.5px * min(var(--sv-scala),1.5));
  fill:var(--ls)}
.didascalia{font-size:var(--t-cap);color:var(--lt);line-height:1.6;max-width:36em;margin-top:6px}

table{width:100%;border-collapse:collapse;font-size:var(--t-cap);margin-top:6px}
th{text-align:left;font-size:var(--t-lab);letter-spacing:.13em;text-transform:uppercase;
  color:var(--lt);font-weight:500;padding:0 14px 10px 0;border-bottom:1px solid var(--sep2)}
td{padding:13px 14px 13px 0;border-bottom:1px solid var(--sep);color:var(--ls);
  vertical-align:top;line-height:1.55}
tr:hover td{color:var(--lp)}
.t-let{font-family:var(--mono);font-size:var(--t-lab);color:var(--orng);width:26px;padding-top:15px}
.t-mis{font-family:var(--mono);font-size:var(--t-cap);color:var(--lp);white-space:nowrap}

/* Il marcatore sta nella colonna di margine, cosi' il titolo del blocco e il
   testo che si apre sotto partono dallo stesso bordo di tutto il resto. */
details{border-top:1px solid var(--sep);padding:15px 0}
details[open]{padding-bottom:22px}
/* Titolo nella colonna di margine e testo in quella di lettura, come le voci
   del capitolo precedente: erano l'unico blocco con un rientro suo. */
summary{cursor:pointer;list-style:none;font-size:var(--t-sec);font-weight:600;color:var(--lp);
  line-height:1.45}
summary::-webkit-details-marker{display:none}
summary::before{content:"+ ";font-family:var(--mono);color:var(--orng);font-size:var(--t-sec)}
details[open] summary::before{content:"\\2212 "}
details.riga>summary{grid-column:1}
details.riga>.prosa{grid-column:2;margin-top:0}
details .prosa{font-size:var(--t-sec)}

footer{border-top:1px solid var(--sep);padding:30px 0 0;margin-top:20px;
  font-size:var(--t-cap);color:var(--lt)}
footer>div{display:flex;flex-wrap:wrap;justify-content:center;text-align:center;
  gap:14px 26px}
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
  table{font-size:var(--t-cap)}
  .t-mis{white-space:normal}
  /* Qui la griglia e' a colonna unica: una seconda colonna non esiste. */
  .hero .cifre{grid-column:auto}
}

/* ── Sopra i 1500px ──────────────────────────────────────────────────────
   Su un monitor grande la pagina era una colonna in mezzo allo schermo: il
   contenitore si ferma a 1040px, la colonna di margine ne prende altri 176 e
   il testo e' tappato a 36em, cosi' a 1920px l'inchiostro occupava il 41%
   della larghezza e il titolo cominciava a 656px dal bordo.

   La misura del testo NON si tocca: 36em sono ~75 battute per riga, ed e'
   quella che rende la prosa leggibile. Quello che cambia e' dove va tutto il
   resto — le cifre e le voci dei capitoli — che finora si accontentava di
   accodarsi sotto al testo lasciando vuota mezza pagina. La pagina si
   allarga, la riga di lettura resta quella. */
@media(min-width:1500px){
  .doc{max-width:1340px}

  /* L'eroe passa a tre colonne. Le cifre stavano sotto il sommario e la fascia
     destra restava vuota proprio nella prima schermata, che e' quella che si
     vede aprendo la pagina. */
  .hero{grid-template-columns:var(--marg) minmax(0,1fr) 268px}
  .hero .cifre{grid-column:3;grid-row:1;align-self:end;
    display:flex;flex-direction:column;gap:24px;
    margin:0;padding:6px 0 6px 32px;
    border-top:0;border-left:1px solid var(--sep)}
  .hero .cifra b{font-size:30px}

  /* Le voci dei capitoli sono elenchi di cose parallele, non una sequenza da
     leggere in ordine: a due a due riempiono la riga invece di lasciarla a
     meta'. Dentro ogni colonna il margine si stringe, se no al testo
     resterebbero quattro parole per riga.
     Nota: qui i bordi sinistri diventano quattro invece dei due che il resto
     dell'impaginazione tiene. E' il prezzo della colonna doppia, non una
     svista. */
  .ev-g{--marg:150px;display:grid;grid-template-columns:repeat(2,minmax(0,1fr));
    gap:0 44px}
  .ev-g .ev{border-top:1px solid var(--sep)}
  .ev-g .ev:nth-child(-n+2){border-top:1px solid var(--sep2)}
  .ev-g .ev-txt{max-width:none}
}
/* Le cinque voci chiedono 336px, e non si stringono: quello che cede e' il
   marchio, prima la stagione e poi tutto. Le soglie sono misurate, non a
   occhio — con il marchio in riga fino a 420px l'ultima voce, TPI Pro, finiva
   fuori dallo schermo su qualunque telefono, e nessuno pensa a trascinare di
   lato un'intestazione. Sotto i 400px scorre comunque: li' non ci sta niente. */
@media(max-width:700px){
  .nav-brand small{display:none}
}
@media(max-width:560px){
  .nav{gap:10px;padding:0 14px}
  .nav-brand{display:none}
  .nav-mark{display:inline-flex}
  [data-i18n-switcher]{order:9}
}
@media(max-width:440px){
  .nav{height:46px;padding:0 8px;gap:8px}
  .nav-links{gap:9px}
  .nav-link{font-size:11px;height:28px}
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
# Le cinque pagine del sito, nell'ordine in cui compaiono in ogni nav. L'ordine
# e' quello di lettura — si entra dalla homepage, si guarda la classifica, poi
# si chiede se regge e come e' fatta — e non cambia da una pagina all'altra:
# una voce che si sposta costringe a rileggere la fila ogni volta.
VOCI = [("index.html", "Homepage", "Homepage"),
        ("dashboard_serie_a.html", "Classifica", "Ranking"),
        ("validazione.html", "Validazione", "Validation"),
        ("guida_completa.html", "Metodo", "Method"),
        ("dashboard_pro.html", "TPI Pro", "TPI Pro")]


def nav(pagina: str) -> str:
    """Barra in cima. `pagina` e' quella corrente: resta in fila, sottolineata.

    Prima la voce corrente veniva tolta, e cinque pagine mostravano quattro
    file diverse: chi arrivava da un link non aveva modo di sapere dove fosse
    finito. Adesso la fila e' sempre la stessa e cambia solo cosa e' acceso.
    """
    link = "".join(
        '<a class="nav-link{cls}" href="{h}"{cur} {b}>{it}</a>'.format(
            cls=("".join((" on" if h == pagina else "",
                          " pro" if h == "dashboard_pro.html" else ""))),
            h=h, cur=' aria-current="page"' if h == pagina else "",
            b=bi(it, en), it=it)
        for h, it, en in VOCI)
    return f"""<nav class="nav">
  <a class="nav-brand" href="index.html">Serie A Scout <small>25/26</small></a>
  <a class="nav-mark" href="index.html" aria-label="Serie A Scout Index" title="Serie A Scout Index"><svg viewBox="0 0 32 32" width="19" height="19" aria-hidden="true" focusable="false"><rect x="6" y="19" width="5" height="7" fill="currentColor"/><rect x="13.5" y="13" width="5" height="13" fill="currentColor"/><rect x="21" y="6" width="5" height="20" fill="currentColor"/></svg></a>
  <div class="nav-sp"></div>
  <span data-i18n-switcher></span>
  <div class="nav-links">{link}</div>
</nav>"""


def footer(voci: list[tuple[str, str, str]]) -> str:
    """Il piede non sta piu' nella colonna di lettura ma in mezzo alla pagina.

    Era un `.riga` come tutto il resto, quindi partiva dal bordo del testo con
    la colonna di margine vuota accanto: su uno schermo largo restava un
    grumo di scritte spinto a destra, sotto una riga che invece attraversa
    tutta la pagina. Una firma si mette al centro.
    """
    link = "".join(f'<a href="{h}" {bi(it, en)}>{it}</a>' for h, it, en in voci)
    return f"""<footer>
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
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'><rect width='32' height='32' rx='7' fill='%230A1512'/><rect x='6' y='19' width='5' height='7' fill='%23FFB020'/><rect x='13.5' y='13' width='5' height='13' fill='%23FFB020'/><rect x='21' y='6' width='5' height='20' fill='%23FFB020'/></svg>">
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
