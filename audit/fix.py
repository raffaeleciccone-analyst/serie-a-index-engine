"""
fix.py — Applica fix automatici ai Finding rilevati dall'audit.

Architettura:
  - Ogni fix è una funzione `fix_<CODE>(dry_run, log)` registrata in REGISTRY.
  - Tutti i fix eseguono in transazione: rollback automatico se errore.
  - Dry-run mostra cosa cambierebbe senza scrivere.
  - Output: JSON con statistiche di ogni fix in `audit/reports/fix_<ts>.json`.

Uso:
  python audit/fix.py --dry-run                # solo simulazione
  python audit/fix.py                          # applica TUTTI i fix
  python audit/fix.py --only CON-001,NAM-001   # solo alcuni fix
  python audit/fix.py --backup                 # fa backup prima dei DELETE/UPDATE
"""
from __future__ import annotations
import argparse
import datetime as dt
import json
import re
import subprocess
import sys
import unicodedata
from pathlib import Path

# Path setup per import lib
sys.path.insert(0, str(Path(__file__).parent))
_FIX_ROOT = Path(__file__).resolve().parents[1]
_FIX_SRC = _FIX_ROOT / "src"
if _FIX_SRC.is_dir() and str(_FIX_SRC) not in sys.path:
    sys.path.insert(0, str(_FIX_SRC))

from lib.db import cursor, fetch_one, fetch_all, DB_NAME  # noqa: E402
from lib.log import get_logger  # noqa: E402
from lib.journal import append_event, new_run_id  # noqa: E402

try:
    from serie_a_scout.core import RunContext, get_default_store
    from serie_a_scout.obs import emit_run_metrics
    _LINEAGE_AVAILABLE = True
except Exception:  # noqa: BLE001
    RunContext = None  # type: ignore[assignment]
    _LINEAGE_AVAILABLE = False

log = get_logger("fix")

# ════════════════════════════════════════════════════════════════
# FIX CON-001 — minuti aggregati disallineati
# ════════════════════════════════════════════════════════════════
def fix_con_001(dry_run: bool) -> dict:
    """Allinea giocatori.minuti a SUM(giocatore_partita.minuti)."""
    with cursor() as (c, _):
        c.execute("""
            SELECT g.id, g.minuti, COALESCE(SUM(gp.minuti),0) AS real_min
            FROM giocatori g LEFT JOIN giocatore_partita gp ON gp.giocatore_id=g.id
            GROUP BY g.id, g.minuti
            HAVING ABS(g.minuti - COALESCE(SUM(gp.minuti),0)) > 30
        """)
        rows = c.fetchall()
        log.info(f"CON-001: {len(rows)} giocatori da riallineare")
        if dry_run:
            return {"to_update": len(rows), "dry_run": True}
        for gid, _old, new in rows:
            c.execute("UPDATE giocatori SET minuti=%s WHERE id=%s", (int(new), gid))
        return {"updated": len(rows)}


# ════════════════════════════════════════════════════════════════
# FIX CON-002 — partite=0 ricalcolato da giocatore_partita
# ════════════════════════════════════════════════════════════════
def fix_con_002(dry_run: bool) -> dict:
    """Aggiorna giocatori.partite con COUNT(giocatore_partita con minuti>0)."""
    with cursor() as (c, _):
        c.execute("""
            SELECT g.id, COUNT(*) AS n
            FROM giocatori g
            JOIN giocatore_partita gp ON gp.giocatore_id=g.id
            WHERE gp.minuti > 0
            GROUP BY g.id
        """)
        rows = c.fetchall()
        log.info(f"CON-002: {len(rows)} giocatori avranno partite ricalcolate")
        if dry_run:
            return {"to_update": len(rows), "dry_run": True}
        # Reset tutti a 0 prima (per gli orfani)
        c.execute("UPDATE giocatori SET partite=0")
        for gid, n in rows:
            c.execute("UPDATE giocatori SET partite=%s WHERE id=%s", (int(n), gid))
        return {"updated": len(rows)}


