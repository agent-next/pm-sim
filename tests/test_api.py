"""Tests for the Polymarket HTTP client."""

from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

from pm_trader.api import (
    CACHE_TTL_SECONDS,
    CLOB_BASE,
    GAMMA_BASE,
    PolymarketClient,
    _parse_market,
    _parse_order_book,
)
from pm_trader.db import Database
from pm_trader.models import ApiError, MarketNotFoundError


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def db(tmp_data_dir: Path) -> Database:
    database = Database(tmp_data_dir)
    database.init_schema()
    return database


@pytest.fixture
def client(db: Database) -> PolymarketClient:
    c = PolymarketClient(db)
    yield c
    c.close()


# ---------------------------------------------------------------------------
# Gamma API response fixtures
# ---------------------------------------------------------------------------

SAMPLE_GAMMA_MARKET = {
    "condition_id": "0xabc123",
    "slug": "will-bitcoin-hit-100k",
    "question": "Will Bitcoin hit $100k by end of 2026?",
    "description": "Resolves YES if BTC >= 100k.",
    "outcomes": '["Yes", "No"]',
    "outcomePrices": '["0.65", "0.35"]',
    "tokens": json.dumps([
        {"token_id": "tok_yes", "outcome": "Yes"},
        {"token_id": "tok_no", "outcome": "No"},
    ]),
    "active": True,
    "closed": False,
    "acceptingOrders": True,
    "negRisk": False,
    "volume": "5000000",
    "liquidity": "250000",
    "end_date_iso": "2026-12-31T23:59:59Z",
    "fee_rate_bps": 0,
    "minimum_tick_size": "0.01",
}

SAMPLE_BOOK_RESPONSE = {
    "bids": [
        {"price": "0.64", "size": "150"},
        {"price": "0.63", "size": "200"},
    ],
    "asks": [
        {"price": "0.66", "size": "80"},
        {"price": "0.67", "size": "120"},
    ],
}


# ---------------------------------------------------------------------------
# _parse_market tests
# ---------------------------------------------------------------------------

class TestParseMarket:
    def test_basic_parsing(self):
        market = _parse_market(SAMPLE_GAMMA_MARKET)
        assert market.condition_id == "0xabc123"
        assert market.slug == "will-bitcoin-hit-100k"
        assert market.outcomes == ["Yes", "No"]
        assert market.outcome_prices == [0.65, 0.35]
        assert market.active is True
        assert market.closed is False
        assert market.accepting_orders is True
        assert market.neg_risk is False
        assert market.volume == 5_000_000.0
        assert market.liquidity == 250_000.0
        assert market.fee_rate_bps == 0
        assert market.tick_size == 0.01

    def test_tokens_parsed_from_json_string(self):
        market = _parse_market(SAMPLE_GAMMA_MARKET)
        assert len(market.tokens) == 2
        assert market.tokens[0]["token_id"] == "tok_yes"
        assert market.tokens[0]["outcome"] == "Yes"
        assert market.tokens[1]["token_id"] == "tok_no"

    def test_tokens_parsed_from_list(self):
        data = {**SAMPLE_GAMMA_MARKET, "tokens": [
            {"token_id": "t1", "outcome": "Yes"},
            {"token_id": "t2", "outcome": "No"},
        ]}
        market = _parse_market(data)
        assert market.tokens[0]["token_id"] == "t1"

    def test_outcome_prices_from_list(self):
        data = {**SAMPLE_GAMMA_MARKET, "outcomePrices": [0.7, 0.3]}
        market = _parse_market(data)
        assert market.outcome_prices == [0.7, 0.3]

    def test_missing_fields_use_defaults(self):
        data = {"condition_id": "0x1"}
        market = _parse_market(data)
        assert market.slug == ""
        assert market.volume == 0.0
        assert market.outcome_prices == [0.0, 0.0]
        assert market.tick_size == 0.01
        assert market.accepting_orders is True
        assert market.neg_risk is False

    def test_null_volume_liquidity(self):
        data = {**SAMPLE_GAMMA_MARKET, "volume": None, "liquidity": None}
        market = _parse_market(data)
        assert market.volume == 0.0
        assert market.liquidity == 0.0

    def test_string_bool_flags_are_parsed(self):
        data = {
            **SAMPLE_GAMMA_MARKET,
            "active": "TRUE",
            "closed": "false",
            "acceptingOrders": "true",
            "negRisk": "FALSE",
        }
        market = _parse_market(data)
        assert market.active is True
        assert market.closed is False
        assert market.accepting_orders is True
        assert market.neg_risk is False


