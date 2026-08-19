"""Dataclasses and error types for pm-trader."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# Error hierarchy
# ---------------------------------------------------------------------------

class SimError(Exception):
    """Base error for all pm-trader errors."""

    code: str = "SIM_ERROR"

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


class NotInitializedError(SimError):
    code = "NOT_INITIALIZED"

    def __init__(self, message: str = "Account not initialized. Run 'pm-trader init' first.") -> None:
        super().__init__(message)


class InsufficientBalanceError(SimError):
    code = "INSUFFICIENT_BALANCE"

    def __init__(self, required: float, available: float) -> None:
        super().__init__(
            f"Insufficient balance: need ${required:.2f}, have ${available:.2f}"
        )
        self.required = required
        self.available = available


class MarketNotFoundError(SimError):
    code = "MARKET_NOT_FOUND"

    def __init__(self, identifier: str) -> None:
        super().__init__(f"Market not found: {identifier}")
        self.identifier = identifier


class MarketClosedError(SimError):
    code = "MARKET_CLOSED"

    def __init__(self, slug: str) -> None:
        super().__init__(f"Market is closed: {slug}")
        self.slug = slug


class NoPositionError(SimError):
    code = "NO_POSITION"

    def __init__(self, market: str, outcome: str) -> None:
        super().__init__(f"No position in {market} ({outcome})")
        self.market = market
        self.outcome = outcome


class InvalidOutcomeError(SimError):
    code = "INVALID_OUTCOME"

    def __init__(self, outcome: str, valid: list[str] | None = None) -> None:
        if valid:
            super().__init__(f"Invalid outcome: {outcome!r}. Must be one of {valid}.")
        else:
            super().__init__(f"Invalid outcome: {outcome!r}.")
        self.outcome = outcome


class OrderRejectedError(SimError):
    code = "ORDER_REJECTED"

    def __init__(self, reason: str) -> None:
        super().__init__(f"Order rejected: {reason}")
        self.reason = reason


class TickSizeViolationError(SimError):
    code = "TICK_SIZE_VIOLATION"

    def __init__(self, price: float, tick_size: float) -> None:
        super().__init__(
            f"Price {price} violates tick size {tick_size}"
        )
        self.price = price
        self.tick_size = tick_size


class AmbiguousResolutionError(SimError):
    code = "AMBIGUOUS_RESOLUTION"

    def __init__(self, slug: str, prices: dict) -> None:
        super().__init__(
            f"No clear winner for {slug}: outcome prices {prices} "
            f"(none >= 0.99)"
        )
        self.slug = slug
        self.prices = prices


class ApiError(SimError):
    code = "API_ERROR"

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


# ---------------------------------------------------------------------------
# Market
# ---------------------------------------------------------------------------

@dataclass
class Market:
    """A Polymarket prediction market (binary: YES/NO)."""

    condition_id: str
    slug: str
    question: str
    description: str
    outcomes: list[str]
    outcome_prices: list[float]
    tokens: list[dict[str, Any]]
    active: bool
    closed: bool
    accepting_orders: bool = True
    neg_risk: bool = False
    volume: float = 0.0
    liquidity: float = 0.0
    end_date: str = ""
    fee_rate_bps: int = 0
    tick_size: float = 0.01

    def get_token_id(self, outcome: str) -> str:
        """Token ID for any outcome (case-insensitive)."""
        outcome_lower = outcome.lower()
        for token in self.tokens:
            if token.get("outcome", "").lower() == outcome_lower:
                return token["token_id"]
        raise ValueError(f"No token found for outcome {outcome!r}")

    @property
    def yes_token_id(self) -> str:
        """Token ID for the YES outcome."""
        return self.get_token_id("yes")

    @property
    def no_token_id(self) -> str:
        """Token ID for the NO outcome."""
        return self.get_token_id("no")

    @property
    def yes_price(self) -> float:
        """Current YES price from outcome_prices."""
        for i, outcome in enumerate(self.outcomes):
            if outcome.lower() == "yes":
                return self.outcome_prices[i]
        return 0.0

    @property
    def no_price(self) -> float:
        """Current NO price from outcome_prices."""
        for i, outcome in enumerate(self.outcomes):
            if outcome.lower() == "no":
                return self.outcome_prices[i]
        return 0.0


# ---------------------------------------------------------------------------
# Order book
# ---------------------------------------------------------------------------

@dataclass
class OrderBookLevel:
    """A single price level in the order book."""

    price: float
    size: float


@dataclass
class OrderBook:
    """Full order book for a token (one side of a market)."""

    bids: list[OrderBookLevel] = field(default_factory=list)
    asks: list[OrderBookLevel] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Fill / execution
# ---------------------------------------------------------------------------

@dataclass
class Fill:
    """A per-level fill from walking the order book."""

    price: float
    shares: float
    cost: float
    level: int


@dataclass
class FillResult:
    """Result of walking the order book to fill an order."""

    filled: bool
    avg_price: float
    total_cost: float
    total_shares: float
    fee: float
    slippage_bps: float
    levels_filled: int
    is_partial: bool
    fills: list[Fill] = field(default_factory=list)
    slippage_bps_midpoint: float = 0.0


# ---------------------------------------------------------------------------
# Trade
# ---------------------------------------------------------------------------

@dataclass
class Trade:
    """A recorded trade with full execution details."""

    id: int
    market_condition_id: str
    market_slug: str
    market_question: str
    outcome: str
    side: str
    order_type: str
    avg_price: float
    amount_usd: float
    shares: float
    fee_rate_bps: int
    fee: float
    slippage: float
    levels_filled: int
    is_partial: bool
    created_at: str


# ---------------------------------------------------------------------------
# Position
# ---------------------------------------------------------------------------

@dataclass
class Position:
    """A position in a specific market outcome."""

    market_condition_id: str
    market_slug: str
    market_question: str
    outcome: str
    shares: float
    avg_entry_price: float
    total_cost: float
    realized_pnl: float
    is_resolved: bool
    resolved_at: str | None = None

    def current_price(self, live_price: float) -> float:
        """Return the live price for this position."""
        return live_price

    def current_value(self, live_price: float) -> float:
        """Current value of this position at the live price."""
        return self.shares * live_price

    def unrealized_pnl(self, live_price: float) -> float:
        """Unrealized P&L at the given live price."""
        return self.current_value(live_price) - self.total_cost

    def percent_pnl(self, live_price: float) -> float:
        """Percentage P&L at the given live price."""
        if self.total_cost == 0:
            return 0.0
        return (self.unrealized_pnl(live_price) / self.total_cost) * 100


# ---------------------------------------------------------------------------
# Account
# ---------------------------------------------------------------------------

@dataclass
class Account:
    """Paper trading account."""

    id: int
    starting_balance: float
    cash: float
    created_at: str


# ---------------------------------------------------------------------------
# Result wrappers
# ---------------------------------------------------------------------------

@dataclass
class TradeResult:
    """Wrapper returned after executing a trade."""

    trade: Trade
    account: Account


@dataclass
class ResolveResult:
    """Wrapper returned after resolving a market position."""

    position: Position
    payout: float
    account: Account
