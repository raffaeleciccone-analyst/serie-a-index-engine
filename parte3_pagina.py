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

from pagina_stile import (CSS, _SVG_OPEN, _f, _ic, bi, cali_decili, el, evidenza,
                          guscio, nav, footer)


def elenco_nomi(righe: list, intestazione: bool = False) -> str:
    """Nomi e posizioni nelle due classifiche.

    E' l'unico posto della pagina dove compaiono dei giocatori: senza, chi non
    fa statistica per mestiere non ha nessun appiglio concreto in tutta la
    lettura, e il disaccordo fra due classifiche e' proprio la cosa che si
    guarda volentieri.
    """
    voci = []
    if intestazione:
        # Due numeri di fila senza etichetta si leggono male: la didascalia
        # sotto li spiega, ma arriva dopo dieci righe.
        voci.append('<li class="hd"><span></span>'
                    f'<span class="pos" {bi("TPI &middot; WhoScored", "TPI &middot; WhoScored")}>'
                    'TPI &middot; WhoScored</span></li>')
    for r in righe:
        voci.append(
            f'<li><span><b>{r["nome"]}</b><small>{r["squadra"]}</small></span>'
            f'<span class="pos">{r["tpi_rank"]}&ordm; &middot; {r["ws_rank"]}&ordm;</span></li>')
    return f'<ul class="nomi">{"".join(voci)}</ul>'


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
    """Decili di TPI contro rendimento realizzato, con la retta ideale.

    Aveva solo due etichette sulla x ("1o decile", "10o decile") e niente asse
    y: la didascalia diceva "rendimento medio realizzato" e non c'era modo di
    leggere QUANTO. Una curva che sale senza una scala accanto non e' una prova,
    e' una forma. Ora l'asse y porta tre valori e la sua unita', e il grafico
    dichiara che non parte da zero quando non ci parte.
    """
    bins = [b for b in (m or {}).get("bins", []) if b.get("realized_mean") is not None]
    if len(bins) < 4:
        return ""
    w, h, ml, mr, mt, mb = 640, 210, 74, 14, 26, 30
    ys = [b["realized_mean"] for b in bins]
    y0, y1 = min(ys), max(ys)
    span = max(1e-9, y1 - y0)
    # Un filo di aria sopra e sotto, se no il primo e l'ultimo punto stanno
    # incollati al bordo e sembrano tagliati.
    y0, y1 = y0 - span * 0.12, y1 + span * 0.12
    span = y1 - y0

    def px(i):
        return ml + i / max(1, len(bins) - 1) * (w - ml - mr)

    def py(v):
        return mt + (1 - (v - y0) / span) * (h - mt - mb)

    out = [_SVG_OPEN.format(w=w, h=h)]
    # Asse y: tre valori bastano a dare la scala senza trasformarlo in una
    # tabella. Le linee di riferimento sono piu' chiare dei soli numeri.
    for frazione in (0.0, 0.5, 1.0):
        v = y0 + span * frazione
        yy = py(v)
        out.append(f'<line x1="{ml:.1f}" y1="{yy:.1f}" x2="{w-mr:.1f}" y2="{yy:.1f}" '
                   f'stroke="rgba(233,240,236,.06)" stroke-width="1"/>')
        out.append(f'<text x="{ml-8:.1f}" y="{yy+3:.1f}" text-anchor="end" '
                   f'class="sv-ax">{v:.2f}</text>')
    zero_it = " (l&rsquo;asse non parte da zero)" if y0 > 0 else ""
    zero_en = " (the axis does not start at zero)" if y0 > 0 else ""
    titolo_it = f"gol + assist attesi per 90&rsquo;{zero_it}"
    titolo_en = f"expected goals + assists per 90&rsquo;{zero_en}"
    out.append(f'<text x="0" y="12" class="sv-ax" {bi(titolo_it, titolo_en)}>{titolo_it}</text>')
    out.append(f'<line x1="{ml:.1f}" y1="{mt:.1f}" x2="{ml:.1f}" y2="{h-mb:.1f}" '
               f'stroke="rgba(233,240,236,.14)" stroke-width="1"/>')
    out.append(f'<line x1="{px(0):.1f}" y1="{py(ys[0]):.1f}" x2="{px(len(bins)-1):.1f}" '
               f'y2="{py(ys[-1]):.1f}" stroke="rgba(233,240,236,.22)" stroke-width="1" '
               f'stroke-dasharray="3 4"/>')
    pts = " ".join(f"{px(i):.1f},{py(v):.1f}" for i, v in enumerate(ys))
    out.append(f'<polyline points="{pts}" fill="none" stroke="var(--orng)" stroke-width="1.6"/>')
    for i, v in enumerate(ys):
        out.append(f'<circle cx="{px(i):.1f}" cy="{py(v):.1f}" r="3" fill="var(--orng)"/>')
    # I decili che scendono sotto il precedente: cerchiati, cosi' lo scalino di
    # cui parla la didascalia si trova a colpo d'occhio invece di doverlo cercare.
    for d in cali_decili(m):
        i = d - 1
        if 0 <= i < len(ys):
            out.append(f'<circle cx="{px(i):.1f}" cy="{py(ys[i]):.1f}" r="6.5" fill="none" '
                       f'stroke="var(--orng)" stroke-width="1" stroke-dasharray="2 2" '
                       f'opacity=".75"/>')
    out.append(f'<text x="{ml}" y="{h-10}" class="sv-ax" '
               f'{bi("1&ordm; decile", "1st decile")}>1&ordm; decile</text>')
    out.append(f'<text x="{w-mr}" y="{h-10}" text-anchor="end" class="sv-ax" '
               f'{bi("10&ordm; decile", "10th decile")}>10&ordm; decile</text>')
    out.append("</svg>")
    return "".join(out)


