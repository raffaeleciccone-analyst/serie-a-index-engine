"""Genera `guida_completa.html`: come e' costruito l'indice.

I pesi, i parametri e le soglie li legge da `parte1_analisi.Config`, cioe' dal
motore che li usa davvero, e i risultati da `validazione_dati.json`. La guida
precedente era scritta a mano e alcune sue pagine descrivevano formule che il
codice non eseguiva piu' — la consistenza come 1&minus;IQR/mediana, per dire,
sostituita da un pezzo che nel frattempo era cambiato.

La divisione con la pagina di validazione e' netta: qui c'e' **come e'
costruito**, li' c'e' **quanto regge**. I tre capitoli di statistica che stavano
in fondo alla guida (test A, B, C) sono spariti: erano gli stessi numeri, scritti
due volte, e divergevano.

Uso:  python pagina_guida.py
"""
from __future__ import annotations

import json
import config
import logging
import math
import os
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from pagina_stile import assicura_css, _SVG_OPEN, _f, bi, el, evidenza, guscio

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("pagina_guida")

BASE_DIR = Path(__file__).parent
OUTPUT_DIR = config.cartella_uscita(BASE_DIR)
# Il repo del sito di QUESTA lega: era "serie-a-index" per tutte, quindi
# generare la Premier scriveva le sue pagine nel repo della Serie A.
DEMO_DIR = config.cartella_pubblicazione(BASE_DIR.parent)

# Nome leggibile e una riga sul perche', per ognuna delle sette dimensioni.
# La formula e i pesi arrivano dal motore: qui c'e' solo la prosa.
DIMENSIONI = {
    "output_adj": (
        "Output offensivo", "Attacking output",
        "(xG + xA) / 90 &divide; SOS",
        "Quanto un giocatore produce per novanta minuti, diviso per la "
        "difficolt&agrave; media di chi ha affrontato. &Egrave; il segnale "
        "dominante: tutto il resto serve a correggerlo o a spiegarlo.",
        "How much a player produces per ninety minutes, divided by the average "
        "difficulty of the opponents he faced. It is the dominant signal: "
        "everything else is there to correct or explain it."),
    "finishing": (
        "Finalizzazione", "Finishing",
        "(gol &minus; xG) / &radic;xG",
        "Quanto un giocatore segna sopra o sotto quello che le sue occasioni "
        "valevano. La radice al denominatore evita che chi tira pochissimo "
        "finisca in cima per due tiri fortunati. Rigori esclusi.",
        "How much a player scores above or below what his chances were worth. "
        "The square root in the denominator stops players who barely shoot from "
        "topping the list on two lucky finishes. Penalties excluded."),
    "centralita": (
        "Centralit&agrave;", "Centrality",
        "(xG + xA del giocatore) / xG della squadra &times; 100",
        "Quanta parte della produzione offensiva della squadra passa da lui. "
        "&Egrave; shrinkata verso la media: chi ha giocato poco non sale in cima "
        "per una partita in cui la squadra ha creato solo con lui.",
        "How much of the team&rsquo;s attacking production runs through him. It "
        "is shrunk towards the mean: a player with few minutes does not top the "
        "list because of one match where the team only created through him."),
    "form": (
        "Forma recente", "Recent form",
        "EWMA(output per-90), &alpha; = {ewma}",
        "Una media che pesa di pi&ugrave; le ultime partite. Dice dove sta andando un "
        "giocatore adesso, non quanto ha fatto da agosto.",
        "A moving average that weighs recent matches more. It says where a "
        "player is heading now, not what he did since August."),
    "buildup_adj": (
        "Buildup", "Buildup",
        "xGBuildup / 90 &divide; SOS",
        "Il coinvolgimento nella manovra che porta al tiro, <strong>escludendo "
        "il tiro e l&rsquo;assist</strong>. Serve a vedere chi costruisce senza "
        "comparire nel tabellino: per costruzione non si sovrappone "
        "all&rsquo;output.",
        "Involvement in the build-up that leads to a shot, <strong>excluding the "
        "shot and the assist</strong>. It shows who builds without appearing on "
        "the scoresheet: by construction it does not overlap with output."),
    "consistenza": (
        "Consistenza", "Consistency",
        "media / (media + deviazione standard)",
        "Contributo regolare partita dopo partita, contro produzione a sprazzi. "
        "La versione precedente usava 1 &minus; IQR/mediana e su una "
        "distribuzione piena di zeri collassava: era una dimensione morta.",
        "A steady contribution match after match, against production in bursts. "
        "The previous version used 1 &minus; IQR/median and collapsed on a "
        "zero-inflated distribution: it was a dead dimension."),
    "boost_ratio": (
        "Effetto squadra", "Team effect",
        "exp( log(xG con lui / xG senza lui) &times; n/(n+{bk}) )",
        "Quanto la squadra produce con lui in campo rispetto a senza, "
        "compresso verso il neutro in base a quante partite &laquo;senza&raquo; "
        "esistono davvero. Pesa poco proprio perch&eacute; se ne fida poco.",
        "How much the team produces with him on the pitch versus without, "
        "shrunk towards neutral according to how many &laquo;without&raquo; "
        "matches actually exist. It weighs little precisely because it is "
        "trusted little."),
}