# ---------------------------------------------------------------------------
# _parse_order_book tests
# ---------------------------------------------------------------------------

class TestParseOrderBook:
    def test_basic_parsing(self):
        book = _parse_order_book(SAMPLE_BOOK_RESPONSE)
        assert len(book.bids) == 2
        assert len(book.asks) == 2
        assert book.bids[0].price == 0.64
        assert book.bids[0].size == 150.0
        assert book.asks[0].price == 0.66
        assert book.asks[0].size == 80.0

    def test_empty_book(self):
        book = _parse_order_book({})
        assert book.bids == []
        assert book.asks == []

    def test_one_sided_book(self):
        book = _parse_order_book({"bids": [{"price": "0.5", "size": "100"}]})
        assert len(book.bids) == 1
        assert book.asks == []


# ---------------------------------------------------------------------------
# PolymarketClient.get_market tests (with httpx mock)
# ---------------------------------------------------------------------------

class TestGetMarket:
    def test_get_market_by_slug(self, client: PolymarketClient, httpx_mock):
        httpx_mock.add_response(
            url=httpx.URL(GAMMA_BASE + "/markets", params={"slug": "will-bitcoin-hit-100k"}),
            json=[SAMPLE_GAMMA_MARKET],
        )
        market = client.get_market("will-bitcoin-hit-100k")
        assert market.condition_id == "0xabc123"
        assert market.slug == "will-bitcoin-hit-100k"

    def test_get_market_by_condition_id(self, client: PolymarketClient, httpx_mock):
        # First request (slug lookup) returns empty
        httpx_mock.add_response(
            url=httpx.URL(GAMMA_BASE + "/markets", params={"slug": "0xabc123"}),
            json=[],
        )
        # Second request goes to CLOB /markets/{condition_id}
        httpx_mock.add_response(
            url=httpx.URL(CLOB_BASE + "/markets/0xabc123"),
            json={
                "condition_id": "0xabc123",
                "market_slug": "will-bitcoin-hit-100k",
                "question": "Will Bitcoin hit $100k?",
                "description": "",
                "active": "True",
                "closed": "False",
                "minimum_tick_size": "0.01",
                "tokens": json.dumps([
                    {"token_id": "tok_yes", "outcome": "Yes"},
                    {"token_id": "tok_no", "outcome": "No"},
                ]),
            },
        )
        # CLOB lookup triggers a Gamma slug lookup for enrichment
        httpx_mock.add_response(
            url=httpx.URL(GAMMA_BASE + "/markets", params={"slug": "will-bitcoin-hit-100k"}),
            json=[SAMPLE_GAMMA_MARKET],
        )
        market = client.get_market("0xabc123")
        assert market.condition_id == "0xabc123"

    def test_market_not_found(self, client: PolymarketClient, httpx_mock):
        httpx_mock.add_response(
            url=httpx.URL(GAMMA_BASE + "/markets", params={"slug": "nonexistent"}),
            json=[],
        )
        # "nonexistent" doesn't start with "0x" so no CLOB lookup
        with pytest.raises(MarketNotFoundError):
            client.get_market("nonexistent")

    def test_market_not_found_by_condition_id(self, client: PolymarketClient, httpx_mock):
        httpx_mock.add_response(
            url=httpx.URL(GAMMA_BASE + "/markets", params={"slug": "0xdead"}),
            json=[],
        )
        httpx_mock.add_response(
            url=httpx.URL(CLOB_BASE + "/markets/0xdead"),
            status_code=404,
            text="Not Found",
        )
        with pytest.raises(MarketNotFoundError):
            client.get_market("0xdead")

    def test_market_cached_on_second_call(self, client: PolymarketClient, httpx_mock):
        httpx_mock.add_response(
            url=httpx.URL(GAMMA_BASE + "/markets", params={"slug": "btc"}),
            json=[SAMPLE_GAMMA_MARKET],
        )
        m1 = client.get_market("btc")
        m2 = client.get_market("btc")  # Should use cache, no second HTTP call
        assert m1.condition_id == m2.condition_id
        assert len(httpx_mock.get_requests()) == 1

    def test_api_http_error(self, client: PolymarketClient, httpx_mock):
        httpx_mock.add_response(
            url=httpx.URL(GAMMA_BASE + "/markets", params={"slug": "err"}),
            status_code=500,
            text="Internal Server Error",
        )
        with pytest.raises(ApiError) as exc_info:
            client.get_market("err")
        assert exc_info.value.status_code == 500