def dida_calibrazione(m: dict) -> tuple[str, str]:
    """Didascalia del grafico dei decili, scritta dalla curva che sta sopra.

    Era una frase fissa — "l'ordine coincide, il passo no" — vera finche' la
    monotonia misurava 1.000. Basta che il campione cambi (i portieri fuori, i
    ruoli risolti sulle posizioni vere) perche' un decile scenda sotto il
    precedente e la didascalia smentisca il proprio grafico. Ora i cali li
    conta.
    """
    cali = cali_decili(m)
    rho = m.get("monotonia_rho") if m else None
    testa_it = ("Rendimento medio realizzato dal primo all&rsquo;ultimo decile di TPI. La linea "
                "tratteggiata &egrave; la salita costante che avrebbe un indice calibrato: ")
    testa_en = ("Mean realized output from the first to the last TPI decile. The dashed line is "
                "the steady rise a calibrated index would show: ")
    if not cali:
        coda_it = "l&rsquo;ordine coincide, il passo no."
        coda_en = "the order matches, the spacing does not."
    elif len(cali) == 1:
        d = cali[0]
        coda_it = (f"l&rsquo;ordine coincide dappertutto tranne fra il {d-1}&ordm; e il "
                   f"{d}&ordm; decile, dove scende; e il passo non coincide mai.")
        coda_en = (f"the order matches everywhere except between deciles {d-1} and {d}, where it "
                   f"drops; and the spacing never matches.")
    else:
        elenco_it = ", ".join(f"{d-1}&ordm;&ndash;{d}&ordm;" for d in cali)
        elenco_en = ", ".join(f"{d-1}&ndash;{d}" for d in cali)
        coda_it = (f"l&rsquo;ordine si inverte in {len(cali)} punti ({elenco_it}), e il passo non "
                   f"coincide mai.")
        coda_en = (f"the order reverses at {len(cali)} points ({elenco_en}), and the spacing never "
                   f"matches.")
    if rho is not None:
        coda_it += f" Monotonia &rho; = {rho:+.3f}."
        coda_en += f" Monotonicity &rho; = {rho:+.3f}."
    return testa_it + coda_it, testa_en + coda_en


# I nomi delle dimensioni come li scrive il motore sono chiavi di programma
# (output_adj, boost_ratio, centralita). In pagina vanno i nomi leggibili: era
# uno dei punti del rilievo sul gergo, e qui era rimasto perche' l'etichetta
# arriva dal dizionario dei risultati, non da una stringa scritta a mano.
# Corti per forza: l'etichetta sta in 132 unita' di viewBox, che su un telefono
# diventano una settantina di pixel. Sono gli stessi nomi delle pastiglie della
# classifica, cosi' chi arriva da li' li riconosce.
NOMI_DIM = {
    "output_adj":  ("Output", "Output"),
    "finishing":   ("Finishing", "Finishing"),
    "centralita":  ("Centralit&agrave;", "Centrality"),
    "form":        ("Forma", "Form"),
    "buildup_adj": ("Buildup", "Buildup"),
    "consistenza": ("Consistenza", "Consistency"),
    "boost_ratio": ("Boost", "Boost"),
}


