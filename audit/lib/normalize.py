"""Normalizzazione canonica dei nomi giocatore/squadra."""
from __future__ import annotations
import re
import unicodedata


def normalize_name(s: str | None) -> str:
    """
    Canonical form: NFKD → ASCII fold → lowercase → collapse whitespace → trim.
    Es. ' Mike  Maignan ' → 'mike maignan'
        'Nícolas González' → 'nicolas gonzalez'
        'Côte'          → 'cote'
    """
    if s is None:
        return ""
    s = str(s)
    # NFKD: decompone accenti come combining chars
    s = unicodedata.normalize("NFKD", s)
    # Rimuovi i combining accents
    s = "".join(c for c in s if not unicodedata.combining(c))
    # Caratteri di controllo / zero-width → spazio
    s = re.sub(r"[​-‍﻿\x00-\x1F]", " ", s)
    s = s.lower().strip()
    s = re.sub(r"\s+", " ", s)
    return s


def visible_diff(a: str, b: str) -> str:
    """Mostra dove due stringhe differiscono (debug duplicati invisibili)."""
    if a == b:
        return "(identical)"
    pairs = []
    for i, (ca, cb) in enumerate(zip(a, b)):
        if ca != cb:
            pairs.append(f"pos{i}: {ca!r}({hex(ord(ca))}) != {cb!r}({hex(ord(cb))})")
    if len(a) != len(b):
        pairs.append(f"len: {len(a)} vs {len(b)}")
    return " | ".join(pairs[:3])
