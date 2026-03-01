"""MCP server exposing pm-trader as tools for AI agents.

Run with:
    pm-trader mcp                  # stdio transport (default)
    python -m pm_trader.mcp_server # direct execution
"""

from __future__ import annotations

import json
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from pm_trader.engine import Engine

DEFAULT_DATA_DIR = Path.home() / ".pm-trader" / "default"

mcp = FastMCP("pm-trader", json_response=True)

# ---------------------------------------------------------------------------
# Engine lifecycle — one Engine per server session
# ---------------------------------------------------------------------------

_engine: Engine | None = None


def _get_engine(account: str = "default") -> Engine:
    """Return the current Engine, creating if needed."""
    global _engine
    data_dir = Path.home() / ".pm-trader" / account
    if _engine is None or _engine.db.data_dir != data_dir:
        if _engine is not None:
            _engine.close()
        _engine = Engine(data_dir)
    return _engine


def _ok(data: object) -> str:
    """Wrap data in the standard pm-trader JSON envelope."""
    return json.dumps({"ok": True, "data": data})


def _err(msg: str, code: str = "error") -> str:
    """Return an error envelope."""
    return json.dumps({"ok": False, "error": msg, "code": code})


# ---------------------------------------------------------------------------
# Account tools
# ---------------------------------------------------------------------------


@mcp.tool()
def init_account(balance: float = 10_000.0, account: str = "default") -> str:
    """Initialize a paper trading account with starting balance (USD).

    Creates a new account or resets an existing one.
    """
    engine = _get_engine(account)
    acct = engine.init_account(balance)
    return _ok({
        "cash": acct.cash,
        "starting_balance": acct.starting_balance,
    })


@mcp.tool()
def get_balance(account: str = "default") -> str:
    """Get current account balance, positions value, and P&L."""
    try:
        engine = _get_engine(account)
        bal = engine.get_balance()
        return _ok(bal)
    except Exception as e:
        return _err(str(e), "not_initialized")


@mcp.tool()
def reset_account(account: str = "default") -> str:
    """Reset account — deletes all trades, positions, and balance."""
    engine = _get_engine(account)
    engine.reset()
    return _ok({"reset": True})


# ---------------------------------------------------------------------------
# Market data tools
# ---------------------------------------------------------------------------


@mcp.tool()
def search_markets(query: str, limit: int = 10) -> str:
    """Search Polymarket for markets matching a query string."""
    engine = _get_engine()
    markets = engine.api.search_markets(query, limit=limit)
    return _ok([
        {
            "slug": m.slug,
            "question": m.question,
            "condition_id": m.condition_id,
            "outcomes": m.outcomes,
            "outcome_prices": m.outcome_prices,
            "volume": m.volume,
            "liquidity": m.liquidity,
            "closed": m.closed,
            "end_date": m.end_date,
        }
        for m in markets
    ])


@mcp.tool()
def list_markets(limit: int = 20, sort_by: str = "volume") -> str:
    """List active Polymarket markets sorted by volume or liquidity."""
    engine = _get_engine()
    markets = engine.api.list_markets(limit=limit, sort_by=sort_by)
    return _ok([
        {
            "slug": m.slug,
            "question": m.question,
            "condition_id": m.condition_id,
            "outcomes": m.outcomes,
            "outcome_prices": m.outcome_prices,
            "volume": m.volume,
            "liquidity": m.liquidity,
            "closed": m.closed,
        }
        for m in markets
    ])


@mcp.tool()
def get_market(slug_or_id: str) -> str:
    """Get detailed info for a specific market by slug or condition ID."""
    try:
        engine = _get_engine()
        m = engine.api.get_market(slug_or_id)
        return _ok({
            "slug": m.slug,
            "question": m.question,
            "condition_id": m.condition_id,
            "outcomes": m.outcomes,
            "outcome_prices": m.outcome_prices,
            "volume": m.volume,
            "liquidity": m.liquidity,
            "closed": m.closed,
            "end_date": m.end_date,
        })
    except Exception as e:
        return _err(str(e), "market_not_found")


