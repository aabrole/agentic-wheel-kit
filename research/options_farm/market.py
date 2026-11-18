"""
market.py - option chain access + THE FILL MODEL. Part of the frozen rule set (hashed).

Contract selection (decision after the close of session d, entry at the open of t):
  * expiry: among cube expiries with E > t (and E >= min_expiry when given) that already
    have a bar on or before d, the one whose calendar DTE (E - t) is closest to the target;
    ties go to the later expiry.
  * delta: implied vol from every contract of that expiry that traded on d (close price,
    raw spot close of d, BS r=4.5%, no dividends); OTM side only (calls K >= 0.98 S,
    puts K <= 1.02 S); linear interpolation in ln(K/S), flat extrapolation. If < 2 points,
    pool points from expiries whose DTE is within 0.5x-2x. Delta for each listed strike at
    spot S0 (= close of d, or the open of t when the strategy decides at the open, PEAD).
  * candidates: |delta| within target +- 0.10, sorted by distance to target. Estimated
    price = close on d if it traded (and S0 is the close), else the BS price on the smile.
    The account takes the first candidate whose estimated cost fits its budget.

Fills (no historical quotes exist on this plan):
  * entry at the option's daily-bar OPEN on t. A bar exists only if volume > 0; with no bar
    on t the order slips to t+1's open; still none -> no fill.
  * fill price x (1 + h) on buys, x (1 - h) on sells, h = live-calibrated half-spread for the
    fill's (premium, moneyness, DTE) bucket (calibrate.Haircut), x stress multiplier.
  * fees $0.03 per contract per side (Robinhood $0 commission + regulatory fees).
  * exit rule 'hold' H: open of session entry+H (first later session with a bar; never past
    expiry or the period end).
  * exit rule 'tpsl' H: TP = 2x raw entry open, SL = 0.5x. On each bar from the entry bar
    on: open beyond a level -> exit at the open; else low <= SL -> exit at SL; else
    high >= TP -> exit at TP; both in one bar -> SL (conservative). Time stop as 'hold' H.
  * no bar before expiry: intrinsic value at the expiry-day raw close, sold at x (1 - h);
    OTM -> worthless (-100%). Period end with no bar: last close x (1 - h).
"""
from __future__ import annotations

from functools import lru_cache
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

import bs
import cube
import odata as D

FEE = 0.03                     # $ per contract per side
DELTA_TOL = 0.10
MAX_CANDIDATES = 8


class Calendar:
    def __init__(self):
        idx = D.stock_daily("SPY", adjustment="raw").index
        self.sessions = pd.DatetimeIndex(idx)
        self.pos = {d: i for i, d in enumerate(self.sessions)}

    def idx(self, d) -> int:
        return self.pos[pd.Timestamp(d)]

    def last_le(self, d) -> int:
        """Index of the last session on or before date d."""
        return int(self.sessions.searchsorted(pd.Timestamp(d), side="right") - 1)


CAL = Calendar()


