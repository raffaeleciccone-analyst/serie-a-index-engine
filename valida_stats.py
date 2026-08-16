"""
valida_stats.py — Helper statistici puri per la validazione TPI
================================================================
Funzioni senza dipendenze dal payload o dal DB: prendono array/liste e
restituiscono numeri. Pensate per essere unit-testabili in isolamento e
riusate da parte3_valida_tpi.py.

Coprono le metriche aggiunte alla validazione:
  · bootstrap_ci          — IC bootstrap per Pearson/Spearman/Kendall
  · loo_corr_range        — leave-one-out: range della correlazione (T6)
  · partial_spearman_by_group — correlazione parziale controllando il ruolo
  · hypergeom_overlap     — significatività dell'overlap top-N (test B)
  · skill_scores          — skill predittivo vs baseline persistenza/gruppo (C)
  · oos_rmse_kfold        — RMSE out-of-sample con k-fold sui soggetti (C)
  · placebo_corr          — distribuzione nulla per permutazione (T3)
  · pca_explained         — varianza spiegata dalle componenti (T4)
  · paired_rmse_bootstrap — confronto incrementale appaiato TPI vs TPI Pro (D/E)
  · team_level_corr       — validità ecologica a livello squadra (T1)
"""
from __future__ import annotations

from typing import Sequence

import numpy as np
from scipy import stats

_CORR = {
    "pearson":  lambda a, b: stats.pearsonr(a, b)[0],
    "spearman": lambda a, b: stats.spearmanr(a, b)[0],
    "kendall":  lambda a, b: stats.kendalltau(a, b)[0],
}


def _as_arrays(x: Sequence[float], y: Sequence[float]) -> tuple[np.ndarray, np.ndarray]:
    a = np.asarray(x, dtype=float)
    b = np.asarray(y, dtype=float)
    mask = np.isfinite(a) & np.isfinite(b)
    return a[mask], b[mask]


def correlation(x: Sequence[float], y: Sequence[float], method: str = "spearman") -> float | None:
    a, b = _as_arrays(x, y)
    if len(a) < 3 or np.std(a) == 0 or np.std(b) == 0:
        return None
    r = _CORR[method](a, b)
    return None if (r is None or np.isnan(r)) else float(r)


def bootstrap_ci(x: Sequence[float], y: Sequence[float], method: str = "spearman",
                 n: int = 1000, seed: int = 42,
                 alpha: float = 0.05) -> tuple[float | None, float | None]:
    """IC bootstrap percentile per la correlazione indicata. (lo, hi) o (None, None)."""
    a, b = _as_arrays(x, y)
    if len(a) < 5:
        return None, None
    rng = np.random.default_rng(seed)
    fn = _CORR[method]
    boot = []
    m = len(a)
    for _ in range(n):
        idx = rng.integers(0, m, m)
        sa, sb = a[idx], b[idx]
        if np.std(sa) == 0 or np.std(sb) == 0:
            continue
        r = fn(sa, sb)
        if r is not None and not np.isnan(r):
            boot.append(r)
    if len(boot) < n * 0.5:
        return None, None
    lo = float(np.percentile(boot, 100 * alpha / 2))
    hi = float(np.percentile(boot, 100 * (1 - alpha / 2)))
    return round(lo, 4), round(hi, 4)


def loo_corr_range(x: Sequence[float], y: Sequence[float],
                   method: str = "pearson") -> dict | None:
    """
    Leave-one-out: ricalcola la correlazione escludendo un punto alla volta.
    Restituisce min/max/full e l'indice del punto più influente (T6).
    Un range ampio segnala che la r dipende da pochi soggetti.
    """
    a, b = _as_arrays(x, y)
    n = len(a)
    if n < 6:
        return None
    fn = _CORR[method]
    full = fn(a, b)
    vals = []
    for i in range(n):
        sa = np.delete(a, i)
        sb = np.delete(b, i)
        if np.std(sa) == 0 or np.std(sb) == 0:
            continue
        vals.append(fn(sa, sb))
    if not vals:
        return None
    vals = np.array(vals, dtype=float)
    i_min = int(np.argmin(vals))
    i_max = int(np.argmax(vals))
    return {
        "full": round(float(full), 4),
        "min":  round(float(vals.min()), 4),
        "max":  round(float(vals.max()), 4),
        "spread": round(float(vals.max() - vals.min()), 4),
        # indice del punto la cui RIMOZIONE abbassa di più la r → punto che la sostiene
        "most_influential_idx": i_min,
    }


