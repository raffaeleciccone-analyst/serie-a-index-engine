"""
Batteria di check di integrità per il DB `serie_a_25_26`.

Ogni funzione `check_xxx(report)` accetta un Report e aggiunge i Finding
rilevati. Le funzioni sono registrate in CHECKS in fondo al modulo.

Convenzione codici Finding:
  SCH-xxx  schema
  REF-xxx  integrità referenziale
  DUP-xxx  duplicati
  CON-xxx  consistenza statistiche
  TMP-xxx  temporali (date)
  NAM-xxx  naming
  UNU-xxx  inutilizzati
  PIP-xxx  pipeline
  MET-xxx  metriche anomale
"""
from __future__ import annotations
from collections import Counter, defaultdict
import datetime as dt

from .db import fetch_all, fetch_one, fetch_dict, cursor
from .findings import Report, Finding, Severity, Area
from .normalize import normalize_name


# ════════════════════════════════════════════════════════════════
# SCHEMA — Discovery
# ════════════════════════════════════════════════════════════════
def check_schema_overview(report: Report) -> None:
    """Discovery: lista tabelle, righe, viste. Nessun Finding salvo eccezioni."""
    rows = fetch_all("""
        SELECT TABLE_NAME, TABLE_TYPE, TABLE_ROWS, ENGINE
        FROM information_schema.TABLES
        WHERE TABLE_SCHEMA = DATABASE()
        ORDER BY TABLE_NAME
    """)
    tables = [r[0] for r in rows if r[1] == "BASE TABLE"]
    views = [r[0] for r in rows if r[1] == "VIEW"]
    report.tables_inspected = tables
    report.summary["base_tables"] = tables
    report.summary["views"] = views
    report.summary["table_stats"] = {r[0]: {"type": r[1], "rows_approx": r[2], "engine": r[3]} for r in rows}


def check_missing_unique_keys(report: Report) -> None:
    """REF-001: tabelle critiche prive di UNIQUE KEY su chiavi naturali."""
    expected_uniques = {
        "giocatori": [("nome", "squadra_id"), ("nome",)],   # accetta uno dei due
        "squadre": [("nome",)],
        "calendario": [("game_id_understat",), ("data", "squadra_casa_id", "squadra_trasferta_id")],
        "giocatore_partita": [("giocatore_id", "calendario_id")],
    }
    rows = fetch_all("""
        SELECT TABLE_NAME, INDEX_NAME, GROUP_CONCAT(COLUMN_NAME ORDER BY SEQ_IN_INDEX)
        FROM information_schema.STATISTICS
        WHERE TABLE_SCHEMA = DATABASE() AND NON_UNIQUE = 0
        GROUP BY TABLE_NAME, INDEX_NAME
    """)
    have_uniques: dict[str, list[tuple]] = defaultdict(list)
    for t, idx, cols_str in rows:
        cols = tuple((cols_str or "").split(","))
        have_uniques[t].append(cols)

    for tbl, candidates in expected_uniques.items():
        present = have_uniques.get(tbl, [])
        if not any(set(c) <= set(sum(present, ())) for c in candidates):
            report.add(Finding(
                code="REF-001", area=Area.REFERENTIAL, severity=Severity.HIGH,
                title=f"UNIQUE KEY mancante su `{tbl}`",
                table=tbl,
                description=f"Nessuna UNIQUE su {candidates}. Indici unici trovati: {present}",
                root_cause="Schema iniziale senza vincoli; ogni INSERT crea duplicati silenziosamente.",
                fix_available=True,
                fix_strategy=f"ALTER TABLE {tbl} ADD UNIQUE KEY uq_... ({candidates[0]})",
            ))