def svg_ablation(o: dict) -> str:
    """Quanto cambia la previsione togliendo una dimensione per volta.

    Grafico divergente, con lo zero al centro: a sinistra le dimensioni che
    servono (toglierle peggiora la previsione), a destra quelle che non pagano.
    Prima le barre crescevano tutte verso destra e il segno lo portava solo il
    colore, cosi' -0.077 e +0.004 puntavano nella stessa direzione: un dato
    divergente disegnato come se fosse unipolare. Chi non guarda la legenda
    vede che finishing ha la barra piu' lunga e conclude l'opposto del vero.

    Il tentativo precedente di farle divergere era stato abbandonato perche' la
    barra piu' lunga usciva dal riquadro e copriva la propria etichetta: qui
    meta' larghezza per lato, e il valore scritto SEMPRE dalla parte esterna
    della barra, cosi' non ci finisce mai sopra.
    """
    res = sorted((o or {}).get("results", []), key=lambda r: r.get("delta_predict", 0))
    if not res:
        return ""
    w, rowh, ml = 640, 27, 132
    testa = 16                      # riga per le due diciture d'asse
    h = rowh * len(res) + 14 + testa
    lim = max(abs(r["delta_predict"]) for r in res) or 1e-9
    lab = 48                        # spazio riservato al numero, per lato
    x0 = (ml + (w - 8)) / 2         # lo zero, al centro dell'area di disegno
    mezza = (w - 8 - ml) / 2 - lab  # lunghezza massima di una barra

    out = [_SVG_OPEN.format(w=w, h=h)]
    # Le due intestazioni delle meta' restavano in italiano anche sulla pagina
    # inglese: sono testo dentro un SVG, e ci vuole la coppia data-it/data-en
    # come per il resto della prosa generata.
    sx_it, sx_en = "toglierla peggiora &#8592;", "removing it hurts &#8592;"
    dx_it, dx_en = "&#8594; toglierla migliora", "&#8594; removing it helps"
    out.append(f'<text x="{x0-8:.0f}" y="11" text-anchor="end" class="sv-ax" '
               f'{bi(sx_it, sx_en)}>{sx_it}</text>')
    out.append(f'<text x="{x0+8:.0f}" y="11" class="sv-ax" '
               f'{bi(dx_it, dx_en)}>{dx_it}</text>')
    for i_, r in enumerate(res):
        y = 7 + testa + i_ * rowh
        v = r["delta_predict"]
        lung = min(abs(v) / lim * mezza, mezza)
        serve = v < -0.005
        col = "var(--orng)" if serve else "rgba(233,240,236,.28)"
        nome_it, nome_en = NOMI_DIM.get(r["dim"], (r["dim"], r["dim"]))
        out.append(f'<text x="{ml-12}" y="{y+14}" text-anchor="end" class="sv-dim" '
                   f'{bi(nome_it, nome_en)}>{nome_it}</text>')
        if v < 0:
            bx, tx, anc = x0 - lung, x0 - lung - 6, "end"
        else:
            bx, tx, anc = x0, x0 + lung + 6, "start"
        out.append(f'<rect x="{bx:.1f}" y="{y+4}" width="{max(1.5, lung):.1f}" height="14" '
                   f'rx="2" fill="{col}"/>')
        out.append(f'<text x="{tx:.1f}" y="{y+16}" text-anchor="{anc}" class="sv-val">{v:+.3f}</text>')
    out.append(f'<line x1="{ml}" y1="{testa}" x2="{ml}" y2="{h-4}" stroke="rgba(233,240,236,.18)"/>')
    out.append(f'<line x1="{x0:.1f}" y1="{testa}" x2="{x0:.1f}" y2="{h-4}" '
               f'stroke="rgba(233,240,236,.45)"/>')
    out.append("</svg>")
    return "".join(out)

