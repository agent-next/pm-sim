"""Performance analytics for pm-trader paper trading.

Pure functions that compute metrics from trade history and account data.
No side effects, no API calls, no database writes.
"""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import date, datetime, timedelta

from pm_trader.models import Account, Trade

_EPSILON = 1e-9


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

    chronological = _sort_trades_chronological(trades)

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

    Uses FIFO lots per (market_condition_id, outcome) for entry cost accounting.
    A sell is "winning" if net proceeds exceed realized FIFO entry cost.
    """
    chronological = _sort_trades_chronological(trades)
    sells = [t for t in chronological if t.side == "sell"]
    if not sells:
        return 0.0

    # FIFO lots: [(remaining_shares, cost_per_share), ...]
    lots: dict[tuple[str, str], list[list[float]]] = defaultdict(list)
    wins = 0

    for t in chronological:
        key = (t.market_condition_id, t.outcome.lower().strip())
        if t.side == "buy":
            if t.shares <= _EPSILON:
                continue
            cost_per_share = (t.amount_usd + t.fee) / t.shares
            lots[key].append([t.shares, cost_per_share])
            continue
        if t.side != "sell":
            continue

        remaining = max(0.0, t.shares)
        entry_cost = 0.0

        while remaining > _EPSILON and lots[key]:
            lot = lots[key][0]
            lot_shares, lot_cost_per_share = lot
            take = min(lot_shares, remaining)
            entry_cost += take * lot_cost_per_share
            lot_shares -= take
            remaining -= take

            if lot_shares <= _EPSILON:
                lots[key].pop(0)
            else:
                lot[0] = lot_shares

        if remaining > _EPSILON:
            # Unknown lot basis fallback: neutral to avoid false wins.
            entry_cost += remaining * t.avg_price

        proceeds = t.amount_usd - t.fee
        if proceeds > entry_cost + _EPSILON:
            wins += 1

    return wins / len(sells)


def sharpe_ratio(
    trades_chronological: list[Trade],
    starting_balance: float,
    annualize_days: int = 365,
) -> float:
    """Annualized Sharpe ratio from daily equity returns (risk-free=0)."""
    daily_returns = _daily_returns(trades_chronological, starting_balance)
    if len(daily_returns) < 2:
        return 0.0

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
    """Maximum drawdown from the daily equity curve (0.0 to 1.0)."""
    if not trades_chronological:
        return 0.0

    equity_curve = _daily_equity_curve(trades_chronological, starting_balance)
    peak = equity_curve[0]
    max_dd = 0.0

    for equity in equity_curve:
        if equity > peak:
            peak = equity

        if peak > 0:
            dd = (peak - equity) / peak
            max_dd = max(max_dd, dd)

    return max_dd


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _daily_pnl(trades_chronological: list[Trade]) -> list[float]:
    """Daily net cashflow series from first trade date to last, zero-filled."""
    return _daily_cashflows(trades_chronological)


def _avg_trade_size(trades: list[Trade]) -> float:
    """Average trade size in USD."""
    if not trades:
        return 0.0
    return sum(t.amount_usd for t in trades) / len(trades)


def _sort_trades_chronological(trades: list[Trade]) -> list[Trade]:
    """Sort trades oldest-first by timestamp, then id."""
    return sorted(trades, key=lambda t: (_parse_trade_datetime(t.created_at), t.id))


def _parse_trade_datetime(created_at: str) -> datetime:
    text = created_at.strip()
    if not text:
        return datetime.min
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        pass
    if " " in text and "T" not in text:
        try:
            return datetime.fromisoformat(text.replace(" ", "T", 1))
        except ValueError:
            pass
    try:
        return datetime.strptime(text[:19], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return datetime.min


def _daily_cashflows(trades_chronological: list[Trade]) -> list[float]:
    if not trades_chronological:
        return []

    by_day: dict[date, float] = defaultdict(float)
    for t in trades_chronological:
        day = _parse_trade_datetime(t.created_at).date()
        if t.side == "buy":
            by_day[day] -= (t.amount_usd + t.fee)
        elif t.side == "sell":
            by_day[day] += (t.amount_usd - t.fee)

    if not by_day:
        return []

    days: list[float] = []
    current = min(by_day.keys())
    end = max(by_day.keys())
    while current <= end:
        days.append(by_day.get(current, 0.0))
        current += timedelta(days=1)
    return days


def _daily_equity_curve(
    trades_chronological: list[Trade],
    starting_balance: float,
) -> list[float]:
    daily_cashflows = _daily_cashflows(trades_chronological)
    equity = [starting_balance]
    for pnl in daily_cashflows:
        equity.append(equity[-1] + pnl)
    return equity


def _daily_returns(
    trades_chronological: list[Trade],
    starting_balance: float,
) -> list[float]:
    equity_curve = _daily_equity_curve(trades_chronological, starting_balance)
    if len(equity_curve) < 2:
        return []
    returns: list[float] = []
    for i in range(1, len(equity_curve)):
        previous = equity_curve[i - 1]
        current = equity_curve[i]
        if previous > 0:
            returns.append((current - previous) / previous)
        else:
            returns.append(0.0)
    return returns
