"""
prevent.py — Installa vincoli strutturali e hook di validazione automatica.

Obiettivo: rendere IMPOSSIBILE rintrodurre i bug già fixati.
Tre livelli di difesa:

  1. SCHEMA — UNIQUE/FK/INDEX che fanno fallire l'INSERT scorretto.
  2. TRIGGER — BEFORE INSERT/UPDATE che normalizza nomi e blocca anomalie.
  3. POST-INGESTION — hook Python che lancia `audit/audit.py` dopo ogni
     `parte4_aggiorna.py` e fallisce se exit code = 2 (CRITICAL).

Uso:
  python audit/prevent.py --dry-run
  python audit/prevent.py --apply
  python audit/prevent.py --apply --install-hook    # aggiunge call audit a parte4
"""
from __future__ import annotations
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from lib.db import cursor  # noqa: E402
from lib.log import get_logger  # noqa: E402

log = get_logger("prevent")

# ════════════════════════════════════════════════════════════════
# LIVELLO 1 — VINCOLI DI SCHEMA
# ════════════════════════════════════════════════════════════════
SCHEMA_HARDENING = [
    # UNIQUE giocatori(nome,squadra_id) — protegge da dedup mancante
    ("giocatori_uq_nome_sq", """
        ALTER TABLE giocatori
        ADD UNIQUE KEY uq_giocatori_nome_squadra (nome, squadra_id)
    """),
    # UNIQUE su game_id_understat
    ("calendario_uq_gid", """
        ALTER TABLE calendario
        ADD UNIQUE KEY uq_cal_game_id (game_id_understat)
    """),
    # UNIQUE su squadre.nome
    ("squadre_uq_nome", """
        ALTER TABLE squadre ADD UNIQUE KEY uq_squadre_nome (nome)
    """),
    # Indice utile per le query di trend
    ("idx_gp_data", """
        CREATE INDEX idx_gp_giocatore_minuti ON giocatore_partita (giocatore_id, minuti)
    """),
    # CHECK su date infortunio
    ("chk_inf_dates", """
        ALTER TABLE t_infortuni
        ADD CONSTRAINT chk_inf_dates CHECK (data_rientro IS NULL OR data_rientro >= data_inizio)
    """),
    # CHECK su minuti partita (0-130 per tempi supplementari)
    ("chk_gp_minuti", """
        ALTER TABLE giocatore_partita
        ADD CONSTRAINT chk_gp_minuti CHECK (minuti BETWEEN 0 AND 130)
    """),
]


# ════════════════════════════════════════════════════════════════
# LIVELLO 2 — TRIGGER DI NORMALIZZAZIONE
# ════════════════════════════════════════════════════════════════
def _norm_expr(col: str) -> str:
    """Espressione SQL che pulisce un campo: NBSP→space, SHY→nulla, multi-space→1, trim."""
    return (
        f"TRIM(REGEXP_REPLACE("
        f"REPLACE(REPLACE(IFNULL({col}, ''), "
        f"CHAR(0xC2,0xA0 USING utf8mb4), ' '), "
        f"CHAR(0xC2,0xAD USING utf8mb4), ''), "
        f"' {{2,}}', ' '))"
    )


# Single-statement triggers: usano SET NEW.col1=...; SET NEW.col2=...;
# combinati con CONCAT_WS o subselect, evitando BEGIN..END
TRIGGERS = {
    "trg_giocatori_normalize_ins": (
        f"CREATE TRIGGER trg_giocatori_normalize_ins "
        f"BEFORE INSERT ON giocatori FOR EACH ROW "
        f"SET NEW.nome = {_norm_expr('NEW.nome')}, "
        f"    NEW.cognome = NULLIF({_norm_expr('NEW.cognome')}, '')"
    ),
    "trg_giocatori_normalize_upd": (
        f"CREATE TRIGGER trg_giocatori_normalize_upd "
        f"BEFORE UPDATE ON giocatori FOR EACH ROW "
        f"SET NEW.nome = {_norm_expr('NEW.nome')}, "
        f"    NEW.cognome = NULLIF({_norm_expr('NEW.cognome')}, '')"
    ),
    "trg_squadre_normalize_ins": (
        f"CREATE TRIGGER trg_squadre_normalize_ins "
        f"BEFORE INSERT ON squadre FOR EACH ROW "
        f"SET NEW.nome = {_norm_expr('NEW.nome')}"
    ),
}


