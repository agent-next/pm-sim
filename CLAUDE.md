# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

# polymarket-paper-trader

Paper trading simulator for Polymarket. Built for AI agents. Python 3.10+, SQLite, Click CLI, FastMCP.

## Commands

```bash
# Install
pip install -e ".[dev]"

# Tests (615 non-live + 42 live = 657 total, 100% coverage)
python3 -m pytest tests/ -x -q -m "not live"            # fast, skip live API tests
python3 -m pytest tests/ -v                              # verbose
python3 -m pytest tests/ --cov=pm_trader --cov-report=term-missing  # coverage
python3 -m pytest tests/test_e2e_live.py -v              # live API (requires network)

# Single test file / single test
python3 -m pytest tests/test_engine.py -x -q             # one file
python3 -m pytest tests/test_engine.py::TestBuy::test_buy_yes -x -q  # one test

# Run
pm-trader init --balance 10000
pm-trader-mcp                                            # MCP server on stdio
```

## Architecture

```
cli.py → engine.py → api.py (Polymarket HTTP)
                   → db.py (SQLite, WAL mode)
                   → orderbook.py (fill simulation)
                   → orders.py (limit order state machine)

mcp_server.py → engine.py (trading tools, 30 MCP tools)
              → analytics.py, card.py, benchmark.py, backtest.py (lazy imports)
```

- **Engine** is the orchestrator. All trading logic goes through it.
- **mcp_server.py** uses engine for trading, but imports analytics/card/benchmark/backtest directly (lazy, inside tool functions, to keep startup fast).
- **orderbook.py** has pure functions (`simulate_buy_fill`, `simulate_sell_fill`) — no side effects.
- **orders.py** has pure SQLite functions for limit order CRUD — no Engine dependency.
- **api.py** talks to Gamma API (market discovery) and CLOB API (prices, order books).
- **db.py** owns the SQLite schema. WAL mode for concurrent reads.

## Conventions

### Code style
- `from __future__ import annotations` at top of every module
- Complete type hints on all functions: `def foo(x: int) -> str:`
- Union types use `|` syntax: `str | None`, not `Optional[str]`
- Private functions prefixed with `_`: `_parse_market()`, `_get_cached()`
- Outcomes always lowercase: `"yes"`, `"no"` (normalized via `_validate_outcome`)

### Error handling
- Custom hierarchy: `SimError` → `InsufficientBalanceError`, `MarketClosedError`, etc.
- Each error has a `code` class attribute: `code = "INSUFFICIENT_BALANCE"`
- CLI and MCP use JSON envelope: `{"ok": true, "data": {...}}` or `{"ok": false, "error": "msg", "code": "CODE"}`
- Helper functions: `_ok(data)` and `_err(error, code)` in both cli.py and mcp_server.py
- `_err_from(e)` in mcp_server.py: wraps exceptions — exposes `SimError`/`ValueError`/`TypeError` messages, sanitizes everything else to `"Internal error"`

### Shared helpers
- `_market_to_dict(m)` in mcp_server.py — single serializer for Market→dict (used by all market-returning tools)
- `_parse_market_list(data)` in api.py — shared parser for Gamma API market list responses
- Don't cache empty API responses — guard with `len(data) > 0` before `_set_cached()`

### Security
- Account names validated via `_validate_account_name()`: rejects `..`, `/`, `\`, empty, leading/trailing whitespace
- `MAX_RESULTS = 100` caps all market-listing tool limits to prevent resource exhaustion

### Key design decisions
- **Fee formula**: `(bps/10000) * min(price, 1-price) * size` — summed per filled level
- **Slippage**: primary metric is vs best quote, with midpoint slippage also reported for context
- **FOK** (fill-or-kill): all or nothing. **FAK** (fill-and-kill): partial fills ok
- **Limit orders**: GTC (rest until filled/cancelled) or GTD (expire at timestamp)
- **No price/book caching**: always live from API. Market metadata cached 5 min.
- **Multi-account**: separate SQLite databases at `~/.pm-trader/<account>/paper.db`

## Testing rules

- **Always run tests after changes**: `python3 -m pytest tests/ -x -q -m "not live"`
- **Update tests in the same pass** as bug fixes or refactors. A change is not done until tests pass.
- **100% coverage is maintained.** New code must include tests. Use `pragma: no cover` only for `if __name__ == "__main__"` guards.
- Test files mirror source: `pm_trader/engine.py` → `tests/test_engine.py`
- Behavior tests in `test_behavior.py`: test from an agent's perspective (full workflows, not internals)
- E2E live tests in `test_e2e_live.py`: use `pytest.skip()` when live API data is unavailable
- Shared fixtures in `conftest.py`: `tmp_data_dir`, `sample_market`, `closed_market`, `sample_order_book`
- Mock API in behavior tests with `_mock(engine, market=..., book=..., fee=...)` helper
- Use `pytest.approx()` for float comparisons, `pytest.raises(ErrorType)` for exceptions

## Git rules

- Atomic commits: one logical change per commit
- Run tests before committing
- If rebase fails twice, reset and cherry-pick instead