# ---------------------------------------------------------------------------
# PolymarketClient.list_markets tests
# ---------------------------------------------------------------------------

class TestListMarkets:
    def test_list_markets(self, client: PolymarketClient, httpx_mock):
        httpx_mock.add_response(json=[SAMPLE_GAMMA_MARKET])
        markets = client.list_markets(limit=5)
        assert len(markets) == 1
        assert markets[0].slug == "will-bitcoin-hit-100k"

    def test_list_markets_empty(self, client: PolymarketClient, httpx_mock):
        httpx_mock.add_response(json=[])
        markets = client.list_markets()
        assert markets == []


# ---------------------------------------------------------------------------
# PolymarketClient.search_markets tests
# ---------------------------------------------------------------------------

class TestSearchMarkets:
    def test_search(self, client: PolymarketClient, httpx_mock):
        httpx_mock.add_response(json=[SAMPLE_GAMMA_MARKET])
        results = client.search_markets("bitcoin")
        assert len(results) == 1
        assert "bitcoin" in results[0].slug


# ---------------------------------------------------------------------------
# CLOB API tests
# ---------------------------------------------------------------------------

class TestClobEndpoints:
    def test_get_order_book(self, client: PolymarketClient, httpx_mock):
        httpx_mock.add_response(
            url=httpx.URL(CLOB_BASE + "/book", params={"token_id": "tok_yes"}),
            json=SAMPLE_BOOK_RESPONSE,
        )
        book = client.get_order_book("tok_yes")
        assert len(book.bids) == 2
        assert len(book.asks) == 2

    def test_get_midpoint(self, client: PolymarketClient, httpx_mock):
        httpx_mock.add_response(
            url=httpx.URL(CLOB_BASE + "/midpoint", params={"token_id": "tok_yes"}),
            json={"mid": "0.65"},
        )
        mid = client.get_midpoint("tok_yes")
        assert mid == 0.65

    def test_get_fee_rate(self, client: PolymarketClient, httpx_mock):
        httpx_mock.add_response(
            url=httpx.URL(CLOB_BASE + "/fee-rate", params={"token_id": "tok_yes"}),
            json={"base_fee": 200},
        )
        fee = client.get_fee_rate("tok_yes")
        assert fee == 200

    def test_fee_rate_cached(self, client: PolymarketClient, httpx_mock):
        httpx_mock.add_response(
            url=httpx.URL(CLOB_BASE + "/fee-rate", params={"token_id": "tok_yes"}),
            json={"base_fee": 175},
        )
        f1 = client.get_fee_rate("tok_yes")
        f2 = client.get_fee_rate("tok_yes")
        assert f1 == f2 == 175
        assert len(httpx_mock.get_requests()) == 1

    def test_get_fee_rate_legacy_field_compat(self, client: PolymarketClient, httpx_mock):
        httpx_mock.add_response(
            url=httpx.URL(CLOB_BASE + "/fee-rate", params={"token_id": "tok_yes"}),
            json={"fee_rate_bps": 150},
        )
        fee = client.get_fee_rate("tok_yes")
        assert fee == 150

    def test_get_tick_size(self, client: PolymarketClient, httpx_mock):
        httpx_mock.add_response(
            url=httpx.URL(CLOB_BASE + "/tick-size", params={"token_id": "tok_yes"}),
            json={"minimum_tick_size": 0.001},
        )
        tick = client.get_tick_size("tok_yes")
        assert tick == 0.001

    def test_clob_api_error(self, client: PolymarketClient, httpx_mock):
        httpx_mock.add_response(
            url=httpx.URL(CLOB_BASE + "/book", params={"token_id": "bad"}),
            status_code=404,
            text="Not Found",
        )
        with pytest.raises(ApiError) as exc_info:
            client.get_order_book("bad")
        assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------------
