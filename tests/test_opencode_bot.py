"""Public /oc bot wiring — asserts the shipped workflow + opencode.json.

Reads the real files on disk. Does not reimplement OpenCode or mock YAML
into a parallel config.
"""
from __future__ import annotations

import json
import os
import re
import stat
import subprocess
from pathlib import Path

import pytest

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
        for job in ("comment", "triage", "review"):
            block = _job_block(job)
            assert "name: Preflight FreeInference key" in block, job
            assert "FREEINFERENCE_API_KEY: ${{ secrets.FREEINFERENCE_PUBLIC_KEY }}" in block, job
        implement = _job_block("implement")
        assert "FREEINFERENCE_API_KEY: ${{ secrets.FREEINFERENCE_API_KEY }}" in implement
        assert "secrets.OPENCODE_API_KEY" not in text

    def test_preflight_fails_closed_without_key(self) -> None:
        """Missing/empty secret must fail the job before OpenCode starts."""
        text = _workflow_text()
        assert text.count("name: Preflight FreeInference key") == 4
        assert "FREEINFERENCE_API_KEY is empty" in text
        assert "secrets.FREEINFERENCE_PUBLIC_KEY" in text
        # Local actions resolve from the checked-out tree; on PR review-comment
        # events that tree is PR-controlled, so preflight must stay inline.
        assert "uses: ./.github/actions/" not in text


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
        text = _workflow_text()
        block = _job_block("implement")
        assert "model: ${{ env.OPENCODE_IMPLEMENT_MODEL }}" in block
        assert "OPENCODE_IMPLEMENT_MODEL: freeinference/deepseek-v4-flash" in text
        comment = _job_block("comment")
        assert "model: ${{ env.OPENCODE_MODEL }}" in comment

    def test_review_cannot_write_repo_contents(self) -> None:
        block = _job_block("review")
        assert re.search(r"contents:\s*read", block)
        assert not re.search(r"contents:\s*write", block)
        assert "Do not push commits" in block

    def test_implement_dispatches_tests_after_github_token_push(self) -> None:
        """GITHUB_TOKEN pushes skip Tests; implement must dispatch them."""
        block = _job_block("implement")
        assert "name: Dispatch Tests after GITHUB_TOKEN push" in block
        assert "gh workflow run Tests" in block
        tests = (ROOT / ".github" / "workflows" / "test.yml").read_text(encoding="utf-8")
        assert "workflow_dispatch:" in tests

    def test_implement_passes_freeinference_to_preflight(self) -> None:
        block = _job_block("implement")
        assert "name: Preflight FreeInference key" in block
        assert "FREEINFERENCE_API_KEY: ${{ secrets.FREEINFERENCE_API_KEY }}" in block

    def test_implement_gated_to_trusted_actors(self) -> None:
        block = _job_block("implement")
        assert "name: Gate to trusted actors" in block
        assert "collaborators/${ACTOR}/permission" in block
        assert "admin|maintain|write" in block

    def test_implement_command_and_comment_exclusion_aligned(self) -> None:
        implement = _job_block("implement")
        comment = _job_block("comment")
        # Review comments must route to implement, not fall between both jobs.
        assert "pull_request_review_comment" in implement
        for block in (implement, comment):
            assert "startsWith(github.event.comment.body, '/oc implement')" in block

    def test_comment_job_has_no_contents_write(self) -> None:
        block = _job_block("comment")
        assert not re.search(r"contents:\s*write", block)
        assert re.search(r"issues:\s*write", block)
        assert re.search(r"pull-requests:\s*write", block)

    def test_no_self_merge_on_implement_or_review(self) -> None:
        for name in ("implement", "review"):
            block = _job_block(name)
            assert "gh pr merge" not in block
            assert "pulls/" not in block
            assert "/merge" not in block

    def test_merge_ready_signals_without_merging(self) -> None:
        block = _job_block("merge-ready")
        assert "Python 3.10" in block
        assert "gh pr merge" not in block
        assert "/merge" not in block
        assert "--remove-label merge-ready" in block
        assert "workflow_dispatch" in block

    def test_merge_ready_gh_jq_is_a_single_filter(self) -> None:
        script = _merge_ready_script()
        assert "--jq --arg" not in script
        assert "env.CTX" in script
        assert script.count("--jq") >= 2

    def test_merge_ready_contexts_match_tests_matrix(self) -> None:
        matrix_text = (ROOT / ".github" / "workflows" / "test.yml").read_text(encoding="utf-8")
        match = re.search(r"python-version:\s*\[([^\]]+)\]", matrix_text)
        assert match, "python-version matrix not found in test.yml"
        versions = [v.strip().strip('"') for v in match.group(1).split(",")]
        assert versions, "empty python-version matrix"
        script = _merge_ready_script()
        for version in versions:
            assert f"\"Python {version}\"" in script, f"Python {version} not gated by merge-ready"


