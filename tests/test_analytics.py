"""Tests for performance analytics."""

from __future__ import annotations

import math

import pytest

from pm_trader.analytics import (
    _daily_pnl,
    _parse_trade_datetime,
    compute_stats,
    max_drawdown,
    sharpe_ratio,
    win_rate,
)
from pm_trader.models import Account, Trade


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _trade(
    *,
    id: int = 1,
    side: str = "buy",
    outcome: str = "yes",
    avg_price: float = 0.60,
    amount_usd: float = 60.0,
    shares: float = 100.0,
    fee: float = 0.0,
    market_condition_id: str = "0xabc",
    market_slug: str = "test-market",
    created_at: str = "2026-01-15 12:00:00",
) -> Trade:
    return Trade(
        id=id,
        market_condition_id=market_condition_id,
        market_slug=market_slug,
        market_question="Test?",
        outcome=outcome,
        side=side,
        order_type="fok",
        avg_price=avg_price,
        amount_usd=amount_usd,
        shares=shares,
        fee_rate_bps=0,
        fee=fee,
        slippage=0.0,
        levels_filled=1,
        is_partial=False,
        created_at=created_at,
    )


def _account(cash: float = 9_000.0, starting: float = 10_000.0) -> Account:
    return Account(id=1, starting_balance=starting, cash=cash, created_at="2026-01-01")


# ---------------------------------------------------------------------------
# win_rate tests
# ---------------------------------------------------------------------------


class TestWinRate:
    def test_no_trades(self):
        assert win_rate([]) == 0.0

    def test_no_sells(self):
        trades = [_trade(side="buy")]
        assert win_rate(trades) == 0.0

    def test_sell_without_prior_buy(self):
        """Sell with no matching buy falls back to sell's own avg_price (always tie → 0%)."""
        trades = [_trade(id=1, side="sell", avg_price=0.50, amount_usd=50.0, shares=100.0)]
        assert win_rate(trades) == 0.0

    def test_all_wins(self):
        trades = [
            _trade(id=1, side="buy", avg_price=0.50, amount_usd=50.0, shares=100.0,
                   created_at="2026-01-01 10:00:00"),
            _trade(id=2, side="sell", avg_price=0.70, amount_usd=70.0, shares=100.0,
                   created_at="2026-01-02 10:00:00"),
        ]
        assert win_rate(trades) == 1.0

    def test_all_losses(self):
        trades = [
            _trade(id=1, side="buy", avg_price=0.70),
            _trade(id=2, side="sell", avg_price=0.50),
        ]
        assert win_rate(trades) == 0.0

    def test_mixed(self):
        trades = [
            _trade(id=1, side="buy", avg_price=0.50, amount_usd=50.0, shares=100.0, market_condition_id="0x1"),
            _trade(id=2, side="sell", avg_price=0.70, amount_usd=70.0, shares=100.0, market_condition_id="0x1"),  # win
            _trade(id=3, side="buy", avg_price=0.60, amount_usd=60.0, shares=100.0, market_condition_id="0x2"),
            _trade(id=4, side="sell", avg_price=0.40, amount_usd=40.0, shares=100.0, market_condition_id="0x2"),  # loss
        ]
        assert win_rate(trades) == 0.5

    def test_cost_averaged_entry(self):
        """Multiple buys at different prices: win_rate uses weighted-average entry."""
        # Buy 100@0.40 ($40) + Buy 50@0.60 ($30) → avg entry = $70/150 = 0.467
        # Sell at 0.50 > 0.467 → should be a WIN
        trades = [
            _trade(id=1, side="buy", avg_price=0.40, amount_usd=40.0, shares=100.0),
            _trade(id=2, side="buy", avg_price=0.60, amount_usd=30.0, shares=50.0),
            _trade(id=3, side="sell", avg_price=0.50, amount_usd=37.5, shares=75.0),
        ]
        assert win_rate(trades) == 1.0  # Sell at 0.50 > avg entry 0.467

    def test_cost_averaged_loss(self):
        """Cost-averaged entry correctly identifies a loss."""
        # Buy 100@0.60 ($60) + Buy 100@0.70 ($70) → avg entry = $130/200 = 0.65
        # Sell at 0.60 < 0.65 → LOSS
        trades = [
            _trade(id=1, side="buy", avg_price=0.60, amount_usd=60.0, shares=100.0),
            _trade(id=2, side="buy", avg_price=0.70, amount_usd=70.0, shares=100.0),
            _trade(id=3, side="sell", avg_price=0.60, amount_usd=60.0, shares=100.0),
        ]
        assert win_rate(trades) == 0.0

    def test_buy_fees_increase_entry_basis(self):
        """Entry basis includes buy fees for win/loss classification."""
        # Buy 100 shares for $50 with $5 fee -> fee-inclusive basis is 0.55/share.
        # Sell at 0.52 should be LOSS when fees are included.
        trades = [
            _trade(id=1, side="buy", amount_usd=50.0, shares=100.0, avg_price=0.50, fee=5.0),
            _trade(id=2, side="sell", amount_usd=52.0, shares=100.0, avg_price=0.52, fee=0.0),
        ]
        assert win_rate(trades) == 0.0

    def test_fifo_lot_basis_for_partial_sell(self):
        """Sell win/loss classification uses FIFO realized lot cost."""
        trades = [
            _trade(
                id=1,
                side="buy",
                avg_price=0.20,
                amount_usd=20.0,
                shares=100.0,
                created_at="2026-01-01 10:00:00",
            ),
            _trade(
                id=2,
                side="buy",
                avg_price=0.80,
                amount_usd=80.0,
                shares=100.0,
                created_at="2026-01-02 10:00:00",
            ),
            _trade(
                id=3,
                side="sell",
                avg_price=0.50,
                amount_usd=50.0,
                shares=100.0,
                created_at="2026-01-03 10:00:00",
            ),
        ]
        assert win_rate(trades) == 1.0

    def test_ignores_zero_share_buy_and_unknown_side(self):
        trades = [
            _trade(id=1, side="buy", shares=0.0, amount_usd=0.0),
            _trade(id=2, side="hold", amount_usd=0.0),  # unknown side should be ignored
            _trade(id=3, side="sell", avg_price=0.5, amount_usd=50.0, shares=100.0),
        ]
        assert win_rate(trades) == 0.0


