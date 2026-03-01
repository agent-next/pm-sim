# pm-sim — Polymarket Paper Trading Simulator

**Date:** 2026-02-28
**Status:** Approved (v2 — 1:1 faithful execution)
**Author:** Claude + Robert

## 1. Overview

A CLI paper trading simulator for Polymarket, designed for AI agent benchmarking.
Agents call `pm-sim` via shell commands and receive JSON responses. No wallet, no
real money — but execution matches real Polymarket EXACTLY.

### Core Principles

- **1:1 faithful**: Order book execution, real fees, real slippage — no shortcuts
- **Agent benchmark**: Multiple agents each get $10k, compare real-world-equivalent results
- **Agent-first**: All output is JSON by default, machine-parseable
- **Zero wallet**: Only uses Polymarket public APIs (Gamma + CLOB), no authentication
- **Auditable**: Every trade logged in SQLite with full provenance

### What "1:1 Faithful" Means

| Aspect | Our Simulator | Real Polymarket | Match? |
|--------|--------------|-----------------|:------:|
| Execution price | Walk real order book level-by-level | Walk real order book | YES |
| Slippage | Based on actual book depth | Based on actual book depth | YES |
| Fees | Fetched per-market from `/fee-rate` | Per-market fee_rate_bps | YES |
| Fee formula | `(bps/10000) * min(p, 1-p) * size` | Same formula | YES |
| Order types | FOK (all-or-nothing), FAK (partial) | FOK, FAK, GTC, GTD | Subset |
| Buy semantics | Amount in USD → receive shares | Same | YES |
| Sell semantics | Amount in shares → receive USD | Same | YES |
| Tick size | Enforced per-market | Enforced per-market | YES |
| Min order size | Enforced | Enforced | YES |
| Resolution | Payout $1/share for winning outcome | Same | YES |

## 2. Architecture

```
Agent (LLM / script / OpenClaw / Claude Code)
    │  shell subprocess
    ▼
pm-sim CLI (Click)
    │
    ├── cli.py         ─── Command definitions, JSON envelope
    ├── engine.py      ─── Trade execution: order book walk, fee calc, P&L
    ├── orderbook.py   ─── Order book simulation: fill logic, slippage calc
    ├── api.py         ─── Polymarket HTTP client (Gamma + CLOB)
    ├── db.py          ─── SQLite operations, schema
    └── models.py      ─── Dataclasses (Market, Trade, Position, Fill)
    │
    ├── Read  ─→ HTTP GET Polymarket API (market data cached 5min, prices NEVER cached)
    └── Write ─→ SQLite at ~/.pm-sim/paper.db
```

## 3. CLI Interface

```
pm-sim [--data-dir PATH] [--output json|table] <command>
```

### Account Management

```bash
pm-sim init [--balance 10000]              # Initialize paper account
pm-sim balance                              # Show cash + total value
pm-sim reset [--confirm]                    # Wipe all data
```

### Market Data

```bash
pm-sim markets list [--limit 20] [--sort volume|liquidity]
pm-sim markets search <query> [--limit 10]
pm-sim markets get <slug-or-id>             # Full detail incl. fee rate, tick size
pm-sim price <slug-or-id>                   # YES/NO midpoints + spread
pm-sim book <slug-or-id> [--depth 10]       # Order book with depth
pm-sim sync                                 # Pre-warm cache
```

### Trading (1:1 faithful simulation)

```bash
# BUY: spend USD, receive shares (walks ask side of book)
pm-sim buy <slug-or-id> <yes|no> <amount-usd> [--type fok|fak]

# SELL: sell shares, receive USD (walks bid side of book)
pm-sim sell <slug-or-id> <yes|no> <shares> [--type fok|fak]
```

**Key: buy amount is in USD, sell amount is in SHARES** — matches Polymarket exactly.

- `--type fok` (default): Fill entire order or cancel (all-or-nothing)
- `--type fak`: Fill what's available, cancel remainder (partial fills)

### Portfolio & History

