# pm-sim Phase 1 MVP Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a working CLI paper trading simulator for Polymarket that AI agents can call via shell commands and receive JSON responses.

**Architecture:** Click CLI → engine (pure logic) → api (HTTP to Polymarket) + db (SQLite). All output wrapped in `{"ok": true/false, ...}` envelope. Market data cached 5min, prices always live.

**Tech Stack:** Python 3.10+, Click (CLI), httpx (HTTP), sqlite3 (stdlib), pytest + pytest-httpx (testing)

**Design doc:** `docs/plans/2026-02-28-pm-sim-design.md`

---

### Task 1: Project Scaffolding

**Files:**
- Create: `pyproject.toml`
- Create: `pm_sim/__init__.py`
- Create: `pm_sim/models.py`
- Create: `tests/__init__.py`
- Create: `tests/conftest.py`

**Step 1: Create pyproject.toml**

```toml
[build-system]
requires = ["setuptools>=68.0"]
build-backend = "setuptools.backends._legacy:_Backend"

[project]
name = "pm-sim"
version = "0.1.0"
description = "Polymarket paper trading simulator for AI agents"
requires-python = ">=3.10"
dependencies = [
    "click>=8.0",
    "httpx>=0.27",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-httpx>=0.30",
]

[project.scripts]
pm-sim = "pm_sim.cli:main"
```

**Step 2: Create pm_sim/__init__.py**

```python
"""pm-sim: Polymarket paper trading simulator for AI agents."""
```

**Step 3: Create pm_sim/models.py with all dataclasses**

```python
"""Data models for pm-sim."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Market:
    """A Polymarket market."""
    condition_id: str
    slug: str
    question: str
    outcomes: list[str]
    outcome_prices: list[float]
    token_ids: list[str]
    active: bool
    closed: bool
    volume: float = 0.0
    liquidity: float = 0.0
    description: str = ""
    end_date: str = ""

    @property
    def yes_token_id(self) -> str:
        return self.token_ids[0] if self.token_ids else ""

    @property
    def no_token_id(self) -> str:
        return self.token_ids[1] if len(self.token_ids) > 1 else ""

    @property
    def yes_price(self) -> float:
        return self.outcome_prices[0] if self.outcome_prices else 0.0

    @property
    def no_price(self) -> float:
        return self.outcome_prices[1] if len(self.outcome_prices) > 1 else 0.0


@dataclass
class OrderBookLevel:
    """A single level in an order book."""
    price: float
    size: float


@dataclass
class OrderBook:
    """Order book for a market outcome."""
    bids: list[OrderBookLevel] = field(default_factory=list)
    asks: list[OrderBookLevel] = field(default_factory=list)


@dataclass
class Trade:
    """A recorded paper trade."""
    id: int
    market_condition_id: str
    market_slug: str
    market_question: str
    outcome: str
    side: str
    price: float
    amount_usd: float
    shares: float
    fee: float
    created_at: str


@dataclass
class Position:
    """A current holding."""
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
    # Computed at query time (not stored):
    current_price: float = 0.0
    current_value: float = 0.0
    unrealized_pnl: float = 0.0
    percent_pnl: float = 0.0


@dataclass
class Account:
    """Paper trading account state."""
    starting_balance: float
    cash: float
    fee_bps: int
    created_at: str


@dataclass
class TradeResult:
    """Result of executing a paper trade."""
    trade_id: int
    market_slug: str
    outcome: str
    side: str
    price: float
    shares: float
    amount_usd: float
    fee: float
    cash_remaining: float


@dataclass
class ResolveResult:
    """Result of resolving a market position."""
    market_slug: str
    market_question: str
    winning_outcome: str
    positions_settled: list[dict]
    total_payout: float
    total_realized_pnl: float


class SimError(Exception):
    """Base error for pm-sim with error code."""
    def __init__(self, message: str, code: str):
        super().__init__(message)
        self.code = code


class NotInitializedError(SimError):
    def __init__(self):
        super().__init__("Account not initialized. Run: pm-sim init", "NOT_INITIALIZED")


class InsufficientBalanceError(SimError):
    def __init__(self, need: float, have: float):
        super().__init__(f"Need ${need:.2f} but only have ${have:.2f}", "INSUFFICIENT_BALANCE")


class MarketNotFoundError(SimError):
    def __init__(self, identifier: str):
        super().__init__(f"Market not found: {identifier}", "MARKET_NOT_FOUND")


class MarketClosedError(SimError):
    def __init__(self, slug: str):
        super().__init__(f"Market is closed: {slug}", "MARKET_CLOSED")


class NoPositionError(SimError):
    def __init__(self, slug: str, outcome: str):
        super().__init__(f"No {outcome} position in {slug}", "NO_POSITION")


class InvalidOutcomeError(SimError):
    def __init__(self, outcome: str):
        super().__init__(f"Invalid outcome '{outcome}'. Must be 'yes' or 'no'", "INVALID_OUTCOME")


class ApiError(SimError):
    def __init__(self, detail: str):
        super().__init__(f"Polymarket API error: {detail}", "API_ERROR")
```

**Step 4: Create tests/__init__.py and tests/conftest.py**

`tests/__init__.py` — empty file.

`tests/conftest.py`:

```python
"""Shared test fixtures for pm-sim."""

import os
import tempfile

import pytest

from pm_sim.models import Market


@pytest.fixture
def tmp_data_dir(tmp_path):
    """Provide a temporary directory for SQLite database."""
    return str(tmp_path)


@pytest.fixture
def sample_market() -> Market:
    """A realistic sample market for testing."""
    return Market(
        condition_id="0xabc123",
        slug="bitcoin-above-100k",
        question="Will Bitcoin be above $100k on Dec 31?",
        outcomes=["Yes", "No"],
        outcome_prices=[0.65, 0.35],
        token_ids=["TOKEN_YES_123", "TOKEN_NO_456"],
        active=True,
        closed=False,
        volume=5000000.0,
        liquidity=250000.0,
        description="Resolves based on CoinGecko price",
        end_date="2026-12-31T23:59:59Z",
    )


@pytest.fixture
def closed_market() -> Market:
    """A resolved market where YES won."""
    return Market(
        condition_id="0xdef789",
        slug="trump-wins-2024",
        question="Will Trump win the 2024 election?",
        outcomes=["Yes", "No"],
        outcome_prices=[1.0, 0.0],
        token_ids=["TOKEN_YES_789", "TOKEN_NO_012"],
        active=False,
        closed=True,
        volume=145000000.0,
        liquidity=0.0,
    )
```

**Step 5: Install in dev mode and verify**

Run: `cd /Users/robert/workspace/polymarket && pip install -e ".[dev]"`
Expected: Successful install with click, httpx, pytest, pytest-httpx

**Step 6: Run pytest to confirm test infrastructure works**

Run: `cd /Users/robert/workspace/polymarket && python -m pytest tests/ -v --co`
Expected: "no tests ran" (collection only, no errors)

**Step 7: Commit**

```bash
git add pyproject.toml pm_sim/__init__.py pm_sim/models.py tests/__init__.py tests/conftest.py
git commit -m "feat: project scaffolding with models and test fixtures"
```

---

### Task 2: Database Layer (db.py)

**Files:**
- Create: `pm_sim/db.py`
- Create: `tests/test_db.py`

**Step 1: Write failing tests for db.py**

