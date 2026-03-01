"""Click CLI for pm-trader — Polymarket paper trading simulator."""

from __future__ import annotations

import dataclasses
import json
import sys
from pathlib import Path

import click

from pm_trader.engine import Engine
from pm_trader.models import SimError

DEFAULT_DATA_DIR = Path.home() / ".pm-trader"
DEFAULT_ACCOUNT = "default"


# ---------------------------------------------------------------------------
# JSON helpers
# ---------------------------------------------------------------------------

def _ok(data) -> str:
    """Return a JSON success envelope."""
    return json.dumps({"ok": True, "data": _serialize(data)}, indent=2)


def _err(error: SimError) -> str:
    """Return a JSON error envelope."""
    return json.dumps(
        {"ok": False, "error": error.message, "code": error.code},
        indent=2,
    )


def _serialize(obj):
    """Recursively convert dataclasses and other objects to JSON-safe dicts."""
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {k: _serialize(v) for k, v in dataclasses.asdict(obj).items()}
    if isinstance(obj, list):
        return [_serialize(item) for item in obj]
    if isinstance(obj, dict):
        return {k: _serialize(v) for k, v in obj.items()}
    return obj


# ---------------------------------------------------------------------------
# CLI group
# ---------------------------------------------------------------------------

@click.group()
@click.option(
    "--data-dir",
    type=click.Path(path_type=Path),
    default=DEFAULT_DATA_DIR,
    envvar="PM_TRADER_DATA_DIR",
    help="Data directory for SQLite database.",
)
@click.option(
    "--account",
    default=DEFAULT_ACCOUNT,
    envvar="PM_TRADER_ACCOUNT",
    help="Account name (each account gets its own database).",
)
@click.pass_context
def main(ctx: click.Context, data_dir: Path, account: str) -> None:
    """pm-trader — your AI agent trades Polymarket."""
    ctx.ensure_object(dict)
    ctx.obj["data_dir"] = data_dir
    ctx.obj["account"] = account


def _get_account_dir(ctx: click.Context) -> Path:
    """Return the data directory for the active account."""
    base = ctx.obj["data_dir"]
    account = ctx.obj["account"]
    return base / account


def _get_engine(ctx: click.Context) -> Engine:
    return Engine(_get_account_dir(ctx))


# ---------------------------------------------------------------------------
# Account commands
# ---------------------------------------------------------------------------

@main.command()
@click.option("--balance", type=float, default=10_000.0, help="Starting balance in USD.")
@click.pass_context
def init(ctx: click.Context, balance: float) -> None:
    """Initialize a paper trading account."""
    engine = _get_engine(ctx)
    try:
        account = engine.init_account(balance)
        click.echo(_ok(account))
    except SimError as e:
        click.echo(_err(e))
        sys.exit(1)
    finally:
        engine.close()


@main.command()
@click.pass_context
def balance(ctx: click.Context) -> None:
    """Show account balance and total portfolio value."""
    engine = _get_engine(ctx)
    try:
        data = engine.get_balance()
        click.echo(_ok(data))
    except SimError as e:
        click.echo(_err(e))
        sys.exit(1)
    finally:
        engine.close()


@main.command()
@click.option("--confirm", is_flag=True, help="Required to confirm reset.")
@click.pass_context
def reset(ctx: click.Context, confirm: bool) -> None:
    """Wipe all data and start fresh."""
    if not confirm:
        click.echo(
            json.dumps(
                {"ok": False, "error": "Pass --confirm to reset all data.", "code": "CONFIRM_REQUIRED"},
                indent=2,
            )
        )
        sys.exit(1)
    engine = _get_engine(ctx)
    try:
        engine.reset()
        click.echo(_ok({"reset": True}))
    except SimError as e:
        click.echo(_err(e))
        sys.exit(1)
    finally:
        engine.close()


# ---------------------------------------------------------------------------
# Market commands
# ---------------------------------------------------------------------------

@main.group()
def markets() -> None:
    """Market data commands."""
    pass