# get_trade_context tests
# ---------------------------------------------------------------------------

class TestGetTradeContext:
    def test_returns_market_book_fee(self, client: PolymarketClient, httpx_mock):
        httpx_mock.add_response(
            url=httpx.URL(GAMMA_BASE + "/markets", params={"slug": "btc"}),
            json=[SAMPLE_GAMMA_MARKET],
        )
        httpx_mock.add_response(
            url=httpx.URL(CLOB_BASE + "/book", params={"token_id": "tok_yes"}),
            json=SAMPLE_BOOK_RESPONSE,
        )
        httpx_mock.add_response(
            url=httpx.URL(CLOB_BASE + "/fee-rate", params={"token_id": "tok_yes"}),
            json={"base_fee": 0},
        )
        market, book, fee = client.get_trade_context("btc", "yes")
        assert market.condition_id == "0xabc123"
        assert len(book.bids) == 2
        assert fee == 0

    def test_no_outcome(self, client: PolymarketClient, httpx_mock):
        httpx_mock.add_response(
            url=httpx.URL(GAMMA_BASE + "/markets", params={"slug": "btc"}),
            json=[SAMPLE_GAMMA_MARKET],
        )
        httpx_mock.add_response(
            url=httpx.URL(CLOB_BASE + "/book", params={"token_id": "tok_no"}),
            json=SAMPLE_BOOK_RESPONSE,
        )
        httpx_mock.add_response(
            url=httpx.URL(CLOB_BASE + "/fee-rate", params={"token_id": "tok_no"}),
            json={"base_fee": 175},
        )
        market, book, fee = client.get_trade_context("btc", "no")
        assert fee == 175


# ===========================================================================
# Coverage tests for the 14 uncovered lines
# ===========================================================================


# ---------------------------------------------------------------------------
# Line 61: _get_cached returns None when cache entry is stale (TTL expired)
# ---------------------------------------------------------------------------

class TestCacheTtlExpiry:
    def test_stale_cache_returns_none(self, client: PolymarketClient):
        """When the cached entry is older than CACHE_TTL_SECONDS, _get_cached
        must return None (line 61)."""
        # Insert a cache entry with a fetched_at timestamp well in the past.
        stale_time = datetime.now(timezone.utc) - timedelta(seconds=CACHE_TTL_SECONDS + 60)
        client.db.conn.execute(
            "INSERT OR REPLACE INTO market_cache (cache_key, data, fetched_at) "
            "VALUES (?, ?, ?)",
            ("stale_key", json.dumps({"x": 1}), stale_time.isoformat()),
        )
        client.db.conn.commit()

        result = client._get_cached("stale_key")
        assert result is None

    def test_fresh_cache_returns_data(self, client: PolymarketClient):
        """Sanity: a fresh cache entry should be returned normally."""
        client._set_cached("fresh_key", {"y": 2})
        result = client._get_cached("fresh_key")
        assert result == {"y": 2}


# ---------------------------------------------------------------------------
# Lines 83-84: httpx.RequestError in _gamma_get
# ---------------------------------------------------------------------------

class TestGammaRequestError:
    def test_request_error_raises_api_error(self, client: PolymarketClient):
        """A network-level error (DNS failure, timeout, connection refused)
        should be caught and re-raised as ApiError (lines 83-84)."""
        with patch.object(
            client._http, "get",
            side_effect=httpx.ConnectError("Connection refused"),
        ):
            with pytest.raises(ApiError, match="Gamma API request failed"):
                client._gamma_get("/markets")

    def test_timeout_error_raises_api_error(self, client: PolymarketClient):
        """httpx.TimeoutException (subclass of RequestError) should also
        be caught and re-raised as ApiError."""
        with patch.object(
            client._http, "get",
            side_effect=httpx.ReadTimeout("Read timed out"),
        ):
            with pytest.raises(ApiError, match="Gamma API request failed"):
                client._gamma_get("/markets")


# ---------------------------------------------------------------------------
# Lines 98-99: httpx.RequestError in _clob_get
# ---------------------------------------------------------------------------