def partial_spearman_by_group(x: Sequence[float], y: Sequence[float],
                              groups: Sequence) -> float | None:
    """
    Correlazione parziale (rank-based) di x e y controllando una variabile
    categoriale (es. ruolo): si centra ogni variabile per la media-gruppo dei
    ranghi, poi si correla il residuo. Rimuove la quota di correlazione dovuta
    alle differenze sistematiche fra gruppi.
    """
    a = np.asarray(x, dtype=float)
    b = np.asarray(y, dtype=float)
    g = np.asarray(groups, dtype=object)
    mask = np.isfinite(a) & np.isfinite(b)
    a, b, g = a[mask], b[mask], g[mask]
    if len(a) < 5:
        return None
    ra = stats.rankdata(a)
    rb = stats.rankdata(b)
    res_a = ra.astype(float).copy()
    res_b = rb.astype(float).copy()
    for grp in np.unique(g):
        sel = g == grp
        if sel.sum() > 0:
            res_a[sel] -= ra[sel].mean()
            res_b[sel] -= rb[sel].mean()
    if np.std(res_a) == 0 or np.std(res_b) == 0:
        return None
    r = stats.pearsonr(res_a, res_b)[0]  # Pearson su ranghi residui = Spearman parziale
    return None if np.isnan(r) else round(float(r), 4)


def hypergeom_overlap(k: int, top_n: int, ws_top_n: int, pool: int) -> dict:
    """
    Significatività dell'overlap fra due top-list estratte dallo stesso bacino.
    P(overlap >= k) sotto ipotesi di indipendenza (coda superiore ipergeometrica).
    k = coincidenze osservate; top_n/ws_top_n = ampiezze liste; pool = candidati comuni.
    """
    if pool <= 0 or top_n <= 0 or ws_top_n <= 0:
        return {"p": None, "expected": None, "k": k}
    expected = top_n * ws_top_n / pool
    # P(X >= k) = sf(k-1)
    p = float(stats.hypergeom.sf(k - 1, pool, ws_top_n, top_n))
    return {"p": round(p, 4), "expected": round(expected, 2), "k": int(k)}