@markets.command("list")
@click.option("--limit", type=int, default=20)
@click.option("--sort", "sort_by", type=click.Choice(["volume", "liquidity"]), default="volume")
@click.pass_context
def markets_list(ctx: click.Context, limit: int, sort_by: str) -> None:
    """List active markets."""
    engine = _get_engine(ctx)
    try:
        result = engine.api.list_markets(limit=limit, sort_by=sort_by)
        click.echo(_ok(result))
    except SimError as e:
        click.echo(_err(e))
        sys.exit(1)
    finally:
        engine.close()


@markets.command("search")
@click.argument("query")
@click.option("--limit", type=int, default=10)
@click.pass_context
def markets_search(ctx: click.Context, query: str, limit: int) -> None:
    """Search markets by text query."""
    engine = _get_engine(ctx)
    try:
        result = engine.api.search_markets(query, limit=limit)
        click.echo(_ok(result))
    except SimError as e:
        click.echo(_err(e))
        sys.exit(1)
    finally:
        engine.close()


@markets.command("get")
@click.argument("slug_or_id")
@click.pass_context
def markets_get(ctx: click.Context, slug_or_id: str) -> None:
    """Get full market details."""
    engine = _get_engine(ctx)
    try:
        market = engine.api.get_market(slug_or_id)
        click.echo(_ok(market))
    except SimError as e:
        click.echo(_err(e))
        sys.exit(1)
    finally:
        engine.close()


# ---------------------------------------------------------------------------
# Price & book commands
# ---------------------------------------------------------------------------

@main.command()
@click.argument("slug_or_id")
@click.pass_context
def price(ctx: click.Context, slug_or_id: str) -> None:
    """Show YES/NO midpoint prices and spread."""
    engine = _get_engine(ctx)
    try:
        market = engine.api.get_market(slug_or_id)
        yes_mid = engine.api.get_midpoint(market.yes_token_id)
        no_mid = engine.api.get_midpoint(market.no_token_id)
        spread = abs(yes_mid - (1.0 - no_mid)) if yes_mid and no_mid else 0.0
        click.echo(_ok({
            "market": market.slug,
            "question": market.question,
            "yes_price": yes_mid,
            "no_price": no_mid,
            "spread": spread,
        }))
    except SimError as e:
        click.echo(_err(e))
        sys.exit(1)
    finally:
        engine.close()


@main.command()
@click.argument("slug_or_id")
@click.option("--depth", type=int, default=10, help="Number of levels to show.")
@click.pass_context
def book(ctx: click.Context, slug_or_id: str, depth: int) -> None:
    """Show the order book for a market's YES token."""
    engine = _get_engine(ctx)
    try:
        market = engine.api.get_market(slug_or_id)
        ob = engine.api.get_order_book(market.yes_token_id)
        bids = sorted(ob.bids, key=lambda l: l.price, reverse=True)[:depth]
        asks = sorted(ob.asks, key=lambda l: l.price)[:depth]
        click.echo(_ok({
            "market": market.slug,
            "token_id": market.yes_token_id,
            "bids": [{"price": l.price, "size": l.size} for l in bids],
            "asks": [{"price": l.price, "size": l.size} for l in asks],
        }))
    except SimError as e:
        click.echo(_err(e))
        sys.exit(1)
    finally:
        engine.close()


# ---------------------------------------------------------------------------
# Trading commands
# ---------------------------------------------------------------------------

@main.command()
@click.argument("slug_or_id")
@click.argument("outcome")
@click.argument("amount_usd", type=float)
@click.option("--type", "order_type", type=click.Choice(["fok", "fak"]), default="fok")
@click.pass_context
def buy(ctx: click.Context, slug_or_id: str, outcome: str, amount_usd: float, order_type: str) -> None:
    """Buy shares: spend USD, buy at market price."""
    engine = _get_engine(ctx)
    try:
        result = engine.buy(slug_or_id, outcome, amount_usd, order_type)
        click.echo(_ok(result))
    except SimError as e:
        click.echo(_err(e))
        sys.exit(1)
    finally:
        engine.close()


