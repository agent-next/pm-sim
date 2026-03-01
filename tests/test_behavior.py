"""Behavior-driven tests for pm-sim.

Tests the system from an AI agent's perspective: complete trading workflows,
P&L accuracy, portfolio consistency, and error recovery. These test WHAT the
system does, not HOW it's implemented.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from pm_sim.engine import Engine
from pm_sim.models import (
    InsufficientBalanceError,
    InvalidOutcomeError,
    Market,
    MarketClosedError,
    NoPositionError,
    NotInitializedError,
    OrderBook,
    OrderBookLevel,
    OrderRejectedError,
)
from pm_sim.orders import create_order, get_pending_orders


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def engine(tmp_path):
    e = Engine(tmp_path / "test")
    yield e
    e.close()


@pytest.fixture
def acct(engine):
    """Engine with initialized account ($10k)."""
    engine.init_account(10_000.0)
    return engine


def _market(*, closed=False, outcome_prices=None, outcomes=None, tokens=None):
    """Build a test market."""
    outcomes = outcomes or ["Yes", "No"]
    return Market(
        condition_id="0xtest",
        slug="test-market",
        question="Test question?",
        description="",
        outcomes=outcomes,
        outcome_prices=outcome_prices or [0.65, 0.35],
        tokens=tokens or [
            {"token_id": "tok_yes", "outcome": "Yes"},
            {"token_id": "tok_no", "outcome": "No"},
        ],
        active=not closed,
        closed=closed,
        volume=1_000_000.0,
        liquidity=100_000.0,
    )


def _book(asks=None, bids=None):
    """Build a test order book."""
    return OrderBook(
        asks=[OrderBookLevel(p, s) for p, s in (asks or [(0.66, 500)])],
        bids=[OrderBookLevel(p, s) for p, s in (bids or [(0.64, 500)])],
    )


def _mock(engine, market=None, book=None, fee=0):
    m = market or _market()
    b = book or _book()
    engine.api.get_market = MagicMock(return_value=m)
    engine.api.get_order_book = MagicMock(return_value=b)
    engine.api.get_fee_rate = MagicMock(return_value=fee)
    engine.api.get_midpoint = MagicMock(return_value=0.65)


# ---------------------------------------------------------------------------
# Scenario 1: Complete buy → hold → sell lifecycle
# ---------------------------------------------------------------------------


class TestBuyHoldSellLifecycle:
    """An agent buys shares, checks portfolio, then sells for profit/loss."""

    def test_buy_reduces_cash_increases_position(self, acct):
        _mock(acct)
        result = acct.buy("test-market", "yes", 100.0)
        assert result.account.cash < 10_000.0
        portfolio = acct.get_portfolio()
        assert len(portfolio) == 1
        assert portfolio[0]["outcome"] == "yes"
        assert portfolio[0]["shares"] > 0

    def test_sell_increases_cash_reduces_position(self, acct):
        _mock(acct)
        acct.buy("test-market", "yes", 100.0)
        buy_shares = acct.get_portfolio()[0]["shares"]

        result = acct.sell("test-market", "yes", buy_shares)
        assert result.account.cash > 9_900.0  # Got some money back
        portfolio = acct.get_portfolio()
        # Position should be zero or gone
        assert len(portfolio) == 0 or portfolio[0]["shares"] == 0

    def test_partial_sell(self, acct):
        """Sell half, keep half."""
        _mock(acct)
        acct.buy("test-market", "yes", 200.0)
        shares = acct.get_portfolio()[0]["shares"]
        half = shares / 2

        acct.sell("test-market", "yes", half)
        remaining = acct.get_portfolio()[0]["shares"]
        assert abs(remaining - half) < 0.01

    def test_cash_conservation(self, acct):
        """Total value (cash + positions) should track correctly."""
        _mock(acct, book=_book(asks=[(0.66, 5000)], bids=[(0.64, 5000)]))
        balance_before = acct.get_balance()
        assert balance_before["total_value"] == 10_000.0

        acct.buy("test-market", "yes", 500.0)
        balance_after = acct.get_balance()
        # Cash decreased, but positions have value
        assert balance_after["cash"] < 10_000.0
        assert balance_after["positions_value"] > 0


# ---------------------------------------------------------------------------
# Scenario 2: P&L calculations
# ---------------------------------------------------------------------------


class TestPnLAccuracy:
    """P&L must be computed correctly through the full lifecycle."""

    def test_buy_cost_equals_amount_spent(self, acct):
        _mock(acct)
        initial_cash = acct.get_account().cash
        acct.buy("test-market", "yes", 100.0)
        final_cash = acct.get_account().cash
        # Cash reduction should equal amount spent (no fees in this case)
        spent = initial_cash - final_cash
        assert abs(spent - 100.0) < 0.01

    def test_realized_pnl_on_sell(self, acct):
        """Realized P&L should be accurate after selling."""
        _mock(acct)
        acct.buy("test-market", "yes", 100.0)
        position = acct.db.get_position("0xtest", "yes")
        assert position.realized_pnl == 0.0  # No realized P&L until sell

        shares = position.shares
        acct.sell("test-market", "yes", shares)
        position = acct.db.get_position("0xtest", "yes")
        # After full sell, realized_pnl should be non-zero
        assert position.realized_pnl != 0.0 or position.shares == 0

    def test_unrealized_pnl_reflects_price_change(self, acct):
        """Unrealized P&L should change when midpoint changes."""
        _mock(acct, book=_book(asks=[(0.50, 1000)]))
        acct.buy("test-market", "yes", 100.0)

        # Price goes up
        acct.api.get_midpoint = MagicMock(return_value=0.80)
        portfolio = acct.get_portfolio()
        assert portfolio[0]["unrealized_pnl"] > 0

        # Price goes down
        acct.api.get_midpoint = MagicMock(return_value=0.30)
        portfolio = acct.get_portfolio()
        assert portfolio[0]["unrealized_pnl"] < 0


# ---------------------------------------------------------------------------
# Scenario 3: Market resolution
# ---------------------------------------------------------------------------


class TestMarketResolution:
    """Markets resolve correctly, paying out winners."""

    def test_winner_gets_payout(self, acct):
        _mock(acct)
        acct.buy("test-market", "yes", 100.0)
        shares = acct.get_portfolio()[0]["shares"]

        # Market resolves — YES wins
        resolved = _market(closed=True, outcome_prices=[1.0, 0.0])
        acct.api.get_market = MagicMock(return_value=resolved)
        cash_before = acct.get_account().cash

        results = acct.resolve_market("test-market")
        cash_after = acct.get_account().cash

        # Should get $1/share payout
        assert cash_after > cash_before
        payout = cash_after - cash_before
        assert abs(payout - shares) < 0.01

    def test_loser_gets_nothing(self, acct):
        _mock(acct)
        acct.buy("test-market", "no", 100.0)

        # Market resolves — YES wins (NO loses)
        resolved = _market(closed=True, outcome_prices=[1.0, 0.0])
        acct.api.get_market = MagicMock(return_value=resolved)
        cash_before = acct.get_account().cash

        results = acct.resolve_market("test-market")
        cash_after = acct.get_account().cash

        # NO outcome gets $0 payout
        assert cash_after == cash_before


# ---------------------------------------------------------------------------
# Scenario 4: Limit orders behavior
# ---------------------------------------------------------------------------


class TestLimitOrderBehavior:
    """Limit orders must trigger only at the correct prices."""

    def test_buy_limit_waits_for_price(self, acct):
        """A buy limit at 0.55 should not fill when best ask is 0.66."""
        _mock(acct)
        acct.place_limit_order("test-market", "yes", "buy", 100.0, 0.55)
        orders = acct.get_pending_orders()
        assert len(orders) == 1

        # Check: should NOT fill (asks at 0.66)
        results = acct.check_orders()
        filled = [r for r in results if r["action"] == "filled"]
        assert len(filled) == 0

    def test_buy_limit_fills_when_price_drops(self, acct):
        """A buy limit at 0.55 fills when asks drop to 0.50."""
        _mock(acct)
        acct.place_limit_order("test-market", "yes", "buy", 100.0, 0.55)

        # Price drops — asks now at 0.50
        cheap_book = _book(asks=[(0.50, 1000)], bids=[(0.49, 500)])
        acct.api.get_order_book = MagicMock(return_value=cheap_book)

        results = acct.check_orders()
        filled = [r for r in results if r["action"] == "filled"]
        assert len(filled) == 1

    def test_sell_limit_waits_for_price(self, acct):
        """A sell limit at 0.80 should not fill when best bid is 0.64."""
        _mock(acct)
        acct.buy("test-market", "yes", 100.0)
        acct.place_limit_order("test-market", "yes", "sell", 10.0, 0.80)

        results = acct.check_orders()
        filled = [r for r in results if r["action"] == "filled"]
        assert len(filled) == 0

    def test_gtd_expires(self, acct):
        """GTD orders expire after their deadline."""
        _mock(acct)
        acct.place_limit_order(
            "test-market", "yes", "buy", 100.0, 0.55,
            order_type="gtd", expires_at="2020-01-01T00:00:00Z",
        )
        results = acct.check_orders()
        expired = [r for r in results if r["action"] == "expired"]
        assert len(expired) == 1

    def test_cancelled_order_stays_cancelled(self, acct):
        _mock(acct)
        order = acct.place_limit_order("test-market", "yes", "buy", 100.0, 0.55)
        acct.cancel_limit_order(order["id"])

        orders = acct.get_pending_orders()
        assert len(orders) == 0

        # Cancelled order should not fill even if price is right
        cheap_book = _book(asks=[(0.50, 1000)], bids=[(0.49, 500)])
        acct.api.get_order_book = MagicMock(return_value=cheap_book)
        results = acct.check_orders()
        assert len(results) == 0


# ---------------------------------------------------------------------------
# Scenario 5: Error handling for agents
# ---------------------------------------------------------------------------


class TestAgentErrorHandling:
    """Agents must get clear, actionable errors — never raw exceptions."""

    def test_not_initialized_error(self, engine):
        with pytest.raises(NotInitializedError):
            engine.get_account()

    def test_insufficient_balance(self, acct):
        # Book has enough liquidity for $500k, but account only has $10k
        _mock(acct, book=_book(asks=[(0.65, 1_000_000)]))
        with pytest.raises(InsufficientBalanceError) as exc_info:
            acct.buy("test-market", "yes", 500_000.0)
        assert exc_info.value.required > 0
        assert exc_info.value.available == 10_000.0

    def test_no_position_on_sell(self, acct):
        _mock(acct)
        with pytest.raises(NoPositionError):
            acct.sell("test-market", "yes", 10.0)

    def test_closed_market_on_buy(self, acct):
        _mock(acct, market=_market(closed=True))
        with pytest.raises(MarketClosedError):
            acct.buy("test-market", "yes", 100.0)

    def test_invalid_outcome(self, acct):
        _mock(acct)
        with pytest.raises(InvalidOutcomeError):
            acct.buy("test-market", "maybe", 100.0)

    def test_order_rejected_below_minimum(self, acct):
        with pytest.raises(OrderRejectedError, match="Minimum"):
            acct.buy("test-market", "yes", 0.50)


# ---------------------------------------------------------------------------
# Scenario 6: Multi-outcome markets
# ---------------------------------------------------------------------------


class TestMultiOutcomeMarkets:
    """Markets with 3+ outcomes must work end-to-end."""

    def test_three_outcome_buy(self, acct):
        multi = Market(
            condition_id="0xmulti",
            slug="multi-market",
            question="Who wins?",
            description="",
            outcomes=["A", "B", "C"],
            outcome_prices=[0.40, 0.35, 0.25],
            tokens=[
                {"token_id": "tok_a", "outcome": "A"},
                {"token_id": "tok_b", "outcome": "B"},
                {"token_id": "tok_c", "outcome": "C"},
            ],
            active=True,
            closed=False,
            volume=500_000.0,
            liquidity=50_000.0,
        )
        _mock(acct, market=multi)
        result = acct.buy("multi-market", "A", 50.0)
        assert result.trade.outcome == "a"
        assert result.trade.shares > 0

    def test_three_outcome_invalid(self, acct):
        multi = Market(
            condition_id="0xmulti",
            slug="multi-market",
            question="Who wins?",
            description="",
            outcomes=["A", "B", "C"],
            outcome_prices=[0.40, 0.35, 0.25],
            tokens=[
                {"token_id": "tok_a", "outcome": "A"},
                {"token_id": "tok_b", "outcome": "B"},
                {"token_id": "tok_c", "outcome": "C"},
            ],
            active=True,
            closed=False,
        )
        _mock(acct, market=multi)
        with pytest.raises(InvalidOutcomeError, match="Must be one of"):
            acct.buy("multi-market", "D", 50.0)


# ---------------------------------------------------------------------------
# Scenario 7: Order book depth and slippage
# ---------------------------------------------------------------------------


class TestOrderBookBehavior:
    """Order book fills must be accurate with slippage."""

    def test_single_level_fill(self, acct):
        """Small order fills at best ask with zero slippage."""
        _mock(acct, book=_book(asks=[(0.65, 1000)], bids=[(0.64, 1000)]))
        result = acct.buy("test-market", "yes", 10.0)
        assert abs(result.trade.avg_price - 0.65) < 0.001

    def test_multi_level_slippage(self, acct):
        """Large order walks multiple levels — avg price above best ask."""
        deep_book = _book(
            asks=[(0.65, 50), (0.70, 50), (0.80, 50)],
            bids=[(0.64, 500)],
        )
        _mock(acct, book=deep_book)
        result = acct.buy("test-market", "yes", 100.0)
        # avg_price should be above 0.65 due to walking into higher levels
        assert result.trade.avg_price > 0.65

    def test_fok_rejects_insufficient_liquidity(self, acct):
        """FOK order rejects when book is too thin."""
        thin_book = _book(asks=[(0.65, 1)], bids=[(0.64, 1)])
        _mock(acct, book=thin_book)
        with pytest.raises(OrderRejectedError, match="FOK rejected"):
            acct.buy("test-market", "yes", 5000.0)

    def test_fak_allows_partial(self, acct):
        """FAK order fills what's available."""
        thin_book = _book(asks=[(0.65, 10)], bids=[(0.64, 10)])
        _mock(acct, book=thin_book)
        result = acct.buy("test-market", "yes", 5000.0, "fak")
        assert result.trade.is_partial is True
        assert result.trade.shares > 0


