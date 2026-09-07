"""Genera una pagina per ogni squadra: `squadra-<nome>.html`.

Nasce da una richiesta precisa del tifoso che ha guardato il sito: «la pagina
della propria squadra con link diretto. Il riquadro "AC Milan - 0.58 TPI MEDIO"
c'e' gia': farlo diventare una pagina vera da mandare nel gruppo». Il riquadro
era un pezzo di dashboard che spariva al primo filtro; qui diventa un indirizzo
che si puo' spedire.

Legge `payload_full.json` (tutti i qualificati, con la posizione vera) e il
blocco `roster` (chi e' in rosa e quanti minuti ha). Come le altre pagine: ogni
numero visibile arriva dai dati, nessuno e' scritto a mano.

Uso:  python pagina_squadra.py
"""
from __future__ import annotations

import json
import config
import logging
import os
import re
import sys
import unicodedata
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from pagina_stile import assicura_css, _f, avvisa_se_superato, bi, el, evidenza, guscio

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("pagina_squadra")

BASE_DIR = Path(__file__).parent
OUTPUT_DIR = config.cartella_uscita(BASE_DIR)
# Il repo del sito di QUESTA lega: era "serie-a-index" per tutte, quindi
# generare la Premier scriveva le sue pagine nel repo della Serie A.
DEMO_DIR = config.cartella_pubblicazione(BASE_DIR.parent)

# Quanti giocatori mostrare per intero prima di passare all'elenco compatto.
N_IN_EVIDENZA = 5


def slug(nome: str) -> str:
    """Nome squadra -> pezzo di indirizzo. `AC Milan` diventa `ac-milan`."""
    piatto = unicodedata.normalize("NFKD", nome).encode("ascii", "ignore").decode()
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", piatto.lower())).strip("-")