@main.command()
@click.argument("slug_or_id")
@click.argument("outcome")
@click.argument("shares", type=float)
@click.option("--type", "order_type", type=click.Choice(["fok", "fak"]), default="fok")
@click.pass_context
def sell(ctx: click.Context, slug_or_id: str, outcome: str, shares: float, order_type: str) -> None:
    """Sell shares: sell shares, receive USD at market price."""
    engine = _get_engine(ctx)
    try:
        result = engine.sell(slug_or_id, outcome, shares, order_type)
        click.echo(_ok(result))
    except SimError as e:
        click.echo(_err(e))
        sys.exit(1)
    finally:
        engine.close()


# ---------------------------------------------------------------------------
# Portfolio & history commands
# ---------------------------------------------------------------------------

@main.command()
@click.pass_context
def portfolio(ctx: click.Context) -> None:
    """Show open positions with live prices and unrealized P&L."""
    engine = _get_engine(ctx)
    try:
        data = engine.get_portfolio()
        click.echo(_ok(data))
    except SimError as e:
        click.echo(_err(e))
        sys.exit(1)
    finally:
        engine.close()


@main.command()
@click.option("--limit", type=int, default=50)
@click.pass_context
def history(ctx: click.Context, limit: int) -> None:
    """Show trade history."""
    engine = _get_engine(ctx)
    try:
        trades = engine.get_history(limit)
        click.echo(_ok(trades))
    except SimError as e:
        click.echo(_err(e))
        sys.exit(1)
    finally:
        engine.close()


# ---------------------------------------------------------------------------
# Resolution commands
# ---------------------------------------------------------------------------

@main.command()
@click.argument("slug_or_id", required=False)
@click.option("--all", "resolve_all", is_flag=True, help="Resolve all closed markets.")
@click.pass_context
def resolve(ctx: click.Context, slug_or_id: str | None, resolve_all: bool) -> None:
    """Resolve a market or all closed markets."""
    engine = _get_engine(ctx)
    try:
        if resolve_all:
            results = engine.resolve_all()
        elif slug_or_id:
            results = engine.resolve_market(slug_or_id)
        else:
            click.echo(
                json.dumps(
                    {"ok": False, "error": "Provide a market slug/id or --all", "code": "MISSING_ARGUMENT"},
                    indent=2,
                )
            )
            sys.exit(1)
        click.echo(_ok(results))
    except SimError as e:
        click.echo(_err(e))
        sys.exit(1)
    finally:
        engine.close()


# ---------------------------------------------------------------------------
# Analytics commands
# ---------------------------------------------------------------------------

@main.command()
@click.option("--card", is_flag=True, default=False, help="Output shareable stats card for chat.")
@click.option("--plain", is_flag=True, default=False, help="Plain text card (no markdown).")
@click.option("--tweet", is_flag=True, default=False, help="X/Twitter optimized card.")
@click.pass_context
def stats(ctx: click.Context, card: bool, plain: bool, tweet: bool) -> None:
    """Show performance analytics (win rate, ROI, profit, max drawdown)."""
    from pm_trader.analytics import compute_stats

    engine = _get_engine(ctx)
    try:
        account = engine.get_account()
        trades = engine.get_history(limit=10_000)
        portfolio = engine.get_portfolio()
        positions_value = sum(p["current_value"] for p in portfolio)
        result = compute_stats(trades, account, positions_value)
        if tweet or card or plain:
            from pm_trader.card import generate_card, generate_card_plain, generate_tweet
            account_name = ctx.obj["account"]
            if tweet:
                click.echo(generate_tweet(result, account_name, portfolio))
            elif plain:
                click.echo(generate_card_plain(result, account_name, portfolio))
            else:
                click.echo(generate_card(result, account_name, portfolio))
        else:
            click.echo(_ok(result))
    except SimError as e:
        click.echo(_err(e))
        sys.exit(1)
    finally:
        engine.close()