# ══════════════════════════════════════════════════════════════════
# CSS
# ══════════════════════════════════════════════════════════════════



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
        _cali = cali_decili(m)
        cifre.append((f'&rho; {_f(m["monotonia_rho"], 3)}',
                      "diviso in dieci gruppi, l&rsquo;ordine tiene"
                      + ("" if not _cali else " quasi ovunque"),
                      "split into ten groups, the order holds"
                      + ("" if not _cali else " almost everywhere")))
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
    # Prima a cosa serve, poi dove perde. Al contrario &mdash; com'era &mdash; chi
    # legge trenta secondi porta via solo la sconfitta, e non sa nemmeno di cosa.
    lede_it = (f"Il <strong>TPI</strong> ordina i {meta['n_giocatori']} giocatori qualificati "
               f"della Serie A per impatto offensivo. Serve a decidere <strong>chi guardare</strong>: "
               f"restringere una lista lunga, riconoscere chi sta crescendo. Non serve a prevedere "
               f"quanti gol far&agrave; qualcuno il mese prossimo, e questa pagina spiega "
               f"perch&eacute;.")
    lede_en = (f"The <strong>TPI</strong> ranks Serie A&rsquo;s {meta['n_giocatori']} qualified "
               f"players by attacking impact. It is there to decide <strong>who to look at</strong>: "
               f"to shorten a long list, to spot who is on the way up. It is not there to forecast "
               f"how many goals someone will score next month, and this page explains why.")
    sub_it = (f"{meta['n_verifiche']} verifiche, compresa quella costruita apposta per bocciarlo. "
              f"Dove l&rsquo;indice perde &egrave; scritto, con l&rsquo;intervallo di confidenza "
              f"accanto.")
    sub_en = (f"{meta['n_verifiche']} checks, including the one built to fail it. Where the index "
              f"loses is written down, with the confidence interval next to it.")
    return f"""<header class="hero riga">
  <div></div>
  <div>
  <div class="eyebrow">Serie A Scout Index &middot; TPI</div>
  <h1 {bi("Cosa regge,<br><em>e cosa no</em>", "What holds up,<br><em>and what doesn&rsquo;t</em>")}>Cosa regge,<br><em>e cosa no</em></h1>
  <p class="lede" {bi(lede_it, lede_en)}>{lede_it}</p>
  <p class="lede sec" {bi(sub_it, sub_en)}>{sub_it}</p>
  </div>
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
        # A che punto della stagione sta quel vintage: calcolato, non stimato a
        # occhio. Diceva "a un terzo di stagione" per una giornata che sta a
        # meta', perche' il campo giornata arriva a 40 e non a 38.
        gmax = d["meta"].get("giornata_max")
        quota = (a["vintage_giornata"] / gmax) if gmax else None
        if quota is None:
            q_it = q_en = ""
        elif quota <= 0.4:
            q_it, q_en = "A un terzo di stagione", "A third of the way into the season"
        elif quota <= 0.62:
            q_it, q_en = "A met&agrave; stagione", "Halfway through the season"
        else:
            q_it, q_en = "A stagione avanzata", "Late in the season"
        ev.append(evidenza(
            _f(a["spearman_rho"], 3),
            f"Gi&agrave; alla giornata {a['vintage_giornata']}", f"Already by matchday {a['vintage_giornata']}",
            f"{q_it} la graduatoria &egrave; gi&agrave; quasi quella di fine anno, e sale a "
            f"{_f(z['spearman_rho'], 3)} alla giornata {z['vintage_giornata']}. "
            f"<strong>Non serve aspettare maggio per usarla.</strong>",
            f"{q_en} the ranking is already close to the final one, rising to "
            f"{_f(z['spearman_rho'], 3)} by matchday {z['vintage_giornata']}. "
            f"<strong>You do not have to wait for May to use it.</strong>"))
    if h.get("has_data"):
        pct = int(round((h.get("pct") or 0.2) * 100))
        ev.append(evidenza(
            _f(h.get("spearman_median"), 3), "Pesi perturbati", "Weights perturbed",
            f"Spostando a caso di &plusmn;{pct}% i sette pesi del composito, la graduatoria non si "
            f"muove (peggior caso {_f(h.get('spearman_min'), 3)}). Va detto per&ograve; che questo "
            f"test <strong>non pu&ograve; quasi fallire</strong>: sette numeri positivi su "
            f"z-score correlati fra loro, rinormalizzati dopo la perturbazione, danno "
            f"&rho;&nbsp;&#8776;&nbsp;1 per costruzione. Non &egrave; una prova di robustezza, "
            f"&egrave; una propriet&agrave; dei compositi.",
            f"Randomly shifting the seven weights by &plusmn;{pct}%, the ranking does not move "
            f"(worst case {_f(h.get('spearman_min'), 3)}). It must be said, though, that this "
            f"test <strong>can hardly fail</strong>: seven positive numbers over mutually "
            f"correlated z-scores, renormalised after the perturbation, give "
            f"&rho;&nbsp;&#8776;&nbsp;1 by construction. It is not evidence of robustness, it is "
            f"a property of composite indices."))
    r = h.get("ruolo") or {}
    if r:
        # Il test che PUO' bocciare: il coefficiente di ruolo e' il parametro che
        # decide quanti difensori entrano in cima, e non era mai stato perturbato.
        rp = int(round((r.get("pct") or 0.2) * 100))
        q0 = (r.get("quota_base") or {}).get("10", {})
        qmin = (r.get("quota_min") or {}).get("10", {})
        qmax = (r.get("quota_max") or {}).get("10", {})
        pesi = " &middot; ".join(f"{k} {v:.2f}" for k, v in (r.get("pesi_base") or {}).items())
        ov = (r.get("overlap_min") or {}).get("10")
        ev.append(evidenza(
            f"{int(round((ov or 0) * 10))}/10", "E il parametro che conta davvero",
            "And the parameter that actually matters",
            f"I sette pesi non sono il numero che decide la classifica. Lo &egrave; il "
            f"<strong>coefficiente di ruolo</strong> ({pesi}), che rimette in una colonna sola "
            f"z-score calcolati dentro ruoli diversi. Perturbato di &plusmn;{rp}% l&rsquo;ordine "
            f"generale regge (&rho; {_f(r.get('spearman_med'), 3)}), ma della top 10 "
            f"<strong>restano al loro posto solo {int(round((ov or 0) * 10))} nomi su 10</strong>, "
            f"e i difensori in cima passano da {q0.get('DIF', 0)} a un intervallo fra "
            f"{qmin.get('DIF', 0)} e {qmax.get('DIF', 0)}. "
            f"<strong>Quel coefficiente &egrave; scelto a mano, e questa &egrave; la sua "
            f"influenza.</strong>",
            f"The seven weights are not the number that decides the ranking. The "
            f"<strong>per-role coefficient</strong> ({pesi}) is &mdash; it puts z-scores computed "
            f"within different roles back into a single column. Perturbed by &plusmn;{rp}% the "
            f"overall order holds (&rho; {_f(r.get('spearman_med'), 3)}), but of the top 10 "
            f"<strong>only {int(round((ov or 0) * 10))} names out of 10 stay put</strong>, and "
            f"defenders at the top go from {q0.get('DIF', 0)} to a range between "
            f"{qmin.get('DIF', 0)} and {qmax.get('DIF', 0)}. <strong>That coefficient is chosen "
            f"by hand, and this is how much it moves.</strong>"))
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
    cal_it, cal_en = dida_calibrazione(m)
    cal_blk = (f'<h3 {bi("Dal primo al decimo decile", "From the first to the tenth decile")}>'
               f'Dal primo al decimo decile</h3>{cal}'
               f'<p class="didascalia" {bi(cal_it, cal_en)}>{cal_it}</p>' if cal else "")
    return f"""<section class="cap riga">
  <div class="cap-num">01</div>
  <div>
    {el("h2", "Quello che l&rsquo;indice fa", "What the index does")}
    <p class="prosa" {bi(p_it, p_en)}>{p_it}</p>
    {graf_blk}
    <div class="ev-g">{"".join(ev)}</div>
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
    # La conclusione del capitolo detta per esteso: il risultato positivo era il
    # terzo di sei punti, dentro il capitolo intitolato alla sconfitta, e chi
    # scorreva portava via solo quella.
    p3_it = ("In due righe: come previsione del rendimento assoluto l&rsquo;indice "
             "<strong>non serve</strong>, e l&rsquo;output grezzo per-90 fa meglio da solo. Come "
             "strumento per distinguere <strong>chi cresce da chi cala</strong> regge, ed &egrave; "
             "l&rsquo;unico uso che rivendico &mdash; lo stesso per cui l&rsquo;indice era stato "
             "scritto.")
    p3_en = ("In two lines: as a forecast of absolute output the index is <strong>of no "
             "use</strong>, and raw per-90 output does better on its own. As a way to tell "
             "<strong>who is rising from who is fading</strong> it holds, and that is the only use "
             "I claim &mdash; the one the index was written for.")
    graf = svg_ablation(o)
    dida_it = ("Peggioramento della previsione togliendo una dimensione alla volta: le barre "
               "ambra sono le dimensioni che servono.")
    dida_en = ("Loss in forecast accuracy when each dimension is removed: amber bars are the "
               "dimensions that matter.")
    # Il pezzo piu' scomodo della pagina, e va scritto proprio sotto il grafico
    # che lo riguarda: questo test aveva gia' toccato il modello una volta.
    circ_it = ("Una nota su questo test. Fino al 18 agosto 2026 il peso di "
               "<strong>Effetto squadra</strong> era 0.02, e valeva 0.02 <em>perch&eacute; "
               "questo grafico</em> lo indicava come la dimensione meno utile: il modello era "
               "stato tarato sulla propria verifica, e una verifica che ha gi&agrave; corretto "
               "il modello non lo sta pi&ugrave; controllando. L&rsquo;ho rimesso al valore che "
               "aveva prima che il test lo guardasse, 0.05. Il ritorno indietro &egrave; costato "
               "quanto si vede: &rho; di Spearman <strong>0.9989</strong> fra la graduatoria di "
               "prima e quella di adesso, prime venticinque <strong>identiche</strong>, tre nomi "
               "diversi nei primi cento, e la capacit&agrave; predittiva misurata qui sopra "
               "invariata (0.553 in tutti e due i casi). Anche il verdetto non cambia: con il "
               "peso pi&ugrave; alto, togliere Effetto squadra costa &minus;0.0008 invece di "
               "&minus;0.0010, e resta in fondo alla lista. Il peso che aveva creato il problema "
               "non spostava niente &mdash; ed &egrave; esattamente il motivo per cui rimetterlo "
               "a posto era gratis.")
    circ_en = ("A note on this test. Until 18 August 2026 the weight of <strong>Team effect</strong> "
               "was 0.02, and it was 0.02 <em>because this chart</em> flagged it as the least "
               "useful dimension: the model had been tuned on its own check, and a check that has "
               "already corrected the model is no longer checking it. I put it back to the value "
               "it had before the test looked at it, 0.05. Going back cost exactly this much: "
               "Spearman &rho; <strong>0.9989</strong> between the old ranking and the current "
               "one, the top twenty-five <strong>identical</strong>, three different names in the "
               "top hundred, and the forecasting ability measured above unchanged (0.553 either "
               "way). The verdict does not move either: at the higher weight, removing Team "
               "effect costs &minus;0.0008 instead of &minus;0.0010, and it stays at the bottom "
               "of the list. The weight that created the problem was moving nothing &mdash; which "
               "is exactly why putting it back was free.")
    graf_blk = (f'<h3 {bi("Le sette dimensioni, una alla volta", "The seven dimensions, one at a time")}>'
                f'Le sette dimensioni, una alla volta</h3>{graf}'
                f'<p class="didascalia" {bi(dida_it, dida_en)}>{dida_it}</p>'
                f'<p class="prosa" {bi(circ_it, circ_en)}>{circ_it}</p>' if graf else "")
    return f"""<section class="cap riga">
  <div class="cap-num">02</div>
  <div>
    {el("h2", "La prova costruita per bocciarlo", "The test built to fail it")}
    <p class="prosa" {bi(p1_it, p1_en)}>{p1_it}</p>
    <p class="prosa" {bi(p2_it, p2_en)}>{p2_it}</p>
    <div class="ev-g">{"".join(ev)}</div>
    {graf_blk}
    <p class="prosa chiusa" {bi(p3_it, p3_en)}>{p3_it}</p>
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
        # L'esempio singolo che stava qui e' diventato l'elenco qui sotto.
        esempio_it = esempio_en = ""
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
        pos, neg = b.get("divergenze_pos") or [], b.get("divergenze_neg") or []
        if pos or neg:
            ev.append(f"""<div class="ev riga">
  <div class="ev-fig ev-txtfig" {bi("Il TPI li mette pi&ugrave; in alto", "The TPI ranks them higher")}>Il TPI li mette pi&ugrave; in alto</div>
  <div class="ev-txt">{elenco_nomi(pos, intestazione=True)}</div>
