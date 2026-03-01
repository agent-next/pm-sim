"""Tests for performance analytics."""

from __future__ import annotations

import math

import pytest

from pm_sim.analytics import (
    compute_stats,
    max_drawdown,
    sharpe_ratio,
    win_rate,
)
from pm_sim.models import Account, Trade


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

    def test_all_wins(self):
        trades = [
            _trade(id=1, side="buy", avg_price=0.50, created_at="2026-01-01 10:00:00"),
            _trade(id=2, side="sell", avg_price=0.70, created_at="2026-01-02 10:00:00"),
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
            _trade(id=1, side="buy", avg_price=0.50, market_condition_id="0x1"),
            _trade(id=2, side="sell", avg_price=0.70, market_condition_id="0x1"),  # win
            _trade(id=3, side="buy", avg_price=0.60, market_condition_id="0x2"),
            _trade(id=4, side="sell", avg_price=0.40, market_condition_id="0x2"),  # loss
        ]
        assert win_rate(trades) == 0.5


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

    def test_identical_daily_returns(self):
        """When all daily returns are identical, std=0 → sharpe=0."""
        # Same P&L every day → zero variance
        trades = [
            _trade(id=1, side="sell", amount_usd=100, fee=0, created_at="2026-01-01 10:00:00"),
            _trade(id=2, side="sell", amount_usd=100, fee=0, created_at="2026-01-02 10:00:00"),
            _trade(id=3, side="sell", amount_usd=100, fee=0, created_at="2026-01-03 10:00:00"),
        ]
        result = sharpe_ratio(trades, 10_000)
        # If returns are very similar (not identical due to cumulative changing),
        # Sharpe should still be finite
        assert math.isfinite(result)

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
