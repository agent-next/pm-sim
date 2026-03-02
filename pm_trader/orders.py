"""Limit order management for pm-trader.

GTC (Good-Til-Cancelled): rests until price target is hit or manually cancelled.
GTD (Good-Til-Date): GTC with an expiry timestamp.

Orders are stored in SQLite and checked against live midpoint prices
when the agent calls `pm-trader orders check`.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone


def _normalize_timestamp(ts: str) -> str:
    """Normalize an ISO timestamp to a consistent format for TEXT comparison.

    Replaces 'Z' suffix with '+00:00' and ensures the string sorts correctly
    as TEXT in SQLite.
    """
    if ts.endswith("Z"):
        ts = ts[:-1] + "+00:00"
    return ts


@dataclass
class LimitOrder:
    """A pending limit order."""

    id: int
    market_slug: str
    market_condition_id: str
    outcome: str
    side: str  # "buy" or "sell"
    amount: float  # USD for buy, shares for sell
    limit_price: float
    order_type: str  # "gtc" or "gtd"
    expires_at: str | None  # ISO timestamp for GTD, None for GTC
    status: str  # "pending", "filled", "cancelled", "expired"
    created_at: str
    filled_at: str | None = None


ORDERS_SCHEMA = """\
CREATE TABLE IF NOT EXISTS limit_orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    market_slug TEXT NOT NULL,
    market_condition_id TEXT NOT NULL,
    outcome TEXT NOT NULL CHECK (length(outcome) > 0),
    side TEXT NOT NULL CHECK (side IN ('buy', 'sell')),
    amount REAL NOT NULL,
    limit_price REAL NOT NULL,
    order_type TEXT NOT NULL CHECK (order_type IN ('gtc', 'gtd')),
    expires_at TEXT,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'filled', 'cancelled', 'expired', 'rejected')),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    filled_at TEXT
);
"""


def init_orders_schema(conn: sqlite3.Connection) -> None:
    """Create the limit_orders table if it doesn't exist."""
    conn.executescript(ORDERS_SCHEMA)


def create_order(
    conn: sqlite3.Connection,
    *,
    market_slug: str,
    market_condition_id: str,
    outcome: str,
    side: str,
    amount: float,
    limit_price: float,
    order_type: str = "gtc",
    expires_at: str | None = None,
) -> LimitOrder:
    """Create a new pending limit order."""
    normalized_expires = _normalize_timestamp(expires_at) if expires_at else None
    cursor = conn.execute(
        """\
        INSERT INTO limit_orders (
            market_slug, market_condition_id, outcome, side,
            amount, limit_price, order_type, expires_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (market_slug, market_condition_id, outcome, side,
         amount, limit_price, order_type, normalized_expires),
    )
    conn.commit()
    return _get_order(conn, cursor.lastrowid)


def get_pending_orders(conn: sqlite3.Connection) -> list[LimitOrder]:
    """Return all pending limit orders."""
    rows = conn.execute(
        "SELECT * FROM limit_orders WHERE status = 'pending' ORDER BY id"
    ).fetchall()
    return [_row_to_order(r) for r in rows]


def get_order(conn: sqlite3.Connection, order_id: int) -> LimitOrder | None:
    """Return a specific order, or None."""
    return _get_order(conn, order_id)


def cancel_order(conn: sqlite3.Connection, order_id: int) -> LimitOrder | None:
    """Cancel a pending order. Returns the updated order or None if not found."""
    order = _get_order(conn, order_id)
    if order is None or order.status != "pending":
        return None
    conn.execute(
        "UPDATE limit_orders SET status = 'cancelled' WHERE id = ?",
        (order_id,),
    )
    conn.commit()
    return _get_order(conn, order_id)


def cancel_all_orders(conn: sqlite3.Connection) -> list[LimitOrder]:
    """Cancel all pending orders. Returns list of cancelled orders."""
    pending = get_pending_orders(conn)
    if not pending:
        return []
    ids = [o.id for o in pending]
    conn.execute(
        "UPDATE limit_orders SET status = 'cancelled' WHERE status = 'pending'"
    )
    conn.commit()
    placeholders = ",".join("?" * len(ids))
    rows = conn.execute(
        f"SELECT * FROM limit_orders WHERE id IN ({placeholders})", ids
    ).fetchall()
    return [_row_to_order(row) for row in rows]


def mark_filled(conn: sqlite3.Connection, order_id: int) -> LimitOrder:
    """Mark an order as filled."""
    conn.execute(
        "UPDATE limit_orders SET status = 'filled', filled_at = datetime('now') WHERE id = ?",
        (order_id,),
    )
    conn.commit()
    return _get_order(conn, order_id)


def reject_order(conn: sqlite3.Connection, order_id: int) -> LimitOrder:
    """Mark an order as permanently rejected (unfillable)."""
    conn.execute(
        "UPDATE limit_orders SET status = 'rejected' WHERE id = ?",
        (order_id,),
    )
    conn.commit()
    return _get_order(conn, order_id)


def expire_orders(conn: sqlite3.Connection) -> list[LimitOrder]:
    """Expire all GTD orders past their expires_at. Returns expired orders."""
    now = _normalize_timestamp(datetime.now(timezone.utc).isoformat())
    rows = conn.execute(
        """\
        SELECT * FROM limit_orders
        WHERE status = 'pending' AND order_type = 'gtd' AND expires_at <= ?
        """,
        (now,),
    ).fetchall()

    if rows:
        conn.execute(
            """\
            UPDATE limit_orders SET status = 'expired'
            WHERE status = 'pending' AND order_type = 'gtd' AND expires_at <= ?
            """,
            (now,),
        )
        conn.commit()

    return [_row_to_order(r) for r in rows]


def should_fill(order: LimitOrder, best_price: float) -> bool:
    """Check if a limit order should be filled at the given best price.

    Buy limit: fill when best_ask <= limit_price (can buy at or below target)
    Sell limit: fill when best_bid >= limit_price (can sell at or above target)
    """
    if order.side == "buy":
        return best_price <= order.limit_price
    else:  # sell
        return best_price >= order.limit_price


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _get_order(conn: sqlite3.Connection, order_id: int) -> LimitOrder | None:
    row = conn.execute(
        "SELECT * FROM limit_orders WHERE id = ?", (order_id,)
    ).fetchone()
    if row is None:
        return None
    return _row_to_order(row)


def _row_to_order(row: sqlite3.Row) -> LimitOrder:
    return LimitOrder(
        id=row["id"],
        market_slug=row["market_slug"],
        market_condition_id=row["market_condition_id"],
        outcome=row["outcome"],
        side=row["side"],
        amount=row["amount"],
        limit_price=row["limit_price"],
        order_type=row["order_type"],
        expires_at=row["expires_at"],
        status=row["status"],
        created_at=row["created_at"],
        filled_at=row["filled_at"],
    )