```python
"""Tests for pm_sim.db."""

import sqlite3

import pytest

from pm_sim.db import Database
from pm_sim.models import NotInitializedError


class TestDatabaseInit:
    def test_creates_db_file(self, tmp_data_dir):
        db = Database(tmp_data_dir)
        db.init_schema()
        import os
        assert os.path.exists(os.path.join(tmp_data_dir, "paper.db"))

    def test_init_account(self, tmp_data_dir):
        db = Database(tmp_data_dir)
        db.init_schema()
        db.init_account(balance=5000.0, fee_bps=10)
        acct = db.get_account()
        assert acct.starting_balance == 5000.0
        assert acct.cash == 5000.0
        assert acct.fee_bps == 10

    def test_init_account_defaults(self, tmp_data_dir):
        db = Database(tmp_data_dir)
        db.init_schema()
        db.init_account()
        acct = db.get_account()
        assert acct.starting_balance == 10000.0
        assert acct.fee_bps == 0

    def test_get_account_not_initialized(self, tmp_data_dir):
        db = Database(tmp_data_dir)
        db.init_schema()
        with pytest.raises(NotInitializedError):
            db.get_account()

    def test_reset_clears_all(self, tmp_data_dir):
        db = Database(tmp_data_dir)
        db.init_schema()
        db.init_account(balance=5000.0)
        db.reset()
        with pytest.raises(NotInitializedError):
            db.get_account()


class TestTrades:
    def test_insert_trade(self, tmp_data_dir):
        db = Database(tmp_data_dir)
        db.init_schema()
        db.init_account()
        trade_id = db.insert_trade(
            market_condition_id="0xabc",
            market_slug="test-market",
            market_question="Test?",
            outcome="yes",
            side="buy",
            price=0.65,
            amount_usd=100.0,
            shares=153.85,
            fee=0.0,
        )
        assert trade_id == 1

    def test_get_trades(self, tmp_data_dir):
        db = Database(tmp_data_dir)
        db.init_schema()
        db.init_account()
        db.insert_trade("0xabc", "test", "Test?", "yes", "buy", 0.65, 100.0, 153.85, 0.0)
        db.insert_trade("0xabc", "test", "Test?", "no", "buy", 0.35, 50.0, 142.86, 0.0)
        trades = db.get_trades(limit=10)
        assert len(trades) == 2
        assert trades[0].side == "buy"


class TestPositions:
    def test_upsert_position_new(self, tmp_data_dir):
        db = Database(tmp_data_dir)
        db.init_schema()
        db.upsert_position("0xabc", "test", "Test?", "yes", shares_delta=100.0, cost_delta=65.0)
        pos = db.get_position("0xabc", "yes")
        assert pos is not None
        assert pos.shares == 100.0
        assert pos.avg_entry_price == 0.65
        assert pos.total_cost == 65.0

    def test_upsert_position_add(self, tmp_data_dir):
        db = Database(tmp_data_dir)
        db.init_schema()
        db.upsert_position("0xabc", "test", "Test?", "yes", shares_delta=100.0, cost_delta=65.0)
        db.upsert_position("0xabc", "test", "Test?", "yes", shares_delta=50.0, cost_delta=35.0)
        pos = db.get_position("0xabc", "yes")
        assert pos.shares == 150.0
        assert pos.total_cost == 100.0
        assert pos.avg_entry_price == pytest.approx(100.0 / 150.0, abs=0.001)

    def test_upsert_position_sell(self, tmp_data_dir):
        db = Database(tmp_data_dir)
        db.init_schema()
        db.upsert_position("0xabc", "test", "Test?", "yes", shares_delta=100.0, cost_delta=65.0)
        db.upsert_position("0xabc", "test", "Test?", "yes", shares_delta=-50.0, cost_delta=-32.5)
        pos = db.get_position("0xabc", "yes")
        assert pos.shares == 50.0
        assert pos.total_cost == 32.5

    def test_get_open_positions(self, tmp_data_dir):
        db = Database(tmp_data_dir)
        db.init_schema()
        db.upsert_position("0xabc", "test1", "Test1?", "yes", shares_delta=100.0, cost_delta=65.0)
        db.upsert_position("0xdef", "test2", "Test2?", "no", shares_delta=50.0, cost_delta=25.0)
        positions = db.get_open_positions()
        assert len(positions) == 2

    def test_resolve_position(self, tmp_data_dir):
        db = Database(tmp_data_dir)
        db.init_schema()
        db.upsert_position("0xabc", "test", "Test?", "yes", shares_delta=100.0, cost_delta=65.0)
        db.resolve_position("0xabc", "yes", payout=100.0)
        pos = db.get_position("0xabc", "yes")
        assert pos.is_resolved is True
        assert pos.realized_pnl == 35.0  # payout - cost

    def test_get_position_returns_none(self, tmp_data_dir):
        db = Database(tmp_data_dir)
        db.init_schema()
        pos = db.get_position("0xnonexistent", "yes")
        assert pos is None


class TestCache:
    def test_set_and_get_cache(self, tmp_data_dir):
        db = Database(tmp_data_dir)
        db.init_schema()
        db.set_cache("markets:list:10", '{"data": []}')
        result = db.get_cache("markets:list:10", max_age_seconds=300)
        assert result == '{"data": []}'

    def test_cache_expired(self, tmp_data_dir):
        db = Database(tmp_data_dir)
        db.init_schema()
        db.set_cache("old-key", '{"old": true}')
        # Expire by using max_age=0
        result = db.get_cache("old-key", max_age_seconds=0)
        assert result is None

    def test_cache_miss(self, tmp_data_dir):
        db = Database(tmp_data_dir)
        db.init_schema()
        result = db.get_cache("nonexistent", max_age_seconds=300)
        assert result is None


class TestCashUpdate:
    def test_update_cash(self, tmp_data_dir):
        db = Database(tmp_data_dir)
        db.init_schema()
        db.init_account(balance=10000.0)
        db.update_cash(-100.0)
        acct = db.get_account()
        assert acct.cash == 9900.0

    def test_update_cash_add(self, tmp_data_dir):
        db = Database(tmp_data_dir)
        db.init_schema()
        db.init_account(balance=10000.0)
        db.update_cash(-100.0)
        db.update_cash(50.0)
        acct = db.get_account()
        assert acct.cash == 9950.0
```

**Step 2: Run tests to verify they fail**

Run: `cd /Users/robert/workspace/polymarket && python -m pytest tests/test_db.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pm_sim.db'`

**Step 3: Implement pm_sim/db.py**