class TestClobRequestError:
    def test_request_error_raises_api_error(self, client: PolymarketClient):
        """A network-level error in _clob_get should be caught and
        re-raised as ApiError (lines 98-99)."""
        with patch.object(
            client._http, "get",
            side_effect=httpx.ConnectError("Connection refused"),
        ):
            with pytest.raises(ApiError, match="CLOB API request failed"):
                client._clob_get("/book")

    def test_timeout_error_raises_api_error(self, client: PolymarketClient):
        """httpx.TimeoutException (subclass of RequestError) should also
        be caught and re-raised as ApiError."""
        with patch.object(
            client._http, "get",
            side_effect=httpx.ReadTimeout("Read timed out"),
        ):
            with pytest.raises(ApiError, match="CLOB API request failed"):
                client._clob_get("/midpoint")


# ---------------------------------------------------------------------------
# Lines 122-124: get_market — Gamma returns a dict (not a list) with a
# condition_id.  This is the "slug starts with 0x" CLOB fallback path
# when Gamma returns a dict instead of a list.
# ---------------------------------------------------------------------------

class TestGetMarketGammaDictFallback:
    def test_gamma_returns_dict_with_condition_id(
        self, client: PolymarketClient, httpx_mock
    ):
        """When _gamma_get returns a *dict* (not a list) that has a
        condition_id, get_market should use it directly (lines 122-124)."""
        market_dict = {
            "condition_id": "0xdict1",
            "conditionId": "0xdict1",
            "slug": "dict-market",
            "question": "Dict market?",
            "description": "",
            "outcomes": '["Yes","No"]',
            "outcomePrices": '["0.50","0.50"]',
            "tokens": json.dumps([
                {"token_id": "tok_d_yes", "outcome": "Yes"},
                {"token_id": "tok_d_no", "outcome": "No"},
            ]),
            "active": True,
            "closed": False,
        }
        httpx_mock.add_response(
            url=httpx.URL(GAMMA_BASE + "/markets", params={"slug": "dict-market"}),
            json=market_dict,
        )
        market = client.get_market("dict-market")
        assert market.condition_id == "0xdict1"
        assert market.slug == "dict-market"


# ---------------------------------------------------------------------------
# Lines 123-124 (and 127-146): get_market CLOB fallback path when slug
# starts with 0x and CLOB returns valid data.
# ---------------------------------------------------------------------------

