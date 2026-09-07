"""Genera `caso-mercato.html`: l'indice usato per rispondere a una domanda di mercato.

    python caso_mercato.py                      # la stagione dei dati piu' completa
    python caso_mercato.py --stagione 2025-26   # una stagione precisa
    python caso_mercato.py --elenca             # dice quali stagioni potrebbe usare

Legge il payload della lista — tutti i qualificati, non i primi 100 — e scrive la
pagina. I numeri non sono battuti a mano da nessuna parte: se il payload cambia,
si rilancia e la pagina si riallinea. Se un giorno un numero qui dentro smettesse
di coincidere con la classifica, sarebbe un bug, non una svista redazionale.

La domanda: un club ha un budget e tre posti in rosa. L'indice ordina i giocatori,
ma un ordinamento non e' una decisione — questa pagina fa il passo che manca.

**Perche' e' finito qui.** Stava dentro `serie-a-index`, cioe' dentro il repo del
sito pubblicato, che e' l'unico posto dove un generatore non deve stare: quel
repo contiene il sito, non il motore che lo scrive. Per questo era anche l'unica
pagina fuori dalla sequenza di `pubblica.py` — e al cambio di stagione restava
indietro da sola, con la stagione battuta a mano nella barra.

**Due stagioni, non una.** La barra dice la stagione del *sito*, presa da
`config`; il caso e' costruito sui dati di *un'altra*, e la pagina lo dichiara.
Servono minuti veri — la soglia e' 1500', circa diciassette partite intere —
quindi a settembre il caso non si puo' rifare sull'annata in corso: si tiene
quello dell'ultima stagione conclusa finche' la nuova non ha abbastanza partite.
Tacerlo vorrebbe dire pubblicare tre nomi del 2025-26 sotto un'etichetta 2026-27.
"""

import argparse
import itertools
import json
import sys
from collections import Counter
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))
import config  # noqa: E402

USCITA = config.cartella_uscita(BASE_DIR)
REPO = config.cartella_pubblicazione(BASE_DIR.parent)

# Il payload si cerca prima nel repo pubblicato e poi nella cartella di lavoro:
# quella del motore puo' essere gia' andata avanti alla stagione nuova, e il caso
# si costruisce su quella conclusa. Il primo posto che ce l'ha vince.
DOVE = [d for d in (REPO, USCITA) if d.is_dir()]
PAYLOAD = None          # deciso da scegli_stagione()
PAYLOAD_PREC = None     # per verificare se i valori si muovono
OUT = USCITA / "caso-mercato.html"

MIN_QUALIFICATI = 40    # sotto, non e' un mercato: e' un campione


def _lista(cartella: Path, stagione: str | None):
    """Il file della lista per una stagione, o quello corrente se coincide."""
    if stagione:
        p = cartella / ("payload_lista_%s.json" % stagione)
        if p.is_file():
            return p
    corrente = cartella / "payload_lista.json"
    if corrente.is_file():
        try:
            dentro = json.loads(corrente.read_text(encoding="utf-8")).get("stagione")
        except (OSError, ValueError):
            return None
        if stagione is None or str(dentro) == stagione:
            return corrente
    return None


def stagioni_disponibili() -> dict[str, Path]:
    """Quali stagioni si potrebbero raccontare, e da quale file."""
    fuori: dict[str, Path] = {}
    for cartella in DOVE:
        for p in sorted(cartella.glob("payload_lista*.json")):
            if "tutte-le-stagioni" in p.name:
                continue
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            st = d.get("stagione")
            if not st or st == "tutte":
                continue
            n = sum(1 for x in d.get("players") or [] if (x.get("minuti") or 0) >= MIN_MINUTI)
            fuori.setdefault(str(st), p)
            fuori["%s|n" % st] = n          # comodo per --elenca
    return fuori


def scegli_stagione(voluta: str | None) -> tuple[str, Path, Path | None]:
    """La stagione del caso, il suo file e quello dell'anno prima.

    Senza `--stagione` si prende **la piu' recente che ha abbastanza minuti**,
    non semplicemente la piu' recente: a settembre l'annata in corso ha tre
    giornate, nessuno arriva a 1500 minuti e la pagina uscirebbe vuota. Meglio
    il caso dell'anno scorso, dichiarato, che tre nomi scelti su due partite.
    """
    trovate = stagioni_disponibili()
    stagioni = sorted((k for k in trovate if not k.endswith("|n")), reverse=True)
    if not stagioni:
        raise SystemExit(
            "Nessun payload_lista trovato in %s: genera prima la lista "
            "(parte1_analisi.py)." % " o ".join(str(d) for d in DOVE))

    if voluta:
        if voluta not in trovate:
            raise SystemExit("Stagione %s non disponibile. Ci sono: %s"
                             % (voluta, ", ".join(stagioni)))
        scelta = voluta
    else:
        buone = [s for s in stagioni if trovate.get("%s|n" % s, 0) >= MIN_QUALIFICATI]
        if not buone:
            raise SystemExit(
                "Nessuna stagione ha almeno %d giocatori sopra i %d minuti: il caso "
                "di mercato non si puo' costruire adesso. Stagioni viste: %s"
                % (MIN_QUALIFICATI, MIN_MINUTI,
                   ", ".join("%s (%d)" % (s, trovate.get("%s|n" % s, 0)) for s in stagioni)))
        scelta = buone[0]

    n = trovate.get("%s|n" % scelta, 0)
    if n < MIN_QUALIFICATI:
        raise SystemExit(
            "La %s ha solo %d giocatori sopra i %d minuti: troppo pochi per una "
            "terna. E' quello che succede a inizio stagione — rilancia senza "
            "--stagione per usare l'ultima annata conclusa." % (scelta, n, MIN_MINUTI))

    anno = int(scelta[:4])
    prec_nome = "%d-%02d" % (anno - 1, anno % 100)
    prec = None
    for cartella in DOVE:
        p = _lista(cartella, prec_nome)
        if p is not None:
            prec = p
            break
    return scelta, trovate[scelta], prec