def skill_scores(early: Sequence[float], late: Sequence[float],
                 group_late_means: Sequence[float] | None = None,
                 k: int = 5, seed: int = 42) -> dict:
    """
    Skill predittivo del segnale 'early' su 'late', confrontato con due baseline:
      · persistenza:  late_hat = early                (nessun modello)
      · gruppo/ruolo: late_hat = media-ruolo di late  (se fornita)
    Skill = 1 − SSE_modello / SSE_baseline. >0 = batte la baseline.
    Il modello usa la retta early→late stimata OUT-OF-SAMPLE in k-fold.
    group_late_means, se fornita, deve essere allineata posizionalmente a early/late.
    """
    e = np.asarray(early, dtype=float)
    l = np.asarray(late, dtype=float)
    has_group = group_late_means is not None and len(group_late_means) == len(early)
    gm = np.asarray(group_late_means, dtype=float) if has_group else None

    finite = np.isfinite(e) & np.isfinite(l)
    if has_group:
        finite &= np.isfinite(gm)
    e, l = e[finite], l[finite]
    if has_group:
        gm = gm[finite]
    if len(e) < 10:
        return {"n": len(e), "r": None, "rmse_oos": None,
                "skill_persistence": None, "skill_group": None}

    # Predizioni OOS del modello lineare (k-fold sui soggetti)
    rng = np.random.default_rng(seed)
    idx = rng.permutation(len(e))
    folds = np.array_split(idx, min(k, len(e)))
    pred = np.full(len(e), np.nan)
    for f in folds:
        train = np.setdiff1d(np.arange(len(e)), f)
        if len(train) < 3 or np.std(e[train]) == 0:
            pred[f] = np.mean(l[train]) if len(train) else np.nan
            continue
        sl, ic, *_ = stats.linregress(e[train], l[train])
        pred[f] = sl * e[f] + ic
    ok = np.isfinite(pred)
    sse_model = float(np.sum((l[ok] - pred[ok]) ** 2))
    rmse_oos = float(np.sqrt(np.mean((l[ok] - pred[ok]) ** 2)))

    sse_pers = float(np.sum((l[ok] - e[ok]) ** 2))
    skill_pers = 1 - sse_model / sse_pers if sse_pers > 0 else None

    skill_group = None
    if has_group:
        sse_group = float(np.sum((l[ok] - gm[ok]) ** 2))
        skill_group = 1 - sse_model / sse_group if sse_group > 0 else None

    r = correlation(e, l, "spearman")
    return {
        "n": int(ok.sum()),
        "r": r,
        "rmse_oos": round(rmse_oos, 4),
        "skill_persistence": round(skill_pers, 4) if skill_pers is not None else None,
        "skill_group": round(skill_group, 4) if skill_group is not None else None,
    }


def oos_rmse_kfold(x: Sequence[float], y: Sequence[float],
                   k: int = 5, seed: int = 42) -> float | None:
    e, l = _as_arrays(x, y)
    if len(e) < 10:
        return None
    rng = np.random.default_rng(seed)
    idx = rng.permutation(len(e))
    folds = np.array_split(idx, min(k, len(e)))
    err = []
    for f in folds:
        train = np.setdiff1d(np.arange(len(e)), f)
        if len(train) < 3 or np.std(e[train]) == 0:
            continue
        sl, ic, *_ = stats.linregress(e[train], l[train])
        pred = sl * e[f] + ic
        err.extend((l[f] - pred) ** 2)
    if not err:
        return None
    return round(float(np.sqrt(np.mean(err))), 4)


def placebo_corr(x: Sequence[float], y: Sequence[float], method: str = "spearman",
                 n: int = 500, seed: int = 42) -> dict:
    """
    Controllo negativo: permuta y rompendo l'accoppiamento e calcola la
    distribuzione nulla della correlazione. Restituisce r osservata, media nulla,
    soglia 95° percentile (|r|) e un p-value di permutazione.
    Se l'osservata supera nettamente la soglia nulla → il test discrimina (T3).
    """
    a, b = _as_arrays(x, y)
    if len(a) < 8:
        return {"observed": None, "null_mean": None, "null_p95": None, "p_perm": None}
    fn = _CORR[method]
    observed = fn(a, b)
    rng = np.random.default_rng(seed)
    null = []
    for _ in range(n):
        bp = rng.permutation(b)
        r = fn(a, bp)
        if r is not None and not np.isnan(r):
            null.append(r)
    null = np.array(null, dtype=float)
    p_perm = float((np.sum(np.abs(null) >= abs(observed)) + 1) / (len(null) + 1))
    return {
        "observed": round(float(observed), 4),
        "null_mean": round(float(np.mean(null)), 4),
        "null_p95": round(float(np.percentile(np.abs(null), 95)), 4),
        "p_perm": round(p_perm, 4),
    }


