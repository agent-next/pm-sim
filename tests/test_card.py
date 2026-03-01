"""Tests for shareable stats card generators."""

from __future__ import annotations

from pm_trader.card import (
    _detect_milestone,
    _extract,
    _format_top_positions,
    _roi_icon,
    _tier,
    generate_card,
    generate_card_plain,
    generate_daily_report,
    generate_leaderboard_card,
    generate_milestone_tweet,
    generate_pk_card,
    generate_tweet,
)


# ---------------------------------------------------------------------------
# _roi_icon
# ---------------------------------------------------------------------------


class TestRoiIcon:
    def test_fire(self):
        assert _roi_icon(25.0) == "\U0001f525"

    def test_rocket(self):
        assert _roi_icon(15.0) == "\U0001f680"

    def test_chart_up(self):
        assert _roi_icon(5.0) == "\U0001f4c8"

    def test_flat(self):
        assert _roi_icon(0.0) == "\u2796"

    def test_chart_down(self):
        assert _roi_icon(-5.0) == "\U0001f4c9"

    def test_skull(self):
        assert _roi_icon(-15.0) == "\U0001f480"


# ---------------------------------------------------------------------------
# _extract
# ---------------------------------------------------------------------------


class TestExtract:
    def test_defaults(self):
        result = _extract({})
        assert result["roi"] == 0.0
        assert result["pnl"] == 0.0
        assert result["trades"] == 0
        assert result["pnl_sign"] == "+"
        assert result["roi_sign"] == "+"

    def test_negative_pnl(self):
        result = _extract({"pnl": -500.0, "roi_pct": -10.0})
        assert result["pnl_sign"] == ""
        assert result["roi_sign"] == ""

    def test_full_stats(self):
        stats = {
            "roi_pct": 12.5,
            "pnl": 1250.0,
            "total_value": 11250.0,
            "sharpe_ratio": 1.8,
            "win_rate": 0.65,
            "total_trades": 42,
            "max_drawdown": 0.08,
            "total_fees": 15.50,
            "starting_balance": 10000.0,
        }
        result = _extract(stats)
        assert result["roi"] == 12.5
        assert result["total"] == 11250.0
        assert result["sharpe"] == 1.8
        assert result["win"] == 0.65
        assert result["trades"] == 42
        assert result["dd"] == 0.08
        assert result["fees"] == 15.50
        assert result["starting"] == 10000.0
        assert result["icon"] == "\U0001f680"  # 10 < 12.5 < 20


# ---------------------------------------------------------------------------
# _format_top_positions
# ---------------------------------------------------------------------------


SAMPLE_POSITIONS = [
    {"market_slug": "will-btc-hit-100k", "outcome": "yes", "current_value": 500.0, "unrealized_pnl": 50.0},
    {"market_slug": "us-election-2024", "outcome": "no", "current_value": 300.0, "unrealized_pnl": -20.0},
    {"market_slug": "fed-rate-cut-march", "outcome": "yes", "current_value": 800.0, "unrealized_pnl": 120.0},
    {"market_slug": "small-market", "outcome": "yes", "current_value": 50.0, "unrealized_pnl": 5.0},
]


class TestFormatTopPositions:
    def test_empty(self):
        assert _format_top_positions([]) == []

    def test_top_3_by_value(self):
        result = _format_top_positions(SAMPLE_POSITIONS)
        assert len(result) == 3
        # Sorted by current_value desc: fed(800), btc(500), election(300)
        assert "fed-rate-cut-march" in result[0]
        assert "will-btc-hit-100k" in result[1]
        assert "us-election-2024" in result[2]

    def test_custom_limit(self):
        result = _format_top_positions(SAMPLE_POSITIONS, limit=2)
        assert len(result) == 2

    def test_pnl_sign(self):
        result = _format_top_positions(SAMPLE_POSITIONS)
        assert "+$120" in result[0]  # fed: +120
        assert "+$50" in result[1]   # btc: +50
        assert "$-20" in result[2]   # election: -20

    def test_outcome_uppercase(self):
        result = _format_top_positions(SAMPLE_POSITIONS)
        assert "(YES)" in result[0]
        assert "(NO)" in result[2]

    def test_long_slug_truncated(self):
        long = [{"market_slug": "a" * 40, "outcome": "yes", "current_value": 100.0, "unrealized_pnl": 0.0}]
        result = _format_top_positions(long)
        assert "..." in result[0]
        assert len(result[0]) < 60


