"""Performance analytics for pm-trader paper trading.

Pure functions that compute metrics from trade history and account data.
No side effects, no API calls, no database writes.
"""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime

from pm_trader.models import Account, Trade


def compute_stats(
    trades: list[Trade],
    account: Account,
    positions_value: float = 0.0,
) -> dict:
    """Compute all analytics metrics from trade history.

    Args:
        trades: All trades (newest first from DB).
        account: Current account state.
        positions_value: Sum of current_value for open positions.

    Returns:
        Dict with all metrics.
    """
    total_value = account.cash + positions_value
    pnl = total_value - account.starting_balance
    roi_pct = (pnl / account.starting_balance * 100) if account.starting_balance else 0.0

    # Reverse to chronological order for time-series calculations
    chronological = list(reversed(trades))

    return {
        "starting_balance": account.starting_balance,
        "cash": account.cash,
        "positions_value": positions_value,
        "total_value": total_value,
        "pnl": pnl,
        "roi_pct": roi_pct,
        "total_trades": len(trades),
        "buy_count": sum(1 for t in trades if t.side == "buy"),
        "sell_count": sum(1 for t in trades if t.side == "sell"),
        "win_rate": win_rate(trades),
        "sharpe_ratio": sharpe_ratio(chronological, account.starting_balance),
        "max_drawdown": max_drawdown(chronological, account.starting_balance),
        "total_fees": sum(t.fee for t in trades),
        "avg_trade_size": _avg_trade_size(trades),
    }


def win_rate(trades: list[Trade]) -> float:
    """Fraction of sell trades with positive realized P&L.

    A sell is "winning" if the sell avg_price exceeds the weighted-average
    entry price from all buys in that (market, outcome).

    Tracks cumulative fee-inclusive cost and shares per position key to compute
    cost-averaged entry, rather than using only the last buy price.
    """
    sells = [t for t in trades if t.side == "sell"]
    if not sells:
        return 0.0

    # Build weighted-average entry price per (market, outcome)
    buy_cost: dict[tuple[str, str], float] = defaultdict(float)
    buy_shares: dict[tuple[str, str], float] = defaultdict(float)
    for t in trades:
        if t.side == "buy":
            key = (t.market_condition_id, t.outcome)
            # Align with engine position accounting: buy cost basis includes fees.
            buy_cost[key] += (t.amount_usd + t.fee)
            buy_shares[key] += t.shares

    wins = 0
    for t in sells:
        key = (t.market_condition_id, t.outcome)
        total_shares = buy_shares.get(key, 0.0)
        if total_shares > 0:
            entry_price = buy_cost[key] / total_shares
        else:
            entry_price = t.avg_price
        if t.avg_price > entry_price:
            wins += 1

    return wins / len(sells)


def sharpe_ratio(
    trades_chronological: list[Trade],
    starting_balance: float,
    annualize_days: int = 365,
) -> float:
    """Annualized Sharpe ratio from daily P&L.

    Groups trades by date, computes daily returns as
    daily_pnl / portfolio_value_at_start_of_day.
    Assumes risk-free rate = 0 (standard for prediction markets).
    """
    daily_pnl = _daily_pnl(trades_chronological)
    if len(daily_pnl) < 2:
        return 0.0

    # Convert daily P&L to daily returns
    cumulative = starting_balance
    daily_returns = []
    for pnl in daily_pnl:
        if cumulative > 0:
            daily_returns.append(pnl / cumulative)
        else:
            daily_returns.append(0.0)
        cumulative += pnl

    mean_ret = sum(daily_returns) / len(daily_returns)
    variance = sum((r - mean_ret) ** 2 for r in daily_returns) / (len(daily_returns) - 1)
    std_ret = math.sqrt(variance)

    if std_ret == 0:
        return 0.0

    return (mean_ret / std_ret) * math.sqrt(annualize_days)


def max_drawdown(
    trades_chronological: list[Trade],
    starting_balance: float,
) -> float:
    """Maximum drawdown as a fraction (0.0 to 1.0).

    Tracks cumulative P&L, finds the largest peak-to-trough decline
    relative to the peak.
    """
    if not trades_chronological:
        return 0.0

    cumulative = starting_balance
    peak = cumulative
    max_dd = 0.0

    for t in trades_chronological:
        if t.side == "buy":
            cumulative -= (t.amount_usd + t.fee)
        elif t.side == "sell":
            cumulative += (t.amount_usd - t.fee)

        if cumulative > peak:
            peak = cumulative

        if peak > 0:
            dd = (peak - cumulative) / peak
            max_dd = max(max_dd, dd)

    return max_dd


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _daily_pnl(trades_chronological: list[Trade]) -> list[float]:
    """Group trades by date and compute net P&L per day."""
    if not trades_chronological:
        return []

    by_date: dict[str, float] = defaultdict(float)
    for t in trades_chronological:
        # Parse date from created_at (format: "YYYY-MM-DD HH:MM:SS" or ISO)
        date_str = t.created_at[:10]
        if t.side == "buy":
            by_date[date_str] -= (t.amount_usd + t.fee)
        elif t.side == "sell":
            by_date[date_str] += (t.amount_usd - t.fee)

    # Return in date order
    return [by_date[d] for d in sorted(by_date.keys())]


def _avg_trade_size(trades: list[Trade]) -> float:
    """Average trade size in USD."""
    if not trades:
        return 0.0
    return sum(t.amount_usd for t in trades) / len(trades)
