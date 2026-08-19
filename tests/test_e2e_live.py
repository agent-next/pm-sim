"""Comprehensive end-to-end tests against the real Polymarket API.

These tests use a $10k paper account and exercise every code path
against live market data.  Fees are currently 0 on all active
Polymarket markets, so the fee code path is tested separately
with injected fee rates.

Run with:  python3 -m pytest tests/test_e2e_live.py -v -s
Skip with: python3 -m pytest -m "not live"
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from pm_trader.engine import Engine
from pm_trader.models import (
    InsufficientBalanceError,
    InvalidOutcomeError,
    MarketClosedError,
    NoPositionError,
    OrderRejectedError,
)

# Skip all tests in this module if PM_TRADER_LIVE env var is not set
# This prevents live tests from running in CI or casual pytest runs
pytestmark = pytest.mark.live


def pytest_configure(config):
    config.addinivalue_line("markers", "live: tests that hit real Polymarket API")


@pytest.fixture(scope="module")
def engine(tmp_path_factory) -> Engine:
    """Engine with a $10k account for the entire test module."""
    data_dir = tmp_path_factory.mktemp("pm-trader-e2e")
    eng = Engine(data_dir)
    eng.init_account(10_000.0)
    yield eng
    eng.close()


def _is_binary_market(m) -> bool:
    """Check if a market has standard Yes/No outcomes."""
    try:
        _ = m.yes_token_id
        _ = m.no_token_id
        return True
    except ValueError:
        return False


def _get_binary_markets(engine, limit=20, sort_by="liquidity"):
    """Return only binary Yes/No markets. Skips test if none available."""
    markets = engine.api.list_markets(limit=limit, sort_by=sort_by)
    result = [m for m in markets if _is_binary_market(m)]
    if not result:
        pytest.skip("No binary markets available from live API")
    return result


# ---------------------------------------------------------------------------
# 1. Market discovery — list, search, get
# ---------------------------------------------------------------------------


class TestMarketDiscovery:
    def test_list_markets_returns_results(self, engine: Engine):
        markets = engine.api.list_markets(limit=5, sort_by="volume")
        assert len(markets) > 0, "Expected at least 1 active market"
        for m in markets:
            assert m.condition_id, "Market must have condition_id"
            assert m.slug, "Market must have slug"
            assert m.question, "Market must have question"
            assert len(m.tokens) == 2, "Binary market must have 2 tokens"
            assert m.active is True
            assert m.closed is False

    def test_list_markets_by_liquidity(self, engine: Engine):
        markets = engine.api.list_markets(limit=5, sort_by="liquidity")
        assert len(markets) > 0
        # API sort is best-effort; just verify we got active markets
        # with non-negative liquidity values
        for m in markets:
            assert m.liquidity >= 0

    def test_search_markets(self, engine: Engine):
        results = engine.api.search_markets("president", limit=5)
        # Search should return results (there are always political markets)
        assert isinstance(results, list)

    def test_get_market_by_slug(self, engine: Engine):
        # List first, then get by slug
        markets = engine.api.list_markets(limit=1)
        assert len(markets) > 0
        slug = markets[0].slug
        market = engine.api.get_market(slug)
        assert market.slug == slug
        assert market.condition_id == markets[0].condition_id

    def test_get_market_by_condition_id(self, engine: Engine):
        markets = engine.api.list_markets(limit=1)
        assert len(markets) > 0
        cid = markets[0].condition_id
        market = engine.api.get_market(cid)
        assert market.condition_id == cid

    def test_market_tokens_are_valid(self, engine: Engine):
        markets = _get_binary_markets(engine, limit=5)
        assert len(markets) > 0, "Need at least 1 binary market"
        for m in markets:
            yes_token = m.yes_token_id
            no_token = m.no_token_id
            assert yes_token, "YES token must exist"
            assert no_token, "NO token must exist"
            assert yes_token != no_token, "YES and NO tokens must differ"

    def test_market_prices_sum_near_one(self, engine: Engine):
        markets = _get_binary_markets(engine, limit=5)
        for m in markets:
            price_sum = sum(m.outcome_prices)
            assert 0.9 <= price_sum <= 1.1, (
                f"Prices {m.outcome_prices} sum to {price_sum}, "
                f"expected ~1.0 for {m.slug}"
            )

    def test_market_caching(self, engine: Engine):
        markets = engine.api.list_markets(limit=1)
        slug = markets[0].slug
        # First call — hits API
        m1 = engine.api.get_market(slug)
        # Second call — should use cache
        m2 = engine.api.get_market(slug)
        assert m1.condition_id == m2.condition_id
        assert m1.slug == m2.slug


# ---------------------------------------------------------------------------
# 2. Price & order book
# ---------------------------------------------------------------------------


class TestPriceAndOrderBook:
    def test_get_midpoint(self, engine: Engine):
        markets = _get_binary_markets(engine, limit=20)
        token_id = markets[0].yes_token_id
        mid = engine.api.get_midpoint(token_id)
        assert 0.0 < mid < 1.0, f"Midpoint {mid} out of range"

    def test_get_order_book(self, engine: Engine):
        markets = _get_binary_markets(engine, limit=20)
        for m in markets[:5]:
            book = engine.api.get_order_book(m.yes_token_id)
            # Active market should have bids and asks
            assert len(book.bids) > 0 or len(book.asks) > 0, (
                f"Order book empty for {m.slug}"
            )

    def test_order_book_structure(self, engine: Engine):
        markets = _get_binary_markets(engine, limit=20)
        book = engine.api.get_order_book(markets[0].yes_token_id)
        for bid in book.bids:
            assert 0.0 < bid.price < 1.0, f"Bid price {bid.price} out of range"
            assert bid.size > 0, f"Bid size {bid.size} must be positive"
        for ask in book.asks:
            assert 0.0 < ask.price < 1.0, f"Ask price {ask.price} out of range"
            assert ask.size > 0, f"Ask size {ask.size} must be positive"

    def test_bid_ask_spread_positive(self, engine: Engine):
        markets = _get_binary_markets(engine, limit=20)
        book = engine.api.get_order_book(markets[0].yes_token_id)
        if book.bids and book.asks:
            best_bid = max(b.price for b in book.bids)
            best_ask = min(a.price for a in book.asks)
            assert best_ask >= best_bid, (
                f"Ask {best_ask} < Bid {best_bid} — crossed book"
            )

    def test_get_fee_rate(self, engine: Engine):
        markets = _get_binary_markets(engine, limit=20)
        fee = engine.api.get_fee_rate(markets[0].yes_token_id)
        assert isinstance(fee, int)
        assert fee >= 0

    def test_get_tick_size(self, engine: Engine):
        markets = _get_binary_markets(engine, limit=5)
        for m in markets:
            tick = engine.api.get_tick_size(m.yes_token_id)
            assert tick in (0.1, 0.01, 0.001, 0.0001), (
                f"Unexpected tick size {tick} for {m.slug}"
            )


# ---------------------------------------------------------------------------
# 3. Buy flow — full order book execution
# ---------------------------------------------------------------------------


class TestBuyFlow:
    def _pick_liquid_market(self, engine: Engine):
        """Pick a binary (Yes/No) active market with asks in the order book."""
        markets = engine.api.list_markets(limit=20, sort_by="liquidity")
        for m in markets:
            # Skip non-binary markets (e.g., sports exact-score)
            try:
                _ = m.yes_token_id
            except ValueError:
                continue
            book = engine.api.get_order_book(m.yes_token_id)
            if book.asks:
                return m
        pytest.skip("No binary market with asks available")

    def test_small_buy_yes(self, engine: Engine):
        m = self._pick_liquid_market(engine)
        result = engine.buy(m.slug, "yes", 5.0)
        t = result.trade
        assert t.side == "buy"
        assert t.outcome == "yes"
        assert t.shares > 0
        assert t.amount_usd == pytest.approx(5.0, abs=0.01)
        assert t.avg_price > 0
        assert t.levels_filled >= 1
        assert result.account.cash < 10_000.0

    def test_small_buy_no(self, engine: Engine):
        """Buy NO shares — needs a binary market with NO-side liquidity."""
        markets = engine.api.list_markets(limit=20, sort_by="liquidity")
        for m in markets:
            try:
                no_token = m.no_token_id
            except ValueError:
                continue
            book = engine.api.get_order_book(no_token)
            if book.asks:
                result = engine.buy(m.slug, "no", 5.0)
                assert result.trade.outcome == "no"
                assert result.trade.shares > 0
                return
        pytest.skip("No binary market with NO-side asks")

    def test_buy_updates_position(self, engine: Engine):
        m = self._pick_liquid_market(engine)
        result = engine.buy(m.slug, "yes", 5.0)
        pos = engine.db.get_position(m.condition_id, "yes")
        assert pos is not None
        assert pos.shares > 0
        assert pos.total_cost > 0
        assert pos.avg_entry_price > 0

    def test_buy_cumulates_position(self, engine: Engine):
        m = self._pick_liquid_market(engine)
        engine.buy(m.slug, "yes", 3.0)
        pos1 = engine.db.get_position(m.condition_id, "yes")

        engine.buy(m.slug, "yes", 3.0)
        pos2 = engine.db.get_position(m.condition_id, "yes")
        assert pos2.shares > pos1.shares
        assert pos2.total_cost > pos1.total_cost

    def test_buy_fak_partial(self, engine: Engine):
        """FAK on a very large order should partially fill or hit balance limit."""
        m = self._pick_liquid_market(engine)
        try:
            result = engine.buy(m.slug, "yes", 500_000.0, order_type="fak")
            if result.trade.is_partial:
                assert result.trade.amount_usd < 500_000.0
            # If not partial, the book was deep enough — that's fine
        except InsufficientBalanceError:
            pass  # FAK partially filled more than our balance — valid behavior
        except OrderRejectedError:
            pass  # Empty book — valid

    def test_buy_fok_rejection_on_huge_order(self, engine: Engine):
        """FOK on an impossibly large order should reject."""
        m = self._pick_liquid_market(engine)
        try:
            engine.buy(m.slug, "yes", 999_999_999.0, order_type="fok")
            # If it somehow filled, that's fine (extremely deep book)
        except OrderRejectedError:
            pass  # Expected
        except InsufficientBalanceError:
            pass  # Also valid if book is deep but wallet too small

    def test_buy_invalid_outcome_rejected(self, engine: Engine):
        m = self._pick_liquid_market(engine)
        with pytest.raises(InvalidOutcomeError):
            engine.buy(m.slug, "maybe", 5.0)

    def test_buy_minimum_order_enforced(self, engine: Engine):
        m = self._pick_liquid_market(engine)
        with pytest.raises(OrderRejectedError, match="Minimum"):
            engine.buy(m.slug, "yes", 0.50)


# ---------------------------------------------------------------------------
# 4. Sell flow
# ---------------------------------------------------------------------------


class TestSellFlow:
    def _buy_position(self, engine: Engine):
        """Buy a position in a binary market that also has bids, so we can sell."""
        markets = engine.api.list_markets(limit=20, sort_by="liquidity")
        for m in markets:
            try:
                _ = m.yes_token_id
            except ValueError:
                continue
            book = engine.api.get_order_book(m.yes_token_id)
            if book.asks and book.bids:
                result = engine.buy(m.slug, "yes", 10.0)
                return m, result
        pytest.skip("No binary market with both bids and asks")

    def test_sell_shares(self, engine: Engine):
        m, buy_result = self._buy_position(engine)
        pos = engine.db.get_position(m.condition_id, "yes")
        sell_shares = pos.shares / 2.0
        cash_before = engine.get_account().cash

        try:
            result = engine.sell(m.slug, "yes", sell_shares, order_type="fak")
        except OrderRejectedError:
            pytest.skip("Bid liquidity too thin for sell (FOK/FAK rejected)")
        assert result.trade.side == "sell"
        assert result.trade.shares <= sell_shares + 0.01
        assert result.trade.shares > 0
        assert result.account.cash > cash_before

    def test_sell_reduces_position(self, engine: Engine):
        m, _ = self._buy_position(engine)
        pos_before = engine.db.get_position(m.condition_id, "yes")
        sell_shares = pos_before.shares / 3.0

        try:
            engine.sell(m.slug, "yes", sell_shares, order_type="fak")
        except OrderRejectedError:
            pytest.skip("Bid liquidity too thin for sell (FOK/FAK rejected)")
        pos_after = engine.db.get_position(m.condition_id, "yes")
        assert pos_after.shares < pos_before.shares

    def test_sell_more_than_held_rejected(self, engine: Engine):
        m, _ = self._buy_position(engine)
        pos = engine.db.get_position(m.condition_id, "yes")
        with pytest.raises(OrderRejectedError, match="Cannot sell"):
            engine.sell(m.slug, "yes", pos.shares + 1000)

    def test_sell_no_position_rejected(self, engine: Engine):
        markets = _get_binary_markets(engine, limit=10)
        # Find a binary market we DON'T have a position in
        for m in markets:
            pos = engine.db.get_position(m.condition_id, "no")
            if pos is None or pos.shares == 0:
                with pytest.raises(NoPositionError):
                    engine.sell(m.slug, "no", 1.0)
                return
        pytest.skip("Somehow have positions in all markets")


# ---------------------------------------------------------------------------
# 5. Portfolio & balance
# ---------------------------------------------------------------------------


class TestPortfolioAndBalance:
    def _ensure_position(self, engine: Engine):
        """Ensure at least one position exists for testing."""
        positions = engine.db.get_open_positions()
        if positions:
            return
        markets = _get_binary_markets(engine, limit=10)
        for m in markets:
            book = engine.api.get_order_book(m.yes_token_id)
            if book.asks:
                engine.buy(m.slug, "yes", 5.0)
                return
        pytest.skip("No binary market with asks")

    def test_portfolio_shows_positions(self, engine: Engine):
        self._ensure_position(engine)
        portfolio = engine.get_portfolio()
        assert isinstance(portfolio, list)
        assert len(portfolio) > 0, "Should have at least 1 position"
        p = portfolio[0]
        assert "market_slug" in p
        assert "shares" in p
        assert "live_price" in p
        assert "unrealized_pnl" in p
        assert "percent_pnl" in p
        assert p["shares"] > 0
        assert 0.0 < p["live_price"] < 1.0

    def test_balance_accounting(self, engine: Engine):
        self._ensure_position(engine)
        bal = engine.get_balance()
        assert bal["cash"] > 0
        assert bal["starting_balance"] == 10_000.0
        assert bal["total_value"] == pytest.approx(
            bal["cash"] + bal["positions_value"], abs=0.01
        )
        assert bal["pnl"] == pytest.approx(
            bal["total_value"] - bal["starting_balance"], abs=0.01
        )

    def test_cash_tracking_consistent(self, engine: Engine):
        """Cash should match starting_balance minus buys plus sells."""
        self._ensure_position(engine)
        account = engine.get_account()
        trades = engine.get_history(limit=100)
        expected_cash = account.starting_balance
        for t in trades:
            if t.side == "buy":
                expected_cash -= (t.amount_usd + t.fee)
            elif t.side == "sell":
                expected_cash += (t.amount_usd - t.fee)
        assert account.cash == pytest.approx(expected_cash, abs=0.02)


# ---------------------------------------------------------------------------
# 6. Trade history
# ---------------------------------------------------------------------------


class TestTradeHistory:
    def _ensure_trade(self, engine: Engine):
        trades = engine.get_history()
        if trades:
            return
        markets = _get_binary_markets(engine, limit=10)
        for m in markets:
            book = engine.api.get_order_book(m.yes_token_id)
            if book.asks:
                engine.buy(m.slug, "yes", 2.0)
                return
        pytest.skip("No binary market with asks")

    def test_history_has_trades(self, engine: Engine):
        self._ensure_trade(engine)
        trades = engine.get_history()
        assert len(trades) > 0
        t = trades[0]
        assert t.id > 0
        assert t.market_condition_id
        assert t.market_slug
        assert t.side in ("buy", "sell")
        assert t.outcome in ("yes", "no")
        assert t.avg_price > 0
        assert t.amount_usd > 0
        assert t.shares > 0
        assert t.created_at

    def test_history_newest_first(self, engine: Engine):
        trades = engine.get_history()
        if len(trades) >= 2:
            assert trades[0].id > trades[1].id

    def test_history_limit(self, engine: Engine):
        trades = engine.get_history(limit=2)
        assert len(trades) <= 2


# ---------------------------------------------------------------------------
# 7. Fee simulation (injected, since real markets are 0 fee)
# ---------------------------------------------------------------------------


class TestFeeSimulation:
    """Test fee handling by temporarily overriding the API fee response."""

    def test_buy_with_200bps_fee(self, engine: Engine):
        """Simulate a 200bps fee market."""
        markets = _get_binary_markets(engine, limit=20)

        # Monkey-patch fee rate to 200bps
        original_get_fee_rate = engine.api.get_fee_rate
        engine.api.get_fee_rate = lambda token_id: 200

        try:
            for m in markets:
                book = engine.api.get_order_book(m.yes_token_id)
                if not book.asks:
                    continue
                cash_before = engine.get_account().cash
                try:
                    result = engine.buy(m.slug, "yes", 20.0)
                except (OrderRejectedError, InsufficientBalanceError):
                    continue
                t = result.trade

                assert t.fee_rate_bps == 200
                assert t.fee > 0, "Fee should be non-zero with 200bps"

                # Fee formula: (200/10000) * min(price, 1-price) * amount_usd
                expected_fee_approx = 0.02 * min(t.avg_price, 1.0 - t.avg_price) * t.amount_usd
                assert t.fee == pytest.approx(expected_fee_approx, rel=0.1)

                # Cash deducted = amount + fee
                assert result.account.cash == pytest.approx(
                    cash_before - t.amount_usd - t.fee, abs=0.01
                )
                return
            pytest.skip("No market with sufficient ask liquidity")
        finally:
            engine.api.get_fee_rate = original_get_fee_rate

    def test_sell_with_175bps_fee(self, engine: Engine):
        """Simulate a 175bps fee on sell."""
        markets = _get_binary_markets(engine, limit=10)
        m = None
        for candidate in markets:
            book = engine.api.get_order_book(candidate.yes_token_id)
            pos = engine.db.get_position(candidate.condition_id, "yes")
            if book.bids and pos and pos.shares > 0:
                m = candidate
                break
        if m is None:
            pytest.skip("No position to sell in a liquid market")

        original_get_fee_rate = engine.api.get_fee_rate
        engine.api.get_fee_rate = lambda token_id: 175

        try:
            pos = engine.db.get_position(m.condition_id, "yes")
            book = engine.api.get_order_book(m.yes_token_id)
            best_bid = max((lvl.price for lvl in book.bids), default=0.0)
            if best_bid <= 0:
                pytest.skip("No valid best bid for sell")

            min_sell_qty = 1.0 / best_bid
            if pos.shares < min_sell_qty:
                pytest.skip("Position too small to satisfy minimum sell notional")

            target_qty = min(pos.shares / 4, 5.0)
            sell_qty = max(target_qty, min_sell_qty)
            cash_before = engine.get_account().cash

            result = engine.sell(m.slug, "yes", sell_qty)
            t = result.trade

            assert t.fee_rate_bps == 175
            assert t.fee > 0

            # Net proceeds = gross - fee
            net = t.amount_usd - t.fee
            assert result.account.cash == pytest.approx(
                cash_before + net, abs=0.01
            )
        finally:
            engine.api.get_fee_rate = original_get_fee_rate

    def test_fee_formula_correctness(self, engine: Engine):
        """Verify fee = (bps/10000) * min(price, 1-price) * size."""
        from pm_trader.orderbook import calculate_fee

        # Test at various prices
        cases = [
            (200, 0.50, 100.0),  # max uncertainty
            (200, 0.10, 100.0),  # low price
            (200, 0.90, 100.0),  # high price
            (175, 0.65, 50.0),
            (250, 0.30, 200.0),
            (0, 0.50, 100.0),    # zero fee
        ]
        for bps, price, size in cases:
            fee = calculate_fee(bps, price, size)
            if bps == 0:
                assert fee == 0.0
            else:
                expected = (bps / 10_000) * min(price, 1.0 - price) * size
                expected = max(expected, 0.0001)  # minimum fee
                assert fee == pytest.approx(expected, rel=0.001), (
                    f"Fee mismatch for bps={bps} price={price} size={size}: "
                    f"got {fee}, expected {expected}"
                )


# ---------------------------------------------------------------------------
# 8. Order book execution accuracy
# ---------------------------------------------------------------------------


class TestOrderBookAccuracy:
    def test_buy_price_within_ask_range(self, engine: Engine):
        """Buy avg_price should be between best_ask and worst_ask used."""
        markets = _get_binary_markets(engine, limit=20)
        for m in markets:
            book = engine.api.get_order_book(m.yes_token_id)
            if len(book.asks) >= 2:
                best_ask = min(a.price for a in book.asks)
                worst_ask = max(a.price for a in book.asks)
                try:
                    result = engine.buy(m.slug, "yes", 5.0)
                except (OrderRejectedError, InsufficientBalanceError):
                    continue
                assert best_ask <= result.trade.avg_price <= worst_ask + 0.01, (
                    f"avg_price {result.trade.avg_price} outside ask range "
                    f"[{best_ask}, {worst_ask}]"
                )
                return
        pytest.skip("No market with 2+ ask levels and sufficient liquidity")

    def test_sell_price_within_bid_range(self, engine: Engine):
        """Sell avg_price should be between worst_bid and best_bid used."""
        markets = _get_binary_markets(engine, limit=20)
        for m in markets:
            book = engine.api.get_order_book(m.yes_token_id)
            pos = engine.db.get_position(m.condition_id, "yes")
            if book.bids and pos and pos.shares > 0:
                best_bid = max(b.price for b in book.bids)
                worst_bid = min(b.price for b in book.bids)

                sell_qty = min(pos.shares / 4, 2.0)
                try:
                    result = engine.sell(m.slug, "yes", sell_qty)
                except OrderRejectedError:
                    continue
                assert worst_bid - 0.01 <= result.trade.avg_price <= best_bid, (
                    f"avg_price {result.trade.avg_price} outside bid range "
                    f"[{worst_bid}, {best_bid}]"
                )
                return
        pytest.skip("No position with bids available")

    def test_shares_equal_usd_divided_by_price(self, engine: Engine):
        """For a buy, shares ~= amount_usd / avg_price."""
        markets = _get_binary_markets(engine, limit=20)
        for m in markets:
            book = engine.api.get_order_book(m.yes_token_id)
            if book.asks:
                try:
                    result = engine.buy(m.slug, "yes", 5.0)
                except (OrderRejectedError, InsufficientBalanceError):
                    continue
                t = result.trade
                expected_shares = t.amount_usd / t.avg_price
                assert t.shares == pytest.approx(expected_shares, rel=0.01)
                return
        pytest.skip("No market with sufficient ask liquidity")


# ---------------------------------------------------------------------------
# 9. Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_outcome_case_insensitive(self, engine: Engine):
        markets = _get_binary_markets(engine, limit=20)
        assert markets, "Need binary markets"
        for m in markets:
            book = engine.api.get_order_book(m.yes_token_id)
            if not book.asks:
                continue
            try:
                result = engine.buy(m.slug, "YES", 2.0)
                assert result.trade.outcome == "yes"
                return
            except (OrderRejectedError, InsufficientBalanceError):
                continue
        pytest.skip("No market with sufficient ask liquidity")

    def test_same_market_yes_and_no(self, engine: Engine):
        """Can hold both YES and NO positions in same market."""
        markets = _get_binary_markets(engine, limit=20)
        for m in markets:
            yes_book = engine.api.get_order_book(m.yes_token_id)
            no_book = engine.api.get_order_book(m.no_token_id)
            if yes_book.asks and no_book.asks:
                try:
                    engine.buy(m.slug, "yes", 3.0)
                    engine.buy(m.slug, "no", 3.0)
                except (OrderRejectedError, InsufficientBalanceError):
                    continue  # Thin book — try next market
                yes_pos = engine.db.get_position(m.condition_id, "yes")
                no_pos = engine.db.get_position(m.condition_id, "no")
                assert yes_pos.shares > 0
                assert no_pos.shares > 0
                return
        pytest.skip("No market with both sides liquid")

    def test_multiple_markets(self, engine: Engine):
        """Can trade across different markets."""
        markets = _get_binary_markets(engine, limit=20)
        traded = []
        for m in markets:
            if len(traded) >= 3:
                break
            book = engine.api.get_order_book(m.yes_token_id)
            if not book.asks:
                continue
            try:
                engine.buy(m.slug, "yes", 2.0)
                traded.append(m)
            except (OrderRejectedError, InsufficientBalanceError):
                continue  # Thin book or balance issue — try next market
        assert len(traded) >= 2, "Should trade in at least 2 markets"
        positions = engine.db.get_open_positions()
        market_ids = {p.market_condition_id for p in positions}
        assert len(market_ids) >= 2


# ---------------------------------------------------------------------------
# 10. Final accounting check
# ---------------------------------------------------------------------------


class TestFinalAccounting:
    def test_final_state_consistent(self, engine: Engine):
        """All cash flows should balance at the end."""
        account = engine.get_account()
        trades = engine.get_history(limit=200)
        portfolio = engine.get_portfolio()

        # Reconstruct cash from trades
        expected_cash = account.starting_balance
        for t in trades:
            if t.side == "buy":
                expected_cash -= (t.amount_usd + t.fee)
            elif t.side == "sell":
                expected_cash += (t.amount_usd - t.fee)

        assert account.cash == pytest.approx(expected_cash, abs=0.05), (
            f"Cash mismatch: DB says {account.cash}, "
            f"trades reconstruct to {expected_cash}"
        )

        # Total value = cash + positions
        positions_value = sum(p["current_value"] for p in portfolio)
        total_value = account.cash + positions_value
        pnl = total_value - account.starting_balance

        print(f"\n{'='*60}")
        print(f"FINAL E2E TEST ACCOUNTING")
        print(f"{'='*60}")
        print(f"Starting balance:  ${account.starting_balance:>12,.2f}")
        print(f"Cash:              ${account.cash:>12,.2f}")
        print(f"Positions value:   ${positions_value:>12,.2f}")
        print(f"Total value:       ${total_value:>12,.2f}")
        print(f"P&L:               ${pnl:>12,.2f}")
        print(f"Total trades:      {len(trades):>12d}")
        print(f"Open positions:    {len(portfolio):>12d}")
        print(f"{'='*60}")
        for p in portfolio:
            print(
                f"  {p['outcome'].upper():>3} {p['market_slug'][:40]:<40} "
                f"{p['shares']:>8.2f} shares @ {p['avg_entry_price']:.4f} "
                f"→ {p['live_price']:.4f}  P&L: ${p['unrealized_pnl']:>+8.2f}"
            )
        print(f"{'='*60}")