def _carica() -> dict:
    """Il payload piu' completo DELLA STAGIONE PUBBLICATA.

    Piu' completo serve: `payload_full.json` ha tutti i qualificati e quindi la
    posizione vera, non quella dentro i primi cento. Ma quel file non si
    rigenera al cambio di annata — e' l'ingresso del backtest, che gira sulla
    stagione conclusa — quindi da settembre e' il file dell'anno prima. Preso
    com'era, le ventisei pagine delle squadre sarebbero uscite con le rose e i
    numeri della stagione passata sotto il titolo di quella nuova, e nessun
    controllo se ne sarebbe accorto: i dati sono veri, e' l'annata a essere
    un'altra. Se le stagioni non coincidono si pubblica dal payload della
    stagione in corso, che ha cento giocatori invece di tutti ma e' l'unico che
    parla del campionato che il sito dichiara.
    """
    corrente = OUTPUT_DIR / "payload.json"
    if not corrente.is_file():
        raise SystemExit("payload non trovato: lancia prima parte1_analisi.py")
    pay = json.loads(corrente.read_text(encoding="utf-8"))
    config.pretendi_stagione_coerente(pay.get("stagione"), corrente.name)

    pieno = OUTPUT_DIR / "payload_full.json"
    if pieno.is_file():
        try:
            dati = json.loads(pieno.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            dati = None
        if dati is not None:
            if dati.get("stagione") == pay.get("stagione"):
                avvisa_se_superato(pieno, corrente, log)
                return dati
            log.info(f"payload_full.json e' la stagione {dati.get('stagione')} e il "
                     f"sito pubblica la {pay.get('stagione')}: le pagine squadra si "
                     f"scrivono dal payload pubblicato.")
    return pay


def _media(valori: list[float]) -> float | None:
    valori = [v for v in valori if v is not None]
    return sum(valori) / len(valori) if valori else None


def _classifica_squadre(pay: dict) -> list[tuple[str, float, int]]:
    """Squadre ordinate per TPI medio dei qualificati, con quanti ne hanno."""
    per_squadra: dict[str, list[float]] = {}
    for g in pay.get("players") or []:
        t = (g.get("tpi") or {}).get("totale")
        if t is not None:
            per_squadra.setdefault(g["squadra"], []).append(t)
    righe = [(sq, _media(v), len(v)) for sq, v in per_squadra.items()]
    return sorted([r for r in righe if r[1] is not None], key=lambda r: -r[1])


def _pagina(pay: dict, squadra: str, posizione: int, classifica: list) -> str:
    gioc = sorted((g for g in (pay.get("players") or []) if g["squadra"] == squadra),
                  key=lambda g: (g.get("rank") or {}).get("TPI") or 9999)
    rosa = [r for r in (pay.get("roster") or []) if r["squadra"] == squadra]
    ids_qualificati = {g["id"] for g in gioc}
    altri = sorted((r for r in rosa if r["id"] not in ids_qualificati),
                   key=lambda r: -(r.get("minuti") or 0))
    media = _media([(g.get("tpi") or {}).get("totale") for g in gioc])
    n_squadre = len(classifica)
    ruoli = (pay.get("metodo") or {}).get("ruoli_specifici") or {}

    def ruolo_scritto(g, en=False):
        k = g.get("ruolo_fine")
        if k and k in ruoli:
            return ruoli[k]["nome_en" if en else "nome_it"]
        return g.get("ruolo") or ""

    cifre = []
    if media is not None:
        cifre.append((f"{media:+.2f}", "TPI medio dei qualificati",
                      "Mean TPI of the qualified"))
    cifre.append((f"{posizione}&ordm;", f"su {n_squadre} squadre per TPI medio",
                  f"of {n_squadre} teams by mean TPI"))
    cifre.append((str(len(gioc)), "giocatori qualificati", "qualified players"))
    blocco_cifre = "".join(
        f'<div class="cifra"><b>{v}</b><span {bi(it, en)}>{it}</span></div>'
        for v, it, en in cifre)

    ev = []
    for g in gioc[:N_IN_EVIDENZA]:
        pos = (g.get("rank") or {}).get("TPI")
        tot = (g.get("rank") or {}).get("n_total")
        t = (g.get("tpi") or {}).get("totale")
        quota = g.get("ruolo_fine_quota")
        q_txt = f" ({quota*100:.0f}% dei minuti)" if isinstance(quota, (int, float)) else ""
        q_txt_en = f" ({quota*100:.0f}% of his minutes)" if isinstance(quota, (int, float)) else ""
        # Prima il nome: e' il dato della riga. Il ruolo e i minuti spiegano.
        ev.append(evidenza(
            _f(t, 2, True), f"#{pos} su {tot}", f"#{pos} of {tot}",
            f"<strong>{g['nome']}</strong> &mdash; {ruolo_scritto(g).lower()}{q_txt}, "
            f"{int(g.get('minuti') or 0)} minuti giocati.",
            f"<strong>{g['nome']}</strong> &mdash; {ruolo_scritto(g, True).lower()}{q_txt_en}, "
            f"{int(g.get('minuti') or 0)} minutes played."))

    righe_resto = "".join(
        f'<li><span class="pos">#{(g.get("rank") or {}).get("TPI")}</span>'
        f'{g["nome"]}<small>{ruolo_scritto(g)} &middot; {int(g.get("minuti") or 0)}&prime;</small>'
        f'<span class="pos">{_f((g.get("tpi") or {}).get("totale"), 2, True)}</span></li>'
        for g in gioc[N_IN_EVIDENZA:])
    resto = (f'<h3 {bi("Gli altri qualificati", "The other qualified players")}>'
             f'Gli altri qualificati</h3><ul class="nomi">{righe_resto}</ul>'
             if righe_resto else "")

    righe_rosa = "".join(
        f'<li>{r["nome"]}<small>{r.get("ruolo_fine") and ruoli.get(r["ruolo_fine"], {}).get("nome_it") or r.get("ruolo") or "&mdash;"}'
        f' &middot; {int(r.get("minuti") or 0)}&prime;</small></li>'
        for r in altri[:24])
    fuori = (f'<h3 {bi("Il resto della rosa", "The rest of the squad")}>Il resto della rosa</h3>'
             f'<p class="didascalia" {bi("Minuti sotto la soglia per entrare nell&rsquo;indice.", "Below the minutes threshold to enter the index.")}>'
             f'Minuti sotto la soglia per entrare nell&rsquo;indice.</p>'
             f'<ul class="nomi">{righe_rosa}</ul>' if righe_rosa else "")

    p_it = (f"I giocatori del {squadra} nell&rsquo;indice, con la posizione che occupano fra "
            f"tutti i qualificati della {config.LEGA_NOME}. Il TPI medio della squadra &egrave; la media "
            f"dei suoi qualificati: dice quanto pesa la rosa in attacco, non quanti punti fa.")
    p_en = (f"{squadra}&rsquo;s players in the index, with the position they hold among all "
            f"qualified {config.LEGA_NOME} players. The team&rsquo;s mean TPI is the average of its "
            f"qualified players: it says how much the squad weighs in attack, not how many "
            f"points it takes.")

    corpo = f"""<header class="hero riga">
  <div class="cap-num">&nbsp;</div>
  <div>
    <div class="eyebrow" {bi(f"{config.SITO_NOME} &middot; squadra", f"{config.SITO_NOME} &middot; team")}>{config.SITO_NOME} &middot; squadra</div>
    <h1>{squadra}</h1>
    <p class="lede" {bi(p_it, p_en)}>{p_it}</p>
  </div>
  <div class="cifre">{blocco_cifre}</div>
</header>
<section class="cap riga">
  <div class="cap-num">01</div>
  <div>
    {el("h2", "Chi pesa di pi&ugrave;", "Who weighs most")}
    <div class="ev-g">{"".join(ev)}</div>
    {resto}
    {fuori}
    <p class="prosa" style="margin-top:26px"><a class="oltre" href="dashboard_{config.LEGA_SLUG}.html"
      {bi("Aprire la classifica completa", "Open the full ranking")}>Aprire la classifica completa</a></p>
  </div>
</section>"""

    return guscio(
        f"{squadra} &mdash; {config.SITO_NOME}",
        f"I giocatori del {squadra} nell'indice: TPI, ruolo e minuti.",
        f"{squadra} players in the index: TPI, role and minutes.",
        "index.html", corpo,
        [("dashboard_%s.html" % config.LEGA_SLUG, "La classifica", "The ranking"),
         ("index.html", "Torna alla homepage", "Back to the homepage")])


def _togli_squadre_uscite(vive: set[str]) -> None:
    """Cancella le pagine delle squadre che non sono piu' nel campionato.

    Le pagine si scrivono una per squadra del payload, e chi non c'e' piu' non
    viene riscritto: viene lasciato. Finche' la stagione e' la stessa non
    succede niente; al cambio di annata le tre retrocesse restano pubblicate
    con i numeri dell'anno prima e la stagione vecchia nella barra, raggiungibili
    dal loro indirizzo anche se nessuna pagina ci punta piu'. Una pagina che
    nessuno collega ma che Google ha gia' indicizzato e' il modo piu' silenzioso
    di pubblicare dati scaduti.

    La cancellazione vale solo per un giro completo: se il payload contenesse
    quattro squadre per un errore a monte, cancellare le altre sedici sarebbe
    molto peggio del difetto che si sta chiudendo.
    """
    if len(vive) < 10:
        log.warning(f"Solo {len(vive)} pagine squadra scritte: le altre restano "
                    f"dove sono, non e' un giro completo.")
        return
    for cartella in (OUTPUT_DIR, DEMO_DIR):
        if not cartella.is_dir():
            continue
        for vecchia in sorted(cartella.glob("squadra-*.html")):
            if vecchia.name in vive:
                continue
            try:
                vecchia.unlink()
                log.info(f"   tolta {vecchia.name} (squadra non piu' nel campionato)")
            except OSError as e:
                log.warning(f"   {vecchia.name} non si e' lasciata togliere: {e}")


def main() -> None:
    pay = _carica()
    classifica = _classifica_squadre(pay)
    if not classifica:
        raise SystemExit("nessuna squadra nel payload")
    indice = {sq: i + 1 for i, (sq, _, _) in enumerate(classifica)}
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    # un foglio solo, accanto alle pagine, invece di una copia per pagina
    assicura_css(OUTPUT_DIR, DEMO_DIR)
    scritte = []
    for squadra, media, n in classifica:
        html = _pagina(pay, squadra, indice[squadra], classifica)
        nome = f"squadra-{slug(squadra)}.html"
        (OUTPUT_DIR / nome).write_bytes(html.encode("utf-8", "replace"))
        if DEMO_DIR.is_dir():
            (DEMO_DIR / nome).write_bytes(html.encode("utf-8", "replace"))
        scritte.append((nome, squadra, media, n))
    _togli_squadre_uscite({n for n, _, _, _ in scritte})
    log.info(f"OK → {len(scritte)} pagine squadra in {OUTPUT_DIR}")
    if DEMO_DIR.is_dir():
        log.info(f"OK → copiate anche in {DEMO_DIR}")
    for nome, squadra, media, n in scritte[:3]:
        log.info(f"   {nome}  ({squadra}: TPI medio {media:+.2f} su {n} qualificati)")


if __name__ == "__main__":
    main()
