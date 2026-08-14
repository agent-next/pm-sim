# Changelog

All notable changes to `polymarket-paper-trader` are documented here.

## Unreleased

### Added
- OpenCode GitHub Action on `/oc` / `/opencode`, new issues, and non-draft PRs. Public bot model is FreeInference `qwen3.6-35b` (`opencode.json` + `FREEINFERENCE_API_KEY`).
- Preflight step fails the `/oc` jobs when `FREEINFERENCE_API_KEY` is empty; `tests/test_opencode_bot.py` pins the shipped workflow and provider.
- Closed loop (no self-merge): `implement` only on `bot:implement` or `/oc implement` (`deepseek-v4-flash`); after a `GITHUB_TOKEN` push it `gh workflow run Tests` so checks still fire (org disables deploy keys; no PAT API). `review` stays comment-only; `merge-ready` also accepts Tests from `workflow_dispatch`.
- merge-ready queries required checks via `gh api --jq` + `env.CTX` (not illegal `--jq --arg`); wiring tests execute the shipped script.
- Review hardening: `implement` gated to trusted actors (collaborator write+ via permission API) and the exact `/oc implement` command form, including PR review comments; `merge-ready` matches the open PR whose head is the tested SHA (no stale-green labels), skips duplicate labeling, fails loud if a required check context disappears, and gains `issues: write` for the label API; `comment` job drops `contents: write`; bot jobs get `timeout-minutes` and stop logging secret lengths.

## [0.1.8] - 2026-08-14

### Fixed
- **CRITICAL**: Pin `mcp>=1.28,<2` — MCP SDK 2.0.0 (2026-07-28) removed `mcp.server.fastmcp`, breaking every fresh install of the MCP server (`pm-trader-mcp` failed with `ModuleNotFoundError`). SDK 1.x stays on security-maintenance per upstream; migration to `MCPServer` tracked for a future release.
- Sync stale version fields: `server.json` 0.1.6 → 0.1.8, `SKILL.md` 0.1.7 → 0.1.8

## [0.1.7] - 2026-03-01

### Added
- skills.sh support — skill discoverable via `npx skills add agent-next/polymarket-paper-trader`
- Market discovery: `get_tags` — list all market categories/tags (cached 5 min)
- Market discovery: `get_markets_by_tag` — filter markets by tag slug
- Market discovery: `get_event` — fetch event details (group of related markets, cached 5 min)
- `cancel_all_orders` — batch cancel all pending limit orders at once
- CLI: `markets tags`, `markets --tag <slug>`, `markets event <slug>`, `orders cancel-all`
- MCP tool count: 26 → 30

### Security
- Add data trust boundaries to SKILL.md — mark Polymarket API data as untrusted, prevent indirect prompt injection via market content
- **CRITICAL**: Block arbitrary code execution via `importlib.import_module` — strategy loading now restricted to allowlisted packages (`examples.*`, `tests.test_benchmark.*`)
- **HIGH**: Prevent path traversal via account parameter — reject `..`, `/`, `\` in account names (MCP + CLI)
- **HIGH**: Harden CI/CD — pin all GitHub Actions to full commit SHAs, add restrictive top-level `permissions:`, pin `clawhub@0.7.0`
- **HIGH**: Bump `setuptools` minimum to `>=78.1.1` (CVE fix)
- **MEDIUM**: Sanitize error messages — hide internal paths from unexpected exceptions
- **MEDIUM**: Cap API result limits (`MAX_RESULTS=100`) to prevent resource exhaustion
- **MEDIUM**: Validate backtest `data_path` against allowed directories

## [0.1.6] - 2026-03-01

### Fixed
- Stop shipping `examples` as top-level package in PyPI wheel — was polluting user site-packages with a generic name

### Changed
- Publish workflow now runs full test suite (Python 3.10–3.13, 100% coverage) before publishing
- ClawHub publish automated in GitHub Actions
- GitHub Release auto-created with generated notes on tag push

### Added
- MCP Registry `server.json` for future registry submission
- README ownership verification tag for MCP Registry

## [0.1.5] - 2026-03-01

### Added
- PyPI publishing via GitHub Actions (`publish.yml`)
- LICENSE file (MIT)
- PyPI badge in README
- `py.typed` marker (PEP 561)
- `[project.urls]` in pyproject.toml (Homepage, Repository, Issues)
- Package metadata: authors, keywords, classifiers

### Changed
- Install section in README now shows `pip install` first

## [0.1.4] - 2026-03-01

### Added
- GitHub Actions CI test workflow (Python 3.10–3.13, 100% coverage gate)
- PK battle system — head-to-head strategy comparison
- Leaderboard card generator and MCP tool
- `share_content` MCP tool with platform-specific templates
- Strategy examples: momentum, mean reversion, limit grid
- Stats cards with top positions and template variants

### Changed
- Rewrite all user-facing copy for viral distribution
- Rename `pm_sim` → `pm_trader` package
- Extract `card.py` constants/helpers, eliminate duplication

### Fixed
- Negative P&L copy semantics
- False 280-char claim in stats cards
- `pragma: no cover` on `__main__.py` for CI

## [0.1.3] - 2026-03-01

### Added
- ClawHub skill for OpenClaw integration
- SKILL.md agent behavior program
- 26 MCP tools (up from 20)

## [0.1.2] - 2026-03-01

### Added
- GTC/GTD limit order management
- Backtesting engine with historical price replay
- Multi-outcome (neg-risk) market support
- Performance analytics (Sharpe, win rate, drawdown)
- CSV/JSON export for trades and positions
- Benchmarking harness for strategies
- Multi-account support

### Fixed
- Limit orders enforce price ceiling/floor
- GTD expiry validation
- `watch_prices` raises ValueError for invalid outcomes
- `check_orders` rejects permanently unfillable orders

## [0.1.1] - 2026-03-01

### Added
- MCP server exposing 20 tools for AI agents
- Click CLI with JSON envelope output
- 1:1 faithful order book fill engine (FOK/FAK)
- Polymarket HTTP client (Gamma + CLOB API)
- Trade execution engine (buy/sell/resolve)
- SQLite database with WAL mode
- E2E tests against live Polymarket API
