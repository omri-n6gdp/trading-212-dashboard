"""
Trading 212 read-only adapter — full surface.

Endpoints used (all GET, all read-only):
  /equity/account/cash             1/2s
  /equity/account/info             1/2s
  /equity/portfolio                1/5s
  /equity/pies                     1/2s
  /equity/pies/{id}                1/2s
  /equity/metadata/instruments     1/30s (disk-cached 1h)
  /equity/metadata/exchanges       1/30s (disk-cached 1h)
  /equity/history/orders           6/min
  /history/dividends               6/min
  /history/transactions            6/min

Auth: Basic base64(key:secret).
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from src.config import settings
from src.hub import Hub

log = logging.getLogger("jarvis.t212")

BASE_URL = "https://live.trading212.com/api/v0"

INSTRUMENT_CACHE = settings.jarvis_dir / "t212_instruments.json"
EXCHANGE_CACHE = settings.jarvis_dir / "t212_exchanges.json"


class T212Client:
    def __init__(self, api_key: str, api_secret: str = "") -> None:
        if api_secret:
            cred = base64.b64encode(f"{api_key}:{api_secret}".encode()).decode()
            auth = f"Basic {cred}"
        else:
            auth = api_key
        self.client = httpx.AsyncClient(
            base_url=BASE_URL,
            headers={"Authorization": auth, "User-Agent": "claude-jarvis/0.1"},
            timeout=20.0,
        )

    async def close(self) -> None:
        await self.client.aclose()

    async def _get(self, path: str, params: dict | None = None) -> Any:
        r = await self.client.get(path, params=params)
        r.raise_for_status()
        return r.json()

    async def cash(self) -> dict:
        return await self._get("/equity/account/cash")

    async def info(self) -> dict:
        return await self._get("/equity/account/info")

    async def portfolio(self) -> list[dict]:
        return await self._get("/equity/portfolio")

    async def pies(self) -> list[dict]:
        return await self._get("/equity/pies")

    async def pie(self, pid: int) -> dict:
        return await self._get(f"/equity/pies/{pid}")

    async def dividends(self, limit: int = 50) -> dict:
        return await self._get("/history/dividends", {"limit": limit})

    async def transactions(self, limit: int = 50) -> dict:
        return await self._get("/history/transactions", {"limit": limit})

    async def orders_history(self, limit: int = 50) -> dict:
        return await self._get("/equity/history/orders", {"limit": limit})

    async def _cached(self, path: str, cache: Path, ttl: int = 3600) -> Any:
        if cache.exists() and time.time() - cache.stat().st_mtime < ttl:
            return json.loads(cache.read_text())
        data = await self._get(path)
        try:
            cache.write_text(json.dumps(data))
        except Exception:
            log.exception("t212 · cache write failed: %s", cache)
        return data

    async def instruments(self) -> list[dict]:
        return await self._cached("/equity/metadata/instruments", INSTRUMENT_CACHE)

    async def exchanges(self) -> list[dict]:
        return await self._cached("/equity/metadata/exchanges", EXCHANGE_CACHE)


# ---------- Shaping helpers ----------

def shape_position(p: dict, ins_by_ticker: dict[str, dict], total_value: float) -> dict:
    raw_ticker = p.get("ticker", "")
    qty = float(p.get("quantity", 0) or 0)
    avg_price = float(p.get("averagePrice", 0) or 0)
    cur_price = float(p.get("currentPrice", 0) or 0)
    ppl = float(p.get("ppl", 0) or 0)
    fx_ppl = float(p.get("fxPpl", 0) or 0)
    initial = p.get("initialFillDate")
    source = p.get("frontend", "")

    instrument = ins_by_ticker.get(raw_ticker, {})
    short = (instrument.get("shortName") or raw_ticker.split("_")[0])[:8]
    name = instrument.get("name") or short
    inst_type = instrument.get("type", "")  # STOCK, ETF, etc
    inst_ccy = instrument.get("currencyCode") or ""
    is_lse = raw_ticker.endswith("l_EQ") or "_LON" in raw_ticker
    is_us = raw_ticker.endswith("_US_EQ")
    geo = "UK" if is_lse else ("US" if is_us else (inst_ccy or "OTHER"))

    # GBX prices are pence — convert to GBP for the wallet currency
    ccy_scale = 0.01 if inst_ccy == "GBX" else 1.0
    market_value = qty * cur_price * ccy_scale
    nav_pct = (market_value / total_value * 100) if total_value else 0.0
    cost_basis = qty * avg_price * ccy_scale
    return_pct = ((cur_price - avg_price) / avg_price * 100) if avg_price else 0.0
    pnl_bps = (ppl / cost_basis * 10000) if cost_basis else 0

    return {
        "ticker": short,
        "raw_ticker": raw_ticker,
        "name": (name or "")[:48],
        "exchange": "LSE" if is_lse else ("US" if is_us else "T212"),
        "geo": geo,
        "type": inst_type,
        "currency": inst_ccy,
        "source": source,
        "initial_fill": initial,
        "nav_pct": round(nav_pct, 3),
        "return_pct": round(return_pct, 2),
        "pnl_bps": int(round(pnl_bps)),
        "ppl": round(ppl, 2),
        "fx_ppl": round(fx_ppl, 2),
        "market_value": round(market_value, 2),
        "cost_basis": round(cost_basis, 2),
        "qty": qty,
        "avg_price": avg_price,
        "current_price": cur_price,
        "day_pct": round(return_pct, 2),
        "spark": [],
    }


def derive_breakdowns(positions: list[dict]) -> dict:
    """Aggregate exposures by currency, geo, asset-type, source."""
    by_ccy: dict[str, float] = defaultdict(float)
    by_geo: dict[str, float] = defaultdict(float)
    by_type: dict[str, float] = defaultdict(float)
    by_source: dict[str, float] = defaultdict(float)
    fx_pnl_by_ccy: dict[str, float] = defaultdict(float)
    pnl_by_geo: dict[str, float] = defaultdict(float)
    total_mv = sum(p["market_value"] for p in positions) or 1
    for p in positions:
        by_ccy[p["currency"] or "?"] += p["market_value"]
        by_geo[p["geo"]] += p["market_value"]
        by_type[p["type"] or "?"] += p["market_value"]
        by_source[p["source"] or "?"] += p["market_value"]
        fx_pnl_by_ccy[p["currency"] or "?"] += p["fx_ppl"]
        pnl_by_geo[p["geo"]] += p["ppl"]

    def pct(d: dict[str, float]) -> list[dict]:
        return sorted(
            [{"label": k, "value": round(v, 2), "pct": round(v / total_mv * 100, 2)} for k, v in d.items()],
            key=lambda x: x["value"], reverse=True,
        )

    # Concentration (Herfindahl)
    shares = [p["market_value"] / total_mv for p in positions]
    hhi = sum(s * s for s in shares) * 10000  # 0–10,000
    top5_pct = sum(sorted(shares, reverse=True)[:5]) * 100

    return {
        "by_currency": pct(by_ccy),
        "by_geography": pct(by_geo),
        "by_asset_type": pct(by_type),
        "by_source": pct(by_source),
        "fx_pnl_by_currency": [{"label": k, "value": round(v, 2)} for k, v in fx_pnl_by_ccy.items()],
        "pnl_by_geography": [{"label": k, "value": round(v, 2)} for k, v in pnl_by_geo.items()],
        "concentration_hhi": round(hhi, 1),
        "top5_weight_pct": round(top5_pct, 2),
        "position_count": len(positions),
    }


def shape_dividend(d: dict) -> dict:
    return {
        "ticker": (d.get("ticker") or "").split("_")[0],
        "name": (d.get("instrument") or {}).get("name", ""),
        "amount": float(d.get("amount") or 0),
        "currency": d.get("currency") or "",
        "gross_per_share": float(d.get("grossAmountPerShare") or 0),
        "quantity": float(d.get("quantity") or 0),
        "paid_on": d.get("paidOn") or d.get("dateTime") or "",
        "type": d.get("type") or "",
    }


def shape_transaction(t: dict) -> dict:
    return {
        "type": t.get("type") or "",
        "amount": float(t.get("amount") or 0),
        "currency": t.get("currency") or "",
        "datetime": t.get("dateTime") or "",
    }


def shape_order(o: dict) -> dict:
    order = o.get("order") or {}
    fill = o.get("fill") or {}
    wallet = (fill.get("walletImpact") or {})
    return {
        "id": order.get("id"),
        "ticker": (order.get("ticker") or "").split("_")[0],
        "raw_ticker": order.get("ticker"),
        "name": (order.get("instrument") or {}).get("name", ""),
        "side": order.get("side") or "",
        "type": order.get("type") or "",
        "status": order.get("status") or "",
        "strategy": order.get("strategy") or "",
        "source": order.get("initiatedFrom") or "",
        "value": float(order.get("filledValue") or order.get("value") or 0),
        "currency": order.get("currency") or "GBP",
        "created_at": order.get("createdAt") or "",
        "filled_at": fill.get("filledAt") or "",
        "filled_qty": float(fill.get("quantity") or 0),
        "fill_price": float(fill.get("price") or 0),
        "fx_rate": float(wallet.get("fxRate") or 0),
    }


def shape_pie(p: dict, detail: dict | None = None) -> dict:
    result = p.get("result") or {}
    div = p.get("dividendDetails") or {}
    out = {
        "id": p.get("id"),
        "cash": float(p.get("cash") or 0),
        "invested": float(result.get("priceAvgInvestedValue") or 0),
        "current_value": float(result.get("priceAvgValue") or 0),
        "result_abs": float(result.get("priceAvgResult") or 0),
        "result_pct": round(float(result.get("priceAvgResultCoef") or 0) * 100, 2),
        "div_gained": float(div.get("gained") or 0),
        "div_reinvested": float(div.get("reinvested") or 0),
        "div_in_cash": float(div.get("inCash") or 0),
        "progress": p.get("progress"),
        "status": p.get("status"),
        "slices": [],
    }
    if detail and isinstance(detail.get("instruments"), list):
        for ins in detail["instruments"]:
            r = ins.get("result") or {}
            out["slices"].append({
                "ticker": (ins.get("ticker") or "").split("_")[0],
                "raw_ticker": ins.get("ticker"),
                "expected": float(ins.get("expectedShare") or 0) * 100,
                "current": float(ins.get("currentShare") or 0) * 100,
                "owned_qty": float(ins.get("ownedQuantity") or 0),
                "invested": float(r.get("priceAvgInvestedValue") or 0),
                "value": float(r.get("priceAvgValue") or 0),
                "result_pct": round(float(r.get("priceAvgResultCoef") or 0) * 100, 2),
                "result_abs": float(r.get("priceAvgResult") or 0),
            })
        if detail.get("settings"):
            out["name"] = detail["settings"].get("name", "")
            out["goal"] = detail["settings"].get("goal")
        if "name" not in out:
            out["name"] = f"Pie {out['id']}"
    else:
        out["name"] = f"Pie {out['id']}"
    return out


# ---------- Top-level adapter task ----------

async def run(hub: Hub) -> None:
    if not settings.trading212_api_key:
        log.info("t212 · TRADING212_API_KEY not set, adapter idle")
        return

    log.info("t212 · starting (full-surface)")
    client = T212Client(settings.trading212_api_key, settings.trading212_api_secret)
    connected = False
    ins_by_ticker: dict[str, dict] = {}
    pie_detail_cache: dict[int, dict] = {}
    cycle = 0

    try:
        # one-shot: account info + metadata
        try:
            info = await client.info()
            hub.update("t212_account", {"id": info.get("id"), "currency": info.get("currencyCode", "GBP")})
        except Exception:
            log.exception("t212 · info fetch failed")

        await asyncio.sleep(0.5)

        try:
            ins = await client.instruments()
            ins_by_ticker = {i["ticker"]: i for i in ins}
            log.info("t212 · loaded %d instruments", len(ins_by_ticker))
        except Exception:
            log.exception("t212 · instruments fetch failed")

        await asyncio.sleep(0.5)

        try:
            exchanges = await client.exchanges()
            hub.replace("t212_exchanges", exchanges)
        except Exception:
            log.exception("t212 · exchanges fetch failed")

        while True:
            try:
                # ----- cash + portfolio every cycle (~30s) -----
                cash = await client.cash()
                total_value = float(cash.get("total", 0) or 0)
                hub.update("t212_account", {
                    "total_value": total_value,
                    "free_cash": float(cash.get("free", 0) or 0),
                    "invested": float(cash.get("invested", 0) or 0),
                    "ppl": float(cash.get("ppl", 0) or 0),
                    "result": float(cash.get("result", 0) or 0),
                    "pie_cash": float(cash.get("pieCash", 0) or 0),
                    "blocked": float(cash.get("blocked", 0) or 0),
                })

                await asyncio.sleep(0.8)
                portfolio = await client.portfolio()
                positions = [shape_position(p, ins_by_ticker, total_value) for p in portfolio]
                # Reconcile to account ccy (GBP). T212 `ppl` is in GBP and reliable;
                # `return_pct` is currency-neutral. So cost_basis_GBP = ppl / (return_pct/100)
                # whenever return_pct is non-trivial. For tiny / zero-return positions, fall
                # back to a per-ccy FX factor derived from positions where we have a clean read.
                for p in positions:
                    rp = p["return_pct"]
                    if abs(rp) > 0.05 and p["ppl"] != 0:
                        cb_gbp = p["ppl"] / (rp / 100)
                        if cb_gbp > 0:
                            p["cost_basis"] = round(cb_gbp, 2)
                            p["market_value"] = round(cb_gbp + p["ppl"], 2)
                            p["_reconciled"] = True
                # Derive FX factor per currency from reconciled rows
                fx_by_ccy: dict[str, list[float]] = defaultdict(list)
                for p in positions:
                    if p.get("_reconciled") and p["qty"] > 0 and p["current_price"] > 0:
                        ccy_scale = 0.01 if p["currency"] == "GBX" else 1.0
                        implied_fx = p["market_value"] / (p["qty"] * p["current_price"] * ccy_scale)
                        fx_by_ccy[p["currency"]].append(implied_fx)
                fx_factor = {c: (sum(v) / len(v)) for c, v in fx_by_ccy.items() if v}
                # Apply FX factor to non-reconciled rows
                for p in positions:
                    if not p.get("_reconciled"):
                        f = fx_factor.get(p["currency"], 1.0)
                        p["market_value"] = round(p["market_value"] * f, 2)
                        p["cost_basis"] = round(p["cost_basis"] * f, 2)
                    p["nav_pct"] = round(p["market_value"] / total_value * 100, 3) if total_value else 0
                    p.pop("_reconciled", None)
                positions.sort(key=lambda r: r["nav_pct"], reverse=True)
                hub.replace("t212_positions", positions)
                merge_equities(hub, "t212", positions)

                # Breakdowns
                hub.replace("t212_breakdowns", derive_breakdowns(positions))

                # Account-level KPIs from positions
                top = positions[0] if positions else None
                worst = min(positions, key=lambda p: p["ppl"]) if positions else None
                best = max(positions, key=lambda p: p["ppl"]) if positions else None
                hub.update("t212_account", {
                    "positions": len(positions),
                    "top_ticker": top["ticker"] if top else "—",
                    "top_pct": top["nav_pct"] if top else 0,
                    "best_ticker": best["ticker"] if best else "—",
                    "best_pnl": best["ppl"] if best else 0,
                    "worst_ticker": worst["ticker"] if worst else "—",
                    "worst_pnl": worst["ppl"] if worst else 0,
                    "total_fx_ppl": round(sum(p["fx_ppl"] for p in positions), 2),
                })

                if not connected:
                    hub.update("system", {"venues_connected": _bump_venues(hub, +1)})
                    connected = True

                # ----- pies (cycle 0, every 5 cycles) -----
                # /equity/pies/{id} is rate-limited to ~1 per 5s, so space the
                # detail calls and cache last-good detail to keep names/slices.
                if cycle % 5 == 0:
                    await asyncio.sleep(1.0)
                    try:
                        pies_raw = await client.pies()
                        pies_out: list[dict] = []
                        for p in pies_raw:
                            await asyncio.sleep(5.0)
                            detail = pie_detail_cache.get(p["id"])
                            try:
                                detail = await client.pie(p["id"])
                                if isinstance(detail, dict) and detail.get("instruments"):
                                    pie_detail_cache[p["id"]] = detail
                                else:
                                    detail = pie_detail_cache.get(p["id"])
                            except Exception:
                                detail = pie_detail_cache.get(p["id"])
                            pies_out.append(shape_pie(p, detail))
                        hub.replace("t212_pies", pies_out)
                    except Exception:
                        log.exception("t212 · pies fetch failed")

                # ----- dividends (every 3 cycles) -----
                if cycle % 3 == 0:
                    await asyncio.sleep(0.8)
                    try:
                        divs = await client.dividends(limit=50)
                        items = [shape_dividend(d) for d in divs.get("items", [])]
                        hub.replace("t212_dividends", items)
                        # 30-day run-rate
                        from datetime import timedelta
                        cutoff = datetime.now(timezone.utc) - timedelta(days=30)
                        last30 = 0.0
                        for d in items:
                            try:
                                ts = datetime.fromisoformat(d["paid_on"].replace("Z", "+00:00"))
                                if ts >= cutoff:
                                    last30 += d["amount"]
                            except Exception:
                                pass
                        hub.update("t212_account", {
                            "div_30d": round(last30, 2),
                            "div_annualized": round(last30 * 12, 2),
                        })
                    except Exception:
                        log.exception("t212 · dividends fetch failed")

                # ----- transactions (every 3 cycles) -----
                if cycle % 3 == 0:
                    await asyncio.sleep(0.8)
                    try:
                        txs = await client.transactions(limit=50)
                        items = [shape_transaction(t) for t in txs.get("items", [])]
                        hub.replace("t212_transactions", items)
                        deposits = sum(t["amount"] for t in items if t["type"] in ("DEPOSIT", "TRANSFER") and t["amount"] > 0)
                        withdrawals = sum(-t["amount"] for t in items if t["type"] == "WITHDRAWAL" or t["amount"] < 0)
                        hub.update("t212_account", {
                            "deposits_lifetime": round(deposits, 2),
                            "withdrawals_lifetime": round(withdrawals, 2),
                            "net_deposits": round(deposits - withdrawals, 2),
                        })
                    except Exception:
                        log.exception("t212 · transactions fetch failed")

                # ----- order history (every 2 cycles) -----
                if cycle % 2 == 0:
                    await asyncio.sleep(0.8)
                    try:
                        oh = await client.orders_history(limit=50)
                        items = [shape_order(o) for o in oh.get("items", [])]
                        hub.replace("t212_orders", items)
                        # Activity per day (last 14 days)
                        per_day: dict[str, int] = defaultdict(int)
                        vol_per_day: dict[str, float] = defaultdict(float)
                        for o in items:
                            try:
                                day = o["filled_at"][:10] if o["filled_at"] else o["created_at"][:10]
                                if day:
                                    per_day[day] += 1
                                    vol_per_day[day] += o["value"]
                            except Exception:
                                pass
                        days_sorted = sorted(per_day.keys())[-14:]
                        hub.replace("t212_activity", [
                            {"day": d, "count": per_day[d], "volume": round(vol_per_day[d], 2)}
                            for d in days_sorted
                        ])
                    except Exception:
                        log.exception("t212 · orders fetch failed")

                cycle += 1
            except httpx.HTTPStatusError as e:
                code = e.response.status_code
                if code == 401:
                    log.error("t212 · 401 — bad key, stopping")
                    return
                log.warning("t212 · HTTP %s: %s", code, e.response.text[:200])
            except Exception:
                log.exception("t212 · cycle failed")

            await asyncio.sleep(30)
    finally:
        await client.close()


def merge_equities(hub: Hub, source: str, rows: list[dict]) -> None:
    sources: dict = hub.state.setdefault("_equities_sources", {})
    sources[source] = rows
    merged: dict[str, dict] = {}
    for src_rows in sources.values():
        for r in src_rows:
            existing = merged.get(r["ticker"])
            if not existing or r["nav_pct"] > existing["nav_pct"]:
                merged[r["ticker"]] = r
    out = sorted(merged.values(), key=lambda r: r["nav_pct"], reverse=True)
    hub.replace("equities", out)


def _bump_venues(hub: Hub, delta: int) -> int:
    cur = hub.state.get("system", {}).get("venues_connected", 0)
    total = hub.state.get("system", {}).get("venues_total", 4)
    return max(0, min(total, cur + delta))


__all__ = ["run", "merge_equities", "T212Client"]
