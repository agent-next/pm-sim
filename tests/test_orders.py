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
    get_reserved_buy_notional,
    init_orders_schema,
    _migrate_orders_schema_if_needed,
    mark_partially_filled,
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
        assert order.remaining_amount == 100.0
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

    def test_includes_partially_filled(self, conn):
        _create(conn, amount=120.0)
        mark_partially_filled(conn, 1, 40.0)
        pending = get_pending_orders(conn)
        assert len(pending) == 1
        assert pending[0].status == "partially_filled"
        assert pending[0].remaining_amount == 40.0


class TestReservedBuyNotional:
    def test_empty_is_zero(self, conn):
        assert get_reserved_buy_notional(conn) == 0.0

    def test_sums_only_open_buy_remaining_amount(self, conn):
        _create(conn, side="buy", amount=100.0)   # id=1
        _create(conn, side="buy", amount=50.0)    # id=2
        _create(conn, side="sell", amount=70.0)   # id=3
        mark_partially_filled(conn, 2, 20.0)
        cancel_order(conn, 1)
        assert get_reserved_buy_notional(conn) == pytest.approx(20.0)

    def test_handles_none_row_from_driver(self):
        class _Cursor:
            def fetchone(self):
                return None

        class _Conn:
            def execute(self, *_args, **_kwargs):
                return _Cursor()

        assert get_reserved_buy_notional(_Conn()) == 0.0


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

    def test_cancel_partially_filled(self, conn):
        _create(conn, amount=120.0)
        mark_partially_filled(conn, 1, 40.0)
        cancelled = cancel_order(conn, 1)
        assert cancelled is not None
        assert cancelled.status == "cancelled"


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
            remaining_amount=100,
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
            remaining_amount=50,
            order_type="gtc", expires_at=None, status="pending",
            created_at="", filled_at=None,
        )
        assert should_fill(order, 0.70) is True
        assert should_fill(order, 0.80) is True
        assert should_fill(order, 0.60) is False


class TestOrdersMigration:
    def test_noop_when_table_missing(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        _migrate_orders_schema_if_needed(conn)

    def test_migrates_legacy_schema_to_remaining_amount(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(
            """\
            CREATE TABLE limit_orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                market_slug TEXT NOT NULL,
                market_condition_id TEXT NOT NULL,
                outcome TEXT NOT NULL,
                side TEXT NOT NULL,
                amount REAL NOT NULL,
                limit_price REAL NOT NULL,
                order_type TEXT NOT NULL DEFAULT 'gtc',
                expires_at TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                filled_at TEXT
            );
            INSERT INTO limit_orders (
                market_slug, market_condition_id, outcome, side, amount,
                limit_price, order_type, status
            ) VALUES
                ('m1', '0x1', 'yes', 'buy', 100.0, 0.55, 'gtc', 'pending'),
                ('m2', '0x2', 'no', 'sell', 50.0, 0.60, 'gtc', 'filled');
            """
        )

        _migrate_orders_schema_if_needed(conn)

        migrated = conn.execute(
            "SELECT amount, remaining_amount, status FROM limit_orders ORDER BY id"
        ).fetchall()
        assert migrated[0]["status"] == "pending"
        assert migrated[0]["remaining_amount"] == pytest.approx(migrated[0]["amount"])
        assert migrated[1]["status"] == "filled"
        assert migrated[1]["remaining_amount"] == pytest.approx(0.0)
