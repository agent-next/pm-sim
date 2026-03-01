# Phase 2: Analytics & Benchmarking Design

## Overview

Add performance analytics, multi-account support, benchmarking harness, and trade export to pm-sim.

## Multi-Account Support

Each account gets its own SQLite database file in the data directory.

```
~/.pm-sim/
  default.db          # default account
  agent-a.db          # named account
  agent-b.db
```

- `--account <name>` global CLI option (default: "default")
- `pm-sim accounts list` — list all accounts
- `pm-sim accounts create <name> --balance <amount>`
- `pm-sim accounts delete <name>`
- Engine and DB are scoped to a single account — no schema changes needed

## Analytics Module (`pm_sim/analytics.py`)

Pure functions that take trade/position data and return metrics.

### Metrics

| Metric | Formula | Notes |
|--------|---------|-------|
| Total P&L | `cash + positions_value - starting_balance` | Already in engine |
| ROI % | `pnl / starting_balance * 100` | |
| Win Rate | `winning_trades / total_closed_trades` | Trade is "winning" if realized_pnl > 0 |
| Sharpe Ratio | `mean(daily_returns) / std(daily_returns) * sqrt(365)` | Annualized, 365 days for prediction markets |
| Max Drawdown | `max(peak - trough) / peak` over cumulative P&L curve | |

### Daily Returns

Compute from trade timestamps: group trades by date, sum realized P&L per day, compute daily return as `daily_pnl / portfolio_value_at_start_of_day`.

### CLI

`pm-sim stats` → JSON envelope with all metrics.
`pm-sim stats --metric sharpe` → single metric.

## Benchmarking Harness (`pm_sim/benchmark.py`)

### Strategy Protocol

```python
from pm_sim.engine import Engine

def my_strategy(engine: Engine) -> None:
    """A strategy is a callable that receives an Engine and trades."""
    markets = engine.api.list_markets(limit=5)
    for m in markets:
        engine.buy(m.slug, "yes", 100.0)
```

### Runner

1. Create fresh account (tmp data dir)
2. Import strategy via dotted path: `module.function_name`
3. Call `strategy(engine)`
4. Compute analytics on resulting trades
5. Output scorecard

### CLI

```
pm-sim benchmark run my_strategies.momentum --balance 10000
pm-sim benchmark compare agent-a agent-b  # compare two accounts
```

## Export (`pm_sim/export.py`)

### Commands

```
pm-sim export trades --format csv    # stdout
pm-sim export trades --format json --output trades.json
pm-sim export positions --format csv
```

### CSV Format

Trades: `id,timestamp,market_slug,side,outcome,shares,avg_price,amount_usd,fee,realized_pnl`
Positions: `market_slug,condition_id,outcome,shares,avg_entry_price,total_cost,live_price,unrealized_pnl`

## New Files

- `pm_sim/analytics.py` — pure metric computation functions
- `pm_sim/benchmark.py` — strategy runner and comparison
- `pm_sim/export.py` — CSV/JSON export
- `tests/test_analytics.py`
- `tests/test_benchmark.py`
- `tests/test_export.py`
- Modified: `pm_sim/cli.py` (new commands), `pm_sim/db.py` (account name in path)
