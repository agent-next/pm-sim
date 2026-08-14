"""Meta-consistency guards: version strings and doc counts must not drift.

The 0.1.8 release existed because the same version string lived in five
hand-edited fields and drifted (server.json sat two releases behind). These
tests pin every copy to the installed package version so the release gate
fails before a mismatched artifact ships.
"""
from __future__ import annotations

import filecmp
import json
import re
from pathlib import Path

from importlib.metadata import version as pkg_version

ROOT = Path(__file__).resolve().parent.parent


def _installed_version() -> str:
    return pkg_version("polymarket-paper-trader")


def _frontmatter_version(path: Path) -> str:
    match = re.search(r"^version:\s*(\S+)$", path.read_text(encoding="utf-8"), re.MULTILINE)
    assert match, f"no version field in {path}"
    return match.group(1)


def _tool_names() -> list[str]:
    src = (ROOT / "pm_trader" / "mcp_server.py").read_text(encoding="utf-8")
    return sorted(re.findall(r"@mcp\.tool\(\)\s*\ndef\s+([a-z_]+)\(", src))


class TestVersionConsistency:
    def test_server_json_matches_installed(self) -> None:
        data = json.loads((ROOT / "server.json").read_text(encoding="utf-8"))
        assert data["version"] == _installed_version()
        assert data["packages"][0]["version"] == _installed_version()

    def test_skill_frontmatter_matches_installed(self) -> None:
        for rel in ("skill/polymarket-paper-trader/SKILL.md", ".claude/skills/polymarket-paper-trader/SKILL.md"):
            assert _frontmatter_version(ROOT / rel) == _installed_version(), rel

    def test_changelog_latest_heading_matches_installed(self) -> None:
        text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        match = re.search(r"^## \[(\S+)\] - ", text, re.MULTILINE)
        assert match, "no release heading in CHANGELOG.md"
        assert match.group(1) == _installed_version()

    def test_skill_copies_are_identical(self) -> None:
        assert filecmp.cmp(
            ROOT / "skill/polymarket-paper-trader/SKILL.md",
            ROOT / ".claude/skills/polymarket-paper-trader/SKILL.md",
            shallow=False,
        )


class TestDocCounts:
    def test_readme_tool_table_matches_registered_tools(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        table = re.search(r"### MCP tools\n\n.*?\n\n", readme, re.DOTALL)
        assert table, "MCP tools table not found in README.md"
        rows = re.findall(r"^\| `([a-z_]+)` ", table.group(0), re.MULTILINE)
        assert sorted(rows) == _tool_names()