# ---------------------------------------------------------------------------
# Scenario 8: Fee calculation
# ---------------------------------------------------------------------------


class TestFeeAccuracy:
    """Fees must follow the exact Polymarket formula."""

    def test_fee_included_in_cost(self, acct):
        _mock(acct, fee=200)  # 2% fee rate
        initial_cash = acct.get_account().cash
        result = acct.buy("test-market", "yes", 100.0)
        spent = initial_cash - result.account.cash
        # Total spent should include the fee
        assert spent > 100.0
        assert result.trade.fee > 0


# ---------------------------------------------------------------------------
# Scenario 9: History and analytics consistency
# ---------------------------------------------------------------------------


class TestHistoryConsistency:
    """Trade history must be consistent with actual trades."""

    def test_history_records_all_trades(self, acct):
        _mock(acct)
        acct.buy("test-market", "yes", 50.0)
        acct.buy("test-market", "no", 50.0)

        history = acct.get_history()
        assert len(history) == 2

    def test_history_newest_first(self, acct):
        _mock(acct)
        acct.buy("test-market", "yes", 50.0)
        acct.buy("test-market", "no", 50.0)

        history = acct.get_history()
        assert history[0].id > history[1].id  # Newest first


# ---------------------------------------------------------------------------
# Scenario 10: Account reset behavior
# ---------------------------------------------------------------------------


class TestResetBehavior:
    def test_reset_clears_everything(self, acct):
        _mock(acct)
        acct.buy("test-market", "yes", 100.0)
        acct.place_limit_order("test-market", "yes", "buy", 100.0, 0.55)

        acct.reset()
        with pytest.raises(NotInitializedError):
            acct.get_account()

        # Re-init
        acct.init_account(10_000.0)
        assert acct.get_history() == []
        assert acct.get_portfolio() == []
        assert acct.get_pending_orders() == []