BUDGET = 15.0          # milioni, per tre giocatori
BUDGET_STRETTO = 12.0  # il budget su cui si finisce per discutere davvero
ETA_GIOIELLO = 23.0    # chi si vende a peso d'oro: il piu' caro sotto questa eta'
MIN_MINUTI = 1500      # ~17 partite intere: sotto, il campione non regge
MIN_CONFIDENCE = 0.70  # confidenza dell'indice sul singolo giocatore
MIN_AFFIDABILITA = 0.75


def carica():
    d = json.loads(PAYLOAD.read_text(encoding="utf-8"))
    out = []
    for x in d["players"]:
        v = x.get("valore_mercato")
        if not v:
            continue
        ph, ct = x["physical"], (x.get("contratto") or {})
        out.append({
            "nome": x["nome"], "sq": x["squadra"], "ruolo": x["ruolo"],
            "val": v / 1e6, "tpi": x["tpi"]["totale"], "rank": x["rank"]["TPI"],
            "eta": ph["eta"], "aff": ph["affidabilita"], "inf": ph["n_infortuni"],
            "out_gg": ph.get("giorni_out"), "min": x["minuti"], "conf": x["confidence"],
            "scad": ct.get("scadenza"), "prestito": ct.get("in_prestito"),
        })
    return d, out


def valori_fermi():
    """Quanti giocatori presenti in entrambe le stagioni hanno lo STESSO valore di
    mercato. Se sono tutti, quel campo non e' una rilevazione per stagione ma una
    fotografia sola riusata — e chi legge la pagina deve saperlo.

    Restituisce (uguali, confrontabili) oppure None se il file precedente manca.
    """
    if PAYLOAD_PREC is None or not PAYLOAD_PREC.exists():
        return None
    prec = {x["id"]: x for x in json.loads(PAYLOAD_PREC.read_text(encoding="utf-8"))["players"]}
    ora = {x["id"]: x for x in json.loads(PAYLOAD.read_text(encoding="utf-8"))["players"]}
    coppie = [(prec[i].get("valore_mercato"), ora[i].get("valore_mercato"))
              for i in set(prec) & set(ora)]
    coppie = [(a, b) for a, b in coppie if a and b]
    return sum(1 for a, b in coppie if a == b), len(coppie)


def ammissibile(c):
    """Perche' un giocatore entra nella scelta. I motivi sono tre e sono in pagina."""
    return (c["min"] >= MIN_MINUTI and c["conf"] >= MIN_CONFIDENCE
            and c["aff"] >= MIN_AFFIDABILITA and not c["prestito"])


def motivi_esclusione(c, en=False):
    m = []
    if c["min"] < MIN_MINUTI:
        m.append(f"{c['min']:.0f} minutes played" if en else f"{c['min']:.0f} minuti giocati")
    if c["conf"] < MIN_CONFIDENCE:
        m.append(f"index confidence {c['conf']:.2f}" if en
                 else f"confidenza dell'indice {c['conf']:.2f}")
    if c["aff"] < MIN_AFFIDABILITA:
        if en:
            gg = f", {c['out_gg']:.0f} days out" if c.get("out_gg") else ""
            m.append(f"physical availability {c['aff']:.2f} ({c['inf']} injuries{gg})")
        else:
            gg = f", {c['out_gg']:.0f} giorni fuori" if c.get("out_gg") else ""
            m.append(f"affidabilit&agrave; fisica {c['aff']:.2f} ({c['inf']} infortuni{gg})")
    return m


def miglior_terna(pool, budget):
    """La terna che massimizza la somma dei TPI dentro il budget, con almeno due
    reparti diversi: tre giocatori dello stesso ruolo non sono un mercato."""
    best = None
    for t in itertools.combinations(pool, 3):
        if sum(c["val"] for c in t) > budget:
            continue
        if len({c["ruolo"] for c in t}) < 2:
            continue
        s = sum(c["tpi"] for c in t)
        if best is None or s > best[0]:
            best = (s, t)
    return best


def terne_valide(pool):
    for t in itertools.combinations(pool, 3):
        if len({c["ruolo"] for c in t}) >= 2:
            yield t


def under_piu_economica(pool, eta_max=25.0):
    """La terna under `eta_max` che costa MENO: dice a che prezzo l'opzione
    'comprare futuro' comincia a esistere. Non la migliore possibile — quella
    costerebbe cifre che nessun club di meta' classifica ha, e non sarebbe un
    confronto onesto con un budget da quindici milioni."""
    giovani = [c for c in pool if c["eta"] <= eta_max]
    t = min(terne_valide(giovani), key=lambda t: sum(c["val"] for c in t))
    return sum(c["tpi"] for c in t), t


def under_che_eguaglia(pool, bersaglio, eta_max=25.0):
    """La terna under `eta_max` piu' economica che raggiunge `bersaglio` di TPI.
    None se nessuna ci arriva a nessun prezzo (dentro questo campione)."""
    giovani = [c for c in pool if c["eta"] <= eta_max]
    cand = [t for t in terne_valide(giovani) if sum(c["tpi"] for c in t) >= bersaglio]
    return min(cand, key=lambda t: sum(c["val"] for c in t)) if cand else None


def _at(t: str) -> str:
    """Il testo entra in un attributo che i18n.js rimette come innerHTML: vanno
    protette solo le virgolette, le entita' HTML devono restare intatte."""
    return t.replace('"', "&quot;")


def bi(it: str, en: str) -> str:
    return f'data-it="{_at(it)}" data-en="{_at(en)}"'


def el(tag: str, it: str, en: str, attr: str = "") -> str:
    """Elemento bilingue. Il testo visibile e' l'italiano, cosi' la pagina resta
    leggibile anche a JavaScript spento."""
    a = f" {attr}" if attr else ""
    return f"<{tag}{a} {bi(it, en)}>{it}</{tag}>"


def euro(m):
    return f"{m:.1f}".rstrip("0").rstrip(".") + "&nbsp;M&euro;"