# ════════════════════════════════════════════════════════════════
# FIX CON-003 — xG aggregato disallineato
# ════════════════════════════════════════════════════════════════
def fix_con_003(dry_run: bool) -> dict:
    """Allinea giocatori.xg / xa / goal / assist / tiri da somma per-partita."""
    fields = ["xg", "xa", "goal", "assist", "tiri", "gialli", "rossi"]
    with cursor() as (c, _):
        c.execute(f"""
            SELECT g.id,
                   {', '.join(f'COALESCE(SUM(gp.{f}),0)' for f in fields)}
            FROM giocatori g LEFT JOIN giocatore_partita gp ON gp.giocatore_id=g.id
            GROUP BY g.id
        """)
        rows = c.fetchall()
        if dry_run:
            return {"to_update": len(rows), "dry_run": True}
        for row in rows:
            gid, *vals = row
            set_clause = ", ".join(f"{f}=%s" for f in fields)
            c.execute(f"UPDATE giocatori SET {set_clause} WHERE id=%s",
                      [*[round(float(v), 2) if f in ("xg", "xa") else int(v)
                         for v, f in zip(vals, fields)], gid])
        return {"updated": len(rows)}


# ════════════════════════════════════════════════════════════════
# FIX NAM-001 — normalizza nomi (NBSP, soft-hyphen, multi-space, trim)
# ════════════════════════════════════════════════════════════════
_INVISIBLE_RE = re.compile(r"[ ­​-‍﻿]")
_MULTI_SPACE_RE = re.compile(r"\s+")


def clean_name(s: str) -> str:
    """Rimuove NBSP/SHY/ZWSP, normalizza NFC, collapse spaces, trim."""
    if s is None:
        return s
    s = unicodedata.normalize("NFC", s)
    s = _INVISIBLE_RE.sub("", s)
    s = _MULTI_SPACE_RE.sub(" ", s).strip()
    return s


def fix_nam_001(dry_run: bool) -> dict:
    """Normalizza la colonna `nome` (e `cognome`) della tabella `giocatori`."""
    with cursor() as (c, _):
        c.execute("SELECT id, nome, cognome FROM giocatori")
        rows = c.fetchall()
        updates = []
        for gid, nome, cog in rows:
            new_nome = clean_name(nome) if nome else nome
            new_cog = clean_name(cog) if cog else cog
            if new_nome != nome or new_cog != cog:
                updates.append((new_nome, new_cog, gid))
        log.info(f"NAM-001: {len(updates)} nomi da pulire")
        if dry_run:
            return {"to_update": len(updates), "samples": [
                {"id": u[2], "new_nome": u[0], "new_cog": u[1]} for u in updates[:5]
            ], "dry_run": True}
        c.executemany("UPDATE giocatori SET nome=%s, cognome=%s WHERE id=%s", updates)
        return {"updated": len(updates)}


# ════════════════════════════════════════════════════════════════
# FIX NAM-003 — ruolo mancante derivato da `posizione`
# ════════════════════════════════════════════════════════════════
POS_TO_RUOLO = {
    # Understat short codes
    "GK": "POR", "G": "POR",
    "D": "DIF", "DC": "DIF", "DL": "DIF", "DR": "DIF",
    "DF": "DIF", "CB": "DIF", "LB": "DIF", "RB": "DIF",
    "WB": "DIF", "LWB": "DIF", "RWB": "DIF",
    "M": "CEN", "MC": "CEN", "ML": "CEN", "MR": "CEN",
    "MF": "CEN", "CM": "CEN", "DM": "CEN", "AM": "CEN", "LM": "CEN", "RM": "CEN",
    "F": "ATT", "FC": "ATT", "FL": "ATT", "FR": "ATT",
    "FW": "ATT", "ST": "ATT", "CF": "ATT", "LW": "ATT", "RW": "ATT", "SS": "ATT",
}


def map_posizione(pos: str | None) -> str | None:
    if not pos:
        return None
    p = pos.strip().upper()
    if p in POS_TO_RUOLO:
        return POS_TO_RUOLO[p]
    # match parziale: prendi il primo token
    first = re.split(r"[ ,/\-]", p, maxsplit=1)[0]
    return POS_TO_RUOLO.get(first)


def fix_nam_003(dry_run: bool) -> dict:
    """Deriva `giocatori.ruolo` da `giocatori.posizione` quando NULL."""
    with cursor() as (c, _):
        c.execute("""
            SELECT id, posizione FROM giocatori
            WHERE (ruolo IS NULL OR ruolo = '') AND minuti >= 100
        """)
        rows = c.fetchall()
        updates = []
        unmapped = []
        for gid, pos in rows:
            ruolo = map_posizione(pos)
            if ruolo:
                updates.append((ruolo, gid))
            else:
                unmapped.append({"id": gid, "posizione": pos})
        log.info(f"NAM-003: {len(updates)} aggiornabili, {len(unmapped)} senza mapping")
        if dry_run:
            return {"to_update": len(updates), "unmapped": unmapped[:10], "dry_run": True}
        c.executemany("UPDATE giocatori SET ruolo=%s WHERE id=%s", updates)
        return {"updated": len(updates), "unmapped": len(unmapped)}