```bash
pm-sim portfolio                            # Positions + unrealized P&L (live prices)
pm-sim history [--limit 50]                 # Trade log with fill details
pm-sim performance                          # Win rate, P&L, Sharpe, drawdown
```

### Market Resolution

```bash
pm-sim resolve <slug-or-id>                 # Settle one market
pm-sim resolve --all                        # Settle all resolved markets
```

### JSON Output Format

```json
{"ok": true, "data": { ... }}
{"ok": false, "error": "...", "code": "INSUFFICIENT_BALANCE"}
```

Error codes: `NOT_INITIALIZED`, `INSUFFICIENT_BALANCE`, `MARKET_NOT_FOUND`,
`MARKET_CLOSED`, `NO_POSITION`, `INVALID_OUTCOME`, `ORDER_REJECTED` (insufficient
liquidity for FOK), `TICK_SIZE_VIOLATION`, `API_ERROR`

## 4. Data Model

### SQLite Schema

```sql
CREATE TABLE account (
    id INTEGER PRIMARY KEY DEFAULT 1,
    starting_balance REAL NOT NULL DEFAULT 10000,
    cash REAL NOT NULL DEFAULT 10000,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    CHECK (id = 1)
);

CREATE TABLE trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    market_condition_id TEXT NOT NULL,
    market_slug TEXT NOT NULL,
    market_question TEXT NOT NULL,
    outcome TEXT NOT NULL CHECK (outcome IN ('yes', 'no')),
    side TEXT NOT NULL CHECK (side IN ('buy', 'sell')),
    order_type TEXT NOT NULL DEFAULT 'fok' CHECK (order_type IN ('fok', 'fak')),
    -- Execution details (1:1 match to what Polymarket would do)
    avg_price REAL NOT NULL,         -- Volume-weighted avg across filled levels
    amount_usd REAL NOT NULL,        -- USD spent (buy) or received before fee (sell)
    shares REAL NOT NULL,            -- Shares received (buy) or sold (sell)
    fee_rate_bps INTEGER NOT NULL,   -- Actual market fee rate
    fee REAL NOT NULL DEFAULT 0,     -- Actual fee charged
    slippage REAL NOT NULL DEFAULT 0,-- Difference from midpoint in bps
    levels_filled INTEGER NOT NULL DEFAULT 1, -- How many book levels consumed
    is_partial INTEGER NOT NULL DEFAULT 0,    -- FAK partial fill?
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE positions (
    market_condition_id TEXT NOT NULL,
    market_slug TEXT NOT NULL,
    market_question TEXT NOT NULL,
    outcome TEXT NOT NULL CHECK (outcome IN ('yes', 'no')),
    shares REAL NOT NULL DEFAULT 0,
    avg_entry_price REAL NOT NULL DEFAULT 0,
    total_cost REAL NOT NULL DEFAULT 0,
    realized_pnl REAL NOT NULL DEFAULT 0,
    is_resolved INTEGER NOT NULL DEFAULT 0,
    resolved_at TEXT,
    PRIMARY KEY (market_condition_id, outcome)
);

CREATE TABLE market_cache (
    cache_key TEXT PRIMARY KEY,
    data TEXT NOT NULL,
    fetched_at TEXT NOT NULL DEFAULT (datetime('now'))
);
```

## 5. Execution Model — 1:1 Faithful

### BUY Flow (amount in USD)