class TestGetMarketClobFallbackPath:
    def test_clob_returns_data_gamma_enrichment_succeeds(
        self, client: PolymarketClient, httpx_mock
    ):
        """Gamma returns empty list for 0x slug, CLOB returns valid market
        data with a slug, and Gamma enrichment succeeds (lines 127-141)."""
        # Gamma slug lookup returns empty list
        httpx_mock.add_response(
            url=httpx.URL(GAMMA_BASE + "/markets", params={"slug": "0xclob1"}),
            json=[],
        )
        # CLOB returns valid market data
        httpx_mock.add_response(
            url=httpx.URL(CLOB_BASE + "/markets/0xclob1"),
            json={
                "condition_id": "0xclob1",
                "market_slug": "clob-market-slug",
                "question": "CLOB market?",
                "description": "",
                "active": True,
                "closed": False,
                "minimum_tick_size": "0.01",
                "tokens": [
                    {"token_id": "tok_c_yes", "outcome": "Yes"},
                    {"token_id": "tok_c_no", "outcome": "No"},
                ],
            },
        )
        # Gamma enrichment lookup succeeds
        httpx_mock.add_response(
            url=httpx.URL(GAMMA_BASE + "/markets", params={"slug": "clob-market-slug"}),
            json=[SAMPLE_GAMMA_MARKET],
        )
        market = client.get_market("0xclob1")
        # Should return the Gamma-enriched data
        assert market.condition_id == "0xabc123"  # from SAMPLE_GAMMA_MARKET

    def test_clob_returns_data_gamma_enrichment_fails_falls_back_to_clob(
        self, client: PolymarketClient, httpx_mock
    ):
        """Gamma enrichment raises an exception -> falls back to CLOB-only
        data (lines 142-146)."""
        # Gamma slug lookup returns empty list
        httpx_mock.add_response(
            url=httpx.URL(GAMMA_BASE + "/markets", params={"slug": "0xclob2"}),
            json=[],
        )
        # CLOB returns valid market data with a slug
        httpx_mock.add_response(
            url=httpx.URL(CLOB_BASE + "/markets/0xclob2"),
            json={
                "condition_id": "0xclob2",
                "market_slug": "clob-only-market",
                "question": "CLOB only market?",
                "description": "",
                "active": True,
                "closed": False,
                "minimum_tick_size": "0.005",
                "tokens": [
                    {"token_id": "tok_co_yes", "outcome": "Yes"},
                    {"token_id": "tok_co_no", "outcome": "No"},
                ],
            },
        )
        # Gamma enrichment lookup fails (network error)
        httpx_mock.add_response(
            url=httpx.URL(GAMMA_BASE + "/markets", params={"slug": "clob-only-market"}),
            status_code=500,
            text="Internal Server Error",
        )
        market = client.get_market("0xclob2")
        # Should return the CLOB-only parsed market
        assert market.condition_id == "0xclob2"
        assert market.slug == "clob-only-market"
        assert market.tick_size == 0.005

    def test_clob_returns_data_no_slug_falls_back_to_clob(
        self, client: PolymarketClient, httpx_mock
    ):
        """CLOB data has no slug (market_slug is empty), so the Gamma
        enrichment is skipped; falls back to CLOB-only (lines 144-146)."""
        # Gamma slug lookup returns empty list
        httpx_mock.add_response(
            url=httpx.URL(GAMMA_BASE + "/markets", params={"slug": "0xnoslugs"}),
            json=[],
        )
        # CLOB returns valid market data but without a slug
        httpx_mock.add_response(
            url=httpx.URL(CLOB_BASE + "/markets/0xnoslugs"),
            json={
                "condition_id": "0xnoslugs",
                "market_slug": "",
                "question": "No-slug market?",
                "description": "",
                "active": True,
                "closed": False,
                "minimum_tick_size": "0.01",
                "tokens": [
                    {"token_id": "tok_ns_yes", "outcome": "Yes"},
                    {"token_id": "tok_ns_no", "outcome": "No"},
                ],
            },
        )
        market = client.get_market("0xnoslugs")
        assert market.condition_id == "0xnoslugs"
        assert market.slug == ""

    def test_clob_returns_data_gamma_enrichment_returns_empty(
        self, client: PolymarketClient, httpx_mock
    ):
        """Gamma enrichment returns an empty list -> falls back to CLOB-only
        data (lines 142-146)."""
        # Gamma slug lookup returns empty list
        httpx_mock.add_response(
            url=httpx.URL(GAMMA_BASE + "/markets", params={"slug": "0xclob3"}),
            json=[],
        )
        # CLOB returns valid market data with a slug
        httpx_mock.add_response(
            url=httpx.URL(CLOB_BASE + "/markets/0xclob3"),
            json={
                "condition_id": "0xclob3",
                "market_slug": "gamma-empty-result",
                "question": "Gamma empty?",
                "description": "",
                "active": True,
                "closed": False,
                "minimum_tick_size": "0.01",
                "tokens": [
                    {"token_id": "tok_ge_yes", "outcome": "Yes"},
                    {"token_id": "tok_ge_no", "outcome": "No"},
                ],
            },
        )
        # Gamma enrichment lookup returns empty list (no match)
        httpx_mock.add_response(
            url=httpx.URL(GAMMA_BASE + "/markets", params={"slug": "gamma-empty-result"}),
            json=[],
        )
        market = client.get_market("0xclob3")
        # Should fall back to CLOB-only parsed market
        assert market.condition_id == "0xclob3"
        assert market.slug == "gamma-empty-result"


# ---------------------------------------------------------------------------
# Line 170: list_markets returns non-list -> returns []
# ---------------------------------------------------------------------------

class TestListMarketsNonList:
    def test_non_list_response_returns_empty(
        self, client: PolymarketClient, httpx_mock
    ):
        """When _gamma_get returns a dict instead of a list,
        list_markets should return [] (line 170)."""
        httpx_mock.add_response(json={"error": "unexpected format"})
        result = client.list_markets()
        assert result == []

    def test_string_response_returns_empty(
        self, client: PolymarketClient
    ):
        """When _gamma_get returns a non-list (string), return []."""
        with patch.object(client, "_gamma_get", return_value="not a list"):
            result = client.list_markets()
            assert result == []


# ---------------------------------------------------------------------------
# Line 179: search_markets returns non-list -> returns []
# ---------------------------------------------------------------------------