OPEN_PR = {"number": 23, "state": "open", "head": {"sha": "deadbeef"}}
PYTHON_CONTEXTS = ("Python 3.10", "Python 3.11", "Python 3.12", "Python 3.13")
GREEN_CHECKS = [{"name": n, "conclusion": "success"} for n in PYTHON_CONTEXTS]
GREEN_STATUSES = [{"context": c, "state": "success"} for c in PYTHON_CONTEXTS]

STUB_GH = """#!/usr/bin/env python3
import json, os, subprocess, sys
args = sys.argv[1:]
log = os.environ['GH_STUB_LOG']
with open(log, 'a', encoding='utf-8') as fh:
    fh.write(' '.join(args) + '\\n')
if args[:1] == ['api']:
    url = args[1]
    jq = args[args.index('--jq') + 1] if '--jq' in args else None
    if url.endswith('/pulls'):
        data = json.loads(os.environ['GH_STUB_PULLS'])
    elif url.endswith('/status'):
        data = {'statuses': json.loads(os.environ['GH_STUB_STATUSES'])}
    elif url.endswith('/check-runs'):
        data = {'check_runs': json.loads(os.environ['GH_STUB_CHECK_RUNS'])}
    else:
        sys.exit(2)
    raw = json.dumps(data)
    if jq is None:
        print(raw)
        raise SystemExit(0)
    r = subprocess.run(['jq', '-r', jq], input=raw, text=True, capture_output=True)
    sys.stdout.write(r.stdout)
    raise SystemExit(r.returncode)
if args[:2] == ['pr', 'view']:
    jq = args[args.index('--jq') + 1] if '--jq' in args else None
    if jq and 'headRefOid' in jq:
        sys.stdout.write(os.environ.get('GH_STUB_PR_HEAD', 'deadbeef') + '\\n')
    else:
        sys.stdout.write(os.environ.get('GH_STUB_PR_LABELS', ''))
    raise SystemExit(0)
raise SystemExit(0)
"""


class TestMergeReadyScript:
    """Execute the shipped merge-ready shell against a stub gh + fixture JSON."""

    def test_labels_when_python_checks_green(self, tmp_path: Path) -> None:
        proc, logged = _run_merge_ready(tmp_path, [OPEN_PR], [], GREEN_CHECKS)
        assert proc.returncode == 0, proc.stderr + proc.stdout
        assert "--add-label merge-ready" in logged
        assert "pr comment" in logged
        assert "pr merge" not in logged
        assert "/status" not in logged

    def test_labels_via_status_api_fallback_when_check_runs_lack_contexts(self, tmp_path: Path) -> None:
        unrelated = [{"name": "clawhub-scan", "conclusion": "success"}]
        proc, logged = _run_merge_ready(tmp_path, [OPEN_PR], GREEN_STATUSES, unrelated)
        assert proc.returncode == 0, proc.stderr + proc.stdout
        assert "--add-label merge-ready" in logged
        assert "/status" in logged

    def test_no_label_when_a_python_check_failed(self, tmp_path: Path) -> None:
        checks = [dict(c, conclusion="failure") if c["name"] == "Python 3.11" else c for c in GREEN_CHECKS]
        proc, logged = _run_merge_ready(tmp_path, [OPEN_PR], [], checks)
        assert proc.returncode == 0, proc.stderr
        assert "--add-label merge-ready" not in logged
        assert "pr comment" not in logged

    def test_fails_loud_when_required_check_missing_entirely(self, tmp_path: Path) -> None:
        proc, logged = _run_merge_ready(tmp_path, [OPEN_PR], [], GREEN_CHECKS[:3])
        assert proc.returncode == 1, proc.stdout
        assert "missing entirely" in proc.stderr
        assert "--add-label merge-ready" not in logged

    def test_no_label_when_pr_head_moved(self, tmp_path: Path) -> None:
        stale = {"number": 23, "state": "open", "head": {"sha": "cafebabe"}}
        proc, logged = _run_merge_ready(tmp_path, [stale], [], GREEN_CHECKS)
        assert proc.returncode == 0, proc.stderr
        assert "No open PR" in proc.stdout
        assert "--add-label merge-ready" not in logged

    def test_skips_when_label_already_present(self, tmp_path: Path) -> None:
        proc, logged = _run_merge_ready(tmp_path, [OPEN_PR], [], GREEN_CHECKS, pr_labels="merge-ready\n")
        assert proc.returncode == 0, proc.stderr
        assert "--add-label merge-ready" not in logged
        assert "pr comment" not in logged

    def test_no_label_when_check_pending(self, tmp_path: Path) -> None:
        checks = [dict(c, conclusion=None) if c["name"] == "Python 3.11" else c for c in GREEN_CHECKS]
        proc, logged = _run_merge_ready(tmp_path, [OPEN_PR], [], checks)
        assert proc.returncode == 0, proc.stderr
        assert "check Python 3.11 -> pending" in proc.stdout
        assert "/status" not in logged
        assert "--add-label merge-ready" not in logged

    def test_no_label_when_head_moves_before_labeling(self, tmp_path: Path) -> None:
        proc, logged = _run_merge_ready(tmp_path, [OPEN_PR], [], GREEN_CHECKS, pr_head="cafebabe")
        assert proc.returncode == 0, proc.stderr
        assert "head moved" in proc.stdout
        assert "--add-label merge-ready" not in logged

    def test_removes_label_when_tests_conclusion_failure(self, tmp_path: Path) -> None:
        proc, logged = _run_merge_ready(
            tmp_path, [OPEN_PR], [], GREEN_CHECKS, pr_labels="merge-ready\n", conclusion="failure"
        )
        assert proc.returncode == 0, proc.stderr
        assert "--remove-label merge-ready" in logged
        assert "--add-label merge-ready" not in logged

    def test_failure_without_label_exits_cleanly(self, tmp_path: Path) -> None:
        proc, logged = _run_merge_ready(tmp_path, [OPEN_PR], [], GREEN_CHECKS, conclusion="cancelled")
        assert proc.returncode == 0, proc.stderr
        assert "--remove-label" not in logged
        assert "--add-label" not in logged

    def test_failure_when_pr_head_moved_does_not_strip(self, tmp_path: Path) -> None:
        """A delayed failure for an old SHA must not touch a moved-on PR's label."""
        stale = {"number": 23, "state": "open", "head": {"sha": "cafebabe"}}
        proc, logged = _run_merge_ready(
            tmp_path, [stale], [], [], pr_labels="merge-ready\n", conclusion="failure"
        )
        assert proc.returncode == 0, proc.stderr
        assert "No open PR" in proc.stdout
        assert "--remove-label" not in logged

    def test_exits_cleanly_when_no_pr_for_sha(self, tmp_path: Path) -> None:
        proc, logged = _run_merge_ready(tmp_path, [], [], [])
        assert proc.returncode == 0, proc.stderr
        assert "No open PR" in proc.stdout
        assert "--add-label" not in logged