```python
"""SQLite database operations for pm-sim."""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone

from pm_sim.models import Account, NotInitializedError, Position, Trade

SCHEMA = """
CREATE TABLE IF NOT EXISTS account (
    id INTEGER PRIMARY KEY DEFAULT 1,
    starting_balance REAL NOT NULL DEFAULT 10000,
    cash REAL NOT NULL DEFAULT 10000,
    fee_bps INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    CHECK (id = 1)
);

CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    market_condition_id TEXT NOT NULL,
    market_slug TEXT NOT NULL,
    market_question TEXT NOT NULL,
    outcome TEXT NOT NULL CHECK (outcome IN ('yes', 'no')),
    side TEXT NOT NULL CHECK (side IN ('buy', 'sell')),
    price REAL NOT NULL,
    amount_usd REAL NOT NULL,
    shares REAL NOT NULL,
    fee REAL NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS positions (
    market_condition_id TEXT NOT NULL,
    market_slug TEXT NOT NULL,
    market_question TEXT NOT NULL,
    outcome TEXT NOT NULL CHECK (outcome IN ('yes', 'no')),
    shares REAL NOT NULL DEFAULT 0,
    avg_entry_price REAL NOT NULL DEFAULT 0,
    total_cost REAL NOT NULL DEFAULT 0,
    realized_pnl REAL NOT NULL DEFAULT 0,
    is_resolved INTEGER NOT NULL DEFAULT 0,
    resolved_at TEXT,
    PRIMARY KEY (market_condition_id, outcome)
);

CREATE TABLE IF NOT EXISTS market_cache (
    cache_key TEXT PRIMARY KEY,
    data TEXT NOT NULL,
    fetched_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


class Database:
    """SQLite operations for pm-sim paper trading."""

    def __init__(self, data_dir: str):
        self.data_dir = data_dir
        self.db_path = os.path.join(data_dir, "paper.db")

    def _connect(self) -> sqlite3.Connection:
        os.makedirs(self.data_dir, exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(SCHEMA)

    def init_account(self, balance: float = 10000.0, fee_bps: int = 0) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO account (id, starting_balance, cash, fee_bps) VALUES (1, ?, ?, ?)",
                (balance, balance, fee_bps),
            )

    def get_account(self) -> Account:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM account WHERE id = 1").fetchone()
        if row is None:
            raise NotInitializedError()
        return Account(
            starting_balance=row["starting_balance"],
            cash=row["cash"],
            fee_bps=row["fee_bps"],
            created_at=row["created_at"],
        )

    def update_cash(self, delta: float) -> None:
        with self._connect() as conn:
            conn.execute("UPDATE account SET cash = cash + ? WHERE id = 1", (delta,))

    def reset(self) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM trades")
            conn.execute("DELETE FROM positions")
            conn.execute("DELETE FROM account")
            conn.execute("DELETE FROM market_cache")

    def insert_trade(
        self,
        market_condition_id: str,
        market_slug: str,
        market_question: str,
        outcome: str,
        side: str,
        price: float,
        amount_usd: float,
        shares: float,
        fee: float,
    ) -> int:
        with self._connect() as conn:
            cursor = conn.execute(
                """INSERT INTO trades
                   (market_condition_id, market_slug, market_question, outcome, side, price, amount_usd, shares, fee)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (market_condition_id, market_slug, market_question, outcome, side, price, amount_usd, shares, fee),
            )
            return cursor.lastrowid

    def get_trades(self, limit: int = 50) -> list[Trade]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM trades ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [
            Trade(
                id=r["id"],
                market_condition_id=r["market_condition_id"],
                market_slug=r["market_slug"],
                market_question=r["market_question"],
                outcome=r["outcome"],
                side=r["side"],
                price=r["price"],
                amount_usd=r["amount_usd"],
                shares=r["shares"],
                fee=r["fee"],
                created_at=r["created_at"],
            )
            for r in rows
        ]

    def upsert_position(
        self,
        market_condition_id: str,
        market_slug: str,
        market_question: str,
        outcome: str,
        shares_delta: float,
        cost_delta: float,
    ) -> None:
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT shares, total_cost FROM positions WHERE market_condition_id = ? AND outcome = ?",
                (market_condition_id, outcome),
            ).fetchone()

            if existing is None:
                avg_price = cost_delta / shares_delta if shares_delta != 0 else 0
                conn.execute(
                    """INSERT INTO positions
                       (market_condition_id, market_slug, market_question, outcome, shares, avg_entry_price, total_cost)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (market_condition_id, market_slug, market_question, outcome, shares_delta, avg_price, cost_delta),
                )
            else:
                new_shares = existing["shares"] + shares_delta
                new_cost = existing["total_cost"] + cost_delta
                new_avg = new_cost / new_shares if new_shares > 0 else 0
                conn.execute(
                    """UPDATE positions
                       SET shares = ?, avg_entry_price = ?, total_cost = ?
                       WHERE market_condition_id = ? AND outcome = ?""",
                    (new_shares, new_avg, new_cost, market_condition_id, outcome),
                )

    def get_position(self, market_condition_id: str, outcome: str) -> Position | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM positions WHERE market_condition_id = ? AND outcome = ?",
                (market_condition_id, outcome),
            ).fetchone()
        if row is None:
            return None
        return self._row_to_position(row)

    def get_open_positions(self) -> list[Position]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM positions WHERE is_resolved = 0 AND shares > 0"
            ).fetchall()
        return [self._row_to_position(r) for r in rows]

    def get_positions_for_market(self, market_condition_id: str) -> list[Position]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM positions WHERE market_condition_id = ? AND is_resolved = 0 AND shares > 0",
                (market_condition_id,),
            ).fetchall()
        return [self._row_to_position(r) for r in rows]

    def resolve_position(self, market_condition_id: str, outcome: str, payout: float) -> None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT total_cost FROM positions WHERE market_condition_id = ? AND outcome = ?",
                (market_condition_id, outcome),
            ).fetchone()
            realized = payout - row["total_cost"] if row else 0
            conn.execute(
                """UPDATE positions
                   SET is_resolved = 1, resolved_at = datetime('now'), realized_pnl = ?
                   WHERE market_condition_id = ? AND outcome = ?""",
                (realized, market_condition_id, outcome),
            )

    def set_cache(self, key: str, data: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO market_cache (cache_key, data, fetched_at) VALUES (?, ?, datetime('now'))",
                (key, data),
            )

    def get_cache(self, key: str, max_age_seconds: int = 300) -> str | None:
        with self._connect() as conn:
            row = conn.execute(
                """SELECT data FROM market_cache
                   WHERE cache_key = ?
                   AND (julianday('now') - julianday(fetched_at)) * 86400 < ?""",
                (key, max_age_seconds),
            ).fetchone()
        return row["data"] if row else None

    def _row_to_position(self, row: sqlite3.Row) -> Position:
        return Position(
            market_condition_id=row["market_condition_id"],
            market_slug=row["market_slug"],
            market_question=row["market_question"],
            outcome=row["outcome"],
            shares=row["shares"],
            avg_entry_price=row["avg_entry_price"],
            total_cost=row["total_cost"],
            realized_pnl=row["realized_pnl"],
            is_resolved=bool(row["is_resolved"]),
            resolved_at=row["resolved_at"],
        )
```

**Step 4: Run tests to verify they pass**

Run: `cd /Users/robert/workspace/polymarket && python -m pytest tests/test_db.py -v`
Expected: All tests PASS

**Step 5: Commit**

```bash
git add pm_sim/db.py tests/test_db.py
git commit -m "feat: SQLite database layer with account, trades, positions, cache"
```

---

### Task 3: Polymarket API Client (api.py)

**Files:**
- Create: `pm_sim/api.py`
- Create: `tests/test_api.py`

**Step 1: Write failing tests for api.py**

These tests use `pytest-httpx` to mock HTTP responses. The fixtures simulate Polymarket API responses based on the real format discovered during research.

```python
"""Tests for pm_sim.api — Polymarket HTTP client."""

import json

import pytest

from pm_sim.api import PolymarketClient
from pm_sim.models import ApiError


@pytest.fixture
def client(tmp_data_dir):
    from pm_sim.db import Database
    db = Database(tmp_data_dir)
    db.init_schema()
    return PolymarketClient(db)


@pytest.fixture
def gamma_market_response():
    """Realistic Gamma API market response."""
    return [
        {
            "id": "12345",
            "conditionId": "0xabc123def456",
            "slug": "bitcoin-above-100k",
            "question": "Will Bitcoin be above $100k on Dec 31?",
            "outcomes": '["Yes","No"]',
            "outcomePrices": '["0.65","0.35"]',
            "clobTokenIds": '["TOKEN_YES_123","TOKEN_NO_456"]',
            "active": True,
            "closed": False,
            "volume": "5000000",
            "liquidity": "250000",
            "description": "Resolves based on CoinGecko",
            "endDate": "2026-12-31T23:59:59Z",
        }
    ]


class TestListMarkets:
    def test_list_markets(self, client, httpx_mock, gamma_market_response):
        httpx_mock.add_response(
            url="https://gamma-api.polymarket.com/markets?limit=10&active=true&closed=false&order=volume&ascending=false",
            json=gamma_market_response,
        )
        markets = client.list_markets(limit=10)
        assert len(markets) == 1
        assert markets[0].slug == "bitcoin-above-100k"
        assert markets[0].yes_price == 0.65

    def test_list_markets_uses_cache(self, client, httpx_mock, gamma_market_response):
        httpx_mock.add_response(
            url="https://gamma-api.polymarket.com/markets?limit=10&active=true&closed=false&order=volume&ascending=false",
            json=gamma_market_response,
        )
        client.list_markets(limit=10)
        # Second call should use cache, no new HTTP request
        markets = client.list_markets(limit=10)
        assert len(markets) == 1
        assert len(httpx_mock.get_requests()) == 1  # Only one HTTP call


class TestSearchMarkets:
    def test_search(self, client, httpx_mock, gamma_market_response):
        httpx_mock.add_response(
            url="https://gamma-api.polymarket.com/markets?_q=bitcoin&limit=10&active=true&closed=false",
            json=gamma_market_response,
        )
        markets = client.search_markets("bitcoin", limit=10)
        assert len(markets) == 1
        assert "bitcoin" in markets[0].slug


class TestGetMarket:
    def test_get_by_slug(self, client, httpx_mock, gamma_market_response):
        httpx_mock.add_response(
            url="https://gamma-api.polymarket.com/markets?slug=bitcoin-above-100k",
            json=gamma_market_response,
        )
        market = client.get_market("bitcoin-above-100k")
        assert market.condition_id == "0xabc123def456"
        assert market.question == "Will Bitcoin be above $100k on Dec 31?"


class TestGetMidpoint:
    def test_get_midpoint(self, client, httpx_mock):
        httpx_mock.add_response(
            url="https://clob.polymarket.com/midpoint?token_id=TOKEN_YES_123",
            json={"mid": "0.6500"},
        )
        mid = client.get_midpoint("TOKEN_YES_123")
        assert mid == 0.65

    def test_get_midpoint_api_error(self, client, httpx_mock):
        httpx_mock.add_response(
            url="https://clob.polymarket.com/midpoint?token_id=BAD_TOKEN",
            status_code=500,
        )
        with pytest.raises(ApiError):
            client.get_midpoint("BAD_TOKEN")


class TestGetOrderBook:
    def test_get_book(self, client, httpx_mock):
        httpx_mock.add_response(
            url="https://clob.polymarket.com/book?token_id=TOKEN_YES_123",
            json={
                "bids": [
                    {"price": "0.64", "size": "1500"},
                    {"price": "0.63", "size": "2000"},
                ],
                "asks": [
                    {"price": "0.66", "size": "1200"},
                    {"price": "0.67", "size": "800"},
                ],
            },
        )
        book = client.get_order_book("TOKEN_YES_123")
        assert len(book.bids) == 2
        assert book.bids[0].price == 0.64
        assert book.asks[0].size == 1200.0
```