@main.command()
@click.pass_context
def leaderboard(ctx: click.Context) -> None:
    """Generate a leaderboard entry for ranking and PK."""
    from pm_trader.analytics import compute_stats

    engine = _get_engine(ctx)
    try:
        account = engine.get_account()
        trades = engine.get_history(limit=10_000)
        portfolio = engine.get_portfolio()
        positions_value = sum(p["current_value"] for p in portfolio)
        result = compute_stats(trades, account, positions_value)

        first_trade = trades[-1].created_at if trades else None
        last_trade = trades[0].created_at if trades else None
        account_name = ctx.obj["account"]

        click.echo(_ok({
            "account": account_name,
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
            "open_positions": len(portfolio),
            "qualified": result.get("total_trades", 0) >= 10,
        }))
    except SimError as e:
        click.echo(_err(e))
        sys.exit(1)
    finally:
        engine.close()


@main.command()
@click.argument("account_a")
@click.argument("account_b")
@click.pass_context
def pk(ctx: click.Context, account_a: str, account_b: str) -> None:
    """Battle: who's the better trader? Compare two accounts head-to-head."""
    from pm_trader.analytics import compute_stats
    from pm_trader.card import generate_pk_card

    base = ctx.obj.get("data_dir") or Path.home() / ".pm-trader"

    try:
        results = {}
        for name in (account_a, account_b):
            data_dir = base / name
            engine = Engine(data_dir)
            try:
                account = engine.get_account()
                trades = engine.get_history(limit=10_000)
                portfolio = engine.get_portfolio()
                positions_value = sum(p["current_value"] for p in portfolio)
                results[name] = compute_stats(trades, account, positions_value)
            finally:
                engine.close()

        card = generate_pk_card(results[account_a], account_a, results[account_b], account_b)
        click.echo(card)
    except SimError as e:
        click.echo(_err(e))
        sys.exit(1)


# ---------------------------------------------------------------------------
# Export commands
# ---------------------------------------------------------------------------

@main.group()
def export() -> None:
    """Export trades or positions to CSV/JSON."""
    pass


@export.command("trades")
@click.option("--format", "fmt", type=click.Choice(["csv", "json"]), default="csv")
@click.option("--output", "output_file", type=click.Path(path_type=Path), default=None)
@click.option("--limit", type=int, default=10_000)
@click.pass_context
def export_trades(ctx: click.Context, fmt: str, output_file: Path | None, limit: int) -> None:
    """Export trade history."""
    from pm_trader.export import export_trades_csv, export_trades_json

    engine = _get_engine(ctx)
    try:
        trades = engine.get_history(limit)
        if fmt == "csv":
            content = export_trades_csv(trades)
        else:
            content = export_trades_json(trades)

        if output_file:
            output_file.write_text(content)
            click.echo(_ok({"exported": len(trades), "file": str(output_file)}))
        else:
            click.echo(content)
    except SimError as e:
        click.echo(_err(e))
        sys.exit(1)
    finally:
        engine.close()


@export.command("positions")
@click.option("--format", "fmt", type=click.Choice(["csv", "json"]), default="csv")
@click.option("--output", "output_file", type=click.Path(path_type=Path), default=None)
@click.pass_context
def export_positions(ctx: click.Context, fmt: str, output_file: Path | None) -> None:
    """Export current positions."""
    from pm_trader.export import export_positions_csv, export_positions_json

    engine = _get_engine(ctx)
    try:
        portfolio = engine.get_portfolio()
        if fmt == "csv":
            content = export_positions_csv(portfolio)
        else:
            content = export_positions_json(portfolio)

        if output_file:
            output_file.write_text(content)
            click.echo(_ok({"exported": len(portfolio), "file": str(output_file)}))
        else:
            click.echo(content)
    except SimError as e:
        click.echo(_err(e))
        sys.exit(1)
    finally:
        engine.close()


# ---------------------------------------------------------------------------
# Account management commands
# ---------------------------------------------------------------------------

@main.group()
def accounts() -> None:
    """Manage named accounts."""
    pass


@accounts.command("list")
@click.pass_context
def accounts_list(ctx: click.Context) -> None:
    """List all accounts."""
    base = ctx.obj["data_dir"]
    result = []
    if base.exists():
        for entry in sorted(base.iterdir()):
            if entry.is_dir() and (entry / "paper.db").exists():
                engine = Engine(entry)
                try:
                    account = engine.db.get_account()
                    if account:
                        result.append({
                            "name": entry.name,
                            "starting_balance": account.starting_balance,
                            "cash": account.cash,
                            "created_at": account.created_at,
                        })
                finally:
                    engine.close()
    click.echo(_ok(result))