```
Input: pm-sim buy "bitcoin-100k" yes 100

1. Validate account exists
2. Resolve "bitcoin-100k" → condition_id + token_ids (cached)
3. Check market is active (not closed)
4. Fetch REAL order book for YES token: GET /book?token_id=X
5. Fetch REAL fee rate: GET /fee-rate?token_id=X → e.g. 0 bps
6. Fetch tick size: GET /tick-size?token_id=X
7. Walk ASK side of order book to fill $100:

   Book asks:
   Level 1: price=0.66, size=200 shares → can buy 151.52 shares for $100
   (If $100 fills within level 1, done. Otherwise continue to level 2, 3...)

   Example multi-level fill:
   Level 1: 0.66 × 80 shares  = $52.80    (80 shares available)
   Level 2: 0.67 × 70.45 shares = $47.20  (fill remaining $47.20)
   Total: 150.45 shares for $100.00
   Avg price: 100 / 150.45 = 0.6647

8. Calculate fee: (0/10000) * min(0.6647, 0.3353) * 100 = $0.00
   (or for 200bps market: 0.02 * 0.3353 * 100 = $0.67)
9. Check cash >= amount + fee
10. FOR FOK: if book depth < amount, REJECT entirely
    FOR FAK: fill whatever is available, return partial
11. Record trade with: avg_price, actual shares, fee, slippage, levels_filled
12. Update position with actual execution details
```

### SELL Flow (amount in SHARES)

```
Input: pm-sim sell "bitcoin-100k" yes 100  (sell 100 shares)

1-3. Same validation
4. Fetch REAL order book for YES token
5. Walk BID side of order book to sell 100 shares:

   Book bids:
   Level 1: price=0.64, size=150 shares → sell 100 shares at $0.64
   Proceeds: 100 * 0.64 = $64.00

   Multi-level example:
   Level 1: 0.64 × 60 shares  = $38.40
   Level 2: 0.63 × 40 shares  = $25.20
   Total: $63.60 for 100 shares
   Avg price: 63.60 / 100 = 0.636

6. Fee: (bps/10000) * min(0.636, 0.364) * 100_shares = ...
7. Net proceeds = gross - fee
8. FOR FOK: if book depth < shares, REJECT
   FOR FAK: sell what's available
9. Update cash += net_proceeds
10. Update position: shares -= sold, recalc realized P&L
```

### Resolution Flow (unchanged)

```
1. Fetch market, check closed + outcomePrices settled
2. Winning outcome gets $1.00/share, losing gets $0.00/share
3. Update cash, mark positions resolved
```

## 6. Order Book Simulation Engine (orderbook.py)

The core differentiator. This module walks the real order book exactly as
Polymarket's matching engine would.

```python
@dataclass
class FillResult:
    filled: bool           # True if order fully filled (FOK satisfied)
    avg_price: float       # Volume-weighted average fill price
    total_cost: float      # Total USD spent (buy) or received (sell)
    total_shares: float    # Total shares received (buy) or sold (sell)
    fee: float             # Fee charged
    slippage_bps: float    # Slippage from midpoint in basis points
    levels_filled: int     # Number of book levels consumed
    is_partial: bool       # True if FAK partial fill
    fills: list[Fill]      # Per-level fill details

@dataclass
class Fill:
    price: float
    shares: float
    cost: float
    level: int
```

### Fill Algorithm for BUY (walk asks):

```
remaining_usd = amount
fills = []
for level in asks (sorted low to high):
    max_shares_at_level = level.size
    max_cost_at_level = max_shares_at_level * level.price
    if max_cost_at_level <= remaining_usd:
        # consume entire level
        fills.append(Fill(price=level.price, shares=level.size, cost=max_cost_at_level))
        remaining_usd -= max_cost_at_level
    else:
        # partial level fill
        shares = remaining_usd / level.price
        fills.append(Fill(price=level.price, shares=shares, cost=remaining_usd))
        remaining_usd = 0
        break

if FOK and remaining_usd > 0: REJECT
if FAK: return partial fill
```

### Fill Algorithm for SELL (walk bids):

```
remaining_shares = shares_to_sell
fills = []
for level in bids (sorted high to low):
    if level.size <= remaining_shares:
        cost = level.size * level.price
        fills.append(Fill(price=level.price, shares=level.size, cost=cost))
        remaining_shares -= level.size
    else:
        cost = remaining_shares * level.price
        fills.append(Fill(price=level.price, shares=remaining_shares, cost=cost))
        remaining_shares = 0
        break

if FOK and remaining_shares > 0: REJECT
if FAK: return partial fill
```

