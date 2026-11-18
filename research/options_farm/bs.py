"""Vectorized Black-Scholes (no dividends) for implied vol and delta from daily closes.
r is a flat 4.5% (roughly the 2024-2026 T-bill average). American early exercise ignored."""
from __future__ import annotations

import numpy as np
from scipy.special import ndtr

R = 0.045


def price(S, K, T, sig, is_call):
    S, K, T, sig = map(lambda x: np.asarray(x, float), (S, K, T, sig))
    T = np.maximum(T, 1e-6); sig = np.maximum(sig, 1e-6)
    sq = sig * np.sqrt(T)
    d1 = (np.log(S / K) + (R + 0.5 * sig * sig) * T) / sq
    d2 = d1 - sq
    disc = K * np.exp(-R * T)
    call = S * ndtr(d1) - disc * ndtr(d2)
    put = disc * ndtr(-d2) - S * ndtr(-d1)
    return np.where(is_call, call, put)


def delta(S, K, T, sig, is_call):
    S, K, T, sig = map(lambda x: np.asarray(x, float), (S, K, T, sig))
    T = np.maximum(T, 1e-6); sig = np.maximum(sig, 1e-6)
    d1 = (np.log(S / K) + (R + 0.5 * sig * sig) * T) / (sig * np.sqrt(T))
    return np.where(is_call, ndtr(d1), ndtr(d1) - 1.0)


def implied_vol(px, S, K, T, is_call, lo=0.01, hi=6.0, iters=60):
    """Bisection; NaN where the price is outside the no-arbitrage band."""
    px, S, K, T = map(lambda x: np.asarray(x, float), (px, S, K, T))
    is_call = np.asarray(is_call, bool)
    a = np.full(px.shape, lo); b = np.full(px.shape, hi)
    pa = price(S, K, T, a, is_call); pb = price(S, K, T, b, is_call)
    ok = (px > pa) & (px < pb) & np.isfinite(px)
    for _ in range(iters):
        m = 0.5 * (a + b)
        pm = price(S, K, T, m, is_call)
        up = pm < px
        a = np.where(up, m, a); b = np.where(up, b, m)
    iv = 0.5 * (a + b)
    return np.where(ok, iv, np.nan)