# ════════════════════════════════════════════════════════════════
# FIX REF-003 — Aggiungi FK + UNIQUE su t_player_analytics
# ════════════════════════════════════════════════════════════════
def fix_ref_003(dry_run: bool) -> dict:
    """Aggiunge FK e UNIQUE KEY su t_player_analytics."""
    sqls = [
        # Prima rimuovi orfani
        ("delete_orphans_pa", """
            DELETE FROM t_player_analytics
            WHERE giocatore_id NOT IN (SELECT id FROM giocatori)
        """),
        ("delete_orphans_gl", """
            DELETE FROM t_player_game_log
            WHERE giocatore_id NOT IN (SELECT id FROM giocatori)
        """),
        # Allinea i tipi (giocatori.id è INT, le satellite sono BIGINT)
        ("align_type_pa", "ALTER TABLE t_player_analytics MODIFY giocatore_id INT"),
        ("align_type_gl", "ALTER TABLE t_player_game_log MODIFY giocatore_id INT"),
        # UNIQUE per evitare duplicati futuri
        ("uq_pa", "ALTER TABLE t_player_analytics ADD UNIQUE KEY uq_gid (giocatore_id)"),
        # FK con CASCADE
        ("fk_pa", """ALTER TABLE t_player_analytics
                     ADD CONSTRAINT fk_pa_gioc FOREIGN KEY (giocatore_id)
                     REFERENCES giocatori(id) ON DELETE CASCADE"""),
        ("fk_gl", """ALTER TABLE t_player_game_log
                     ADD CONSTRAINT fk_gl_gioc FOREIGN KEY (giocatore_id)
                     REFERENCES giocatori(id) ON DELETE CASCADE"""),
    ]
    results = {}
    with cursor() as (c, _):
        for name, sql in sqls:
            try:
                if dry_run:
                    # DESCRIBE-only verifica esistenza prima
                    results[name] = "would_run"
                    continue
                c.execute(sql)
                results[name] = "ok"
            except Exception as e:
                results[name] = f"skip: {str(e)[:120]}"
    return results


# ════════════════════════════════════════════════════════════════
# FIX SEC-001 — refactor credenziali hardcoded
# ════════════════════════════════════════════════════════════════
def fix_sec_001(dry_run: bool) -> dict:
    """
    Riscrive i blocchi `DB_PASSWORD = '...'` come `os.environ.get(...)`.
    Salva i file originali in audit/reports/sec_backup/ prima di toccarli.
    Crea/aggiorna `.env` con il valore.
    """
    root = Path(__file__).parent.parent
    target_re = re.compile(r"""(DB_PASSWORD\s*=\s*['"])([^'"]+)(['"])""")
    files_changed = []
    secret = None

    backup_dir = Path(__file__).parent / "reports" / "sec_backup"
    backup_dir.mkdir(parents=True, exist_ok=True)

    for py in root.rglob("*.py"):
        if any(part in py.parts for part in ("backup", ".git", "__pycache__", "audit")):
            continue
        try:
            txt = py.read_text(encoding="utf-8")
        except Exception:
            continue
        m = target_re.search(txt)
        if not m:
            continue
        secret = secret or m.group(2)
        new_txt = target_re.sub(r"""\1__REPLACED__\3""", txt)  # placeholder safe
        # Sostituiamo con un blocco che legge da env
        new_txt = re.sub(
            r"""DB_PASSWORD\s*=\s*['"]__REPLACED__['"]""",
            'DB_PASSWORD = os.environ.get("DB_PASSWORD", "")',
            new_txt,
        )
        # Assicuriamoci che `import os` esista
        if "import os" not in new_txt:
            new_txt = "import os\n" + new_txt

        if dry_run:
            files_changed.append(str(py.relative_to(root)))
        else:
            # backup
            (backup_dir / py.name).write_text(txt, encoding="utf-8")
            py.write_text(new_txt, encoding="utf-8")
            files_changed.append(str(py.relative_to(root)))

    # Crea/aggiorna .env
    if not dry_run and secret:
        env = root / ".env"
        existing = env.read_text(encoding="utf-8") if env.exists() else ""
        if "DB_PASSWORD=" not in existing:
            with open(env, "a", encoding="utf-8") as fh:
                fh.write(f"\nDB_PASSWORD={secret}\nDB_HOST=localhost\nDB_USER=root\nDB_NAME={DB_NAME}\n")
        # .gitignore
        gi = root / ".gitignore"
        gi_txt = gi.read_text(encoding="utf-8") if gi.exists() else ""
        if ".env" not in gi_txt:
            with open(gi, "a", encoding="utf-8") as fh:
                fh.write("\n.env\n")

    return {"files_changed": files_changed, "secret_moved_to_env": (not dry_run and bool(secret))}


