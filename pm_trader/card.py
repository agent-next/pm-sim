"""Shareable stats cards for social platforms.

Generates formatted trading performance cards optimized for:
- X/Twitter (280 chars, hashtags, engagement bait)
- Chat apps (Telegram, Discord, WhatsApp — markdown)
- Plain text (fallback)

Templates:
- performance: overall stats summary (default)
- milestone: achievement celebration (e.g. "Hit 50 trades!")
- daily: daily activity report with top movers
"""

from __future__ import annotations


def _roi_icon(roi: float) -> str:
    """Pick emoji based on ROI performance."""
    if roi > 20:
        return "\U0001f525"
    if roi > 10:
        return "\U0001f680"
    if roi > 0:
        return "\U0001f4c8"
    if roi == 0:
        return "\u2796"
    if roi > -10:
        return "\U0001f4c9"
    return "\U0001f480"


def _extract(stats: dict) -> dict:
    """Extract and format common fields from stats dict."""
    roi = stats.get("roi_pct", 0.0)
    pnl = stats.get("pnl", 0.0)
    return {
        "roi": roi,
        "pnl": pnl,
        "total": stats.get("total_value", 0.0),
        "sharpe": stats.get("sharpe_ratio", 0.0),
        "win": stats.get("win_rate", 0.0),
        "trades": stats.get("total_trades", 0),
        "dd": stats.get("max_drawdown", 0.0),
        "fees": stats.get("total_fees", 0.0),
        "starting": stats.get("starting_balance", 0.0),
        "icon": _roi_icon(roi),
        "pnl_sign": "+" if pnl >= 0 else "",
        "roi_sign": "+" if roi >= 0 else "",
    }


def _format_top_positions(positions: list[dict], limit: int = 3) -> list[str]:
    """Format top positions by current value for card display."""
    if not positions:
        return []
    top = sorted(positions, key=lambda p: p.get("current_value", 0), reverse=True)[:limit]
    lines = []
    for p in top:
        slug = p.get("market_slug", "?")
        # Shorten slug for display: "will-trump-win-2024" → "will-trump-win-2024"
        display = slug[:30] + "..." if len(slug) > 30 else slug
        outcome = p.get("outcome", "?").upper()
        pnl = p.get("unrealized_pnl", 0.0)
        sign = "+" if pnl >= 0 else ""
        lines.append(f"{display} ({outcome}) {sign}${pnl:,.0f}")
    return lines


def _detect_milestone(stats: dict) -> str | None:
    """Detect achievements worth celebrating."""
    trades = stats.get("total_trades", 0)
    roi = stats.get("roi_pct", 0.0)
    pnl = stats.get("pnl", 0.0)
    if trades == 1:
        return "First trade executed!"
    if trades == 10:
        return "10 trades milestone!"
    if trades == 50:
        return "50 trades — getting serious!"
    if trades == 100:
        return "100 trades — centurion!"
    if roi >= 50:
        return f"ROI hit {roi:.0f}%!"
    if pnl >= 10000:
        return f"P&L crossed $10k!"
    if pnl >= 5000:
        return f"P&L crossed $5k!"
    if pnl >= 1000:
        return f"P&L crossed $1k!"
    return None


def generate_tweet(
    stats: dict,
    account: str = "default",
    positions: list[dict] | None = None,
) -> str:
    """Generate a tweet-optimized card (< 280 chars).

    Designed for X/Twitter sharing. Compact, eye-catching, with hashtags.
    """
    s = _extract(stats)

    lines = [
        f"{s['icon']} My AI agent {'made' if s['pnl'] >= 0 else 'lost'} ${abs(s['pnl']):,.0f} trading Polymarket",
        "",
        f"{s['roi_sign']}{s['roi']:.1f}% ROI | {s['win'] * 100:.0f}% win rate | {s['trades']} trades",
        "Zero risk. Real prices.",
    ]

    top = _format_top_positions(positions or [])
    if top:
        lines.append("")
        lines.append("Top calls:")
        for t in top:
            lines.append(f"  {t}")

    lines.extend([
        "",
        "Can your agent beat mine?",
        "",
        "#Polymarket #AITrading #OpenClaw",
        "Make your agent trade \u2192 npx clawhub install polymarket-paper-trader",
    ])

    return "\n".join(lines)


