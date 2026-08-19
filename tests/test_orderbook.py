"""Comprehensive tests for the order book fill simulation engine.

Every expected value is hand-calculated to verify exact mathematical
correctness.  This is the most critical test file in the project — any
error in fill calculation makes the entire benchmark invalid.
"""

from __future__ import annotations

import pytest

from pm_trader.models import Fill, OrderBook, OrderBookLevel
from pm_trader.orderbook import (
    _best_ask,
    _best_bid,
    _sum_level_fees,
    calculate_fee,
    simulate_buy_fill,
    simulate_sell_fill,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def simple_book() -> OrderBook:
    """A book with a single level on each side."""
    return OrderBook(
        bids=[OrderBookLevel(price=0.64, size=200.0)],
        asks=[OrderBookLevel(price=0.66, size=200.0)],
    )


@pytest.fixture
def multi_level_book() -> OrderBook:
    """A multi-level book matching the design doc example."""
    return OrderBook(
        bids=[
            OrderBookLevel(price=0.64, size=150.0),
            OrderBookLevel(price=0.63, size=200.0),
            OrderBookLevel(price=0.62, size=300.0),
            OrderBookLevel(price=0.60, size=500.0),
        ],
        asks=[
            OrderBookLevel(price=0.66, size=80.0),
            OrderBookLevel(price=0.67, size=120.0),
            OrderBookLevel(price=0.68, size=200.0),
            OrderBookLevel(price=0.70, size=400.0),
        ],
    )


@pytest.fixture
def thin_book() -> OrderBook:
    """A thin book with limited liquidity."""
    return OrderBook(
        bids=[OrderBookLevel(price=0.64, size=30.0)],
        asks=[OrderBookLevel(price=0.66, size=20.0)],
    )


@pytest.fixture
def empty_book() -> OrderBook:
    """An order book with no levels on either side."""
    return OrderBook(bids=[], asks=[])


# =========================================================================
# BUY TESTS
# =========================================================================

class TestBuySingleLevelFill:
    """$50 buy fills entirely at the first ask level."""

    def test_fills_at_single_level(self, simple_book: OrderBook) -> None:
        # Ask: 0.66, size=200.  $50 / 0.66 = 75.7575... shares
        result = simulate_buy_fill(simple_book, 50.0, fee_rate_bps=0)

        assert result.filled is True
        assert result.is_partial is False
        assert result.levels_filled == 1
        assert len(result.fills) == 1

        assert result.total_cost == pytest.approx(50.0)
        assert result.total_shares == pytest.approx(50.0 / 0.66)
        assert result.avg_price == pytest.approx(0.66)
        assert result.fee == 0.0

    def test_fill_details(self, simple_book: OrderBook) -> None:
        result = simulate_buy_fill(simple_book, 50.0, fee_rate_bps=0)
        fill = result.fills[0]

        assert fill.price == pytest.approx(0.66)
        assert fill.shares == pytest.approx(50.0 / 0.66)
        assert fill.cost == pytest.approx(50.0)
        assert fill.level == 1


class TestBuyMultiLevelFill:
    """$100 buy crosses 2 ask levels."""

    def test_crosses_two_levels(self, multi_level_book: OrderBook) -> None:
        # Ask level 1: price=0.66, size=80 -> cost = 80 * 0.66 = $52.80
        # Remaining: $100 - $52.80 = $47.20
        # Ask level 2: price=0.67, shares = $47.20 / 0.67 = 70.44776...
        # Total shares: 80 + 70.44776... = 150.44776...
        # Avg price: 100 / 150.44776... = 0.66468...
        result = simulate_buy_fill(multi_level_book, 100.0, fee_rate_bps=0)

        assert result.filled is True
        assert result.is_partial is False
        assert result.levels_filled == 2

        expected_remaining = 100.0 - (80.0 * 0.66)
        expected_shares_l2 = expected_remaining / 0.67
        expected_total_shares = 80.0 + expected_shares_l2

        assert result.total_cost == pytest.approx(100.0)
        assert result.total_shares == pytest.approx(expected_total_shares)
        assert result.avg_price == pytest.approx(100.0 / expected_total_shares)

    def test_per_level_fill_details(self, multi_level_book: OrderBook) -> None:
        result = simulate_buy_fill(multi_level_book, 100.0, fee_rate_bps=0)

        assert len(result.fills) == 2

        # Level 1: entire level consumed
        assert result.fills[0].price == pytest.approx(0.66)
        assert result.fills[0].shares == pytest.approx(80.0)
        assert result.fills[0].cost == pytest.approx(52.80)
        assert result.fills[0].level == 1

        # Level 2: partial fill
        remaining = 100.0 - 52.80
        assert result.fills[1].price == pytest.approx(0.67)
        assert result.fills[1].shares == pytest.approx(remaining / 0.67)
        assert result.fills[1].cost == pytest.approx(remaining)
        assert result.fills[1].level == 2


class TestBuyExactLevelBoundary:
    """Buy exactly consumes one level with zero remainder."""

    def test_exact_level_consumption(self, multi_level_book: OrderBook) -> None:
        # Ask level 1: price=0.66, size=80.  Exact cost = 80 * 0.66 = $52.80
        amount = 80.0 * 0.66  # $52.80
        result = simulate_buy_fill(multi_level_book, amount, fee_rate_bps=0)

        assert result.filled is True
        assert result.levels_filled == 1
        assert result.total_cost == pytest.approx(52.80)
        assert result.total_shares == pytest.approx(80.0)
        assert result.avg_price == pytest.approx(0.66)


class TestBuyFokInsufficientLiquidity:
    """FOK order with insufficient book depth is rejected."""

    def test_fok_rejected(self, thin_book: OrderBook) -> None:
        # Ask: price=0.66, size=20.  Max capacity = 20 * 0.66 = $13.20
        # $50 > $13.20 -> FOK rejected
        result = simulate_buy_fill(thin_book, 50.0, fee_rate_bps=0, order_type="fok")

        assert result.filled is False
        assert result.total_cost == 0.0
        assert result.total_shares == 0.0
        assert result.levels_filled == 0
        assert result.fills == []


class TestBuyFakPartialFill:
    """FAK order fills partially when book is thin."""

    def test_fak_partial(self, thin_book: OrderBook) -> None:
        # Ask: price=0.66, size=20.  Max capacity = 20 * 0.66 = $13.20
        # FAK fills $13.20 out of $50 requested
        result = simulate_buy_fill(thin_book, 50.0, fee_rate_bps=0, order_type="fak")

        assert result.filled is False
        assert result.is_partial is True
        assert result.levels_filled == 1
        assert result.total_cost == pytest.approx(20.0 * 0.66)
        assert result.total_shares == pytest.approx(20.0)
        assert result.avg_price == pytest.approx(0.66)


class TestBuyEmptyBook:
    """No asks means no possible fill."""

    def test_fok_rejected(self, empty_book: OrderBook) -> None:
        result = simulate_buy_fill(empty_book, 100.0, fee_rate_bps=0, order_type="fok")

        assert result.filled is False
        assert result.total_shares == 0.0
        assert result.levels_filled == 0

    def test_fak_zero_shares(self, empty_book: OrderBook) -> None:
        result = simulate_buy_fill(empty_book, 100.0, fee_rate_bps=0, order_type="fak")

        assert result.filled is False
        assert result.total_shares == 0.0
        assert result.levels_filled == 0


class TestBuySlippageCalculation:
    """Verify slippage metrics vs best quote and midpoint."""

    def test_single_level_slippage(self, multi_level_book: OrderBook) -> None:
        # Single-level fill at best ask (0.66) => zero slippage vs best quote.
        # Midpoint remains positive slippage.
        result = simulate_buy_fill(multi_level_book, 50.0, fee_rate_bps=0)

        best_ask = 0.66
        midpoint = 0.65
        expected_best_quote = (0.66 - best_ask) / best_ask * 10_000
        expected_midpoint = (0.66 - midpoint) / midpoint * 10_000
        assert result.slippage_bps == pytest.approx(expected_best_quote)
        assert result.slippage_bps_midpoint == pytest.approx(expected_midpoint)

    def test_multi_level_slippage(self, multi_level_book: OrderBook) -> None:
        # $100 buy crosses levels; avg above best ask and above midpoint.
        result = simulate_buy_fill(multi_level_book, 100.0, fee_rate_bps=0)

        best_ask = 0.66
        midpoint = 0.65
        expected_best_quote = (result.avg_price - best_ask) / best_ask * 10_000
        expected_midpoint = (result.avg_price - midpoint) / midpoint * 10_000
        assert result.slippage_bps == pytest.approx(expected_best_quote)
        assert result.slippage_bps_midpoint == pytest.approx(expected_midpoint)
        assert result.slippage_bps > 0  # buying pushes price up


# =========================================================================
# SELL TESTS
# =========================================================================

class TestSellSingleLevelFill:
    """Sell 50 shares, fills at top bid."""

    def test_fills_at_top_bid(self, multi_level_book: OrderBook) -> None:
        # Best bid: price=0.64, size=150.  Selling 50 shares at 0.64
        # Proceeds = 50 * 0.64 = $32.00
        result = simulate_sell_fill(multi_level_book, 50.0, fee_rate_bps=0)

        assert result.filled is True
        assert result.is_partial is False
        assert result.levels_filled == 1
        assert result.total_shares == pytest.approx(50.0)
        assert result.total_cost == pytest.approx(50.0 * 0.64)
        assert result.avg_price == pytest.approx(0.64)
        assert result.fee == 0.0

    def test_sell_fill_details(self, multi_level_book: OrderBook) -> None:
        result = simulate_sell_fill(multi_level_book, 50.0, fee_rate_bps=0)
        fill = result.fills[0]

        assert fill.price == pytest.approx(0.64)
        assert fill.shares == pytest.approx(50.0)
        assert fill.cost == pytest.approx(32.0)
        assert fill.level == 1


class TestSellMultiLevelFill:
    """Sell 200 shares, crosses 2 bid levels."""

    def test_crosses_two_levels(self, multi_level_book: OrderBook) -> None:
        # Bid level 1: price=0.64, size=150 -> sell 150 at 0.64 = $96.00
        # Remaining: 200 - 150 = 50 shares
        # Bid level 2: price=0.63, size=200 -> sell 50 at 0.63 = $31.50
        # Total proceeds: 96.00 + 31.50 = $127.50
        # Avg price: 127.50 / 200 = 0.6375
        result = simulate_sell_fill(multi_level_book, 200.0, fee_rate_bps=0)

        assert result.filled is True
        assert result.is_partial is False
        assert result.levels_filled == 2
        assert result.total_shares == pytest.approx(200.0)
        assert result.total_cost == pytest.approx(127.50)
        assert result.avg_price == pytest.approx(0.6375)

    def test_per_level_fill_details(self, multi_level_book: OrderBook) -> None:
        result = simulate_sell_fill(multi_level_book, 200.0, fee_rate_bps=0)

        assert len(result.fills) == 2

        # Level 1: entire bid level consumed
        assert result.fills[0].price == pytest.approx(0.64)
        assert result.fills[0].shares == pytest.approx(150.0)
        assert result.fills[0].cost == pytest.approx(96.0)

        # Level 2: partial fill
        assert result.fills[1].price == pytest.approx(0.63)
        assert result.fills[1].shares == pytest.approx(50.0)
        assert result.fills[1].cost == pytest.approx(31.50)

    def test_sell_slippage_is_negative(self, multi_level_book: OrderBook) -> None:
        # Selling below best bid and midpoint both produce negative slippage.
        result = simulate_sell_fill(multi_level_book, 200.0, fee_rate_bps=0)

        best_bid = 0.64
        midpoint = 0.65
        expected_best_quote = (0.6375 - best_bid) / best_bid * 10_000
        expected_midpoint = (0.6375 - midpoint) / midpoint * 10_000
        assert result.slippage_bps == pytest.approx(expected_best_quote)
        assert result.slippage_bps_midpoint == pytest.approx(expected_midpoint)
        assert result.slippage_bps < 0  # selling pushes price down


class TestSellFokInsufficientLiquidity:
    """FOK sell rejected when not enough bids."""

    def test_fok_rejected(self, thin_book: OrderBook) -> None:
        # Only 30 shares on the bid side, trying to sell 100
        result = simulate_sell_fill(thin_book, 100.0, fee_rate_bps=0, order_type="fok")

        assert result.filled is False
        assert result.total_cost == 0.0
        assert result.total_shares == 0.0
        assert result.levels_filled == 0


class TestSellFakPartialFill:
    """FAK sells what's available."""

    def test_fak_partial(self, thin_book: OrderBook) -> None:
        # Bid: price=0.64, size=30.  Trying to sell 100 shares.
        # FAK fills 30 shares at 0.64 = $19.20
        result = simulate_sell_fill(thin_book, 100.0, fee_rate_bps=0, order_type="fak")

        assert result.filled is False
        assert result.is_partial is True
        assert result.levels_filled == 1
        assert result.total_shares == pytest.approx(30.0)
        assert result.total_cost == pytest.approx(30.0 * 0.64)
        assert result.avg_price == pytest.approx(0.64)


class TestSellEmptyBook:
    """No bids means no possible sell."""

    def test_fok_rejected(self, empty_book: OrderBook) -> None:
        result = simulate_sell_fill(empty_book, 50.0, fee_rate_bps=0, order_type="fok")

        assert result.filled is False
        assert result.total_shares == 0.0

    def test_fak_zero_shares(self, empty_book: OrderBook) -> None:
        result = simulate_sell_fill(empty_book, 50.0, fee_rate_bps=0, order_type="fak")

        assert result.filled is False
        assert result.total_shares == 0.0


# =========================================================================
# FEE TESTS
# =========================================================================

class TestFeeZeroBps:
    """fee_rate_bps=0 always produces zero fee."""

    def test_zero_fee(self) -> None:
        assert calculate_fee(0, 0.50, 100.0) == 0.0
        assert calculate_fee(0, 0.65, 50.0) == 0.0
        assert calculate_fee(0, 0.99, 1000.0) == 0.0


class TestFeeAtMidpoint:
    """Price=0.50 gives maximum fee (min(0.5, 0.5) = 0.5)."""

    def test_midpoint_fee(self) -> None:
        # 200 bps at price 0.50, size $100
        # fee = (200/10000) * min(0.50, 0.50) * 100 = 0.02 * 0.50 * 100 = 1.00
        fee = calculate_fee(200, 0.50, 100.0)
        assert fee == pytest.approx(1.00)


class TestFeeAtExtreme:
    """Price=0.95 gives near-zero fee (min(0.95, 0.05) = 0.05)."""

    def test_extreme_fee(self) -> None:
        # 200 bps at price 0.95, size $100
        # fee = (200/10000) * min(0.95, 0.05) * 100 = 0.02 * 0.05 * 100 = 0.10
        fee = calculate_fee(200, 0.95, 100.0)
        assert fee == pytest.approx(0.10)


class TestFee200BpsBuy:
    """200 bps on a $100 buy at price 0.65."""

    def test_exact_calculation(self) -> None:
        # fee = (200/10000) * min(0.65, 0.35) * 100 = 0.02 * 0.35 * 100 = 0.70
        fee = calculate_fee(200, 0.65, 100.0)
        assert fee == pytest.approx(0.70)

    def test_buy_with_fee(self, multi_level_book: OrderBook) -> None:
        # $100 buy with 200 bps fee
        result = simulate_buy_fill(multi_level_book, 100.0, fee_rate_bps=200)

        expected_fee = sum(
            calculate_fee(200, fill.price, fill.cost, enforce_minimum=False)
            for fill in result.fills
        )
        assert result.fee == pytest.approx(expected_fee)
        assert result.fee > 0


class TestFee175BpsSell:
    """175 bps on a sell with $64 gross proceeds."""

    def test_exact_calculation(self) -> None:
        # Sell 100 shares at $0.64 => gross proceeds = $64
        # fee = (175/10000) * min(0.64, 0.36) * 64 = 0.4032
        fee = calculate_fee(175, 0.64, 64.0)
        assert fee == pytest.approx(0.4032)

    def test_sell_with_fee(self, multi_level_book: OrderBook) -> None:
        # Sell 100 shares with 175 bps fee
        result = simulate_sell_fill(multi_level_book, 100.0, fee_rate_bps=175)

        # 100 shares fills entirely at bid level 1 (price=0.64, size=150):
        # gross proceeds = $64, fee = 0.0175 * min(0.64, 0.36) * 64 = 0.4032
        assert result.avg_price == pytest.approx(0.64)
        assert result.fee == pytest.approx(0.4032)


class TestFeeMinimum:
    """Minimum fee of 0.0001 is enforced when fee_rate_bps > 0."""

    def test_very_small_trade(self) -> None:
        # Tiny trade: 1 bps at price 0.50, size 0.001
        # fee = (1/10000) * 0.50 * 0.001 = 0.00000005 -> clamped to 0.0001
        fee = calculate_fee(1, 0.50, 0.001)
        assert fee == pytest.approx(0.0001)


class TestFokFloatingPointTolerance:
    """FOK should not reject fills due to tiny floating-point remainders."""

    def test_buy_exact_depth_with_float_noise(self) -> None:
        book = OrderBook(
            bids=[OrderBookLevel(price=0.40, size=1.0)],
            asks=[
                OrderBookLevel(price=0.10, size=1.0),
                OrderBookLevel(price=0.20, size=1.0),
            ],
        )
        result = simulate_buy_fill(book, 0.1 + 0.2, fee_rate_bps=0, order_type="fok")

        assert result.filled is True
        assert result.is_partial is False
        assert result.total_cost == pytest.approx(0.3)

    def test_sell_exact_depth_with_float_noise(self) -> None:
        book = OrderBook(
            bids=[
                OrderBookLevel(price=0.60, size=0.1),
                OrderBookLevel(price=0.59, size=0.2),
            ],
            asks=[OrderBookLevel(price=0.65, size=1.0)],
        )
        result = simulate_sell_fill(book, 0.1 + 0.2, fee_rate_bps=0, order_type="fok")

        assert result.filled is True
        assert result.is_partial is False
        assert result.total_shares == pytest.approx(0.3)


class TestFeeSymmetry:
    """Fee at price p equals fee at price (1-p)."""

    def test_symmetric_prices(self) -> None:
        fee_low = calculate_fee(200, 0.30, 100.0)
        fee_high = calculate_fee(200, 0.70, 100.0)
        assert fee_low == pytest.approx(fee_high)

    def test_symmetric_at_various_prices(self) -> None:
        for price in [0.10, 0.20, 0.35, 0.45, 0.50]:
            fee_a = calculate_fee(150, price, 50.0)
            fee_b = calculate_fee(150, 1.0 - price, 50.0)
            assert fee_a == pytest.approx(fee_b), f"Asymmetric fee at price={price}"


# =========================================================================
# MATH VERIFICATION TESTS
# =========================================================================

class TestBuyTotalCostMatchesSumOfFills:
    """Sum of per-level fill costs must equal total_cost."""

    def test_single_level(self, simple_book: OrderBook) -> None:
        result = simulate_buy_fill(simple_book, 50.0, fee_rate_bps=0)
        assert sum(f.cost for f in result.fills) == pytest.approx(result.total_cost)

    def test_multi_level(self, multi_level_book: OrderBook) -> None:
        result = simulate_buy_fill(multi_level_book, 100.0, fee_rate_bps=0)
        assert sum(f.cost for f in result.fills) == pytest.approx(result.total_cost)

    def test_three_levels(self, multi_level_book: OrderBook) -> None:
        # Enough to cross 3 levels:
        # L1: 80 * 0.66 = 52.80
        # L2: 120 * 0.67 = 80.40
        # L3: partial at 0.68
        # Total L1+L2 = 133.20, so $200 fills into L3
        result = simulate_buy_fill(multi_level_book, 200.0, fee_rate_bps=0)
        assert sum(f.cost for f in result.fills) == pytest.approx(result.total_cost)


class TestSellTotalSharesMatchesSumOfFills:
    """Sum of per-level fill shares must equal total_shares."""

    def test_single_level(self, multi_level_book: OrderBook) -> None:
        result = simulate_sell_fill(multi_level_book, 50.0, fee_rate_bps=0)
        assert sum(f.shares for f in result.fills) == pytest.approx(result.total_shares)

    def test_multi_level(self, multi_level_book: OrderBook) -> None:
        result = simulate_sell_fill(multi_level_book, 200.0, fee_rate_bps=0)
        assert sum(f.shares for f in result.fills) == pytest.approx(result.total_shares)

    def test_three_levels(self, multi_level_book: OrderBook) -> None:
        # 150 + 200 + some from level 3
        result = simulate_sell_fill(multi_level_book, 400.0, fee_rate_bps=0)
        assert sum(f.shares for f in result.fills) == pytest.approx(result.total_shares)


class TestBuySharesEqualsCostDivAvgPrice:
    """total_shares must equal total_cost / avg_price."""

    def test_single_level(self, simple_book: OrderBook) -> None:
        result = simulate_buy_fill(simple_book, 50.0, fee_rate_bps=0)
        assert result.total_shares == pytest.approx(result.total_cost / result.avg_price)

    def test_multi_level(self, multi_level_book: OrderBook) -> None:
        result = simulate_buy_fill(multi_level_book, 100.0, fee_rate_bps=0)
        assert result.total_shares == pytest.approx(result.total_cost / result.avg_price)

    def test_large_order(self, multi_level_book: OrderBook) -> None:
        result = simulate_buy_fill(multi_level_book, 300.0, fee_rate_bps=0)
        assert result.total_shares == pytest.approx(result.total_cost / result.avg_price)


class TestSellCostEqualsSharesTimesAvgPrice:
    """total_cost (proceeds) must equal total_shares * avg_price."""

    def test_single_level(self, multi_level_book: OrderBook) -> None:
        result = simulate_sell_fill(multi_level_book, 50.0, fee_rate_bps=0)
        assert result.total_cost == pytest.approx(result.total_shares * result.avg_price)

    def test_multi_level(self, multi_level_book: OrderBook) -> None:
        result = simulate_sell_fill(multi_level_book, 200.0, fee_rate_bps=0)
        assert result.total_cost == pytest.approx(result.total_shares * result.avg_price)


class TestFillLevelConsistency:
    """Each fill's cost must equal price * shares."""

    def test_buy_fill_level_math(self, multi_level_book: OrderBook) -> None:
        result = simulate_buy_fill(multi_level_book, 200.0, fee_rate_bps=0)
        for fill in result.fills:
            assert fill.cost == pytest.approx(fill.price * fill.shares)

    def test_sell_fill_level_math(self, multi_level_book: OrderBook) -> None:
        result = simulate_sell_fill(multi_level_book, 300.0, fee_rate_bps=0)
        for fill in result.fills:
            assert fill.cost == pytest.approx(fill.price * fill.shares)


# =========================================================================
# EDGE CASE TESTS
# =========================================================================

class TestUnsortedBookLevels:
    """Verify the engine sorts book levels correctly regardless of input order."""

    def test_unsorted_asks(self) -> None:
        book = OrderBook(
            bids=[OrderBookLevel(price=0.64, size=100.0)],
            asks=[
                OrderBookLevel(price=0.70, size=100.0),
                OrderBookLevel(price=0.66, size=50.0),
                OrderBookLevel(price=0.68, size=80.0),
            ],
        )
        result = simulate_buy_fill(book, 30.0, fee_rate_bps=0)
        # Should fill at lowest ask (0.66) first
        assert result.fills[0].price == pytest.approx(0.66)

    def test_unsorted_bids(self) -> None:
        book = OrderBook(
            bids=[
                OrderBookLevel(price=0.60, size=100.0),
                OrderBookLevel(price=0.64, size=50.0),
                OrderBookLevel(price=0.62, size=80.0),
            ],
            asks=[OrderBookLevel(price=0.66, size=100.0)],
        )
        result = simulate_sell_fill(book, 30.0, fee_rate_bps=0)
        # Should fill at highest bid (0.64) first
        assert result.fills[0].price == pytest.approx(0.64)


class TestBidsOnlyBook:
    """Book with only bids — buy should fail, sell should work."""

    def test_buy_fails(self) -> None:
        book = OrderBook(
            bids=[OrderBookLevel(price=0.64, size=100.0)],
            asks=[],
        )
        result = simulate_buy_fill(book, 50.0, fee_rate_bps=0)
        assert result.filled is False

    def test_sell_works(self) -> None:
        book = OrderBook(
            bids=[OrderBookLevel(price=0.64, size=100.0)],
            asks=[],
        )
        result = simulate_sell_fill(book, 50.0, fee_rate_bps=0)
        assert result.filled is True
        assert result.total_shares == pytest.approx(50.0)
        # Slippage is 0.0 because no midpoint can be calculated
        assert result.slippage_bps == pytest.approx(0.0)


class TestAsksOnlyBook:
    """Book with only asks — sell should fail, buy should work."""

    def test_sell_fails(self) -> None:
        book = OrderBook(
            bids=[],
            asks=[OrderBookLevel(price=0.66, size=100.0)],
        )
        result = simulate_sell_fill(book, 50.0, fee_rate_bps=0)
        assert result.filled is False

    def test_buy_works(self) -> None:
        book = OrderBook(
            bids=[],
            asks=[OrderBookLevel(price=0.66, size=100.0)],
        )
        result = simulate_buy_fill(book, 50.0, fee_rate_bps=0)
        assert result.filled is True
        assert result.total_shares == pytest.approx(50.0 / 0.66)
        # Slippage is 0.0 because no midpoint can be calculated
        assert result.slippage_bps == pytest.approx(0.0)


class TestZeroAmountBuy:
    """Buying $0 should produce an empty result."""

    def test_zero_amount(self, simple_book: OrderBook) -> None:
        result = simulate_buy_fill(simple_book, 0.0, fee_rate_bps=0)
        assert result.total_cost == pytest.approx(0.0)
        assert result.total_shares == pytest.approx(0.0)
        assert result.levels_filled == 0


class TestDesignDocExample:
    """Reproduce the exact example from the design doc, section 5."""

    def test_buy_example(self) -> None:
        # Design doc: Buy $100 of YES token
        # Level 1: 0.66 x 80 shares = $52.80
        # Level 2: 0.67 x 70.45 shares = $47.20
        # Total: 150.45 shares for $100.00
        # Avg price: 100 / 150.45 = 0.6647
        book = OrderBook(
            bids=[OrderBookLevel(price=0.64, size=150.0)],
            asks=[
                OrderBookLevel(price=0.66, size=80.0),
                OrderBookLevel(price=0.67, size=120.0),
            ],
        )
        result = simulate_buy_fill(book, 100.0, fee_rate_bps=0)

        remaining_after_l1 = 100.0 - (80.0 * 0.66)
        shares_l2 = remaining_after_l1 / 0.67
        total_shares = 80.0 + shares_l2

        assert result.total_cost == pytest.approx(100.0)
        assert result.total_shares == pytest.approx(total_shares)
        assert result.avg_price == pytest.approx(100.0 / total_shares)

    def test_sell_example(self) -> None:
        # Design doc: Sell 100 shares
        # Level 1: 0.64 x 60 shares = $38.40
        # Level 2: 0.63 x 40 shares = $25.20
        # Total: $63.60 for 100 shares
        # Avg price: 63.60 / 100 = 0.636
        book = OrderBook(
            bids=[
                OrderBookLevel(price=0.64, size=60.0),
                OrderBookLevel(price=0.63, size=200.0),
            ],
            asks=[OrderBookLevel(price=0.66, size=100.0)],
        )
        result = simulate_sell_fill(book, 100.0, fee_rate_bps=0)

        assert result.total_shares == pytest.approx(100.0)
        assert result.total_cost == pytest.approx(63.60)
        assert result.avg_price == pytest.approx(0.636)

    def test_fee_example_200bps(self) -> None:
        # Design doc: 200bps market at avg_price 0.6647
        # fee = 0.02 * min(0.6647, 0.3353) * 100 = 0.02 * 0.3353 * 100 = 0.6706
        book = OrderBook(
            bids=[OrderBookLevel(price=0.64, size=150.0)],
            asks=[
                OrderBookLevel(price=0.66, size=80.0),
                OrderBookLevel(price=0.67, size=120.0),
            ],
        )
        result = simulate_buy_fill(book, 100.0, fee_rate_bps=200)

        expected_fee = sum(
            calculate_fee(200, fill.price, fill.cost, enforce_minimum=False)
            for fill in result.fills
        )
        assert result.fee == pytest.approx(expected_fee)


class TestCoverageBranches:
    def test_best_quote_helpers_return_none_on_empty_side(self) -> None:
        assert _best_ask(OrderBook(bids=[OrderBookLevel(price=0.60, size=10.0)], asks=[])) is None
        assert _best_bid(OrderBook(bids=[], asks=[OrderBookLevel(price=0.70, size=10.0)])) is None

    def test_sum_level_fees_returns_zero_when_formula_is_zero(self) -> None:
        fills = [Fill(price=0.0, shares=10.0, cost=5.0, level=1)]
        assert _sum_level_fees(200, fills) == 0.0

    def test_buy_slippage_falls_back_to_zero_when_best_ask_nonpositive(self) -> None:
        book = OrderBook(
            bids=[OrderBookLevel(price=0.40, size=100.0)],
            asks=[OrderBookLevel(price=0.0, size=100.0)],
        )
        result = simulate_buy_fill(book, 10.0, fee_rate_bps=0, order_type="fak")
        assert result.slippage_bps == pytest.approx(0.0)

    def test_sell_slippage_falls_back_to_zero_when_best_bid_nonpositive(self) -> None:
        book = OrderBook(
            bids=[OrderBookLevel(price=0.0, size=100.0)],
            asks=[OrderBookLevel(price=0.60, size=100.0)],
        )
        result = simulate_sell_fill(book, 10.0, fee_rate_bps=0, order_type="fak")
        assert result.slippage_bps == pytest.approx(0.0)