## 7. Fee Model — Real Per-Market Rates

**No configurable fee override.** Simulator fetches the REAL `fee_rate_bps` from
Polymarket's CLOB API for each market at trade time.

```
GET https://clob.polymarket.com/fee-rate?token_id={token_id}
→ {"fee_rate_bps": 0}  (most markets)
→ {"fee_rate_bps": 250} (crypto 15-min markets)
→ {"fee_rate_bps": 175} (sports markets)
```

Formula (Polymarket exact):
```
fee = (fee_rate_bps / 10000) * min(price, 1 - price) * size
```

Where `size`:
- BUY: amount in USD
- SELL: amount in shares

Fee is:
- Deducted from cash on BUY (cash_out = amount + fee)
- Deducted from proceeds on SELL (cash_in = proceeds - fee)

## 8. Polymarket API Endpoints

| Endpoint | Purpose | Cache? |
|----------|---------|--------|
| `GET gamma/markets?slug=X` | Resolve slug → market data | 5 min |
| `GET gamma/markets?limit=N` | List markets | 5 min |
| `GET gamma/markets?_q=X` | Search markets | 5 min |
| `GET clob/midpoint?token_id=X` | Current midpoint | Never |
| `GET clob/book?token_id=X` | **Full order book** | **Never** |
| `GET clob/fee-rate?token_id=X` | **Market fee rate** | 5 min |
| `GET clob/tick-size?token_id=X` | **Market tick size** | 5 min |
| `GET clob/neg-risk?token_id=X` | Neg-risk flag | 5 min |
| `GET clob/price-history?...` | Historical prices | 5 min |

## 9. Project Structure

```
polymarket/
├── pm_sim/
│   ├── __init__.py
│   ├── cli.py          # Click CLI, JSON envelope
│   ├── engine.py       # Trade orchestration, portfolio, resolve
│   ├── orderbook.py    # Order book fill simulation (core)
│   ├── api.py          # Polymarket HTTP client
│   ├── db.py           # SQLite operations
│   └── models.py       # All dataclasses + error types
├── tests/
│   ├── conftest.py     # Fixtures: sample markets, order books
│   ├── test_orderbook.py  # Fill algorithm tests (most critical)
│   ├── test_engine.py
│   ├── test_api.py
│   ├── test_db.py
│   └── test_cli.py
├── pyproject.toml
└── README.md
```

## 10. Roadmap

### Phase 1 — 1:1 Faithful MVP (Current)

- [ ] Project scaffolding + models
- [ ] SQLite database layer
- [ ] Polymarket API client (Gamma + CLOB + fee-rate + tick-size)
- [ ] **Order book fill engine** (walk book, slippage, FOK/FAK)
- [ ] Trade engine (buy/sell/portfolio/resolve using real book execution)
- [ ] CLI commands
- [ ] Integration tests with realistic order book fixtures
- [ ] README

### Phase 2 — Benchmarking & Analytics

- [ ] `performance` command (win rate, P&L, Sharpe, max drawdown)
- [ ] Multi-account support (`--account agent-a`)
- [ ] Benchmark harness (run N agents, compare results)
- [ ] `history --export csv`

### Phase 3 — Advanced Orders

- [ ] GTC limit orders (rest on simulated book until price hit)
- [ ] GTD time-limited orders
- [ ] `watch` real-time price monitoring
- [ ] Neg-risk multi-outcome market support

### Phase 4 — Platform Expansion

- [ ] Kalshi adapter
- [ ] Historical data backtesting
- [ ] MCP Server mode
- [ ] WebSocket real-time feeds

## 11. Dependencies

```toml
[project]
name = "pm-sim"
version = "0.1.0"
description = "1:1 faithful Polymarket paper trading simulator for AI agents"
requires-python = ">=3.10"
dependencies = [
    "click>=8.0",
    "httpx>=0.27",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-httpx>=0.30",
]

[project.scripts]
pm-sim = "pm_sim.cli:main"
```