**Step 2: Run tests to verify they fail**

Run: `cd /Users/robert/workspace/polymarket && python -m pytest tests/test_api.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pm_sim.api'`

**Step 3: Implement pm_sim/api.py**

```python
"""Polymarket HTTP client with caching."""

from __future__ import annotations

import json

import httpx

from pm_sim.db import Database
from pm_sim.models import ApiError, Market, MarketNotFoundError, OrderBook, OrderBookLevel

GAMMA_BASE = "https://gamma-api.polymarket.com"
CLOB_BASE = "https://clob.polymarket.com"
CACHE_TTL = 300  # 5 minutes


class PolymarketClient:
    """Read-only Polymarket API client with SQLite-backed cache."""

    def __init__(self, db: Database):
        self.db = db
        self._http = httpx.Client(timeout=15.0, follow_redirects=True)

    def list_markets(self, limit: int = 20, sort: str = "volume") -> list[Market]:
        cache_key = f"markets:list:{limit}:{sort}"
        cached = self.db.get_cache(cache_key, max_age_seconds=CACHE_TTL)
        if cached:
            return [self._parse_gamma_market(m) for m in json.loads(cached)]

        params = {
            "limit": limit,
            "active": "true",
            "closed": "false",
            "order": sort,
            "ascending": "false",
        }
        data = self._gamma_get("/markets", params)
        self.db.set_cache(cache_key, json.dumps(data))
        return [self._parse_gamma_market(m) for m in data]

    def search_markets(self, query: str, limit: int = 10) -> list[Market]:
        cache_key = f"markets:search:{query}:{limit}"
        cached = self.db.get_cache(cache_key, max_age_seconds=CACHE_TTL)
        if cached:
            return [self._parse_gamma_market(m) for m in json.loads(cached)]

        params = {"_q": query, "limit": limit, "active": "true", "closed": "false"}
        data = self._gamma_get("/markets", params)
        self.db.set_cache(cache_key, json.dumps(data))
        return [self._parse_gamma_market(m) for m in data]

    def get_market(self, slug_or_id: str) -> Market:
        cache_key = f"market:{slug_or_id}"
        cached = self.db.get_cache(cache_key, max_age_seconds=CACHE_TTL)
        if cached:
            data = json.loads(cached)
            if isinstance(data, list) and data:
                return self._parse_gamma_market(data[0])

        # Try slug first, then condition ID
        data = self._gamma_get("/markets", {"slug": slug_or_id})
        if not data:
            data = self._gamma_get("/markets", {"id": slug_or_id})
        if not data:
            raise MarketNotFoundError(slug_or_id)

        self.db.set_cache(cache_key, json.dumps(data))
        return self._parse_gamma_market(data[0])

    def get_midpoint(self, token_id: str) -> float:
        """Fetch real-time midpoint price. Never cached."""
        data = self._clob_get("/midpoint", {"token_id": token_id})
        return float(data.get("mid", 0))

    def get_order_book(self, token_id: str) -> OrderBook:
        """Fetch real-time order book. Never cached."""
        data = self._clob_get("/book", {"token_id": token_id})
        return OrderBook(
            bids=[OrderBookLevel(price=float(b["price"]), size=float(b["size"])) for b in data.get("bids", [])],
            asks=[OrderBookLevel(price=float(a["price"]), size=float(a["size"])) for a in data.get("asks", [])],
        )

    def _gamma_get(self, path: str, params: dict) -> list[dict]:
        try:
            resp = self._http.get(f"{GAMMA_BASE}{path}", params=params)
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPStatusError as e:
            raise ApiError(f"Gamma API {e.response.status_code}: {path}") from e
        except httpx.RequestError as e:
            raise ApiError(f"Network error: {e}") from e

    def _clob_get(self, path: str, params: dict) -> dict:
        try:
            resp = self._http.get(f"{CLOB_BASE}{path}", params=params)
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPStatusError as e:
            raise ApiError(f"CLOB API {e.response.status_code}: {path}") from e
        except httpx.RequestError as e:
            raise ApiError(f"Network error: {e}") from e

    def _parse_gamma_market(self, raw: dict) -> Market:
        """Parse a raw Gamma API market dict into a Market dataclass."""

        def _parse_json_list(val, cast=str) -> list:
            if isinstance(val, str):
                return [cast(x) for x in json.loads(val)]
            if isinstance(val, list):
                return [cast(x) for x in val]
            return []

        return Market(
            condition_id=raw.get("conditionId", raw.get("condition_id", "")),
            slug=raw.get("slug", ""),
            question=raw.get("question", ""),
            outcomes=_parse_json_list(raw.get("outcomes", "[]")),
            outcome_prices=_parse_json_list(raw.get("outcomePrices", "[]"), float),
            token_ids=_parse_json_list(raw.get("clobTokenIds", "[]")),
            active=bool(raw.get("active", False)),
            closed=bool(raw.get("closed", False)),
            volume=float(raw.get("volume", 0) or 0),
            liquidity=float(raw.get("liquidity", 0) or 0),
            description=raw.get("description", ""),
            end_date=raw.get("endDate", ""),
        )
```

**Step 4: Run tests to verify they pass**

Run: `cd /Users/robert/workspace/polymarket && python -m pytest tests/test_api.py -v`
Expected: All tests PASS

**Step 5: Commit**

```bash
git add pm_sim/api.py tests/test_api.py
git commit -m "feat: Polymarket API client with Gamma + CLOB endpoints and caching"
```

---

### Task 4: Trade Engine (engine.py)

**Files:**
- Create: `pm_sim/engine.py`
- Create: `tests/test_engine.py`

**Step 1: Write failing tests for engine.py**

These tests mock the API client to test pure trading logic in isolation.