def check_orphan_foreign_keys(report: Report) -> None:
    """REF-002..N: righe con FK che puntano a parent inesistenti."""
    fk_rows = fetch_all("""
        SELECT TABLE_NAME, COLUMN_NAME, REFERENCED_TABLE_NAME, REFERENCED_COLUMN_NAME, CONSTRAINT_NAME
        FROM information_schema.KEY_COLUMN_USAGE
        WHERE TABLE_SCHEMA = DATABASE() AND REFERENCED_TABLE_NAME IS NOT NULL
    """)
    for child_tbl, child_col, parent_tbl, parent_col, cname in fk_rows:
        n = fetch_one(f"""
            SELECT COUNT(*) FROM `{child_tbl}` c
            LEFT JOIN `{parent_tbl}` p ON p.`{parent_col}` = c.`{child_col}`
            WHERE c.`{child_col}` IS NOT NULL AND p.`{parent_col}` IS NULL
        """)[0]
        if n > 0:
            report.add(Finding(
                code=f"REF-002:{cname}", area=Area.REFERENTIAL, severity=Severity.HIGH,
                title=f"Orfani FK `{child_tbl}.{child_col}` → `{parent_tbl}.{parent_col}`",
                table=child_tbl, rows_affected=n,
                description=f"{n} righe in {child_tbl} hanno {child_col} non NULL ma parent assente.",
                root_cause="FK senza ON DELETE CASCADE o cancellazione manuale del parent.",
                fix_available=True,
                fix_strategy=f"DELETE FROM {child_tbl} WHERE {child_col} NOT IN (SELECT {parent_col} FROM {parent_tbl})",
            ))


def check_id_orphans_no_fk(report: Report) -> None:
    """REF-003: tabelle con giocatore_id ma SENZA FK formale (es. t_player_analytics)."""
    candidates = [
        ("t_player_analytics", "giocatore_id"),
        ("t_player_game_log", "giocatore_id"),
    ]
    for tbl, col in candidates:
        # esiste FK?
        n_fk = fetch_one("""
            SELECT COUNT(*) FROM information_schema.KEY_COLUMN_USAGE
            WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s AND COLUMN_NAME=%s
                  AND REFERENCED_TABLE_NAME IS NOT NULL
        """, (tbl, col))[0]
        if n_fk == 0:
            n_orph = fetch_one(f"""
                SELECT COUNT(*) FROM `{tbl}` t
                LEFT JOIN giocatori g ON g.id = t.{col}
                WHERE t.{col} IS NOT NULL AND g.id IS NULL
            """)[0]
            sev = Severity.HIGH if n_orph > 0 else Severity.MEDIUM
            report.add(Finding(
                code="REF-003", area=Area.REFERENTIAL, severity=sev,
                title=f"`{tbl}.{col}` senza FK + orfani={n_orph}",
                table=tbl, rows_affected=n_orph,
                description=f"Tabella analytics senza vincolo FK su giocatori. {n_orph} righe puntano a id inesistenti.",
                root_cause="t_player_analytics importata da dump esterno senza FK; può divergere dal master.",
                fix_available=True,
                fix_strategy=f"DELETE righe orfane + ALTER TABLE {tbl} ADD CONSTRAINT FOREIGN KEY ({col}) REFERENCES giocatori(id) ON DELETE CASCADE",
            ))


# ════════════════════════════════════════════════════════════════
# DUPLICATI
# ════════════════════════════════════════════════════════════════
def check_duplicates_by_normalized_name(report: Report) -> None:
    """DUP-001: giocatori con nome canonicamente equivalente nella STESSA squadra."""
    rows = fetch_all("""
        SELECT id, nome, COALESCE(cognome,''), squadra_id, minuti, partite
        FROM giocatori
    """)
    groups: dict[tuple[str, int], list[tuple]] = defaultdict(list)
    for r in rows:
        gid, nome, cog, sq, minuti, partite = r
        # Considera sia "Nome" che "Nome Cognome"
        key1 = (normalize_name(nome), sq)
        key2 = (normalize_name(f"{nome} {cog}"), sq) if cog else None
        groups[key1].append(r)
        if key2 and key2 != key1:
            groups[key2].append(r)
    samples = []
    affected = 0
    for k, ids in groups.items():
        unique_ids = {x[0] for x in ids}
        if len(unique_ids) > 1:
            affected += len(unique_ids) - 1
            if len(samples) < 5:
                samples.append({"key": k[0], "squadra_id": k[1],
                                "ids": [{"id": x[0], "nome": x[1], "cog": x[2],
                                         "minuti": x[4], "partite": x[5]} for x in ids[:4]]})
    if affected:
        report.add(Finding(
            code="DUP-001", area=Area.DUPLICATES, severity=Severity.HIGH,
            title=f"{affected} giocatori sono duplicati canonici di altri",
            table="giocatori", rows_affected=affected,
            description="Esistono giocatori con stesso nome (normalizzato) e stessa squadra ma id diverso, oppure con varianti nome/(nome+cognome).",
            root_cause="Mancanza di chiave canonica + parte4 inseriva 'Nome' separato da 'Cognome' in run vecchi, poi 'Nome Cognome' uniti.",
            fix_available=True,
            fix_strategy="Merge tramite fingerprint canonico + UPDATE giocatore_id satellite + DELETE ghost.",
            samples=samples,
        ))