# ══════════════════════════════════════════════════════════════════
# Grafici
# ══════════════════════════════════════════════════════════════════
def svg_pesi(pesi: dict) -> str:
    """I sette pesi del TPI, in scala."""
    voci = sorted(pesi.items(), key=lambda kv: -kv[1])
    w, rowh, ml = 640, 27, 132
    h = rowh * len(voci) + 12
    lim = max(v for _, v in voci) or 1
    fondo = w - 58
    out = [_SVG_OPEN.format(w=w, h=h)]
    for i, (dim, v) in enumerate(voci):
        y = 6 + i * rowh
        lung = v / lim * (fondo - ml)
        out.append(f'<text x="{ml-12}" y="{y+14}" text-anchor="end" class="sv-dim">{dim}</text>')
        out.append(f'<rect x="{ml}" y="{y+4}" width="{max(1.5,lung):.1f}" height="14" rx="2" '
                   f'fill="var(--orng)" opacity="{0.35 + 0.65*v/lim:.2f}"/>')
        out.append(f'<text x="{w-6}" y="{y+16}" text-anchor="end" class="sv-val">{v:.2f}</text>')
    out.append(f'<line x1="{ml}" y1="2" x2="{ml}" y2="{h-4}" stroke="rgba(233,240,236,.25)"/>')
    out.append("</svg>")
    return "".join(out)


def svg_curva_aii(cfg) -> str:
    """La curva dell'AII calcolata con i parametri attuali del motore."""
    import parte1_analisi as p1
    eta = [e / 2 for e in range(34, 74)]           # 17 → 36.5
    val = [p1.compute_age_index(e, cfg) or 0 for e in eta]
    w, h, ml, mr, mt, mb = 640, 200, 34, 12, 14, 28
    def px(e):
        return ml + (e - eta[0]) / (eta[-1] - eta[0]) * (w - ml - mr)
    def py(v):
        return mt + (1 - v) * (h - mt - mb)
    pts = " ".join(f"{px(e):.1f},{py(v):.1f}" for e, v in zip(eta, val))
    top_e = max(zip(eta, val), key=lambda t: t[1])
    out = [_SVG_OPEN.format(w=w, h=h)]
    for g in (0.25, 0.5, 0.75, 1.0):
        out.append(f'<line x1="{ml}" y1="{py(g):.1f}" x2="{w-mr}" y2="{py(g):.1f}" '
                   f'stroke="rgba(233,240,236,.07)"/>')
        out.append(f'<text x="{ml-8}" y="{py(g)+3:.1f}" text-anchor="end" class="sv-ax">{g:.2f}</text>')
    out.append(f'<polyline points="{pts}" fill="none" stroke="var(--orng)" stroke-width="1.8"/>')
    out.append(f'<line x1="{px(top_e[0]):.1f}" y1="{py(top_e[1]):.1f}" x2="{px(top_e[0]):.1f}" '
               f'y2="{h-mb}" stroke="rgba(255,176,32,.35)" stroke-dasharray="3 3"/>')
    out.append(f'<text x="{px(top_e[0]):.1f}" y="{py(top_e[1])-9:.1f}" text-anchor="middle" '
               f'class="sv-val">massimo a {top_e[0]:.1f}</text>')
    for e in (18, 22, 26, 30, 34):
        out.append(f'<text x="{px(e):.1f}" y="{h-9}" text-anchor="middle" class="sv-ax">{e}</text>')
    out.append("</svg>")
    return "".join(out)


# ══════════════════════════════════════════════════════════════════
# Capitoli
# ══════════════════════════════════════════════════════════════════
def _hero(cfg, pay: dict) -> str:
    cifre = [(str(len(cfg.tpi_weights)), "dimensioni nel TPI", "dimensions in the TPI"),
             ("5", "contesti per giocatore", "contexts per player"),
             (str(len(cfg.tpi_pro_weights) - 1), "modulatori nel TPI Pro",
              "modulators in the TPI Pro")]
    box = "".join(f'<div class="cifra"><b>{v}</b><span {bi(li, le)}>{li}</span></div>'
                  for v, li, le in cifre)
    lede_it = ("Ogni pezzo dell&rsquo;indice, con la formula che il codice esegue davvero e il "
               "problema a cui risponde. I pesi e i parametri di questa pagina sono <strong>letti "
               "dal motore</strong>, non ricopiati: se cambiano nel codice, cambiano qui.")
    lede_en = ("Every piece of the index, with the formula the code actually runs and the problem "
               "it answers. The weights and parameters on this page are <strong>read from the "
               "engine</strong>, not copied: if they change in the code, they change here.")
    return f"""<header class="hero riga">
  <div></div>
  <div>
  <div class="eyebrow">{config.SITO_NOME} &middot; Metodo</div>
  <h1 {bi("Come &egrave;<br><em>costruito</em>", "How it is<br><em>built</em>")}>Come &egrave;<br><em>costruito</em></h1>
  <p class="lede" {bi(lede_it, lede_en)}>{lede_it}</p>
  </div>
  <div class="cifre">{box}</div>
</header>"""