@mcp.tool()
def get_order_book(slug_or_id: str, outcome: str = "yes") -> str:
    """Get the live order book for a market outcome (asks and bids)."""
    try:
        engine = _get_engine()
        market = engine.api.get_market(slug_or_id)
        token_id = market.get_token_id(outcome)
        book = engine.api.get_order_book(token_id)
        return _ok({
            "market_slug": market.slug,
            "outcome": outcome.lower(),
            "asks": [{"price": a.price, "size": a.size} for a in book.asks],
            "bids": [{"price": b.price, "size": b.size} for b in book.bids],
        })
    except Exception as e:
        return _err(str(e), "order_book_error")


@mcp.tool()
def watch_prices(
    slugs: str, outcomes: str = "yes",
) -> str:
    """Watch live midpoint prices for one or more markets.

    slugs: comma-separated market slugs or condition IDs
    outcomes: comma-separated outcomes (default: "yes")
    """
    try:
        engine = _get_engine()
        slug_list = [s.strip() for s in slugs.split(",")]
        outcome_list = [o.strip() for o in outcomes.split(",")]
        prices = engine.watch_prices(slug_list, outcome_list)
        return _ok(prices)
    except Exception as e:
        return _err(str(e), type(e).__name__)


# ---------------------------------------------------------------------------
# Trading tools
# ---------------------------------------------------------------------------


@mcp.tool()
def buy(
    slug_or_id: str,
    outcome: str,
    amount_usd: float,
    order_type: str = "fok",
    account: str = "default",
) -> str:
    """Buy shares in a Polymarket outcome.

    Spends amount_usd to buy at best available prices.
    order_type: "fok" (fill-or-kill) or "fak" (fill-and-kill, allows partial).
    """
    try:
        engine = _get_engine(account)
        result = engine.buy(slug_or_id, outcome, amount_usd, order_type)
        return _ok({
            "trade": {
                "id": result.trade.id,
                "market_slug": result.trade.market_slug,
                "outcome": result.trade.outcome,
                "side": result.trade.side,
                "avg_price": result.trade.avg_price,
                "amount_usd": result.trade.amount_usd,
                "shares": result.trade.shares,
                "fee": result.trade.fee,
                "slippage_bps": result.trade.slippage,
            },
            "account": {
                "cash": result.account.cash,
            },
        })
    except Exception as e:
        return _err(str(e), type(e).__name__)


@mcp.tool()
def sell(
    slug_or_id: str,
    outcome: str,
    shares: float,
    order_type: str = "fok",
    account: str = "default",
) -> str:
    """Sell shares in a Polymarket outcome.

    Sells shares at best available prices.
    order_type: "fok" (fill-or-kill) or "fak" (fill-and-kill, allows partial).
    """
    try:
        engine = _get_engine(account)
        result = engine.sell(slug_or_id, outcome, shares, order_type)
        return _ok({
            "trade": {
                "id": result.trade.id,
                "market_slug": result.trade.market_slug,
                "outcome": result.trade.outcome,
                "side": result.trade.side,
                "avg_price": result.trade.avg_price,
                "amount_usd": result.trade.amount_usd,
                "shares": result.trade.shares,
                "fee": result.trade.fee,
                "slippage_bps": result.trade.slippage,
            },
            "account": {
                "cash": result.account.cash,
            },
        })
    except Exception as e:
        return _err(str(e), type(e).__name__)


# ---------------------------------------------------------------------------
# Portfolio tools
# ---------------------------------------------------------------------------


@mcp.tool()
def portfolio(account: str = "default") -> str:
    """Get all open positions with live prices and unrealized P&L."""
    try:
        engine = _get_engine(account)
        positions = engine.get_portfolio()
        return _ok(positions)
    except Exception as e:
        return _err(str(e), type(e).__name__)


@mcp.tool()
def history(limit: int = 50, account: str = "default") -> str:
    """Get recent trade history."""
    try:
        engine = _get_engine(account)
        trades = engine.get_history(limit)
        return _ok([
            {
                "id": t.id,
                "market_slug": t.market_slug,
                "outcome": t.outcome,
                "side": t.side,
                "avg_price": t.avg_price,
                "amount_usd": t.amount_usd,
                "shares": t.shares,
                "fee": t.fee,
                "created_at": t.created_at,
            }
            for t in trades
        ])
    except Exception as e:
        return _err(str(e), type(e).__name__)