# ---------------------------------------------------------------------------
# sharpe_ratio tests
# ---------------------------------------------------------------------------


class TestSharpeRatio:
    def test_no_trades(self):
        assert sharpe_ratio([], 10_000) == 0.0

    def test_single_trade(self):
        # Need at least 2 days of P&L
        trades = [_trade(side="sell", amount_usd=100, fee=0)]
        assert sharpe_ratio(trades, 10_000) == 0.0

    def test_consistent_positive_returns(self):
        # Two days of positive P&L → positive Sharpe
        trades = [
            _trade(id=1, side="sell", amount_usd=100, fee=0, created_at="2026-01-01 10:00:00"),
            _trade(id=2, side="sell", amount_usd=100, fee=0, created_at="2026-01-02 10:00:00"),
        ]
        result = sharpe_ratio(trades, 10_000)
        assert result > 0

    def test_zero_cumulative(self):
        """When cumulative balance goes to zero, daily return should be 0.0."""
        # Two trades that wipe out the account: lose everything day 1, gain day 2
        trades = [
            _trade(id=1, side="buy", amount_usd=10_000, fee=0, created_at="2026-01-01 10:00:00"),
            _trade(id=2, side="sell", amount_usd=50, fee=0, created_at="2026-01-02 10:00:00"),
        ]
        result = sharpe_ratio(trades, 10_000)
        # Should not crash and return some finite value
        assert math.isfinite(result)

    def test_zero_std_returns_zero(self):
        """When all daily returns are exactly zero, std=0 → sharpe=0."""
        # Each day: buy $100 + sell $100 → net P&L = 0 per day
        trades = [
            _trade(id=1, side="buy", amount_usd=100, fee=0, created_at="2026-01-01 08:00:00"),
            _trade(id=2, side="sell", amount_usd=100, fee=0, created_at="2026-01-01 12:00:00"),
            _trade(id=3, side="buy", amount_usd=100, fee=0, created_at="2026-01-02 08:00:00"),
            _trade(id=4, side="sell", amount_usd=100, fee=0, created_at="2026-01-02 12:00:00"),
        ]
        result = sharpe_ratio(trades, 10_000)
        assert result == 0.0

    def test_volatile_returns(self):
        # Big win then big loss → lower Sharpe than consistent
        consistent = [
            _trade(id=1, side="sell", amount_usd=50, fee=0, created_at="2026-01-01 10:00:00"),
            _trade(id=2, side="sell", amount_usd=50, fee=0, created_at="2026-01-02 10:00:00"),
        ]
        volatile = [
            _trade(id=1, side="sell", amount_usd=200, fee=0, created_at="2026-01-01 10:00:00"),
            _trade(id=2, side="buy", amount_usd=100, fee=0, created_at="2026-01-02 10:00:00"),
        ]
        assert sharpe_ratio(consistent, 10_000) > sharpe_ratio(volatile, 10_000)

    def test_zero_trade_days_are_included_in_returns_series(self):
        contiguous = [
            _trade(id=1, side="sell", amount_usd=100, fee=0, created_at="2026-01-01 10:00:00"),
            _trade(id=2, side="sell", amount_usd=100, fee=0, created_at="2026-01-02 10:00:00"),
        ]
        with_gap = [
            _trade(id=1, side="sell", amount_usd=100, fee=0, created_at="2026-01-01 10:00:00"),
            _trade(id=2, side="sell", amount_usd=100, fee=0, created_at="2026-01-03 10:00:00"),
        ]
        assert sharpe_ratio(with_gap, 10_000) < sharpe_ratio(contiguous, 10_000)