def _cap_dati(cfg, pay: dict, val: dict) -> str:
    meta = (val or {}).get("meta", {})
    n_rec = meta.get("n_record")
    ev = [
        evidenza("xG &middot; xA", "Il dato di partenza", "The starting data",
                 f"Una riga per giocatore e per partita" + (f", {n_rec} in tutto" if n_rec else "") +
                 ": minuti, xG e xA individuali, xG della squadra. Gli expected goals "
                 "vengono da Understat, l&rsquo;anagrafica da Transfermarkt, agganciata per "
                 "identificativo e non per nome.",
                 f"One row per player per match" + (f", {n_rec} in total" if n_rec else "") +
                 ": minutes, individual xG and xA, team xG. Expected goals come from Understat, "
                 "biographical data from Transfermarkt, joined on identifiers rather than names."),
        evidenza("SOS", "La forza dell&rsquo;avversario", "Opponent strength",
                 "Per ogni squadra, gli xG che concede in media, normalizzati a "
                 "<strong>1.00</strong> sulla media della lega. Ogni valore per-90 viene diviso "
                 "per la media SOS delle partite giocate: segnare a chi concede tanto vale meno.",
                 "For each team, the xG it concedes on average, normalised to <strong>1.00</strong> "
                 "on the league mean. Every per-90 figure is divided by the mean SOS of the "
                 "matches played: scoring against a leaky defence counts for less."),
        evidenza(f"&ge; {cfg.min_appearances_context}", "Chi entra", "Who is included",
                 f"Servono almeno {cfg.min_appearances_context} presenze in un contesto "
                 f"perch&eacute; quel contesto venga calcolato, e "
                 f"{cfg.min_appearances_form} per la forma. Sotto quella soglia il valore resta "
                 f"vuoto invece di essere inventato, e il punteggio si ricalcola sulle altre "
                 f"dimensioni.",
                 f"A context needs at least {cfg.min_appearances_context} appearances to be "
                 f"computed, and {cfg.min_appearances_form} for form. Below that the value stays "
                 f"empty instead of being invented, and the score is renormalised on the other "
                 f"dimensions."),
    ]
    p_it = ("Tutto parte da una riga per giocatore e per partita. Le medie per novanta minuti "
            "arrivano dopo, e prima di diventare un punteggio passano da una correzione che nel "
            "calcio conta pi&ugrave; di quanto sembri: contro chi hai giocato.")
    p_en = ("Everything starts from one row per player per match. Per-ninety averages come after, "
            "and before becoming a score they pass through a correction that matters more than it "
            "sounds in football: who you played against.")
    return f"""<section class="cap riga">
  <div class="cap-num">01</div>
  <div>
    {el("h2", "Da dove vengono i numeri", "Where the numbers come from")}
    <p class="prosa" {bi(p_it, p_en)}>{p_it}</p>
    <div class="ev-g">{"".join(ev)}</div>
  </div>
</section>"""


def _cap_dimensioni(cfg) -> str:
    pesi = cfg.tpi_weights
    ev = []
    for dim, (t_it, t_en, formula, d_it, d_en) in DIMENSIONI.items():
        peso = pesi.get(dim)
        if peso is None:
            continue
        f = formula.format(ewma=cfg.ewma_alpha, bk=int(cfg.boost_shrink_k))
        # Prima la frase, poi la formula: chi non e' un analista si ferma alla
        # riga uno, e la riga uno era un'espressione algebrica. La spiegazione
        # c'era gia' ed era buona, stava solo dopo.
        corpo_it = f'{d_it}<span class="form">{f}</span>'
        corpo_en = f'{d_en}<span class="form">{f}</span>'
        ev.append(evidenza(f"{peso:.2f}", t_it, t_en, corpo_it, corpo_en))
    p_it = ("Sette misure, ognuna con un peso. Nessuna &egrave; una statistica grezza presa "
            "cos&igrave; com&rsquo;&egrave;: tutte sono corrette per qualcosa, e il peso dice "
            "quanto mi fido di quello che misurano.")
    p_en = ("Seven measures, each with a weight. None is a raw statistic taken as it comes: every "
            "one is corrected for something, and the weight says how much I trust what it "
            "measures.")
    dida_it = ("I pesi, in scala. Sommano a 1: quando una dimensione manca per un giocatore, il "
               "punteggio si ricalcola sulle altre invece di trattarla come zero.")
    dida_en = ("The weights, to scale. They sum to 1: when a dimension is missing for a player, "
               "the score is renormalised on the others instead of treating it as zero.")
    return f"""<section class="cap riga">
  <div class="cap-num">02</div>
  <div>
    {el("h2", "Le sette dimensioni", "The seven dimensions")}
    <p class="prosa" {bi(p_it, p_en)}>{p_it}</p>
    <div class="ev-g">{"".join(ev)}</div>
    {svg_pesi(pesi)}
    <p class="didascalia" {bi(dida_it, dida_en)}>{dida_it}</p>
  </div>
</section>"""