def generate_card(
    stats: dict,
    account: str = "default",
    positions: list[dict] | None = None,
) -> str:
    """Generate a chat-optimized card with markdown.

    For Telegram, Discord, Slack — supports bold/italic.
    """
    s = _extract(stats)

    lines = [
        f"{s['icon']} *Polymarket Paper Trading*",
        "",
        f"ROI: *{s['roi_sign']}{s['roi']:.1f}%* | Win Rate: *{s['win'] * 100:.0f}%*",
        f"Trades: *{s['trades']}* | P&L: *{s['pnl_sign']}${s['pnl']:,.2f}*",
        f"Portfolio: *${s['total']:,.2f}* (started ${s['starting']:,.0f})",
    ]

    top = _format_top_positions(positions or [])
    if top:
        lines.append("")
        lines.append("*Top positions:*")
        for t in top:
            lines.append(f"  {t}")

    lines.extend([
        "",
        "Can your agent beat mine?",
        "",
        "`Make your agent trade \u2192 npx clawhub install polymarket-paper-trader`",
    ])

    return "\n".join(lines)


def _tier(stats: dict) -> str:
    """Determine leaderboard tier."""
    trades = stats.get("total_trades", 0)
    roi = stats.get("roi_pct", 0.0)
    sharpe = stats.get("sharpe_ratio", 0.0)
    if trades >= 50 and roi > 20 and sharpe > 1.5:
        return "\U0001f48e Diamond"
    if trades >= 30 and roi > 10 and sharpe > 1.0:
        return "\U0001f947 Gold"
    if trades >= 20 and roi > 5:
        return "\U0001f948 Silver"
    if trades >= 10:
        return "\U0001f949 Bronze"
    return "\u2014 Unranked"


def generate_pk_card(
    stats_a: dict, name_a: str,
    stats_b: dict, name_b: str,
) -> str:
    """Generate a head-to-head PK comparison card for X/Twitter."""
    a = _extract(stats_a)
    b = _extract(stats_b)
    tier_a = _tier(stats_a)
    tier_b = _tier(stats_b)

    # Determine winner by ROI
    if a["roi"] > b["roi"]:
        verdict = f"{name_a} wins"
    elif b["roi"] > a["roi"]:
        verdict = f"{name_b} wins"
    else:
        verdict = "Tie"

    lines = [
        "\u2694\ufe0f AI Trader PK \u2014 Head to Head",
        "",
        f"  {name_a:>12} vs {name_b}",
        f"  ROI:   {a['roi_sign']}{a['roi']:.1f}%  vs  {b['roi_sign']}{b['roi']:.1f}%",
        f"  Sharpe: {a['sharpe']:.2f}   vs  {b['sharpe']:.2f}",
        f"  Win:    {a['win'] * 100:.0f}%     vs  {b['win'] * 100:.0f}%",
        f"  Trades: {a['trades']}      vs  {b['trades']}",
        f"  Tier:   {tier_a} vs {tier_b}",
        "",
        f"Winner: {verdict} {a['icon'] if a['roi'] >= b['roi'] else b['icon']}",
        "",
        "Who's the better trader? \U0001f914",
        "",
        "#Polymarket #AITrading #OpenClaw",
        "Make your agent trade \u2192 npx clawhub install polymarket-paper-trader",
    ]

    return "\n".join(lines)


