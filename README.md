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
| [`ripara_righe_omonimi.py`](ripara_righe_omonimi.py) | A data bug, start to finish: impossible statistics → diagnosis → a repair rule that refuses to run where it cannot prove itself. The docstring tells the whole story, including the first rule I tried and why it was wrong. |
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

- **A data bug caught by its own implausibility.** A goalkeeper had 1 goal, 1.47 xG and 66
  minutes per game. He was not a strange goalkeeper: his record held another player's matches.
  Ten records were affected, and every pair shared a **first name** — the import had matched on
  it. The obvious repair (delete rows identical to the neighbour's) destroyed real data, because
  a keeper and a defender who both play 90 minutes with no goals produce identical rows *by
  coincidence*. The rule that worked uses two signals: Understat says **how many** rows are in
  excess, the duplicate says **which**. Eight of ten pairs reconcile exactly and were repaired;
  two do not, and the script refuses to touch them.
- **Vintage backtesting** — `main(max_giornata=N)` recomputes the whole index as it would have
  looked at matchday N, from the raw match rows. That is how the convergence curve on the site is
  measured: ρ 0.31 at matchday 3, 0.86 at matchday 22. It is also how the model is tested
  out-of-sample without leaking the future into the past.
- **The dead-dimension guard** — a dimension whose distribution collapses is caught at build
  time. It exists because one of them did: consistency used to be `1 − IQR/median`, which on a
  zero-inflated distribution produced almost no variance at all.
- **Clustered bootstrap** — the same player appears in several vintages, so confidence intervals
  resample by player, not by row. Doing it the naive way made the intervals look about twice as
  good as they were.
- **A published failure** — the index does **not** beat raw per-90 output at forecasting the next
  half-season, and the site says so, with the interval next to it.
- **A published circularity** — one weight had been lowered *because* the ablation study flagged
  it, and that same study is published as a check. The weight was put back, the cost of doing so
  was measured (ρ 0.9989 between the two rankings), and the episode is written on the page under
  the chart it concerns.
- **99 tests** (`tests/`), including regression tests that pin the payload's invariants: no
  goalkeepers in an attacking index, no `NaN` in JSON, no player disagreeing with himself between
  the ranking and the squad list. Each of those was a real bug first.

## What is not here

The ingestion layer: scrapers, database schema, anagraphic reconciliation, maintenance scripts.
Two reasons, and neither is modesty:

1. Without the database this code cannot run anyway, so publishing the plumbing would add pages
   without adding understanding.
2. That layer is the part with ongoing value, and this license reserves commercial use.

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