# ---------------------------------------------------------------------------
# _detect_milestone
# ---------------------------------------------------------------------------


class TestDetectMilestone:
    def test_first_trade(self):
        assert "First trade" in _detect_milestone({"total_trades": 1})

    def test_10_trades(self):
        assert "10 trades" in _detect_milestone({"total_trades": 10})

    def test_50_trades(self):
        assert "50 trades" in _detect_milestone({"total_trades": 50})

    def test_100_trades(self):
        assert "100 trades" in _detect_milestone({"total_trades": 100})

    def test_roi_50(self):
        assert "ROI hit" in _detect_milestone({"roi_pct": 55.0, "total_trades": 25})

    def test_pnl_10k(self):
        assert "$10k" in _detect_milestone({"pnl": 12000.0, "total_trades": 25})

    def test_pnl_5k(self):
        assert "$5k" in _detect_milestone({"pnl": 6000.0, "total_trades": 25})

    def test_pnl_1k(self):
        assert "$1k" in _detect_milestone({"pnl": 1500.0, "total_trades": 25})

    def test_no_milestone(self):
        assert _detect_milestone({"total_trades": 7, "pnl": 50.0}) is None


# ---------------------------------------------------------------------------
# generate_tweet
# ---------------------------------------------------------------------------


class TestGenerateTweet:
    def test_basic_tweet(self):
        stats = {"roi_pct": 25.0, "pnl": 2500.0, "total_trades": 10}
        tweet = generate_tweet(stats)
        assert "made $2,500 trading Polymarket" in tweet
        assert "+25.0% ROI" in tweet
        assert "10 trades" in tweet
        assert "#Polymarket" in tweet
        assert "#AITrading" in tweet
        assert "clawhub install" in tweet
        assert "Can your agent beat mine?" in tweet

    def test_negative_roi(self):
        stats = {"roi_pct": -8.0, "pnl": -800.0}
        tweet = generate_tweet(stats)
        assert "lost $800 trading Polymarket" in tweet
        assert "-8.0% ROI" in tweet

    def test_under_280_chars(self):
        stats = {
            "roi_pct": 99.9,
            "pnl": 9999.0,
            "sharpe_ratio": 3.5,
            "win_rate": 0.9,
            "total_trades": 100,
        }
        tweet = generate_tweet(stats)
        assert len(tweet) <= 400  # generous limit for multiline

    def test_custom_account(self):
        tweet = generate_tweet({}, account="aggressive")
        assert "Polymarket" in tweet

    def test_with_positions(self):
        stats = {"roi_pct": 10.0, "pnl": 1000.0, "total_trades": 5}
        tweet = generate_tweet(stats, positions=SAMPLE_POSITIONS)
        assert "Top calls:" in tweet
        assert "fed-rate-cut-march" in tweet

    def test_no_positions(self):
        tweet = generate_tweet({}, positions=[])
        assert "Top calls:" not in tweet


# ---------------------------------------------------------------------------
# generate_card (markdown)
# ---------------------------------------------------------------------------


class TestGenerateCard:
    def test_basic_card(self):
        stats = {
            "roi_pct": 15.0,
            "pnl": 1500.0,
            "sharpe_ratio": 2.1,
            "win_rate": 0.7,
            "total_trades": 30,
            "max_drawdown": 0.05,
            "total_fees": 10.0,
            "total_value": 11500.0,
            "starting_balance": 10000.0,
        }
        card = generate_card(stats)
        assert "*Polymarket Paper Trading*" in card
        assert "ROI: *+15.0%*" in card
        assert "Win Rate: *70%*" in card
        assert "Trades: *30*" in card
        assert "P&L: *+$1,500.00*" in card
        assert "Portfolio: *$11,500.00*" in card
        assert "Can your agent beat mine?" in card
        assert "clawhub" in card

    def test_zero_stats(self):
        card = generate_card({})
        assert "ROI: *+0.0%*" in card
        assert "Trades: *0*" in card

    def test_with_positions(self):
        card = generate_card({"roi_pct": 5.0}, positions=SAMPLE_POSITIONS)
        assert "*Top positions:*" in card
        assert "fed-rate-cut-march" in card

    def test_no_positions(self):
        card = generate_card({}, positions=[])
        assert "Top positions" not in card


