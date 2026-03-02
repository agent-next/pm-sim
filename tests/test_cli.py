"""Tests for the pm-trader CLI."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import click.testing
import pytest

from pm_trader.cli import main
from pm_trader.models import (
    Market,
    OrderBook,
    OrderBookLevel,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def runner():
    return click.testing.CliRunner()


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    d = tmp_path / "pm-trader-cli-test"
    d.mkdir()
    return d


def _invoke(runner, args: list[str], data_dir: Path):
    """Invoke the CLI with --data-dir and return parsed JSON."""
    result = runner.invoke(main, ["--data-dir", str(data_dir)] + args)
    return result


def _parse(result) -> dict:
    """Parse the JSON output from a CLI invocation."""
    return json.loads(result.output)


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
    bids=[
        OrderBookLevel(price=0.64, size=500),
        OrderBookLevel(price=0.63, size=500),
    ],
    asks=[
        OrderBookLevel(price=0.66, size=500),
        OrderBookLevel(price=0.67, size=500),
    ],
)


# ---------------------------------------------------------------------------
# Init / Balance / Reset
# ---------------------------------------------------------------------------

class TestAccountCommands:
    def test_init(self, runner, data_dir):
        result = _invoke(runner, ["init"], data_dir)
        assert result.exit_code == 0
        data = _parse(result)
        assert data["ok"] is True
        assert data["data"]["cash"] == 10_000.0

    def test_init_custom_balance(self, runner, data_dir):
        result = _invoke(runner, ["init", "--balance", "5000"], data_dir)
        data = _parse(result)
        assert data["ok"] is True
        assert data["data"]["cash"] == 5000.0

    def test_balance_not_initialized(self, runner, data_dir):
        result = _invoke(runner, ["balance"], data_dir)
        assert result.exit_code == 1
        data = _parse(result)
        assert data["ok"] is False
        assert data["code"] == "NOT_INITIALIZED"

    def test_balance_after_init(self, runner, data_dir):
        _invoke(runner, ["init"], data_dir)
        result = _invoke(runner, ["balance"], data_dir)
        assert result.exit_code == 0
        data = _parse(result)
        assert data["ok"] is True
        assert data["data"]["cash"] == 10_000.0
        assert data["data"]["total_value"] == 10_000.0

    def test_reset_without_confirm(self, runner, data_dir):
        result = _invoke(runner, ["reset"], data_dir)
        assert result.exit_code == 1
        data = _parse(result)
        assert data["code"] == "CONFIRM_REQUIRED"

    def test_reset_with_confirm(self, runner, data_dir):
        _invoke(runner, ["init"], data_dir)
        result = _invoke(runner, ["reset", "--confirm"], data_dir)
        assert result.exit_code == 0
        data = _parse(result)
        assert data["ok"] is True
        assert data["data"]["reset"] is True


# ---------------------------------------------------------------------------
# Buy / Sell (with mocked API)
# ---------------------------------------------------------------------------

class TestTradingCommands:
    def _init_and_mock(self, runner, data_dir):
        """Initialize account and set up API mocks."""
        _invoke(runner, ["init"], data_dir)

    @patch("pm_trader.engine.PolymarketClient")
    def test_buy(self, MockClient, runner, data_dir):
        self._init_and_mock(runner, data_dir)

        # Mock the API client that gets created in Engine.__init__
        mock_instance = MockClient.return_value
        mock_instance.get_market.return_value = SAMPLE_MARKET
        mock_instance.get_order_book.return_value = SAMPLE_BOOK
        mock_instance.get_fee_rate.return_value = 0
        mock_instance.get_midpoint.return_value = 0.65

        result = _invoke(runner, ["buy", "will-bitcoin-hit-100k", "yes", "100"], data_dir)
        data = _parse(result)
        assert data["ok"] is True
        assert data["data"]["trade"]["side"] == "buy"
        assert data["data"]["trade"]["outcome"] == "yes"
        assert data["data"]["account"]["cash"] < 10_000.0

    @patch("pm_trader.engine.PolymarketClient")
    def test_sell_no_position(self, MockClient, runner, data_dir):
        self._init_and_mock(runner, data_dir)

        mock_instance = MockClient.return_value
        mock_instance.get_market.return_value = SAMPLE_MARKET

        result = _invoke(runner, ["sell", "will-bitcoin-hit-100k", "yes", "10"], data_dir)
        data = _parse(result)
        assert data["ok"] is False
        assert data["code"] == "NO_POSITION"

    @patch("pm_trader.engine.PolymarketClient")
    def test_buy_invalid_outcome(self, MockClient, runner, data_dir):
        _invoke(runner, ["init"], data_dir)
        mock_instance = MockClient.return_value
        mock_instance.get_market.return_value = SAMPLE_MARKET
        result = _invoke(runner, ["buy", "btc", "maybe", "100"], data_dir)
        data = _parse(result)
        assert data["ok"] is False
        assert data["code"] == "INVALID_OUTCOME"

    def test_buy_minimum_order(self, runner, data_dir):
        _invoke(runner, ["init"], data_dir)
        result = _invoke(runner, ["buy", "btc", "yes", "0.5"], data_dir)
        data = _parse(result)
        assert data["ok"] is False
        assert data["code"] == "ORDER_REJECTED"


# ---------------------------------------------------------------------------
# Portfolio / History
# ---------------------------------------------------------------------------

class TestPortfolioCommands:
    def test_portfolio_not_initialized(self, runner, data_dir):
        result = _invoke(runner, ["portfolio"], data_dir)
        data = _parse(result)
        assert data["ok"] is False
        assert data["code"] == "NOT_INITIALIZED"

    @patch("pm_trader.engine.PolymarketClient")
    def test_portfolio_empty(self, MockClient, runner, data_dir):
        _invoke(runner, ["init"], data_dir)
        mock_instance = MockClient.return_value
        mock_instance.get_midpoint.return_value = 0.65

        result = _invoke(runner, ["portfolio"], data_dir)
        data = _parse(result)
        assert data["ok"] is True
        assert data["data"] == []

    def test_history_not_initialized(self, runner, data_dir):
        result = _invoke(runner, ["history"], data_dir)
        data = _parse(result)
        assert data["ok"] is False

    def test_history_empty(self, runner, data_dir):
        _invoke(runner, ["init"], data_dir)
        result = _invoke(runner, ["history"], data_dir)
        data = _parse(result)
        assert data["ok"] is True
        assert data["data"] == []


# ---------------------------------------------------------------------------
# Resolve
# ---------------------------------------------------------------------------

class TestResolveCommand:
    def test_resolve_missing_argument(self, runner, data_dir):
        _invoke(runner, ["init"], data_dir)
        result = _invoke(runner, ["resolve"], data_dir)
        data = _parse(result)
        assert data["ok"] is False

    @patch("pm_trader.engine.PolymarketClient")
    def test_resolve_all_empty(self, MockClient, runner, data_dir):
        _invoke(runner, ["init"], data_dir)
        result = _invoke(runner, ["resolve", "--all"], data_dir)
        data = _parse(result)
        assert data["ok"] is True
        assert data["data"] == []


# ---------------------------------------------------------------------------
# JSON envelope format
# ---------------------------------------------------------------------------

class TestJsonEnvelope:
    def test_success_has_ok_true(self, runner, data_dir):
        result = _invoke(runner, ["init"], data_dir)
        data = _parse(result)
        assert "ok" in data
        assert data["ok"] is True
        assert "data" in data

    def test_error_has_ok_false_and_code(self, runner, data_dir):
        result = _invoke(runner, ["balance"], data_dir)
        data = _parse(result)
        assert data["ok"] is False
        assert "error" in data
        assert "code" in data


# ---------------------------------------------------------------------------
# Market commands (with mocked API)
# ---------------------------------------------------------------------------

class TestMarketCommands:
    @patch("pm_trader.engine.PolymarketClient")
    def test_markets_list(self, MockClient, runner, data_dir):
        _invoke(runner, ["init"], data_dir)
        mock_instance = MockClient.return_value
        mock_instance.list_markets.return_value = [SAMPLE_MARKET]
        result = _invoke(runner, ["markets", "list"], data_dir)
        data = _parse(result)
        assert data["ok"] is True
        assert len(data["data"]) == 1

    @patch("pm_trader.engine.PolymarketClient")
    def test_markets_list_by_liquidity(self, MockClient, runner, data_dir):
        _invoke(runner, ["init"], data_dir)
        mock_instance = MockClient.return_value
        mock_instance.list_markets.return_value = [SAMPLE_MARKET]
        result = _invoke(runner, ["markets", "list", "--sort", "liquidity"], data_dir)
        data = _parse(result)
        assert data["ok"] is True

    @patch("pm_trader.engine.PolymarketClient")
    def test_markets_search(self, MockClient, runner, data_dir):
        _invoke(runner, ["init"], data_dir)
        mock_instance = MockClient.return_value
        mock_instance.search_markets.return_value = [SAMPLE_MARKET]
        result = _invoke(runner, ["markets", "search", "bitcoin"], data_dir)
        data = _parse(result)
        assert data["ok"] is True
        assert len(data["data"]) == 1

    @patch("pm_trader.engine.PolymarketClient")
    def test_markets_get(self, MockClient, runner, data_dir):
        _invoke(runner, ["init"], data_dir)
        mock_instance = MockClient.return_value
        mock_instance.get_market.return_value = SAMPLE_MARKET
        result = _invoke(runner, ["markets", "get", "will-bitcoin-hit-100k"], data_dir)
        data = _parse(result)
        assert data["ok"] is True
        assert data["data"]["slug"] == "will-bitcoin-hit-100k"

    @patch("pm_trader.engine.PolymarketClient")
    def test_markets_list_by_tag(self, MockClient, runner, data_dir):
        _invoke(runner, ["init"], data_dir)
        mock_instance = MockClient.return_value
        mock_instance.get_markets_by_tag.return_value = [SAMPLE_MARKET]
        result = _invoke(runner, ["markets", "list", "--tag", "politics"], data_dir)
        data = _parse(result)
        assert data["ok"] is True
        assert len(data["data"]) == 1

    @patch("pm_trader.engine.PolymarketClient")
    def test_markets_tags(self, MockClient, runner, data_dir):
        _invoke(runner, ["init"], data_dir)
        mock_instance = MockClient.return_value
        mock_instance.get_tags.return_value = [
            {"id": "1", "label": "Politics", "slug": "politics"},
        ]
        result = _invoke(runner, ["markets", "tags"], data_dir)
        data = _parse(result)
        assert data["ok"] is True
        assert len(data["data"]) == 1
        assert data["data"][0]["slug"] == "politics"

    @patch("pm_trader.engine.PolymarketClient")
    def test_markets_tags_error(self, MockClient, runner, data_dir):
        from pm_trader.models import ApiError
        _invoke(runner, ["init"], data_dir)
        mock_instance = MockClient.return_value
        mock_instance.get_tags.side_effect = ApiError("fail")
        result = _invoke(runner, ["markets", "tags"], data_dir)
        data = _parse(result)
        assert data["ok"] is False

    @patch("pm_trader.engine.PolymarketClient")
    def test_markets_event(self, MockClient, runner, data_dir):
        _invoke(runner, ["init"], data_dir)
        mock_instance = MockClient.return_value
        mock_instance.get_event.return_value = {
            "title": "US Elections 2028",
            "slug": "us-elections-2028",
        }
        result = _invoke(runner, ["markets", "event", "us-elections-2028"], data_dir)
        data = _parse(result)
        assert data["ok"] is True
        assert data["data"]["title"] == "US Elections 2028"

    @patch("pm_trader.engine.PolymarketClient")
    def test_markets_event_error(self, MockClient, runner, data_dir):
        from pm_trader.models import ApiError
        _invoke(runner, ["init"], data_dir)
        mock_instance = MockClient.return_value
        mock_instance.get_event.side_effect = ApiError("not found")
        result = _invoke(runner, ["markets", "event", "bad"], data_dir)
        data = _parse(result)
        assert data["ok"] is False


# ---------------------------------------------------------------------------
# Price & book commands
# ---------------------------------------------------------------------------

class TestPriceCommands:
    @patch("pm_trader.engine.PolymarketClient")
    def test_price(self, MockClient, runner, data_dir):
        _invoke(runner, ["init"], data_dir)
        mock_instance = MockClient.return_value
        mock_instance.get_market.return_value = SAMPLE_MARKET
        mock_instance.get_midpoint.return_value = 0.65
        result = _invoke(runner, ["price", "will-bitcoin-hit-100k"], data_dir)
        data = _parse(result)
        assert data["ok"] is True
        assert data["data"]["yes_price"] == 0.65

    @patch("pm_trader.engine.PolymarketClient")
    def test_book(self, MockClient, runner, data_dir):
        _invoke(runner, ["init"], data_dir)
        mock_instance = MockClient.return_value
        mock_instance.get_market.return_value = SAMPLE_MARKET
        mock_instance.get_order_book.return_value = SAMPLE_BOOK
        result = _invoke(runner, ["book", "will-bitcoin-hit-100k"], data_dir)
        data = _parse(result)
        assert data["ok"] is True
        assert len(data["data"]["bids"]) == 2
        assert len(data["data"]["asks"]) == 2


# ---------------------------------------------------------------------------
# Stats command
# ---------------------------------------------------------------------------

class TestStatsCommand:
    @patch("pm_trader.engine.PolymarketClient")
    def test_stats_empty(self, MockClient, runner, data_dir):
        _invoke(runner, ["init"], data_dir)
        result = _invoke(runner, ["stats"], data_dir)
        data = _parse(result)
        assert data["ok"] is True
        assert data["data"]["total_trades"] == 0

    def test_stats_not_initialized(self, runner, data_dir):
        result = _invoke(runner, ["stats"], data_dir)
        data = _parse(result)
        assert data["ok"] is False

    @patch("pm_trader.engine.PolymarketClient")
    def test_stats_card(self, MockClient, runner, data_dir):
        _invoke(runner, ["init"], data_dir)
        result = _invoke(runner, ["stats", "--card"], data_dir)
        assert result.exit_code == 0
        assert "*Polymarket Paper Trading*" in result.output

    @patch("pm_trader.engine.PolymarketClient")
    def test_stats_plain(self, MockClient, runner, data_dir):
        _invoke(runner, ["init"], data_dir)
        result = _invoke(runner, ["stats", "--plain"], data_dir)
        assert result.exit_code == 0
        assert "Polymarket Paper Trading" in result.output
        assert "*" not in result.output

    @patch("pm_trader.engine.PolymarketClient")
    def test_stats_tweet(self, MockClient, runner, data_dir):
        _invoke(runner, ["init"], data_dir)
        result = _invoke(runner, ["stats", "--tweet"], data_dir)
        assert result.exit_code == 0
        assert "#Polymarket" in result.output
        assert "clawhub install" in result.output


class TestLeaderboardCommand:
    @patch("pm_trader.engine.PolymarketClient")
    def test_leaderboard_empty(self, MockClient, runner, data_dir):
        _invoke(runner, ["init"], data_dir)
        result = _invoke(runner, ["leaderboard"], data_dir)
        data = _parse(result)
        assert data["ok"] is True
        assert data["data"]["total_trades"] == 0
        assert data["data"]["qualified"] is False

    def test_leaderboard_not_initialized(self, runner, data_dir):
        result = _invoke(runner, ["leaderboard"], data_dir)
        data = _parse(result)
        assert data["ok"] is False


class TestPkCommand:
    @patch("pm_trader.engine.PolymarketClient")
    def test_pk_two_accounts(self, MockClient, runner, tmp_path):
        # --data-dir sets the base, --account sets subdir under it
        base = tmp_path / "pk-base"
        base.mkdir()
        runner.invoke(main, ["--data-dir", str(base), "--account", "alice", "init"])
        runner.invoke(main, ["--data-dir", str(base), "--account", "bob", "init"])
        result = runner.invoke(
            main, ["--data-dir", str(base), "pk", "alice", "bob"],
        )
        assert result.exit_code == 0
        assert "PK" in result.output
        assert "alice" in result.output
        assert "bob" in result.output

    @patch("pm_trader.engine.PolymarketClient")
    def test_pk_not_initialized(self, MockClient, runner, tmp_path):
        base = tmp_path / "pk-empty"
        base.mkdir()
        result = runner.invoke(main, ["--data-dir", str(base), "pk", "nope", "nada"])
        assert result.exit_code == 1


# ---------------------------------------------------------------------------
# Export commands
# ---------------------------------------------------------------------------

class TestExportCommands:
    @patch("pm_trader.engine.PolymarketClient")
    def test_export_trades_csv(self, MockClient, runner, data_dir):
        _invoke(runner, ["init"], data_dir)
        result = _invoke(runner, ["export", "trades"], data_dir)
        # CSV output (empty — just headers)
        assert result.exit_code == 0

    @patch("pm_trader.engine.PolymarketClient")
    def test_export_trades_json(self, MockClient, runner, data_dir):
        _invoke(runner, ["init"], data_dir)
        result = _invoke(runner, ["export", "trades", "--format", "json"], data_dir)
        assert result.exit_code == 0

    @patch("pm_trader.engine.PolymarketClient")
    def test_export_trades_to_file(self, MockClient, runner, data_dir):
        _invoke(runner, ["init"], data_dir)
        out = data_dir / "trades.csv"
        result = _invoke(runner, ["export", "trades", "--output", str(out)], data_dir)
        data = _parse(result)
        assert data["ok"] is True
        assert out.exists()

    @patch("pm_trader.engine.PolymarketClient")
    def test_export_positions_csv(self, MockClient, runner, data_dir):
        _invoke(runner, ["init"], data_dir)
        mock_instance = MockClient.return_value
        mock_instance.get_midpoint.return_value = 0.65
        result = _invoke(runner, ["export", "positions"], data_dir)
        assert result.exit_code == 0

    @patch("pm_trader.engine.PolymarketClient")
    def test_export_positions_json(self, MockClient, runner, data_dir):
        _invoke(runner, ["init"], data_dir)
        mock_instance = MockClient.return_value
        mock_instance.get_midpoint.return_value = 0.65
        result = _invoke(runner, ["export", "positions", "--format", "json"], data_dir)
        assert result.exit_code == 0

    @patch("pm_trader.engine.PolymarketClient")
    def test_export_positions_to_file(self, MockClient, runner, data_dir):
        _invoke(runner, ["init"], data_dir)
        mock_instance = MockClient.return_value
        mock_instance.get_midpoint.return_value = 0.65
        out = data_dir / "positions.json"
        result = _invoke(
            runner,
            ["export", "positions", "--format", "json", "--output", str(out)],
            data_dir,
        )
        data = _parse(result)
        assert data["ok"] is True
        assert out.exists()


# ---------------------------------------------------------------------------
# Accounts commands
# ---------------------------------------------------------------------------

class TestAccountsCommands:
    def test_accounts_list_empty(self, runner, data_dir):
        result = _invoke(runner, ["accounts", "list"], data_dir)
        data = _parse(result)
        assert data["ok"] is True
        assert data["data"] == []

    def test_accounts_create(self, runner, data_dir):
        result = _invoke(runner, ["accounts", "create", "alice"], data_dir)
        data = _parse(result)
        assert data["ok"] is True
        assert data["data"]["name"] == "alice"

    def test_accounts_create_duplicate(self, runner, data_dir):
        _invoke(runner, ["accounts", "create", "alice"], data_dir)
        result = _invoke(runner, ["accounts", "create", "alice"], data_dir)
        data = _parse(result)
        assert data["ok"] is False
        assert data["code"] == "ACCOUNT_EXISTS"

    def test_accounts_list_after_create(self, runner, data_dir):
        _invoke(runner, ["accounts", "create", "alice", "--balance", "5000"], data_dir)
        result = _invoke(runner, ["accounts", "list"], data_dir)
        data = _parse(result)
        assert data["ok"] is True
        assert len(data["data"]) == 1
        assert data["data"][0]["name"] == "alice"

    def test_accounts_delete(self, runner, data_dir):
        _invoke(runner, ["accounts", "create", "alice"], data_dir)
        result = _invoke(runner, ["accounts", "delete", "alice", "--confirm"], data_dir)
        data = _parse(result)
        assert data["ok"] is True
        assert data["data"]["deleted"] == "alice"

    def test_accounts_delete_not_found(self, runner, data_dir):
        result = _invoke(runner, ["accounts", "delete", "ghost", "--confirm"], data_dir)
        data = _parse(result)
        assert data["ok"] is False
        assert data["code"] == "ACCOUNT_NOT_FOUND"


# ---------------------------------------------------------------------------
# Order commands
# ---------------------------------------------------------------------------

class TestOrderCommands:
    @patch("pm_trader.engine.PolymarketClient")
    def test_orders_place_gtc(self, MockClient, runner, data_dir):
        _invoke(runner, ["init"], data_dir)
        mock_instance = MockClient.return_value
        mock_instance.get_market.return_value = SAMPLE_MARKET
        result = _invoke(
            runner,
            ["orders", "place", "will-bitcoin-hit-100k", "yes", "buy", "100", "0.55"],
            data_dir,
        )
        data = _parse(result)
        assert data["ok"] is True
        assert data["data"]["status"] == "pending"

    @patch("pm_trader.engine.PolymarketClient")
    def test_orders_list(self, MockClient, runner, data_dir):
        _invoke(runner, ["init"], data_dir)
        result = _invoke(runner, ["orders", "list"], data_dir)
        data = _parse(result)
        assert data["ok"] is True

    @patch("pm_trader.engine.PolymarketClient")
    def test_orders_cancel_not_found(self, MockClient, runner, data_dir):
        _invoke(runner, ["init"], data_dir)
        result = _invoke(runner, ["orders", "cancel", "999"], data_dir)
        data = _parse(result)
        assert data["ok"] is False

    @patch("pm_trader.engine.PolymarketClient")
    def test_orders_check(self, MockClient, runner, data_dir):
        _invoke(runner, ["init"], data_dir)
        result = _invoke(runner, ["orders", "check"], data_dir)
        data = _parse(result)
        assert data["ok"] is True

    @patch("pm_trader.engine.PolymarketClient")
    def test_orders_cancel_all_empty(self, MockClient, runner, data_dir):
        _invoke(runner, ["init"], data_dir)
        result = _invoke(runner, ["orders", "cancel-all"], data_dir)
        data = _parse(result)
        assert data["ok"] is True
        assert data["data"]["cancelled"] == 0

    @patch("pm_trader.engine.PolymarketClient")
    def test_orders_cancel_all_with_orders(self, MockClient, runner, data_dir):
        _invoke(runner, ["init"], data_dir)
        mock_instance = MockClient.return_value
        mock_instance.get_market.return_value = SAMPLE_MARKET
        _invoke(
            runner,
            ["orders", "place", "will-bitcoin-hit-100k", "yes", "buy", "100", "0.55"],
            data_dir,
        )
        _invoke(
            runner,
            ["orders", "place", "will-bitcoin-hit-100k", "yes", "buy", "200", "0.50"],
            data_dir,
        )
        result = _invoke(runner, ["orders", "cancel-all"], data_dir)
        data = _parse(result)
        assert data["ok"] is True
        assert data["data"]["cancelled"] == 2


# ---------------------------------------------------------------------------
# Watch command
# ---------------------------------------------------------------------------

class TestWatchCommand:
    @patch("pm_trader.engine.PolymarketClient")
    def test_watch(self, MockClient, runner, data_dir):
        _invoke(runner, ["init"], data_dir)
        mock_instance = MockClient.return_value
        mock_instance.get_market.return_value = SAMPLE_MARKET
        mock_instance.get_midpoint.return_value = 0.65
        result = _invoke(runner, ["watch", "will-bitcoin-hit-100k"], data_dir)
        data = _parse(result)
        assert data["ok"] is True
        assert len(data["data"]) >= 1

    def test_watch_not_initialized(self, runner, data_dir):
        result = _invoke(runner, ["watch", "btc"], data_dir)
        data = _parse(result)
        assert data["ok"] is False

    @patch("pm_trader.engine.PolymarketClient")
    def test_watch_invalid_outcome(self, MockClient, runner, data_dir):
        _invoke(runner, ["init"], data_dir)
        mock_instance = MockClient.return_value
        mock_instance.get_market.return_value = SAMPLE_MARKET
        result = _invoke(
            runner,
            ["watch", "will-bitcoin-hit-100k", "--outcome", "invalid"],
            data_dir,
        )
        data = _parse(result)
        assert data["ok"] is False
        assert data["code"] == "INVALID_OUTCOME"


# ---------------------------------------------------------------------------
# Account flag
# ---------------------------------------------------------------------------

class TestAccountFlag:
    def test_account_flag(self, runner, data_dir):
        """--account creates separate data directories."""
        result = _invoke(
            runner,
            ["--account", "test-acct", "init", "--balance", "7777"],
            data_dir,
        )
        data = _parse(result)
        assert data["ok"] is True
        assert data["data"]["cash"] == 7777.0


# ---------------------------------------------------------------------------
# Benchmark commands
# ---------------------------------------------------------------------------

class TestBenchmarkCommands:
    def test_benchmark_run_bad_strategy(self, runner, data_dir):
        result = _invoke(runner, ["benchmark", "run", "nonexistent.strategy"], data_dir)
        data = _parse(result)
        assert data["ok"] is False
        assert data["code"] == "BENCHMARK_ERROR"

    def test_benchmark_compare_missing_account(self, runner, data_dir):
        result = _invoke(runner, ["benchmark", "compare", "ghost"], data_dir)
        data = _parse(result)
        assert data["ok"] is False
        assert data["code"] == "ACCOUNT_NOT_FOUND"

    def test_benchmark_compare_success(self, runner, data_dir):
        # Create two accounts
        _invoke(runner, ["accounts", "create", "a1"], data_dir)
        _invoke(runner, ["accounts", "create", "a2", "--balance", "5000"], data_dir)
        result = _invoke(runner, ["benchmark", "compare", "a1", "a2"], data_dir)
        data = _parse(result)
        assert data["ok"] is True


# ---------------------------------------------------------------------------
# Error path coverage for trading commands
# ---------------------------------------------------------------------------

class TestTradingErrorPaths:
    @patch("pm_trader.engine.PolymarketClient")
    def test_sell_success(self, MockClient, runner, data_dir):
        """Full sell cycle: buy then sell."""
        _invoke(runner, ["init"], data_dir)
        mock_instance = MockClient.return_value
        mock_instance.get_market.return_value = SAMPLE_MARKET
        mock_instance.get_order_book.return_value = SAMPLE_BOOK
        mock_instance.get_fee_rate.return_value = 0
        mock_instance.get_midpoint.return_value = 0.65

        _invoke(runner, ["buy", "will-bitcoin-hit-100k", "yes", "100"], data_dir)
        result = _invoke(runner, ["sell", "will-bitcoin-hit-100k", "yes", "10"], data_dir)
        data = _parse(result)
        assert data["ok"] is True
        assert data["data"]["trade"]["side"] == "sell"


# ---------------------------------------------------------------------------
# Order command error paths
# ---------------------------------------------------------------------------

class TestOrderCommandErrors:
    @patch("pm_trader.engine.PolymarketClient")
    def test_orders_place_gtd(self, MockClient, runner, data_dir):
        _invoke(runner, ["init"], data_dir)
        mock_instance = MockClient.return_value
        mock_instance.get_market.return_value = SAMPLE_MARKET
        result = _invoke(
            runner,
            ["orders", "place", "will-bitcoin-hit-100k", "yes", "buy",
             "100", "0.55", "--type", "gtd", "--expires", "2027-01-01T00:00:00Z"],
            data_dir,
        )
        data = _parse(result)
        assert data["ok"] is True
        assert data["data"]["order_type"] == "gtd"

    @patch("pm_trader.engine.PolymarketClient")
    def test_orders_place_and_cancel(self, MockClient, runner, data_dir):
        _invoke(runner, ["init"], data_dir)
        mock_instance = MockClient.return_value
        mock_instance.get_market.return_value = SAMPLE_MARKET
        _invoke(
            runner,
            ["orders", "place", "will-bitcoin-hit-100k", "yes", "buy", "100", "0.55"],
            data_dir,
        )
        result = _invoke(runner, ["orders", "cancel", "1"], data_dir)
        data = _parse(result)
        assert data["ok"] is True
        assert data["data"]["status"] == "cancelled"

    @patch("pm_trader.engine.PolymarketClient")
    def test_orders_place_invalid_price(self, MockClient, runner, data_dir):
        _invoke(runner, ["init"], data_dir)
        mock_instance = MockClient.return_value
        mock_instance.get_market.return_value = SAMPLE_MARKET
        result = _invoke(
            runner,
            ["orders", "place", "will-bitcoin-hit-100k", "yes", "buy", "100", "1.5"],
            data_dir,
        )
        data = _parse(result)
        assert data["ok"] is False
        assert data["code"] == "ORDER_REJECTED"

    def test_orders_list(self, runner, data_dir):
        _invoke(runner, ["init"], data_dir)
        result = _invoke(runner, ["orders", "list"], data_dir)
        data = _parse(result)
        assert data["ok"] is True
        assert data["data"] == []

    @patch("pm_trader.engine.PolymarketClient")
    def test_orders_check(self, MockClient, runner, data_dir):
        _invoke(runner, ["init"], data_dir)
        result = _invoke(runner, ["orders", "check"], data_dir)
        data = _parse(result)
        assert data["ok"] is True

    def test_orders_cancel_not_found(self, runner, data_dir):
        _invoke(runner, ["init"], data_dir)
        result = _invoke(runner, ["orders", "cancel", "999"], data_dir)
        data = _parse(result)
        assert data["ok"] is False
        assert data["code"] == "ORDER_NOT_FOUND"


class TestResolveCommands:
    @patch("pm_trader.engine.PolymarketClient")
    def test_resolve_all(self, MockClient, runner, data_dir):
        _invoke(runner, ["init"], data_dir)
        result = _invoke(runner, ["resolve", "--all"], data_dir)
        data = _parse(result)
        assert data["ok"] is True

    def test_resolve_missing_argument(self, runner, data_dir):
        _invoke(runner, ["init"], data_dir)
        result = _invoke(runner, ["resolve"], data_dir)
        data = _parse(result)
        assert data["ok"] is False
        assert data["code"] == "MISSING_ARGUMENT"

    @patch("pm_trader.engine.PolymarketClient")
    def test_resolve_specific_market_error(self, MockClient, runner, data_dir):
        from pm_trader.models import NoPositionError
        _invoke(runner, ["init"], data_dir)
        mock_instance = MockClient.return_value
        mock_instance.get_market.side_effect = NoPositionError("m", "yes")
        result = _invoke(runner, ["resolve", "some-market"], data_dir)
        data = _parse(result)
        assert data["ok"] is False


class TestBenchmarkCompare:
    def test_compare_missing_account(self, runner, data_dir):
        result = _invoke(runner, ["benchmark", "compare", "nonexistent"], data_dir)
        data = _parse(result)
        assert data["ok"] is False
        assert data["code"] == "ACCOUNT_NOT_FOUND"

    def test_compare_valid_accounts(self, runner, data_dir):
        # Create two accounts
        _invoke(runner, ["--account", "alice", "init", "--balance", "5000"], data_dir)
        _invoke(runner, ["--account", "bob", "init", "--balance", "10000"], data_dir)
        result = _invoke(runner, ["benchmark", "compare", "alice", "bob"], data_dir)
        data = _parse(result)
        assert data["ok"] is True


class TestBenchmarkPk:
    def test_pk_bad_strategy(self, runner, data_dir):
        result = _invoke(
            runner,
            ["benchmark", "pk", "nonexistent.a", "nonexistent.b"],
            data_dir,
        )
        data = _parse(result)
        assert data["ok"] is False
        assert data["code"] == "PK_ERROR"

    def test_pk_success(self, runner, data_dir):
        result = _invoke(
            runner,
            [
                "benchmark", "pk",
                "tests.test_benchmark.noop_strategy",
                "tests.test_benchmark.noop_strategy",
                "--name-a", "alpha",
                "--name-b", "beta",
            ],
            data_dir,
        )
        assert result.exit_code == 0
        assert "alpha" in result.output
        assert "beta" in result.output
        assert "Winner:" in result.output


class TestCliSimErrorPaths:
    """Test that SimError exceptions in CLI produce proper error JSON."""

    def test_balance_not_initialized(self, runner, data_dir):
        result = _invoke(runner, ["balance"], data_dir)
        data = _parse(result)
        assert data["ok"] is False
        assert result.exit_code == 1

    def test_portfolio_not_initialized(self, runner, data_dir):
        result = _invoke(runner, ["portfolio"], data_dir)
        data = _parse(result)
        assert data["ok"] is False
        assert result.exit_code == 1

    def test_history_not_initialized(self, runner, data_dir):
        result = _invoke(runner, ["history"], data_dir)
        data = _parse(result)
        assert data["ok"] is False
        assert result.exit_code == 1

    @patch("pm_trader.engine.PolymarketClient")
    def test_markets_list_error(self, MockClient, runner, data_dir):
        from pm_trader.models import ApiError
        _invoke(runner, ["init"], data_dir)
        mock_instance = MockClient.return_value
        mock_instance.list_markets.side_effect = ApiError("network error")
        result = _invoke(runner, ["markets", "list"], data_dir)
        data = _parse(result)
        assert data["ok"] is False

    @patch("pm_trader.engine.PolymarketClient")
    def test_markets_search_error(self, MockClient, runner, data_dir):
        from pm_trader.models import ApiError
        _invoke(runner, ["init"], data_dir)
        mock_instance = MockClient.return_value
        mock_instance.search_markets.side_effect = ApiError("timeout")
        result = _invoke(runner, ["markets", "search", "bitcoin"], data_dir)
        data = _parse(result)
        assert data["ok"] is False

    @patch("pm_trader.engine.PolymarketClient")
    def test_markets_get_error(self, MockClient, runner, data_dir):
        from pm_trader.models import ApiError
        _invoke(runner, ["init"], data_dir)
        mock_instance = MockClient.return_value
        mock_instance.get_market.side_effect = ApiError("not found", 404)
        result = _invoke(runner, ["markets", "get", "nonexistent"], data_dir)
        data = _parse(result)
        assert data["ok"] is False

    @patch("pm_trader.engine.PolymarketClient")
    def test_price_error(self, MockClient, runner, data_dir):
        from pm_trader.models import ApiError
        _invoke(runner, ["init"], data_dir)
        mock_instance = MockClient.return_value
        mock_instance.get_market.side_effect = ApiError("fail")
        result = _invoke(runner, ["price", "nonexistent"], data_dir)
        data = _parse(result)
        assert data["ok"] is False
        assert result.exit_code == 1

    @patch("pm_trader.engine.PolymarketClient")
    def test_book_error(self, MockClient, runner, data_dir):
        from pm_trader.models import ApiError
        _invoke(runner, ["init"], data_dir)
        mock_instance = MockClient.return_value
        mock_instance.get_market.side_effect = ApiError("fail")
        result = _invoke(runner, ["book", "nonexistent"], data_dir)
        data = _parse(result)
        assert data["ok"] is False
        assert result.exit_code == 1

    def test_benchmark_run_error(self, runner, data_dir):
        result = _invoke(runner, ["benchmark", "run", "bad_module"], data_dir)
        data = _parse(result)
        assert data["ok"] is False
        assert data["code"] == "BENCHMARK_ERROR"

    def test_export_trades_not_initialized(self, runner, data_dir):
        result = _invoke(runner, ["export", "trades"], data_dir)
        data = _parse(result)
        assert data["ok"] is False
        assert result.exit_code == 1

    def test_export_positions_not_initialized(self, runner, data_dir):
        result = _invoke(runner, ["export", "positions"], data_dir)
        data = _parse(result)
        assert data["ok"] is False
        assert result.exit_code == 1

    @patch("pm_trader.engine.Engine.init_account")
    def test_init_sim_error(self, mock_init, runner, data_dir):
        from pm_trader.models import SimError
        mock_init.side_effect = SimError("boom")
        result = _invoke(runner, ["init"], data_dir)
        data = _parse(result)
        assert data["ok"] is False
        assert result.exit_code == 1

    @patch("pm_trader.engine.Engine.reset")
    def test_reset_sim_error(self, mock_reset, runner, data_dir):
        from pm_trader.models import SimError
        _invoke(runner, ["init"], data_dir)
        mock_reset.side_effect = SimError("boom")
        result = _invoke(runner, ["reset", "--confirm"], data_dir)
        data = _parse(result)
        assert data["ok"] is False
        assert result.exit_code == 1

    @patch("pm_trader.engine.Engine.get_pending_orders")
    def test_orders_list_sim_error(self, mock_orders, runner, data_dir):
        from pm_trader.models import SimError
        _invoke(runner, ["init"], data_dir)
        mock_orders.side_effect = SimError("fail")
        result = _invoke(runner, ["orders", "list"], data_dir)
        data = _parse(result)
        assert data["ok"] is False
        assert result.exit_code == 1

    @patch("pm_trader.engine.Engine.cancel_limit_order")
    def test_orders_cancel_sim_error(self, mock_cancel, runner, data_dir):
        from pm_trader.models import SimError
        _invoke(runner, ["init"], data_dir)
        mock_cancel.side_effect = SimError("fail")
        result = _invoke(runner, ["orders", "cancel", "1"], data_dir)
        data = _parse(result)
        assert data["ok"] is False
        assert result.exit_code == 1

    @patch("pm_trader.engine.Engine.check_orders")
    def test_orders_check_sim_error(self, mock_check, runner, data_dir):
        from pm_trader.models import SimError
        _invoke(runner, ["init"], data_dir)
        mock_check.side_effect = SimError("fail")
        result = _invoke(runner, ["orders", "check"], data_dir)
        data = _parse(result)
        assert data["ok"] is False
        assert result.exit_code == 1

    @patch("pm_trader.engine.Engine.cancel_all_orders")
    def test_orders_cancel_all_sim_error(self, mock_cancel_all, runner, data_dir):
        from pm_trader.models import SimError
        _invoke(runner, ["init"], data_dir)
        mock_cancel_all.side_effect = SimError("fail")
        result = _invoke(runner, ["orders", "cancel-all"], data_dir)
        data = _parse(result)
        assert data["ok"] is False
        assert result.exit_code == 1

    @patch("pm_trader.benchmark.run_strategy")
    def test_benchmark_run_success(self, mock_run, runner, data_dir):
        """benchmark run success path echoes _ok(result)."""
        mock_run.return_value = {"strategy": "mod.fn", "pnl": 100.0}
        result = _invoke(runner, ["benchmark", "run", "mod.fn"], data_dir)
        data = _parse(result)
        assert data["ok"] is True
        assert data["data"]["strategy"] == "mod.fn"

    @patch("pm_trader.benchmark.compare_accounts")
    def test_benchmark_compare_exception(self, mock_compare, runner, data_dir):
        """benchmark compare raises an exception."""
        mock_compare.side_effect = RuntimeError("comparison failed")
        # Create the accounts so the path check passes
        _invoke(runner, ["--account", "alice", "init"], data_dir)
        _invoke(runner, ["--account", "bob", "init"], data_dir)
        result = _invoke(runner, ["benchmark", "compare", "alice", "bob"], data_dir)
        data = _parse(result)
        assert data["ok"] is False
        assert data["code"] == "BENCHMARK_ERROR"

    def test_account_path_traversal(self, runner, data_dir):
        """Account names with path traversal are rejected."""
        result = _invoke(runner, ["--account", "../../etc", "init"], data_dir)
        assert result.exit_code != 0

    @patch("pm_trader.mcp_server.main")
    def test_mcp_command(self, mock_mcp_main, runner, data_dir):
        """mcp command invokes the MCP server."""
        result = runner.invoke(main, ["mcp"])
        mock_mcp_main.assert_called_once()