def _cap_composito(cfg, val: dict) -> str:
    rw = " &middot; ".join(f"{k} {v:.2f}" for k, v in cfg.offensive_role_weight.items())
    # I ruoli specifici sono nuovi (19/08) e vanno spiegati qui, o in pagina
    # compaiono parole — quinto, mezzala, trequartista — che il lettore non sa
    # da dove escano ne' fino a che punto fidarsene.
    # Il vocabolario arriva dal motore: se domani si aggiunge una casella, la
    # guida se ne accorge da sola.
    import parte1_analisi as p1
    voci_fini = [v for k, v in p1.RUOLI_FINI.items() if k != "POR"]
    fini = " &middot; ".join(it.lower() for it, _ in voci_fini)
    fini_en = " &middot; ".join(en.lower() for _, en in voci_fini)
    ev = [
        # I dati di contorno vanno dichiarati per quello che sono, o uno pensa
        # che l'indice li usi.
        evidenza("0", "Quello che NON entra nel punteggio",
                 "What does NOT enter the score",
                 "Accanto a ogni giocatore ci sono <strong>valore di mercato</strong> e "
                 "<strong>scadenza del contratto</strong>, presi da Transfermarkt. Non entrano "
                 "in nessun calcolo: servono a decidere se un nome &egrave; anche "
                 "<em>raggiungibile</em>, che &egrave; una domanda diversa da quanto rende. Il "
                 "valore ha anzi il ruolo opposto: la validazione lo usa come "
                 "<strong>metro da battere</strong> &mdash; se il TPI non facesse meglio del "
                 "prezzo, non servirebbe. Ingaggio e prestiti non ci sono: non esiste una fonte "
                 "che li pubblichi in modo affidabile, e preferiamo dirlo che stimarli.",
                 "Next to every player there are a <strong>market value</strong> and a "
                 "<strong>contract expiry</strong>, taken from Transfermarkt. They enter no "
                 "calculation: they are there to tell whether a name is also <em>reachable</em>, "
                 "which is a different question from how much he delivers. The value plays the "
                 "opposite role: the validation uses it as a <strong>baseline to beat</strong> "
                 "&mdash; if the TPI did not do better than the price tag, it would be pointless. "
                 "Wages and loan status are absent: no source publishes them reliably, and we "
                 "would rather say so than estimate them."),
        evidenza(str(len(voci_fini)), "Ruoli, quelli veri",
                 "Roles, the real ones",
                 f"Accanto ad ATT/CEN/DIF ogni giocatore porta il ruolo che fa davvero "
                 f"({fini}), ricavato dai <strong>minuti per posizione</strong> di Understat: si "
                 f"somma quanto tempo ha passato in ogni zona e vince la piu' battuta. Accanto al "
                 f"nome c'&egrave; la quota, perch&eacute; un ruolo al 93% e uno al 34% non sono "
                 f"la stessa affermazione. Chi gioca da centravanti ma passa almeno un quarto del "
                 f"tempo sulla trequarti diventa <em>seconda punta</em>. "
                 f"<strong>Non entrano nel punteggio</strong>: gli z-score restano dentro i tre "
                 f"gruppi grossi. Cambiare i gruppi di confronto vorrebbe dire un altro indice e "
                 f"un'altra validazione, non un'etichetta pi&ugrave; fine.",
                 f"Next to FWD/MID/DEF every player carries the role he actually plays "
                 f"({fini_en}), derived from Understat&rsquo;s <strong>minutes per position</strong>: "
                 f"time spent in each zone is summed and the most-played one wins. The share is "
                 f"shown next to it, because a role held 93% of the time and one held 34% are not "
                 f"the same claim. A player used as a centre-forward who spends at least a quarter "
                 f"of his time behind the striker becomes a <em>second striker</em>. "
                 f"<strong>They do not enter the score</strong>: z-scores stay inside the three "
                 f"broad groups. Changing the comparison groups would mean a different index and a "
                 f"different validation, not a finer label."),
        evidenza("&plusmn;&sigma;", "Confrontato con chi gioca dove gioca lui",
                 "Compared with players in his own role",
                 "Gli z-score si calcolano <strong>dentro il ruolo</strong>: un difensore &egrave; "
                 "confrontato con i difensori, non con i centravanti. &Egrave; la scelta che fa "
                 "emergere il terzino che spinge, e va detta perch&eacute; cambia cosa significa "
                 "il numero: <strong>non &egrave; produzione offensiva assoluta, &egrave; quanto "
                 "uno spicca per il suo ruolo</strong>. Poi un coefficiente per ruolo "
                 f"({rw}) li rimette in una colonna sola. "
                 "Il conto rifatto sulla lega intera d&agrave; una top 10 di soli attaccanti: "
                 "spariscono Dimarco, Cambiaso, Wesley e McTominay, cio&egrave; i nomi per cui "
                 "questa classifica esiste. Chi vuole rifare la misura: "
                 "<span class=\"form\">parte1_analisi.py --z-lega</span>",
                 "Z-scores are computed <strong>within the role</strong>: a defender is compared "
                 "with defenders, not with strikers. It is the choice that surfaces the attacking "
                 "full-back, and it must be stated because it changes what the number means: "
                 "<strong>it is not absolute attacking output, it is how much a player stands out "
                 "for his role</strong>. A per-role coefficient "
                 f"({rw}) then puts them back in a single column. "
                 "Recomputed league-wide, the top 10 is all strikers: Dimarco, Cambiaso, Wesley "
                 "and McTominay disappear &mdash; the very names this ranking exists for. To redo "
                 "the measurement: <span class=\"form\">parte1_analisi.py --z-lega</span>"),
        evidenza("z", "Stessa unit&agrave; di misura", "One unit of measure",
                 "Ogni dimensione diventa uno z-score <strong>winsorizzato al 5%</strong>: gli "
                 "estremi vengono schiacciati sul quinto e sul novantacinquesimo percentile prima "
                 "di standardizzare. Una partita fuori scala non deve spostare la graduatoria di "
                 "tutti.",
                 "Each dimension becomes a <strong>5% winsorised</strong> z-score: the extremes "
                 "are clipped at the fifth and ninety-fifth percentile before standardising. One "
                 "off-the-charts match must not move everyone else&rsquo;s ranking."),
        evidenza("Bayes", "Chi ha giocato poco", "Few minutes played",
                 f'<span class="form">shrunk = media_ruolo + ({cfg.confidence_floor:.2f} + '
                 f'{1 - cfg.confidence_floor:.2f} &times; confidence) &times; (media_pesata '
                 f'&minus; media_ruolo)</span>'
                 f"La media pesata viene tirata verso la media del suo ruolo, tanto pi&ugrave; "
                 f"quanto meno ha giocato. Chi ha fatto due partite buone non scavalca chi regge "
                 f"da trenta.",
                 f'<span class="form">shrunk = role_mean + ({cfg.confidence_floor:.2f} + '
                 f'{1 - cfg.confidence_floor:.2f} &times; confidence) &times; (weighted_mean '
                 f'&minus; role_mean)</span>'
                 f"The weighted mean is pulled towards the mean of the player&rsquo;s role, the "
                 f"more so the less he has played. Two good matches do not overtake thirty solid "
                 f"ones."),
        evidenza("&times; ruolo", "Il peso del ruolo", "The role weight",
                 f'<span class="form">{rw}</span>'
                 "Il criterio &egrave; l&rsquo;impatto <em>offensivo</em>: un difensore che "
                 "attacca vale, ma non pu&ograve; competere con un attaccante sulla stessa scala. "
                 "&Egrave; una scelta dichiarata, non una misura &mdash; ed &egrave; il motivo per "
                 "cui questo indice non serve a giudicare un difensore che difende.",
                 f'<span class="form">{rw}</span>'
                 "The criterion is <em>attacking</em> impact: an attacking defender counts, but "
                 "cannot compete with a forward on the same scale. It is a stated choice, not a "
                 "measurement &mdash; and it is why this index is no use for judging a defender "
                 "who defends."),
        evidenza("&times; disp.", "Chi c&rsquo;&egrave; stato", "Who was available",
                 "Una penalit&agrave; finale tiene conto delle partite saltate. Un punteggio alto "
                 "fatto in un terzo di stagione non pesa quanto lo stesso punteggio fatto "
                 "sempre.",
                 "A final penalty accounts for matches missed. A high score built over a third of "
                 "the season does not weigh the same as the same score built throughout."),
    ]
    p_it = ("Il TPI <strong>non &egrave; la media pesata delle sette dimensioni</strong>, anche se "
            "&egrave; l&rsquo;errore che farebbe chiunque leggendo solo la tabella dei pesi. Dopo "
            "quella media ci sono altri tre passaggi, e sono quelli che decidono la classifica.")
    p_en = ("The TPI is <strong>not the weighted mean of the seven dimensions</strong>, even "
            "though that is the mistake anyone would make reading only the weight table. After "
            "that mean come three more steps, and they are the ones that decide the ranking.")
    fin_it = ("TPI = peso_ruolo &times; shrunk &times; penalit&agrave;_disponibilit&agrave;")
    return f"""<section class="cap riga">
  <div class="cap-num">03</div>
  <div>
    {el("h2", "Come diventano un punteggio solo", "How they become a single score")}
    <p class="prosa" {bi(p_it, p_en)}>{p_it}</p>
    <div class="ev-g">{"".join(ev)}</div>
    <div class="form form-big">{fin_it}</div>
  </div>
</section>"""


