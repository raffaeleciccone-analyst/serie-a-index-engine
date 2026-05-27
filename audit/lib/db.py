"""Connessione e helper SQL per il modulo audit."""
from __future__ import annotations
import sys, os
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

from contextlib import contextmanager
import mysql.connector
from mysql.connector.connection import MySQLConnection

# Credenziali via config.py centralizzato (fail-fast su DB_PASSWORD)
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parents[2]))
from config import DB_HOST, DB_USER, DB_PASSWORD, DB_NAME  # noqa: E402


def connect(autocommit: bool = False) -> MySQLConnection:
    return mysql.connector.connect(
        host=DB_HOST, user=DB_USER, password=DB_PASSWORD,
        database=DB_NAME, autocommit=autocommit,
    )


@contextmanager
def cursor(autocommit: bool = False):
    """Context manager: cursore bufferizzato, chiusura sicura, rollback on error."""
    db = connect(autocommit=autocommit)
    cur = db.cursor(buffered=True)
    try:
        yield cur, db
        if not autocommit:
            db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        cur.close()
        db.close()


def fetch_all(sql: str, params=()) -> list[tuple]:
    with cursor(autocommit=True) as (c, _):
        c.execute(sql, params)
        return c.fetchall()


def fetch_one(sql: str, params=()):
    with cursor(autocommit=True) as (c, _):
        c.execute(sql, params)
        return c.fetchone()


def fetch_dict(sql: str, params=()) -> list[dict]:
    with cursor(autocommit=True) as (c, _):
        c.execute(sql, params)
        cols = [d[0] for d in c.description]
        return [dict(zip(cols, row)) for row in c.fetchall()]