def pca_explained(matrix: Sequence[Sequence[float]],
                  labels: Sequence[str] | None = None) -> dict | None:
    """
    PCA su una matrice (righe = soggetti, colonne = dimensioni). Standardizza le
    colonne e restituisce la quota di varianza spiegata da ogni componente +
    la matrice di correlazione fra dimensioni (T4).
    PC1 ~ 1.0 → composito di fatto monodimensionale (pesi poco rilevanti).
    """
    M = np.asarray(matrix, dtype=float)
    if M.ndim != 2 or M.shape[0] < 5 or M.shape[1] < 2:
        return None
    # tieni solo righe complete
    M = M[np.all(np.isfinite(M), axis=1)]
    if M.shape[0] < 5:
        return None
    mu = M.mean(axis=0)
    sd = M.std(axis=0, ddof=1)
    sd[sd == 0] = 1.0
    Z = (M - mu) / sd
    # correlazione fra dimensioni
    corr = np.corrcoef(Z, rowvar=False)
    # PCA via SVD sulla matrice standardizzata
    _, s, _ = np.linalg.svd(Z, full_matrices=False)
    var = s ** 2
    ratios = (var / var.sum()).tolist()
    out = {
        "n": int(M.shape[0]),
        "explained": [round(float(r), 4) for r in ratios],
        "pc1": round(float(ratios[0]), 4),
        "corr": np.round(corr, 3).tolist(),
    }
    if labels is not None:
        out["labels"] = list(labels)
    return out


def paired_rmse_bootstrap(early: Sequence[float], late: Sequence[float],
                          early_pro: Sequence[float],
                          n: int = 1000, seed: int = 42) -> dict | None:
    """
    Validità incrementale appaiata (D/E): confronta l'errore predittivo OOS del
    segnale base vs il segnale 'pro' sugli stessi soggetti. Restituisce la
    differenza di RMSE (base − pro) con IC bootstrap: se l'IC esclude 0 a favore
    del pro, il modulo aggiunge potere predittivo reale (non circolare).
    """
    e = np.asarray(early, dtype=float)
    ep = np.asarray(early_pro, dtype=float)
    l = np.asarray(late, dtype=float)
    mask = np.isfinite(e) & np.isfinite(ep) & np.isfinite(l)
    e, ep, l = e[mask], ep[mask], l[mask]
    if len(e) < 12:
        return None

    def _oos_err(x, y):
        rng = np.random.default_rng(seed)
        idx = rng.permutation(len(x))
        folds = np.array_split(idx, min(5, len(x)))
        err = np.full(len(x), np.nan)
        for f in folds:
            tr = np.setdiff1d(np.arange(len(x)), f)
            if len(tr) < 3 or np.std(x[tr]) == 0:
                continue
            sl, ic, *_ = stats.linregress(x[tr], y[tr])
            err[f] = (y[f] - (sl * x[f] + ic)) ** 2
        return err

    err_base = _oos_err(e, l)
    err_pro = _oos_err(ep, l)
    ok = np.isfinite(err_base) & np.isfinite(err_pro)
    if ok.sum() < 12:
        return None
    rmse_base = float(np.sqrt(np.mean(err_base[ok])))
    rmse_pro = float(np.sqrt(np.mean(err_pro[ok])))

    rng = np.random.default_rng(seed)
    diffs = []
    eb, ep_ = err_base[ok], err_pro[ok]
    m = ok.sum()
    for _ in range(n):
        bi = rng.integers(0, m, m)
        diffs.append(np.sqrt(np.mean(eb[bi])) - np.sqrt(np.mean(ep_[bi])))
    lo = float(np.percentile(diffs, 2.5))
    hi = float(np.percentile(diffs, 97.5))
    return {
        "n": int(m),
        "rmse_base": round(rmse_base, 4),
        "rmse_pro": round(rmse_pro, 4),
        "delta_rmse": round(rmse_base - rmse_pro, 4),  # >0 = pro migliore
        "ci_lo": round(lo, 4),
        "ci_hi": round(hi, 4),
        "pro_better": bool(lo > 0),
    }


