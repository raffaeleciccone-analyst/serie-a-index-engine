"""
Multi-provider merge engine.

Given several `FetchResult`s for the same `(season, kind)`, produce one
canonical DataFrame plus a `MergeReport` listing every conflict
encountered. Conflicts are NEVER silently resolved — every disagreement
becomes a row in `MergeReport.conflicts`.

Strategy: per-column precedence (`provider_precedence` from
`config/seasons.yml`). The first provider in the list wins. If the
winning provider didn't observe the row, fall back to the next provider.

Each conflict carries enough metadata to feed an analyst review queue
(F3) or an `AnomalyType.PROVIDER_DISAGREEMENT` anomaly (F2.0).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable, Mapping, Sequence

import numpy as np
import pandas as pd

from .base import FetchResult


# ─────────────────────────────────────────────────────────────────────
# Result types
# ─────────────────────────────────────────────────────────────────────
class MergeStrategy(str, Enum):
    """How to combine multiple values for the same `(player_id, column)`."""
    PRECEDENCE = "precedence"            # default: pick winning provider
    AVERAGE = "average"                  # numeric-only mean of providers
    MAX = "max"
    MIN = "min"


@dataclass
class MergeConflict:
    """One disagreement between providers on a single (row, column)."""
    season: str
    kind: str
    row_key: dict[str, Any]              # what identifies the row
    column: str
    values_by_provider: dict[str, Any]
    chosen_provider: str
    chosen_value: Any
    relative_spread: float | None        # |max-min|/|mean| for numerics

    def to_dict(self) -> dict[str, Any]:
        return {
            "season": self.season,
            "kind": self.kind,
            "row_key": dict(self.row_key),
            "column": self.column,
            "values_by_provider": dict(self.values_by_provider),
            "chosen_provider": self.chosen_provider,
            "chosen_value": self.chosen_value,
            "relative_spread": self.relative_spread,
        }


@dataclass
class MergeReport:
    season: str
    kind: str
    n_input_rows: int
    n_output_rows: int
    providers: list[str]
    conflicts: list[MergeConflict] = field(default_factory=list)

    @property
    def n_conflicts(self) -> int:
        return len(self.conflicts)

    def to_dict(self) -> dict[str, Any]:
        return {
            "season": self.season,
            "kind": self.kind,
            "n_input_rows": self.n_input_rows,
            "n_output_rows": self.n_output_rows,
            "providers": list(self.providers),
            "n_conflicts": self.n_conflicts,
            "conflicts": [c.to_dict() for c in self.conflicts],
        }


# ─────────────────────────────────────────────────────────────────────
# Engine
# ─────────────────────────────────────────────────────────────────────
def merge_provider_frames(
    fetches: Sequence[FetchResult],
    *,
    precedence: Mapping[str, Sequence[str]] | None = None,
    key_columns: Sequence[str] = ("player_id",),
    strategy: MergeStrategy = MergeStrategy.PRECEDENCE,
    disagreement_relative_threshold: float = 0.10,
) -> tuple[pd.DataFrame, MergeReport]:
    """
    Merge several `FetchResult`s into one canonical DataFrame.

    Parameters
    ----------
    fetches
        At least one FetchResult. All must share the same (season, kind).
    precedence
        Mapping `column → ordered provider list`. For columns not in the
        map, the order of `fetches` defines precedence.
    key_columns
        Row identity. Defaults to `(player_id,)`; for player_matches use
        `("player_id", "giornata")`.
    strategy
        Default merge strategy when precedence resolves to multiple
        providers with values. Currently only `PRECEDENCE` is fully
        implemented (the others would be column-specific overrides).
    disagreement_relative_threshold
        Numeric disagreement above this (|max-min|/|mean|) is logged as a
        conflict regardless of strategy. 10% by default.
    """
    if not fetches:
        raise ValueError("merge_provider_frames: at least one FetchResult required")

    seasons = {f.season for f in fetches}
    kinds = {f.kind for f in fetches}
    if len(seasons) > 1 or len(kinds) > 1:
        raise ValueError(
            f"merge requires homogeneous (season, kind); got "
            f"seasons={seasons}, kinds={kinds}"
        )
    season = next(iter(seasons))
    kind = next(iter(kinds))
    providers = [f.provider for f in fetches]
    precedence = dict(precedence or {})

    # Build the long-form view: one row per (provider, *key_columns).
    frames: list[pd.DataFrame] = []
    for f in fetches:
        if f.df.empty:
            continue
        if "provider" not in f.df.columns:
            df = f.df.copy()
            df["provider"] = f.provider
        else:
            df = f.df
        frames.append(df)
    if not frames:
        empty = pd.DataFrame(columns=list(fetches[0].df.columns))
        return empty, MergeReport(
            season=season, kind=kind,
            n_input_rows=0, n_output_rows=0, providers=providers,
        )

    long_df = pd.concat(frames, ignore_index=True, sort=False)
    n_input = len(long_df)

    # Group by row key.
    grouped = long_df.groupby(list(key_columns), dropna=False)

    out_rows: list[dict[str, Any]] = []
    conflicts: list[MergeConflict] = []

    # All non-key, non-provider columns are candidates for merge.
    value_columns = [
        c for c in long_df.columns
        if c not in set(key_columns) and c != "provider"
    ]

    for key_vals, sub in grouped:
        key_vals_tuple = key_vals if isinstance(key_vals, tuple) else (key_vals,)
        row_key = {
            k: _to_jsonable(v) for k, v in zip(key_columns, key_vals_tuple)
        }
        merged: dict[str, Any] = dict(zip(key_columns, key_vals_tuple))
        provider_seen = list(sub["provider"].astype(str).tolist())
        merged["providers_seen"] = ",".join(sorted(set(provider_seen)))

        for col in value_columns:
            values_by_prov: dict[str, Any] = {}
            for _, r in sub.iterrows():
                prov = str(r["provider"])
                v = r[col]
                if _is_present(v):
                    values_by_prov[prov] = v
            if not values_by_prov:
                merged[col] = None
                continue

            order = list(precedence.get(col, []))
            if not order:
                order = providers
            chosen_prov, chosen_val = _pick_winner(values_by_prov, order)
            merged[col] = chosen_val

            if _is_conflicting(values_by_prov, disagreement_relative_threshold):
                spread = _relative_spread(values_by_prov)
                conflicts.append(MergeConflict(
                    season=season,
                    kind=kind,
                    row_key=dict(row_key),
                    column=col,
                    values_by_provider={
                        k: _to_jsonable(v) for k, v in values_by_prov.items()
                    },
                    chosen_provider=chosen_prov,
                    chosen_value=_to_jsonable(chosen_val),
                    relative_spread=spread,
                ))

        out_rows.append(merged)

    out_df = pd.DataFrame(out_rows)
    # Deterministic ordering: sort by key columns.
    if not out_df.empty:
        out_df = out_df.sort_values(list(key_columns)).reset_index(drop=True)

    report = MergeReport(
        season=season, kind=kind,
        n_input_rows=n_input, n_output_rows=len(out_df),
        providers=providers, conflicts=conflicts,
    )
    return out_df, report


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────
def _is_present(v: Any) -> bool:
    if v is None:
        return False
    if isinstance(v, float) and (np.isnan(v) or np.isinf(v)):
        return False
    try:
        if pd.isna(v):
            return False
    except (TypeError, ValueError):
        return True
    return True


def _pick_winner(
    values: Mapping[str, Any], order: Sequence[str],
) -> tuple[str, Any]:
    for prov in order:
        if prov in values:
            return prov, values[prov]
    # Fallback: deterministic on sorted provider name.
    prov = sorted(values)[0]
    return prov, values[prov]


def _is_conflicting(values: Mapping[str, Any], rel_threshold: float) -> bool:
    if len(values) < 2:
        return False
    uniq = set()
    for v in values.values():
        try:
            uniq.add(round(float(v), 6))
        except (TypeError, ValueError):
            uniq.add(str(v))
    if len(uniq) == 1:
        return False
    nums: list[float] = []
    for v in values.values():
        try:
            nums.append(float(v))
        except (TypeError, ValueError):
            return True   # categorical disagreement
    if not nums:
        return True
    return _relative_spread(values) > rel_threshold


def _relative_spread(values: Mapping[str, Any]) -> float | None:
    nums: list[float] = []
    for v in values.values():
        try:
            nums.append(float(v))
        except (TypeError, ValueError):
            return None
    if not nums:
        return None
    mean = float(np.mean(nums))
    if abs(mean) < 1e-9:
        return 0.0 if (max(nums) - min(nums)) < 1e-9 else float("inf")
    return abs(max(nums) - min(nums)) / abs(mean)


def _to_jsonable(v: Any) -> Any:
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.floating,)):
        return float(v)
    return v
