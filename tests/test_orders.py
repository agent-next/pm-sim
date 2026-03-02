"""Tests for limit order management."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from pm_trader.orders import (
    cancel_all_orders,
    cancel_order,
    create_order,
    expire_orders,
    get_pending_orders,
    init_orders_schema,
    should_fill,
    LimitOrder,
)


@pytest.fixture
def conn():
    """In-memory SQLite connection with orders schema."""
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    init_orders_schema(c)
    return c


def _create(conn, **overrides):
    defaults = dict(
        market_slug="test-market",
        market_condition_id="0xabc",
        outcome="yes",
        side="buy",
        amount=100.0,
        limit_price=0.55,
        order_type="gtc",
        expires_at=None,
    )
    defaults.update(overrides)
    return create_order(conn, **defaults)


class TestCreateOrder:
    def test_creates_pending_order(self, conn):
        order = _create(conn)
        assert order.id == 1
        assert order.status == "pending"
        assert order.market_slug == "test-market"
        assert order.limit_price == 0.55
        assert order.order_type == "gtc"

    def test_auto_increments_id(self, conn):
        o1 = _create(conn)
        o2 = _create(conn)
        assert o2.id == o1.id + 1

    def test_gtd_with_expiry(self, conn):
        expires = "2026-03-01T00:00:00Z"
        order = _create(conn, order_type="gtd", expires_at=expires)
        assert order.order_type == "gtd"
        # Z is normalized to +00:00 for consistent TEXT comparison
        assert order.expires_at == "2026-03-01T00:00:00+00:00"

    def test_gtd_z_and_plus00_are_equivalent(self, conn):
        """Bug #5: 'Z' and '+00:00' must be treated as the same instant."""
        from pm_trader.orders import expire_orders
        from datetime import datetime, timezone
        # Create an order with Z-suffix that has already expired
        order = _create(
            conn, order_type="gtd", expires_at="2020-01-01T00:00:00Z",
        )
        expired = expire_orders(conn)
        assert len(expired) == 1
        assert expired[0].id == order.id


class TestGetPendingOrders:
    def test_empty(self, conn):
        assert get_pending_orders(conn) == []

    def test_returns_pending_only(self, conn):
        _create(conn)
        _create(conn)
        cancel_order(conn, 1)
        pending = get_pending_orders(conn)
        assert len(pending) == 1
        assert pending[0].id == 2


class TestCancelOrder:
    def test_cancel_pending(self, conn):
        _create(conn)
        order = cancel_order(conn, 1)
        assert order.status == "cancelled"

    def test_cancel_nonexistent(self, conn):
        assert cancel_order(conn, 999) is None

    def test_cancel_already_cancelled(self, conn):
        _create(conn)
        cancel_order(conn, 1)
        assert cancel_order(conn, 1) is None


class TestExpireOrders:
    def test_expires_past_gtd(self, conn):
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        _create(conn, order_type="gtd", expires_at=past)
        expired = expire_orders(conn)
        assert len(expired) == 1

    def test_does_not_expire_future_gtd(self, conn):
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        _create(conn, order_type="gtd", expires_at=future)
        expired = expire_orders(conn)
        assert len(expired) == 0

    def test_does_not_expire_gtc(self, conn):
        _create(conn, order_type="gtc")
        expired = expire_orders(conn)
        assert len(expired) == 0


class TestCancelAllOrders:
    def test_cancel_all_empty(self, conn):
        result = cancel_all_orders(conn)
        assert result == []

    def test_cancel_all_cancels_pending(self, conn):
        _create(conn)
        _create(conn)
        _create(conn)
        cancelled = cancel_all_orders(conn)
        assert len(cancelled) == 3
        assert all(o.status == "cancelled" for o in cancelled)
        pending = get_pending_orders(conn)
        assert len(pending) == 0

    def test_cancel_all_skips_non_pending(self, conn):
        _create(conn)
        _create(conn)
        cancel_order(conn, 1)  # manually cancel #1
        cancelled = cancel_all_orders(conn)
        assert len(cancelled) == 1  # only #2 was pending
        assert cancelled[0].id == 2
        assert cancelled[0].status == "cancelled"


class TestShouldFill:
    def test_buy_at_limit(self):
        order = LimitOrder(
            id=1, market_slug="m", market_condition_id="0x1",
            outcome="yes", side="buy", amount=100, limit_price=0.55,
            order_type="gtc", expires_at=None, status="pending",
            created_at="", filled_at=None,
        )
        assert should_fill(order, 0.55) is True
        assert should_fill(order, 0.50) is True
        assert should_fill(order, 0.60) is False

    def test_sell_at_limit(self):
        order = LimitOrder(
            id=1, market_slug="m", market_condition_id="0x1",
            outcome="yes", side="sell", amount=50, limit_price=0.70,
            order_type="gtc", expires_at=None, status="pending",
            created_at="", filled_at=None,
        )
        assert should_fill(order, 0.70) is True
        assert should_fill(order, 0.80) is True
        assert should_fill(order, 0.60) is False
