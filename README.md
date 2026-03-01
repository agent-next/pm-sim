# polymarket-paper-trader

[![ClawHub](https://img.shields.io/badge/ClawHub-install-orange.svg)](https://clawhub.com/robotlearning123/polymarket-paper-trader)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

**Your AI agent just became a Polymarket trader.**

Install → your agent gets $10,000 paper money → trades real Polymarket order books → tracks P&L → competes on a public leaderboard. Zero risk. Real prices.

> "My AI agent hit +18% ROI on Polymarket in one week. Zero risk, real order books."

Part of [agent-next](https://github.com/agent-next) — open research lab for self-evolving autonomous agents.

## 60-second demo

```bash
npx clawhub install polymarket-paper-trader    # install via ClawHub
pm-trader init --balance 10000                 # $10k paper money
pm-trader markets search "bitcoin"             # find markets
pm-trader buy will-bitcoin-hit-100k yes 500    # buy $500 of YES
pm-trader stats --card                         # shareable stats card
```

That's it. Your AI agent is now trading Polymarket with zero risk.

## Install

```bash
# via ClawHub (for OpenClaw agents)
npx clawhub install polymarket-paper-trader

# via pip (for direct use)
uv pip install -e .

# dev dependencies (tests)
uv pip install -e ".[dev]"
```

Requires Python 3.10+.

## Not a toy — this is a real exchange simulator

Other tools mock prices or use random numbers. We simulate the actual exchange:

- **Level-by-level order book execution** — your order walks the real Polymarket ask/bid book, consuming liquidity at each price level, just like a real trade
- **Exact fee model** — `bps/10000 × min(price, 1-price) × shares` — the same formula Polymarket uses
- **Slippage tracking** — every trade records how much worse your fill was vs the midpoint, in basis points
- **Limit order state machine** — GTC (good-til-cancelled) and GTD (good-til-date) with full lifecycle
- **Strategy backtesting** — replay your strategy against historical price snapshots
- **Multi-outcome markets** — not just YES/NO binary, supports any number of outcomes

Your paper P&L would match real P&L within the spread. That's the point.

## Quick start

```bash
# Initialize with $10k paper balance
pm-trader init --balance 10000

# Browse markets
pm-trader markets list --sort liquidity
pm-trader markets search "bitcoin"

# Trade
pm-trader buy will-bitcoin-hit-100k yes 100      # buy $100 of YES
pm-trader sell will-bitcoin-hit-100k yes 50       # sell 50 shares

# Check portfolio and P&L
pm-trader portfolio
pm-trader stats
```

## CLI commands

| Command | Description |
|---------|-------------|
| `init [--balance N]` | Create paper trading account |
| `balance` | Show cash, positions value, total P&L |
| `reset --confirm` | Wipe all data |
| `markets list [--limit N] [--sort volume\|liquidity]` | Browse active markets |
| `markets search QUERY` | Full-text market search |
| `markets get SLUG` | Market details |
| `price SLUG` | YES/NO midpoints and spread |
| `book SLUG [--depth N]` | Order book snapshot |
| `watch SLUG [SLUG...] [--outcome yes\|no]` | Monitor live prices |
| `buy SLUG OUTCOME AMOUNT [--type fok\|fak]` | Buy at market price |
| `sell SLUG OUTCOME SHARES [--type fok\|fak]` | Sell at market price |
| `portfolio` | Open positions with live prices |
| `history [--limit N]` | Trade history |
| `orders place SLUG OUTCOME SIDE AMOUNT PRICE` | Limit order |
| `orders list` | Pending limit orders |
| `orders cancel ID` | Cancel a limit order |
| `orders check` | Fill limit orders if price crosses |
| `stats` | Win rate, ROI, profit, max drawdown |
| `export trades [--format csv\|json]` | Export trade history |
| `export positions [--format csv\|json]` | Export positions |
| `benchmark run MODULE.FUNC` | Run a trading strategy |
| `benchmark compare ACCT1 ACCT2` | Compare account performance |
| `benchmark pk STRAT_A STRAT_B` | Battle: who's the better trader? |
| `accounts list` | List named accounts |
| `accounts create NAME` | Create account for A/B testing |
| `mcp` | Start MCP server (stdio transport) |

Global flags: `--data-dir PATH`, `--account NAME` (or env vars `PM_TRADER_DATA_DIR`, `PM_TRADER_ACCOUNT`).

## MCP server — what your agent can do

Your agent gets 26 tools via the [Model Context Protocol](https://modelcontextprotocol.io):

```bash
pm-trader-mcp  # starts on stdio
```

Add to your Claude Code config:

```json
{
  "mcpServers": {
    "polymarket-paper-trader": {
      "command": "pm-trader-mcp"
    }
  }
}
```

### MCP tools

| Tool | What it does |
|------|---------|
| `init_account` | Create paper account with starting balance |
| `get_balance` | Cash, positions value, total P&L |
| `reset_account` | Wipe all data and start fresh |
| `search_markets` | Find markets by keyword |
| `list_markets` | Browse markets sorted by volume/liquidity |
| `get_market` | Market details with outcomes and prices |
| `get_order_book` | Live order book snapshot (bids + asks) |
| `watch_prices` | Monitor prices for multiple markets |
| `buy` | Buy shares at best available prices |
| `sell` | Sell shares at best available prices |
| `portfolio` | Open positions with live valuations and P&L |
| `history` | Recent trade log with execution details |
| `place_limit_order` | Limit order — stays open until filled or cancelled/expired |
| `list_orders` | Pending limit orders |
| `cancel_order` | Cancel a pending order |
| `check_orders` | Execute pending orders against live prices |
| `stats` | Win rate, ROI, profit, max drawdown |
| `resolve` | Resolve a closed market (winners get $1/share) |
| `resolve_all` | Resolve all closed markets |
| `backtest` | Backtest a strategy against historical snapshots |
| `stats_card` | Shareable stats card (tweet/markdown/plain) |
| `share_content` | Platform-specific content (twitter/telegram/discord) |
| `leaderboard_entry` | Generate verifiable leaderboard submission |
| `leaderboard_card` | Top 10 ranking card from all local accounts |
| `pk_card` | Head-to-head comparison between two accounts |
| `pk_battle` | Run two strategies head-to-head, auto-compare |

## Strategy examples

Three ready-to-use strategies in `examples/`:

### Momentum (`examples/momentum.py`)

Buys when YES price crosses above 0.55, takes profit at 0.70, stops loss at 0.35.

```bash
pm-trader benchmark run examples.momentum.run
```

### Mean reversion (`examples/mean_reversion.py`)

Buys when YES price drops 12+ cents below 0.50 fair value, sells when it reverts.

```bash
pm-trader benchmark run examples.mean_reversion.run
```

### Limit grid (`examples/limit_grid.py`)

Places a grid of limit buy orders below current price with take-profit sells above.

```bash
pm-trader benchmark run examples.limit_grid.run
```

### Writing your own strategy

```python
# my_strategy.py
from pm_trader.engine import Engine

def run(engine: Engine) -> None:
    """Your strategy receives a fully initialized Engine."""
    markets = engine.api.search_markets("crypto")
    for market in markets:
        if market.closed or market.yes_price < 0.3:
            continue
        engine.buy(market.slug, "yes", 100.0)
```

```bash
pm-trader benchmark run my_strategy.run
```

For backtesting with historical data:

```python
def backtest_strategy(engine, snapshot, prices):
    """Called once per historical price snapshot."""
    if snapshot.midpoint > 0.6:
        engine.buy(snapshot.market_slug, snapshot.outcome, 50.0)
```

## Multi-account support

Run parallel strategies with isolated accounts:

```bash
pm-trader --account aggressive init --balance 5000
pm-trader --account conservative init --balance 5000

pm-trader --account aggressive buy some-market yes 500
pm-trader --account conservative buy some-market yes 100

pm-trader benchmark compare aggressive conservative
```

## Share your results

Generate a shareable stats card and post to X/Twitter:

```bash
pm-trader stats --tweet    # X/Twitter optimized (< 280 chars)
pm-trader stats --card     # markdown for Telegram/Discord
pm-trader stats --plain    # plain text
```

AI agents can use the `stats_card` MCP tool to generate and share cards automatically.

## OpenClaw / ClawHub

Available on [ClawHub](https://clawhub.com) as `polymarket-paper-trader`:

```bash
npx clawhub install polymarket-paper-trader
```

## Tests

```bash
pytest                           # full test suite
pytest tests/test_e2e_live.py    # live API integration tests
```

## License

MIT
