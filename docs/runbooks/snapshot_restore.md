# Runbook · Snapshot WORM — restore & disaster recovery

> Operating manual per il subsystem **WORM snapshot** introdotto in F1.3.
> Indirizzato a chiunque (operatore, on-call, SRE futuro) debba intervenire
> per ripristinare uno stato passato del DB.

## Concetti

- Uno **snapshot** è una cartella immutabile: `snapshots/<season>/giornata_<NN>/`.
- È **write-once**: lo stesso `(season, giornata)` non può essere ri-scritto a meno di `--force`, che archivia il precedente come `giornata_NN.backup.<short_run_id>`.
- Ogni file ha SHA-256 in `manifest.json`. Un verify positivo è prerequisito per qualsiasi restore.
- Il restore è **transazionale e distruttivo**: `TRUNCATE + INSERT` per ogni tabella scelta, dentro `engine.begin()`.

## Procedure standard

### A. Verifica integrità (sempre safe, read-only)

```bash
python snapshots/restore.py --snapshot snapshots/2025-26/giornata_36 --verify
```

Esce 0 se OK, 2 se checksum/size/file-extra non quadrano. Esegui sempre questo prima di toccare il DB.

### B. Dry-run del restore (legge parquet, non scrive)

```bash
python snapshots/restore.py --snapshot snapshots/2025-26/giornata_36 --dry-run
```

Stampa una riga per tabella con `rows_in_snapshot` e `DRY-RUN` come messaggio. Nessuna scrittura DB.

### C. Restore reale (distruttivo)

```bash
# 1. backup precauzionale (anche se hai lo snapshot, questo è il DB CORRENTE)
python set_up_tpi_pro/backup_db.py

# 2. verifica
python snapshots/restore.py --snapshot snapshots/2025-26/giornata_36 --verify

# 3. esegui
python snapshots/restore.py \
  --snapshot snapshots/2025-26/giornata_36 \
  --confirm "RESTORE"
```

Il token `--confirm "RESTORE"` è obbligatorio per evitare comandi accidentali. Subset di tabelle:

```bash
python snapshots/restore.py \
  --snapshot snapshots/2025-26/giornata_36 \
  --tables t_infortuni \
  --confirm "RESTORE"
```

### D. Rollback dopo restore sbagliato

`backup_db.py` lascia un file SQL in `backup/<timestamp>/serie_a_25_26_backup.sql`. Per ripristinare:

```bash
# Windows (PowerShell): in mancanza di mysql client si usa lo script python
python set_up_tpi_pro/recupera_partite_dal_backup.py \
  --backup backup/<timestamp>/serie_a_25_26_backup.sql
```

Oppure se `mysql` cli è installato:

```bash
mysql -u root -p serie_a_25_26 < backup/<timestamp>/serie_a_25_26_backup.sql
```

## Scenari di disastro

### S1. `manifest.json` mancante o JSON corrotto

`verify_snapshot()` ritorna `(False, ["manifest.json mancante" | "manifest.json corrotto: …"])`.

Azioni:
1. Lo snapshot è **non recuperabile** programmaticamente.
2. Controlla se è stato troncato (`ls -la`) — un manifest valido pesa ~1-2 KB.
3. Cerca uno snapshot precedente: `ls snapshots/<season>/`.
4. Recupera lo stato dalla `audit/reports/journal.jsonl` (entry `snapshot.take/completed` ha `manifest_run_id`).

### S2. Checksum mismatch (file modificato)

```
✗ Integrità fallita: ...
  - giocatori.parquet: sha256 mismatch (atteso a3f1…, trovato 9d77…)
```

Possibili cause: filesystem corrotto, edit manuale, antivirus.

Azioni:
1. **NON usare** lo snapshot per restore.
2. Recupera la backup-archive se esiste: `ls snapshots/<season>/giornata_NN.backup.*`.
3. Se nessuna backup-archive: snapshot perso.
4. Apri un incident in `journal.jsonl` con un `append_event("incident", "snapshot_corrupted", ...)`.

### S3. Snapshot esiste ma volevo creare un nuovo

`SnapshotExistsError` è il comportamento corretto. Opzioni:

1. **Volevi davvero rifare lo stesso `(season, giornata)`?** → `--force` (archivia il vecchio).
2. **Volevi un nome diverso?** → cambia `--giornata` o `--season`.

### S4. Restore fallito a metà transazione

`engine.begin()` rollbacka automaticamente. Il DB è nello stato pre-restore. Stato verificabile via `parte5_verifica.py`.

### S5. Riempimento disco

Ogni snapshot per Serie A 25/26 è ~1.2 MB compresso. 38 giornate × 5 stagioni ≈ 230 MB. Tollerabile.
Per ridurre: elimina manualmente le `*.backup.*` dopo aver verificato che non servono.

## Lifecycle consigliato

```
fine giornata di Serie A
    ↓
python parte4_aggiorna.py          # ingest + audit auto-hook
    ↓
python parte1_analisi.py           # analytics
    ↓
python parte2_dashboard.py         # serving
    ↓
python snapshots/take.py --giornata <NN>   # WORM
    ↓
journal.jsonl ← evento snapshot.take/completed
obs/metrics/snapshot.prom ← duration + rows
```

## Verifiche periodiche raccomandate

1. **Giornaliero**: `verify` sull'ultimo snapshot creato.
2. **Settimanale**: `verify` su TUTTI gli snapshot della stagione corrente (cron).
3. **Mensile**: dry-run restore su uno snapshot a campione (regression smoke test).

## Limiti noti

- Restore non gestisce schema migrations: se il DB target ha colonne diverse da quelle del manifest, `pd.to_sql(append)` fallirà. Per le migrazioni di schema, usa staging schema separato (`serie_a_restore`) e SWAP manuale.
- Le viste (`v_classifica` ecc.) NON sono snapshotted: sono ricalcolate al primo SELECT post-restore.
- Trigger di normalizzazione (`prevent.py`) RUNNANO durante il restore: i nomi vengono ri-normalizzati al re-INSERT. Idempotente per definizione.