def _cap_contesti(pay: dict) -> str:
    """I cinque contesti, e perche' due di essi possono sembrare lo stesso.

    Le due liste venivano stampate una sotto l'altra, e quando coincidono - il
    che succede ogni volta che le prime sei in classifica sono anche le sei
    difese piu' solide, cioe' spesso - il lettore conclude che uno dei cinque
    contesti sia finto. Non lo e': la differenza non sta nella lista globale ma
    nel fatto che l'insieme si calcola per giocatore, togliendo la sua squadra.
    """
    top6_l = pay.get("top6_names", [])[:6]
    forti_l = pay.get("forti_names", [])[:6]
    top6 = ", ".join(top6_l)
    forti = ", ".join(forti_l)
    if top6_l and top6_l == forti_l:
        quali_it = (f"Quest&rsquo;anno sono le stesse sei squadre &mdash; {top6} &mdash; "
                    f"ma i due contesti <strong>non coincidono</strong>")
        quali_en = (f"This season they are the same six teams &mdash; {top6} &mdash; "
                    f"but the two contexts <strong>do not coincide</strong>")
    else:
        quali_it = f"Le prime sei sono {top6}, le difese pi&ugrave; solide {forti}"
        quali_en = f"The top six are {top6}, the tightest defences {forti}"
    ev = [
        evidenza("5", "Un punteggio per contesto", "One score per context",
                 f"Totale, casa, trasferta, contro le <strong>prime sei in classifica</strong> e "
                 f"contro le <strong>sei difese che concedono meno xG</strong>. {quali_it}: "
                 f"entrambi escludono la squadra del giocatore &mdash; contro s&eacute; stessi non "
                 f"si gioca &mdash; ma il primo si limita a toglierla, mentre il secondo fa "
                 f"scorrere dentro la settima difesa. Cos&igrave; chi gioca in una delle prime sei "
                 f"ha cinque avversari nel primo contesto e sei nel secondo, e i due punteggi "
                 f"vengono diversi. Ogni contesto rif&agrave; tutto il calcolo sulle sole partite "
                 f"che gli appartengono.",
                 f"Overall, home, away, against the <strong>top six in the table</strong> and "
                 f"against the <strong>six defences conceding the fewest xG</strong>. {quali_en}: "
                 f"both exclude the player&rsquo;s own team &mdash; you do not play yourself "
                 f"&mdash; but the first simply drops it, while the second slides the seventh "
                 f"defence in. So a player at one of the top six faces five opponents in the "
                 f"first context and six in the second, and the two scores come out different. "
                 f"Each context redoes the whole computation on its own matches only."),
        evidenza("&Delta;", "La differenza &egrave; il dato", "The gap is the data",
                 "Un giocatore che ha un punteggio alto in casa e basso in trasferta non &egrave; "
                 "lo stesso giocatore di uno che li ha uguali, anche a parit&agrave; di totale. "
                 "La stabilit&agrave; fra i cinque contesti diventa uno dei modulatori del TPI "
                 "Pro.",
                 "A player with a high home score and a low away score is not the same player as "
                 "one with equal scores, even at the same overall value. Stability across the "
                 "five contexts becomes one of the TPI Pro modulators."),
    ]
    return f"""<section class="cap riga">
  <div class="cap-num">04</div>
  <div>
    {el("h2", "I cinque contesti", "The five contexts")}
    <div class="ev-g">{"".join(ev)}</div>
  </div>
</section>"""