@accounts.command("create")
@click.argument("name")
@click.option("--balance", type=float, default=10_000.0)
@click.pass_context
def accounts_create(ctx: click.Context, name: str, balance: float) -> None:
    """Create a new named account."""
    base = ctx.obj["data_dir"]
    acct_dir = base / name
    if (acct_dir / "paper.db").exists():
        click.echo(json.dumps(
            {"ok": False, "error": f"Account '{name}' already exists", "code": "ACCOUNT_EXISTS"},
            indent=2,
        ))
        sys.exit(1)
    engine = Engine(acct_dir)
    try:
        account = engine.init_account(balance)
        click.echo(_ok({"name": name, **_serialize(account)}))
    finally:
        engine.close()


@accounts.command("delete")
@click.argument("name")
@click.option("--confirm", is_flag=True, required=True, help="Required to confirm deletion.")
@click.pass_context
def accounts_delete(ctx: click.Context, name: str, confirm: bool) -> None:
    """Delete a named account and all its data."""
    import shutil
    base = ctx.obj["data_dir"]
    acct_dir = base / name
    if not acct_dir.exists():
        click.echo(json.dumps(
            {"ok": False, "error": f"Account '{name}' not found", "code": "ACCOUNT_NOT_FOUND"},
            indent=2,
        ))
        sys.exit(1)
    shutil.rmtree(acct_dir)
    click.echo(_ok({"deleted": name}))


# ---------------------------------------------------------------------------
# Benchmark commands
# ---------------------------------------------------------------------------

@main.group()
def benchmark() -> None:
    """Run and compare trading strategies."""
    pass


@benchmark.command("run")
@click.argument("strategy_path")
@click.option("--balance", type=float, default=10_000.0)
@click.pass_context
def benchmark_run(ctx: click.Context, strategy_path: str, balance: float) -> None:
    """Run a strategy: pm-trader benchmark run module.function"""
    from pm_trader.benchmark import run_strategy

    try:
        result = run_strategy(strategy_path, balance=balance)
        click.echo(_ok(result))
    except Exception as e:
        click.echo(json.dumps(
            {"ok": False, "error": str(e), "code": "BENCHMARK_ERROR"},
            indent=2,
        ))
        sys.exit(1)


@benchmark.command("pk")
@click.argument("strategy_a")
@click.argument("strategy_b")
@click.option("--name-a", default="player_a", help="Display name for strategy A.")
@click.option("--name-b", default="player_b", help="Display name for strategy B.")
@click.option("--balance", type=float, default=10_000.0)
@click.pass_context
def benchmark_pk(
    ctx: click.Context, strategy_a: str, strategy_b: str,
    name_a: str, name_b: str, balance: float,
) -> None:
    """PK battle: run two strategies head-to-head."""
    from pm_trader.benchmark import pk_battle

    try:
        result = pk_battle(strategy_a, strategy_b, name_a, name_b, balance)
        click.echo(result["card"])
        click.echo(f"\nWinner: {result['winner']}")
    except Exception as e:
        click.echo(json.dumps(
            {"ok": False, "error": str(e), "code": "PK_ERROR"},
            indent=2,
        ))
        sys.exit(1)


@benchmark.command("compare")
@click.argument("account_names", nargs=-1, required=True)
@click.pass_context
def benchmark_compare(ctx: click.Context, account_names: tuple[str, ...]) -> None:
    """Compare analytics across named accounts."""
    from pm_trader.benchmark import compare_accounts

    base = ctx.obj["data_dir"]
    data_dirs = {}
    for name in account_names:
        acct_dir = base / name
        if not (acct_dir / "paper.db").exists():
            click.echo(json.dumps(
                {"ok": False, "error": f"Account '{name}' not found", "code": "ACCOUNT_NOT_FOUND"},
                indent=2,
            ))
            sys.exit(1)
        data_dirs[name] = acct_dir

    try:
        results = compare_accounts(data_dirs)
        click.echo(_ok(results))
    except Exception as e:
        click.echo(json.dumps(
            {"ok": False, "error": str(e), "code": "BENCHMARK_ERROR"},
            indent=2,
        ))
        sys.exit(1)


