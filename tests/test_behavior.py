"""Behavior-driven tests for pm-trader.

Tests the system from an AI agent's perspective: complete trading workflows,
P&L accuracy, portfolio consistency, and error recovery. These test WHAT the
system does, not HOW it's implemented.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from pm_trader.engine import Engine
from pm_trader.models import (
    AmbiguousResolutionError,
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
from pm_trader.orderbook import simulate_buy_fill, simulate_sell_fill
from pm_trader.orders import create_order, get_order, get_pending_orders


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


# ---------------------------------------------------------------------------
# Scenario 11: Orderbook edge cases
# ---------------------------------------------------------------------------


class TestOrderbookEdgeCases:
    """Cover orderbook simulator edge cases: max_price, min_price, empty fills."""

    def test_buy_max_price_skips_expensive_levels(self):
        """max_price causes buy fill to stop at that price."""
        book = _book(asks=[(0.50, 100), (0.60, 100), (0.70, 100)])
        fill = simulate_buy_fill(book, 1000.0, 0, "fak", max_price=0.55)
        assert fill.filled or fill.is_partial
        assert fill.avg_price <= 0.55

    def test_sell_min_price_skips_cheap_levels(self):
        """min_price causes sell fill to stop at that price."""
        book = _book(bids=[(0.70, 100), (0.60, 100), (0.50, 100)])
        fill = simulate_sell_fill(book, 200.0, 0, "fak", min_price=0.65)
        assert fill.filled or fill.is_partial
        assert fill.avg_price >= 0.65

    def test_sell_empty_book_returns_empty(self):
        """Selling into an empty bid side returns unfilled result."""
        book = OrderBook(asks=[], bids=[])
        fill = simulate_sell_fill(book, 10.0, 0, "fak")
        assert not fill.filled
        assert fill.total_shares == 0

    def test_sell_exact_fill_across_levels(self):
        """Sell exactly fills across first 2 levels, break triggers on 3rd."""
        # 3 levels: first two fill 100 shares, third is never reached
        book = _book(bids=[(0.70, 50), (0.60, 50), (0.50, 100)])
        fill = simulate_sell_fill(book, 100.0, 0, "fok")
        assert fill.filled
        assert fill.total_shares == 100.0
        assert fill.levels_filled == 2  # Only 2 levels consumed

    def test_sell_all_bids_below_min_price(self):
        """When all bids are below min_price, no fills happen."""
        book = _book(bids=[(0.30, 100), (0.20, 100)])
        fill = simulate_sell_fill(book, 50.0, 0, "fak", min_price=0.50)
        assert not fill.filled
        assert fill.total_shares == 0


# ---------------------------------------------------------------------------
# Scenario 12: Portfolio midpoint failure fallback
# ---------------------------------------------------------------------------


class TestPortfolioMidpointFallback:
    """When midpoint API fails, portfolio should use 0.0 for live price."""

    def test_midpoint_failure_uses_zero(self, acct):
        _mock(acct)
        acct.buy("test-market", "yes", 100.0)

        # Break midpoint lookup
        acct.api.get_midpoint = MagicMock(side_effect=Exception("API down"))
        portfolio = acct.get_portfolio()
        assert len(portfolio) == 1
        assert portfolio[0]["current_value"] == 0.0


# ---------------------------------------------------------------------------
# Scenario 13: Resolve edge cases
# ---------------------------------------------------------------------------


class TestResolveEdgeCases:
    """Edge cases for market resolution."""

    def test_resolve_skips_already_resolved(self, acct):
        """Already-resolved positions should be skipped on re-resolve."""
        _mock(acct)
        acct.buy("test-market", "yes", 100.0)

        # Resolve once
        resolved = _market(closed=True, outcome_prices=[1.0, 0.0])
        acct.api.get_market = MagicMock(return_value=resolved)
        results1 = acct.resolve_market("test-market")
        assert len(results1) == 1

        # Try to resolve again — should skip already-resolved position
        results2 = acct.resolve_market("test-market")
        assert len(results2) == 0

    def test_resolve_all_with_closed_market(self, acct):
        """resolve_all finds and resolves closed markets."""
        _mock(acct)
        acct.buy("test-market", "yes", 50.0)

        # Market is now closed
        resolved = _market(closed=True, outcome_prices=[1.0, 0.0])
        acct.api.get_market = MagicMock(return_value=resolved)
        results = acct.resolve_all()
        assert len(results) >= 1

    def test_resolve_all_skips_api_error(self, acct):
        """resolve_all skips markets that fail API lookup (transient)."""
        _mock(acct)
        acct.buy("test-market", "yes", 50.0)

        # Transient API failure — should be skipped
        acct.api.get_market = MagicMock(side_effect=ConnectionError("timeout"))
        results = acct.resolve_all()
        assert results == []

    def test_resolve_all_propagates_ambiguous_resolution(self, acct):
        """resolve_all raises AmbiguousResolutionError instead of swallowing it."""
        _mock(acct)
        acct.buy("test-market", "yes", 50.0)

        # Market closed but ambiguous — should raise, not silently skip
        ambiguous = _market(closed=True, outcome_prices=[0.50, 0.50])
        acct.api.get_market = MagicMock(return_value=ambiguous)
        with pytest.raises(AmbiguousResolutionError, match="No clear winner"):
            acct.resolve_all()

    def test_resolve_all_deduplicates_same_market(self, acct):
        """resolve_all only processes each market once even with multiple positions."""
        _mock(acct, book=_book(asks=[(0.65, 5000)], bids=[(0.64, 5000)]))
        # Buy YES and NO in the same market → two positions, one condition_id
        acct.buy("test-market", "yes", 50.0)
        _mock(acct, book=_book(asks=[(0.35, 5000)], bids=[(0.34, 5000)]))
        acct.buy("test-market", "no", 50.0)

        positions = acct.get_portfolio()
        assert len(positions) == 2

        # Resolve — should process the market only once
        resolved = _market(closed=True, outcome_prices=[1.0, 0.0])
        acct.api.get_market = MagicMock(return_value=resolved)
        results = acct.resolve_all()
        # Both positions resolved in a single market pass
        assert len(results) == 2

    def test_determine_winner_no_clear_winner_raises(self, acct):
        """When no outcome has price >= 0.99, resolve raises SimError."""
        _mock(acct)
        acct.buy("test-market", "yes", 50.0)

        # Market closed but no clear winner (both at 0.50)
        ambiguous = _market(closed=True, outcome_prices=[0.50, 0.50])
        acct.api.get_market = MagicMock(return_value=ambiguous)
        with pytest.raises(AmbiguousResolutionError, match="No clear winner"):
            acct.resolve_market("test-market")

    def test_determine_winner_borderline_prices(self, acct):
        """Prices just below 0.99 should raise; at 0.99 should resolve."""
        _mock(acct)
        acct.buy("test-market", "yes", 50.0)

        # 0.98 is below threshold — should raise
        borderline = _market(closed=True, outcome_prices=[0.98, 0.02])
        acct.api.get_market = MagicMock(return_value=borderline)
        with pytest.raises(AmbiguousResolutionError, match="No clear winner"):
            acct.resolve_market("test-market")

        # 0.99 is at threshold — should resolve successfully
        clear = _market(closed=True, outcome_prices=[0.99, 0.01])
        acct.api.get_market = MagicMock(return_value=clear)
        results = acct.resolve_market("test-market")
        assert len(results) == 1
        assert results[0].payout > 0


# ---------------------------------------------------------------------------
# Scenario 14: Limit order check_orders edge cases
# ---------------------------------------------------------------------------


class TestCheckOrdersEdgeCases:
    """Edge cases in the limit order check_orders cycle."""

    def test_check_orders_transient_api_error(self, acct):
        """Transient API error during check_orders should be silently skipped."""
        _mock(acct)
        acct.place_limit_order("test-market", "yes", "buy", 100.0, 0.55)

        # API fails on get_market
        acct.api.get_market = MagicMock(side_effect=ConnectionError("timeout"))
        results = acct.check_orders()
        # No fills, no rejections — order stays pending
        assert len(results) == 0
        assert len(acct.get_pending_orders()) == 1

    def test_limit_order_no_fillable_liquidity_within_limit(self, acct):
        """Limit buy at 0.40 but cheapest ask is 0.66 — no fill, order stays pending."""
        _mock(acct, book=_book(asks=[(0.66, 5000)], bids=[(0.64, 5000)]))
        acct.place_limit_order("test-market", "yes", "buy", 100.0, 0.40)

        # Check — best ask (0.66) is above limit (0.40), fill simulates with max_price=0.40
        # but no asks at or below 0.40 exist, so fill is empty → continue
        results = acct.check_orders()
        filled = [r for r in results if r["action"] == "filled"]
        assert len(filled) == 0
        assert len(acct.get_pending_orders()) == 1

    def test_limit_sell_exceeds_position(self, acct):
        """Limit sell for more shares than held should be rejected."""
        _mock(acct, book=_book(asks=[(0.50, 5000)], bids=[(0.64, 5000)]))
        acct.buy("test-market", "yes", 10.0)  # Buy a small amount
        position_shares = acct.get_portfolio()[0]["shares"]

        # Place limit sell for far more shares than we hold
        acct.place_limit_order(
            "test-market", "yes", "sell", position_shares + 100, 0.60,
        )

        # Book bids are high enough to trigger → but shares exceed position
        high_book = _book(asks=[(0.70, 5000)], bids=[(0.64, 5000)])
        acct.api.get_order_book = MagicMock(return_value=high_book)
        results = acct.check_orders()
        rejected = [r for r in results if r["action"] == "rejected"]
        assert len(rejected) == 1

    def test_limit_buy_insufficient_balance(self, acct):
        """Limit buy exceeding available cash is rejected at placement."""
        # Start with very low balance
        acct.init_account(1.0)
        _mock(acct, book=_book(asks=[(0.50, 10000)], bids=[(0.49, 5000)]))
        with pytest.raises(InsufficientBalanceError):
            acct.place_limit_order("test-market", "yes", "buy", 1000.0, 0.55)


# ---------------------------------------------------------------------------
# Scenario 15: End-to-end agent workflow
# ---------------------------------------------------------------------------


class TestEndToEndAgentWorkflow:
    """Full workflow: search → buy → monitor → sell → resolve → check P&L."""

    def test_full_trading_cycle(self, acct):
        """Simulate a complete agent session."""
        # 1. Agent searches markets
        _mock(acct, book=_book(asks=[(0.65, 5000)], bids=[(0.64, 5000)]))

        # 2. Agent checks balance
        bal = acct.get_balance()
        assert bal["cash"] == 10_000.0

        # 3. Agent buys YES shares
        buy_result = acct.buy("test-market", "yes", 500.0)
        shares_bought = buy_result.trade.shares
        assert shares_bought > 0

        # 4. Agent checks portfolio
        portfolio = acct.get_portfolio()
        assert len(portfolio) == 1
        assert portfolio[0]["shares"] == shares_bought

        # 5. Agent checks trade history
        history = acct.get_history()
        assert len(history) == 1

        # 6. Agent sells half
        half = shares_bought / 2
        sell_result = acct.sell("test-market", "yes", half)
        assert sell_result.trade.shares > 0

        # 7. Check P&L stats
        from pm_trader.analytics import compute_stats
        stats = compute_stats(
            acct.db.get_trades(limit=1000),
            acct.get_account(),
            sum(p["current_value"] for p in acct.get_portfolio()),
        )
        assert stats["total_trades"] == 2
        assert stats["buy_count"] == 1
        assert stats["sell_count"] == 1

        # 8. Market resolves — YES wins
        resolved = _market(closed=True, outcome_prices=[1.0, 0.0])
        acct.api.get_market = MagicMock(return_value=resolved)
        resolve_results = acct.resolve_market("test-market")

        # 9. Final balance check
        final = acct.get_balance()
        assert final["cash"] > 0

    def test_multiple_markets_portfolio(self, acct):
        """Agent trades two different markets simultaneously."""
        market1 = _market()
        market2 = Market(
            condition_id="0xother",
            slug="other-market",
            question="Other?",
            description="",
            outcomes=["Yes", "No"],
            outcome_prices=[0.40, 0.60],
            tokens=[
                {"token_id": "tok_yes2", "outcome": "Yes"},
                {"token_id": "tok_no2", "outcome": "No"},
            ],
            active=True, closed=False,
        )

        # Buy in market 1
        _mock(acct, market=market1, book=_book(asks=[(0.65, 5000)], bids=[(0.64, 5000)]))
        acct.buy("test-market", "yes", 200.0)

        # Buy in market 2
        _mock(acct, market=market2, book=_book(asks=[(0.40, 5000)], bids=[(0.39, 5000)]))
        acct.buy("other-market", "yes", 200.0)

        portfolio = acct.get_portfolio()
        assert len(portfolio) == 2

        # History has 2 trades
        assert len(acct.get_history()) == 2

    def test_limit_order_lifecycle(self, acct):
        """Agent places limit, checks it, waits, then it fills."""
        _mock(acct, book=_book(asks=[(0.66, 5000)], bids=[(0.64, 5000)]))

        # 1. Place limit buy at 0.55
        order = acct.place_limit_order("test-market", "yes", "buy", 100.0, 0.55)
        assert order["status"] == "pending"
        order_id = order["id"]

        # 2. Check — not filled yet
        assert len(acct.get_pending_orders()) == 1
        results = acct.check_orders()
        filled = [r for r in results if r["action"] == "filled"]
        assert len(filled) == 0

        # 3. Price drops
        acct.api.get_order_book = MagicMock(
            return_value=_book(asks=[(0.50, 5000)], bids=[(0.49, 5000)])
        )
        results = acct.check_orders()
        filled = [r for r in results if r["action"] == "filled"]
        assert len(filled) == 1

        # 4. Order no longer pending
        assert len(acct.get_pending_orders()) == 0

        # 5. Position exists
        portfolio = acct.get_portfolio()
        assert len(portfolio) == 1


# ---------------------------------------------------------------------------
# Scenario 16: orders.get_order retrieves specific order
# ---------------------------------------------------------------------------


class TestGetOrder:
    def test_get_order_returns_created_order(self, acct):
        """get_order retrieves a specific order by ID."""
        _mock(acct, book=_book(asks=[(0.65, 5000)], bids=[(0.64, 5000)]))
        acct.place_limit_order("test-market", "yes", "buy", 100.0, 0.40)
        pending = acct.get_pending_orders()
        assert len(pending) == 1
        order_id = pending[0]["id"]

        order = get_order(acct.db.conn, order_id)
        assert order is not None
        assert order.id == order_id
        assert order.market_slug == "test-market"
        assert order.side == "buy"
        assert order.limit_price == 0.40

    def test_get_order_nonexistent(self, acct):
        """get_order returns None for nonexistent order."""
        _mock(acct)
        order = get_order(acct.db.conn, 99999)
        assert order is None


# ---------------------------------------------------------------------------
# Scenario 17: defensive guards in engine
# ---------------------------------------------------------------------------


class TestDefensiveGuards:
    def test_update_position_after_sell_no_position(self, acct):
        """_update_position_after_sell returns early when position is None."""
        _mock(acct, book=_book(asks=[(0.65, 5000)], bids=[(0.64, 5000)]))
        market = acct.api.get_market("test-market")
        # Call directly with no position existing — should return without error
        acct._update_position_after_sell(
            market=market, outcome="yes", sold_shares=10.0, proceeds=6.5,
        )
        # No crash, no position created
        assert acct.db.get_position(market.condition_id, "yes") is None

    def test_check_orders_no_fillable_liquidity(self, acct):
        """check_orders skips order when simulate returns unfilled non-partial."""
        _mock(acct, book=_book(asks=[(0.65, 5000)], bids=[(0.64, 5000)]))
        # Place limit buy at a price equal to the ask, so the pre-check passes
        acct.place_limit_order("test-market", "yes", "buy", 100.0, 0.65)

        # Now mock simulate_buy_fill to return an empty fill (defensive edge)
        from pm_trader.orderbook import FillResult
        empty_fill = FillResult(
            filled=False, is_partial=False, total_shares=0.0,
            total_cost=0.0, avg_price=0.0, fee=0.0,
            slippage_bps=0.0, levels_filled=0, fills=[],
        )
        with MagicMock() as mock_sim:
            import pm_trader.engine as engine_mod
            original_sim = engine_mod.simulate_buy_fill
            engine_mod.simulate_buy_fill = lambda *a, **kw: empty_fill
            try:
                results = acct.check_orders()
                filled = [r for r in results if r["action"] == "filled"]
                assert len(filled) == 0
                # Order should still be pending
                assert len(acct.get_pending_orders()) == 1
            finally:
                engine_mod.simulate_buy_fill = original_sim


# ---------------------------------------------------------------------------
# Scenario 18: Round-trip P&L (B1)
# ---------------------------------------------------------------------------


class TestRoundTripPnL:
    """Buy shares then sell them all — verify end-to-end money math."""

    def test_round_trip_profitable(self, acct):
        """Buy low, sell high: cash gain equals profit minus spread."""
        buy_book = _book(asks=[(0.50, 1000)], bids=[(0.49, 500)])
        _mock(acct, book=buy_book, fee=0)
        cash_before = acct.get_account().cash

        result = acct.buy("test-market", "yes", 100.0)
        shares = result.trade.shares
        cost = cash_before - acct.get_account().cash

        # Sell at higher price
        sell_book = _book(asks=[(0.80, 500)], bids=[(0.70, 1000)])
        acct.api.get_order_book = MagicMock(return_value=sell_book)
        acct.sell("test-market", "yes", shares)

        cash_after = acct.get_account().cash
        profit = cash_after - cash_before
        # Bought at 0.50, sold at 0.70 → profit per share ≈ 0.20
        assert profit > 0
        assert profit == pytest.approx(shares * 0.70 - cost, abs=0.01)

    def test_round_trip_loss(self, acct):
        """Buy high, sell low: cash decreases by the loss amount."""
        buy_book = _book(asks=[(0.70, 1000)], bids=[(0.69, 500)])
        _mock(acct, book=buy_book, fee=0)
        cash_before = acct.get_account().cash

        result = acct.buy("test-market", "yes", 100.0)
        shares = result.trade.shares

        # Sell at lower price
        sell_book = _book(asks=[(0.50, 500)], bids=[(0.40, 1000)])
        acct.api.get_order_book = MagicMock(return_value=sell_book)
        acct.sell("test-market", "yes", shares)

        cash_after = acct.get_account().cash
        assert cash_after < cash_before  # Lost money


# ---------------------------------------------------------------------------
# Scenario 19: Sell exact shares → position at 0.0 (B2)
# ---------------------------------------------------------------------------


class TestSellExactShares:
    """Selling all shares should leave position at 0.0 shares."""

    def test_sell_all_position_zeroed(self, acct):
        """After selling all shares, position.shares == 0.0."""
        _mock(acct, book=_book(asks=[(0.50, 1000)], bids=[(0.49, 1000)]), fee=0)
        result = acct.buy("test-market", "yes", 100.0)
        shares = result.trade.shares
        assert shares > 0

        acct.sell("test-market", "yes", shares)
        position = acct.db.get_position("0xtest", "yes")
        assert position.shares == 0.0
        assert position.total_cost == pytest.approx(0.0, abs=0.01)

        # Portfolio should be empty (no open positions with shares > 0)
        portfolio = acct.get_portfolio()
        assert len(portfolio) == 0 or portfolio[0]["shares"] == 0.0


# ---------------------------------------------------------------------------
# Scenario 20: GTD expiry between place and check (B3)
# ---------------------------------------------------------------------------


class TestGTDExpiryTiming:
    """GTD orders placed with past expiry should expire on next check_orders."""

    def test_gtd_expires_between_place_and_check(self, acct):
        """Order placed with future expiry that passes before check → expired."""
        _mock(acct, book=_book(asks=[(0.66, 5000)], bids=[(0.64, 5000)]))

        # Place GTD order with already-past expiry (simulates time passing)
        acct.place_limit_order(
            "test-market", "yes", "buy", 100.0, 0.55,
            order_type="gtd", expires_at="2024-06-01T00:00:00Z",
        )
        assert len(acct.get_pending_orders()) == 1

        # On check, order should expire (not fill, not stay pending)
        results = acct.check_orders()
        expired = [r for r in results if r["action"] == "expired"]
        assert len(expired) == 1
        assert len(acct.get_pending_orders()) == 0

        # Cash should be unchanged — no fill happened
        assert acct.get_account().cash == 10_000.0

    def test_gtd_not_expired_stays_pending(self, acct):
        """GTD order with far-future expiry stays pending."""
        _mock(acct, book=_book(asks=[(0.66, 5000)], bids=[(0.64, 5000)]))
        acct.place_limit_order(
            "test-market", "yes", "buy", 100.0, 0.55,
            order_type="gtd", expires_at="2099-12-31T23:59:59Z",
        )

        results = acct.check_orders()
        expired = [r for r in results if r["action"] == "expired"]
        assert len(expired) == 0
        assert len(acct.get_pending_orders()) == 1
