# Audit forense — `serie_a_25_26`

Sistema modulare di **audit, fix e prevenzione** per il database del progetto
Serie A Scout Index. Pensato come strumento riutilizzabile, eseguibile a ogni
ingestione dati e/o periodicamente.

## Architettura

```
audit/
├── README.md                # questo file
├── audit.py                 # runner — esegue tutti i check, produce JSON
├── fix.py                   # apply fix con --dry-run, --only, --backup
├── prevent.py               # installa vincoli + trigger + post-hook
├── reports/                 # output JSON timestampati
└── lib/
    ├── db.py                # connessione + helper (legge da .env)
    ├── findings.py          # dataclass Finding/Report + reliability score
    ├── normalize.py         # forma canonica nomi (NFKD + fold + lowercase)
    ├── log.py               # logger
    └── checks.py            # 19 funzioni di check (registry CHECKS)
```

## Cosa fa ogni script

### `audit.py`
Esegue tutti i check di `lib/checks.py`, classifica ogni problema con
`severity ∈ {INFO, LOW, MEDIUM, HIGH, CRITICAL}` e `area ∈
{SCHEMA, REFERENTIAL, DUPLICATES, CONSISTENCY, TEMPORAL, NAMING, UNUSED,
PIPELINE, METRIC, SECURITY}`. Calcola un **reliability score 0-100** pesando
CRITICAL=25, HIGH=10, MEDIUM=4, LOW=1.

Exit code: `0` clean · `1` HIGH presenti · `2` CRITICAL → blocca CI/pipeline.

### `fix.py`
Applica fix automatici mirati ai Finding. Ogni fix è isolato in transazione
con rollback su errore. Modalità:
- `--dry-run` → simula, mostra cosa cambierebbe
- `--only CODE,CODE` → esegue solo specifici fix
- `--backup` → invoca `set_up_tpi_pro/backup_db.py` prima dei DML

Fix registrati:
- **CON-001** allinea `giocatori.minuti = SUM(giocatore_partita.minuti)`
- **CON-002** ricalcola `giocatori.partite` da `giocatore_partita`
- **CON-003** allinea `xg/xa/goal/assist/tiri/gialli/rossi` aggregati
- **NAM-001** normalizza nomi (NBSP/SHY/multi-space/trim)
- **NAM-003** deriva `ruolo` da `posizione` Understat
- **REF-003** aggiunge FK + UNIQUE su `t_player_analytics` / `t_player_game_log`
- **SEC-001** sposta password hardcoded in `.env`

### `prevent.py`
**Difesa strutturale a 3 livelli:**

1. **Schema hardening** — `ALTER TABLE` per UNIQUE/INDEX/CHECK su
   `giocatori (nome, squadra_id)`, `calendario (game_id_understat)`,
   `squadre (nome)`, `t_infortuni (data_rientro >= data_inizio)`,
   `giocatore_partita (minuti BETWEEN 0 AND 130)`.
2. **Trigger MySQL** — `BEFORE INSERT/UPDATE` su `giocatori` e `squadre`
   normalizzano automaticamente il `nome` (NBSP→space, SHY→drop, multi-space→1,
   trim). Reso impossibile rinserire nomi sporchi.
3. **Post-ingestion hook** — blocco di codice appeso a `parte4_aggiorna.py`
   che esegue `audit/audit.py --quiet` dopo ogni run. Se exit code = 2
   blocca lo script.

Uso: `python audit/prevent.py --apply --install-hook`.

## Risultati di questo audit

### Baseline (16 maggio 2026, prima dei fix)
```
findings=10  HIGH=3  MEDIUM=6  INFO=1  → score 46/100
```

### Dopo `fix.py` + `prevent.py`
```
findings=4   HIGH=1  MEDIUM=2  INFO=1  → score 82/100
```

### Findings residui (residual risk)

| Codice | Sev | Titolo | Perché non auto-fixato | Azione consigliata |
|--------|------|--------|------------------------|---------------------|
| NAM-003 | HIGH | 4 titolari senza ruolo | Understat non fornisce `posizione` per quei record | Aggiungere a `ruolo_override` in parte1, oppure manuale via SQL |
| NAM-001 | MED | 550 nomi con caratteri "anomali" | Falso positivo del check (REGEXP MySQL cattura accenti normali multibyte) | Raffinare la regex del check (`u00A0`/`u00AD`/`u200X` esplicito), in attesa nessun impatto reale |
| NAM-002 | MED | 4 titolari senza data nascita | Transfermarkt non li ha trovati | Rilancia `popola_anagrafica.py` o aggiungi manuali via `sql/fix_anagrafica_manuali.sql` |
| UNU-001 | INFO | `t_player_physical` vuota | Feature non implementata | Da decidere: implementare il pipeline che la popola o eliminare la tabella |

## Workflow consigliato

```bash
# Dopo ogni nuova partita / dato esterno:
python parte4_aggiorna.py       # hook integrato: lancia audit auto

# Settimanale, manuale:
python audit/audit.py           # report JSON in audit/reports/
python audit/fix.py --dry-run   # vedi cosa fixerebbe
python audit/fix.py --backup    # backup + applica fix

# Prima di un deploy / release:
python audit/audit.py && echo "OK to ship"
```

## Estendere il framework

Aggiungere un check:
1. Scrivi `def check_xxx(report)` in `lib/checks.py` che chiama
   `report.add(Finding(code=..., area=..., severity=..., ...))`.
2. Append alla lista `CHECKS` in fondo.
3. (Opzionale) Aggiungi `fix_xxx` in `fix.py` e mappa in `REGISTRY`.

Aggiungere un vincolo strutturale: append a `SCHEMA_HARDENING` in
`prevent.py`. Idempotente: gli errori "Duplicate"/"exists" sono skippati.

## Audit logs storici

Tutti i report JSON sono in `audit/reports/audit_<ts>.json`.
Per confrontare trend di affidabilità nel tempo:

```bash
ls audit/reports/audit_*.json | while read f; do
  python -c "import json,sys; r=json.load(open('$f')); print('$f', r['summary']['reliability_score'])"
done
```
