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
