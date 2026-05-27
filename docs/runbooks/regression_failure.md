# Runbook — TPI regression failure

> Quando la CI `TPI Regression Stability` esce in rosso o il job locale
> `tests/regression/run_regression.py` ritorna exit-code 2.

## 0 · Cosa sta proteggendo il sistema

La pipeline F1.4 confronta uno **snapshot baseline** (WORM, F1.3) con
l'output **candidate** dell'analytics attuale. Una `RegressionDiff`
CRITICAL significa che almeno uno tra:

- Spearman ρ tra rank baseline e nuovo < soglia (default 0.95)
- Top-10 / Top-20 overlap sotto soglia
- Δ TPI sul top-1 player oltre soglia
- Drift mean / std / p95 della distribuzione TPI oltre soglia
- Quota giocatori con |Δ TPI| > soglia > limite
- Quota di giocatori comuni baseline ↔ candidate < 90 %

è uscito dai bound.

## 1 · Trovare il report

Il report Markdown è in `reports/regression/` (locale) e tra gli artifact
dell'esecuzione GitHub Actions (`regression-report`). Apri il più recente:

```bash
ls -t reports/regression | head -n 1
```

Sezioni chiave:

- **Sintesi** — tabella con valori vs soglie
- **Perché ha fallito** — lista delle failure_reasons del classifier
- **Biggest climbers / fallers** — i 10 giocatori con maggior movimento
- **Giocatori con drift sopra soglia** — outlier ordinati per |Δ|

## 2 · Distinguere drift INTENZIONALE da REGRESSIONE

Prima di rollback chiediti, in ordine:

1. **Ho cambiato la formula TPI consapevolmente?**
   → es. nuova ponderazione `centralita`, nuovo z-score cap.
   → guarda i commit dal baseline:
   `git log --oneline -- src/serie_a_scout/analytics parte1_analisi.py`
2. **È cambiato un provider o un mapping anagrafico?**
   → ingestion può aver inserito/rimosso partite. Controlla:
   `audit/reports/journal.jsonl` (ultime 24h) e
   `logs/quarantine/player_resolution.jsonl`.
3. **È cambiata la normalizzazione (z-score, SOS, k_ctx)?**
   → vedi `src/serie_a_scout/analytics/regression_diff.py::summarize_distribution_changes`.
4. **C'è un evento sportivo che giustifica il movimento?**
   → es. infortunio del top-1, transfer window: legittimo, vai a §5.

Se sì a 1 e il diff è **monotono** (Spearman alto, distribuzione scalata) →
è un cambio di scala intenzionale: aggiorna la baseline (§4).

Se Spearman è basso o ci sono molti rank-swap **disordinati** → è una
regressione vera, vai a §3.

## 3 · Debug

```bash
# 1) ricarica e re-verifica baseline
python -c "import sys; sys.path.insert(0,'src'); \
from serie_a_scout.snapshots import verify_snapshot; \
from pathlib import Path; \
vr=verify_snapshot(Path('snapshots/2025-26/giornata_36')); \
print(vr.ok, vr.errors)"

# 2) rigenera report locale con verbose
python tests/regression/run_regression.py \
    --baseline-season 2025-26 --baseline-giornata 36

# 3) isolane i 10 peggiori
python -c "import sys; sys.path.insert(0,'src'); sys.path.insert(0,'tests'); \
from regression.helpers import load_snapshot; \
from serie_a_scout.analytics import run_regression_diff, load_thresholds; \
b=load_snapshot('2025-26',36); \
d=run_regression_diff(b.tpi_table,b.tpi_table.copy(), season=b.season, giornata=b.giornata, dimension='totale', thresholds=load_thresholds()); \
[print(m) for m in d.biggest_fallers]"

# 4) confronta i parquet riga-per-riga con un secondo snapshot
python - <<'PY'
import pandas as pd
old = pd.read_parquet('snapshots/2025-26/giornata_36/giocatore_partita.parquet')
new = pd.read_parquet('snapshots/2025-26/giornata_36_candidate/giocatore_partita.parquet')
diff = old.merge(new, on='id', suffixes=('_old','_new'))
diff['xg_delta'] = (diff['xg_new'] - diff['xg_old']).abs()
print(diff.nlargest(20, 'xg_delta')[['id','xg_old','xg_new','xg_delta']])
PY
```

## 4 · Aggiornare la baseline (drift intenzionale accettato)

Il movimento è giustificato? Riscrivi la baseline:

```bash
# A) elimina (archiviato, non distrutto)
python snapshots/restore.py --snapshot snapshots/2025-26/giornata_36 --verify

# B) prendi un nuovo snapshot
python snapshots/take.py --season 2025-26 --giornata 36 --force

# C) commit dei nuovi sha256
git add snapshots/2025-26/giornata_36
git commit -m "regression: rebaseline TPI g36 — <motivo>"
```

> Regola d'oro: **mai rebaseline silenzioso**. Il messaggio di commit deve
> linkare il PR che ha generato il drift e l'analisi che lo accetta.

## 5 · Decision tree rollback

```
Severity CRITICAL?
├── No → niente da fare, il warning è informativo
└── Sì
    ├── Drift dovuto a refactor sul codice analytics?
    │     ├── intenzionale → rebaseline (§4)
    │     └── unintended  → revert PR + re-run regression
    ├── Drift dovuto a nuova ingestion?
    │     ├── provider OK, giornata nuova → nuova baseline su nuova giornata
    │     └── provider corrupt → snapshots/restore.py della giornata precedente
    └── Drift dovuto a normalizzazione?
          ├── parametri cambiati → rebaseline + nota in CHANGELOG
          └── bug nei z-score    → fix + re-run regression
```

## 6 · Verificare che il watchdog funzioni ancora

I test `test_engine_flags_*` in `tests/regression/test_tpi_stability.py`
perturbano artificialmente il candidate (inversione, scalatura, top-1
corruption). Se *quelli* falliscono, l'engine non sta più rilevando le
regressioni — la priorità è ripararlo prima di toccare il TPI.

```bash
python -m pytest tests/regression/test_tpi_stability.py::test_engine_flags_inverted_ranking -v
```

## 7 · Escalation

- Coinvolgi il responsabile analytics se: drift > 20 % sul mean,
  Spearman < 0.80, top-10 overlap < 5.
- Apri una issue con label `regression-failure` includendo il file
  Markdown del report come allegato.

## 8 · Riferimenti

- `src/serie_a_scout/analytics/regression_diff.py` — engine
- `tests/regression/test_tpi_stability.py` — suite
- `config/regression.yml` — soglie
- `.github/workflows/regression.yml` — CI
- `obs/metrics/regression.prom` — metriche
- `docs/runbooks/snapshot_restore.md` — rollback dati (F1.3)