def check_duplicates_t_player_analytics(report: Report) -> None:
    """DUP-002: stesso giocatore_id presente più volte in t_player_analytics."""
    n = fetch_one("""
        SELECT COUNT(*) FROM (
            SELECT giocatore_id, COUNT(*) c FROM t_player_analytics
            GROUP BY giocatore_id HAVING c > 1
        ) sub
    """)[0]
    if n > 0:
        report.add(Finding(
            code="DUP-002", area=Area.DUPLICATES, severity=Severity.MEDIUM,
            title=f"{n} giocatori con più righe in t_player_analytics",
            table="t_player_analytics", rows_affected=n,
            description="Per giocatore_id ci dovrebbe essere 1 sola riga aggregata.",
            root_cause="Tabella popolata da dump esterno senza UNIQUE KEY su giocatore_id.",
            fix_available=True,
            fix_strategy="Per ogni gid duplicato: tieni la riga con MAX(minuti) e DELETE le altre. Aggiungi UNIQUE KEY.",
        ))


def check_invisible_unicode(report: Report) -> None:
    """
    NAM-001: nomi con caratteri davvero invisibili/problematici.
    Versione robusta: filtra in Python invece che in MySQL REGEXP.
    """
    import unicodedata as _u
    import re as _re

    INVISIBLE = {
        " ", "­",
        "​", "‌", "‍", "‎", "‏",
        "⁠", "﻿",
    }
    multi_ws = _re.compile(r"\s{2,}")

    def reason(s):
        if s is None:
            return None
        bad = [c for c in s if c in INVISIBLE]
        if bad:
            return "invisible:" + ",".join(hex(ord(c)) for c in bad)
        if s != s.strip():
            return "trim"
        if multi_ws.search(s):
            return "multi_space"
        for c in s:
            if _u.category(c).startswith("C"):
                return "control:" + hex(ord(c))
        return None

    rows = fetch_all("SELECT id, nome FROM giocatori")
    bad = [(gid, n, reason(n)) for gid, n in rows if reason(n)]
    if bad:
        report.add(Finding(
            code="NAM-001", area=Area.NAMING, severity=Severity.MEDIUM,
            title=f"{len(bad)} nomi con caratteri invisibili/anomali",
            table="giocatori", rows_affected=len(bad),
            description="Nomi con NBSP/SHY/zero-width/control/multi-space.",
            root_cause="Copia/incolla da fonti esterne o encoding misto.",
            fix_available=True,
            fix_strategy="Esegui `python audit/fix.py --only NAM-001`.",
            samples=[{"id": gid, "nome": n, "reason": r,
                      "hex": n.encode("utf-8")[:32].hex()} for gid, n, r in bad[:5]],
        ))


