"""
Portfolio performance reconstruction + analytics.

Trading 212 has no historical NAV / pie time-series endpoint, so we
reconstruct daily value curves from yfinance using the *current*
holdings: for each holding we pull ~1y of adjusted daily closes, convert
to GBP with daily FX, weight by the currently-owned quantity, and sum.
The reconstructed series is then anchored so its final point equals the
real T212 value reported live, which absorbs any unmapped holdings and
minor price/FX drift.

This yields, for both the whole account and each pie:
  - a daily % return curve (vs a GBP benchmark, VWRL.L),
  - a deep stats block (Sharpe/Sortino/Calmar, drawdowns, VaR/CVaR,
    beta/alpha, momentum, expected forward profit, etc).

It calls yfinance only (never Trading 212), reads current holdings from
the hub state the t212 adapter already populates, and caches the price
matrix to disk so restarts and re-runs are cheap.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import pickle
import sqlite3
import time
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src import config_user
from src.config import settings
from src.hub import Hub
from src.metrics import quant
from src.metrics.orders_history import build_quantity_timeline, daily_net_cashflow, get_orders

log = logging.getLogger("jarvis.performance")

BENCHMARK = "SPY"            # S&P 500 (total return via adj close), converted to GBP
BENCHMARK_LABEL = "S&P 500"
RF_ANNUAL = 0.04             # risk-free assumption for Sharpe (UK base ~ this)
LOOKBACK = "1y"
TRADING_DAYS = 252
REFRESH_SECONDS = 6 * 3600   # re-pull yfinance history every 6h
ANCHOR_REFRESH_SECONDS = 60  # re-anchor the live tip from T212 every minute

PRICE_CACHE = settings.jarvis_dir / "perf_prices.pkl"
INSTRUMENT_CACHE = settings.jarvis_dir / "t212_instruments.json"

# Trading 212 single-letter exchange suffixes -> Yahoo suffix.
EXCH_SUFFIX = {
    "l": ".L",    # London
    "d": ".DE",   # Xetra / Deutsche Boerse
    "p": ".PA",   # Paris
    "a": ".AS",   # Amsterdam
    "m": ".MC",   # Madrid
    "b": ".BR",   # Brussels
    "h": ".HE",   # Helsinki
    "s": ".SW",   # SIX Swiss
    "i": ".MI",   # Milan
    "z": ".ST",   # Stockholm (approx)
}

FX_PAIRS = {"USD": "GBPUSD=X", "EUR": "EURGBP=X"}


# ----------------------------------------------------------------------
# Ticker mapping
# ----------------------------------------------------------------------

def load_instruments() -> dict[str, dict]:
    if not INSTRUMENT_CACHE.exists():
        return {}
    try:
        data = json.loads(INSTRUMENT_CACHE.read_text())
        return {i["ticker"]: i for i in data}
    except Exception:
        log.exception("performance · failed to read instruments cache")
        return {}


def to_yahoo(raw_ticker: str, instr: dict) -> str | None:
    """Map a Trading 212 raw ticker to a Yahoo Finance symbol."""
    if not raw_ticker:
        return None
    short = (instr.get("shortName") or raw_ticker.split("_")[0]).strip()
    if not short:
        return None
    if raw_ticker.endswith("_US_EQ"):
        return short  # US listings have no Yahoo suffix
    # European single-letter exchange code immediately before _EQ
    if raw_ticker.endswith("_EQ"):
        core = raw_ticker[:-3]  # strip _EQ
        if core and core[-1].islower():
            suffix = EXCH_SUFFIX.get(core[-1])
            if suffix:
                return short + suffix
    return None


def ccy_to_gbp_scale(ccy: str) -> float:
    """Static minor-unit scale (FX handled separately)."""
    return 0.01 if ccy == "GBX" else 1.0


# ----------------------------------------------------------------------
# yfinance history (threaded; cached)
# ----------------------------------------------------------------------

def _fetch_history_sync(symbols: list[str]) -> dict[str, dict[str, float]]:
    """Return {symbol: {iso_date: adj_close}}. Runs in a worker thread."""
    import yfinance as yf

    out: dict[str, dict[str, float]] = {}
    if not symbols:
        return out
    try:
        df = yf.download(
            symbols,
            period=LOOKBACK,
            interval="1d",
            auto_adjust=True,
            progress=False,
            threads=True,
            repair=True,  # fixes Yahoo's 100x GBp/GBP and split glitches
        )
    except Exception:
        log.exception("performance · yfinance download failed")
        return out

    close = df["Close"] if "Close" in df else df
    # Single symbol -> Series; normalise to DataFrame
    if hasattr(close, "to_frame") and getattr(close, "ndim", 2) == 1:
        close = close.to_frame(name=symbols[0])

    for sym in close.columns:
        series = close[sym].dropna()
        if series.empty:
            continue
        out[str(sym)] = {ts.strftime("%Y-%m-%d"): float(v) for ts, v in series.items()}
    return out


def _sanitize_series(series: dict[str, float]) -> dict[str, float]:
    """Correct residual Yahoo GBp/GBP (100x) denomination outliers using the
    series median as the reference scale. Only exact factor-of-100 fixes are
    applied so legitimate large moves are preserved."""
    vals = [v for v in series.values() if v and v > 0]
    if len(vals) < 5:
        return series
    s = sorted(vals)
    med = s[len(s) // 2]
    if med <= 0:
        return series
    hi, lo = med * 20, med / 20
    out: dict[str, float] = {}
    for d, v in series.items():
        if v and v > 0:
            if v > hi and lo <= v / 100 <= hi:
                v = v / 100
            elif v < lo and lo <= v * 100 <= hi:
                v = v * 100
        out[d] = v
    return out


async def get_price_matrix(symbols: set[str]) -> dict[str, dict[str, float]]:
    """Disk-cached price history keyed by symbol. Refreshes on TTL or new symbols."""
    cache: dict[str, dict[str, float]] = {}
    fresh = False
    if PRICE_CACHE.exists():
        age = time.time() - PRICE_CACHE.stat().st_mtime
        fresh = age < REFRESH_SECONDS
        try:
            cache = pickle.loads(PRICE_CACHE.read_bytes())
        except Exception:
            cache = {}

    need = set(symbols)
    missing = need - set(cache.keys())
    if fresh and not missing:
        return {s: cache[s] for s in need if s in cache}

    to_fetch = sorted(need if not fresh else (need | set(cache.keys())))
    log.info("performance · fetching %d symbols from yfinance (cache_fresh=%s)", len(to_fetch), fresh)
    fetched: dict[str, dict[str, float]] = {}
    for i in range(0, len(to_fetch), 50):
        batch = to_fetch[i:i + 50]
        part = await asyncio.to_thread(_fetch_history_sync, batch)
        fetched.update(part)
    cache.update(fetched)
    try:
        PRICE_CACHE.write_bytes(pickle.dumps(cache))
    except Exception:
        log.exception("performance · price cache write failed")
    return {s: _sanitize_series(cache[s]) for s in need if s in cache}


# ----------------------------------------------------------------------
# Curve reconstruction
# ----------------------------------------------------------------------

def _align_dates(price_matrix: dict[str, dict[str, float]]) -> list[str]:
    dates: set[str] = set()
    for series in price_matrix.values():
        dates.update(series.keys())
    return sorted(dates)


def _series_on_dates(series: dict[str, float], dates: list[str]) -> list[float]:
    """Forward/backfill a per-symbol series onto the master date axis."""
    out: list[float] = []
    last: float | None = None
    for d in dates:
        v = series.get(d)
        if v is not None:
            last = v
        out.append(last if last is not None else math.nan)
    # back-fill the leading gap
    first_valid = next((x for x in out if not math.isnan(x)), None)
    if first_valid is not None:
        out = [first_valid if math.isnan(x) else x for x in out]
    return out


def reconstruct_curve(
    holdings: list[dict],          # [{yahoo, qty, ccy}]
    price_matrix: dict[str, dict[str, float]],
    fx: dict[str, list[float]],    # ccy -> per-date GBP factor on master axis
    dates: list[str],
    anchor_value: float,
) -> dict[str, Any]:
    """Build a GBP value curve, anchored so the last point == anchor_value."""
    if not dates:
        return {"curve": [], "coverage": 0.0}

    total = [0.0] * len(dates)
    covered_today = 0.0
    today_total = 0.0
    for h in holdings:
        sym = h.get("yahoo")
        series = price_matrix.get(sym) if sym else None
        if not series:
            continue
        px = _series_on_dates(series, dates)
        if math.isnan(px[-1]):
            continue
        qty = float(h.get("qty", 0) or 0)
        ccy = h.get("ccy", "")
        scale = ccy_to_gbp_scale(ccy)
        fx_series = fx.get(ccy if ccy != "GBX" else "GBP")
        for i, p in enumerate(px):
            f = fx_series[i] if fx_series else 1.0
            total[i] += qty * p * scale * f
        covered_today += h.get("market_value", 0.0) or 0.0
        today_total += qty * px[-1] * scale * (fx.get(ccy if ccy != "GBX" else "GBP", [1.0])[-1])

    if today_total <= 0:
        return {"curve": [], "coverage": 0.0}

    k = (anchor_value / today_total) if anchor_value and today_total else 1.0
    base = total[0] * k if total[0] else (anchor_value if anchor_value else 1.0)
    if base <= 0:
        base = next((v * k for v in total if v > 0), 1.0)

    curve = []
    for d, v in zip(dates, total, strict=False):
        val = v * k
        curve.append({"t": d, "v": round(val, 2), "pct": round((val / base - 1) * 100, 3)})

    declared_mv = sum((h.get("market_value", 0.0) or 0.0) for h in holdings) or 1.0
    coverage = min(1.0, covered_today / declared_mv) if declared_mv else 0.0
    return {"curve": curve, "coverage": round(coverage, 3)}


def reconstruct_timevarying(
    qty_by_ticker: dict[str, list[float]],   # raw_ticker -> qty per date
    instr: dict[str, dict],
    price_matrix: dict[str, dict[str, float]],
    fx: dict[str, list[float]],
    dates: list[str],
    anchor_value: float,
) -> dict[str, Any]:
    """GBP portfolio-value curve using the per-date quantity timeline, anchored
    so the final point equals the live holdings value."""
    if not dates:
        return {"curve": [], "coverage": 0.0}
    n = len(dates)
    total = [0.0] * n
    today_total = 0.0
    for rt, qty_arr in qty_by_ticker.items():
        meta = instr.get(rt, {})
        sym = to_yahoo(rt, meta)
        series = price_matrix.get(sym) if sym else None
        if not series:
            continue
        px = _series_on_dates(series, dates)
        if math.isnan(px[-1]):
            continue
        ccy = meta.get("currencyCode", "")
        scale = ccy_to_gbp_scale(ccy)
        fx_series = fx.get(ccy if ccy != "GBX" else "GBP")
        for i in range(n):
            f = fx_series[i] if fx_series else 1.0
            total[i] += qty_arr[i] * px[i] * scale * f
        f_last = (fx.get(ccy if ccy != "GBX" else "GBP", [1.0]) or [1.0])[-1]
        today_total += qty_arr[-1] * px[-1] * scale * f_last

    if today_total <= 0:
        return {"curve": [], "coverage": 0.0}
    k = (anchor_value / today_total) if anchor_value and today_total else 1.0
    curve = [{"t": d, "v": round(v * k, 2)} for d, v in zip(dates, total, strict=False)]
    coverage = min(1.0, today_total / anchor_value) if anchor_value else 1.0
    return {"curve": curve, "coverage": round(coverage, 3)}


def sp_same_cashflow(net_flows: list[float], sp_series: dict[str, float],
                     fx: dict[str, list[float]], dates: list[str]) -> list[dict]:
    """'If the same cash had gone into the S&P 500' value curve, in GBP."""
    if not sp_series:
        return []
    px = _series_on_dates(sp_series, dates)
    usd = fx.get("USD")
    gbp_px = [px[i] * (usd[i] if usd else 1.0) for i in range(len(dates))]
    units = 0.0
    out = []
    for i, d in enumerate(dates):
        p = gbp_px[i]
        if p and not math.isnan(p):
            flow = net_flows[i] if i < len(net_flows) else 0.0
            units += flow / p
        out.append({"t": d, "v": round(units * (p if p and not math.isnan(p) else 0.0), 2)})
    return out


def benchmark_curve(price_matrix: dict[str, dict[str, float]], dates: list[str],
                    fx: dict[str, list[float]] | None = None) -> list[dict]:
    """S&P 500 total-return curve, converted to GBP so a GBP investor sees the
    return they'd actually have earned (price move + USD/GBP drift)."""
    series = price_matrix.get(BENCHMARK)
    if not series:
        return []
    px = _series_on_dates(series, dates)
    usd = (fx or {}).get("USD")
    vals = [p * (usd[i] if usd else 1.0) for i, p in enumerate(px)]
    base = next((x for x in vals if not math.isnan(x) and x > 0), None)
    if not base:
        return []
    return [{"t": d, "pct": round((v / base - 1) * 100, 3)} for d, v in zip(dates, vals, strict=False)]