# ---------------------------------------------------------------------------
# Limit order tools
# ---------------------------------------------------------------------------


@mcp.tool()
def place_limit_order(
    slug_or_id: str,
    outcome: str,
    side: str,
    amount: float,
    limit_price: float,
    order_type: str = "gtc",
    expires_at: str | None = None,
    account: str = "default",
) -> str:
    """Place a limit order that stays open until filled or cancelled/expired.

    side: "buy" or "sell"
    limit_price: target price between 0 and 1
    order_type: "gtc" (stays open until cancelled) or "gtd" (expires at timestamp)
    expires_at: ISO timestamp for GTD orders (required if order_type="gtd")
    """
    try:
        engine = _get_engine(account)
        order = engine.place_limit_order(
            slug_or_id, outcome, side, amount, limit_price,
            order_type, expires_at,
        )
        return _ok(order)
    except Exception as e:
        return _err(str(e), type(e).__name__)


@mcp.tool()
def list_orders(account: str = "default") -> str:
    """List all pending limit orders."""
    engine = _get_engine(account)
    orders = engine.get_pending_orders()
    return _ok(orders)


@mcp.tool()
def cancel_order(order_id: int, account: str = "default") -> str:
    """Cancel a pending limit order by ID."""
    engine = _get_engine(account)
    order = engine.cancel_limit_order(order_id)
    if order is None:
        return _err(f"Order {order_id} not found or not pending", "not_found")
    return _ok(order)


@mcp.tool()
def check_orders(account: str = "default") -> str:
    """Check all pending limit orders against live prices and execute fills.

    Call this periodically to trigger limit order evaluation.
    """
    try:
        engine = _get_engine(account)
        results = engine.check_orders()
        return _ok(results)
    except Exception as e:
        return _err(str(e), type(e).__name__)


# ---------------------------------------------------------------------------
# Analytics tools
# ---------------------------------------------------------------------------


@mcp.tool()
def stats(account: str = "default") -> str:
    """Get performance analytics: win rate, ROI, profit, max drawdown."""
    try:
        engine = _get_engine(account)
        from pm_trader.analytics import compute_stats

        acct = engine.get_account()
        trades = engine.db.get_trades(limit=10_000)
        portfolio_items = engine.get_portfolio()
        positions_value = sum(p["current_value"] for p in portfolio_items)

        result = compute_stats(trades, acct, positions_value)
        return _ok(result)
    except Exception as e:
        return _err(str(e), type(e).__name__)


@mcp.tool()
def stats_card(account: str = "default", format: str = "markdown") -> str:
    """Get a shareable stats card — ready to post on X, Telegram, Discord, etc.

    Returns a formatted card showing ROI, Sharpe, win rate, P&L.

    format: "tweet" (X/Twitter optimized), "markdown" (chat apps), "plain" (no formatting)
    """
    try:
        engine = _get_engine(account)
        from pm_trader.analytics import compute_stats
        from pm_trader.card import generate_card, generate_card_plain, generate_tweet

        acct = engine.get_account()
        trades = engine.db.get_trades(limit=10_000)
        portfolio_items = engine.get_portfolio()
        positions_value = sum(p["current_value"] for p in portfolio_items)

        result = compute_stats(trades, acct, positions_value)
        if format == "tweet":
            card = generate_tweet(result, account, portfolio_items)
        elif format == "plain":
            card = generate_card_plain(result, account, portfolio_items)
        else:
            card = generate_card(result, account, portfolio_items)
        return _ok({"card": card, "stats": result})
    except Exception as e:
        return _err(str(e), type(e).__name__)