```python
"""Tests for pm_sim.engine — trade execution logic."""

from unittest.mock import MagicMock

import pytest

from pm_sim.db import Database
from pm_sim.engine import TradingEngine
from pm_sim.models import (
    InsufficientBalanceError,
    InvalidOutcomeError,
    Market,
    MarketClosedError,
    NoPositionError,
    NotInitializedError,
)


@pytest.fixture
def db(tmp_data_dir):
    d = Database(tmp_data_dir)
    d.init_schema()
    d.init_account(balance=10000.0, fee_bps=0)
    return d


@pytest.fixture
def mock_api(sample_market):
    api = MagicMock()
    api.get_market.return_value = sample_market
    api.get_midpoint.return_value = 0.65
    return api


@pytest.fixture
def engine(db, mock_api):
    return TradingEngine(db, mock_api)


class TestBuy:
    def test_buy_yes(self, engine):
        result = engine.buy("bitcoin-above-100k", "yes", 100.0)
        assert result.side == "buy"
        assert result.outcome == "yes"
        assert result.price == 0.65
        assert result.shares == pytest.approx(100.0 / 0.65, abs=0.01)
        assert result.amount_usd == 100.0
        assert result.cash_remaining == pytest.approx(9900.0)

    def test_buy_no(self, engine, mock_api):
        mock_api.get_midpoint.return_value = 0.35
        result = engine.buy("bitcoin-above-100k", "no", 50.0)
        assert result.outcome == "no"
        assert result.price == 0.35
        assert result.shares == pytest.approx(50.0 / 0.35, abs=0.01)

    def test_buy_invalid_outcome(self, engine):
        with pytest.raises(InvalidOutcomeError):
            engine.buy("bitcoin-above-100k", "maybe", 100.0)

    def test_buy_insufficient_balance(self, engine):
        with pytest.raises(InsufficientBalanceError):
            engine.buy("bitcoin-above-100k", "yes", 20000.0)

    def test_buy_closed_market(self, engine, mock_api, closed_market):
        mock_api.get_market.return_value = closed_market
        with pytest.raises(MarketClosedError):
            engine.buy("trump-wins-2024", "yes", 100.0)

    def test_buy_with_fees(self, db, mock_api):
        db.reset()
        db.init_account(balance=10000.0, fee_bps=200)  # 2% base fee
        engine = TradingEngine(db, mock_api)
        result = engine.buy("bitcoin-above-100k", "yes", 100.0)
        # fee = 200/10000 * min(0.65, 0.35) * 100 = 0.02 * 0.35 * 100 = 0.70
        assert result.fee == pytest.approx(0.70, abs=0.01)
        assert result.cash_remaining == pytest.approx(10000.0 - 100.0 - 0.70, abs=0.01)

    def test_buy_creates_position(self, engine, db):
        engine.buy("bitcoin-above-100k", "yes", 100.0)
        pos = db.get_position("0xabc123", "yes")
        assert pos is not None
        assert pos.shares == pytest.approx(100.0 / 0.65, abs=0.01)

    def test_buy_twice_averages_price(self, engine, mock_api, db):
        engine.buy("bitcoin-above-100k", "yes", 100.0)
        mock_api.get_midpoint.return_value = 0.70
        engine.buy("bitcoin-above-100k", "yes", 70.0)
        pos = db.get_position("0xabc123", "yes")
        # 100/0.65 + 70/0.70 = 153.85 + 100 = 253.85 shares, cost = 170
        assert pos.shares == pytest.approx(253.85, abs=0.1)
        assert pos.total_cost == pytest.approx(170.0, abs=0.01)


class TestSell:
    def test_sell_yes(self, engine, mock_api):
        engine.buy("bitcoin-above-100k", "yes", 100.0)
        mock_api.get_midpoint.return_value = 0.70
        result = engine.sell("bitcoin-above-100k", "yes", 70.0)
        assert result.side == "sell"
        assert result.price == 0.70
        assert result.shares == pytest.approx(70.0 / 0.70, abs=0.01)

    def test_sell_no_position(self, engine):
        with pytest.raises(NoPositionError):
            engine.sell("bitcoin-above-100k", "yes", 50.0)

    def test_sell_more_than_held(self, engine, mock_api):
        engine.buy("bitcoin-above-100k", "yes", 100.0)
        mock_api.get_midpoint.return_value = 0.70
        # Try to sell more shares worth than we hold
        with pytest.raises(NoPositionError):
            engine.sell("bitcoin-above-100k", "yes", 200.0)

    def test_sell_updates_position(self, engine, mock_api, db):
        engine.buy("bitcoin-above-100k", "yes", 100.0)
        mock_api.get_midpoint.return_value = 0.70
        engine.sell("bitcoin-above-100k", "yes", 35.0)
        pos = db.get_position("0xabc123", "yes")
        # Bought 153.85 shares, sold 35/0.70 = 50 shares → 103.85 remaining
        assert pos.shares == pytest.approx(103.85, abs=0.1)

    def test_sell_adds_cash(self, engine, mock_api, db):
        engine.buy("bitcoin-above-100k", "yes", 100.0)
        mock_api.get_midpoint.return_value = 0.70
        engine.sell("bitcoin-above-100k", "yes", 35.0)
        acct = db.get_account()
        # 10000 - 100 + 35 = 9935
        assert acct.cash == pytest.approx(9935.0, abs=0.01)


class TestPortfolio:
    def test_empty_portfolio(self, engine):
        portfolio = engine.get_portfolio()
        assert portfolio["positions"] == []
        assert portfolio["total_value"] == pytest.approx(10000.0)

    def test_portfolio_with_position(self, engine, mock_api):
        engine.buy("bitcoin-above-100k", "yes", 100.0)
        mock_api.get_midpoint.return_value = 0.70
        portfolio = engine.get_portfolio()
        assert len(portfolio["positions"]) == 1
        pos = portfolio["positions"][0]
        assert pos.unrealized_pnl == pytest.approx(
            (100.0 / 0.65) * 0.70 - 100.0, abs=0.1
        )

    def test_portfolio_total_value(self, engine, mock_api):
        engine.buy("bitcoin-above-100k", "yes", 100.0)
        mock_api.get_midpoint.return_value = 0.70
        portfolio = engine.get_portfolio()
        # cash=9900 + position_value = 153.85 * 0.70 = 107.69
        assert portfolio["total_value"] == pytest.approx(9900.0 + 107.69, abs=0.1)


class TestResolve:
    def test_resolve_winning(self, engine, mock_api, db, closed_market):
        # First buy at active market
        engine.buy("bitcoin-above-100k", "yes", 100.0)
        # Market resolves — YES wins
        closed_market.condition_id = "0xabc123"
        closed_market.slug = "bitcoin-above-100k"
        mock_api.get_market.return_value = closed_market
        result = engine.resolve("bitcoin-above-100k")
        assert result.winning_outcome == "yes"
        assert result.total_payout == pytest.approx(100.0 / 0.65, abs=0.1)
        # Cash should increase by payout
        acct = db.get_account()
        assert acct.cash == pytest.approx(9900.0 + 100.0 / 0.65, abs=0.1)

    def test_resolve_losing(self, engine, mock_api, db):
        engine.buy("bitcoin-above-100k", "no", 50.0)
        # YES wins, so NO holders get nothing
        resolved = Market(
            condition_id="0xabc123",
            slug="bitcoin-above-100k",
            question="Will Bitcoin be above $100k on Dec 31?",
            outcomes=["Yes", "No"],
            outcome_prices=[1.0, 0.0],
            token_ids=["TOKEN_YES_123", "TOKEN_NO_456"],
            active=False,
            closed=True,
        )
        mock_api.get_market.return_value = resolved
        result = engine.resolve("bitcoin-above-100k")
        assert result.total_payout == 0.0


class TestNotInitialized:
    def test_buy_not_initialized(self, tmp_data_dir, mock_api):
        db = Database(tmp_data_dir)
        db.init_schema()
        engine = TradingEngine(db, mock_api)
        with pytest.raises(NotInitializedError):
            engine.buy("bitcoin-above-100k", "yes", 100.0)
```

**Step 2: Run tests to verify they fail**

Run: `cd /Users/robert/workspace/polymarket && python -m pytest tests/test_engine.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pm_sim.engine'`

**Step 3: Implement pm_sim/engine.py**