def _cap_pro(cfg) -> str:
    import parte1_analisi as p1
    a = cfg
    ev = [
        evidenza("AII", "Et&agrave;", "Age",
                 f'<span class="form">0.55 &times; gaussiana(picco {a.age_peak:.0f}, '
                 f'&sigma; {a.age_sigma}) + 0.25 &times; crescita + 0.20 &times; freschezza</span>'
                 f"Premia chi <strong>sta entrando</strong> nel prime, non chi ci &egrave; "
                 f"gi&agrave;. Il massimo del composito non cade a {a.age_peak:.0f} ma poco prima: "
                 f"a quell&rsquo;et&agrave; il bonus di crescita si &egrave; gi&agrave; azzerato.",
                 f'<span class="form">0.55 &times; gaussian(peak {a.age_peak:.0f}, '
                 f'&sigma; {a.age_sigma}) + 0.25 &times; growth + 0.20 &times; freshness</span>'
                 f"It rewards who is <strong>entering</strong> their prime, not who is already "
                 f"there. The composite peaks slightly before {a.age_peak:.0f}: by then the growth "
                 f"bonus has already gone to zero."),
        evidenza("PRI", "Tenuta fisica", "Physical durability",
                 f'<span class="form">0.50 &times; disponibilit&agrave; + 0.30 &times; '
                 f'(1 &minus; infortuni &times; {a.pri_inj_penalty:.2f}) + 0.20 &times; '
                 f'(1 &minus; giorni_fuori / {a.pri_severity_cap:.0f})</span>'
                 f"Partite giocate su partite disponibili, numero di infortuni e giorni di "
                 f"assenza. Serve almeno {a.pri_min_partite} partite disponibili, altrimenti "
                 f"resta vuoto.",
                 f'<span class="form">0.50 &times; availability + 0.30 &times; '
                 f'(1 &minus; injuries &times; {a.pri_inj_penalty:.2f}) + 0.20 &times; '
                 f'(1 &minus; days_out / {a.pri_severity_cap:.0f})</span>'
                 f"Matches played over matches available, number of injuries and days out. It "
                 f"needs at least {a.pri_min_partite} available matches, otherwise it stays "
                 f"empty."),
        evidenza("CTX", "Stabilit&agrave; fra contesti", "Cross-context stability",
                 "Quanto i cinque punteggi si somigliano fra loro. Alto significa che rende "
                 "ovunque; basso, che dipende da dove gioca e contro chi.",
                 "How close the five scores are to each other. High means he delivers everywhere; "
                 "low, that it depends on where he plays and against whom."),
        evidenza("TREND", "Direzione della forma", "Form direction",
                 "L&rsquo;EWMA confrontata con la media di stagione: dice se un giocatore sta "
                 "salendo o scendendo rispetto a s&eacute; stesso.",
                 "The EWMA compared with the season average: it says whether a player is rising or "
                 "falling relative to himself."),
        evidenza("EMI", "Sopra l&rsquo;attesa", "Above expectation",
                 "Chi produce pi&ugrave; di quanto ci si aspetterebbe alla sua et&agrave;. "
                 "&Egrave; il modulatore pi&ugrave; vicino all&rsquo;idea da cui l&rsquo;indice "
                 "nasce, ed &egrave; anche quello che serviranno anni di dati per giudicare.",
                 "Who produces more than you would expect at his age. It is the modulator closest "
                 "to the idea the index was born from, and also the one that will take years of "
                 "data to judge."),
    ]
    fasce = []
    etichette = {"prospetto": ("Prospetto", "Prospect"), "prime": ("Prime", "Prime"),
                 "veterano": ("Veterano", "Veteran")}
    limiti = {"prospetto": (f"&lt; {a.prospetto_max_age:.0f} anni", f"under {a.prospetto_max_age:.0f}"),
              "prime": (f"{a.prospetto_max_age:.0f}&ndash;{a.veterano_min_age - 1:.0f} anni",
                        f"{a.prospetto_max_age:.0f}&ndash;{a.veterano_min_age - 1:.0f}"),
              "veterano": (f"&ge; {a.veterano_min_age:.0f} anni", f"{a.veterano_min_age:.0f} and over")}
    # Una riga diversa per fascia: dire tre volte la stessa cosa sotto tre
    # numeri diversi fa sembrare che nessuna delle tre valesse la pena.
    commento = {
        "prospetto": ("Il TPI pesa meno e l&rsquo;et&agrave; di pi&ugrave;: su un ragazzo conta "
                      "dove sta andando, e l&rsquo;early momentum &egrave; al suo massimo.",
                      "The TPI weighs less and age more: for a young player what matters is where "
                      "he is heading, and early momentum is at its highest."),
        "prime": ("L&rsquo;equilibrio: il rendimento in campo domina, i modulatori restano "
                  "correzioni.",
                  "The balance: on-pitch output dominates, the modulators stay corrections."),
        "veterano": ("L&rsquo;et&agrave; quasi sparisce e la tenuta fisica diventa il modulatore "
                     "principale: a trent&rsquo;anni la domanda non &egrave; se cresce, &egrave; "
                     "se regge.",
                     "Age almost disappears and physical durability becomes the main modulator: at "
                     "thirty the question is not whether he grows, it is whether he holds up."),
    }
    for fascia, pesi in a.tpi_pro_weights_age.items():
        t_it, t_en = etichette.get(fascia, (fascia, fascia))
        l_it, l_en = limiti.get(fascia, ("", ""))
        c_it, c_en = commento.get(fascia, ("", ""))
        det = " &middot; ".join(
            f"{k.replace('z_', '')} {v:.2f}" for k, v in pesi.items() if k != "tpi")
        fasce.append(evidenza(
            f'{pesi["tpi"]:.2f}', f"{t_it} &mdash; {l_it}", f"{t_en} &mdash; {l_en}",
            f'{c_it}<span class="form">TPI {pesi["tpi"]:.2f} &middot; {det}</span>',
            f'{c_en}<span class="form">TPI {pesi["tpi"]:.2f} &middot; {det}</span>'))
    p_it = ("Il TPI misura il rendimento in campo. Il <strong>TPI Pro</strong> aggiunge cinque "
            "misure che guardano al giocatore invece che alla prestazione, e serve a distinguere "
            "chi &egrave; forte adesso da chi lo sar&agrave;. Fuori campione non predice meglio "
            "del TPI classico: &egrave; uno strumento per leggere un profilo, non per prevedere.")
    p_en = ("The TPI measures on-pitch performance. The <strong>TPI Pro</strong> adds five "
            "measures that look at the player rather than the performance, to tell apart who is "
            "good now from who will be. Out-of-sample it does not predict better than the classic "
            "TPI: it is a tool for reading a profile, not for forecasting.")
    dida_it = ("L&rsquo;indice d&rsquo;et&agrave; calcolato con i parametri attuali del motore, "
               "da 17 a 36 anni.")
    dida_en = "The age index computed with the engine&rsquo;s current parameters, from 17 to 36."
    return f"""<section class="cap riga">
  <div class="cap-num">05</div>
  <div>
    {el("h2", "I modulatori scout e il TPI Pro", "The scout modulators and the TPI Pro")}
    <p class="prosa" {bi(p_it, p_en)}>{p_it}</p>
    <div class="ev-g">{"".join(ev)}</div>
    {svg_curva_aii(cfg)}
    <p class="didascalia" {bi(dida_it, dida_en)}>{dida_it}</p>
    <h3 {bi("I pesi cambiano per fascia d&rsquo;et&agrave;", "Weights change by age band")}>I pesi cambiano per fascia d&rsquo;et&agrave;</h3>
    {el("p", "Lo stesso giocatore, valutato a vent&rsquo;anni o a trentadue, non va pesato allo stesso modo. Le tre fasce hanno pesi diversi, e i tagli sono quelli del motore.", "The same player, judged at twenty or at thirty-two, should not be weighted the same way. The three bands carry different weights, and the cut-offs are the engine&rsquo;s.", "prosa")}
    {"".join(fasce)}
  </div>
</section>"""


