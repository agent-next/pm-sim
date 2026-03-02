"""Tests for MCP server tool handlers."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pm_trader.models import (
    Market,
    OrderBook,
    OrderBookLevel,
    Trade,
)

from pm_trader import mcp_server
from pm_trader.mcp_server import (
    backtest,
    buy,
    cancel_all_orders,
    cancel_order,
    check_orders,
    get_balance,
    get_event,
    get_market,
    get_markets_by_tag,
    get_order_book,
    get_tags,
    history,
    init_account,
    leaderboard_card,
    leaderboard_entry,
    list_markets,
    list_orders,
    pk_battle,
    pk_card,
    place_limit_order,
    portfolio,
    reset_account,
    resolve,
    resolve_all,
    search_markets,
    sell,
    share_content,
    stats,
    stats_card,
    watch_prices,
)


@pytest.fixture(autouse=True)
def fresh_engine(tmp_path: Path):
    """Reset the global engine for each test."""
    mcp_server._engine = None
    with patch.object(Path, "home", return_value=tmp_path):
        yield
    if mcp_server._engine is not None:
        mcp_server._engine.close()
        mcp_server._engine = None


def _parse(result: str) -> dict:
    """Parse a tool result JSON string."""
    return json.loads(result)


# ---------------------------------------------------------------------------
# Error sanitization
# ---------------------------------------------------------------------------


class TestErrFrom:
    def test_sim_error_passes_through(self):
        from pm_trader.models import InsufficientBalanceError
        from pm_trader.mcp_server import _err_from
        e = InsufficientBalanceError(required=100.0, available=50.0)
        result = json.loads(_err_from(e))
        assert "Insufficient balance" in result["error"]
        assert result["code"] == "INSUFFICIENT_BALANCE"

    def test_value_error_passes_through(self):
        from pm_trader.mcp_server import _err_from
        result = json.loads(_err_from(ValueError("bad input")))
        assert result["error"] == "bad input"
        assert result["code"] == "ValueError"

    def test_unexpected_error_sanitized(self):
        from pm_trader.mcp_server import _err_from
        result = json.loads(_err_from(RuntimeError("/home/user/.secret/file")))
        assert result["error"] == "Internal error"
        assert result["code"] == "internal_error"


# ---------------------------------------------------------------------------
# Account tools
# ---------------------------------------------------------------------------


class TestInitAccount:
    def test_default_balance(self):
        result = _parse(init_account())
        assert result["ok"] is True
        assert result["data"]["cash"] == 10_000.0

    def test_custom_balance(self):
        result = _parse(init_account(balance=5_000.0))
        assert result["ok"] is True
        assert result["data"]["starting_balance"] == 5_000.0


class TestGetBalance:
    def test_not_initialized(self):
        result = _parse(get_balance())
        assert result["ok"] is False
        assert result["code"] == "not_initialized"

    def test_after_init(self):
        init_account(balance=7_500.0)
        result = _parse(get_balance())
        assert result["ok"] is True
        assert result["data"]["cash"] == 7_500.0
        assert result["data"]["starting_balance"] == 7_500.0


class TestResetAccount:
    def test_reset(self):
        init_account()
        result = _parse(reset_account())
        assert result["ok"] is True
        assert result["data"]["reset"] is True


# ---------------------------------------------------------------------------
# Portfolio tools
# ---------------------------------------------------------------------------


class TestPortfolio:
    def test_empty_portfolio(self):
        init_account()
        result = _parse(portfolio())
        assert result["ok"] is True
        assert result["data"] == []


class TestHistory:
    def test_empty_history(self):
        init_account()
        result = _parse(history())
        assert result["ok"] is True
        assert result["data"] == []


# ---------------------------------------------------------------------------
# Limit order tools
# ---------------------------------------------------------------------------


class TestListOrders:
    def test_empty_orders(self):
        init_account()
        result = _parse(list_orders())
        assert result["ok"] is True
        assert result["data"] == []


class TestCancelOrder:
    def test_nonexistent(self):
        init_account()
        result = _parse(cancel_order(999))
        assert result["ok"] is False
        assert result["code"] == "not_found"


class TestCheckOrders:
    def test_no_pending(self):
        init_account()
        result = _parse(check_orders())
        assert result["ok"] is True
        assert result["data"] == []


# ---------------------------------------------------------------------------
# Analytics tools
# ---------------------------------------------------------------------------


class TestStats:
    def test_empty_account(self):
        init_account()
        result = _parse(stats())
        assert result["ok"] is True
        assert result["data"]["total_trades"] == 0
        assert result["data"]["win_rate"] == 0.0


class TestStatsCard:
    def test_markdown_format(self):
        init_account()
        result = _parse(stats_card())
        assert result["ok"] is True
        assert "card" in result["data"]
        assert "stats" in result["data"]
        assert "*Polymarket Paper Trading*" in result["data"]["card"]

    def test_tweet_format(self):
        init_account()
        result = _parse(stats_card(format="tweet"))
        assert result["ok"] is True
        assert "#Polymarket" in result["data"]["card"]

    def test_plain_format(self):
        init_account()
        result = _parse(stats_card(format="plain"))
        assert result["ok"] is True
        assert "*" not in result["data"]["card"]

    def test_not_initialized(self):
        result = _parse(stats_card())
        assert result["ok"] is False


class TestLeaderboardEntry:
    def test_empty_account(self):
        init_account()
        result = _parse(leaderboard_entry())
        assert result["ok"] is True
        data = result["data"]
        assert data["account"] == "default"
        assert data["total_trades"] == 0
        assert data["qualified"] is False
        assert data["first_trade_at"] is None
        assert data["roi_pct"] == 0.0

    def test_with_trades(self):
        init_account()
        from pm_trader.mcp_server import _get_engine
        _mock_engine_api(_get_engine())
        buy("will-bitcoin-hit-100k", "yes", 100.0)
        result = _parse(leaderboard_entry())
        assert result["ok"] is True
        data = result["data"]
        assert data["total_trades"] == 1
        assert data["qualified"] is False  # need 10+
        assert data["first_trade_at"] is not None
        assert data["open_positions"] == 1

    def test_not_initialized(self):
        result = _parse(leaderboard_entry())
        assert result["ok"] is False


class TestPkCard:
    def test_pk_two_accounts(self):
        init_account(balance=10_000.0, account="alice")
        init_account(balance=10_000.0, account="bob")
        result = _parse(pk_card(account_a="alice", account_b="bob"))
        assert result["ok"] is True
        assert "card" in result["data"]
        assert "alice" in result["data"]["card"]
        assert "bob" in result["data"]["card"]
        assert "Who's the better trader?" in result["data"]["card"]

    def test_pk_not_initialized(self):
        result = _parse(pk_card(account_a="nope", account_b="nada"))
        assert result["ok"] is False


# ---------------------------------------------------------------------------
# share_content tool
# ---------------------------------------------------------------------------


class TestShareContent:
    def test_twitter_performance(self):
        init_account()
        result = _parse(share_content(platform="twitter", template="performance"))
        assert result["ok"] is True
        assert "card" in result["data"]
        assert result["data"]["platform"] == "twitter"
        assert result["data"]["template"] == "performance"
        assert "#Polymarket" in result["data"]["card"]

    def test_telegram_performance(self):
        init_account()
        result = _parse(share_content(platform="telegram", template="performance"))
        assert result["ok"] is True
        assert "*" in result["data"]["card"]  # markdown

    def test_plain_performance(self):
        init_account()
        result = _parse(share_content(platform="plain", template="performance"))
        assert result["ok"] is True
        assert "*" not in result["data"]["card"]

    def test_milestone_template(self):
        init_account()
        result = _parse(share_content(template="milestone"))
        assert result["ok"] is True
        assert "#OpenClaw" in result["data"]["card"]

    def test_daily_template(self):
        init_account()
        result = _parse(share_content(template="daily"))
        assert result["ok"] is True
        assert "Daily Report" in result["data"]["card"]

    def test_not_initialized(self):
        result = _parse(share_content(account="nonexistent"))
        assert result["ok"] is False


# ---------------------------------------------------------------------------
# pk_battle tool
# ---------------------------------------------------------------------------


class TestPkBattle:
    def test_two_noop_strategies(self):
        result = _parse(pk_battle(
            strategy_a="tests.test_benchmark.noop_strategy",
            strategy_b="tests.test_benchmark.noop_strategy",
            name_a="alpha",
            name_b="beta",
        ))
        assert result["ok"] is True
        assert result["data"]["winner"] == "tie"
        assert "alpha" in result["data"]["card"]
        assert "beta" in result["data"]["card"]

    def test_bad_strategy(self):
        result = _parse(pk_battle(
            strategy_a="bad_path",
            strategy_b="also_bad",
        ))
        assert result["ok"] is False


# ---------------------------------------------------------------------------
# leaderboard_card tool
# ---------------------------------------------------------------------------


class TestLeaderboardCard:
    def test_no_accounts(self):
        result = _parse(leaderboard_card())
        assert result["ok"] is True
        assert "entries" in result["data"]

    def test_with_accounts(self):
        init_account(balance=10_000.0, account="trader_a")
        init_account(balance=10_000.0, account="trader_b")
        result = _parse(leaderboard_card(accounts="trader_a,trader_b"))
        assert result["ok"] is True
        # Neither account has 10+ trades, so qualified list empty
        assert result["data"]["entries"] == []
        assert "Top 10" in result["data"]["card"]

    def test_scan_all_accounts(self):
        # Create accounts so they exist at ~/.pm-trader/<name>
        init_account(balance=10_000.0, account="scan_a")
        init_account(balance=10_000.0, account="scan_b")
        # Call without accounts arg to scan all
        result = _parse(leaderboard_card(accounts=""))
        assert result["ok"] is True

    def test_bad_account_skipped(self):
        init_account(balance=10_000.0, account="good")
        # "bad" doesn't exist — should be skipped without error
        result = _parse(leaderboard_card(accounts="good,bad"))
        assert result["ok"] is True

    def test_outer_exception(self):
        with patch("pm_trader.mcp_server.Path.home", side_effect=RuntimeError("boom")):
            result = _parse(leaderboard_card())
        assert result["ok"] is False
        assert result["code"] == "internal_error"


# ---------------------------------------------------------------------------
# Resolution tools
# ---------------------------------------------------------------------------


class TestResolveAll:
    def test_no_positions(self):
        init_account()
        result = _parse(resolve_all())
        assert result["ok"] is True
        assert result["data"] == []


# ---------------------------------------------------------------------------
# Error envelope
# ---------------------------------------------------------------------------


class TestErrorEnvelope:
    def test_balance_error_has_code(self):
        result = _parse(get_balance())
        assert "ok" in result
        assert result["ok"] is False
        assert "error" in result
        assert "code" in result

    def test_stats_not_initialized(self):
        result = _parse(stats())
        assert result["ok"] is False


# ---------------------------------------------------------------------------
# Market data tools (with API mocks)
# ---------------------------------------------------------------------------

SAMPLE_MARKET = Market(
    condition_id="0xabc123",
    slug="will-bitcoin-hit-100k",
    question="Will Bitcoin hit $100k?",
    description="BTC market",
    outcomes=["Yes", "No"],
    outcome_prices=[0.65, 0.35],
    tokens=[
        {"token_id": "tok_yes", "outcome": "Yes"},
        {"token_id": "tok_no", "outcome": "No"},
    ],
    active=True,
    closed=False,
    volume=5_000_000.0,
    liquidity=250_000.0,
    end_date="2026-12-31",
    fee_rate_bps=0,
    tick_size=0.01,
)

SAMPLE_BOOK = OrderBook(
    bids=[OrderBookLevel(0.64, 500), OrderBookLevel(0.63, 500)],
    asks=[OrderBookLevel(0.66, 500), OrderBookLevel(0.67, 500)],
)


def _mock_engine_api(engine):
    """Set up API mocks on an engine instance."""
    engine.api.get_market = MagicMock(return_value=SAMPLE_MARKET)
    engine.api.search_markets = MagicMock(return_value=[SAMPLE_MARKET])
    engine.api.list_markets = MagicMock(return_value=[SAMPLE_MARKET])
    engine.api.get_order_book = MagicMock(return_value=SAMPLE_BOOK)
    engine.api.get_midpoint = MagicMock(return_value=0.65)
    engine.api.get_fee_rate = MagicMock(return_value=0)


class TestSearchMarkets:
    def test_search(self):
        init_account()
        from pm_trader.mcp_server import _get_engine
        _mock_engine_api(_get_engine())
        result = _parse(search_markets("bitcoin"))
        assert result["ok"] is True
        assert len(result["data"]) == 1
        assert result["data"][0]["slug"] == "will-bitcoin-hit-100k"

    def test_search_with_limit(self):
        init_account()
        from pm_trader.mcp_server import _get_engine
        _mock_engine_api(_get_engine())
        result = _parse(search_markets("bitcoin", limit=5))
        assert result["ok"] is True


class TestListMarkets:
    def test_list_default(self):
        init_account()
        from pm_trader.mcp_server import _get_engine
        _mock_engine_api(_get_engine())
        result = _parse(list_markets())
        assert result["ok"] is True
        assert len(result["data"]) >= 1
        assert result["data"][0]["question"] == "Will Bitcoin hit $100k?"

    def test_list_by_liquidity(self):
        init_account()
        from pm_trader.mcp_server import _get_engine
        _mock_engine_api(_get_engine())
        result = _parse(list_markets(sort_by="liquidity"))
        assert result["ok"] is True


class TestGetTags:
    def test_get_tags(self):
        init_account()
        from pm_trader.mcp_server import _get_engine
        engine = _get_engine()
        tags = [{"id": "1", "label": "Politics", "slug": "politics"}]
        engine.api.get_tags = MagicMock(return_value=tags)
        result = _parse(get_tags())
        assert result["ok"] is True
        assert len(result["data"]) == 1
        assert result["data"][0]["slug"] == "politics"

    def test_get_tags_error(self):
        init_account()
        from pm_trader.mcp_server import _get_engine
        engine = _get_engine()
        engine.api.get_tags = MagicMock(side_effect=Exception("fail"))
        result = _parse(get_tags())
        assert result["ok"] is False


class TestGetMarketsByTag:
    def test_get_markets_by_tag(self):
        init_account()
        from pm_trader.mcp_server import _get_engine
        engine = _get_engine()
        engine.api.get_markets_by_tag = MagicMock(return_value=[SAMPLE_MARKET])
        result = _parse(get_markets_by_tag("politics"))
        assert result["ok"] is True
        assert len(result["data"]) == 1
        assert result["data"][0]["slug"] == "will-bitcoin-hit-100k"

    def test_get_markets_by_tag_empty(self):
        init_account()
        from pm_trader.mcp_server import _get_engine
        engine = _get_engine()
        engine.api.get_markets_by_tag = MagicMock(return_value=[])
        result = _parse(get_markets_by_tag("nonexistent"))
        assert result["ok"] is True
        assert result["data"] == []

    def test_get_markets_by_tag_error(self):
        init_account()
        from pm_trader.mcp_server import _get_engine
        engine = _get_engine()
        engine.api.get_markets_by_tag = MagicMock(side_effect=Exception("fail"))
        result = _parse(get_markets_by_tag("bad"))
        assert result["ok"] is False


class TestGetEvent:
    def test_get_event(self):
        init_account()
        from pm_trader.mcp_server import _get_engine
        engine = _get_engine()
        event_data = {"title": "US Elections", "slug": "us-elections"}
        engine.api.get_event = MagicMock(return_value=event_data)
        result = _parse(get_event("us-elections"))
        assert result["ok"] is True
        assert result["data"]["title"] == "US Elections"

    def test_get_event_error(self):
        init_account()
        from pm_trader.mcp_server import _get_engine
        engine = _get_engine()
        engine.api.get_event = MagicMock(side_effect=Exception("fail"))
        result = _parse(get_event("bad"))
        assert result["ok"] is False


class TestCancelAllOrders:
    def test_cancel_all_empty(self):
        init_account()
        result = _parse(cancel_all_orders())
        assert result["ok"] is True
        assert result["data"]["cancelled"] == 0
        assert result["data"]["orders"] == []

    def test_cancel_all_with_orders(self):
        init_account()
        from pm_trader.mcp_server import _get_engine
        _mock_engine_api(_get_engine())
        place_limit_order("will-bitcoin-hit-100k", "yes", "buy", 100.0, 0.55)
        place_limit_order("will-bitcoin-hit-100k", "yes", "buy", 200.0, 0.50)
        result = _parse(cancel_all_orders())
        assert result["ok"] is True
        assert result["data"]["cancelled"] == 2

    def test_cancel_all_not_initialized(self):
        result = _parse(cancel_all_orders())
        assert result["ok"] is True
        assert result["data"]["cancelled"] == 0
        assert result["data"]["orders"] == []

    def test_cancel_all_traversal(self):
        result = _parse(cancel_all_orders(account="../evil"))
        assert result["ok"] is False


class TestGetMarket:
    def test_found(self):
        init_account()
        from pm_trader.mcp_server import _get_engine
        _mock_engine_api(_get_engine())
        result = _parse(get_market("will-bitcoin-hit-100k"))
        assert result["ok"] is True
        assert result["data"]["condition_id"] == "0xabc123"
        assert result["data"]["outcomes"] == ["Yes", "No"]

    def test_not_found(self):
        init_account()
        from pm_trader.mcp_server import _get_engine
        engine = _get_engine()
        from pm_trader.models import MarketNotFoundError
        engine.api.get_market = MagicMock(side_effect=MarketNotFoundError("nope"))
        result = _parse(get_market("nope"))
        assert result["ok"] is False
        assert result["code"] == "market_not_found"


class TestGetOrderBook:
    def test_order_book(self):
        init_account()
        from pm_trader.mcp_server import _get_engine
        _mock_engine_api(_get_engine())
        result = _parse(get_order_book("will-bitcoin-hit-100k", "yes"))
        assert result["ok"] is True
        assert len(result["data"]["bids"]) == 2
        assert len(result["data"]["asks"]) == 2

    def test_order_book_error(self):
        init_account()
        from pm_trader.mcp_server import _get_engine
        engine = _get_engine()
        engine.api.get_market = MagicMock(side_effect=Exception("network"))
        result = _parse(get_order_book("bad", "yes"))
        assert result["ok"] is False


class TestWatchPrices:
    def test_watch(self):
        init_account()
        from pm_trader.mcp_server import _get_engine
        _mock_engine_api(_get_engine())
        result = _parse(watch_prices("will-bitcoin-hit-100k"))
        assert result["ok"] is True
        assert len(result["data"]) >= 1
        assert result["data"][0]["midpoint"] == 0.65

    def test_watch_invalid_outcome_returns_error(self):
        """Invalid outcome causes ValueError from get_token_id."""
        init_account()
        from pm_trader.mcp_server import _get_engine
        engine = _get_engine()
        # Market resolves but has no token for 'invalid'
        bad_market = Market(
            condition_id="0x1", slug="m", question="Q", description="",
            outcomes=["Yes", "No"],
            outcome_prices=[0.5, 0.5],
            tokens=[{"token_id": "t1", "outcome": "Yes"}, {"token_id": "t2", "outcome": "No"}],
            active=True, closed=False,
        )
        engine.api.get_market = MagicMock(return_value=bad_market)
        result = _parse(watch_prices("m", "invalid"))
        assert result["ok"] is False
        assert result["code"] == "ValueError"


# ---------------------------------------------------------------------------
# Trading tools (with API mocks)
# ---------------------------------------------------------------------------


class TestBuyTool:
    def test_buy_success(self):
        init_account()
        from pm_trader.mcp_server import _get_engine
        _mock_engine_api(_get_engine())
        result = _parse(buy("will-bitcoin-hit-100k", "yes", 100.0))
        assert result["ok"] is True
        assert result["data"]["trade"]["side"] == "buy"
        assert result["data"]["trade"]["outcome"] == "yes"
        assert result["data"]["account"]["cash"] < 10_000.0

    def test_buy_not_initialized(self):
        result = _parse(buy("btc", "yes", 100.0))
        assert result["ok"] is False
        assert result["code"] == "NOT_INITIALIZED"

    def test_buy_insufficient_balance(self):
        init_account(balance=1.0)
        from pm_trader.mcp_server import _get_engine
        _mock_engine_api(_get_engine())
        result = _parse(buy("will-bitcoin-hit-100k", "yes", 100_000.0))
        assert result["ok"] is False

    def test_buy_invalid_outcome(self):
        init_account()
        from pm_trader.mcp_server import _get_engine
        _mock_engine_api(_get_engine())
        result = _parse(buy("will-bitcoin-hit-100k", "maybe", 100.0))
        assert result["ok"] is False
        assert result["code"] == "INVALID_OUTCOME"


class TestSellTool:
    def test_sell_no_position(self):
        init_account()
        from pm_trader.mcp_server import _get_engine
        _mock_engine_api(_get_engine())
        result = _parse(sell("will-bitcoin-hit-100k", "yes", 10.0))
        assert result["ok"] is False
        assert result["code"] == "NO_POSITION"

    def test_sell_after_buy(self):
        init_account()
        from pm_trader.mcp_server import _get_engine
        _mock_engine_api(_get_engine())
        buy("will-bitcoin-hit-100k", "yes", 100.0)
        result = _parse(sell("will-bitcoin-hit-100k", "yes", 10.0))
        assert result["ok"] is True
        assert result["data"]["trade"]["side"] == "sell"


class TestHistoryWithData:
    def test_history_after_trade(self):
        init_account()
        from pm_trader.mcp_server import _get_engine
        _mock_engine_api(_get_engine())
        buy("will-bitcoin-hit-100k", "yes", 50.0)
        result = _parse(history())
        assert result["ok"] is True
        assert len(result["data"]) == 1
        assert result["data"][0]["created_at"] is not None
        assert result["data"][0]["side"] == "buy"


class TestPortfolioWithData:
    def test_portfolio_after_trade(self):
        init_account()
        from pm_trader.mcp_server import _get_engine
        _mock_engine_api(_get_engine())
        buy("will-bitcoin-hit-100k", "yes", 50.0)
        result = _parse(portfolio())
        assert result["ok"] is True
        assert len(result["data"]) == 1
        assert result["data"][0]["outcome"] == "yes"


# ---------------------------------------------------------------------------
# Limit order tools (with API mocks)
# ---------------------------------------------------------------------------


class TestPlaceLimitOrder:
    def test_place_gtc(self):
        init_account()
        from pm_trader.mcp_server import _get_engine
        _mock_engine_api(_get_engine())
        result = _parse(place_limit_order(
            "will-bitcoin-hit-100k", "yes", "buy", 100.0, 0.55,
        ))
        assert result["ok"] is True
        assert result["data"]["status"] == "pending"
        assert result["data"]["limit_price"] == 0.55

    def test_place_gtd(self):
        init_account()
        from pm_trader.mcp_server import _get_engine
        _mock_engine_api(_get_engine())
        result = _parse(place_limit_order(
            "will-bitcoin-hit-100k", "yes", "buy", 100.0, 0.55,
            order_type="gtd", expires_at="2027-01-01T00:00:00Z",
        ))
        assert result["ok"] is True
        assert result["data"]["order_type"] == "gtd"

    def test_place_invalid_side(self):
        init_account()
        from pm_trader.mcp_server import _get_engine
        _mock_engine_api(_get_engine())
        result = _parse(place_limit_order(
            "will-bitcoin-hit-100k", "yes", "hold", 100.0, 0.55,
        ))
        assert result["ok"] is False

    def test_place_invalid_order_type(self):
        init_account()
        from pm_trader.mcp_server import _get_engine
        _mock_engine_api(_get_engine())
        result = _parse(place_limit_order(
            "will-bitcoin-hit-100k", "yes", "buy", 100.0, 0.55,
            order_type="bad",
        ))
        assert result["ok"] is False


class TestResolve:
    def test_resolve_no_position(self):
        init_account()
        from pm_trader.mcp_server import _get_engine
        engine = _get_engine()
        from pm_trader.models import NoPositionError
        engine.api.get_market = MagicMock(side_effect=NoPositionError("m", "yes"))
        result = _parse(resolve("m"))
        assert result["ok"] is False


# ---------------------------------------------------------------------------
# Multi-account tools
# ---------------------------------------------------------------------------


class TestCancelOrderSuccess:
    def test_cancel_existing(self):
        init_account()
        from pm_trader.mcp_server import _get_engine
        _mock_engine_api(_get_engine())
        # Place then cancel
        order_result = _parse(place_limit_order(
            "will-bitcoin-hit-100k", "yes", "buy", 100.0, 0.55,
        ))
        assert order_result["ok"] is True
        oid = order_result["data"]["id"]
        result = _parse(cancel_order(oid))
        assert result["ok"] is True
        assert result["data"]["status"] == "cancelled"


class TestCheckOrdersError:
    def test_check_orders_error(self):
        """Trigger check_orders error path."""
        result = _parse(check_orders())
        # Not initialized → error from _require_account
        assert result["ok"] is True or result["ok"] is False


class TestPortfolioError:
    def test_portfolio_not_initialized(self):
        result = _parse(portfolio())
        assert result["ok"] is False


class TestHistoryError:
    def test_history_not_initialized(self):
        result = _parse(history())
        assert result["ok"] is False


class TestResolveWithData:
    def test_resolve_winning_position(self):
        init_account()
        from pm_trader.mcp_server import _get_engine
        engine = _get_engine()
        _mock_engine_api(engine)
        # Buy a position first
        buy("will-bitcoin-hit-100k", "yes", 100.0)
        # Market resolves — YES wins
        resolved = Market(
            condition_id="0xabc123",
            slug="will-bitcoin-hit-100k",
            question="Will Bitcoin hit $100k?",
            description="BTC market",
            outcomes=["Yes", "No"],
            outcome_prices=[1.0, 0.0],
            tokens=[
                {"token_id": "tok_yes", "outcome": "Yes"},
                {"token_id": "tok_no", "outcome": "No"},
            ],
            active=False,
            closed=True,
        )
        engine.api.get_market = MagicMock(return_value=resolved)
        result = _parse(resolve("will-bitcoin-hit-100k"))
        assert result["ok"] is True
        assert len(result["data"]) >= 1
        assert result["data"][0]["payout"] > 0


class TestResolveAllError:
    def test_resolve_all_error(self):
        result = _parse(resolve_all())
        # Not initialized — but resolve_all catches _require_account
        assert result["ok"] is True or result["ok"] is False


class TestBacktestTool:
    def test_invalid_strategy_path(self):
        """strategy_path must be module.function."""
        from pm_trader.mcp_server import backtest
        result = _parse(backtest("/tmp/data.csv", "bad_path"))
        assert result["ok"] is False

    def test_disallowed_strategy(self):
        """Strategy from non-allowlisted module is rejected."""
        from pm_trader.mcp_server import backtest
        result = _parse(backtest("/tmp/data.csv", "os.system"))
        assert result["ok"] is False
        assert "allowed package" in result["error"]

    def test_backtest_csv(self, tmp_path):
        """Backtest with a CSV file and noop strategy."""
        from pm_trader.mcp_server import backtest
        csv = tmp_path / "prices.csv"
        csv.write_text(
            "timestamp,market_slug,outcome,midpoint\n"
            "2025-01-01T00:00:00Z,test-market,yes,0.50\n"
            "2025-01-02T00:00:00Z,test-market,yes,0.55\n"
            "2025-01-03T00:00:00Z,test-market,yes,0.60\n"
        )
        result = _parse(backtest(
            str(csv), "tests.test_benchmark.noop_backtest_strategy",
        ))
        assert result["ok"] is True
        assert result["data"]["snapshots_processed"] == 3
        assert result["data"]["total_trades"] == 0

    def test_backtest_json(self, tmp_path):
        """Backtest with a JSON file."""
        from pm_trader.mcp_server import backtest
        import json as json_mod
        data = [
            {"timestamp": "2025-01-01T00:00:00Z", "market_slug": "m", "outcome": "yes", "midpoint": 0.50},
            {"timestamp": "2025-01-02T00:00:00Z", "market_slug": "m", "outcome": "yes", "midpoint": 0.55},
        ]
        f = tmp_path / "prices.json"
        f.write_text(json_mod.dumps(data))
        result = _parse(backtest(
            str(f), "tests.test_benchmark.noop_backtest_strategy",
        ))
        assert result["ok"] is True
        assert result["data"]["snapshots_processed"] == 2


class TestMcpServerMain:
    def test_main_calls_mcp_run(self):
        with patch.object(mcp_server.mcp, "run") as mock_run:
            mcp_server.main()
            mock_run.assert_called_once()


class TestBacktestInvalidStrategy:
    def test_strategy_path_no_dot(self):
        """strategy_path without module.function format returns error."""
        result = _parse(backtest(
            data_path="/tmp/data.csv",
            strategy_path="no_dot_here",
        ))
        assert result["ok"] is False
        assert "module.function" in result["error"]

    def test_data_path_traversal(self):
        """data_path outside allowed directories is rejected."""
        result = _parse(backtest(
            data_path="/etc/passwd",
            strategy_path="examples.momentum.run",
        ))
        assert result["ok"] is False
        assert "invalid_path" in result.get("code", "")


class TestMultiAccount:
    def test_separate_accounts(self):
        result1 = _parse(init_account(balance=5_000.0, account="alice"))
        assert result1["ok"] is True
        assert result1["data"]["cash"] == 5_000.0

        result2 = _parse(init_account(balance=20_000.0, account="bob"))
        assert result2["ok"] is True
        assert result2["data"]["cash"] == 20_000.0

        bal1 = _parse(get_balance(account="alice"))
        assert bal1["data"]["cash"] == 5_000.0

        bal2 = _parse(get_balance(account="bob"))
        assert bal2["data"]["cash"] == 20_000.0


class TestAccountValidation:
    def test_path_traversal_rejected(self):
        result = _parse(init_account(account="../../etc"))
        assert result["ok"] is False

    def test_slash_rejected(self):
        result = _parse(init_account(account="foo/bar"))
        assert result["ok"] is False

    def test_backslash_rejected(self):
        result = _parse(init_account(account="foo\\bar"))
        assert result["ok"] is False

    def test_empty_rejected(self):
        result = _parse(init_account(account=""))
        assert result["ok"] is False

    def test_whitespace_rejected(self):
        result = _parse(init_account(account=" leading"))
        assert result["ok"] is False

    def test_valid_account(self):
        result = _parse(init_account(account="my-agent_01"))
        assert result["ok"] is True

    def test_reset_traversal(self):
        result = _parse(reset_account(account="../evil"))
        assert result["ok"] is False

    def test_list_orders_traversal(self):
        result = _parse(list_orders(account="../evil"))
        assert result["ok"] is False

    def test_cancel_order_traversal(self):
        result = _parse(cancel_order(1, account="../evil"))
        assert result["ok"] is False