def _folds_by_cluster(cl: np.ndarray, k: int, rng) -> list[np.ndarray]:
    """Divide le RIGHE in k folds tenendo insieme le righe dello stesso cluster.

    Con un k-fold sulle righe lo stesso giocatore finisce in train e in test:
    e' lo stesso identico soggetto, e la retta viene stimata su un dato che poi
    deve prevedere. Qui i vintage peggiorano il problema, perche' i criteri sono
    finestre annidate - "tutto dopo la 25a" contiene "tutto dopo la 36a" - quindi
    il target delle due righe e' quasi lo stesso numero.

    Si campionano i CLUSTER, non le righe, come gia' fa il bootstrap accanto:
    due criteri diversi nella stessa funzione erano un'incoerenza, e quello
    ingenuo era proprio sotto il numero che il sito pubblica.
    """
    unici = np.unique(cl)
    ordine = rng.permutation(len(unici))
    gruppi = np.array_split(ordine, min(k, len(unici)))
    pos = {c: i for i, c in enumerate(unici)}
    appartenenza = np.array([pos[c] for c in cl])
    return [np.flatnonzero(np.isin(appartenenza, g)) for g in gruppi]


def paired_rmse_bootstrap_clustered(early: Sequence[float], late: Sequence[float],
                                    early_pro: Sequence[float],
                                    cluster: Sequence,
                                    n: int = 1000, seed: int = 42) -> dict | None:
    """
    Variante con CLUSTER BOOTSTRAP su un identificatore (es. giocatore_id) per
    gestire pseudo-replicazione: lo stesso giocatore in più vintage NON è
    un'osservazione indipendente. Resampling per cluster (giocatore), non per
    riga. Restituisce IC tipicamente PIÙ AMPIO ma onesto sul pseudo-replication.
    """
    e = np.asarray(early, dtype=float)
    ep = np.asarray(early_pro, dtype=float)
    l = np.asarray(late, dtype=float)
    cl = np.asarray(cluster)
    mask = np.isfinite(e) & np.isfinite(ep) & np.isfinite(l)
    e, ep, l, cl = e[mask], ep[mask], l[mask], cl[mask]
    if len(e) < 12:
        return None

    def _oos_err(x, y):
        rng_local = np.random.default_rng(seed)
        folds = _folds_by_cluster(cl, 5, rng_local)
        err = np.full(len(x), np.nan)
        for f in folds:
            tr = np.setdiff1d(np.arange(len(x)), f)
            if len(tr) < 3 or np.std(x[tr]) == 0:
                continue
            sl, ic, *_ = stats.linregress(x[tr], y[tr])
            err[f] = (y[f] - (sl * x[f] + ic)) ** 2
        return err

    err_base = _oos_err(e, l)
    err_pro = _oos_err(ep, l)
    ok = np.isfinite(err_base) & np.isfinite(err_pro)
    if ok.sum() < 12:
        return None
    rmse_base = float(np.sqrt(np.mean(err_base[ok])))
    rmse_pro = float(np.sqrt(np.mean(err_pro[ok])))

    # Cluster bootstrap: campiona cluster (giocatori) con replacement; per ogni
    # cluster prendi TUTTE le sue righe. Mantiene la struttura di dipendenza.
    eb, ep_, cl_ok = err_base[ok], err_pro[ok], cl[ok]
    clusters_unique = np.unique(cl_ok)
    # Mappa cluster_id → indici delle righe
    cluster_idx = {c: np.where(cl_ok == c)[0] for c in clusters_unique}

    rng = np.random.default_rng(seed)
    diffs = []
    for _ in range(n):
        sampled = rng.choice(clusters_unique, size=len(clusters_unique), replace=True)
        boot_rows = np.concatenate([cluster_idx[c] for c in sampled])
        diffs.append(np.sqrt(np.mean(eb[boot_rows])) - np.sqrt(np.mean(ep_[boot_rows])))
    lo = float(np.percentile(diffs, 2.5))
    hi = float(np.percentile(diffs, 97.5))
    return {
        "n": int(ok.sum()),
        "n_clusters": int(len(clusters_unique)),
        "rmse_base": round(rmse_base, 4),
        "rmse_pro": round(rmse_pro, 4),
        "delta_rmse": round(rmse_base - rmse_pro, 4),
        "ci_lo": round(lo, 4),
        "ci_hi": round(hi, 4),
        "pro_better": bool(lo > 0),
        "method": "cluster_bootstrap",
    }