def _cap_lettura(val: dict) -> str:
    m = (val or {}).get("m") or {}
    ev = []
    if m.get("slope"):
        gonf = 1 / float(m["slope"])
        ev.append(evidenza(
            _f(m["slope"], 3), "Una graduatoria, non una scala", "A ranking, not a scale",
            f"La regressione fra decili di TPI e rendimento reale ha pendenza "
            f"{_f(m['slope'], 3)} invece di 1: l&rsquo;ordine &egrave; giusto, le distanze sono "
            f"gonfiate circa {_f(gonf, 1)} volte. Un giocatore con TPI doppio di un altro "
            f"<strong>non rende il doppio</strong>. Il valore non &egrave; stato riscalato di "
            f"proposito: ricalibrarlo vorrebbe dire tararlo su un criterio predittivo che "
            f"l&rsquo;indice non promette.",
            f"Regressing realized output on TPI deciles gives a slope of {_f(m['slope'], 3)} "
            f"instead of 1: the order is right, the distances are inflated by roughly "
            f"{_f(gonf, 1)}&times;. A player with twice another&rsquo;s TPI <strong>does not "
            f"produce twice as much</strong>. The value has deliberately not been rescaled: "
            f"recalibrating would mean tuning it to a predictive criterion the index does not "
            f"promise."))
    ev.append(evidenza(
        "0", "Lo zero &egrave; la media", "Zero is the average",
        "I punteggi sono z-score: zero &egrave; il giocatore medio fra i qualificati, non un "
        "giocatore scarso. Un valore negativo dice &laquo;sotto la media di questo campione&raquo;, "
        "e il campione &egrave; gi&agrave; fatto di chi gioca abbastanza da entrarci.",
        "Scores are z-scores: zero is the average qualified player, not a bad one. A negative "
        "value says &laquo;below the average of this sample&raquo;, and the sample is already made "
        "of players who play enough to be in it."))
    ev.append(evidenza(
        "&ne;", "Non &egrave; una previsione", "It is not a forecast",
        "L&rsquo;indice ordina dentro la stagione. Sul rendimento futuro non batte l&rsquo;output "
        "grezzo per-90, ed &egrave; scritto per esteso nella pagina delle verifiche, con "
        "l&rsquo;intervallo di confidenza accanto.",
        "The index ranks within the season. On future output it does not beat raw per-90 output, "
        "and that is written in full on the validation page, with the confidence interval next to "
        "it."))
    return f"""<section class="cap riga">
  <div class="cap-num">06</div>
  <div>
    {el("h2", "Come si legge un punteggio", "How to read a score")}
    <div class="ev-g">{"".join(ev)}</div>
    <a class="oltre" href="validazione.html" {bi("Quanto regge, verifica per verifica", "How well it holds, check by check")}>Quanto regge, verifica per verifica</a>
  </div>
</section>"""


