"""Public /oc bot wiring — asserts the shipped workflow + opencode.json.

Reads the real files on disk. Does not reimplement OpenCode or mock YAML
into a parallel config.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github" / "workflows" / "opencode.yml"
CONFIG = ROOT / "opencode.json"


def _workflow_text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def _opencode_config() -> dict:
    return json.loads(CONFIG.read_text(encoding="utf-8"))


def _job_block(name: str) -> str:
    text = _workflow_text()
    match = re.search(rf"^  {re.escape(name)}:\n(.*?)(?=^  [a-z0-9_-]+:|\Z)", text, re.M | re.S)
    assert match, f"job {name!r} missing from {WORKFLOW}"
    return match.group(0)


class TestPublicBotWiring:
    def test_shipped_files_exist(self) -> None:
        assert WORKFLOW.is_file(), WORKFLOW
        assert CONFIG.is_file(), CONFIG

    def test_provider_is_freeinference_with_qwen(self) -> None:
        cfg = _opencode_config()
        provider = cfg["provider"]["freeinference"]
        assert provider["options"]["baseURL"] == "https://freeinference.org/v1"
        assert "FREEINFERENCE_API_KEY" in provider["env"]
        assert "{env:FREEINFERENCE_API_KEY}" in provider["options"]["apiKey"]
        assert "qwen3.6-35b" in provider["models"]

    def test_workflow_model_is_freeinference_qwen(self) -> None:
        text = _workflow_text()
        assert re.search(r"^  OPENCODE_MODEL:\s*freeinference/qwen3.6-35b\s*$", text, re.M)
        assert "model: ${{ env.OPENCODE_MODEL }}" in text

    def test_comment_job_triggers_oc_and_opencode(self) -> None:
        text = _workflow_text()
        assert 'mentions: "/opencode,/oc"' in text
        assert "issue_comment:" in text

    def test_jobs_pass_freeinference_secret(self) -> None:
        text = _workflow_text()
        assert text.count("FREEINFERENCE_API_KEY: ${{ secrets.FREEINFERENCE_API_KEY }}") >= 3
        assert "secrets.OPENCODE_API_KEY" not in text

    def test_preflight_fails_closed_without_key(self) -> None:
        """Missing/empty secret must fail the job before OpenCode starts."""
        text = _workflow_text()
        assert "name: Preflight FreeInference key" in text
        assert "secrets.FREEINFERENCE_API_KEY" in text
        assert "FREEINFERENCE_API_KEY is empty" in text


class TestClosedLoopWiring:
    """Issue → gated implement → review → merge-ready. No self-merge."""

    def test_implement_is_gated_not_every_issue(self) -> None:
        block = _job_block("implement")
        assert "bot:implement" in block
        assert "/oc implement" in block
        triage = _job_block("triage")
        assert "deepseek-v4-flash" not in triage
        assert "Do not write code or push" in triage

    def test_implement_uses_flash_not_qwen(self) -> None:
        block = _job_block("implement")
        assert "freeinference/deepseek-v4-flash" in block
        comment = _job_block("comment")
        assert "freeinference/deepseek-v4-flash" not in comment

    def test_review_cannot_write_repo_contents(self) -> None:
        block = _job_block("review")
        assert re.search(r"contents:\s*read", block)
        assert not re.search(r"contents:\s*write", block)
        assert "Do not push commits" in block

    def test_implement_push_is_not_github_token_only(self) -> None:
        block = _job_block("implement")
        assert "GITHUB_TOKEN: ${{ secrets.OC_PUSH_TOKEN }}" in block
        assert "GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}" not in block

    def test_no_self_merge_on_implement_or_review(self) -> None:
        for name in ("implement", "review"):
            block = _job_block(name)
            assert "gh pr merge" not in block
            assert "pulls/" not in block or "merge" not in block
            assert "/merge" not in block

    def test_merge_ready_signals_without_merging(self) -> None:
        block = _job_block("merge-ready")
        assert "merge-ready" in block
        assert "Python 3.10" in block
        assert "gh pr merge" not in block
        assert "/merge" not in block