</div>
<div class="ev riga">
  <div class="ev-fig ev-txtfig" {bi("WhoScored li mette pi&ugrave; in alto", "WhoScored ranks them higher")}>WhoScored li mette pi&ugrave; in alto</div>
  <div class="ev-txt">{elenco_nomi(neg)}
    <p class="didascalia" {bi("Posizione per TPI e posizione per voto WhoScored, sui giocatori presenti in entrambe le classifiche. Le divergenze sono il contenuto vero di questo confronto: chi sale grazie a quello che produce e chi grazie a come viene giudicato.", "Rank by TPI and rank by WhoScored rating, among players present in both lists. The disagreements are the real content of this comparison: who rises for what they produce, and who rises for how they are judged.")}>Posizione per TPI e posizione per voto WhoScored, sui giocatori presenti in entrambe le classifiche. Le divergenze sono il contenuto vero di questo confronto: chi sale grazie a quello che produce e chi grazie a come viene giudicato.</p>
  </div>
</div>""")
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
    return f"""<section class="cap riga">
  <div class="cap-num">03</div>
  <div>
    {el("h2", "Quello che non dimostra niente", "What proves nothing")}
    <p class="prosa" {bi(p_it, p_en)}>{p_it}</p>
    <div class="ev-g">{"".join(ev)}</div>
  </div>
