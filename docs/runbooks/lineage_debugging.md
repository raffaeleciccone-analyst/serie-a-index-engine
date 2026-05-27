# Runbook — Lineage debugging

> Come investigare run falliti, tracciare regressioni TPI, ricostruire la
> provenance di uno snapshot e fare disaster recovery con la
> consapevolezza del grafo. Riferimento: F1.2.

## 0 · Dove vive la lineage

* **DB:** `audit/lineage/lineage.db` (SQLite, WAL, schema versionato).
* **Schema:** `pipeline_run`, `pipeline_artifact`, `pipeline_artifact_input`.
* **Versione:** vedi `pipeline_schema_meta.schema_version`.
* **Codice:** `src/serie_a_scout/core/lineage_store.py` (CRUD),
  `src/serie_a_scout/core/run_context.py` (context manager).

> Deviazione dichiarata vs roadmap: il roadmap menziona uno schema
> `serie_a_meta` su MySQL. Lo *schema* è identico, ma persistiamo su
> SQLite per non legare la tracciabilità forense a un DB di produzione
> potenzialmente compromesso. La migrazione a MySQL è un `pg_dump`-style
> one-shot quando F2 lo richiederà.

## 1 · Generare il report

```bash
# rollup globale (default)
python snapshots/lineage_report.py

# chain di un run specifico
python snapshots/lineage_report.py --run-id <hex_run_id>

# anche JSON
python snapshots/lineage_report.py --run-id <hex> --json
```

Output in `reports/lineage/`:

* `latest_lineage_report.md` — rollup
* `lineage_<short>.md`       — per-run
* `lineage_<ts>.json`        — JSON dump (`--json`)

Sezioni: counters, tabelle runs/artifacts, **DAG Mermaid**, diagnostics.

## 2 · Triage di un run fallito

1. Trova il run nel report (o `sqlite3 audit/lineage/lineage.db "SELECT * FROM pipeline_run WHERE status='fail' ORDER BY started_at DESC LIMIT 5"`).
2. Apri `extra_json.error` per il traceback compatto (15 righe).
3. Risali al `parent_run_id` per capire la stage che lo aveva richiamato.
4. Cerca artifact prodotti: `sqlite3 ... "SELECT * FROM pipeline_artifact WHERE run_id='<rid>'"`.
5. Per ogni artifact, controlla i consumer downstream:
   `SELECT * FROM pipeline_artifact_input WHERE artifact_id=<aid>`.

Query utili (copia-incolla):

```sql
-- ultimi 20 run con durata e stato
SELECT substr(run_id,1,12) AS rid, stage, status, exit_code,
       started_at, duration_ms
  FROM pipeline_run
 ORDER BY started_at DESC LIMIT 20;

-- chain ancestor di un run
WITH RECURSIVE chain(run_id, parent_run_id, stage, status) AS (
  SELECT run_id, parent_run_id, stage, status
    FROM pipeline_run WHERE run_id = :rid
  UNION ALL
  SELECT r.run_id, r.parent_run_id, r.stage, r.status
    FROM pipeline_run r JOIN chain c ON c.parent_run_id = r.run_id
)
SELECT * FROM chain;

-- chi ha consumato un artifact
SELECT r.* FROM pipeline_run r
  JOIN pipeline_artifact_input i ON i.run_id = r.run_id
 WHERE i.artifact_id = :aid;
```

## 3 · Tracciare una regressione TPI

La suite F1.4 registra ogni esecuzione come `stage='regression'` e
linka *come input* lo snapshot baseline + candidate. Per capire **da
quale snapshot proviene** un report di regressione:

