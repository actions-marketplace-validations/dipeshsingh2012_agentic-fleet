"""
Unit tests for pre-commit verification hardening:
1. Taskfile PATH availability check and language fallback
2. Strict test success validation in TestResult
3. Dev agent push abort gate when pre-commit tests fail
"""

from __future__ import annotations

import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from src.event_router import EventRouter
from src.github_client import GitHubClient
from src.test_harness import TestHarness, TestResult
from src.workspace_inspector import WorkspaceInspector


def test_taskfile_fallback_to_backend_pytest_when_task_binary_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """
    When Taskfile.yml exists with a 'test' task, but 'task' binary is NOT installed,
    WorkspaceInspector must fall back to 'pytest -v backend/tests' instead of returning
    a command guaranteed to fail with exit code 127.
    """
    (tmp_path / "Taskfile.yml").write_text("version: '3'\ntasks:\n  test:\n    cmds:\n      - pytest\n")
    backend_tests = tmp_path / "backend" / "tests"
    backend_tests.mkdir(parents=True)
    (tmp_path / "backend" / "requirements.txt").write_text("pytest\nfastapi\n")

    # Simulate 'task' binary not found on system PATH
    monkeypatch.setattr("shutil.which", lambda cmd: None)

    profile = WorkspaceInspector.inspect(tmp_path)
    assert profile.test_command == "pytest -v backend/tests"


def test_test_result_success_and_count_parsing():
    """Validates TestResult __post_init__ parses counts and accurately determines is_success."""
    res_success = TestResult(command="pytest", exit_code=0, duration_seconds=0.5, stdout="10 passed in 0.5s", stderr="")
    assert res_success.is_success is True
    assert res_success.passed_tests == 10
    assert res_success.failed_tests == 0
    assert res_success.total_tests == 10

    res_failure = TestResult(command="pytest", exit_code=1, duration_seconds=0.5, stdout="2 failed, 8 passed", stderr="")
    assert res_failure.is_success is False
    assert res_failure.failed_tests == 2
    assert res_failure.passed_tests == 8

    res_cmd_not_found = TestResult(command="task test", exit_code=127, duration_seconds=0.01, stdout="", stderr="bash: line 1: task: command not found", failed_tests=1, total_tests=1)
    assert res_cmd_not_found.is_success is False


@pytest.mark.asyncio
async def test_dev_agent_aborts_push_when_precommit_tests_fail(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """
    Validates that when pre-commit self-healing fails all iterations,
    handle_dev_agent does NOT push broken code to GitHub and aborts safely.
    """
    backend_dir = tmp_path / "backend" / "app"
    tests_dir = tmp_path / "backend" / "tests"
    backend_dir.mkdir(parents=True)
    tests_dir.mkdir(parents=True)
    (tmp_path / "backend" / "requirements.txt").write_text("fastapi\npytest\n")

    harness = MagicMock(spec=TestHarness)
    harness.cwd = str(tmp_path)
    # Test execution always fails
    harness.run_command = AsyncMock(
        return_value=TestResult(
            command="pytest -v backend/tests",
            exit_code=1,
            duration_seconds=0.2,
            stdout="=== FAILURES ===\ntest_broken FAILED",
            stderr="",
            failed_tests=1,
            total_tests=1,
        )
    )

    router = EventRouter(dry_run=False, test_harness=harness)
    mock_client = MagicMock(spec=GitHubClient)
    mock_client.token = "fake-token"
    mock_client.create_issue_comment = AsyncMock()
    mock_client.create_pull_request = AsyncMock()
    mock_client.get_pull_request = AsyncMock(return_value={"head": {"ref": "feat/123-test"}})
    mock_client.find_existing_pr = AsyncMock(return_value={"number": 16, "head": {"ref": "feat/123-test"}})
    router.github_client = mock_client

    # LLM returns dummy code
    router.llm_runner.generate_response = AsyncMock(
        return_value="```python:backend/app/main.py\n# broken code\n```"
    )

    payload = {
        "action": "created",
        "repository": {"full_name": "dipeshsingh2012/rfpengine"},
        "issue": {"number": 16, "pull_request": {"url": "https://api.github.com/repos/dipeshsingh2012/rfpengine/pulls/16"}},
        "comment": {"body": "@dev-agent fix tests"},
    }

    monkeypatch.setenv("MAX_REMEDIATION_ITERATIONS", "2")
    result = await router.handle_dev_agent("dipeshsingh2012/rfpengine", payload)

    assert result["agent"] == "dev-agent"
    assert result["action"] == "precommit_verification_failed"
    assert "Pre-commit tests failed" in result["error"]

    # Verify git push was NEVER called
    git_push_called = any(
        "git push" in call.args[0] for call in harness.run_command.call_args_list
    )
    assert not git_push_called, "git push should NOT be executed when pre-commit tests fail!"