```python
"""Trading engine — buy, sell, portfolio, resolve logic."""

from __future__ import annotations

from pm_sim.api import PolymarketClient
from pm_sim.db import Database
from pm_sim.models import (
    InsufficientBalanceError,
    InvalidOutcomeError,
    MarketClosedError,
    NoPositionError,
    ResolveResult,
    TradeResult,
)


class TradingEngine:
    """Paper trading execution engine."""

    def __init__(self, db: Database, api: PolymarketClient):
        self.db = db
        self.api = api

    def buy(self, slug_or_id: str, outcome: str, amount_usd: float) -> TradeResult:
        outcome = outcome.lower()
        if outcome not in ("yes", "no"):
            raise InvalidOutcomeError(outcome)

        account = self.db.get_account()
        market = self.api.get_market(slug_or_id)

        if market.closed:
            raise MarketClosedError(market.slug)

        token_id = market.yes_token_id if outcome == "yes" else market.no_token_id
        price = self.api.get_midpoint(token_id)

        fee = self._calc_fee(account.fee_bps, price, amount_usd)
        total_cost = amount_usd + fee

        if account.cash < total_cost:
            raise InsufficientBalanceError(need=total_cost, have=account.cash)

        shares = amount_usd / price

        self.db.update_cash(-total_cost)
        trade_id = self.db.insert_trade(
            market_condition_id=market.condition_id,
            market_slug=market.slug,
            market_question=market.question,
            outcome=outcome,
            side="buy",
            price=price,
            amount_usd=amount_usd,
            shares=shares,
            fee=fee,
        )
        self.db.upsert_position(
            market_condition_id=market.condition_id,
            market_slug=market.slug,
            market_question=market.question,
            outcome=outcome,
            shares_delta=shares,
            cost_delta=amount_usd,
        )

        return TradeResult(
            trade_id=trade_id,
            market_slug=market.slug,
            outcome=outcome,
            side="buy",
            price=price,
            shares=shares,
            amount_usd=amount_usd,
            fee=fee,
            cash_remaining=account.cash - total_cost,
        )

    def sell(self, slug_or_id: str, outcome: str, amount_usd: float) -> TradeResult:
        outcome = outcome.lower()
        if outcome not in ("yes", "no"):
            raise InvalidOutcomeError(outcome)

        account = self.db.get_account()
        market = self.api.get_market(slug_or_id)

        token_id = market.yes_token_id if outcome == "yes" else market.no_token_id
        price = self.api.get_midpoint(token_id)

        position = self.db.get_position(market.condition_id, outcome)
        shares_to_sell = amount_usd / price

        if position is None or position.shares < shares_to_sell:
            raise NoPositionError(market.slug, outcome)

        fee = self._calc_fee(account.fee_bps, price, amount_usd)
        proceeds = amount_usd - fee

        self.db.update_cash(proceeds)
        trade_id = self.db.insert_trade(
            market_condition_id=market.condition_id,
            market_slug=market.slug,
            market_question=market.question,
            outcome=outcome,
            side="sell",
            price=price,
            amount_usd=amount_usd,
            shares=shares_to_sell,
            fee=fee,
        )

        cost_basis_sold = shares_to_sell * position.avg_entry_price
        self.db.upsert_position(
            market_condition_id=market.condition_id,
            market_slug=market.slug,
            market_question=market.question,
            outcome=outcome,
            shares_delta=-shares_to_sell,
            cost_delta=-cost_basis_sold,
        )

        return TradeResult(
            trade_id=trade_id,
            market_slug=market.slug,
            outcome=outcome,
            side="sell",
            price=price,
            shares=shares_to_sell,
            amount_usd=amount_usd,
            fee=fee,
            cash_remaining=account.cash + proceeds,
        )

    def get_portfolio(self) -> dict:
        account = self.db.get_account()
        positions = self.db.get_open_positions()

        total_position_value = 0.0
        for pos in positions:
            token_id = self._resolve_token_id(pos.market_slug, pos.outcome)
            if token_id:
                pos.current_price = self.api.get_midpoint(token_id)
            pos.current_value = pos.shares * pos.current_price
            pos.unrealized_pnl = pos.current_value - pos.total_cost
            pos.percent_pnl = (pos.unrealized_pnl / pos.total_cost * 100) if pos.total_cost > 0 else 0.0
            total_position_value += pos.current_value

        return {
            "cash": account.cash,
            "positions": positions,
            "total_position_value": total_position_value,
            "total_value": account.cash + total_position_value,
        }

    def resolve(self, slug_or_id: str) -> ResolveResult:
        market = self.api.get_market(slug_or_id)
        positions = self.db.get_positions_for_market(market.condition_id)

        if not positions:
            raise NoPositionError(market.slug, "any")

        winning_outcome = "yes" if market.outcome_prices[0] >= 0.99 else "no"
        settled = []
        total_payout = 0.0
        total_realized = 0.0

        for pos in positions:
            payout_per_share = 1.0 if pos.outcome == winning_outcome else 0.0
            payout = pos.shares * payout_per_share
            realized = payout - pos.total_cost

            self.db.resolve_position(market.condition_id, pos.outcome, payout)
            total_payout += payout
            total_realized += realized

            settled.append({
                "outcome": pos.outcome,
                "shares": pos.shares,
                "payout_per_share": payout_per_share,
                "payout": payout,
                "realized_pnl": realized,
            })

        self.db.update_cash(total_payout)

        return ResolveResult(
            market_slug=market.slug,
            market_question=market.question,
            winning_outcome=winning_outcome,
            positions_settled=settled,
            total_payout=total_payout,
            total_realized_pnl=total_realized,
        )

    def _calc_fee(self, fee_bps: int, price: float, amount: float) -> float:
        if fee_bps == 0:
            return 0.0
        return (fee_bps / 10000) * min(price, 1 - price) * amount

    def _resolve_token_id(self, market_slug: str, outcome: str) -> str:
        """Resolve a market slug + outcome to a CLOB token ID."""
        try:
            market = self.api.get_market(market_slug)
            return market.yes_token_id if outcome == "yes" else market.no_token_id
        except Exception:
            return ""
```

**Step 4: Run tests to verify they pass**

Run: `cd /Users/robert/workspace/polymarket && python -m pytest tests/test_engine.py -v`
Expected: All tests PASS

**Step 5: Commit**

```bash
git add pm_sim/engine.py tests/test_engine.py
git commit -m "feat: trading engine with buy, sell, portfolio, resolve logic"
```

---

### Task 5: CLI Layer (cli.py) — Account & Market Commands

**Files:**
- Create: `pm_sim/cli.py`
- Create: `tests/test_cli.py`

**Step 1: Write failing tests for account + market CLI commands**

```python
"""Tests for pm_sim.cli — Click CLI commands."""

import json
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from pm_sim.cli import main


@pytest.fixture
def runner(tmp_data_dir):
    return CliRunner()


@pytest.fixture
def cli_args(tmp_data_dir):
    """Common args to point CLI at temp directory."""
    return ["--data-dir", tmp_data_dir]


class TestInit:
    def test_init_default(self, runner, cli_args):
        result = runner.invoke(main, [*cli_args, "init"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["ok"] is True
        assert data["data"]["starting_balance"] == 10000
        assert data["data"]["fee_bps"] == 0

    def test_init_custom(self, runner, cli_args):
        result = runner.invoke(main, [*cli_args, "init", "--balance", "5000", "--fee-bps", "10"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["data"]["starting_balance"] == 5000
        assert data["data"]["fee_bps"] == 10

    def test_init_twice_resets(self, runner, cli_args):
        runner.invoke(main, [*cli_args, "init"])
        result = runner.invoke(main, [*cli_args, "init", "--balance", "5000"])
        data = json.loads(result.output)
        assert data["data"]["starting_balance"] == 5000


class TestBalance:
    def test_balance(self, runner, cli_args):
        runner.invoke(main, [*cli_args, "init"])
        result = runner.invoke(main, [*cli_args, "balance"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["ok"] is True
        assert data["data"]["cash"] == 10000.0

    def test_balance_not_initialized(self, runner, cli_args):
        result = runner.invoke(main, [*cli_args, "balance"])
        data = json.loads(result.output)
        assert data["ok"] is False
        assert data["code"] == "NOT_INITIALIZED"


class TestReset:
    def test_reset(self, runner, cli_args):
        runner.invoke(main, [*cli_args, "init"])
        result = runner.invoke(main, [*cli_args, "reset", "--confirm"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["ok"] is True

    def test_reset_without_confirm(self, runner, cli_args):
        runner.invoke(main, [*cli_args, "init"])
        result = runner.invoke(main, [*cli_args, "reset"])
        data = json.loads(result.output)
        assert data["ok"] is False


class TestHistory:
    def test_history_empty(self, runner, cli_args):
        runner.invoke(main, [*cli_args, "init"])
        result = runner.invoke(main, [*cli_args, "history"])
        data = json.loads(result.output)
        assert data["ok"] is True
        assert data["data"]["trades"] == []
```

**Step 2: Run tests to verify they fail**

Run: `cd /Users/robert/workspace/polymarket && python -m pytest tests/test_cli.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pm_sim.cli'`

**Step 3: Implement pm_sim/cli.py**