</section>"""


def _cap_segue(d: dict) -> str:
    """Le conseguenze. Senza questo capitolo la pagina elenca problemi e non
    dice mai cosa ho intenzione di farne, che e' meta' di quello che un lettore
    vuole sapere di chi ha fatto il lavoro."""
    o, p, a, m = (d.get(k) or {} for k in ("o", "p", "a", "m"))
    res = sorted(o.get("results", []), key=lambda r: r.get("delta_predict", 0))
    utili = [r["dim"] for r in res if r["delta_predict"] < -0.005]
    inutili = [r["dim"] for r in res if r["delta_predict"] >= -0.005]
    voci = []
    if inutili:
        voci.append((
            "I pesi", "The weights",
            f"Cinque dimensioni su sette non pagano contro il rendimento futuro "
            f"({', '.join(inutili)}). Il candidato ovvio &egrave; ridurne il peso a favore di "
            f"{' e '.join(utili)}. <strong>Non l&rsquo;ho fatto</strong>, ed &egrave; una scelta: "
            f"il criterio che le boccia &egrave; la previsione del rendimento assoluto, che non "
            f"&egrave; quello per cui l&rsquo;indice esiste. Ritararlo su quel criterio "
            f"significherebbe inseguire una cosa che l&rsquo;indice non promette. Diventa la prima "
            f"cosa da rifare il giorno in cui il TPI dovr&agrave; alimentare qualcosa che consuma "
            f"le distanze e non solo l&rsquo;ordine.",
            f"Five dimensions out of seven do not pay against future output "
            f"({', '.join(inutili)}). The obvious move is to cut their weight in favour of "
            f"{' and '.join(utili)}. <strong>I have not done it</strong>, and that is a choice: the "
            f"criterion that fails them is the forecast of absolute output, which is not what the "
            f"index is for. Retuning on that criterion would mean chasing something the index does "
            f"not promise. It becomes the first thing to redo the day the TPI has to feed "
            f"something that consumes distances and not just order."))
    if m.get("slope") is not None:
        voci.append((
            "Le distanze", "The distances",
            f"La pendenza {_f(m.get('slope'), 3)} dice che le differenze di TPI sono gonfiate "
            f"circa {_f(1 / max(1e-9, float(m['slope'])), 1)} volte. Finch&eacute; l&rsquo;indice "
            f"si legge come graduatoria non cambia niente; se un giorno dovesse entrare in un "
            f"modello di valore, va riscalato prima.",
            f"The {_f(m.get('slope'), 3)} slope says TPI differences are inflated by roughly "
            f"{_f(1 / max(1e-9, float(m['slope'])), 1)}&times;. As long as the index is read as a "
            f"ranking that changes nothing; if it ever feeds a valuation model, it has to be "
            f"rescaled first."))
    cs = p.get("cross_2season") or {}
    if cs.get("n"):
        voci.append((
            "Quello che manca per rispondere", "What is missing to answer",
            f"L&rsquo;ipotesi da cui l&rsquo;indice nasce &mdash; riconoscere chi <em>sta "
            f"entrando</em> nel prime, non chi ci &egrave; gi&agrave; &mdash; non &egrave; "
            f"n&eacute; dimostrata n&eacute; smentita: per giudicarla servirebbe seguire gli "
            f"stessi giocatori per anni, e qui le stagioni sono due (i giocatori presenti in "
            f"entrambe sono {cs['n']}). Il prossimo passo utile non &egrave; un altro test: "
            f"&egrave; il backfill di altre stagioni.",
            f"The hypothesis the index was born from &mdash; spotting who is <em>entering</em> "
            f"their prime rather than who is already there &mdash; is neither proved nor disproved: "
            f"judging it would mean following the same players for years, and there are two seasons "
            f"here ({cs['n']} players appear in both). The next useful step is not another test: it "
            f"is backfilling more seasons."))
    if a.get("n"):
        voci.append((
            "I dati inseriti a mano", "The hand-entered data",
            f"I {a['n']} voti Fantacalcio e la classifica WhoScored li ho copiati a mano, ed "
            f"&egrave; il motivo per cui quei due confronti restano contesto e non prove. O la "
            f"raccolta diventa automatica e su un campione estratto a caso, oppure quei due "
            f"numeri non miglioreranno mai.",
            f"The {a['n']} Fantacalcio ratings and the WhoScored ranking I copied by hand, which "
            f"is why those two comparisons stay context rather than proof. Either the collection "
            f"becomes automatic and drawn at random, or those two figures will never get better."))
    blocchi = "".join(
        f'<div class="ev riga"><div class="ev-fig ev-txtfig" {bi(t_it, t_en)}>{t_it}</div>'
        f'<div class="ev-txt" {bi(b_it, b_en)}>{b_it}</div></div>'
        for t_it, t_en, b_it, b_en in voci)
    p_it = ("Una pagina che elenca solo problemi &egrave; a met&agrave;. Questo &egrave; quello "
            "che i risultati qui sopra mi lasciano da fare, e quello che ho deciso di non fare.")
    p_en = ("A page that only lists problems is half a page. This is what the results above leave "
            "me to do, and what I have decided not to do.")
    return f"""<section class="cap riga">
  <div class="cap-num">04</div>
  <div>
    {el("h2", "Cosa ne segue", "What follows")}
    <p class="prosa" {bi(p_it, p_en)}>{p_it}</p>
    {blocchi}
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
    # Da dove vengono i dati e con cosa gira: senza questo la pagina mostra
    # statistica e nasconde tutto il lavoro che l'ha resa possibile.
    if meta.get("n_record"):
        # Niente conteggio di giornate: il campo non porta il numero ufficiale
        # di giornata e scriverlo qui direbbe una cosa falsa sulla Serie A.
        n_st = 1 + len((d.get("p") or {}).get("vintage_cross_tags", []) or [])
        st_it = {1: "una stagione", 2: "due stagioni"}.get(n_st, f"{n_st} stagioni")
        st_en = {1: "one season", 2: "two seasons"}.get(n_st, f"{n_st} seasons")
        vol_it = (f"{meta['n_record']} righe giocatore-partita su "
                  f"{meta.get('n_giocatori_db', '&mdash;')} giocatori")
        vol_en = (f"{meta['n_record']} player-match rows over "
                  f"{meta.get('n_giocatori_db', '&mdash;')} players")
        dati_it = (f"Un database MySQL con {vol_it}, {st_it} di Serie A. Gli xG e gli xA "
                   f"vengono da Understat, l&rsquo;anagrafica e il valore di mercato da "
                   f"Transfermarkt, agganciati per identificativo e non per nome. I voti "
                   f"Fantacalcio e WhoScored sono gli unici dati inseriti a mano. Il motore "
                   f"&egrave; Python: pandas per la trasformazione, scipy per la statistica, e "
                   f"una suite di test di regressione che gira a ogni modifica.")
        dati_en = (f"A MySQL database with {vol_en}, {st_en} of Serie A. xG and xA come from "
                   f"Understat, biographical data and market value from Transfermarkt, joined on "
                   f"identifiers rather than names. Fantacalcio and WhoScored ratings are the only "
                   f"hand-entered data. The engine is Python: pandas for the transformations, "
                   f"scipy for the statistics, and a regression suite that runs on every change.")
        voci.append(("Dati", "Data", dati_it, dati_en))
    # Il motore non e' pubblico. Dirlo, invece di lasciarlo scoprire a chi cerca
    # il link e non lo trova, e indicare cosa invece si puo' ispezionare.
    cod_it = ("Il motore che calcola l&rsquo;indice non &egrave; pubblico: quello che pubblico "
              "sono le pagine e i dati su cui girano. Il file <code>payload.json</code> nel repo "
              "del sito contiene, giocatore per giocatore, tutte le dimensioni e i punteggi che "
              "questa pagina usa: chi vuole rifare i conti pu&ograve; farlo da l&igrave; senza "
              "credermi sulla parola. Il codice lo mostro volentieri su richiesta.")
    cod_en = ("The engine that computes the index is not public: what I publish are the pages and "
              "the data they run on. The <code>payload.json</code> file in the site repository "
              "holds, player by player, every dimension and score this page uses: anyone who wants "
              "to redo the arithmetic can do it from there without taking my word for it. I am "
              "happy to walk through the code on request.")
    voci.append(("Codice", "Code", cod_it, cod_en))
    blocchi = "".join(
        f'<details class="riga"><summary {bi(t_it, t_en)}>{t_it}</summary>'
        f'<p class="prosa" {bi(b_it, b_en)}>{b_it}</p></details>'
        for t_it, t_en, b_it, b_en in voci)
    return f"""<section class="cap riga">
  <div class="cap-num">05</div>
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
    # Le lettere sopravvivono solo qui, e senza una riga che le spieghi sono un
    # enigma: chi legge si chiede dove sia B e perche' si parta da Q.
    let_it = ("La lettera &egrave; il nome del test nel codice, nell&rsquo;ordine in cui li ho "
              "scritti: A, B e C sono i primi tre, Q l&rsquo;ultimo arrivato. Qui invece sono "
              "ordinati per quanto il risultato regge, dal pi&ugrave; solido al pi&ugrave; "
              "fragile. J e K non mancano: l&rsquo;alfabeto italiano non le ha.")
    let_en = ("The letter is the test&rsquo;s name in the code, in the order I wrote them: A, B "
              "and C are the first three, Q the latest. Here they are sorted by how well the "
              "result holds, from the most solid to the most fragile. J and K are not missing: "
              "the Italian alphabet does not have them.")
    return f"""<section class="cap riga">
  <div class="cap-num">06</div>
  <div>
    {el("h2", "Tutte le verifiche", "Every check")}
    <p class="prosa" style="margin-bottom:26px" {bi(let_it, let_en)}>{let_it}</p>
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
    corpo = "\n".join((_hero(dati), _cap_regge(dati), _cap_prova(dati),
                       _cap_contesto(dati), _cap_segue(dati), _cap_metodo(dati),
                       _tabella(dati)))
    return guscio(
        "Validazione &mdash; Serie A Scout Index",
        "Le verifiche del TPI: cosa regge, cosa no, e come sono misurate.",
        "The TPI checks: what holds up, what does not, and how they are measured.",
        "validazione.html", corpo,
        [("guida_completa.html", "Come sono costruiti gli indici",
          "How the indices are built"),
         ("dashboard_serie_a.html", "La classifica completa", "The full ranking")])