def _run_merge_ready(
    tmp_path: Path,
    pulls: list,
    statuses: list,
    check_runs: list,
    pr_labels: str = "",
    pr_head: str = "deadbeef",
    conclusion: str = "success",
) -> tuple[subprocess.CompletedProcess[str], str]:
    script = _merge_ready_script()
    stub_log = tmp_path / "gh.log"
    stub = tmp_path / "gh"
    stub.write_text(STUB_GH, encoding="utf-8")
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    env = os.environ.copy()
    env["PATH"] = f"{tmp_path}{os.pathsep}{env.get('PATH', '')}"
    env["GH_STUB_LOG"] = str(stub_log)
    env["GH_STUB_PULLS"] = json.dumps(pulls)
    env["GH_STUB_STATUSES"] = json.dumps(statuses)
    env["GH_STUB_CHECK_RUNS"] = json.dumps(check_runs)
    env["GH_STUB_PR_LABELS"] = pr_labels
    env["GH_STUB_PR_HEAD"] = pr_head
    env["GH_TOKEN"] = "test"
    env["SHA"] = "deadbeef"
    env["CONCLUSION"] = conclusion
    env["REPO"] = "agent-next/polymarket-paper-trader"
    proc = subprocess.run(
        ["bash", "-c", script],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
    )
    return proc, stub_log.read_text(encoding="utf-8")


def _run_block(step_name: str) -> str:
    text = _workflow_text()
    match = re.search(
        rf"      - name: {re.escape(step_name)}\n"
        r"(?:        .*\n)*?"
        r"        run: \|\n"
        r"((?:          .*\n?)+)",
        text,
    )
    assert match, f"run block for {step_name!r} not found"
    return "\n".join(line[10:] for line in match.group(1).splitlines()) + "\n"


class TestPreflightFailsClosed:
    @pytest.mark.parametrize("var_name", ["FREEINFERENCE_API_KEY"])
    def test_empty_secret_exits_nonzero(self, var_name: str) -> None:
        proc = subprocess.run(
            ["bash", "-c", _run_block("Preflight FreeInference key")],
            env={"PATH": "/usr/bin:/bin", var_name: ""},
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert proc.returncode != 0, (var_name, proc.stdout, proc.stderr)
        assert f"{var_name} is empty" in proc.stderr


def _merge_ready_script() -> str:
    return _run_block("Reconcile merge-ready label with Tests result")
