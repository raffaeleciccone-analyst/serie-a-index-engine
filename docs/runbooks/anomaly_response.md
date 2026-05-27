# Runbook — Anomaly response (F1.5)

> Quando `obs/metrics/anomaly.prom` segna `serie_a_pipeline_health_score < 80`,
> oppure il job CI `Run anomaly scan` fallisce con exit 2, oppure
> `reports/anomaly/anomaly_latest.md` mostra un'anomaly **CRITICAL**.

## 0 · Cosa fa F1.5

`AnomalyEngine` legge:

* il **lineage store** (`audit/lineage/lineage.db`)
* la **cartella snapshots/** (mtime + giornate)
* i **report regression** (`reports/regression/*.md`)
* i **quarantine log** (`logs/quarantine/*.jsonl`)

…calcola baseline rolling su `window_days` (default 14) e fa partire 8
detector:

| Type | Cosa intercetta |
|------|----------------|
| `duration_zscore_spike` | run troppo lunghi/corti vs baseline per stage |
| `run_failure_burst` | numero di fallimenti giornalieri sopra baseline |
| `stale_snapshot` | snapshot più vecchio della soglia oraria |
| `quarantine_growth` | quarantine jsonl cresciuta di N righe nel window |
| `regression_warning_rate` | quota run regression WARN/CRITICAL nel window |
| `regression_frequency_escalation` | quota in crescita fra prima e seconda metà del window |
| `tpi_distribution_drift` | shift assoluto nella tabella distribuzione del report regression |
| `missing_data` | meno run del previsto per una stage |

Ogni anomalia ha `severity ∈ {INFO, WARN, CRITICAL}`, `confidence ∈ [0,1]`,
`evidence` (JSON-safe), `suggested_action`, e i `run_ids` coinvolti.

Il **health score** ∈ [0,100] viene calcolato con i pesi in
`config/anomaly.yml::health_deduction`.

## 1 · Generare/leggere il report

```bash
python snapshots/anomaly_runner.py                # report + metrics
python snapshots/anomaly_runner.py --json         # anche JSON dump
python snapshots/anomaly_runner.py --fail-on-critical   # CI gate
python snapshots/anomaly_runner.py --report-only        # no metrics, no fail
python snapshots/anomaly_runner.py --stage regression   # subset
python snapshots/anomaly_runner.py --days-window 7      # window stretto
```

Output:

* `reports/anomaly/anomaly_latest.md` — **nome deterministico**, niente
  timestamp random (lo storico vive nel lineage)
* `reports/anomaly/anomaly_latest.json` — solo con `--json`
* `obs/metrics/anomaly.prom` — Prometheus textfile

## 2 · Triage per tipo

### `duration_zscore_spike`
1. Apri il report — c'è `run_ids[0]` (la run incriminata).
2. Recupera dettagli: `sqlite3 audit/lineage/lineage.db "SELECT * FROM pipeline_run WHERE run_id='<rid>'"`.
3. Causa tipica: DB lento, lock concorrente, provider in rate-limit.
4. Se la nuova durata è **giustificata** (più dati), va bene — il baseline si aggiusta nei prossimi 14 giorni.

### `stale_snapshot`
1. La stagione e l'ultima giornata sono in `evidence`.
2. `python snapshots/take.py --season <s> --giornata <N>` se la giornata è effettivamente nuova.
3. Se lo scheduler è giù, ripristina cron / orchestrator (F2).

### `quarantine_growth`
1. Drift mapping anagrafico: `python parte4_aggiorna.py --validate-only`.
2. Se il provider ha cambiato schema (FBref, TM), aggiorna i normalizer.
3. Pulire le righe vecchie dopo aver capito la causa:
   ```python
   from serie_a_scout.core.retention import prune_old_quarantine
   prune_old_quarantine(keep_days=60, dry_run=True)
   ```

### `regression_warning_rate` / `regression_frequency_escalation`
1. Apri il runbook F1.4: `docs/runbooks/regression_failure.md`.
2. Distinguere drift intenzionale (formula nuova) vs accidentale.
3. Se intenzionale → rebaseline; se no → revert PR.

### `tpi_distribution_drift`
1. L'evidence punta al report regression specifico.
2. Confronta mean/std/p95 fra il baseline e il candidate.
3. Spesso correlato a una nuova ingestion: `git log -- src/serie_a_scout/analytics`.

### `missing_data`
1. Quale stage? `evidence.stage`.
2. Quanti run osservati vs attesi? `evidence.observed_runs` / `expected_runs`.
3. Probabile scheduler down; per F2 sarà l'orchestrator a notificare per primo, F1.5 ora è la rete di sicurezza.

### `run_failure_burst`
1. `evidence.daily_counts` mostra il pattern.
2. `run_ids` carica gli ultimi 5 falliti — apri `extra.error.traceback_tail`.
3. Cause comuni: credenziali scadute, DB pieno, rate-limit del provider.

## 3 · Decision tree

```
Anomaly severity CRITICAL?
├── No → WARN/INFO: monitora, niente paging
└── Sì
    ├── type ∈ {duration_zscore_spike, run_failure_burst}
    │    └── apri logs del run, controlla deps esterne
    ├── type == stale_snapshot
    │    └── rilancia take.py o ripara scheduler
    ├── type == quarantine_growth
    │    └── analizza nuove righe, fix mapping / ER
    ├── type ∈ {regression_warning_rate, _frequency_escalation,
    │          tpi_distribution_drift}
    │    └── apri docs/runbooks/regression_failure.md
    └── type == missing_data
         └── scheduler down? cron? orchestrator?
```

## 4 · Tuning soglie

Tutte in `config/anomaly.yml`. Regole:

* alza la soglia DOPO che hai capito perché è scattata, non prima
* annota la motivazione nel commit message
* per ogni nuova stagione, considera un primo run con `--days-window 30` per evitare cold-start

## 5 · Retention (preview only, never auto-delete)

```python
from serie_a_scout.core.retention import (
    prune_old_lineage, prune_old_metrics, prune_old_quarantine,
)

# tutte ritornano un RetentionReport con `candidates` ma NON cancellano.
rep = prune_old_lineage(keep_days=90, dry_run=True)
for c in rep.candidates[:10]:
    print(c)

# Per cancellare davvero dopo review:
prune_old_lineage(keep_days=90, dry_run=False)
```

`prune_old_metrics` e `prune_old_quarantine` rifiutano path fuori dai
"safe roots" (`obs/metrics/`, `logs/quarantine/`, `audit/lineage/`) — non
si possono usare per cancellare snapshot o report.

## 6 · Integrazione CI

In `.github/workflows/regression.yml` lo step "Run anomaly scan"
fallisce solo con CRITICAL e solo se `--fail-on-critical` è attivo. Per
disattivare temporaneamente:

```yaml
- name: Run anomaly scan
  continue-on-error: true
```

## 7 · Riferimenti

* `src/serie_a_scout/analytics/anomaly_engine.py` — detectors
* `src/serie_a_scout/analytics/anomaly_models.py` — enums + dataclasses
* `src/serie_a_scout/analytics/anomaly_report.py` — renderer
* `src/serie_a_scout/obs/anomaly_metrics.py` — emitter
* `src/serie_a_scout/core/retention.py` — preview pruners
* `snapshots/anomaly_runner.py` — CLI
* `config/anomaly.yml` — soglie
* `tests/regression/test_anomaly_detection.py` — 32 test
* `docs/runbooks/regression_failure.md` — sister runbook (F1.4)
* `docs/runbooks/lineage_debugging.md` — sister runbook (F1.2)