def check_minutes_consistency(report: Report) -> None:
    """CON-001: giocatori.minuti != SUM(giocatore_partita.minuti)."""
    rows = fetch_all("""
        SELECT g.id, g.nome, g.minuti AS minuti_agg, COALESCE(SUM(gp.minuti),0) AS minuti_sum
        FROM giocatori g
        LEFT JOIN giocatore_partita gp ON gp.giocatore_id = g.id
        GROUP BY g.id, g.nome, g.minuti
        HAVING ABS(g.minuti - COALESCE(SUM(gp.minuti),0)) > 30
    """)
    if rows:
        report.add(Finding(
            code="CON-001", area=Area.CONSISTENCY, severity=Severity.HIGH,
            title=f"{len(rows)} giocatori con minuti aggregati != SUM(partite)",
            table="giocatori", rows_affected=len(rows),
            description="Differenza > 30' tra `giocatori.minuti` e `SUM(giocatore_partita.minuti)`.",
            root_cause="`giocatori.minuti` aggiornato da Understat read_player_season_stats; dopo dedup non più allineato a giocatore_partita.",
            fix_available=True,
            fix_strategy="UPDATE giocatori g JOIN (SELECT giocatore_id, SUM(minuti) m FROM giocatore_partita GROUP BY giocatore_id) s ON s.giocatore_id=g.id SET g.minuti=s.m;",
            samples=[{"id": r[0], "nome": r[1], "minuti_agg": int(r[2]), "minuti_sum": int(r[3]),
                      "diff": int(r[2] - r[3])} for r in rows[:5]],
        ))


def check_partite_field_zero(report: Report) -> None:
    """CON-002: `giocatori.partite` = 0 ma esistono righe in giocatore_partita."""
    n = fetch_one("""
        SELECT COUNT(*) FROM giocatori g
        WHERE g.partite = 0
          AND EXISTS (SELECT 1 FROM giocatore_partita gp WHERE gp.giocatore_id=g.id AND gp.minuti>0)
    """)[0]
    if n > 0:
        report.add(Finding(
            code="CON-002", area=Area.CONSISTENCY, severity=Severity.MEDIUM,
            title=f"{n} giocatori con `partite=0` ma con partite registrate",
            table="giocatori", rows_affected=n,
            description="Il campo `partite` non viene mai aggiornato da parte4_aggiorna.py (Understat non lo restituisce o la colonna mappata è sbagliata).",
            root_cause="In parte4 il campo `partite` viene letto da `games`/`MP` di Understat che spesso è NULL; defaulta a 0 e non viene mai ricalcolato.",
            fix_available=True,
            fix_strategy="UPDATE giocatori g SET g.partite=(SELECT COUNT(*) FROM giocatore_partita gp WHERE gp.giocatore_id=g.id AND gp.minuti>0).",
        ))


def check_xg_consistency(report: Report) -> None:
    """CON-003: giocatori.xg vs SUM(giocatore_partita.xg)."""
    rows = fetch_all("""
        SELECT g.id, g.nome, g.xg AS xg_agg, COALESCE(SUM(gp.xg),0) AS xg_sum
        FROM giocatori g
        LEFT JOIN giocatore_partita gp ON gp.giocatore_id = g.id
        GROUP BY g.id, g.nome, g.xg
        HAVING ABS(g.xg - COALESCE(SUM(gp.xg),0)) > 0.5
    """)
    if rows:
        report.add(Finding(
            code="CON-003", area=Area.CONSISTENCY, severity=Severity.MEDIUM,
            title=f"{len(rows)} giocatori con xG aggregato != SUM partite",
            table="giocatori", rows_affected=len(rows),
            description="xG aggregato (Understat season_stats) non corrisponde alla somma delle singole partite.",
            root_cause="Stesso problema dei minuti dopo dedup; oppure Understat usa modelli leggermente diversi tra season e per-match.",
            fix_available=True,
            fix_strategy="UPDATE da SUM(gp.xg) se difference > soglia.",
            samples=[{"id": r[0], "nome": r[1], "xg_agg": float(r[2] or 0), "xg_sum": float(r[3] or 0)} for r in rows[:5]],
        ))


def check_calendario_giornate(report: Report) -> None:
    """CON-004: una giornata Serie A deve avere esattamente 10 partite."""
    rows = fetch_all("""
        SELECT giornata, COUNT(*) c FROM calendario
        GROUP BY giornata HAVING c != 10
    """)
    if rows:
        report.add(Finding(
            code="CON-004", area=Area.CONSISTENCY, severity=Severity.MEDIUM,
            title=f"{len(rows)} giornate con != 10 partite",
            table="calendario", rows_affected=len(rows),
            description="Una giornata di Serie A deve avere 10 partite. Anomalie suggeriscono raggruppamento errato (turni infrasettimanali fusi).",
            root_cause="In versioni precedenti parte4 raggruppava per finestra 7gg → confondeva turni vicini.",
            fix_available=True,
            fix_strategy="Esegui parte6_fix_giornate.py o l'algoritmo `(index // 10) + 1` su partite ordinate per data.",
            samples=[{"giornata": r[0], "partite": r[1]} for r in rows[:10]],
        ))