class TestSearchMarketsNonList:
    def test_non_list_response_returns_empty(
        self, client: PolymarketClient, httpx_mock
    ):
        """When _gamma_get returns a dict instead of a list,
        search_markets should return [] (line 179)."""
        httpx_mock.add_response(json={"error": "unexpected format"})
        result = client.search_markets("bitcoin")
        assert result == []

    def test_none_response_returns_empty(
        self, client: PolymarketClient
    ):
        """When _gamma_get returns None, search_markets should return []."""
        with patch.object(client, "_gamma_get", return_value=None):
            result = client.search_markets("bitcoin")
            assert result == []


# ---------------------------------------------------------------------------
# Line 213: get_tick_size cache hit path
# ---------------------------------------------------------------------------

class TestGetTickSizeCacheHit:
    def test_tick_size_served_from_cache(
        self, client: PolymarketClient, httpx_mock
    ):
        """After fetching tick_size once, the second call should be served
        from cache without another HTTP request (line 213)."""
        httpx_mock.add_response(
            url=httpx.URL(CLOB_BASE + "/tick-size", params={"token_id": "tok_cache"}),
            json={"minimum_tick_size": 0.005},
        )
        t1 = client.get_tick_size("tok_cache")
        t2 = client.get_tick_size("tok_cache")
        assert t1 == 0.005
        assert t2 == 0.005
        # Only one HTTP request should have been made
        assert len(httpx_mock.get_requests()) == 1

    def test_tick_size_cache_returns_correct_value(
        self, client: PolymarketClient
    ):
        """Directly seed the cache and verify get_tick_size reads from it
        without any HTTP call (line 213)."""
        client._set_cached("tick_size:tok_direct", {"minimum_tick_size": 0.002})
        # No httpx_mock needed — should not make any HTTP call
        result = client.get_tick_size("tok_direct")
        assert result == 0.002


# ---------------------------------------------------------------------------
# list_markets sort_by="liquidity" branch (lines 164-166)
# ---------------------------------------------------------------------------


class TestListMarketsLiquidity:
    def test_sort_by_liquidity(
        self, client: PolymarketClient, httpx_mock
    ):
        """list_markets with sort_by='liquidity' sets order=liquidity."""
        httpx_mock.add_response(json=[SAMPLE_GAMMA_MARKET])
        markets = client.list_markets(sort_by="liquidity")
        assert len(markets) == 1
        req = httpx_mock.get_requests()[0]
        assert "order=liquidity" in str(req.url)
        assert "ascending=false" in str(req.url)


# ---------------------------------------------------------------------------
# _parse_market clobTokenIds as JSON string (lines 308-312)
# ---------------------------------------------------------------------------


class TestParseMarketClobTokenIds:
    def test_clob_token_ids_string(self):
        """When clobTokenIds is a JSON string, parse and map to outcomes."""
        data = {
            "condition_id": "0xcondition",
            "slug": "test-clob-ids",
            "question": "Test?",
            "outcomes": '["Yes", "No"]',
            "outcomePrices": '["0.60", "0.40"]',
            "clobTokenIds": '["tok_yes_clob", "tok_no_clob"]',
            "active": True,
            "closed": False,
        }
        market = _parse_market(data)
        assert len(market.tokens) == 2
        assert market.tokens[0]["token_id"] == "tok_yes_clob"
        assert market.tokens[0]["outcome"] == "Yes"
        assert market.tokens[1]["token_id"] == "tok_no_clob"
        assert market.tokens[1]["outcome"] == "No"

    def test_clob_token_ids_list(self):
        """When clobTokenIds is already a list, use directly."""
        data = {
            "condition_id": "0xcondition2",
            "slug": "test-clob-ids-list",
            "question": "Test?",
            "outcomes": '["A", "B", "C"]',
            "outcomePrices": '["0.33", "0.33", "0.34"]',
            "clobTokenIds": ["tok_a", "tok_b", "tok_c"],
            "active": True,
            "closed": False,
        }
        market = _parse_market(data)
        assert len(market.tokens) == 3
        assert market.tokens[2]["outcome"] == "C"

    def test_clob_token_ids_more_ids_than_outcomes(self):
        """Extra token IDs beyond outcomes get 'OutcomeN' names."""
        data = {
            "condition_id": "0xcondition3",
            "slug": "test-extra-ids",
            "question": "Test?",
            "outcomes": '["Yes"]',
            "outcomePrices": '["0.60"]',
            "clobTokenIds": '["tok1", "tok2"]',
            "active": True,
            "closed": False,
        }
        market = _parse_market(data)
        assert market.tokens[0]["outcome"] == "Yes"
        assert market.tokens[1]["outcome"] == "Outcome1"