```python
"""Click CLI for pm-sim paper trading simulator."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict

import click

from pm_sim.api import PolymarketClient
from pm_sim.db import Database
from pm_sim.engine import TradingEngine
from pm_sim.models import SimError

DEFAULT_DATA_DIR = os.path.expanduser("~/.pm-sim")


def _output(ok: bool, data=None, error: str = "", code: str = "") -> str:
    """Format a JSON response envelope."""
    if ok:
        return json.dumps({"ok": True, "data": data}, default=str)
    return json.dumps({"ok": False, "error": error, "code": code})


def _make_deps(data_dir: str) -> tuple[Database, PolymarketClient, TradingEngine]:
    db = Database(data_dir)
    db.init_schema()
    api = PolymarketClient(db)
    engine = TradingEngine(db, api)
    return db, api, engine


@click.group()
@click.option("--data-dir", default=DEFAULT_DATA_DIR, help="Data directory for SQLite")
@click.option("--output", "output_fmt", default="json", type=click.Choice(["json", "table"]))
@click.pass_context
def main(ctx, data_dir: str, output_fmt: str):
    """pm-sim: Polymarket paper trading simulator for AI agents."""
    ctx.ensure_object(dict)
    ctx.obj["data_dir"] = data_dir
    ctx.obj["output_fmt"] = output_fmt


@main.command()
@click.option("--balance", default=10000.0, help="Starting balance in USD")
@click.option("--fee-bps", default=0, help="Fee rate in basis points")
@click.pass_context
def init(ctx, balance: float, fee_bps: int):
    """Initialize a paper trading account."""
    db, _, _ = _make_deps(ctx.obj["data_dir"])
    db.init_account(balance=balance, fee_bps=fee_bps)
    click.echo(_output(True, {
        "starting_balance": balance,
        "cash": balance,
        "fee_bps": fee_bps,
    }))


@main.command()
@click.pass_context
def balance(ctx):
    """Show current cash balance."""
    db, _, _ = _make_deps(ctx.obj["data_dir"])
    try:
        acct = db.get_account()
        click.echo(_output(True, {
            "cash": acct.cash,
            "starting_balance": acct.starting_balance,
            "fee_bps": acct.fee_bps,
        }))
    except SimError as e:
        click.echo(_output(False, error=str(e), code=e.code))


@main.command()
@click.option("--confirm", is_flag=True, help="Confirm reset")
@click.pass_context
def reset(ctx, confirm: bool):
    """Reset all paper trading data."""
    if not confirm:
        click.echo(_output(False, error="Pass --confirm to reset all data", code="CONFIRM_REQUIRED"))
        return
    db, _, _ = _make_deps(ctx.obj["data_dir"])
    db.reset()
    click.echo(_output(True, {"message": "All data reset"}))


@main.command()
@click.option("--limit", default=50, help="Max trades to return")
@click.pass_context
def history(ctx, limit: int):
    """Show trade history."""
    db, _, _ = _make_deps(ctx.obj["data_dir"])
    try:
        db.get_account()  # Check initialized
        trades = db.get_trades(limit=limit)
        click.echo(_output(True, {"trades": [asdict(t) for t in trades]}))
    except SimError as e:
        click.echo(_output(False, error=str(e), code=e.code))


# --- Market commands ---

@main.group()
def markets():
    """Market data commands."""
    pass


@markets.command("list")
@click.option("--limit", default=20)
@click.option("--sort", default="volume", type=click.Choice(["volume", "liquidity"]))
@click.pass_context
def markets_list(ctx, limit: int, sort: str):
    """List active markets."""
    _, api, _ = _make_deps(ctx.obj["data_dir"])
    try:
        mkts = api.list_markets(limit=limit, sort=sort)
        click.echo(_output(True, {
            "markets": [
                {
                    "slug": m.slug,
                    "question": m.question,
                    "yes_price": m.yes_price,
                    "no_price": m.no_price,
                    "volume": m.volume,
                    "liquidity": m.liquidity,
                    "active": m.active,
                }
                for m in mkts
            ]
        }))
    except SimError as e:
        click.echo(_output(False, error=str(e), code=e.code))


@markets.command("search")
@click.argument("query")
@click.option("--limit", default=10)
@click.pass_context
def markets_search(ctx, query: str, limit: int):
    """Search markets by keyword."""
    _, api, _ = _make_deps(ctx.obj["data_dir"])
    try:
        mkts = api.search_markets(query, limit=limit)
        click.echo(_output(True, {
            "markets": [
                {
                    "slug": m.slug,
                    "question": m.question,
                    "yes_price": m.yes_price,
                    "no_price": m.no_price,
                    "volume": m.volume,
                }
                for m in mkts
            ]
        }))
    except SimError as e:
        click.echo(_output(False, error=str(e), code=e.code))


@markets.command("get")
@click.argument("slug_or_id")
@click.pass_context
def markets_get(ctx, slug_or_id: str):
    """Get details for a specific market."""
    _, api, _ = _make_deps(ctx.obj["data_dir"])
    try:
        m = api.get_market(slug_or_id)
        click.echo(_output(True, {
            "condition_id": m.condition_id,
            "slug": m.slug,
            "question": m.question,
            "outcomes": m.outcomes,
            "outcome_prices": m.outcome_prices,
            "token_ids": m.token_ids,
            "active": m.active,
            "closed": m.closed,
            "volume": m.volume,
            "liquidity": m.liquidity,
            "description": m.description,
            "end_date": m.end_date,
        }))
    except SimError as e:
        click.echo(_output(False, error=str(e), code=e.code))


@main.command()
@click.argument("slug_or_id")
@click.pass_context
def price(ctx, slug_or_id: str):
    """Get current YES/NO prices for a market."""
    _, api, _ = _make_deps(ctx.obj["data_dir"])
    try:
        m = api.get_market(slug_or_id)
        yes_mid = api.get_midpoint(m.yes_token_id)
        no_mid = api.get_midpoint(m.no_token_id)
        click.echo(_output(True, {
            "slug": m.slug,
            "question": m.question,
            "yes": yes_mid,
            "no": no_mid,
        }))
    except SimError as e:
        click.echo(_output(False, error=str(e), code=e.code))


@main.command()
@click.argument("slug_or_id")
@click.pass_context
def book(ctx, slug_or_id: str):
    """Get order book for a market."""
    _, api, _ = _make_deps(ctx.obj["data_dir"])
    try:
        m = api.get_market(slug_or_id)
        yes_book = api.get_order_book(m.yes_token_id)
        click.echo(_output(True, {
            "slug": m.slug,
            "question": m.question,
            "outcome": "yes",
            "bids": [{"price": l.price, "size": l.size} for l in yes_book.bids[:5]],
            "asks": [{"price": l.price, "size": l.size} for l in yes_book.asks[:5]],
        }))
    except SimError as e:
        click.echo(_output(False, error=str(e), code=e.code))


# --- Trading commands ---

@main.command()
@click.argument("slug_or_id")
@click.argument("outcome")
@click.argument("amount", type=float)
@click.pass_context
def buy(ctx, slug_or_id: str, outcome: str, amount: float):
    """Buy shares in a market outcome."""
    _, _, engine = _make_deps(ctx.obj["data_dir"])
    try:
        result = engine.buy(slug_or_id, outcome, amount)
        click.echo(_output(True, asdict(result)))
    except SimError as e:
        click.echo(_output(False, error=str(e), code=e.code))


@main.command()
@click.argument("slug_or_id")
@click.argument("outcome")
@click.argument("amount", type=float)
@click.pass_context
def sell(ctx, slug_or_id: str, outcome: str, amount: float):
    """Sell shares from a market position."""
    _, _, engine = _make_deps(ctx.obj["data_dir"])
    try:
        result = engine.sell(slug_or_id, outcome, amount)
        click.echo(_output(True, asdict(result)))
    except SimError as e:
        click.echo(_output(False, error=str(e), code=e.code))


@main.command()
@click.pass_context
def portfolio(ctx):
    """Show current portfolio with P&L."""
    _, _, engine = _make_deps(ctx.obj["data_dir"])
    try:
        p = engine.get_portfolio()
        click.echo(_output(True, {
            "cash": p["cash"],
            "total_position_value": p["total_position_value"],
            "total_value": p["total_value"],
            "positions": [
                {
                    "market_slug": pos.market_slug,
                    "market_question": pos.market_question,
                    "outcome": pos.outcome,
                    "shares": pos.shares,
                    "avg_entry_price": pos.avg_entry_price,
                    "total_cost": pos.total_cost,
                    "current_price": pos.current_price,
                    "current_value": pos.current_value,
                    "unrealized_pnl": pos.unrealized_pnl,
                    "percent_pnl": pos.percent_pnl,
                }
                for pos in p["positions"]
            ],
        }))
    except SimError as e:
        click.echo(_output(False, error=str(e), code=e.code))


@main.command()
@click.argument("slug_or_id", required=False)
@click.option("--all", "resolve_all", is_flag=True, help="Resolve all positions")
@click.pass_context
def resolve(ctx, slug_or_id: str | None, resolve_all: bool):
    """Check and settle resolved markets."""
    _, _, engine = _make_deps(ctx.obj["data_dir"])
    try:
        if slug_or_id:
            result = engine.resolve(slug_or_id)
            click.echo(_output(True, asdict(result)))
        elif resolve_all:
            # Resolve all open positions
            db, api, _ = _make_deps(ctx.obj["data_dir"])
            positions = db.get_open_positions()
            resolved_markets = set()
            results = []
            for pos in positions:
                if pos.market_condition_id not in resolved_markets:
                    try:
                        r = engine.resolve(pos.market_slug)
                        results.append(asdict(r))
                        resolved_markets.add(pos.market_condition_id)
                    except SimError:
                        pass  # Market not yet resolved
            click.echo(_output(True, {"resolved": results}))
        else:
            click.echo(_output(False, error="Provide a market slug or --all", code="INVALID_ARGS"))
    except SimError as e:
        click.echo(_output(False, error=str(e), code=e.code))


@main.command()
@click.pass_context
def sync(ctx):
    """Pre-warm market data cache."""
    _, api, _ = _make_deps(ctx.obj["data_dir"])
    try:
        markets = api.list_markets(limit=100)
        click.echo(_output(True, {"cached_markets": len(markets)}))
    except SimError as e:
        click.echo(_output(False, error=str(e), code=e.code))
```

**Step 4: Run tests to verify they pass**

Run: `cd /Users/robert/workspace/polymarket && python -m pytest tests/test_cli.py -v`
Expected: All tests PASS

