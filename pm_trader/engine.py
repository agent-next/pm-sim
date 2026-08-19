"""Trade execution engine for pm-trader.

Orchestrates the full buy/sell/resolve workflow by wiring together
the API client, order book simulator, and database layer.
"""

from __future__ import annotations

from pathlib import Path

from pm_trader.api import PolymarketClient
from pm_trader.db import Database
from pm_trader.models import (
    Account,
    AmbiguousResolutionError,
    ApiError,
    InsufficientBalanceError,
    InvalidOutcomeError,
    MarketClosedError,
    NoPositionError,
    NotInitializedError,
    OrderRejectedError,
    Position,
    ResolveResult,
    TickSizeViolationError,
    Trade,
    TradeResult,
)
from pm_trader.orders import (
    cancel_all_orders as _cancel_all_orders,
    cancel_order,
    create_order,
    expire_orders,
    get_pending_orders,
    get_reserved_buy_notional,
    init_orders_schema,
    mark_filled,
    mark_partially_filled,
    reject_order,
    should_fill,
)
from pm_trader.orderbook import calculate_fee, simulate_buy_fill, simulate_sell_fill

MIN_ORDER_USD = 1.0  # Polymarket minimum order size
TICK_EPSILON = 1e-9
FILL_EPSILON = 1e-9

# Errors that indicate an order is permanently unfillable (not transient)
_PERMANENT_ORDER_ERRORS = (
    OrderRejectedError,
    InsufficientBalanceError,
    InvalidOutcomeError,
    MarketClosedError,
    NoPositionError,
)