def generate_card_plain(
    stats: dict,
    account: str = "default",
    positions: list[dict] | None = None,
) -> str:
    """Generate a plain-text card (no markdown)."""
    s = _extract(stats)

    lines = [
        f"{s['icon']} Polymarket Paper Trading",
        "",
        f"  ROI:       {s['roi_sign']}{s['roi']:.1f}%",
        f"  Win Rate:  {s['win'] * 100:.0f}%",
        f"  Trades:    {s['trades']}",
        f"  P&L:       {s['pnl_sign']}${s['pnl']:,.2f}",
        f"  Portfolio: ${s['total']:,.2f}",
    ]

    top = _format_top_positions(positions or [])
    if top:
        lines.append("")
        lines.append("  Top positions:")
        for t in top:
            lines.append(f"    {t}")

    lines.extend([
        "",
        "Can your agent beat mine?",
        "",
        "Make your agent trade \u2192 npx clawhub install polymarket-paper-trader",
    ])

    return "\n".join(lines)


def generate_milestone_tweet(stats: dict, milestone: str | None = None) -> str:
    """Generate a milestone celebration tweet.

    Auto-detects milestone if not provided.
    """
    s = _extract(stats)
    ms = milestone or _detect_milestone(stats) or f"{s['trades']} trades and counting"

    lines = [
        f"\U0001f3c6 {ms}",
        "",
        f"${abs(s['pnl']):,.0f} {'profit' if s['pnl'] >= 0 else 'loss'} | {s['roi_sign']}{s['roi']:.1f}% ROI | {s['win'] * 100:.0f}% win rate",
        "",
        f"Tier: {_tier(stats)}",
        "Zero risk. Real Polymarket prices.",
        "",
        "Can your agent beat mine?",
        "",
        "#Polymarket #AITrading #OpenClaw",
        "Make your agent trade \u2192 npx clawhub install polymarket-paper-trader",
    ]

    return "\n".join(lines)


def generate_leaderboard_card(entries: list[dict], title: str = "Top 10 AI Traders") -> str:
    """Generate a leaderboard ranking card from sorted entries.

    Each entry should have: account, roi_pct, pnl, total_trades, sharpe_ratio.
    Entries should be pre-sorted by ranking criteria (e.g. ROI desc).
    """
    lines = [
        f"\U0001f3c6 {title}",
        "",
        "  #  Account         ROI      P&L     Trades  Tier",
        "  " + "-" * 52,
    ]

    for i, e in enumerate(entries[:10], 1):
        name = e.get("account", "?")[:12]
        roi = e.get("roi_pct", 0.0)
        pnl = e.get("pnl", 0.0)
        trades = e.get("total_trades", 0)
        tier = _tier(e)
        sign = "+" if roi >= 0 else ""
        lines.append(
            f"  {i:>2}  {name:<14} {sign}{roi:>5.1f}%  ${pnl:>8,.0f}  {trades:>6}  {tier}"
        )

    lines.extend([
        "",
        "Qualify: 10+ trades | Ranked by ROI%",
        "",
        "#Polymarket #AITrading #OpenClaw",
        "Make your agent trade \u2192 npx clawhub install polymarket-paper-trader",
    ])

    return "\n".join(lines)


def generate_daily_report(
    stats: dict,
    positions: list[dict] | None = None,
    account: str = "default",
) -> str:
    """Generate a daily report card showing current state and top positions."""
    s = _extract(stats)
    tier = _tier(stats)

    lines = [
        f"\U0001f4ca Daily Report — {account}",
        "",
        f"Portfolio: ${s['total']:,.2f} ({s['roi_sign']}{s['roi']:.1f}%)",
        f"P&L: {s['pnl_sign']}${s['pnl']:,.2f} | {s['trades']} trades",
        f"Sharpe: {s['sharpe']:.2f} | Win: {s['win'] * 100:.0f}%",
        f"Tier: {tier}",
    ]

    top = _format_top_positions(positions or [])
    if top:
        lines.append("")
        lines.append("Top positions:")
        for t in top:
            lines.append(f"  {t}")

    lines.extend([
        "",
        "#Polymarket #AITrading #OpenClaw",
        "Make your agent trade \u2192 npx clawhub install polymarket-paper-trader",
    ])

    return "\n".join(lines)