def weight_sensitivity(z_by_dim: dict[str, Sequence[float]],
                       base_weights: dict[str, float],
                       pct: float = 0.20, n: int = 500, seed: int = 42,
                       top_k: tuple[int, int] = (10, 20)) -> dict | None:
    """
    Sensibilità del ranking alla scelta dei pesi (T2), calcolata ricombinando gli
    z-score per-dimensione del payload (NON rieseguendo il motore). Perturba ogni
    peso di ±pct in modo uniforme, rinormalizza, ricalcola il punteggio = Σ w·z e
    misura quanto il ranking resta stabile (Spearman e overlap Top-K vs base).

    'coverage' = quota del peso nominale rappresentata dalle dimensioni disponibili:
    se < 1 il test è parziale (mancano z-dim nel payload).
    """
    dims = [d for d in base_weights if d in z_by_dim and z_by_dim[d] is not None]
    if len(dims) < 2:
        return None
    cols = []
    for d in dims:
        v = np.asarray(z_by_dim[d], dtype=float)
        v = np.where(np.isfinite(v), v, 0.0)  # z mancante → 0 = media-ruolo
        cols.append(v)
    Z = np.column_stack(cols)
    if Z.shape[0] < 10:
        return None

    w0 = np.array([base_weights[d] for d in dims], dtype=float)
    w0n = w0 / w0.sum()
    base_score = Z @ w0n
    base_order = stats.rankdata(base_score)

    def _topset(score, k):
        return set(np.argsort(-score)[:k])

    base_top = {k: _topset(base_score, k) for k in top_k}
    rng = np.random.default_rng(seed)
    sp, ov = [], {k: [] for k in top_k}
    for _ in range(n):
        factor = 1.0 + rng.uniform(-pct, pct, size=len(dims))
        w = np.clip(w0 * factor, 1e-6, None)
        w = w / w.sum()
        s = Z @ w
        r = stats.spearmanr(base_order, stats.rankdata(s))[0]
        if r is not None and not np.isnan(r):
            sp.append(r)
        for k in top_k:
            ov[k].append(len(base_top[k] & _topset(s, k)) / k)

    cov = float(sum(base_weights[d] for d in dims) / sum(base_weights.values()))
    return {
        "n": int(Z.shape[0]),
        "dims": dims,
        "coverage": round(cov, 3),
        "pct": pct,
        "spearman_median": round(float(np.median(sp)), 4),
        "spearman_p05": round(float(np.percentile(sp, 5)), 4),
        "spearman_min": round(float(np.min(sp)), 4),
        "top_overlap": {str(k): round(float(np.median(ov[k])), 3) for k in top_k},
        "top_overlap_min": {str(k): round(float(np.min(ov[k])), 3) for k in top_k},
    }


def team_level_corr(team_tpi: dict, team_criterion: dict,
                    method: str = "spearman") -> dict | None:
    """
    Validità ecologica (T1): correla l'indice aggregato per squadra (es. media
    TPI dei giocatori) con un criterio reale di squadra (es. xG totale, punti).
    n = numero squadre (censo della lega, non campione → riportare con CI).
    """
    keys = [t for t in team_tpi if t in team_criterion
            and team_tpi[t] is not None and team_criterion[t] is not None]
    if len(keys) < 5:
        return None
    x = [team_tpi[t] for t in keys]
    y = [team_criterion[t] for t in keys]
    r = correlation(x, y, method)
    lo, hi = bootstrap_ci(x, y, method)
    rows = sorted(
        [{"squadra": t, "tpi": round(float(team_tpi[t]), 3),
          "criterio": round(float(team_criterion[t]), 3)} for t in keys],
        key=lambda d: d["tpi"], reverse=True,
    )
    return {"n": len(keys), "r": r, "ci_lo": lo, "ci_hi": hi, "rows": rows}