# ════════════════════════════════════════════════════════════════
# LIVELLO 3 — POST-INGESTION HOOK
# ════════════════════════════════════════════════════════════════
POST_HOOK_SNIPPET = '''
# ── Audit post-ingestion (installato da audit/prevent.py) ─────────────────
import subprocess as _audit_sp, sys as _audit_sys, os as _audit_os
print("\\n▶ Eseguo audit post-aggiornamento...")
_audit_rc = _audit_sp.call([
    _audit_sys.executable,
    _audit_os.path.join(_audit_os.path.dirname(__file__), "audit", "audit.py"),
    "--quiet",
])
if _audit_rc == 2:
    print("❌ Audit ha rilevato problemi CRITICAL — verifica audit/reports/")
    _audit_sys.exit(2)
elif _audit_rc == 1:
    print("⚠️  Audit ha rilevato HIGH — continua ma fissa appena possibile.")
print("✓ Audit completato.")
'''


def install_schema(dry_run: bool) -> dict:
    results = {}
    with cursor() as (c, _):
        for name, sql in SCHEMA_HARDENING:
            try:
                if dry_run:
                    results[name] = "would_apply"
                    continue
                c.execute(sql.strip())
                results[name] = "ok"
            except Exception as e:
                msg = str(e)[:120]
                # Errori "già esistente" sono accettabili
                if "Duplicate" in msg or "exists" in msg.lower() or "1061" in msg:
                    results[name] = "skip:already_present"
                else:
                    results[name] = f"FAIL: {msg}"
    return results


def install_triggers(dry_run: bool) -> dict:
    results = {}
    if dry_run:
        return {k: "would_apply" for k in TRIGGERS}
    with cursor(autocommit=True) as (c, _):  # i DDL su trigger richiedono autocommit
        for name, sql in TRIGGERS.items():
            try:
                c.execute(f"DROP TRIGGER IF EXISTS {name}")
                c.execute(sql)
                results[name] = "ok"
            except Exception as e:
                results[name] = f"FAIL: {str(e)[:160]}"
    return results


def install_post_hook(dry_run: bool) -> dict:
    """Aggiunge in coda a parte4_aggiorna.py il blocco di audit auto."""
    target = Path(__file__).parent.parent / "parte4_aggiorna.py"
    if not target.exists():
        return {"file": str(target), "status": "not_found"}
    txt = target.read_text(encoding="utf-8")
    if "Audit post-ingestion" in txt:
        return {"file": str(target), "status": "already_installed"}
    if dry_run:
        return {"file": str(target), "status": "would_install"}
    target.write_text(txt + "\n" + POST_HOOK_SNIPPET, encoding="utf-8")
    return {"file": str(target), "status": "installed"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--install-hook", action="store_true",
                    help="installa anche il hook post-ingestion in parte4_aggiorna.py")
    args = ap.parse_args()

    if not args.dry_run and not args.apply:
        print("Specifica --dry-run o --apply")
        return 1

    log.info("═══ LIVELLO 1: SCHEMA HARDENING ═══")
    r1 = install_schema(args.dry_run)
    for k, v in r1.items():
        log.info(f"  {k}: {v}")

    log.info("\n═══ LIVELLO 2: TRIGGER NORMALIZZAZIONE ═══")
    r2 = install_triggers(args.dry_run)
    for k, v in r2.items():
        log.info(f"  {k}: {v}")

    if args.install_hook:
        log.info("\n═══ LIVELLO 3: POST-INGESTION HOOK ═══")
        r3 = install_post_hook(args.dry_run)
        log.info(f"  {r3}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