# ════════════════════════════════════════════════════════════════
# TEMPORALI
# ════════════════════════════════════════════════════════════════
def check_temporal_anomalies(report: Report) -> None:
    """TMP-001..003: date anomale."""
    # 1) partite con data nel futuro
    n_future = fetch_one("SELECT COUNT(*) FROM calendario WHERE data > NOW() + INTERVAL 1 DAY")[0]
    if n_future:
        report.add(Finding(
            code="TMP-001", area=Area.TEMPORAL, severity=Severity.LOW,
            title=f"{n_future} partite in calendario con data futura",
            table="calendario", rows_affected=n_future,
            description="Partite ancora da disputare (normali se il fixture è preloadato).",
            root_cause="Understat o fixture pre-loaded.",
        ))

    # 2) infortuni con data_rientro < data_inizio
    n_bad = fetch_one("""
        SELECT COUNT(*) FROM t_infortuni
        WHERE data_rientro IS NOT NULL AND data_rientro < data_inizio
    """)[0]
    if n_bad:
        report.add(Finding(
            code="TMP-002", area=Area.TEMPORAL, severity=Severity.HIGH,
            title=f"{n_bad} infortuni con data_rientro < data_inizio",
            table="t_infortuni", rows_affected=n_bad,
            description="Anomalia logica: il rientro non può precedere l'inizio.",
            root_cause="Errore di inserimento manuale o scraping.",
            fix_available=True,
            fix_strategy="Swap delle due date o cancellazione manuale.",
        ))

    # 3) infortuni out-of-season (data_inizio < 1 ago 2025 o > oggi+30)
    n_oos = fetch_one("""
        SELECT COUNT(*) FROM t_infortuni
        WHERE data_inizio < '2025-07-01' OR data_inizio > DATE_ADD(CURDATE(), INTERVAL 30 DAY)
    """)[0]
    if n_oos:
        report.add(Finding(
            code="TMP-003", area=Area.TEMPORAL, severity=Severity.LOW,
            title=f"{n_oos} infortuni fuori range stagionale",
            table="t_infortuni", rows_affected=n_oos,
            description="data_inizio fuori da [2025-07-01, oggi+30gg].",
            root_cause="Dati storici di stagioni precedenti.",
        ))


# ════════════════════════════════════════════════════════════════
# NULL & ANAGRAFICHE
# ════════════════════════════════════════════════════════════════
def check_missing_birthdates(report: Report) -> None:
    """NAM-002: giocatori titolari (>500 min) senza data_nascita."""
    n = fetch_one("""
        SELECT COUNT(*) FROM giocatori
        WHERE minuti >= 500 AND data_nascita IS NULL
    """)[0]
    if n:
        report.add(Finding(
            code="NAM-002", area=Area.NAMING, severity=Severity.MEDIUM,
            title=f"{n} titolari senza data_nascita",
            table="giocatori", rows_affected=n,
            description="AII (Age Index) non calcolabile per questi giocatori → punteggio neutro fallback.",
            root_cause="popola_anagrafica.py non li ha trovati su Transfermarkt (nomi raro, omonimi, redirect).",
            fix_available=True,
            fix_strategy="Rilancia `python set_up_tpi_pro/popola_anagrafica.py` o popola manualmente via `sql/fix_anagrafica_manuali.sql`.",
        ))