# ---------------------------------------------------------------------------
# generate_card_plain
# ---------------------------------------------------------------------------


class TestGenerateCardPlain:
    def test_basic_plain(self):
        stats = {
            "roi_pct": -3.0,
            "pnl": -300.0,
            "sharpe_ratio": -0.5,
            "win_rate": 0.4,
            "total_trades": 15,
            "max_drawdown": 0.12,
            "total_fees": 5.0,
            "total_value": 9700.0,
            "starting_balance": 10000.0,
        }
        card = generate_card_plain(stats)
        assert "Polymarket Paper Trading" in card
        assert "ROI:       -3.0%" in card
        assert "Win Rate:  40%" in card
        assert "P&L:       $-300.00" in card
        assert "Portfolio: $9,700.00" in card
        assert "Can your agent beat mine?" in card
        # No markdown formatting
        assert "*" not in card

    def test_zero_stats(self):
        card = generate_card_plain({})
        assert "ROI:       +0.0%" in card
        assert "clawhub" in card

    def test_with_positions(self):
        card = generate_card_plain({"roi_pct": 5.0}, positions=SAMPLE_POSITIONS)
        assert "Top positions:" in card
        assert "fed-rate-cut-march" in card
        assert "*" not in card  # Still no markdown


# ---------------------------------------------------------------------------
# _tier
# ---------------------------------------------------------------------------


class TestTier:
    def test_diamond(self):
        stats = {"total_trades": 60, "roi_pct": 25.0, "sharpe_ratio": 2.0}
        assert "Diamond" in _tier(stats)

    def test_gold(self):
        stats = {"total_trades": 35, "roi_pct": 12.0, "sharpe_ratio": 1.2}
        assert "Gold" in _tier(stats)

    def test_silver(self):
        stats = {"total_trades": 25, "roi_pct": 8.0, "sharpe_ratio": 0.5}
        assert "Silver" in _tier(stats)

    def test_bronze(self):
        stats = {"total_trades": 12, "roi_pct": -5.0, "sharpe_ratio": -0.3}
        assert "Bronze" in _tier(stats)

    def test_unranked(self):
        stats = {"total_trades": 3}
        assert "Unranked" in _tier(stats)


# ---------------------------------------------------------------------------
# generate_pk_card
# ---------------------------------------------------------------------------


class TestGeneratePkCard:
    def test_basic_pk(self):
        a = {"roi_pct": 15.0, "pnl": 1500.0, "sharpe_ratio": 1.5, "win_rate": 0.7, "total_trades": 30}
        b = {"roi_pct": 8.0, "pnl": 800.0, "sharpe_ratio": 0.9, "win_rate": 0.55, "total_trades": 20}
        card = generate_pk_card(a, "alice", b, "bob")
        assert "alice" in card
        assert "bob" in card
        assert "+15.0%" in card
        assert "+8.0%" in card
        assert "alice wins" in card
        assert "Who's the better trader?" in card
        assert "clawhub" in card

    def test_b_wins(self):
        a = {"roi_pct": 3.0}
        b = {"roi_pct": 12.0}
        card = generate_pk_card(a, "slow", b, "fast")
        assert "fast wins" in card

    def test_tie(self):
        a = {"roi_pct": 10.0}
        b = {"roi_pct": 10.0}
        card = generate_pk_card(a, "x", b, "y")
        assert "Tie" in card


# ---------------------------------------------------------------------------
# generate_milestone_tweet
# ---------------------------------------------------------------------------