**Step 5: Commit**

```bash
git add pm_sim/cli.py tests/test_cli.py
git commit -m "feat: CLI layer with account, market, trading, and resolve commands"
```

---

### Task 6: Integration Tests

**Files:**
- Create: `tests/test_integration.py`

**Step 1: Write integration tests that test the full CLI→engine→db flow**

These tests mock only the HTTP layer (via pytest-httpx), testing everything else end-to-end.

```python
"""Integration tests — full CLI flow with mocked HTTP only."""

import json

import pytest
from click.testing import CliRunner

from pm_sim.cli import main


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def cli(tmp_data_dir):
    return ["--data-dir", tmp_data_dir]


@pytest.fixture
def market_json():
    return [
        {
            "conditionId": "0xabc123",
            "slug": "bitcoin-above-100k",
            "question": "Will Bitcoin be above $100k?",
            "outcomes": '["Yes","No"]',
            "outcomePrices": '["0.65","0.35"]',
            "clobTokenIds": '["TOKEN_YES","TOKEN_NO"]',
            "active": True,
            "closed": False,
            "volume": "5000000",
            "liquidity": "250000",
            "description": "Test market",
            "endDate": "2026-12-31",
        }
    ]


class TestFullTradingFlow:
    """End-to-end: init → buy → portfolio → sell → history."""

    def test_complete_flow(self, runner, cli, httpx_mock, market_json):
        # 1. Init
        result = runner.invoke(main, [*cli, "init", "--balance", "1000"])
        assert json.loads(result.output)["ok"] is True

        # 2. Buy YES — need market lookup + midpoint
        httpx_mock.add_response(
            url="https://gamma-api.polymarket.com/markets?slug=bitcoin-above-100k",
            json=market_json,
        )
        httpx_mock.add_response(
            url="https://clob.polymarket.com/midpoint?token_id=TOKEN_YES",
            json={"mid": "0.65"},
        )
        result = runner.invoke(main, [*cli, "buy", "bitcoin-above-100k", "yes", "100"])
        data = json.loads(result.output)
        assert data["ok"] is True
        assert data["data"]["side"] == "buy"
        assert data["data"]["price"] == 0.65
        assert data["data"]["cash_remaining"] == pytest.approx(900.0)

        # 3. Portfolio — need midpoint again for live price
        httpx_mock.add_response(
            url="https://gamma-api.polymarket.com/markets?slug=bitcoin-above-100k",
            json=market_json,
        )
        httpx_mock.add_response(
            url="https://clob.polymarket.com/midpoint?token_id=TOKEN_YES",
            json={"mid": "0.70"},
        )
        result = runner.invoke(main, [*cli, "portfolio"])
        data = json.loads(result.output)
        assert data["ok"] is True
        assert len(data["data"]["positions"]) == 1
        pos = data["data"]["positions"][0]
        assert pos["current_price"] == 0.70
        assert pos["unrealized_pnl"] > 0  # Price went up

        # 4. Sell some
        httpx_mock.add_response(
            url="https://gamma-api.polymarket.com/markets?slug=bitcoin-above-100k",
            json=market_json,
        )
        httpx_mock.add_response(
            url="https://clob.polymarket.com/midpoint?token_id=TOKEN_YES",
            json={"mid": "0.70"},
        )
        result = runner.invoke(main, [*cli, "sell", "bitcoin-above-100k", "yes", "35"])
        data = json.loads(result.output)
        assert data["ok"] is True
        assert data["data"]["side"] == "sell"

        # 5. History
        result = runner.invoke(main, [*cli, "history"])
        data = json.loads(result.output)
        assert len(data["data"]["trades"]) == 2  # 1 buy + 1 sell


class TestResolutionFlow:
    """End-to-end: buy → market resolves → settle."""

    def test_resolve_winning(self, runner, cli, httpx_mock, market_json):
        runner.invoke(main, [*cli, "init"])

        # Buy YES
        httpx_mock.add_response(
            url="https://gamma-api.polymarket.com/markets?slug=bitcoin-above-100k",
            json=market_json,
        )
        httpx_mock.add_response(
            url="https://clob.polymarket.com/midpoint?token_id=TOKEN_YES",
            json={"mid": "0.65"},
        )
        runner.invoke(main, [*cli, "buy", "bitcoin-above-100k", "yes", "100"])

        # Market resolves — YES wins
        resolved_market = market_json[0].copy()
        resolved_market["active"] = False
        resolved_market["closed"] = True
        resolved_market["outcomePrices"] = '["1.0","0.0"]'

        httpx_mock.add_response(
            url="https://gamma-api.polymarket.com/markets?slug=bitcoin-above-100k",
            json=[resolved_market],
        )
        result = runner.invoke(main, [*cli, "resolve", "bitcoin-above-100k"])
        data = json.loads(result.output)
        assert data["ok"] is True
        assert data["data"]["winning_outcome"] == "yes"
        assert data["data"]["total_payout"] == pytest.approx(100.0 / 0.65, abs=0.1)

        # Check balance increased
        result = runner.invoke(main, [*cli, "balance"])
        data = json.loads(result.output)
        assert data["data"]["cash"] > 10000.0  # Started with 10k, spent 100, got back ~153
```

**Step 2: Run integration tests**

Run: `cd /Users/robert/workspace/polymarket && python -m pytest tests/test_integration.py -v`
Expected: All PASS

**Step 3: Commit**

```bash
git add tests/test_integration.py
git commit -m "test: integration tests for full trading and resolution flows"
```

---

### Task 7: Run Full Test Suite + Fix Any Issues

**Step 1: Run all tests together**

Run: `cd /Users/robert/workspace/polymarket && python -m pytest tests/ -v --tb=short`
Expected: All tests PASS

**Step 2: Verify CLI installs and runs**

Run: `cd /Users/robert/workspace/polymarket && pip install -e ".[dev]" && pm-sim --help`
Expected: Help output showing all commands

**Step 3: Quick smoke test with real API (read-only)**

Run: `cd /Users/robert/workspace/polymarket && pm-sim init && pm-sim markets list --limit 3`
Expected: JSON output with 3 real markets from Polymarket

**Step 4: Fix any issues discovered**

If tests fail or CLI doesn't work, fix the issues and re-run.

**Step 5: Commit any fixes**

```bash
git add -u
git commit -m "fix: address issues from full test suite run"
```

---

### Task 8: README with Agent Usage Examples

**Files:**
- Create: `README.md`

**Step 1: Write README**

```markdown
# pm-sim

Polymarket paper trading simulator for AI agents.

## Install

```bash
pip install -e .
```

## Quick Start

```bash
# Initialize with $10,000 virtual balance
pm-sim init

# Browse markets
pm-sim markets list --limit 5
pm-sim markets search "bitcoin"
pm-sim markets get bitcoin-above-100k

# Check prices
pm-sim price bitcoin-above-100k

# Buy $100 of YES shares
pm-sim buy bitcoin-above-100k yes 100

# Check portfolio
pm-sim portfolio

# Sell $50 of YES shares
pm-sim sell bitcoin-above-100k yes 50

# View trade history
pm-sim history

# Settle resolved markets
pm-sim resolve --all
```

## Agent Integration

All commands output JSON:

```bash
$ pm-sim balance
{"ok": true, "data": {"cash": 9900.0, "starting_balance": 10000.0, "fee_bps": 0}}

$ pm-sim buy bitcoin-above-100k yes 100
{"ok": true, "data": {"trade_id": 1, "market_slug": "bitcoin-above-100k", "outcome": "yes", "side": "buy", "price": 0.65, "shares": 153.85, "amount_usd": 100.0, "fee": 0.0, "cash_remaining": 9900.0}}
```

Errors return structured codes for programmatic handling:

```bash
$ pm-sim sell nonexistent yes 100
{"ok": false, "error": "Market not found: nonexistent", "code": "MARKET_NOT_FOUND"}
```

## Error Codes

| Code | Meaning |
|------|---------|
| `NOT_INITIALIZED` | Run `pm-sim init` first |
| `INSUFFICIENT_BALANCE` | Not enough cash |
| `MARKET_NOT_FOUND` | Invalid slug or ID |
| `MARKET_CLOSED` | Market no longer active |
| `NO_POSITION` | No shares to sell |
| `INVALID_OUTCOME` | Must be "yes" or "no" |
| `API_ERROR` | Polymarket API issue |
```

**Step 2: Commit**

```bash
git add README.md
git commit -m "docs: README with agent usage examples"
```