class Engine:
    """Paper trading engine — 1:1 faithful to Polymarket execution."""

    def __init__(self, data_dir: Path) -> None:
        self.db = Database(data_dir)
        self.db.init_schema()
        init_orders_schema(self.db.conn)
        self.api = PolymarketClient(self.db)

    def close(self) -> None:
        self.api.close()
        self.db.close()

    # ------------------------------------------------------------------
    # Account
    # ------------------------------------------------------------------

    def init_account(self, balance: float = 10_000.0) -> Account:
        return self.db.init_account(balance)

    def get_account(self) -> Account:
        account = self.db.get_account()
        if account is None:
            raise NotInitializedError()
        return account

    def reset(self) -> None:
        self.db.reset()
        init_orders_schema(self.db.conn)

    # ------------------------------------------------------------------
    # Validation helpers
    # ------------------------------------------------------------------

    def _require_account(self) -> Account:
        return self.get_account()

    @staticmethod
    def _validate_outcome(outcome: str, market=None) -> str:
        """Validate and normalize outcome against the market's actual outcomes.

        When market is provided, verifies the outcome exists in that market.
        Without market, only normalizes (caller is responsible for validation).
        """
        outcome = outcome.lower().strip()
        if not outcome:
            raise InvalidOutcomeError(outcome)
        if market is not None:
            valid = [o.lower() for o in market.outcomes]
            if outcome not in valid:
                raise InvalidOutcomeError(outcome, valid)
        return outcome

    @staticmethod
    def _validate_tick_size(price: float, tick_size: float) -> None:
        """Ensure a user-supplied limit price conforms to market tick size."""
        if tick_size <= 0:
            return
        ticks = round(price / tick_size)
        snapped = ticks * tick_size
        if abs(price - snapped) > TICK_EPSILON:
            raise TickSizeViolationError(price, tick_size)

    def _reserved_buy_notional(self) -> float:
        """USD currently reserved by open buy limit orders."""
        return get_reserved_buy_notional(self.db.conn)

    def _available_cash(self) -> float:
        """Cash available for new buy orders after existing reservations."""
        account = self._require_account()
        return max(0.0, account.cash - self._reserved_buy_notional())

    @staticmethod
    def _estimate_buy_fee_upper_bound(amount_usd: float, fee_rate_bps: int) -> float:
        """Conservative fee estimate for reserving buy limit-order cash."""
        if amount_usd <= 0 or fee_rate_bps <= 0:
            return 0.0
        # Worst case of min(price, 1-price) occurs at price=0.5.
        return calculate_fee(fee_rate_bps, 0.5, amount_usd)

    @staticmethod
    def _require_market_tradable(market) -> None:
        """Reject trades on closed, inactive, or non-accepting markets."""
        if market.closed:
            raise MarketClosedError(market.slug)
        if not market.active:
            raise OrderRejectedError(f"Market '{market.slug}' is not active")
        if not market.accepting_orders:
            raise OrderRejectedError(f"Market '{market.slug}' is not accepting orders")

    # ------------------------------------------------------------------
    # BUY — spend USD, receive shares
    # ------------------------------------------------------------------

    def buy(
        self,
        slug_or_id: str,
        outcome: str,
        amount_usd: float,
        order_type: str = "fok",
    ) -> TradeResult:
        """Execute a buy order: spend amount_usd to receive shares.

        Walks the real order book ASK side level-by-level.
        """
        account = self._require_account()

        if amount_usd < MIN_ORDER_USD:
            raise OrderRejectedError(
                f"Minimum order size is ${MIN_ORDER_USD:.2f}"
            )

        # Fetch market and validate outcome against actual market outcomes
        market = self.api.get_market(slug_or_id)
        outcome = self._validate_outcome(outcome, market)
        self._require_market_tradable(market)

        # Fetch live order book and fee rate
        token_id = market.get_token_id(outcome)
        book = self.api.get_order_book(token_id)
        fee_rate_bps = self.api.get_fee_rate(token_id)

        # Simulate fill against the real order book
        fill = simulate_buy_fill(book, amount_usd, fee_rate_bps, order_type)

        if not fill.filled and not fill.is_partial:
            raise OrderRejectedError(
                "Insufficient liquidity in order book (FOK rejected)"
            )

        # Check cash: need total_cost + fee
        total_outflow = fill.total_cost + fill.fee
        if total_outflow > account.cash:
            raise InsufficientBalanceError(
                required=total_outflow, available=account.cash
            )

        # Update cash
        new_cash = account.cash - total_outflow
        self.db.update_cash(new_cash)

        # Record trade
        trade = self.db.insert_trade(
            market_condition_id=market.condition_id,
            market_slug=market.slug,
            market_question=market.question,
            outcome=outcome,
            side="buy",
            order_type=order_type,
            avg_price=fill.avg_price,
            amount_usd=fill.total_cost,
            shares=fill.total_shares,
            fee_rate_bps=fee_rate_bps,
            fee=fill.fee,
            slippage=fill.slippage_bps,
            levels_filled=fill.levels_filled,
            is_partial=fill.is_partial,
        )

        # Update position
        self._update_position_after_buy(
            market=market,
            outcome=outcome,
            new_shares=fill.total_shares,
            cost=fill.total_cost + fill.fee,
            avg_fill_price=fill.avg_price,
        )

        updated_account = self.get_account()
        return TradeResult(trade=trade, account=updated_account)

    def _update_position_after_buy(
        self,
        *,
        market,
        outcome: str,
        new_shares: float,
        cost: float,
        avg_fill_price: float,
    ) -> None:
        """Update or create position after a buy."""
        existing = self.db.get_position(market.condition_id, outcome)
        if existing and existing.shares > 0:
            total_shares = existing.shares + new_shares
            total_cost = existing.total_cost + cost
            avg_entry = total_cost / total_shares if total_shares > 0 else 0.0
        else:
            total_shares = new_shares
            total_cost = cost
            avg_entry = avg_fill_price

        self.db.upsert_position(
            market_condition_id=market.condition_id,
            market_slug=market.slug,
            market_question=market.question,
            outcome=outcome,
            shares=total_shares,
            avg_entry_price=avg_entry,
            total_cost=total_cost,
            realized_pnl=existing.realized_pnl if existing else 0.0,
        )

    # ------------------------------------------------------------------
    # SELL — sell shares, receive USD
    # ------------------------------------------------------------------

    def sell(
        self,
        slug_or_id: str,
        outcome: str,
        shares: float,
        order_type: str = "fok",
    ) -> TradeResult:
        """Execute a sell order: sell shares to receive USD.

        Walks the real order book BID side level-by-level.
        """
        account = self._require_account()

        # Fetch market and validate outcome against actual market outcomes
        market = self.api.get_market(slug_or_id)
        outcome = self._validate_outcome(outcome, market)
        position = self.db.get_position(market.condition_id, outcome)
        if position is None or position.shares <= 0:
            raise NoPositionError(market.slug, outcome)

        if shares > position.shares:
            raise OrderRejectedError(
                f"Cannot sell {shares:.4f} shares, only hold {position.shares:.4f}"
            )
        self._require_market_tradable(market)

        # Fetch live book and fee rate
        token_id = market.get_token_id(outcome)
        book = self.api.get_order_book(token_id)
        fee_rate_bps = self.api.get_fee_rate(token_id)
        best_bid = max((level.price for level in book.bids), default=0.0)
        if best_bid > 0 and shares * best_bid < MIN_ORDER_USD:
            raise OrderRejectedError(
                f"Minimum order size is ${MIN_ORDER_USD:.2f}"
            )

        # Simulate fill against the real order book
        fill = simulate_sell_fill(book, shares, fee_rate_bps, order_type)

        if not fill.filled and not fill.is_partial:
            raise OrderRejectedError(
                "Insufficient liquidity in order book (FOK rejected)"
            )

        # Net proceeds = gross - fee
        net_proceeds = fill.total_cost - fill.fee

        # Update cash
        new_cash = account.cash + net_proceeds
        self.db.update_cash(new_cash)

        # Record trade
        trade = self.db.insert_trade(
            market_condition_id=market.condition_id,
            market_slug=market.slug,
            market_question=market.question,
            outcome=outcome,
            side="sell",
            order_type=order_type,
            avg_price=fill.avg_price,
            amount_usd=fill.total_cost,
            shares=fill.total_shares,
            fee_rate_bps=fee_rate_bps,
            fee=fill.fee,
            slippage=fill.slippage_bps,
            levels_filled=fill.levels_filled,
            is_partial=fill.is_partial,
        )

        # Update position
        self._update_position_after_sell(
            market=market,
            outcome=outcome,
            sold_shares=fill.total_shares,
            proceeds=net_proceeds,
        )

        updated_account = self.get_account()
        return TradeResult(trade=trade, account=updated_account)

    def _update_position_after_sell(
        self,
        *,
        market,
        outcome: str,
        sold_shares: float,
        proceeds: float,
    ) -> None:
        """Update position after a sell."""
        existing = self.db.get_position(market.condition_id, outcome)
        if existing is None:
            return

        remaining_shares = existing.shares - sold_shares
        # Cost basis of sold portion
        cost_of_sold = (
            existing.avg_entry_price * sold_shares
            if existing.shares > 0
            else 0.0
        )
        realized_pnl = existing.realized_pnl + (proceeds - cost_of_sold)
        remaining_cost = existing.total_cost - cost_of_sold

        self.db.upsert_position(
            market_condition_id=market.condition_id,
            market_slug=market.slug,
            market_question=market.question,
            outcome=outcome,
            shares=max(remaining_shares, 0.0),
            avg_entry_price=existing.avg_entry_price,
            total_cost=max(remaining_cost, 0.0),
            realized_pnl=realized_pnl,
        )

    # ------------------------------------------------------------------
    # Portfolio
    # ------------------------------------------------------------------

    def get_portfolio(self) -> list[dict]:
        """Return open positions with live prices and unrealized P&L."""
        self._require_account()
        positions = self.db.get_open_positions()
        result = []
        for pos in positions:
            try:
                token_id = self._get_token_id_for_position(pos)
                live_price = self.api.get_midpoint(token_id)
            except Exception:
                live_price = 0.0

            result.append({
                "market_slug": pos.market_slug,
                "market_question": pos.market_question,
                "outcome": pos.outcome,
                "shares": pos.shares,
                "avg_entry_price": pos.avg_entry_price,
                "total_cost": pos.total_cost,
                "live_price": live_price,
                "current_value": pos.current_value(live_price),
                "unrealized_pnl": pos.unrealized_pnl(live_price),
                "percent_pnl": pos.percent_pnl(live_price),
            })
        return result

    def _get_token_id_for_position(self, pos: Position) -> str:
        """Resolve a position to its token_id for price lookups."""
        market = self.api.get_market(pos.market_slug)
        return market.get_token_id(pos.outcome)

    # ------------------------------------------------------------------
    # Balance
    # ------------------------------------------------------------------

    def get_balance(self) -> dict:
        """Return cash, positions value, and total account value."""
        account = self._require_account()
        portfolio = self.get_portfolio()
        positions_value = sum(p["current_value"] for p in portfolio)
        reserved_cash = self._reserved_buy_notional()
        available_cash = max(0.0, account.cash - reserved_cash)
        return {
            "cash": account.cash,
            "available_cash": available_cash,
            "reserved_cash": reserved_cash,
            "starting_balance": account.starting_balance,
            "positions_value": positions_value,
            "total_value": account.cash + positions_value,
            "pnl": (account.cash + positions_value) - account.starting_balance,
        }

    # ------------------------------------------------------------------
    # Trade history
    # ------------------------------------------------------------------

    def get_history(self, limit: int = 50) -> list[Trade]:
        """Return recent trades."""
        self._require_account()
        return self.db.get_trades(limit)

    # ------------------------------------------------------------------
    # Limit orders (GTC / GTD)
    # ------------------------------------------------------------------

    def place_limit_order(
        self,
        slug_or_id: str,
        outcome: str,
        side: str,
        amount: float,
        limit_price: float,
        order_type: str = "gtc",
        expires_at: str | None = None,
    ) -> dict:
        """Place a GTC or GTD limit order."""
        account = self._require_account()
        if side not in ("buy", "sell"):
            raise OrderRejectedError(f"Invalid side: {side!r}")
        if not (0 < limit_price < 1):
            raise OrderRejectedError(f"Limit price must be between 0 and 1, got {limit_price}")
        if order_type not in ("gtc", "gtd"):
            raise OrderRejectedError(f"Invalid order_type: {order_type!r}. Must be 'gtc' or 'gtd'.")
        if order_type == "gtd" and not expires_at:
            raise OrderRejectedError("GTD orders require expires_at timestamp")
        if side == "buy" and amount < MIN_ORDER_USD:
            raise OrderRejectedError(f"Minimum buy order size is ${MIN_ORDER_USD:.2f}, got ${amount:.2f}")

        market = self.api.get_market(slug_or_id)
        outcome = self._validate_outcome(outcome, market)
        self._require_market_tradable(market)
        token_id = market.get_token_id(outcome)
        tick_size = market.tick_size
        if tick_size <= 0:
            tick_size = self.api.get_tick_size(token_id)
        self._validate_tick_size(limit_price, tick_size)
        if side == "buy":
            try:
                fee_rate_bps = int(float(market.fee_rate_bps))
            except (TypeError, ValueError):
                fee_rate_bps = 0
            if fee_rate_bps <= 0:
                try:
                    fee_rate_bps = int(float(self.api.get_fee_rate(token_id)))
                except Exception:
                    fee_rate_bps = 0
            required = amount + self._estimate_buy_fee_upper_bound(amount, fee_rate_bps)
            available = max(0.0, account.cash - self._reserved_buy_notional())
            if required > available + FILL_EPSILON:
                raise InsufficientBalanceError(required=required, available=available)
        order = create_order(
            self.db.conn,
            market_slug=market.slug,
            market_condition_id=market.condition_id,
            outcome=outcome,
            side=side,
            amount=amount,
            limit_price=limit_price,
            order_type=order_type,
            expires_at=expires_at,
        )
        return _order_to_dict(order)

    def get_pending_orders(self) -> list[dict]:
        """Return all pending limit orders."""
        orders = get_pending_orders(self.db.conn)
        return [_order_to_dict(o) for o in orders]

    def cancel_limit_order(self, order_id: int) -> dict | None:
        """Cancel a pending limit order."""
        order = cancel_order(self.db.conn, order_id)
        if order is None:
            return None
        return _order_to_dict(order)

    def cancel_all_orders(self) -> list[dict]:
        """Cancel all pending limit orders. Returns list of cancelled orders."""
        cancelled = _cancel_all_orders(self.db.conn)
        return [_order_to_dict(o) for o in cancelled]

    def check_orders(self) -> list[dict]:
        """Check all pending orders against live prices and execute fills.

        This is the agent-callable trigger. Call it periodically.
        Returns list of filled/expired orders.

        Limit price enforcement: buy orders only consume ask levels at or
        below the limit price; sell orders only consume bid levels at or
        above the limit price.  This guarantees no "price-through" fills.
        """
        self._require_account()
        results = []

        # First expire any GTD orders past their deadline
        expired = expire_orders(self.db.conn)
        for o in expired:
            results.append({"order": _order_to_dict(o), "action": "expired"})

        # Check pending orders against live order books
        pending = get_pending_orders(self.db.conn)
        for order in pending:
            try:
                market = self.api.get_market(order.market_slug)
                self._require_market_tradable(market)
                token_id = market.get_token_id(order.outcome)
                book = self.api.get_order_book(token_id)
                fee_rate_bps = self.api.get_fee_rate(token_id)

                if order.side == "buy":
                    # Only fill at ask levels <= limit_price
                    best_ask = min((l.price for l in book.asks), default=None)
                    if best_ask is None or best_ask > order.limit_price:
                        continue
                    fill = simulate_buy_fill(
                        book, order.remaining_amount, fee_rate_bps, "fak",
                        max_price=order.limit_price,
                    )
                else:
                    # Only fill at bid levels >= limit_price
                    best_bid = max((l.price for l in book.bids), default=None)
                    if best_bid is None or best_bid < order.limit_price:
                        continue
                    fill = simulate_sell_fill(
                        book, order.remaining_amount, fee_rate_bps, "fak",
                        min_price=order.limit_price,
                    )

                if not fill.filled and not fill.is_partial:
                    continue  # No fillable liquidity within limit

                # Execute the fill through normal trade recording
                if order.side == "buy":
                    self._execute_limit_buy(market, order, fill, fee_rate_bps)
                else:
                    self._execute_limit_sell(market, order, fill, fee_rate_bps)

                remaining = (
                    order.remaining_amount - fill.total_cost
                    if order.side == "buy"
                    else order.remaining_amount - fill.total_shares
                )
                if remaining <= FILL_EPSILON:
                    updated = mark_filled(self.db.conn, order.id)
                    results.append({
                        "order": _order_to_dict(updated),
                        "action": "filled",
                    })
                else:
                    updated = mark_partially_filled(self.db.conn, order.id, remaining)
                    results.append({
                        "order": _order_to_dict(updated),
                        "action": "partially_filled",
                    })
            except _PERMANENT_ORDER_ERRORS as e:
                # Permanent failure — mark rejected so it's not retried
                updated = reject_order(self.db.conn, order.id)
                results.append({
                    "order": _order_to_dict(updated),
                    "action": "rejected",
                    "reason": str(e),
                })
            except Exception:
                continue  # Transient errors (network, API) — retry next check

        return results

    def _execute_limit_buy(self, market, order, fill, fee_rate_bps: int) -> None:
        """Record a limit buy fill using a pre-computed FillResult."""
        account = self._require_account()
        total_outflow = fill.total_cost + fill.fee
        if total_outflow > account.cash:
            raise InsufficientBalanceError(
                required=total_outflow, available=account.cash,
            )
        self.db.update_cash(account.cash - total_outflow)
        self.db.insert_trade(
            market_condition_id=market.condition_id,
            market_slug=market.slug,
            market_question=market.question,
            outcome=order.outcome,
            side="buy",
            order_type="fak",
            avg_price=fill.avg_price,
            amount_usd=fill.total_cost,
            shares=fill.total_shares,
            fee_rate_bps=fee_rate_bps,
            fee=fill.fee,
            slippage=fill.slippage_bps,
            levels_filled=fill.levels_filled,
            is_partial=fill.is_partial,
        )
        self._update_position_after_buy(
            market=market,
            outcome=order.outcome,
            new_shares=fill.total_shares,
            cost=fill.total_cost + fill.fee,
            avg_fill_price=fill.avg_price,
        )

    def _execute_limit_sell(self, market, order, fill, fee_rate_bps: int) -> None:
        """Record a limit sell fill using a pre-computed FillResult."""
        account = self._require_account()
        position = self.db.get_position(market.condition_id, order.outcome)
        if position is None or position.shares <= 0:
            raise NoPositionError(market.slug, order.outcome)
        if fill.total_shares > position.shares:
            raise OrderRejectedError(
                f"Cannot sell {fill.total_shares:.4f} shares, "
                f"only hold {position.shares:.4f}"
            )
        net_proceeds = fill.total_cost - fill.fee
        self.db.update_cash(account.cash + net_proceeds)
        self.db.insert_trade(
            market_condition_id=market.condition_id,
            market_slug=market.slug,
            market_question=market.question,
            outcome=order.outcome,
            side="sell",
            order_type="fak",
            avg_price=fill.avg_price,
            amount_usd=fill.total_cost,
            shares=fill.total_shares,
            fee_rate_bps=fee_rate_bps,
            fee=fill.fee,
            slippage=fill.slippage_bps,
            levels_filled=fill.levels_filled,
            is_partial=fill.is_partial,
        )
        self._update_position_after_sell(
            market=market,
            outcome=order.outcome,
            sold_shares=fill.total_shares,
            proceeds=net_proceeds,
        )

    def watch_prices(
        self, slugs_or_ids: list[str], outcomes: list[str] | None = None,
    ) -> list[dict]:
        """Fetch live midpoint prices for given markets.

        Agent calls this to monitor prices before deciding to trade.
        """
        results = []
        if outcomes is None:
            outcomes = ["yes"]
        for slug in slugs_or_ids:
            try:
                market = self.api.get_market(slug)
            except Exception:
                continue  # Market not found or API error
            for outcome in outcomes:
                outcome = outcome.lower()
                token_id = market.get_token_id(outcome)  # raises ValueError for invalid
                try:
                    mid = self.api.get_midpoint(token_id)
                except Exception:
                    continue  # API error fetching price
                results.append({
                    "market_slug": market.slug,
                    "outcome": outcome,
                    "midpoint": mid,
                    "condition_id": market.condition_id,
                })
        return results

    # ------------------------------------------------------------------
    # Resolution
    # ------------------------------------------------------------------

    def resolve_market(self, slug_or_id: str) -> list[ResolveResult]:
        """Resolve a market's positions, paying out $1/share for winner."""
        account = self._require_account()
        market = self.api.get_market(slug_or_id)

        if not market.closed:
            raise MarketClosedError(
                f"{market.slug} is not yet closed/resolved"
            )

        positions = self.db.get_positions_for_market(market.condition_id)
        if not positions:
            raise NoPositionError(market.slug, "any")

        winning_outcome = _determine_winner(market)

        results = []
        for pos in positions:
            if pos.is_resolved or pos.shares <= 0:
                continue

            if pos.outcome == winning_outcome:
                payout = pos.shares * 1.0
            else:
                payout = 0.0

            resolved_pos = self.db.resolve_position(
                market.condition_id, pos.outcome, payout
            )

            # Add payout to cash
            account = self.get_account()
            new_cash = account.cash + payout
            self.db.update_cash(new_cash)
            account = self.get_account()

            results.append(ResolveResult(
                position=resolved_pos,
                payout=payout,
                account=account,
            ))

        return results

    def resolve_all(self) -> list[ResolveResult]:
        """Resolve all open positions in closed markets.

        Skips markets that fail due to transient API/network errors.
        Raises on permanent resolution failures (e.g. ambiguous outcomes).
        """
        self._require_account()
        positions = self.db.get_open_positions()
        all_results = []

        seen_markets: set[str] = set()
        for pos in positions:
            if pos.market_condition_id in seen_markets:
                continue
            try:
                market = self.api.get_market(pos.market_slug)
                if market.closed:
                    seen_markets.add(pos.market_condition_id)
                    results = self.resolve_market(pos.market_slug)
                    all_results.extend(results)
            except (ApiError, ConnectionError, TimeoutError, OSError):
                continue  # Transient — retry on next call

        return all_results


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _determine_winner(market) -> str:
    """Determine the winning outcome from a resolved market's prices.

    Raises SimError if no outcome has price >= 0.99, preventing silent
    zero-payout on ambiguous or partially-resolved markets.
    """
    for i, outcome in enumerate(market.outcomes):
        price = market.outcome_prices[i] if i < len(market.outcome_prices) else 0.0
        if price >= 0.99:
            return outcome.lower()
    prices = dict(zip(market.outcomes, market.outcome_prices))
    raise AmbiguousResolutionError(market.slug, prices)


def _order_to_dict(order) -> dict:
    """Convert a LimitOrder to a JSON-safe dict."""
    return {
        "id": order.id,
        "market_slug": order.market_slug,
        "market_condition_id": order.market_condition_id,
        "outcome": order.outcome,
        "side": order.side,
        "amount": order.amount,
        "remaining_amount": order.remaining_amount,
        "limit_price": order.limit_price,
        "order_type": order.order_type,
        "expires_at": order.expires_at,
        "status": order.status,
        "created_at": order.created_at,
        "filled_at": order.filled_at,
    }