class Underlying:
    """Everything for one underlying: raw + adjusted stock bars, option cube as arrays."""

    def __init__(self, u: str):
        self.u = u
        n = len(CAL.sessions)
        raw = D.stock_daily(u, adjustment="raw").reindex(CAL.sessions)
        adj = D.stock_daily(u, adjustment="split").reindex(CAL.sessions)
        self.raw_open = raw.open.to_numpy(); self.raw_close = raw.close.to_numpy()
        self.adj = adj
        b = cube.load(u)
        if b.empty:
            self.ok = False
            return
        self.ok = True
        b = b[b.date.isin(CAL.sessions)]
        syms = pd.Index(sorted(b.symbol.unique()))
        meta = b.drop_duplicates("symbol").set_index("symbol").reindex(syms)
        self.sym = syms
        self.strike = meta.strike.to_numpy(float)
        self.is_call = (meta.cp == "C").to_numpy()
        self.expiry = pd.DatetimeIndex(meta.expiry)
        self.exp_sidx = np.array([CAL.last_le(e) for e in self.expiry])   # session idx of expiry day
        b = b.assign(sid=syms.get_indexer(b.symbol), di=b.date.map(CAL.pos).astype(int))
        b = b.sort_values(["sid", "di"])
        self.b_sid = b.sid.to_numpy(); self.b_di = b.di.to_numpy()
        self.o = b.o.to_numpy(float); self.h = b.h.to_numpy(float)
        self.l = b.l.to_numpy(float); self.c = b.c.to_numpy(float); self.v = b.v.to_numpy(float)
        self.off = np.searchsorted(self.b_sid, np.arange(len(syms) + 1))
        # by-date view
        order = np.lexsort((self.b_sid, self.b_di))
        self.d_order = order
        self.d_di = self.b_di[order]
        self.d_off = np.searchsorted(self.d_di, np.arange(n + 1))
        # expiries
        self.expiries = pd.DatetimeIndex(sorted(set(self.expiry)))
        first = pd.Series(self.b_di).groupby(self.expiry[self.b_sid].values).min()
        self.exp_first_di = first.reindex(self.expiries).to_numpy()
        self.exp_members = {E: np.where(self.expiry == E)[0] for E in self.expiries}
        self._smile = {}

    # ------------------------------------------------------------------ bar helpers
    def bars_of(self, sid: int):
        a, z = self.off[sid], self.off[sid + 1]
        return self.b_di[a:z], a

    def bar_at(self, sid: int, di: int) -> Optional[int]:
        dis, a = self.bars_of(sid)
        k = np.searchsorted(dis, di)
        if k < len(dis) and dis[k] == di:
            return a + k
        return None

    def mark(self, sid: int, di: int) -> float:
        dis, a = self.bars_of(sid)
        k = np.searchsorted(dis, di, side="right") - 1
        return float(self.c[a + k]) if k >= 0 else 0.0

    def on_date(self, di: int) -> np.ndarray:
        return self.d_order[self.d_off[di]:self.d_off[di + 1]]

    # ------------------------------------------------------------------ smile
    def smile_points(self, di: int, E: pd.Timestamp):
        key = (di, E)
        if key in self._smile:
            return self._smile[key]
        rows = self.on_date(di)
        sids = self.b_sid[rows]
        m = self.expiry[sids] == E
        rows, sids = rows[m], sids[m]
        S = self.raw_close[di]
        out = (np.array([]), np.array([]))
        if len(rows) and np.isfinite(S):
            K = self.strike[sids]; ic = self.is_call[sids]; px = self.c[rows]
            otm = (ic & (K >= 0.98 * S)) | (~ic & (K <= 1.02 * S))
            K, ic, px = K[otm], ic[otm], px[otm]
            T = max((E - CAL.sessions[di]).days, 0.5) / 365
            iv = bs.implied_vol(px, S, K, T, ic)
            ok = np.isfinite(iv) & (iv > 0.03) & (iv < 5)
            if ok.sum():
                x = np.log(K[ok] / S)
                s = pd.Series(iv[ok]).groupby(np.round(x, 6)).median()
                out = (s.index.to_numpy(float), s.to_numpy(float))
        self._smile[key] = out
        return out

    def smile(self, di: int, E: pd.Timestamp):
        x, y = self.smile_points(di, E)
        if len(x) >= 2:
            return x, y
        # pool neighbours with similar DTE
        dte = max((E - CAL.sessions[di]).days, 1)
        xs, ys = [x], [y]
        for E2 in self.expiries:
            d2 = (E2 - CAL.sessions[di]).days
            if E2 != E and d2 > 0 and 0.5 * dte <= d2 <= 2 * dte:
                a, b = self.smile_points(di, E2)
                xs.append(a); ys.append(b)
        x = np.concatenate(xs); y = np.concatenate(ys)
        if len(x) < 2:
            return None
        o = np.argsort(x)
        s = pd.Series(y[o]).groupby(x[o]).median()
        return s.index.to_numpy(float), s.to_numpy(float)

    # ------------------------------------------------------------------ selection
    def choose_expiry(self, d_i: int, t_i: int, dte_target: int, min_expiry=None) -> Optional[pd.Timestamp]:
        t = CAL.sessions[t_i]
        ok = (self.expiries > t) & (self.exp_first_di <= d_i)
        if min_expiry is not None:
            ok &= self.expiries >= pd.Timestamp(min_expiry)
        if not ok.any():
            return None
        ex = self.expiries[ok]
        dist = np.abs((ex - t).days.to_numpy() - dte_target)
        best = dist.min()
        return ex[np.where(dist == best)[0][-1]]

    def candidates(self, d_i: int, t_i: int, is_call: bool, dte_target: int, delta_target: float,
                   spot_override: Optional[float] = None, min_expiry=None) -> List[dict]:
        E = self.choose_expiry(d_i, t_i, dte_target, min_expiry)
        if E is None:
            return []
        sm = self.smile(d_i, E)
        if sm is None:
            return []
        Sd = self.raw_close[d_i]
        S0 = Sd if spot_override is None else spot_override
        if not np.isfinite(S0):
            return []
        mem = self.exp_members[E]
        mem = mem[self.is_call[mem] == is_call]
        if not len(mem):
            return []
        K = self.strike[mem]
        iv = np.interp(np.log(K / S0), sm[0], sm[1])
        T = max((E - CAL.sessions[t_i]).days, 0.5) / 365
        dl = np.abs(bs.delta(S0, K, T, iv, is_call))
        dist = np.abs(dl - delta_target)
        keep = np.where(dist <= DELTA_TOL)[0]
        keep = keep[np.lexsort((K[keep], dist[keep]))][:MAX_CANDIDATES]
        out = []
        for k in keep:
            sid = int(mem[k])
            est = None
            if spot_override is None:
                r = self.bar_at(sid, d_i)
                if r is not None:
                    est = float(self.c[r])
            if est is None:
                est = float(bs.price(S0, K[k], T, iv[k], is_call))
            if not np.isfinite(est) or est < 0.01:
                continue
            out.append({"sid": sid, "symbol": self.sym[sid], "strike": float(K[k]), "expiry": E,
                        "delta": float(dl[k]), "est_px": est, "iv": float(iv[k]),
                        "dte": int((E - CAL.sessions[t_i]).days)})
        return out

    # ------------------------------------------------------------------ outcome
    def mny(self, sid: int, di: int) -> float:
        S = self.raw_open[di]
        if not np.isfinite(S):
            S = self.raw_close[di - 1]
        x = np.log(self.strike[sid] / S)
        return float(x if self.is_call[sid] else -x)

    def outcome(self, sid: int, t_i: int, rule: str, H: int, pe_i: int, hc, allow_slip: bool = True) -> Optional[dict]:
        dis, a = self.bars_of(sid)
        xi_E = int(self.exp_sidx[sid]); E = self.expiry[sid]
        k = np.searchsorted(dis, t_i)
        if k < len(dis) and dis[k] == t_i:
            ei = t_i; slip = False
        elif allow_slip and k < len(dis) and dis[k] == t_i + 1 and t_i + 1 < min(xi_E, pe_i):
            ei = t_i + 1; slip = True
        else:
            return None
        if ei >= min(xi_E, pe_i):
            return None                      # never open on expiry day or the period's last session
        r0 = a + k
        p0 = float(self.o[r0])
        if p0 <= 0:
            return None
        dte0 = (E - CAL.sessions[ei]).days
        h0 = hc(p0, self.mny(sid, ei), dte0)
        res = {"entry_i": ei, "entry_raw": p0, "entry_fill": p0 * (1 + h0), "slip": slip, "h_entry": h0}
        cap = min(ei + H, xi_E, pe_i)
        tp, sl = 2.0 * p0, 0.5 * p0
        ex_i, ex_raw, reason = None, None, None
        if rule == "tpsl":
            j = r0
            while j < a + len(dis) and self.b_di[j] < cap:
                di = self.b_di[j]
                o, hh, ll = self.o[j], self.h[j], self.l[j]
                if di != ei and o <= sl:
                    ex_i, ex_raw, reason = di, o, "SL_gap"
                elif di != ei and o >= tp:
                    ex_i, ex_raw, reason = di, o, "TP_gap"
                elif ll <= sl:
                    ex_i, ex_raw, reason = di, sl, "SL"
                elif hh >= tp:
                    ex_i, ex_raw, reason = di, tp, "TP"
                if ex_i is not None:
                    break
                j += 1
        if ex_i is None:
            lim = min(xi_E, pe_i)
            kk = np.searchsorted(dis, cap)
            if kk < len(dis) and dis[kk] <= lim:
                ex_i = int(dis[kk]); ex_raw = float(self.o[a + kk])
                reason = "TIME" if ex_i == ei + H else ("EXPIRY_DAY" if ex_i == xi_E else
                                                        ("PERIOD_END" if ex_i == pe_i else "TIME_SLIP"))
            elif pe_i < xi_E:
                ex_i = pe_i; ex_raw = self.mark(sid, pe_i); reason = "PERIOD_END_MARK"
            else:
                ex_i = xi_E
                S = self.raw_close[xi_E]
                K = self.strike[sid]
                intrinsic = max(0.0, (S - K) if self.is_call[sid] else (K - S)) if np.isfinite(S) else 0.0
                ex_raw = intrinsic; reason = "EXPIRED_ITM" if intrinsic > 0 else "EXPIRED_WORTHLESS"
        ex_raw = float(ex_raw)
        if ex_raw > 0:
            h1 = hc(ex_raw, self.mny(sid, ex_i), max((E - CAL.sessions[ex_i]).days, 0))
        else:
            h1 = 0.0
        res.update({"exit_i": int(ex_i), "exit_raw": ex_raw, "exit_fill": ex_raw * (1 - h1), "reason": reason,
                    "h_exit": h1})
        return res


_UND: Dict[str, Underlying] = {}


def U(u: str) -> Underlying:
    if u not in _UND:
        _UND[u] = Underlying(u)
    return _UND[u]