def tabella(terna):
    intest = [("Giocatore", "Player", ""), ("Squadra", "Club", ""), ("R", "Pos", ""),
              ("Valore", "Value", "n"), ("TPI", "TPI", "n"), ("Pos.", "Rank", "n"),
              ("Et&agrave;", "Age", "n"), ("Affid.", "Avail.", "n")]
    th = "".join(el("th", it, en, f'class="{c}"' if c else "") for it, en, c in intest)
    r = [f'<table class="cm-t"><thead><tr>{th}</tr></thead><tbody>']
    for c in sorted(terna, key=lambda c: -c["tpi"]):
        r.append(
            f'<tr><td>{c["nome"]}</td><td>{c["sq"]}</td><td>{c["ruolo"]}</td>'
            f'<td class="n">{euro(c["val"])}</td><td class="n">{c["tpi"]:.2f}</td>'
            f'<td class="n">#{c["rank"]}</td><td class="n">{c["eta"]:.0f}</td>'
            f'<td class="n">{c["aff"]:.2f}</td></tr>')
    r.append("</tbody></table>")
    return "\n".join(r)


def main():
    global PAYLOAD, PAYLOAD_PREC

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--stagione", help="es. 2025-26 (default: l'ultima con abbastanza minuti)")
    ap.add_argument("--elenca", action="store_true",
                    help="dice quali stagioni potrebbe raccontare, e non scrive niente")
    a = ap.parse_args()

    # La pagina e' scritta sulla Serie A: la barra elenca i suoi link e il testo
    # nomina il suo campionato. Generarla altrove pubblicherebbe un menu di
    # un'altra lega — lo stesso difetto per cui PAGINE_EXTRA esiste.
    if config.LEGA_UNDERSTAT != "ITA-Serie A":
        raise SystemExit("Il caso di mercato esiste solo per la Serie A "
                         "(lega attuale: %s). Non si genera." % config.LEGA_UNDERSTAT)

    if a.elenca:
        trovate = stagioni_disponibili()
        for st in sorted((k for k in trovate if not k.endswith("|n")), reverse=True):
            n = trovate.get("%s|n" % st, 0)
            print("  %s  %4d giocatori sopra %d'  %s  %s"
                  % (st, n, MIN_MINUTI,
                     "ok " if n >= MIN_QUALIFICATI else "no ", trovate[st]))
        return

    stagione_caso, PAYLOAD, PAYLOAD_PREC = scegli_stagione(a.stagione)
    d, tutti = carica()
    n_tot = d["players"][0]["rank"]["n_total"]
    ammessi = [c for c in tutti if ammissibile(c)]
    scartati = sorted(
        [c for c in tutti if c["val"] <= BUDGET and not ammissibile(c)],
        key=lambda c: -c["tpi"])[:3]

    s_ora, t_ora = miglior_terna(ammessi, BUDGET)
    # A PARITA' DI BUDGET, non al prezzo minimo: e' il confronto che un club fa davvero.
    s_fut, t_fut = miglior_terna([c for c in ammessi if c["eta"] <= 25], BUDGET)
    pareggio = under_che_eguaglia(ammessi, s_ora)

    sp_ora = sum(c["val"] for c in t_ora)
    costo_futuro = sum(c["val"] for c in t_fut)
    eta_ora = sum(c["eta"] for c in t_ora) / 3
    eta_fut = sum(c["eta"] for c in t_fut) / 3
    rapporto_prezzo = costo_futuro / sp_ora
    rapporto_resa = s_fut / s_ora
    n_under = len([c for c in ammessi if c["eta"] <= 25])

    # La domanda che segue sempre: e a un budget piu' stretto, i giovani?
    giovani_stretto = [c for c in ammessi if c["eta"] <= 25 and c["val"] <= BUDGET_STRETTO]
    s_gv, t_gv = miglior_terna(giovani_stretto, BUDGET_STRETTO)
    s_gen, t_gen = miglior_terna(ammessi, BUDGET_STRETTO)
    ruoli_gv = Counter(c["ruolo"] for c in giovani_stretto)
    trovate = sorted(giovani_stretto, key=lambda c: -c["tpi"])[:2]
    righe_trovate = "".join(
        el("li",
           f'<strong>{c["nome"]}</strong> ({c["sq"]}, {c["ruolo"]}, {c["eta"]:.0f} anni, '
           f'{euro(c["val"])}) &mdash; TPI {c["tpi"]:.2f}, <strong>#{c["rank"]} su {n_tot}</strong>.',
           f'<strong>{c["nome"]}</strong> ({c["sq"]}, {c["ruolo"]}, aged {c["eta"]:.0f}, '
           f'{euro(c["val"])}) &mdash; TPI {c["tpi"]:.2f}, <strong>#{c["rank"]} of {n_tot}</strong>.')
        for c in trovate)

    scarti = "".join(
        el("li",
           f'<strong>{c["nome"]}</strong> ({c["sq"]}, {euro(c["val"])}, TPI {c["tpi"]:.2f}, '
           f'#{c["rank"]}) &mdash; {"; ".join(motivi_esclusione(c))}.',
           f'<strong>{c["nome"]}</strong> ({c["sq"]}, {euro(c["val"])}, TPI {c["tpi"]:.2f}, '
           f'#{c["rank"]}) &mdash; {"; ".join(motivi_esclusione(c, en=True))}.')
        for c in scartati)

    nomi_ora = ", ".join(c["nome"] for c in sorted(t_ora, key=lambda c: -c["tpi"]))
    peggior_rank = max(c["rank"] for c in t_ora)
    tabella_ora = tabella(t_ora)
    n_ammessi, n_valore = len(ammessi), len(tutti)
    n_senza_valore = len(d["players"]) - len(tutti)
    n_giov = len(giovani_stretto)
    n_dif, n_att, n_cen = ruoli_gv["DIF"], ruoli_gv["ATT"], ruoli_gv["CEN"]
    spesa_trovate = sum(c["val"] for c in trovate)
    peggior_rank_trovate = max(c["rank"] for c in trovate)
    s_ora_meno, s_ora_quasi = s_ora - 1.0, s_ora - 0.05
    frase_pareggio = ("non si raggiunge a nessun prezzo" if pareggio is None
                      else "si raggiunge spendendo " + euro(sum(c["val"] for c in pareggio)))
    frase_pareggio_en = ("is not reachable at any price" if pareggio is None
                         else "is reached by spending " + euro(sum(c["val"] for c in pareggio)))


    fermi = valori_fermi()

    # --- Caso 2: il club cede il suo gioiello e reinveste ----------------------
    # Il venduto non e' scelto a mano: e' il giocatore piu' caro sotto i 23 anni,
    # cioe' il caso tipico del club che incassa una plusvalenza e deve rifare la rosa.
    gioiello = max((c for c in tutti if c["eta"] <= ETA_GIOIELLO), key=lambda c: c["val"])
    budget2 = gioiello["val"]
    mercato2 = [c for c in ammessi if c["sq"] != gioiello["sq"]
                and c["nome"] != gioiello["nome"] and c["val"] <= budget2]

    # a) un solo sostituto, stesso ruolo, almeno forte quanto lui
    sostituti = sorted((c for c in mercato2 if c["ruolo"] == gioiello["ruolo"]
                        and c["tpi"] >= gioiello["tpi"]), key=lambda c: -c["tpi"])
    # il piu' giovane fra quelli che lo battono: la risposta che il primo caso non poteva dare
    sost_giovane = min(sostituti, key=lambda c: c["eta"]) if sostituti else None
    # il piu' efficiente: chi lo batte spendendo meno
    sost_efficiente = min(sostituti, key=lambda c: c["val"]) if sostituti else None

    tabella_sost = tabella(sostituti[:4]) if sostituti else ""

    # b) tre giocatori, senza e con vincolo d'eta'
    s2_lib, t2_lib = miglior_terna(mercato2, budget2)
    s2_gio, t2_gio = miglior_terna([c for c in mercato2 if c["eta"] <= 26], budget2)
    eta2_lib = sum(c["eta"] for c in t2_lib) / 3
    eta2_gio = sum(c["eta"] for c in t2_gio) / 3
    resa2 = s2_gio / s2_lib

    # --- blocchi bilingui ------------------------------------------------------
    B = BUDGET
    BS = BUDGET_STRETTO
    e_sp, e_bud, e_fut = euro(sp_ora), euro(B), euro(costo_futuro)
    e_trov = euro(spesa_trovate)

    intro = el("p",
        f"Una classifica dice chi &egrave; pi&ugrave; forte. Comprare &egrave; un'altra domanda, e "
        f"dipende da quanto costa, da quanto gioca e da quanto regge. Questa pagina fa quel passo "
        f"su un campione di {n_tot} giocatori.",
        f"A ranking says who is better. Buying is a different question, and it depends on price, "
        f"on minutes played and on how well a player holds up. This page takes that step, over a "
        f"sample of {n_tot} players.",
        'style="font-size:18px;max-width:62ch"')

    verdetto = (
        el("div", "La decisione", "The call", 'class="cm-occhiello"')
        + el("p", f"<strong>{nomi_ora}</strong>, per {e_sp} dei {e_bud} disponibili.",
                  f"<strong>{nomi_ora}</strong>, for {e_sp} of the {e_bud} available.")
        + el("p",
             f"Sono tre giocatori nei primi {peggior_rank} di {n_tot} e hanno in media "
             f"<strong>{eta_ora:.0f} anni</strong>: con questo budget si compra rendimento adesso, "
             f"e si accetta di non avere niente da rivendere fra due stagioni. La stessa cifra "
             f"spesa su soli under 25 rende il <strong>{rapporto_resa*100:.0f}%</strong>.",
             f"Three players inside the top {peggior_rank} of {n_tot}, average age "
             f"<strong>{eta_ora:.0f}</strong>: this budget buys output now, and accepts having "
             f"nothing left to sell on in two seasons. The same money spent on under-25s returns "
             f"<strong>{rapporto_resa*100:.0f}%</strong> of it."))

    nota_verdetto = el("p",
        "Il resto della pagina &egrave; come ci sono arrivato, e cosa questo conto non copre.",
        "The rest of the page is how I got there, and what this calculation leaves out.",
        'class="cm-nota"')

    h_regole = el("h2", "Le regole della scelta", "The rules of the choice")
    p_regole = el("p",
        "Tre filtri di ammissibilit&agrave; e un vincolo di rosa, fissati prima di guardare i nomi. "
        "Cambiarli cambia la risposta, quindi stanno qui e non in fondo.",
        "Three eligibility filters and one squad constraint, fixed before looking at any name. "
        "Changing them changes the answer, so they belong here and not in a footnote.")
    regole = "<ul>" + "".join([
        el("li", f"<strong>Almeno {MIN_MINUTI} minuti giocati</strong> &mdash; sotto le diciassette "
                 f"partite l'indice descrive un campione, non un giocatore.",
                 f"<strong>At least {MIN_MINUTI} minutes played</strong> &mdash; below seventeen full "
                 f"matches the index describes a sample, not a player."),
        el("li", f"<strong>Confidenza dell'indice &ge; {MIN_CONFIDENCE:.2f}</strong> &mdash; il valore "
                 f"con cui l'indice dichiara quanto si fida di s&eacute; stesso su quel nome.",
                 f"<strong>Index confidence &ge; {MIN_CONFIDENCE:.2f}</strong> &mdash; the figure with "
                 f"which the index states how much it trusts itself on that name."),
        el("li", f"<strong>Affidabilit&agrave; fisica &ge; {MIN_AFFIDABILITA:.2f}</strong> &mdash; chi "
                 f"non &egrave; in campo non &egrave; un affare, per quanto costi poco.",
                 f"<strong>Physical availability &ge; {MIN_AFFIDABILITA:.2f}</strong> &mdash; a player "
                 f"who is not on the pitch is not a bargain, however cheap."),
        el("li", "<strong>Almeno due reparti diversi</strong> &mdash; tre giocatori dello stesso "
                 "ruolo sono una collezione, non un mercato.",
                 "<strong>At least two different lines</strong> &mdash; three players in the same "
                 "role are a collection, not a transfer window."),
    ]) + "</ul>"
    p_passano = el("p",
        f"Passano <strong>{n_ammessi}</strong> giocatori sui {n_valore} con un valore di mercato noto.",
        f"<strong>{n_ammessi}</strong> players pass, out of the {n_valore} with a known market value.")

    h_tre = el("h2", "I tre, in chiaro", "The three, in full")
    nota_tre = el("p",
        f"Spesa {e_sp} &middot; somma TPI {s_ora:.2f} &middot; et&agrave; media {eta_ora:.1f} anni",
        f"Spend {e_sp} &middot; TPI sum {s_ora:.2f} &middot; average age {eta_ora:.1f}",
        'class="cm-nota"')
    p_tre = el("p",
        "Costano poco perch&eacute; sono vecchi: il mercato prezza gli anni che restano, l'indice "
        "misura quelli in corso. &Egrave; su questa differenza che il budget compra qualcosa.",
        "They are cheap because they are old: the market prices the years left, the index measures "
        "the year in progress. That gap is where this budget buys something.")

    h_under = el("h2", "La stessa cifra, su giocatori under 25", "The same money, on under-25s")
    p_under = el("p",
        "Stesso budget, stessi filtri, solo giocatori fino a 25 anni. La terna esiste: cambia "
        "quanto rende.",
        "Same budget, same filters, only players up to 25. The trio exists: what changes is what "
        "it returns.")
    col_a = (el("div", "Senza vincolo d'et&agrave;", "No age constraint", 'class="cm-occhiello"')
             + f'<div class="cm-cifra">{s_ora:.2f}</div>'
             + el("p", f"somma TPI, per {e_sp}<br>et&agrave; media <strong>{eta_ora:.1f}</strong>",
                       f"TPI sum, for {e_sp}<br>average age <strong>{eta_ora:.1f}</strong>",
                  'style="font-size:14.5px"'))
    col_b = (el("div", "Solo under 25", "Under-25s only", 'class="cm-occhiello"')
             + f'<div class="cm-cifra">{s_fut:.2f}</div>'
             + el("p", f"somma TPI, per {e_fut}<br>et&agrave; media <strong>{eta_fut:.1f}</strong>",
                       f"TPI sum, for {e_fut}<br>average age <strong>{eta_fut:.1f}</strong>",
                  'style="font-size:14.5px"'))
    p_pareggio = el("p",
        f"Il livello della terna scelta, con soli under 25, <strong>{frase_pareggio}</strong> "
        f"dentro questo campione.",
        f"The level of the chosen trio, with under-25s only, <strong>{frase_pareggio_en}</strong> "
        f"within this sample.")

    h_giov = el("h2", f"I giovani a {BS:.0f} milioni", f"Young players at {BS:.0f} million")
    p_giov = el("p",
        f"A questo prezzo gli under 25 ammessi sono <strong>{n_giov}</strong>, e "
        f"<strong>{n_dif} di loro sono difensori</strong> contro {n_att} attaccanti e {n_cen} "
        f"centrocampisti. La loro miglior terna somma {s_gv:.2f} contro {s_gen:.2f} di una senza "
        f"vincolo d'et&agrave; &mdash; su una scala che misura l'impatto <em>offensivo</em>, e che "
        f"quindi assegna un punteggio basso a un difensore per costruzione, non per giudizio.",
        f"At this price there are <strong>{n_giov}</strong> eligible under-25s, and "
        f"<strong>{n_dif} of them are defenders</strong> against {n_att} forwards and {n_cen} "
        f"midfielders. Their best trio sums to {s_gv:.2f} against {s_gen:.2f} for one with no age "
        f"limit &mdash; on a scale that measures <em>attacking</em> impact, and therefore scores a "
        f"defender low by construction, not by judgement.")
    p_giov2 = el("p",
        "Il giovane a poco prezzo che esiste in Serie A &egrave; quasi sempre un difensore, e su "
        "questa scala non pu&ograve; vincere. Due arrivano comunque in alto:",
        "The cheap young player who exists in Serie A is nearly always a defender, and on this "
        "scale he cannot win. Two get high anyway:")
    p_giov3 = el("p",
        f"{e_trov} in due, entrambi nei primi {peggior_rank_trovate} di {n_tot}, entrambi sotto i "
        f"venticinque anni.",
        f"{e_trov} for the pair, both inside the top {peggior_rank_trovate} of {n_tot}, both under "
        f"twenty-five.")

    if fermi and fermi[1] and fermi[0] == fermi[1]:
        coda_valore_it = (
            f" <strong>Ed &egrave; una fotografia sola, non una rilevazione per stagione</strong>: "
            f"fra le due stagioni pubblicate il valore non cambia per nessuno dei {fermi[1]} "
            f"giocatori presenti in entrambe. Su questi dati una plusvalenza non &egrave; "
            f"misurabile, e infatti questa pagina non la usa.")
        coda_valore_en = (
            f" <strong>And it is a single snapshot, not a per-season reading</strong>: across the "
            f"two published seasons the value does not change for any of the {fermi[1]} players "
            f"present in both. On this data a resale gain is not measurable, and this page does "
            f"not use one.")
    elif fermi and fermi[1]:
        coda_valore_it = (f" Fra le due stagioni pubblicate il valore cambia per "
                          f"{fermi[1]-fermi[0]} giocatori su {fermi[1]}.")
        coda_valore_en = (f" Across the two published seasons the value changes for "
                          f"{fermi[1]-fermi[0]} players out of {fermi[1]}.")
    else:
        coda_valore_it = coda_valore_en = ""

    e_bud2 = euro(budget2)
    h_caso2 = el("h2", f"Secondo caso: {e_bud2} da reinvestire",
                       f"Second case: {e_bud2} to reinvest")
    p_caso2 = el("p",
        f"Il primo caso ha un budget da club di met&agrave; classifica. Questo &egrave; l'altro "
        f"mercato che esiste davvero: <strong>{gioiello['sq']}</strong> cede "
        f"<strong>{gioiello['nome']}</strong> &mdash; {gioiello['eta']:.0f} anni, TPI "
        f"{gioiello['tpi']:.2f}, #{gioiello['rank']} di {n_tot} &mdash; e reinveste l'incasso, "
        f"{e_bud2}. Stessi filtri, stesso metodo, escluso il {gioiello['sq']} dal mercato.",
        f"The first case has a mid-table club's budget. This is the other market that really "
        f"exists: <strong>{gioiello['sq']}</strong> sell <strong>{gioiello['nome']}</strong> "
        f"&mdash; aged {gioiello['eta']:.0f}, TPI {gioiello['tpi']:.2f}, #{gioiello['rank']} of "
        f"{n_tot} &mdash; and reinvest the {e_bud2} they receive. Same filters, same method, with "
        f"{gioiello['sq']} taken out of the market.")
    p_sost = el("p",
        f"Nel suo ruolo, e dentro quella cifra, i giocatori pi&ugrave; forti di lui sono "
        f"<strong>{len(sostituti)}</strong>. Il pi&ugrave; giovane ha "
        f"<strong>{sost_giovane['eta']:.0f} anni</strong>: {sost_giovane['nome']} "
        f"({sost_giovane['sq']}), TPI {sost_giovane['tpi']:.2f}, #{sost_giovane['rank']}, per "
        f"{euro(sost_giovane['val'])}. Il meno caro &egrave; {sost_efficiente['nome']} "
        f"({sost_efficiente['sq']}, {sost_efficiente['eta']:.0f} anni): lo batte spendendo "
        f"{euro(sost_efficiente['val'])} e ne lascia "
        f"{euro(budget2 - sost_efficiente['val'])} per il resto della rosa.",
        f"In his position, and within that figure, <strong>{len(sostituti)}</strong> players are "
        f"stronger than him. The youngest is <strong>{sost_giovane['eta']:.0f}</strong>: "
        f"{sost_giovane['nome']} ({sost_giovane['sq']}), TPI {sost_giovane['tpi']:.2f}, "
        f"#{sost_giovane['rank']}, for {euro(sost_giovane['val'])}. The cheapest is "
        f"{sost_efficiente['nome']} ({sost_efficiente['sq']}, aged {sost_efficiente['eta']:.0f}): "
        f"he beats him for {euro(sost_efficiente['val'])} and leaves "
        f"{euro(budget2 - sost_efficiente['val'])} for the rest of the squad.")
    col2_a = (el("div", "Tre giocatori, senza vincolo", "Three players, no constraint",
                 'class="cm-occhiello"')
              + f'<div class="cm-cifra">{s2_lib:.2f}</div>'
              + el("p", f"somma TPI, per {euro(sum(c['val'] for c in t2_lib))}<br>"
                        f"et&agrave; media <strong>{eta2_lib:.1f}</strong>",
                        f"TPI sum, for {euro(sum(c['val'] for c in t2_lib))}<br>"
                        f"average age <strong>{eta2_lib:.1f}</strong>",
                   'style="font-size:14.5px"'))
    col2_b = (el("div", "Tre giocatori, tutti under 26", "Three players, all under 26",
                 'class="cm-occhiello"')
              + f'<div class="cm-cifra">{s2_gio:.2f}</div>'
              + el("p", f"somma TPI, per {euro(sum(c['val'] for c in t2_gio))}<br>"
                        f"et&agrave; media <strong>{eta2_gio:.1f}</strong>",
                        f"TPI sum, for {euro(sum(c['val'] for c in t2_gio))}<br>"
                        f"average age <strong>{eta2_gio:.1f}</strong>",
                   'style="font-size:14.5px"'))
    p_caso2_fine = el("p",
        f"Con questo budget il vincolo d'et&agrave; costa il "
        f"<strong>{(1-resa2)*100:.0f}%</strong> del rendimento, contro il "
        f"{(1-rapporto_resa)*100:.0f}% del primo caso. E il miglior sostituto singolo del giocatore "
        f"ceduto ha la sua stessa et&agrave;.",
        f"At this budget the age constraint costs <strong>{(1-resa2)*100:.0f}%</strong> of the "
        f"output, against {(1-rapporto_resa)*100:.0f}% in the first case. And the best single "
        f"replacement for the player sold is the same age as him.")
    verdetto2 = (el("div", "Quello che separa i due casi", "What separates the two cases",
                    'class="cm-occhiello"')
        + el("p",
             f"Non &egrave; l'indice a preferire i vecchi: &egrave; <strong>il budget</strong>. "
             f"Con {euro(BUDGET)} l'unico modo di comprare rendimento &egrave; comprare et&agrave;, "
             f"perch&eacute; a quel prezzo il mercato vende solo anni finali. Con {e_bud2} la "
             f"qualit&agrave; giovane &egrave; sullo scaffale, e la distanza fra le due strade si "
             f"chiude da {(1-rapporto_resa)*100:.0f} punti a {(1-resa2)*100:.0f}.",
             f"It is not the index that prefers old players: it is <strong>the budget</strong>. "
             f"At {euro(BUDGET)} the only way to buy output is to buy age, because at that price "
             f"the market only sells final years. At {e_bud2} young quality is on the shelf, and "
             f"the gap between the two routes closes from {(1-rapporto_resa)*100:.0f} points to "
             f"{(1-resa2)*100:.0f}."))

    h_fuori = el("h2", "Chi i filtri lasciano fuori", "Who the filters leave out")
    p_fuori = el("p",
        "I tre nomi pi&ugrave; forti nella fascia di prezzo non entrano nella scelta. Sono anche i "
        "tre che si comprerebbero leggendo solo la classifica.",
        "The three strongest names in the price band do not make the shortlist. They are also the "
        "three you would buy reading the ranking alone.")

    h_limiti = el("h2", "Quello che questo conto non copre", "What this calculation leaves out",
                  'style="margin-top:0"')
    limiti = "<ul>" + "".join([
        el("li", "<strong>&Egrave; output dimostrato, non previsione.</strong> Il TPI ordina quello "
                 "che &egrave; successo. La <a href=\"validazione.html\">pagina delle verifiche</a> "
                 "dichiara che contro tre predittori elementari l'indice non batte nessuno sul "
                 "rendimento futuro.",
                 "<strong>This is demonstrated output, not a forecast.</strong> The TPI ranks what "
                 "has happened. The <a href=\"validazione.html\">validation page</a> states that "
                 "against three elementary predictors the index beats none of them on future output."),
        el("li", "<strong>Misura il solo impatto offensivo</strong>, quindi tratta male i ruoli il "
                 "cui lavoro non finisce in un gol o in un assist.",
                 "<strong>It measures attacking impact only</strong>, so it treats badly the roles "
                 "whose work does not end in a goal or an assist."),
        el("li", "<strong>Il valore di mercato &egrave; una stima</strong>: nessun club vende a quella "
                 "cifra. Contratto, procuratore e concorrenza la spostano, a volte del doppio."
                 + coda_valore_it,
                 "<strong>Market value is an estimate</strong>: no club sells at that figure. "
                 "Contract, agent and competition move it, sometimes by double." + coda_valore_en),
        el("li", "<strong>Manca l'ingaggio</strong>, che su un trentasettenne &egrave; pi&ugrave; "
                 "della met&agrave; del costo reale.",
                 "<strong>Wages are missing</strong>, and on a thirty-seven-year-old they are more "
                 "than half the real cost."),
        el("li", "<strong>Manca il modulo</strong>: l'indice non sa se questi tre entrano negli "
                 "undici insieme agli altri otto. Il vincolo &laquo;due reparti&raquo; distingue "
                 "DIF, CEN e ATT, non un terzino da un centrale.",
                 "<strong>Shape is missing</strong>: the index does not know whether these three fit "
                 "into an eleven with the other eight. The &laquo;two lines&raquo; constraint tells "
                 "DEF, MID and FWD apart, not a full-back from a centre-back."),
        el("li", f"<strong>Le due soglie sono scelte, non ricavate</strong>: {B:.0f} milioni e 25 "
                 f"anni. Sono i numeri di una trattativa plausibile, non di un calcolo &mdash; con "
                 f"soglie diverse cambiano i nomi, e chi legge deve poterlo sapere.",
                 f"<strong>The two thresholds are chosen, not derived</strong>: {B:.0f} million and "
                 f"25 years. They are the numbers of a plausible negotiation, not of a calculation "
                 f"&mdash; different thresholds give different names, and the reader should know it."),
        el("li", f"<strong>Sommare i TPI &egrave; un'approssimazione.</strong> Il TPI &egrave; un "
                 f"composito su punteggi standardizzati: la somma di tre &egrave; un modo comodo di "
                 f"ordinare le terne, non una misura di quanto una rosa migliora. Fra una terna da "
                 f"{s_ora:.2f} e una da {s_ora_meno:.2f} la differenza &egrave; reale; fra "
                 f"{s_ora:.2f} e {s_ora_quasi:.2f} non significa niente.",
                 f"<strong>Summing TPIs is an approximation.</strong> The TPI is a composite over "
                 f"standardised scores: adding three is a convenient way to rank trios, not a "
                 f"measure of how much a squad improves. Between a {s_ora:.2f} trio and a "
                 f"{s_ora_meno:.2f} one the difference is real; between {s_ora:.2f} and "
                 f"{s_ora_quasi:.2f} it means nothing."),
        el("li", f"<strong>Restano fuori {n_senza_valore} giocatori</strong> dei {n_tot} perch&eacute; "
                 f"il valore di mercato non c'&egrave;. Non li ha esclusi un criterio, mancava il dato.",
                 f"<strong>{n_senza_valore} players of the {n_tot} are left out</strong> because the "
                 f"market value is missing. No criterion excluded them: the data was not there."),
    ]) + "</ul>"
    et_caso = config.etichetta_stagione(stagione_caso)
    diversa = stagione_caso != config.SEASON_CORRENTE
    nota_gen = el("p",
        f"Pagina generata da <code>caso_mercato.py</code> a partire da "
        f"<code>{PAYLOAD.name}</code>, stagione {et_caso}: nessun numero &egrave; "
        f"scritto a mano.",
        f"Page generated by <code>caso_mercato.py</code> from "
        f"<code>{PAYLOAD.name}</code>, {et_caso} season: no figure is typed by hand.",
        'class="cm-nota" style="margin-top:24px"')

    # Quando il caso non e' della stagione che il sito pubblica, lo dice in
    # cima. E' la stessa regola della homepage: la stagione si dichiara, non si
    # lascia indovinare. La soglia dei 1500 minuti rende questa la condizione
    # normale da agosto a dicembre, non l'eccezione.
    avviso_stagione = el("p",
        f"I dati di questa pagina sono della stagione <strong>{et_caso}</strong>, "
        f"conclusa. Il caso ha bisogno di almeno {MIN_MINUTI} minuti a testa — "
        f"circa diciassette partite intere — quindi si rifa&agrave; sulla "
        f"{config.SEASON_ETICHETTA} quando ne avr&agrave; abbastanza.",
        f"The figures on this page are from the <strong>{et_caso}</strong> season, "
        f"now finished. The case needs at least {MIN_MINUTI} minutes per player — "
        f"about seventeen full matches — so it will be rebuilt on "
        f"{config.SEASON_ETICHETTA} once there are enough.",
        'class="cm-nota" style="margin:14px 0 0"') if diversa else ""
    html = f"""<!DOCTYPE html>
<html lang="it">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>Con {BUDGET:.0f} milioni, questi tre &mdash; {config.SITO_NOME}</title>
<meta name="description" content="L'indice usato per rispondere a una domanda di mercato: con un budget dato, quali tre giocatori, a quali condizioni e con quali limiti.">
<link rel="stylesheet" href="stile.css">
<style>
  .cm-w{{max-width:880px;margin:0 auto;padding:0 22px 90px}}
  .cm-w h2{{font-family:var(--disp);text-transform:uppercase;font-weight:500;
    font-size:clamp(20px,3.2vw,27px);letter-spacing:.01em;margin:52px 0 14px}}
  .cm-w p{{color:var(--ls);margin:0 0 15px;max-width:68ch}}
  .cm-w strong{{color:var(--lp)}}
  .cm-occhiello{{font-family:var(--mono);font-size:11px;letter-spacing:.16em;
    text-transform:uppercase;color:var(--lt);margin-bottom:9px}}
  .cm-t{{width:100%;border-collapse:collapse;margin:16px 0 6px;font-size:14.5px}}
  .cm-t th{{font-family:var(--mono);font-size:10.5px;letter-spacing:.11em;
    text-transform:uppercase;color:var(--lt);text-align:left;font-weight:400;
    padding:0 10px 8px 0;border-bottom:1px solid var(--sep2)}}
  .cm-t td{{padding:9px 10px 9px 0;border-bottom:1px solid var(--sep)}}
  .cm-t .n{{text-align:right;font-variant-numeric:tabular-nums}}
  .cm-verdetto{{border:1px solid var(--sep2);border-left:3px solid var(--orng);
    background:var(--bg1);padding:26px 28px;margin:26px 0 8px}}
  .cm-verdetto p{{font-size:17px;max-width:64ch}}
  .cm-verdetto p:last-child{{margin-bottom:0}}
  .cm-nota{{font-family:var(--mono);font-size:12.5px;color:var(--lt);margin:6px 0 0}}
  .cm-due{{display:grid;grid-template-columns:1fr 1fr;gap:26px;margin:20px 0 6px}}
  @media(max-width:720px){{.cm-due{{grid-template-columns:1fr}}}}
  .cm-cifra{{font-family:var(--disp);font-size:clamp(28px,5.5vw,42px);color:var(--orng);
    line-height:1;margin-bottom:6px}}
  .cm-w ul{{color:var(--ls);margin:0 0 15px;padding-left:20px;max-width:68ch}}
  .cm-w li{{margin-bottom:9px}}
  .cm-limiti{{border-top:1px solid var(--sep2);margin-top:60px;padding-top:26px}}
</style>
</head>
<body>
<nav class="nav">
  <a class="nav-brand" href="index.html">{config.SITO_MARCHIO} <small>{config.SEASON_ETICHETTA_BREVE}</small></a>
  <a class="nav-mark" href="index.html" aria-label="{config.SITO_NOME}" title="{config.SITO_NOME}"><svg viewBox="0 0 32 32" width="19" height="19" aria-hidden="true" focusable="false"><rect x="6" y="19" width="5" height="7" fill="currentColor"/><rect x="13.5" y="13" width="5" height="13" fill="currentColor"/><rect x="21" y="6" width="5" height="20" fill="currentColor"/></svg></a>
  <div class="nav-sp"></div>
  <span data-i18n-switcher></span>
  <div class="nav-links"><a class="nav-link" href="index.html" data-it="Homepage" data-en="Homepage">Homepage</a><a class="nav-link" href="dashboard_serie_a.html" data-it="Classifica" data-en="Ranking">Classifica</a><a class="nav-link" href="validazione.html" data-it="Validazione" data-en="Validation">Validazione</a><a class="nav-link" href="guida_completa.html" data-it="Metodo" data-en="Method">Metodo</a><a class="nav-link on" href="caso-mercato.html" aria-current="page" data-it="Caso di mercato" data-en="Market case">Caso di mercato</a><a class="nav-link pro" href="dashboard_pro.html" data-it="TPI Pro" data-en="TPI Pro">TPI Pro</a></div>
</nav>

<main class="cm-w">
  <header style="padding:56px 0 8px">
    <div class="cm-occhiello" data-it="{config.SITO_NOME} &middot; Caso di mercato &middot; dati {et_caso}" data-en="{config.SITO_NOME} &middot; Market case &middot; {et_caso} data">{config.SITO_NOME} &middot; Caso di mercato &middot; dati {et_caso}</div>
    <h1 style="font-family:var(--disp);text-transform:uppercase;font-weight:500;
      font-size:clamp(30px,7vw,58px);line-height:.98;margin:0 0 18px"
      data-it="Con {BUDGET:.0f} milioni,<br><em style=&quot;font-style:normal;color:var(--orng)&quot;>questi tre</em>"
      data-en="With {BUDGET:.0f} million,<br><em style=&quot;font-style:normal;color:var(--orng)&quot;>these three</em>">Con {BUDGET:.0f} milioni,<br><em style="font-style:normal;color:var(--orng)">questi tre</em></h1>
    {intro}
    {avviso_stagione}
  </header>

  <div class="cm-verdetto">{verdetto}</div>
  {nota_verdetto}

  {h_regole}
  {p_regole}
  {regole}
  {p_passano}

  {h_tre}
  {tabella_ora}
  {nota_tre}
  {p_tre}

  {h_under}
  {p_under}
  <div class="cm-due">
    <div>{col_a}</div>
    <div>{col_b}</div>
  </div>
  {p_pareggio}

  {h_giov}
  {p_giov}
  {p_giov2}
  <ul>{righe_trovate}</ul>
  {p_giov3}

  {h_caso2}
  {p_caso2}
  {tabella_sost}
  {p_sost}
  <div class="cm-due">
    <div>{col2_a}</div>
    <div>{col2_b}</div>
  </div>
  {p_caso2_fine}

  <div class="cm-verdetto">{verdetto2}</div>

  {h_fuori}
  {p_fuori}
  <ul>{scarti}</ul>

  <section class="cm-limiti">
    {h_limiti}
    {limiti}
    {nota_gen}
  </section>
</main>
<script src="i18n.js"></script>
</body>
</html>
"""
    USCITA.mkdir(parents=True, exist_ok=True)
    OUT.write_text(html, encoding="utf-8")
    print(f"scritto {OUT}")
    # Stessa regola delle altre pagine: si scrive nella cartella di lavoro e si
    # copia nel repo del sito, se c'e'. Prima il generatore stava dentro il repo
    # e scriveva solo li', che e' il motivo per cui era fuori dalla sequenza.
    if REPO.is_dir():
        (REPO / OUT.name).write_text(html, encoding="utf-8")
        print(f"scritto {REPO / OUT.name}  (copia per il repo del sito)")
    print(f"  stagione dei dati: {stagione_caso}"
          + ("  (il sito pubblica la %s: la pagina lo dichiara)" % config.SEASON_CORRENTE
             if diversa else "  (la stessa che pubblica il sito)"))
    print(f"  ammessi {len(ammessi)}/{len(tutti)}")
    print(f"  terna 'ora'     : {sp_ora:.1f}M, TPI {s_ora:.2f}, eta {eta_ora:.1f} -> "
          + ", ".join(c["nome"] for c in sorted(t_ora, key=lambda c: -c["tpi"])))
    print(f"  terna 'under 25': {costo_futuro:.1f}M, TPI {s_fut:.2f}, eta {eta_fut:.1f} -> "
          + ", ".join(c["nome"] for c in sorted(t_fut, key=lambda c: -c["tpi"])))
    print(f"  rapporto prezzo {rapporto_prezzo:.2f}x, rapporto resa {rapporto_resa*100:.0f}%")


if __name__ == "__main__":
    main()
