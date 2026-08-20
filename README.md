# Serie A Index — engine

The code behind **[serie-a-index](https://raffaeleciccone-analyst.github.io/serie-a-index/)**: a
descriptive ranking model for Serie A players, and — more to the point — the machinery that
checks whether the ranking is worth anything.

This repository is a **curated subset**. See [What is not here](#what-is-not-here).

---

## Start here

If you have ten minutes and want to judge the work, read these three files in this order.

| File | Why |
|---|---|
| [`parte3_valida_tpi.py`](parte3_valida_tpi.py) | Fifteen checks on the index, including the ones it **fails**. Vintage backtests, clustered bootstrap CIs, an ablation study, a permutation placebo, and a baseline test that asks the only question that can sink a composite: *was it worth building?* |
| [`ripara_righe_omonimi.py`](ripara_righe_omonimi.py) | A data bug, start to finish: impossible statistics → diagnosis → a repair rule that refuses to run where it cannot prove itself. The docstring tells the whole story, including the two rules I tried first and why each was wrong. Read it with its three companions — [`unisci_record_doppioni.py`](unisci_record_doppioni.py), [`importa_partite_mancanti.py`](importa_partite_mancanti.py), [`ripara_righe_orfane.py`](ripara_righe_orfane.py) — which is where it gets interesting. |
| [`parte1_analisi.py`](parte1_analisi.py) | The model. Seven dimensions, z-scores computed within role, Bayesian shrinkage toward the role mean, opponent-strength adjustment. The comments say why each choice was made, and where it was wrong before. |

## What this actually does

```
database ──> parte1_analisi.py ──> payload.json ──> parte2_dashboard.py ──> the site
                    │                                pagina_*.py
                    └──> parte3_valida_tpi.py ──> validazione.html
```

Every number on the published site is generated from data by these scripts. None is typed by
hand — that is a rule, and `parte3_valida_tpi.py` dumps its full results to JSON precisely so
that the page cannot claim anything the tests did not produce.

## Things worth looking at

- **A data bug caught by its own implausibility, and the three it was hiding.** A goalkeeper
  had 1 goal, 1.47 xG and 66 minutes per game. He was not a strange goalkeeper: his record held
  another player's matches. Ten records were affected, and every pair shared a **first name** —
  the import had matched on it.

  The obvious repair (delete rows identical to the neighbour's) destroyed real data, because a
  keeper and a defender who both play 90 minutes with no goals produce identical rows *by
  coincidence*. The second rule compared the multiset of minutes against the source and deleted
  the surplus. Eight pairs of ten reconciled; two did not, and the count could not say why. Its
  flaw was structural: it knew *how many* rows were wrong, not *which*, and it added up two
  different defects in one number.

  Because there are two ways to be wrong, and they must be kept apart — rows that belong to
  someone else, and rows that were never imported. A test demanding "after the repair the totals
  must match the source" conflates them and stalls on the second even when the first is solved.

  The rule that worked judges each row on its own, crossing the fixture's source id with the
  player's: a row goes only if the source does not credit it to him, *does* credit it to the
  neighbour at exactly those minutes, and the neighbour's twin row is there in the database. That
  is when the eighth pair turned out to be a false pass — a keeper and a defender with 21 matches
  each, all 90 minutes, identical totals by pure coincidence while 14 of the rows were someone
  else's.

  Behind it were two more defects, each needing its own script and its own acceptance test:
  **82 people existed as two records** because they had changed clubs mid-season, so the
  two-season ranking listed 36 players twice with two different scores; and **277 matches had
  never been imported at all**. The database now reconciles with the source player by player:
  no missing rows, no surplus rows, nobody split in two.
- **A repair that exposed an older bug.** With the split records merged, 49 players vanished from
  the 2024-25 ranking — Krstovic and Baschirotto among them, who played that season in full. The
  cause was not the merge. Every match was tied to the player's **current** club, read from the
  registry, so anyone who had changed shirts silently lost the matches played for the previous
  one; while each record held a single club the damage stayed invisible. The fix takes the club
  from the fixture instead of the registry. The 2024-25 count came back at 356 — *above* the 340
  it started from. Those minutes had been missing all along.
- **Vintage backtesting** — `main(max_giornata=N)` recomputes the whole index as it would have
  looked at matchday N, from the raw match rows. That is how the convergence curve on the site is
  measured: ρ 0.30 at matchday 3, and it does not cross 0.85 until matchday 22. It is also how the model is tested
  out-of-sample without leaking the future into the past.
- **The dead-dimension guard** — a dimension whose distribution collapses is caught at build
  time. It exists because one of them did: consistency used to be `1 − IQR/median`, which on a
  zero-inflated distribution produced almost no variance at all.
- **Clustered bootstrap** — the same player appears in several vintages, so confidence intervals
  resample by player, not by row. Doing it the naive way made the intervals look about twice as
  good as they were.
- **A constant that lived in four places.** The current season was copied into the season
  picker's table, the CSV filename and the aggregate's caption — three edits every August,
  three chances to forget one — while the qualified count sat in the download link as a literal
  and had already drifted (it read 351 when there were 354). The payload now states which season
  it describes, and everything else is derived from it or from the files that actually exist. A
  test simulates next August with three seasons on file and asserts that no line of code needs
  touching; without it, "nothing to change" is a promise nobody checks.
- **A published failure** — the index does **not** beat raw per-90 output at forecasting the next
  half-season, and the site says so, with the interval next to it.
- **A published circularity** — one weight had been lowered *because* the ablation study flagged
  it, and that same study is published as a check. The weight was put back, the cost of doing so
  was measured (ρ 0.9989 between the two rankings), and the episode is written on the page under
  the chart it concerns.
- **115 tests** (`tests/`), including regression tests that pin the payload's invariants: no
  goalkeepers in an attacking index, no `NaN` in JSON, no player disagreeing with himself between
  the ranking and the squad list. Each of those was a real bug first.

## What is not here

The ingestion layer: scrapers, database schema, anagraphic reconciliation, maintenance scripts.
Two reasons, and neither is modesty:

1. Without the database this code cannot run anyway, so publishing the plumbing would add pages
   without adding understanding.
2. That layer is the part with ongoing value, and this license reserves commercial use.

The four repair scripts *are* here, and they are the exception on purpose: they are where the
reasoning lives, and none of them lets anyone rebuild the pipeline — they need a database that
does not exist publicly. What they show is the discipline, not the plumbing. Each one refuses to
write where it cannot prove itself, and one of them refuses because of a mistake I made in the
other: filling both halves of a person who is still split in two would invent a double count that
was not there before.

A few strings in `audit/` still point at scripts from that layer (`set_up_tpi_pro/…`). They are
not broken imports — they are runtime paths and help text in the fuller repository.

## Running it

Not fully: there is no database here. What does work is the part that needs none — importing the
modules, reading them, and running the pure-logic tests:

```bash
pip install -r requirements.txt
python -m pytest tests/regression/test_ruoli.py -q
```

Anything that touches the database raises on the first call, not on import. That is deliberate:
code should be readable without being installed.

## License

[PolyForm Noncommercial 1.0.0](LICENSE.md) — read it, study it, use it for anything
noncommercial. Commercial use is reserved.

---

*Raffaele Ciccone — [the site](https://raffaeleciccone-analyst.github.io/serie-a-index/) ·
[how it is built](https://raffaeleciccone-analyst.github.io/serie-a-index/guida_completa.html) ·
[what holds up and what does not](https://raffaeleciccone-analyst.github.io/serie-a-index/validazione.html)*