```bash
# 1) trova il run regression
python -c "import sys; sys.path.insert(0,'src'); \
from serie_a_scout.core import get_default_store; \
s=get_default_store(); \
[print(r.run_id, r.stage, r.status, r.started_at) \
 for r in s.iter_runs(stage='regression', limit=5)]"

# 2) inputs (snapshot consumati)
python -c "import sys; sys.path.insert(0,'src'); \
from serie_a_scout.core import get_default_store; \
s=get_default_store(); \
[print(a.kind, a.path, a.checksum[:12] if a.checksum else '-') \
 for a in s.inputs_for('<rid>')]"

# 3) regola d'oro: il run snapshot che ha prodotto quegli artifact è
#    nel parent_run_id (oppure cerchiamo per path):
python -c "import sys; sys.path.insert(0,'src'); \
from serie_a_scout.core import get_default_store; \
s=get_default_store(); \
[print(a.run_id, a.path) for a in s.find_artifacts(kind='snapshot')]"
```

## 4 · Provenance completo di uno snapshot

Per un path tipo `snapshots/2025-26/giornata_36/`:

```python
import sys; sys.path.insert(0, "src")
from serie_a_scout.core import get_default_store
from serie_a_scout.obs.lineage_export import collect_lineage, write_run_report

store = get_default_store()
art = store.find_artifacts(kind="snapshot", path_like="%giornata_36")[0]

# chi l'ha prodotto
producer = store.get_run(art.run_id)
print("Produttore:", producer.stage, producer.started_at, producer.exit_code)

# chi l'ha consumato (regression, restore, ecc.)
for cons in store.consumers_of(art.artifact_id):
    print("Consumer:", cons.stage, cons.run_id, cons.status)

# report visivo
write_run_report(producer.run_id, store=store)
```

## 5 · Diagnosi (orphans, missing files)

Il report markdown include una sezione "Diagnostics":

* **runs_still_running** — il processo è morto senza chiudere il
  contesto. Se è vecchio (> 1h), considera "fail" e indagalo:
  ```bash
  sqlite3 audit/lineage/lineage.db \
    "SELECT run_id, stage, started_at FROM pipeline_run \
     WHERE status='running' AND started_at < datetime('now','-1 hour')"
  ```
  Per chiuderlo manualmente (post-mortem):
  ```sql
  UPDATE pipeline_run
     SET status='fail', ended_at=datetime('now'),
         error_message='manual close after orphan detection'
   WHERE run_id='<rid>';
  ```

* **artifacts_missing_file** — il file dichiarato non c'è più su disco
  (snapshot ruotato, report cancellato). Decidere se ri-eseguire la
  stage o purgare la riga.

* **runs_without_artifacts** — un run completato che non ha registrato
  output: spesso un dry-run o un fallimento silenzioso prima della
  scrittura. Verifica `extra_json.events`.

## 6 · Disaster recovery lineage-aware

Sequenza tipica dopo una corruzione del DB di produzione:

1. **Identifica l'ultima snapshot OK**:
   ```sql
   SELECT a.path, a.created_at, r.status
     FROM pipeline_artifact a JOIN pipeline_run r ON r.run_id = a.run_id
    WHERE a.kind='snapshot' AND r.status='ok'
    ORDER BY a.created_at DESC LIMIT 5;
   ```
2. **Verifica integrità** (F1.3):
   ```bash
   python snapshots/restore.py --snapshot <path> --verify
   ```
3. **Restore** (richiede `--confirm "RESTORE"`). Il `RunContext` con
   `stage='snapshot_restore'` collegherà input → output, mantenendo la
   chain.
4. **Re-run regression** per validare il TPI dopo il restore.

## 7 · Backup del DB lineage

Il file SQLite è piccolo (KB). Backup atomico:

```powershell
sqlite3 audit/lineage/lineage.db ".backup audit/lineage/lineage.bak.db"
```

Da automatizzare via cron settimanale; lineage **è** la fonte forense
quando tutto il resto si rompe.

## 8 · Riferimenti

* `src/serie_a_scout/core/lineage_store.py` — schema + CRUD
* `src/serie_a_scout/core/run_context.py` — context manager
* `src/serie_a_scout/obs/lineage_export.py` — JSON + Mermaid
* `src/serie_a_scout/obs/lineage_metrics.py` — Prometheus
* `snapshots/lineage_report.py` — CLI
* `obs/metrics/lineage.prom` — gauges + counters
* `tests/regression/test_lineage_tracking.py` — 19 test
* `docs/runbooks/regression_failure.md` — sister runbook (F1.4)