def check_missing_ruolo(report: Report) -> None:
    """NAM-003: giocatori qualificati senza ruolo."""
    n = fetch_one("""
        SELECT COUNT(*) FROM giocatori
        WHERE minuti >= 500 AND (ruolo IS NULL OR ruolo = '')
    """)[0]
    if n:
        report.add(Finding(
            code="NAM-003", area=Area.NAMING, severity=Severity.HIGH,
            title=f"{n} titolari senza ruolo (POR/DIF/CEN/ATT)",
            table="giocatori", rows_affected=n,
            description="Senza ruolo, z-score relativi alla popolazione di riferimento (ATT+CEN) sono falsati.",
            root_cause="Ruolo non scritto da parte4 (Understat non lo fornisce in modo standard) e ruolo_override.py non copre tutti.",
            fix_available=True,
            fix_strategy="Mapping automatico da `posizione` Understat → ruolo: GK→POR, DEF→DIF, MID→CEN, FW→ATT.",
        ))


# ════════════════════════════════════════════════════════════════
# UNUSED / DEAD DATA
# ════════════════════════════════════════════════════════════════
def check_unused_tables(report: Report) -> None:
    """UNU-001: tabelle vuote o quasi (potenzialmente morte)."""
    rows = fetch_all("""
        SELECT TABLE_NAME, TABLE_ROWS
        FROM information_schema.TABLES
        WHERE TABLE_SCHEMA = DATABASE() AND TABLE_TYPE = 'BASE TABLE'
              AND (TABLE_ROWS IS NULL OR TABLE_ROWS < 5)
    """)
    if rows:
        report.add(Finding(
            code="UNU-001", area=Area.UNUSED, severity=Severity.INFO,
            title=f"{len(rows)} tabelle vuote o quasi",
            description="Possibili tabelle di esperimento o feature non finita.",
            samples=[{"table": r[0], "rows": r[1]} for r in rows],
        ))


def check_orphan_players(report: Report) -> None:
    """UNU-002: giocatori senza nessuna partita (mai usati)."""
    n = fetch_one("""
        SELECT COUNT(*) FROM giocatori g
        WHERE NOT EXISTS (SELECT 1 FROM giocatore_partita gp WHERE gp.giocatore_id=g.id)
    """)[0]
    if n:
        report.add(Finding(
            code="UNU-002", area=Area.UNUSED, severity=Severity.LOW,
            title=f"{n} giocatori senza alcuna partita",
            table="giocatori", rows_affected=n,
            description="Ingombrano la tabella ma non contribuiscono ad alcuna metrica.",
            root_cause="Acquisti recenti, infortunati a inizio stagione o residui di import precedenti.",
            fix_available=True,
            fix_strategy="DELETE solo dopo verifica che non siano nel calciomercato attuale.",
        ))


# ════════════════════════════════════════════════════════════════
# PIPELINE & METRICS
# ════════════════════════════════════════════════════════════════
def check_understat_id_uniqueness(report: Report) -> None:
    """PIP-001: game_id_understat duplicati in calendario."""
    rows = fetch_all("""
        SELECT game_id_understat, COUNT(*) c FROM calendario
        WHERE game_id_understat IS NOT NULL
        GROUP BY game_id_understat HAVING c > 1
    """)
    if rows:
        report.add(Finding(
            code="PIP-001", area=Area.PIPELINE, severity=Severity.HIGH,
            title=f"{len(rows)} game_id_understat duplicati",
            table="calendario", rows_affected=sum(r[1] for r in rows),
            description="Lo stesso match Understat è inserito più volte in calendario.",
            root_cause="parte4 senza UNIQUE su game_id_understat; ogni run inseriva nuova riga.",
            fix_available=True,
            fix_strategy="Tieni MIN(id), cancella le altre + ALTER TABLE calendario ADD UNIQUE (game_id_understat).",
        ))


def check_extreme_metrics(report: Report) -> None:
    """MET-001: KPI fuori range plausibile (sentinella anti-corruzione)."""
    suspects = []
    # xG p90 > 2.5 oppure < 0
    rows = fetch_all("""
        SELECT id, nome, minuti, xg, ROUND(xg/(minuti/90.0), 3) AS xg_p90
        FROM giocatori WHERE minuti > 500
          AND (xg < 0 OR xg/(minuti/90.0) > 2.5)
    """)
    suspects.extend({"id": r[0], "nome": r[1], "xg_p90": float(r[4] or 0)} for r in rows)

    if suspects:
        report.add(Finding(
            code="MET-001", area=Area.METRIC, severity=Severity.MEDIUM,
            title=f"{len(suspects)} giocatori con xG/90 fuori range plausibile",
            table="giocatori", rows_affected=len(suspects),
            description="xG/90 attesi in [0, 1.5]. Valori >2.5 o negativi indicano corruzione.",
            root_cause="Probabile somma duplicata o overflow durante INSERT.",
            samples=suspects[:5],
        ))