# ---------------------------------------------------------------------------
# get_tags tests
# ---------------------------------------------------------------------------


class TestGetTags:
    def test_get_tags(self, client: PolymarketClient, httpx_mock):
        tags = [
            {"id": "1", "label": "Politics", "slug": "politics"},
            {"id": "2", "label": "Crypto", "slug": "crypto"},
        ]
        httpx_mock.add_response(
            url=httpx.URL(GAMMA_BASE + "/tags"),
            json=tags,
        )
        result = client.get_tags()
        assert len(result) == 2
        assert result[0]["slug"] == "politics"

    def test_get_tags_cached(self, client: PolymarketClient, httpx_mock):
        tags = [{"id": "1", "label": "Sports", "slug": "sports"}]
        httpx_mock.add_response(
            url=httpx.URL(GAMMA_BASE + "/tags"),
            json=tags,
        )
        r1 = client.get_tags()
        r2 = client.get_tags()
        assert r1 == r2
        assert len(httpx_mock.get_requests()) == 1

    def test_get_tags_non_list(self, client: PolymarketClient, httpx_mock):
        httpx_mock.add_response(
            url=httpx.URL(GAMMA_BASE + "/tags"),
            json={"error": "unexpected"},
        )
        result = client.get_tags()
        assert result == []


# ---------------------------------------------------------------------------
# get_markets_by_tag tests
# ---------------------------------------------------------------------------


class TestGetMarketsByTag:
    def test_get_markets_by_tag(self, client: PolymarketClient, httpx_mock):
        httpx_mock.add_response(json=[SAMPLE_GAMMA_MARKET])
        markets = client.get_markets_by_tag("politics", limit=5)
        assert len(markets) == 1
        assert markets[0].slug == "will-bitcoin-hit-100k"

    def test_get_markets_by_tag_empty(self, client: PolymarketClient, httpx_mock):
        httpx_mock.add_response(json=[])
        markets = client.get_markets_by_tag("nonexistent")
        assert markets == []

    def test_get_markets_by_tag_non_list(self, client: PolymarketClient, httpx_mock):
        httpx_mock.add_response(json={"error": "bad"})
        markets = client.get_markets_by_tag("bad")
        assert markets == []

    def test_get_markets_by_tag_closed(self, client: PolymarketClient, httpx_mock):
        httpx_mock.add_response(json=[SAMPLE_GAMMA_MARKET])
        markets = client.get_markets_by_tag("politics", closed=True)
        assert len(markets) == 1
        req = httpx_mock.get_requests()[0]
        assert "closed=true" in str(req.url)


# ---------------------------------------------------------------------------
# get_event tests
# ---------------------------------------------------------------------------


class TestGetEvent:
    def test_get_event(self, client: PolymarketClient, httpx_mock):
        event_data = {
            "title": "US Elections 2028",
            "slug": "us-elections-2028",
            "markets": [{"slug": "who-wins-2028"}],
        }
        httpx_mock.add_response(
            url=httpx.URL(GAMMA_BASE + "/events/us-elections-2028"),
            json=event_data,
        )
        result = client.get_event("us-elections-2028")
        assert result["title"] == "US Elections 2028"
        assert len(result["markets"]) == 1

    def test_get_event_cached(self, client: PolymarketClient, httpx_mock):
        event_data = {"title": "Event", "slug": "evt"}
        httpx_mock.add_response(
            url=httpx.URL(GAMMA_BASE + "/events/evt"),
            json=event_data,
        )
        r1 = client.get_event("evt")
        r2 = client.get_event("evt")
        assert r1 == r2
        assert len(httpx_mock.get_requests()) == 1

    def test_get_event_non_dict(self, client: PolymarketClient, httpx_mock):
        httpx_mock.add_response(
            url=httpx.URL(GAMMA_BASE + "/events/bad"),
            json=[],
        )
        result = client.get_event("bad")
        assert result == {}