@mcp.tool()
def leaderboard_entry(account: str = "default") -> str:
    """Generate a verifiable leaderboard entry for ranking and PK.

    Returns standardized JSON with ROI%, Sharpe, win rate, trade count,
    max drawdown, and account metadata. Designed for fair comparison:
    includes starting balance, total trades, and account age.
    """
    try:
        engine = _get_engine(account)
        from pm_trader.analytics import compute_stats

        acct = engine.get_account()
        trades = engine.db.get_trades(limit=10_000)
        portfolio_items = engine.get_portfolio()
        positions_value = sum(p["current_value"] for p in portfolio_items)
        result = compute_stats(trades, acct, positions_value)

        first_trade = trades[-1].created_at if trades else None
        last_trade = trades[0].created_at if trades else None

        return _ok({
            "account": account,
            "starting_balance": result.get("starting_balance", 0.0),
            "total_value": result.get("total_value", 0.0),
            "roi_pct": result.get("roi_pct", 0.0),
            "pnl": result.get("pnl", 0.0),
            "sharpe_ratio": result.get("sharpe_ratio", 0.0),
            "win_rate": result.get("win_rate", 0.0),
            "total_trades": result.get("total_trades", 0),
            "max_drawdown": result.get("max_drawdown", 0.0),
            "total_fees": result.get("total_fees", 0.0),
            "first_trade_at": first_trade,
            "last_trade_at": last_trade,
            "open_positions": len(portfolio_items),
            "qualified": result.get("total_trades", 0) >= 10,
        })
    except Exception as e:
        return _err(str(e), type(e).__name__)


@mcp.tool()
def share_content(
    account: str = "default",
    platform: str = "twitter",
    template: str = "performance",
) -> str:
    """Generate shareable content optimized for a specific platform and template.

    platform: "twitter" (X), "telegram", "discord", "plain"
    template: "performance" (stats overview), "milestone" (achievement),
              "daily" (daily report with positions)

    Each platform gets appropriately formatted content with hashtags and install CTA.
    """
    try:
        from pm_trader.analytics import compute_stats
        from pm_trader.card import (
            generate_card,
            generate_card_plain,
            generate_daily_report,
            generate_milestone_tweet,
            generate_tweet,
        )

        engine = _get_engine(account)
        acct = engine.get_account()
        trades = engine.db.get_trades(limit=10_000)
        portfolio_items = engine.get_portfolio()
        positions_value = sum(p["current_value"] for p in portfolio_items)
        result = compute_stats(trades, acct, positions_value)

        if template == "milestone":
            card = generate_milestone_tweet(result)
        elif template == "daily":
            card = generate_daily_report(result, portfolio_items, account)
        elif platform == "twitter":
            card = generate_tweet(result, account, portfolio_items)
        elif platform == "plain":
            card = generate_card_plain(result, account, portfolio_items)
        else:
            card = generate_card(result, account, portfolio_items)

        return _ok({"card": card, "platform": platform, "template": template})
    except Exception as e:
        return _err(str(e), type(e).__name__)


@mcp.tool()
def pk_card(account_a: str = "default", account_b: str = "aggressive") -> str:
    """Generate a head-to-head PK comparison card between two accounts.

    Compares ROI, Sharpe, win rate, trades, and tier. Outputs a tweet-ready
    card with winner announcement. Great for rivalry and sharing.
    """
    try:
        from pm_trader.analytics import compute_stats
        from pm_trader.card import generate_pk_card

        results = {}
        for name in (account_a, account_b):
            engine = _get_engine(name)
            acct = engine.get_account()
            trades = engine.db.get_trades(limit=10_000)
            portfolio_items = engine.get_portfolio()
            positions_value = sum(p["current_value"] for p in portfolio_items)
            results[name] = compute_stats(trades, acct, positions_value)

        card = generate_pk_card(
            results[account_a], account_a,
            results[account_b], account_b,
        )
        return _ok({
            "card": card,
            account_a: results[account_a],
            account_b: results[account_b],
        })
    except Exception as e:
        return _err(str(e), type(e).__name__)


# ---------------------------------------------------------------------------
# Leaderboard card tool
# ---------------------------------------------------------------------------


@mcp.tool()
def leaderboard_card(accounts: str = "") -> str:
    """Generate a Top 10 leaderboard card from all local accounts.

    Ranks all qualified accounts (10+ trades) by ROI%.
    If accounts is provided (comma-separated), only include those accounts.
    Otherwise scans all accounts in ~/.pm-trader/.
    """
    try:
        from pm_trader.analytics import compute_stats
        from pm_trader.card import generate_leaderboard_card

        base = Path.home() / ".pm-trader"
        if accounts:
            names = [n.strip() for n in accounts.split(",")]
        else:
            if not base.exists():
                return _ok({"card": "No accounts found.", "entries": []})
            names = [
                d.name for d in sorted(base.iterdir())
                if d.is_dir() and (d / "paper.db").exists()
            ]

        entries = []
        for name in names:
            try:
                engine = _get_engine(name)
                acct = engine.get_account()
                trades = engine.db.get_trades(limit=10_000)
                portfolio_items = engine.get_portfolio()
                positions_value = sum(p["current_value"] for p in portfolio_items)
                result = compute_stats(trades, acct, positions_value)
                result["account"] = name
                entries.append(result)
            except Exception:
                continue

        # Sort by ROI descending, qualified first
        qualified = [e for e in entries if e.get("total_trades", 0) >= 10]
        qualified.sort(key=lambda e: e.get("roi_pct", 0.0), reverse=True)

        card = generate_leaderboard_card(qualified)
        return _ok({"card": card, "entries": qualified})
    except Exception as e:
        return _err(str(e), type(e).__name__)