# ---------------------------------------------------------------------------
# max_drawdown tests
# ---------------------------------------------------------------------------


class TestMaxDrawdown:
    def test_no_trades(self):
        assert max_drawdown([], 10_000) == 0.0

    def test_only_wins(self):
        trades = [
            _trade(id=1, side="sell", amount_usd=100, fee=0),
        ]
        assert max_drawdown(trades, 10_000) == 0.0

    def test_single_loss(self):
        trades = [
            _trade(id=1, side="buy", amount_usd=1_000, fee=0),
        ]
        dd = max_drawdown(trades, 10_000)
        # Lost 1000 from 10000 peak → 10%
        assert dd == pytest.approx(0.10, abs=0.001)

    def test_recovery_still_records_peak_dd(self):
        trades = [
            _trade(id=1, side="buy", amount_usd=2_000, fee=0, created_at="2026-01-01 10:00:00"),
            _trade(id=2, side="sell", amount_usd=3_000, fee=0, created_at="2026-01-02 10:00:00"),
        ]
        dd = max_drawdown(trades, 10_000)
        # After buy: 8000, after sell: 11000. Peak was 10000, trough 8000 → 20%
        assert dd == pytest.approx(0.20, abs=0.001)

    def test_multiple_drawdowns(self):
        trades = [
            _trade(id=1, side="buy", amount_usd=1_000, fee=0, created_at="2026-01-01 10:00:00"),
            _trade(id=2, side="sell", amount_usd=2_000, fee=0, created_at="2026-01-02 10:00:00"),
            _trade(id=3, side="buy", amount_usd=3_000, fee=0, created_at="2026-01-03 10:00:00"),
        ]
        dd = max_drawdown(trades, 10_000)
        # Sequence: 10000 → 9000 → 11000 → 8000
        # Max DD = (11000 - 8000) / 11000 = 27.3%
        assert dd == pytest.approx(3_000 / 11_000, abs=0.001)


# ---------------------------------------------------------------------------
# compute_stats tests
# ---------------------------------------------------------------------------


class TestComputeStats:
    def test_empty_account(self):
        stats = compute_stats([], _account(cash=10_000), 0.0)
        assert stats["total_trades"] == 0
        assert stats["pnl"] == 0.0
        assert stats["roi_pct"] == 0.0
        assert stats["win_rate"] == 0.0
        assert stats["sharpe_ratio"] == 0.0
        assert stats["max_drawdown"] == 0.0

    def test_with_trades(self):
        trades = [
            _trade(id=2, side="sell", amount_usd=80, fee=1.0, avg_price=0.70,
                   created_at="2026-01-02 10:00:00"),
            _trade(id=1, side="buy", amount_usd=60, fee=0.5, avg_price=0.50,
                   created_at="2026-01-01 10:00:00"),
        ]
        stats = compute_stats(trades, _account(cash=9_018.5), positions_value=0.0)
        assert stats["total_trades"] == 2
        assert stats["buy_count"] == 1
        assert stats["sell_count"] == 1
        assert stats["total_fees"] == pytest.approx(1.5)
        assert stats["pnl"] == pytest.approx(-981.5)

    def test_roi_calculation(self):
        stats = compute_stats([], _account(cash=11_000, starting=10_000), 0.0)
        assert stats["roi_pct"] == pytest.approx(10.0)

    def test_positions_value_included(self):
        stats = compute_stats([], _account(cash=8_000), positions_value=3_000)
        assert stats["total_value"] == pytest.approx(11_000)
        assert stats["pnl"] == pytest.approx(1_000)


class TestAnalyticsInternals:
    def test_daily_pnl_returns_empty_when_no_cashflow_sides(self):
        trades = [_trade(id=1, side="hold", amount_usd=10.0, created_at="2026-01-01 10:00:00")]
        assert _daily_pnl(trades) == []

    def test_parse_trade_datetime_handles_blank(self):
        assert _parse_trade_datetime("").year == 1

    def test_parse_trade_datetime_handles_z_suffix(self):
        dt = _parse_trade_datetime("2026-01-01T00:00:00Z")
        assert dt.year == 2026

    def test_parse_trade_datetime_invalid_with_space_fallback(self):
        assert _parse_trade_datetime("bad date").year == 1