# ----------------------------------------------------------------------
# Statistics
# ----------------------------------------------------------------------

def _daily_returns(values: list[float]) -> list[float]:
    out = []
    for i in range(1, len(values)):
        if values[i - 1]:
            out.append(values[i] / values[i - 1] - 1)
    return out


def _pct(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    k = (len(s) - 1) * q
    lo, hi = math.floor(k), math.ceil(k)
    if lo == hi:
        return s[int(k)]
    return s[lo] * (hi - k) + s[hi] * (k - lo)


def _period_return(curve: list[dict], lookback_days: int) -> float | None:
    if len(curve) < 2:
        return None
    end = curve[-1]["v"]
    idx = max(0, len(curve) - 1 - lookback_days)
    start = curve[idx]["v"]
    if not start:
        return None
    return round((end / start - 1) * 100, 2)


def _max_drawdown(values: list[float]) -> tuple[float, float, int]:
    peak = values[0] if values else 0.0
    max_dd = 0.0
    cur_dd = 0.0
    longest = 0
    run = 0
    for v in values:
        if v > peak:
            peak = v
            run = 0
        else:
            run += 1
            longest = max(longest, run)
        dd = (v / peak - 1) if peak else 0.0
        max_dd = min(max_dd, dd)
        cur_dd = dd
    return max_dd * 100, cur_dd * 100, longest


def compute_stats(curve: list[dict], bench: list[dict]) -> dict:
    """Full analytics block for a reconstructed curve."""
    if len(curve) < 3:
        return {"insufficient": True}

    values = [p["v"] for p in curve]
    rets = _daily_returns(values)
    n = len(rets)
    if n < 2:
        return {"insufficient": True}

    mean = sum(rets) / n
    var = sum((r - mean) ** 2 for r in rets) / (n - 1)
    std = math.sqrt(var)
    ann = math.sqrt(TRADING_DAYS)
    vol_ann = std * ann

    total_ret = values[-1] / values[0] - 1
    years = n / TRADING_DAYS
    cagr = (values[-1] / values[0]) ** (1 / years) - 1 if years > 0 and values[0] > 0 else 0.0

    sharpe = (cagr - RF_ANNUAL) / vol_ann if vol_ann else 0.0
    neg = [r for r in rets if r < 0]
    downside = math.sqrt(sum(r * r for r in neg) / len(neg)) * ann if neg else 0.0
    sortino = (cagr - RF_ANNUAL) / downside if downside else 0.0

    max_dd, cur_dd, dd_days = _max_drawdown(values)
    calmar = cagr / abs(max_dd / 100) if max_dd else 0.0

    var95 = -_pct(rets, 0.05) * 100
    var99 = -_pct(rets, 0.01) * 100
    p5 = _pct(rets, 0.05)
    tail = [r for r in rets if r <= p5]
    cvar95 = -(sum(tail) / len(tail)) * 100 if tail else 0.0

    best = max(rets)
    worst = min(rets)
    best_i = rets.index(best)
    worst_i = rets.index(worst)
    win_rate = sum(1 for r in rets if r > 0) / n * 100

    # skew / excess-aware kurtosis (normal == 3 convention)
    skew = kurt = 0.0
    if std > 0:
        m3 = sum((r - mean) ** 3 for r in rets) / n
        m4 = sum((r - mean) ** 4 for r in rets) / n
        skew = m3 / std ** 3
        kurt = m4 / std ** 4

    # Beta / alpha / correlation vs benchmark
    beta = alpha = corr = r2 = None
    if bench:
        bvals = [p["pct"] for p in bench]
        # rebuild benchmark levels then daily returns, aligned to curve length
        blevels = [1 + x / 100 for x in bvals]
        brets = _daily_returns(blevels)
        m = min(len(rets), len(brets))
        rp, rb = rets[-m:], brets[-m:]
        if m >= 2:
            mb = sum(rb) / m
            mp = sum(rp) / m
            cov = sum((rp[i] - mp) * (rb[i] - mb) for i in range(m)) / m
            vb = sum((x - mb) ** 2 for x in rb) / m
            vp = sum((x - mp) ** 2 for x in rp) / m
            if vb > 0:
                beta = cov / vb
                alpha = (mp - beta * mb) * TRADING_DAYS * 100  # annualised %
            if vp > 0 and vb > 0:
                corr = cov / math.sqrt(vp * vb)
                r2 = corr ** 2

    # Momentum: 12-1 (skip last month), plus trailing windows
    mom_12_1 = None
    if len(values) > TRADING_DAYS:
        a = values[-TRADING_DAYS]
        b = values[-21]
        if a:
            mom_12_1 = round((b / a - 1) * 100, 2)

    # Forward expectation (lognormal projection, 1y horizon)
    v0 = values[-1]
    mu_d = math.log(values[-1] / values[0]) / n if values[0] > 0 else 0.0
    sig_d = std
    exp_ret = math.exp(mu_d * TRADING_DAYS) - 1
    exp_value = v0 * math.exp(mu_d * TRADING_DAYS)
    band = sig_d * ann
    exp_low = v0 * math.exp(mu_d * TRADING_DAYS - band)
    exp_high = v0 * math.exp(mu_d * TRADING_DAYS + band)

    def rounded(x, d=2):
        return round(x, d) if x is not None else None

    return {
        "start_date": curve[0]["t"],
        "end_date": curve[-1]["t"],
        "n_days": n,
        "current_value": round(v0, 2),
        "total_return_pct": round(total_ret * 100, 2),
        "cagr_pct": round(cagr * 100, 2),
        "avg_daily_return_pct": round(mean * 100, 4),
        "ann_return_pct": round(cagr * 100, 2),
        "vol_ann_pct": round(vol_ann * 100, 2),
        "downside_dev_pct": round(downside * 100, 2),
        "sharpe": round(sharpe, 2),
        "sortino": round(sortino, 2),
        "calmar": round(calmar, 2),
        "max_dd_pct": round(max_dd, 2),
        "current_dd_pct": round(cur_dd, 2),
        "max_dd_days": dd_days,
        "var_95_pct": round(var95, 2),
        "var_99_pct": round(var99, 2),
        "cvar_95_pct": round(cvar95, 2),
        "best_day_pct": round(best * 100, 2),
        "best_day": curve[best_i + 1]["t"],
        "worst_day_pct": round(worst * 100, 2),
        "worst_day": curve[worst_i + 1]["t"],
        "win_rate_pct": round(win_rate, 1),
        "up_days": sum(1 for r in rets if r > 0),
        "down_days": sum(1 for r in rets if r < 0),
        "skew": round(skew, 2),
        "kurtosis": round(kurt, 2),
        "beta": rounded(beta),
        "alpha_ann_pct": rounded(alpha),
        "correlation": rounded(corr),
        "r_squared": rounded(r2),
        "ret_1w_pct": _period_return(curve, 5),
        "ret_1m_pct": _period_return(curve, 21),
        "ret_3m_pct": _period_return(curve, 63),
        "ret_6m_pct": _period_return(curve, 126),
        "ret_1y_pct": _period_return(curve, TRADING_DAYS),
        "momentum_12_1_pct": mom_12_1,
        "exp_return_1y_pct": round(exp_ret * 100, 2),
        "exp_value_1y": round(exp_value, 2),
        "exp_profit_1y": round(exp_value - v0, 2),
        "exp_value_1y_low": round(exp_low, 2),
        "exp_value_1y_high": round(exp_high, 2),
        "rf_assumption_pct": round(RF_ANNUAL * 100, 1),
    }


# ----------------------------------------------------------------------
# Holdings leaderboard
# ----------------------------------------------------------------------

def holdings_leaderboard(
    positions: list[dict],
    instr: dict[str, dict],
    price_matrix: dict[str, dict[str, float]],
    dates: list[str],
) -> list[dict]:
    out = []
    for p in positions:
        raw = p.get("raw_ticker", "")
        sym = to_yahoo(raw, instr.get(raw, {}))
        series = price_matrix.get(sym) if sym else None
        rets = {}
        if series:
            px = _series_on_dates(series, dates)
            mini = [{"v": v} for v in px]
            for label, days in (("1w", 5), ("1m", 21), ("3m", 63), ("6m", 126), ("1y", TRADING_DAYS)):
                idx = max(0, len(px) - 1 - days)
                if px[idx]:
                    val = (px[-1] / px[idx] - 1) * 100
                    rets[label] = round(val, 2) if abs(val) < 800 else None  # drop data-glitch outliers
        out.append({
            "ticker": p.get("ticker"),
            "name": p.get("name"),
            "nav_pct": p.get("nav_pct"),
            "return_pct": p.get("return_pct"),  # since purchase (from T212)
            "ppl": p.get("ppl"),
            "ret_1w": rets.get("1w"),
            "ret_1m": rets.get("1m"),
            "ret_3m": rets.get("3m"),
            "ret_6m": rets.get("6m"),
            "ret_1y": rets.get("1y"),
        })
    return out


# ----------------------------------------------------------------------
# SQLite forward record (durable history beyond the yfinance window)
# ----------------------------------------------------------------------

def _init_value_table() -> None:
    settings.jarvis_dir.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(settings.db_path)) as con:
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS portfolio_value (
                day        TEXT PRIMARY KEY,
                value      REAL NOT NULL,
                invested   REAL,
                source     TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS nav_snapshot (
                ts        TEXT PRIMARY KEY,
                nav_pct   REAL NOT NULL,
                source    TEXT NOT NULL
            );
            """
        )
        con.commit()


def _record_value(value: float, invested: float) -> None:
    try:
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        with closing(sqlite3.connect(settings.db_path)) as con:
            con.execute(
                "INSERT OR REPLACE INTO portfolio_value (day, value, invested, source) VALUES (?, ?, ?, 't212')",
                (day, value, invested),
            )
            con.commit()
    except Exception:
        log.exception("performance · value record failed")


# ----------------------------------------------------------------------
# Holdings extraction from hub state
# ----------------------------------------------------------------------

def overall_holdings(hub: Hub, instr: dict[str, dict]) -> list[dict]:
    holdings = []
    for p in hub.state.get("t212_positions", []):
        raw = p.get("raw_ticker", "")
        sym = to_yahoo(raw, instr.get(raw, {}))
        holdings.append({
            "yahoo": sym,
            "qty": p.get("qty", 0),
            "ccy": p.get("currency", ""),
            "market_value": p.get("market_value", 0.0),
        })
    return holdings


def pie_holdings(pie: dict, instr: dict[str, dict]) -> list[dict]:
    holdings = []
    for s in pie.get("slices", []):
        raw = s.get("raw_ticker", "")
        meta = instr.get(raw, {})
        sym = to_yahoo(raw, meta)
        holdings.append({
            "yahoo": sym,
            "qty": s.get("owned_qty", 0),
            "ccy": meta.get("currencyCode", ""),
            "market_value": s.get("value", 0.0),
        })
    return holdings


def needed_symbols(hub: Hub, instr: dict[str, dict]) -> set[str]:
    syms: set[str] = {BENCHMARK}
    for h in overall_holdings(hub, instr):
        if h["yahoo"]:
            syms.add(h["yahoo"])
    for pie in hub.state.get("t212_pies", []):
        for h in pie_holdings(pie, instr):
            if h["yahoo"]:
                syms.add(h["yahoo"])
    return syms


def order_symbols(orders: list[dict], instr: dict[str, dict]) -> set[str]:
    """Yahoo symbols for tickers that appear in history (incl. since-sold)."""
    syms: set[str] = set()
    for o in orders:
        rt = o.get("raw_ticker", "")
        sym = to_yahoo(rt, instr.get(rt, {}))
        if sym:
            syms.add(sym)
    return syms


def build_fx(price_matrix: dict[str, dict[str, float]], dates: list[str]) -> dict[str, list[float]]:
    """Per-date GBP conversion factor for each currency on the master axis."""
    fx: dict[str, list[float]] = {"GBP": [1.0] * len(dates)}
    # USD: 1 USD = 1/GBPUSD GBP
    usd = price_matrix.get(FX_PAIRS["USD"])
    if usd:
        gbpusd = _series_on_dates(usd, dates)
        fx["USD"] = [(1.0 / x if x else 1.0) for x in gbpusd]
    # EUR: EURGBP is already GBP per EUR
    eur = price_matrix.get(FX_PAIRS["EUR"])
    if eur:
        fx["EUR"] = _series_on_dates(eur, dates)
    return fx


# ----------------------------------------------------------------------
# Main loop
# ----------------------------------------------------------------------

async def run(hub: Hub) -> None:
    log.info("performance · starting")
    _init_value_table()
    instr = load_instruments()
    last_full = 0.0

    # Wait until the t212 adapter has populated positions ...
    for _ in range(60):
        if hub.state.get("t212_positions"):
            break
        await asyncio.sleep(2)
    # ... and give pies a chance to arrive (their detail calls are rate-limited)
    for _ in range(60):
        if hub.state.get("t212_pies"):
            break
        await asyncio.sleep(2)

    if not instr:
        instr = load_instruments()

    while True:
        try:
            positions = hub.state.get("t212_positions", [])
            pies = hub.state.get("t212_pies", [])
            account = hub.state.get("t212_account", {})

            if not positions:
                await asyncio.sleep(ANCHOR_REFRESH_SECONDS)
                continue

            # Only do the (cache-backed) full reconstruction when needed:
            # on TTL, on first run, or when pies finally show up after the
            # overall curve was already built. Otherwise just re-anchor.
            have_overall = bool((hub.state.get("performance_overall") or {}).get("curve"))
            pies_pending = bool(pies) and not hub.state.get("performance_pies")
            need_full = (time.time() - last_full >= REFRESH_SECONDS) or not have_overall or pies_pending
            if not need_full:
                await _reanchor_tip(hub)
                await asyncio.sleep(ANCHOR_REFRESH_SECONDS)
                continue

            orders = await get_orders()
            syms = needed_symbols(hub, instr) | order_symbols(orders, instr)
            price_matrix = await get_price_matrix(syms)
            # include FX pairs (cached together)
            fx_syms = set(FX_PAIRS.values())
            if not (fx_syms & set(price_matrix.keys())):
                price_matrix.update(await get_price_matrix(fx_syms))

            dates = _align_dates({k: v for k, v in price_matrix.items() if k not in FX_PAIRS.values()})
            if not dates:
                log.warning("performance · no price dates available yet")
                await asyncio.sleep(REFRESH_SECONDS)
                continue

            fx = build_fx(price_matrix, dates)
            bench = benchmark_curve(price_matrix, dates, fx)

            # ---- holdings leaderboard (also feeds attribution) ----
            lb = holdings_leaderboard(positions, instr, price_matrix, dates)
            hub.replace("performance_holdings", lb)

            # ---- overall: time-varying holdings from order history ----
            holdings = overall_holdings(hub, instr)
            # Anchor to the real account value (total - cash). The per-position
            # market_value from T212 can over-sum, so don't trust its total here.
            _tv = float(account.get("total_value", 0) or 0)
            _fc = float(account.get("free_cash", 0) or 0)
            anchor = (_tv - _fc) if _tv > 0 else sum((h.get("market_value", 0.0) or 0.0) for h in holdings)
            qty_tl = build_quantity_timeline(positions, orders, dates)
            rec = reconstruct_timevarying(qty_tl, instr, price_matrix, fx, dates, anchor)
            value_curve = rec["curve"]                     # [{t, v}] GBP value over time
            net_flows = daily_net_cashflow(orders, dates)
            sp_equiv = sp_same_cashflow(net_flows, price_matrix.get(BENCHMARK, {}), fx, dates)

            # Risk analytics + Monte Carlo describe the CURRENT portfolio over the
            # trailing year (a stable basis). The account is young and deposit-heavy,
            # so time-weighting the short funded window would explode when annualised;
            # the value curve above already shows the true cashflow-driven history.
            cur = reconstruct_curve(holdings, price_matrix, fx, dates, anchor)
            cur_curve = cur["curve"]
            overall_stats = compute_stats(cur_curve, bench)
            overall_stats["basis"] = "current portfolio · trailing 1Y"
            overall_stats["coverage_pct"] = round(rec["coverage"] * 100, 1)
            overall_stats["invested"] = round(account.get("invested", 0) or 0, 2)
            overall_stats["total_value"] = round(account.get("total_value", 0) or 0, 2)
            overall_stats["holdings_value"] = round(value_curve[-1]["v"], 2) if value_curve else 0.0
            overall_stats["net_invested"] = round(sum(net_flows), 2)
            overall_stats["actual_pnl"] = round(float(account.get("ppl", 0) or 0), 2)
            overall_stats["sp_equiv_value"] = round(sp_equiv[-1]["v"], 2) if sp_equiv else None
            overall_stats["vs_sp_gbp"] = (round(overall_stats["holdings_value"] - overall_stats["sp_equiv_value"], 2)
                                          if sp_equiv else None)
            overall_stats["cash_pct"] = round(
                (account.get("free_cash", 0) or 0) / (account.get("total_value", 1) or 1) * 100, 1
            )
            # forward expectation in real GBP, from current value + current-portfolio drift
            hv = overall_stats["holdings_value"]
            er = overall_stats.get("exp_return_1y_pct")
            vol = overall_stats.get("vol_ann_pct")
            if hv and er is not None:
                overall_stats["exp_value_1y"] = round(hv * (1 + er / 100), 2)
                overall_stats["exp_profit_1y"] = round(hv * er / 100, 2)
                if vol is not None:
                    mu, sig = math.log1p(er / 100), vol / 100
                    overall_stats["exp_value_1y_low"] = round(hv * math.exp(mu - sig), 2)
                    overall_stats["exp_value_1y_high"] = round(hv * math.exp(mu + sig), 2)
            hub.replace("performance_overall", {
                "curve": value_curve,
                "sp_equiv": sp_equiv,
                "benchmark": bench,
                "benchmark_label": BENCHMARK_LABEL,
                "stats": overall_stats,
                "method": (f"value reconstructed from {len(orders)} orders · analytics on current portfolio"
                           if orders else "current holdings (no order history)"),
                "updated": datetime.now(timezone.utc).isoformat(),
            })

            # ---- quantitative analytics (Monte Carlo + benchmark-relative) ----
            port_rets = [cur_curve[i]["v"] / cur_curve[i - 1]["v"] - 1
                         for i in range(1, len(cur_curve)) if cur_curve[i - 1]["v"]]
            blevels = [1 + b["pct"] / 100 for b in bench]
            bench_rets = [blevels[i] / blevels[i - 1] - 1 for i in range(1, len(blevels)) if blevels[i - 1]]
            hub.replace("performance_quant", {
                "monte_carlo": quant.mc_from_cfg(port_rets, overall_stats["holdings_value"], config_user.monte_carlo()),
                "metrics": quant.advanced_metrics(port_rets, bench_rets, [p.get("nav_pct") for p in positions]),
                "attribution": quant.attribution(lb),
                "benchmark_label": BENCHMARK_LABEL,
                "updated": datetime.now(timezone.utc).isoformat(),
            })

            # ---- per pie (current-holdings basis; % return) ----
            # Full quant block (compute_stats) + per-pie Monte Carlo + predicted
            # profit + allocation drift + contribution to the whole portfolio.
            hv_total = (_tv - _fc) if _tv > 0 else sum(
                float(p.get("current_value", 0) or 0) for p in pies)
            pies_out = []
            for pie in pies:
                ph = pie_holdings(pie, instr)
                p_anchor = float(pie.get("current_value", 0) or 0)
                prec = reconstruct_curve(ph, price_matrix, fx, dates, p_anchor)
                stats = compute_stats(prec["curve"], bench)
                stats["coverage_pct"] = round(prec["coverage"] * 100, 1)

                p_rets = [prec["curve"][i]["v"] / prec["curve"][i - 1]["v"] - 1
                          for i in range(1, len(prec["curve"])) if prec["curve"][i - 1]["v"]]
                p_mc = quant.mc_from_cfg(p_rets, p_anchor, config_user.monte_carlo()) if p_anchor > 0 else {"insufficient": True}
                pred_profit = prob_profit = None
                if p_mc and not p_mc.get("insufficient"):
                    pred_profit = round(p_mc["terminal"]["mean"] - p_anchor, 2)
                    prob_profit = p_mc.get("prob_profit")

                # rebalance gap = half the sum of |expected - current| slice weights
                drift = sum(abs((s.get("expected", 0) or 0) - (s.get("current", 0) or 0))
                            for s in pie.get("slices", [])) / 2.0

                pies_out.append({
                    "id": pie.get("id"),
                    "name": pie.get("name"),
                    "current_value": p_anchor,
                    "invested": pie.get("invested"),
                    "result_pct": pie.get("result_pct"),
                    "result_abs": pie.get("result_abs"),
                    "div_gained": pie.get("div_gained"),
                    "slice_count": len(pie.get("slices", [])),
                    "curve": prec["curve"],
                    "stats": stats,
                    "monte_carlo": p_mc,
                    "predicted_profit_1y": pred_profit,
                    "prob_profit": prob_profit,
                    "rebalance_gap_pct": round(drift, 1),
                    "contribution_pct": round(p_anchor / hv_total * 100, 1) if hv_total else None,
                })
            hub.replace("performance_pies", pies_out)

            # ---- durable forward record ----
            _record_value(
                float(account.get("total_value", 0) or 0),
                float(account.get("invested", 0) or 0),
            )

            last_full = time.time()
            log.info(
                "performance · published · pts=%d pies=%d coverage=%.0f%% orders=%d value=%.0f sp_equiv=%s",
                len(value_curve), len(pies_out), rec["coverage"] * 100, len(orders),
                overall_stats["holdings_value"], overall_stats.get("sp_equiv_value"),
            )
        except Exception:
            log.exception("performance · cycle failed")

        # Between full refreshes we wake every minute (handled at loop top) to
        # re-anchor the live tip; that keeps the chart tracking the account.
        await asyncio.sleep(ANCHOR_REFRESH_SECONDS)


async def _reanchor_tip(hub: Hub) -> None:
    """Cheap intraday update: move the final curve point to the live T212 value
    so the graph visibly tracks the account between full yfinance refreshes."""
    perf = hub.state.get("performance_overall")
    if not perf or not perf.get("curve"):
        return
    account = hub.state.get("t212_account", {})
    positions = hub.state.get("t212_positions", [])
    tv = float(account.get("total_value", 0) or 0)
    fc = float(account.get("free_cash", 0) or 0)
    live_holdings_mv = (tv - fc) if tv > 0 else sum((p.get("market_value", 0.0) or 0.0) for p in positions)
    curve = perf["curve"]
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    tip = {"t": today, "v": round(live_holdings_mv, 2)}  # overall curve is GBP value
    if curve and curve[-1]["t"] == today:
        curve[-1] = tip
    else:
        curve.append(tip)
        del curve[: max(0, len(curve) - (TRADING_DAYS + 2))]
    hub.broadcast("performance_overall.replace", perf)

    # also re-anchor each pie tip
    pies = hub.state.get("performance_pies", [])
    live_pies = {p.get("id"): p for p in hub.state.get("t212_pies", [])}
    for pp in pies:
        lp = live_pies.get(pp["id"])
        if not lp or not pp.get("curve"):
            continue
        cv = pp["curve"]
        b = cv[0]["v"] or 1.0
        val = float(lp.get("current_value", 0) or 0)
        if val <= 0:
            continue
        t = {"t": today, "v": round(val, 2), "pct": round((val / b - 1) * 100, 3)}
        if cv[-1]["t"] == today:
            cv[-1] = t
        else:
            cv.append(t)
            del cv[: max(0, len(cv) - (TRADING_DAYS + 2))]
    hub.broadcast("performance_pies.replace", pies)