def check_credentials_in_code(report: Report) -> None:
    """SEC-001: password / credenziali hardcoded nei file di progetto.
    V4 fix: rimosso il salto di 'backup' (i backup contenevano i secret),
    pattern ampliato per beccare anche forma URL `mysql://user:pwd@host`,
    e file estensioni allargate a config/dotenv.
    """
    import os
    import re

    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    # Skip solo VCS / cache / generati — NON skippare cartelle 'backup'
    SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv", "dashboard_output", "debug_html"}
    SCAN_EXT = (".py", ".ini", ".yaml", ".yml", ".toml", ".json", ".env")

    # Esclusi: { } $ < > per evitare false positives su f-string Python
    # ({DB_PASSWORD}), env-var ($PWD), e placeholder docstring (<user>:<pass>).
    EXCL = r"'\"\s{}$<>"
    patterns = [
        re.compile(rf"(?i)password\s*[:=]\s*['\"][^{EXCL}]{{4,}}['\"]"),
        re.compile(rf"(?i)mysql\+?\w*://[^:/{EXCL}]+:[^@/{EXCL}]{{4,}}@"),
        re.compile(rf"(?i)\bDB_PASSWORD\s*=\s*['\"][^{EXCL}]{{4,}}['\"]"),
        re.compile(rf"(?i)\bsecret(_key)?\s*[:=]\s*['\"][^{EXCL}]{{8,}}['\"]"),
        re.compile(rf"(?i)\bapi[-_]?key\s*[:=]\s*['\"][^{EXCL}]{{8,}}['\"]"),
    ]
    # Whitelist: l'_unico_ file dove ci si aspetta DB_PASSWORD=valore è .env (gitignored)
    whitelist_paths = {os.path.join(root, ".env")}

    hits: list[str] = []
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for f in files:
            if not f.endswith(SCAN_EXT):
                continue
            p = os.path.join(dirpath, f)
            if p in whitelist_paths:
                continue
            try:
                txt = open(p, encoding="utf-8", errors="ignore").read()
            except Exception:
                continue
            for pat in patterns:
                if pat.search(txt):
                    hits.append(os.path.relpath(p, root))
                    break

    if hits:
        report.add(Finding(
            code="SEC-001", area=Area.SECURITY, severity=Severity.HIGH,
            title=f"Possibili credenziali hardcoded in {len(hits)} file",
            description="Credenziali in chiaro — leak immediato se il repo va su Git pubblico.",
            root_cause="Mancata adozione di config.py + .env per i secret.",
            fix_available=True,
            fix_strategy="Sostituire con `from config import DB_PASSWORD` (config.py fail-fast su .env).",
            samples=[{"file": h} for h in hits[:10]],
        ))


# ════════════════════════════════════════════════════════════════
# REGISTRY
# ════════════════════════════════════════════════════════════════
# Importa i plausibility check (engine MET-001..010, file dedicato)
from .checks_metric import METRIC_CHECKS

CHECKS = [
    check_schema_overview,
    check_missing_unique_keys,
    check_orphan_foreign_keys,
    check_id_orphans_no_fk,
    check_duplicates_by_normalized_name,
    check_duplicates_t_player_analytics,
    check_invisible_unicode,
    check_minutes_consistency,
    check_partite_field_zero,
    check_xg_consistency,
    check_calendario_giornate,
    check_temporal_anomalies,
    check_missing_birthdates,
    check_missing_ruolo,
    check_unused_tables,
    check_orphan_players,
    check_understat_id_uniqueness,
    # check_extreme_metrics deprecato: sostituito dai METRIC_CHECKS dedicati
    check_credentials_in_code,
    *METRIC_CHECKS,
]