# ---------------------------------------------------------------------------
# Limit order commands
# ---------------------------------------------------------------------------

@main.group()
def orders() -> None:
    """Manage limit orders (GTC/GTD)."""
    pass


@orders.command("place")
@click.argument("slug_or_id")
@click.argument("outcome")
@click.argument("side", type=click.Choice(["buy", "sell"]))
@click.argument("amount", type=float)
@click.argument("limit_price", type=float)
@click.option("--type", "order_type", type=click.Choice(["gtc", "gtd"]), default="gtc")
@click.option("--expires", "expires_at", default=None, help="ISO timestamp for GTD expiry.")
@click.pass_context
def orders_place(
    ctx: click.Context, slug_or_id: str, outcome: str, side: str,
    amount: float, limit_price: float, order_type: str, expires_at: str | None,
) -> None:
    """Place a limit order: pm-trader orders place SLUG yes buy 100 0.55"""
    engine = _get_engine(ctx)
    try:
        result = engine.place_limit_order(
            slug_or_id, outcome, side, amount, limit_price,
            order_type=order_type, expires_at=expires_at,
        )
        click.echo(_ok(result))
    except SimError as e:
        click.echo(_err(e))
        sys.exit(1)
    finally:
        engine.close()


@orders.command("list")
@click.pass_context
def orders_list(ctx: click.Context) -> None:
    """List all pending limit orders."""
    engine = _get_engine(ctx)
    try:
        result = engine.get_pending_orders()
        click.echo(_ok(result))
    except SimError as e:
        click.echo(_err(e))
        sys.exit(1)
    finally:
        engine.close()


@orders.command("cancel")
@click.argument("order_id", type=int)
@click.pass_context
def orders_cancel(ctx: click.Context, order_id: int) -> None:
    """Cancel a pending limit order."""
    engine = _get_engine(ctx)
    try:
        result = engine.cancel_limit_order(order_id)
        if result is None:
            click.echo(json.dumps(
                {"ok": False, "error": f"Order {order_id} not found or not pending", "code": "ORDER_NOT_FOUND"},
                indent=2,
            ))
            sys.exit(1)
        click.echo(_ok(result))
    except SimError as e:
        click.echo(_err(e))
        sys.exit(1)
    finally:
        engine.close()


@orders.command("check")
@click.pass_context
def orders_check(ctx: click.Context) -> None:
    """Check pending orders against live prices and execute fills."""
    engine = _get_engine(ctx)
    try:
        results = engine.check_orders()
        click.echo(_ok(results))
    except SimError as e:
        click.echo(_err(e))
        sys.exit(1)
    finally:
        engine.close()


# ---------------------------------------------------------------------------
# Watch command
# ---------------------------------------------------------------------------

@main.command()
@click.argument("slugs_or_ids", nargs=-1, required=True)
@click.option("--outcome", "outcomes", multiple=True, default=["yes"])
@click.pass_context
def watch(ctx: click.Context, slugs_or_ids: tuple[str, ...], outcomes: tuple[str, ...]) -> None:
    """Get live midpoint prices for markets."""
    engine = _get_engine(ctx)
    try:
        engine._require_account()
        result = engine.watch_prices(list(slugs_or_ids), list(outcomes))
        click.echo(_ok(result))
    except SimError as e:
        click.echo(_err(e))
        sys.exit(1)
    except ValueError as e:
        click.echo(json.dumps(
            {"ok": False, "error": str(e), "code": "INVALID_OUTCOME"},
            indent=2,
        ))
        sys.exit(1)
    finally:
        engine.close()


# ---------------------------------------------------------------------------
# MCP server
# ---------------------------------------------------------------------------


@main.command()
def mcp() -> None:
    """Start MCP server (stdio transport) for AI agent integration."""
    from pm_trader.mcp_server import main as mcp_main
    mcp_main()
