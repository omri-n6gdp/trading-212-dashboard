"""
User personalization config — the single source of truth for everything
personal/display.

Read from ~/.jarvis/jarvis.config.json, merged over built-in defaults so a
missing or partial file still works. Seeded on first run from the example
template (backend/data/jarvis.config.example.json).

Nothing here identifies an account: the account IS the Trading 212 API key in
.env. This file only carries display + analytics preferences, so the whole
dashboard generalizes to any account by swapping the key and (optionally)
editing this file. See 08_PERSONALIZATION.md.
"""
from __future__ import annotations

import json
import logging
from copy import deepcopy
from pathlib import Path
from typing import Any

from src.config import settings

log = logging.getLogger("jarvis.userconfig")

CONFIG_PATH = settings.jarvis_dir / "jarvis.config.json"
EXAMPLE_PATH = Path(__file__).parent.parent / "data" / "jarvis.config.example.json"

DEFAULTS: dict[str, Any] = {
    "display_name": "My Portfolio",
    "base_currency": None,  # null -> use the account currency from the API
    "benchmarks": [
        {"symbol": "SPY", "label": "S&P 500", "currency": "USD"},
        {"symbol": "VWRL.L", "label": "All-World", "currency": "GBP"},
    ],
    "primary_benchmark": "SPY",
    "risk_free_rate": 0.04,
    "monte_carlo": {
        "horizon_days": 252,
        "n_sims": 4000,
        "engine": "bootstrap",  # "bootstrap" | "gbm"
        "seed": 7,
        "gain_threshold_pct": 20,
        "loss_threshold_pct": 20,
        # Drift taming so predictions are defensible (see quant.monte_carlo):
        # target_annual_drift = shrink * historical + (1-shrink) * baseline, clipped.
        "drift_shrink": 0.45,
        "drift_baseline": 0.05,
        "drift_clip_lo": -0.35,
        "drift_clip_hi": 0.40,
    },
    "goal_value": None,
    "ranking": {
        "headline": "predicted_profit",
        "score_weights": {
            "momentum": 0.25,
            "risk_adjusted": 0.25,
            "trend": 0.20,
            "low_volatility": 0.15,
            "value": 0.15,
        },
    },
    "thematic_tags": {},
    "notes": {},
    "theme": "elevated",
}

_cache: dict[str, Any] | None = None
_mtime: float = -1.0


def _deep_merge(base: dict, over: dict) -> dict:
    out = deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _seed_if_missing() -> None:
    if CONFIG_PATH.exists():
        return
    try:
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        if EXAMPLE_PATH.exists():
            CONFIG_PATH.write_text(EXAMPLE_PATH.read_text())
        else:
            CONFIG_PATH.write_text(json.dumps(DEFAULTS, indent=2))
        log.info("userconfig · seeded %s", CONFIG_PATH)
    except Exception:
        log.exception("userconfig · seed failed")


def get_config() -> dict[str, Any]:
    """Merged config (defaults <- file). Re-reads when the file's mtime changes."""
    global _cache, _mtime
    _seed_if_missing()
    try:
        m = CONFIG_PATH.stat().st_mtime if CONFIG_PATH.exists() else 0.0
    except Exception:
        m = 0.0
    if _cache is None or m != _mtime:
        user: dict = {}
        try:
            if CONFIG_PATH.exists():
                user = json.loads(CONFIG_PATH.read_text())
        except Exception:
            log.exception("userconfig · read failed; using defaults")
            user = {}
        _cache = _deep_merge(DEFAULTS, user)
        _mtime = m
    return _cache


# ---- convenience accessors ----

def display_name() -> str:
    return get_config().get("display_name") or "My Portfolio"


def base_currency(account_ccy: str = "GBP") -> str:
    return get_config().get("base_currency") or account_ccy or "GBP"


def benchmarks() -> list[dict]:
    return get_config().get("benchmarks") or []


def primary_benchmark() -> dict:
    cfg = get_config()
    pid = cfg.get("primary_benchmark")
    for b in cfg.get("benchmarks", []):
        if b.get("symbol") == pid:
            return b
    bms = cfg.get("benchmarks") or [{"symbol": "SPY", "label": "S&P 500", "currency": "USD"}]
    return bms[0]


def risk_free_rate() -> float:
    try:
        return float(get_config().get("risk_free_rate") or 0.0)
    except Exception:
        return 0.04


def monte_carlo() -> dict:
    return get_config().get("monte_carlo") or {}


def goal_value() -> float | None:
    g = get_config().get("goal_value")
    try:
        return float(g) if g else None
    except Exception:
        return None


def score_weights() -> dict:
    return (get_config().get("ranking") or {}).get("score_weights") or {}


def headline_metric() -> str:
    return (get_config().get("ranking") or {}).get("headline") or "predicted_profit"


def thematic_tags() -> dict:
    # normalise tag -> set of upper-case tickers
    raw = get_config().get("thematic_tags") or {}
    return {k: [str(t).upper() for t in (v or [])] for k, v in raw.items()}


def notes() -> dict:
    return get_config().get("notes") or {}


def theme() -> str:
    return get_config().get("theme") or "elevated"


def public_config() -> dict:
    """Non-secret config echoed to the frontend so it can render labels/theme."""
    c = get_config()
    return {
        "display_name": c.get("display_name"),
        "base_currency": c.get("base_currency"),
        "benchmarks": c.get("benchmarks"),
        "primary_benchmark": c.get("primary_benchmark"),
        "risk_free_rate": c.get("risk_free_rate"),
        "monte_carlo": c.get("monte_carlo"),
        "goal_value": c.get("goal_value"),
        "ranking": c.get("ranking"),
        "thematic_tags": c.get("thematic_tags"),
        "notes": c.get("notes"),
        "theme": c.get("theme"),
    }


def set_theme(theme_id: str) -> None:
    """Persist the chosen frontend design id (the only UI state saved server-side)."""
    global _cache
    _seed_if_missing()
    try:
        cur: dict = {}
        if CONFIG_PATH.exists():
            cur = json.loads(CONFIG_PATH.read_text())
        cur["theme"] = theme_id
        CONFIG_PATH.write_text(json.dumps(cur, indent=2))
        _cache = None  # force reload on next access
    except Exception:
        log.exception("userconfig · set_theme failed")


__all__ = [
    "get_config", "public_config", "set_theme", "display_name", "base_currency",
    "benchmarks", "primary_benchmark", "risk_free_rate", "monte_carlo",
    "goal_value", "score_weights", "headline_metric", "thematic_tags", "notes", "theme",
]