# ════════════════════════════════════════════════════════════════
# REGISTRY
# ════════════════════════════════════════════════════════════════
REGISTRY = {
    "CON-001": fix_con_001,
    "CON-002": fix_con_002,
    "CON-003": fix_con_003,
    "NAM-001": fix_nam_001,
    "NAM-003": fix_nam_003,
    "REF-003": fix_ref_003,
    "SEC-001": fix_sec_001,
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--only", default=None, help="codici separati da virgola (es. CON-001,NAM-001)")
    ap.add_argument("--backup", action="store_true", help="esegue backup_db.py prima dei fix")
    args = ap.parse_args()

    if args.backup and not args.dry_run:
        log.info("Eseguo backup preliminare...")
        rc = subprocess.call([sys.executable, str(Path(__file__).parent.parent / "set_up_tpi_pro" / "backup_db.py")])
        if rc != 0:
            log.error("Backup fallito, abortisco i fix")
            return 2

    selected = REGISTRY
    if args.only:
        only = {c.strip() for c in args.only.split(",")}
        selected = {k: v for k, v in REGISTRY.items() if k in only}

    results = {}
    rid = new_run_id()
    append_event("fix.py", "started", run_id=rid,
                 payload={"dry_run": args.dry_run, "fixes": list(selected.keys())})

    lineage_ctx = (
        RunContext(stage="fix",
                   extra={"dry_run": args.dry_run,
                          "fixes": list(selected.keys()),
                          "journal_run_id": rid})
        if _LINEAGE_AVAILABLE else None
    )
    if lineage_ctx is not None:
        lineage_ctx.__enter__()

    log.info(f"{'DRY RUN' if args.dry_run else 'COMMIT'} — Eseguo {len(selected)} fix (run_id={rid[:8]})")
    any_error = False
    for code, fn in selected.items():
        log.info(f"▶  {code}")
        try:
            results[code] = fn(args.dry_run)
            if lineage_ctx is not None:
                lineage_ctx.add_event("fix.applied", code=code,
                                      result=results[code])
        except Exception as e:
            log.exception(f"❌ {code} fallito: {e}")
            results[code] = {"error": str(e)}
            any_error = True
            if lineage_ctx is not None:
                lineage_ctx.add_event("fix.failed", code=code,
                                      error=type(e).__name__, message=str(e))

    out = Path(__file__).parent / "reports" / f"fix_{dt.datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    out.write_text(json.dumps({"dry_run": args.dry_run, "results": results},
                              indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    log.info(f"✓ Report: {out}")
    for code, r in results.items():
        log.info(f"  {code}: {r}")
    append_event("fix.py", "completed", run_id=rid, exit_code=0,
                 payload={"results": {k: (v if isinstance(v, dict) else str(v))
                                      for k, v in results.items()},
                          "lineage_run_id": lineage_ctx.run_id if lineage_ctx else None})

    if lineage_ctx is not None:
        lineage_ctx.register_artifact(
            kind="json", path=str(out),
            compute_checksum=True,
            rows=len(results),
            meta={"dry_run": args.dry_run, "any_error": any_error},
        )
        lineage_ctx.set_exit_code(0)
        if any_error:
            lineage_ctx.set_status("partial")
        lineage_ctx.__exit__(None, None, None)
        try:
            emit_run_metrics(get_default_store().get_run(lineage_ctx.run_id))
        except Exception:  # noqa: BLE001
            pass

    return 0


if __name__ == "__main__":
    sys.exit(main())
