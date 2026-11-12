# Claude Jarvis — Handover Package

**For:** Claude Code, picking up an existing mock trading dashboard and wiring it to real data sources.

**Owner:** Omri (London, Imperial MPhys, paper-trading via Alpaca, FastAPI + vectorbt backend already in flight).

---

## ⟢ 2026-05 Rebuild (current focus)

The project has evolved from the original "Claude Jarvis" algo-trading mock into
a **live Trading 212 portfolio analytics dashboard**. The active plan is:

- **`07_FEATURES.md`** — the 100-feature target catalogue.
- **`08_PERSONALIZATION.md`** — how the tool is generalized to any T212 account
  via an editable config file.
- **`05_ROADMAP.md` → "Rebuild roadmap"** — the build order.
- **`02_ARCHITECTURE.md` → "Rebuild architecture"** — new backend modules + API.

Headline principle: **predicted profit** is the lead metric at portfolio, pie,
and stock level. Backend + data first, then **three** distinct frontend designs
to choose from. The older sections below describe the original concept and are
kept for context.

---

## What's in this folder

Read these in order. Each doc is short and direct.

| File | Purpose |
|---|---|
| `01_BRIEF.md` | What we're building and what already exists |
| `02_ARCHITECTURE.md` | Suggested system design — pre-decided so you don't burn turns on it |
| `03_DATA_SOURCES.md` | **The big one.** Every UI panel mapped to a real data source, with auth, endpoints, free vs paid |
| `04_UI_CONTRACT.md` | Exact JSON shapes the frontend expects per panel — implement to these |
| `05_ROADMAP.md` | Phased build order (start with what's free + easy) |
| `06_SETUP.md` | Local environment, dependencies, how to run |
| `reference/claude-jarvis-mock.html` | The current mock (1474 lines). Visual + behavioral reference. Modify in place. |
| `backend/` | Minimal FastAPI skeleton — adapters folder, schemas, WS hub stubs |

## TL;DR for the impatient

1. Read `01_BRIEF.md` and `02_ARCHITECTURE.md` (~5 min).
2. Skim `03_DATA_SOURCES.md` and `05_ROADMAP.md` for scope.
3. Start with **Phase 1** in the roadmap: crypto perps and Polymarket. Both are free, no auth, and high-impact visually.
4. The mock HTML stays as the frontend. Replace its `setInterval` simulations with WebSocket subscriptions to the FastAPI backend, panel by panel.

## Hard constraints

- **Paper trading only.** Live keys are fine for read-only; never wire anything to place real orders without explicit confirmation from Omri.
- **No real currency amounts displayed anywhere.** All P&L stays in percentages or basis points. This is a deliberate UI choice — preserve it.
- **No emojis in code, comments, or UI.** The existing mock is emoji-free for a reason.
- **Trading 212 has no public API.** Don't waste cycles trying to wire the ISA. See `03_DATA_SOURCES.md` for the manual-entry workaround.

## Quick context on the operator

Omri has built FastAPI services before. He's iterated through 12+ versions of a quarter-car suspension analysis script. He prefers direct, terse responses and iterative refinement over long upfront planning. Default to writing code; ask before architectural pivots.