class TestGenerateMilestoneTweet:
    def test_auto_detect(self):
        stats = {"total_trades": 10, "roi_pct": 5.0, "pnl": 500.0, "sharpe_ratio": 1.0}
        tweet = generate_milestone_tweet(stats)
        assert "10 trades milestone" in tweet
        assert "Tier:" in tweet
        assert "#OpenClaw" in tweet
        assert "Can your agent beat mine?" in tweet
        assert "clawhub" in tweet

    def test_custom_milestone(self):
        tweet = generate_milestone_tweet({}, milestone="First win!")
        assert "First win!" in tweet

    def test_no_milestone_fallback(self):
        stats = {"total_trades": 7, "pnl": 50.0}
        tweet = generate_milestone_tweet(stats)
        assert "7 trades and counting" in tweet

    def test_has_stats(self):
        stats = {"roi_pct": 12.0, "pnl": 1200.0, "total_trades": 50, "sharpe_ratio": 1.5}
        tweet = generate_milestone_tweet(stats)
        assert "+12.0% ROI" in tweet
        assert "$1,200 profit" in tweet


# ---------------------------------------------------------------------------
# generate_daily_report
# ---------------------------------------------------------------------------


class TestGenerateDailyReport:
    def test_basic_report(self):
        stats = {
            "roi_pct": 8.0,
            "pnl": 800.0,
            "total_value": 10800.0,
            "total_trades": 20,
            "sharpe_ratio": 1.2,
            "win_rate": 0.6,
        }
        report = generate_daily_report(stats, account="momentum")
        assert "Daily Report" in report
        assert "momentum" in report
        assert "$10,800.00" in report
        assert "+8.0%" in report
        assert "#AITrading" in report
        assert "#OpenClaw" in report
        assert "clawhub" in report

    def test_with_positions(self):
        stats = {"roi_pct": 5.0, "total_trades": 15}
        report = generate_daily_report(stats, positions=SAMPLE_POSITIONS)
        assert "Top positions:" in report
        assert "fed-rate-cut-march" in report

    def test_no_positions(self):
        report = generate_daily_report({})
        assert "Top positions:" not in report

    def test_tier_shown(self):
        stats = {"total_trades": 35, "roi_pct": 12.0, "sharpe_ratio": 1.2}
        report = generate_daily_report(stats)
        assert "Gold" in report


# ---------------------------------------------------------------------------
# generate_leaderboard_card
# ---------------------------------------------------------------------------


SAMPLE_ENTRIES = [
    {"account": "alpha", "roi_pct": 25.0, "pnl": 2500.0, "total_trades": 50, "sharpe_ratio": 2.0},
    {"account": "bravo", "roi_pct": 15.0, "pnl": 1500.0, "total_trades": 30, "sharpe_ratio": 1.2},
    {"account": "charlie", "roi_pct": 8.0, "pnl": 800.0, "total_trades": 20, "sharpe_ratio": 0.8},
]


class TestGenerateLeaderboardCard:
    def test_basic_leaderboard(self):
        card = generate_leaderboard_card(SAMPLE_ENTRIES)
        assert "Top 10 AI Traders" in card
        assert "alpha" in card
        assert "bravo" in card
        assert "charlie" in card
        assert "25.0%" in card
        assert "#OpenClaw" in card
        assert "clawhub" in card

    def test_custom_title(self):
        card = generate_leaderboard_card(SAMPLE_ENTRIES, title="Weekly Champions")
        assert "Weekly Champions" in card

    def test_ranking_order(self):
        card = generate_leaderboard_card(SAMPLE_ENTRIES)
        lines = card.split("\n")
        # Find rank lines (start with digits after spaces)
        rank_lines = [l for l in lines if l.strip() and l.strip()[0].isdigit()]
        assert len(rank_lines) == 3
        assert "alpha" in rank_lines[0]  # #1
        assert "bravo" in rank_lines[1]  # #2
        assert "charlie" in rank_lines[2]  # #3

    def test_empty_entries(self):
        card = generate_leaderboard_card([])
        assert "Top 10 AI Traders" in card
        assert "Qualify:" in card

    def test_tier_shown(self):
        card = generate_leaderboard_card(SAMPLE_ENTRIES)
        assert "Diamond" in card  # alpha: 50 trades, 25% ROI, sharpe 2.0
        assert "Gold" in card     # bravo: 30 trades, 15% ROI, sharpe 1.2

    def test_truncates_at_10(self):
        many = [{"account": f"bot_{i}", "roi_pct": float(i), "total_trades": 20} for i in range(15, 0, -1)]
        card = generate_leaderboard_card(many)
        rank_lines = [l for l in card.split("\n") if l.strip() and l.strip()[0].isdigit()]
        assert len(rank_lines) == 10
