"""
Canonical schema for provider outputs.

Every adapter MUST coerce its native shape into these columns before
returning a `FetchResult`. The merge engine relies on the column names
+ types being stable across providers.

Schema is intentionally narrow — only the columns the analytics layer
actually consumes. Providers can carry additional columns in `extra_*`
without breaking the contract.
"""
from __future__ import annotations

from typing import Mapping

import pandas as pd


# ─────────────────────────────────────────────────────────────────────
# Column sets
# ─────────────────────────────────────────────────────────────────────
CANONICAL_PLAYER_COLUMNS: tuple[str, ...] = (
    "player_id",        # canonical id (entity-resolved). int64
    "provider",         # which provider produced this row. str
    "season",           # "2025-26". str
    "name",             # display name. str
    "team",             # current team name. str
    "position",         # GK/DF/MF/FW. str
    "birth_date",       # YYYY-MM-DD or None. str|None
    "nationality",      # ISO-3 or None. str|None
)

CANONICAL_PLAYER_MATCH_COLUMNS: tuple[str, ...] = (
    "player_id",
    "provider",
    "season",
    "giornata",
    "team",
    "opponent",
    "minutes",
    "goals",
    "assists",
    "xg",
    "xa",
    "shots",
    "yellow_cards",
    "red_cards",
)

CANONICAL_TEAM_COLUMNS: tuple[str, ...] = (
    "team_id",
    "provider",
    "season",
    "name",
    "league",
)


# Type spec (pandas-friendly). Used by `enforce_canonical_schema`.
CANONICAL_TYPES: Mapping[str, str] = {
    "player_id":    "Int64",
    "provider":     "string",
    "season":       "string",
    "name":         "string",
    "team":         "string",
    "opponent":     "string",
    "position":     "string",
    "birth_date":   "string",
    "nationality":  "string",
    "league":       "string",
    "team_id":      "Int64",
    "giornata":     "Int64",
    "minutes":      "Int64",
    "goals":        "Int64",
    "assists":      "Int64",
    "shots":        "Int64",
    "yellow_cards": "Int64",
    "red_cards":    "Int64",
    "xg":           "Float64",
    "xa":           "Float64",
}


# ─────────────────────────────────────────────────────────────────────
# Validation / coercion helpers
# ─────────────────────────────────────────────────────────────────────
class SchemaViolation(ValueError):
    """Raised by `validate_canonical_schema` on missing columns or bad types."""


def validate_canonical_schema(
    df: pd.DataFrame, *, kind: str,
) -> list[str]:
    """
    Validate `df` matches the canonical column set for `kind`.

    Returns a list of error strings; empty list = ok.
    `kind` ∈ {"players", "player_matches", "teams"}.
    """
    needed = _columns_for(kind)
    errors: list[str] = []
    for col in needed:
        if col not in df.columns:
            errors.append(f"missing column: {col}")
    # Extra columns are allowed (provider-specific extras).
    return errors


def enforce_canonical_schema(
    df: pd.DataFrame, *, kind: str,
) -> pd.DataFrame:
    """
    Return a new DataFrame with:
      * exactly the canonical columns (drop unknown)
      * canonical dtypes via pandas' nullable types
      * deterministic column order

    Raises `SchemaViolation` if a required column is missing.
    """
    needed = _columns_for(kind)
    missing = [c for c in needed if c not in df.columns]
    if missing:
        raise SchemaViolation(
            f"canonical schema {kind!r} missing columns: {missing}"
        )
    out = df[list(needed)].copy()
    for col in needed:
        target = CANONICAL_TYPES.get(col, "string")
        try:
            out[col] = out[col].astype(target)
        except (ValueError, TypeError) as e:
            raise SchemaViolation(
                f"column {col!r} cannot coerce to {target}: {e}"
            ) from e
    return out.reset_index(drop=True)


def _columns_for(kind: str) -> tuple[str, ...]:
    if kind == "players":
        return CANONICAL_PLAYER_COLUMNS
    if kind == "player_matches":
        return CANONICAL_PLAYER_MATCH_COLUMNS
    if kind == "teams":
        return CANONICAL_TEAM_COLUMNS
    raise SchemaViolation(f"unknown kind: {kind!r}")