# ---------------------------------------------------------------------------
# PK battle tool
# ---------------------------------------------------------------------------


@mcp.tool()
def pk_battle(
    strategy_a: str,
    strategy_b: str,
    name_a: str = "player_a",
    name_b: str = "player_b",
    balance: float = 10_000.0,
) -> str:
    """Run two strategies head-to-head in a PK battle.

    Both start with the same balance. Each strategy runs independently,
    then results are compared with a PK card and winner announced.

    strategy_a/b: dotted Python path like "examples.momentum.run"
    name_a/b: display names for the PK card
    """
    try:
        from pm_trader.benchmark import pk_battle as _pk_battle

        result = _pk_battle(strategy_a, strategy_b, name_a, name_b, balance)
        return _ok(result)
    except Exception as e:
        return _err(str(e), type(e).__name__)


# ---------------------------------------------------------------------------
# Resolution tools
# ---------------------------------------------------------------------------


@mcp.tool()
def resolve(slug_or_id: str, account: str = "default") -> str:
    """Resolve a market's positions, paying out $1/share for winning outcome."""
    try:
        engine = _get_engine(account)
        results = engine.resolve_market(slug_or_id)
        return _ok([
            {
                "outcome": r.position.outcome,
                "shares": r.position.shares,
                "payout": r.payout,
                "cash_after": r.account.cash,
            }
            for r in results
        ])
    except Exception as e:
        return _err(str(e), type(e).__name__)


@mcp.tool()
def resolve_all(account: str = "default") -> str:
    """Resolve all open positions in closed/resolved markets."""
    try:
        engine = _get_engine(account)
        results = engine.resolve_all()
        return _ok([
            {
                "market_slug": r.position.market_slug,
                "outcome": r.position.outcome,
                "shares": r.position.shares,
                "payout": r.payout,
            }
            for r in results
        ])
    except Exception as e:
        return _err(str(e), type(e).__name__)


# ---------------------------------------------------------------------------
# Backtesting tools
# ---------------------------------------------------------------------------


@mcp.tool()
def backtest(
    data_path: str,
    strategy_path: str,
    balance: float = 10_000.0,
    spread: float = 0.02,
    depth: float = 500.0,
) -> str:
    """Run a backtest with historical price data.

    data_path: path to CSV or JSON file with historical prices
    strategy_path: dotted Python path to strategy function (e.g. "mymod.my_strategy")
    balance: starting balance (USD)
    spread: synthetic order book spread
    depth: synthetic order book depth per level
    """
    try:
        import importlib
        from pathlib import Path as P

        from pm_trader.backtest import (
            load_snapshots_csv,
            load_snapshots_json,
            run_backtest,
        )

        data = P(data_path)
        if data.suffix == ".json":
            snapshots = load_snapshots_json(data)
        else:
            snapshots = load_snapshots_csv(data)

        # Import strategy
        parts = strategy_path.rsplit(".", 1)
        if len(parts) != 2:
            return _err("strategy_path must be module.function", "invalid_strategy")
        mod = importlib.import_module(parts[0])
        strategy_fn = getattr(mod, parts[1])

        from dataclasses import asdict
        result = run_backtest(
            snapshots, strategy_fn, strategy_path, balance, spread, depth,
        )
        return _ok(asdict(result))
    except Exception as e:
        return _err(str(e), type(e).__name__)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main():
    """Run MCP server on stdio transport."""
    mcp.run()


if __name__ == "__main__":  # pragma: no cover
    main()