CSS_EXTRA = """
/* Le formule: monospazio, staccate dal testo che le spiega. */
/* Le formule vanno a capo invece di scorrere: con nowrap ogni riquadro si
   portava dietro una barra di scorrimento e le formule lunghe si vedevano a
   meta'. Il monospazio resta, l'interlinea larga tiene leggibile il ritorno. */
.form{display:block;font-family:var(--mono);font-size:13px;line-height:1.75;
  color:var(--lp);background:rgba(233,240,236,.04);
  border-left:2px solid rgba(255,176,32,.5);padding:9px 12px;
  border-radius:0 4px 4px 0;overflow-wrap:anywhere;
  /* La formula chiude il blocco invece di aprirlo: l'aria le serve sopra. */
  margin:11px 0 0}
.form-big{margin-top:26px;font-size:14.5px}
.oltre{display:inline-block;margin-top:22px;font-size:13.5px;color:var(--ls);
  text-decoration:none;border-bottom:1px solid var(--lq);padding-bottom:2px}
.oltre:hover{color:var(--orng);border-color:var(--orng)}
.oltre::after{content:" \\2192"}
@media(max-width:900px){.form{white-space:normal}}
"""


def render(cfg, pay: dict, val: dict | None) -> str:
    corpo = "\n".join((_hero(cfg, pay), _cap_dati(cfg, pay, val or {}),
                       _cap_dimensioni(cfg), _cap_composito(cfg, val or {}),
                       _cap_contesti(pay), _cap_pro(cfg), _cap_lettura(val or {})))
    html = guscio(
        f"Metodo &mdash; {config.SITO_NOME}",
        "Come &egrave; costruito il TPI: le sette dimensioni, i pesi, lo shrinkage e i modulatori "
        "scout, con le formule che il codice esegue davvero.",
        "How the TPI is built: the seven dimensions, the weights, the shrinkage and the scout "
        "modulators, with the formulas the code actually runs.",
        "guida_completa.html", corpo,
        [("validazione.html", "Quanto regge", "How well it holds"),
         ("dashboard_%s.html" % config.LEGA_SLUG, "La classifica completa", "The full ranking")],
        stile_extra=CSS_EXTRA)
    return html


def main() -> None:
    import parte1_analisi as p1
    cfg = p1.Config()
    pay_path = OUTPUT_DIR / "payload.json"
    if not pay_path.is_file():
        pay_path = DEMO_DIR / "payload.json"
    pay = json.loads(pay_path.read_text(encoding="utf-8")) if pay_path.is_file() else {}
    val_path = OUTPUT_DIR / "validazione_dati.json"
    val = json.loads(val_path.read_text(encoding="utf-8")) if val_path.is_file() else None
    html = render(cfg, pay, val)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    # un foglio solo, accanto alle pagine, invece di una copia per pagina
    assicura_css(OUTPUT_DIR, DEMO_DIR)
    out = OUTPUT_DIR / "guida_completa.html"
    out.write_text(html, encoding="utf-8")
    log.info(f"OK → {out}  ({len(html)//1024} KB)")
    if DEMO_DIR.is_dir():
        (DEMO_DIR / "guida_completa.html").write_text(html, encoding="utf-8")
        log.info(f"OK → {DEMO_DIR / 'guida_completa.html'}  (copia per repo demo)")


if __name__ == "__main__":
    main()
