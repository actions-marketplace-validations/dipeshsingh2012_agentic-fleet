"""Unit & regression tests for Phase 1 (Relevance, Canonical TaskSpec, and Pre-implementation Gates).
"""

from __future__ import annotations

import pytest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from src.task_spec import TaskSpec, TaskSpecError
from src.design_gate import (
    parse_architect_verdict,
    DESIGN_APPROVED,
    CHANGES_REQUESTED,
    INVALID,
)
from src.event_router import EventRouter
from src.test_harness import TestHarness


def test_task_spec_parsing_and_formatting():
    payload = {
        "initiative_id": "init-docs-1",
        "title": "Document LectureScribe Agentic Fleet MCP round-trip",
        "task": "Author complete developer documentation for MCP integration.",
        "acceptance_criteria": [
            "Given a client payload, when routed, then criteria survive intact.",
            "Given an architect review, then it audits all criteria.",
        ],
        "tenant_id": "lecturescribe",
        "request_id": "req-12345",
    }
    spec = TaskSpec.from_client_payload(payload)
    assert spec.initiative_id == "init-docs-1"
    assert spec.title == "Document LectureScribe Agentic Fleet MCP round-trip"
    assert len(spec.acceptance_criteria) == 2
    assert spec.tenant_id == "lecturescribe"
    assert spec.request_id == "req-12345"

    body = spec.to_issue_body()
    assert "Author complete developer documentation" in body
    assert "## Acceptance Criteria" in body
    assert "1. Given a client payload" in body
    assert "2. Given an architect review" in body


def test_task_spec_missing_required_fields_fails():
    with pytest.raises(TaskSpecError, match="initiative_id"):
        TaskSpec.from_client_payload({"title": "Some feature"})

    with pytest.raises(TaskSpecError, match="title"):
        TaskSpec.from_client_payload({"id": "init-1"})


def test_parse_architect_verdict_strict_compliance():
    # Valid approvals
    assert parse_architect_verdict("## Review\nDECISION: DESIGN_APPROVED\nAll good.") == DESIGN_APPROVED
    assert parse_architect_verdict("DECISION: DESIGN_APPROVED ✅") == DESIGN_APPROVED

    # Valid changes requested
    assert parse_architect_verdict("## Review\nDECISION: CHANGES_REQUESTED ⚠️\nMissing tests.") == CHANGES_REQUESTED

    # Casual words must NOT approve (fails closed)
    assert parse_architect_verdict("The design is APPROVED and looks LGTM.") == INVALID
    assert parse_architect_verdict("I think this is PASSED.") == INVALID
    assert parse_architect_verdict("") == INVALID
    assert parse_architect_verdict("No explicit verdict given.") == INVALID

    # Contradictory decisions
    contradictory = "DECISION: DESIGN_APPROVED\nDECISION: CHANGES_REQUESTED"
    assert parse_architect_verdict(contradictory) == INVALID


@pytest.mark.asyncio
async def test_repository_dispatch_invalid_client_payload(tmp_path: Path):
    harness = TestHarness(cwd=str(tmp_path))
    router = EventRouter(dry_run=True, test_harness=harness)

    payload = {
        "action": "mcp_start_dev",
        "repository": {"full_name": "test/repo"},
        "client_payload": {
            # Missing title and initiative_id
            "feedback": "Do something vague"
        },
    }
    result = await router.route_event("repository_dispatch", payload)
    assert result["status"] == "error"
    assert "Invalid client_payload" in result["reason"]


@pytest.mark.asyncio
async def test_architect_gate_halts_pipeline_on_changes_requested(tmp_path: Path):
    harness = TestHarness(cwd=str(tmp_path))
    router = EventRouter(dry_run=False, test_harness=harness)
    (tmp_path / "backend").mkdir(parents=True)

    payload = {
        "action": "mcp_start_dev",
        "repository": {"full_name": "test/repo"},
        "client_payload": {
            "id": "init-gate-test",
            "title": "Strict Architect Gate Test",
            "task": "Test gate failure",
            "acceptance_criteria": ["Criteria 1 must be satisfied"],
        },
    }

    # Mock LLM responses:
    # dev_design authoring -> returns dummy design
    # architect-agent -> returns CHANGES_REQUESTED repeatedly
    async def mock_generate(prompt, user_input, dry_run=False, tier="pro"):
        if "Principal Architect Design Review" in prompt or "architect-agent" in prompt:
            return "## Architect Review\nDECISION: CHANGES_REQUESTED ⚠️\nCriteria 1 was not addressed in design."
        return "# DESIGN-init-gate-test\nDummy Design Content"

    router.llm_runner.generate_response = AsyncMock(side_effect=mock_generate)

    result = await router.route_event("repository_dispatch", payload)
    assert result["pipeline"] == "autonomous-5-agent-sdlc"
    assert result["status"] == "design_changes_requested"
    assert result["stages"]["architect_agent"]["verdict"] == CHANGES_REQUESTED
    # Ensure dev implementation was NOT invoked
    assert "dev_agent" not in result["stages"]


@pytest.mark.asyncio
async def test_architect_gate_halts_pipeline_on_invalid_verdict(tmp_path: Path):
    harness = TestHarness(cwd=str(tmp_path))
    router = EventRouter(dry_run=False, test_harness=harness)
    (tmp_path / "backend").mkdir(parents=True)

    payload = {
        "action": "mcp_start_dev",
        "repository": {"full_name": "test/repo"},
        "client_payload": {
            "id": "init-invalid-verdict",
            "title": "Invalid Verdict Gate Test",
            "task": "Test gate fail-closed",
            "acceptance_criteria": ["Criteria 1"],
        },
    }

    async def mock_generate(prompt, user_input, dry_run=False, tier="pro"):
        if "Principal Architect Design Review" in prompt or "architect-agent" in prompt:
            return "The design looks fine but I did not provide an explicit DECISION line."
        return "# DESIGN-init-invalid-verdict\nDummy Design Content"

    router.llm_runner.generate_response = AsyncMock(side_effect=mock_generate)

    result = await router.route_event("repository_dispatch", payload)
    assert result["pipeline"] == "autonomous-5-agent-sdlc"
    assert result["status"] == "design_review_halted"
    assert result["stages"]["architect_agent"]["verdict"] == INVALID
    # Ensure dev implementation was NOT invoked
    assert "dev_agent" not in result["stages"]


from src.scope_gate import (
    evaluate_diff_relevance,
    evaluate_pr_scope,
    SCOPE_PASS,
    SCOPE_BLOCK,
)


def test_scope_gate_incident_regression_pr1_csv_mismatch():
    """Validates that a documentation task with CSV-export-only diff is blocked."""
    task_spec = TaskSpec(
        initiative_id="init-doc-roundtrip",
        title="Document the LectureScribe Agentic Fleet MCP round-trip",
        task="Write comprehensive developer documentation for MCP tool usage.",
        acceptance_criteria=[
            "Given developer guides, Then document the round-trip flow.",
            "Given MCP schemas, Then describe input parameters.",
        ],
    )

    # Incident scenario: LLM returned only CSV export code
    changed_files = ["backend/app/csv_export.py"]
    diff = """
    +def export_csv(records):
    +    return ",".join(records)
    """

    res = evaluate_diff_relevance(diff, changed_files, task_spec)
    assert res.verdict == SCOPE_BLOCK
    assert not res.is_passed
    assert "Incident regression detected" in res.reason
    assert "backend/app/csv_export.py" in res.flagged_files


def test_scope_gate_positive_documentation_match():
    """Validates that legitimate documentation files are accepted."""
    task_spec = TaskSpec(
        initiative_id="init-doc-roundtrip",
        title="Document the LectureScribe Agentic Fleet MCP round-trip",
        task="Write comprehensive developer documentation for MCP tool usage.",
        acceptance_criteria=["Document round-trip flow"],
    )

    changed_files = ["docs/agentic-fleet-mcp-integration.md"]
    diff = """
    +# Agentic Fleet MCP Integration
    +Here is the complete round-trip flow...
    """

    res = evaluate_diff_relevance(diff, changed_files, task_spec)
    assert res.verdict == SCOPE_PASS
    assert res.is_passed


@pytest.mark.asyncio
async def test_pipeline_halts_when_scope_gate_blocks_pr(tmp_path: Path):
    """E2E test: verifies that dev-agent blocks PR publication when diff is unrelated."""
    harness = TestHarness(cwd=str(tmp_path))
    router = EventRouter(dry_run=False, test_harness=harness)
    (tmp_path / "backend").mkdir(parents=True)

    payload = {
        "action": "mcp_start_dev",
        "repository": {"full_name": "test/repo"},
        "client_payload": {
            "id": "init-docs-mismatch",
            "title": "Document the LectureScribe Agentic Fleet MCP round-trip",
            "task": "Write documentation for MCP round-trip",
            "acceptance_criteria": ["Document MCP round-trip flow in docs/"],
        },
    }

    # Mock LLM to simulate the PR #1 defect:
    # Architect approves design, but dev implementation produces CSV export code instead of docs!
    async def mock_generate(prompt, user_input, dry_run=False, tier="fast"):
        if "Principal Architect Design Review" in prompt or "architect-agent" in prompt:
            return "## Review\nDECISION: DESIGN_APPROVED ✅\nDesign looks good."
        if "dev-agent" in prompt and "PHASE 1: TECHNICAL DESIGN" in user_input:
            return "# DESIGN-init-docs-mismatch\nDocumentation outline"
        # Dev implementation returns CSV export code only:
        return (
            "Here is the code:\n\n"
            "```python:backend/app/csv_export.py\n"
            "def export_csv(data):\n"
            "    return ','.join(data)\n"
            "```\n"
        )

    router.llm_runner.generate_response = AsyncMock(side_effect=mock_generate)
    router.github_client.create_pull_request = AsyncMock()

    result = await router.route_event("repository_dispatch", payload)

    # Assert pipeline halted due to scope gate
    assert result["pipeline"] == "autonomous-5-agent-sdlc"
    assert result["status"] == "scope_gate_blocked"
    assert result["stages"]["dev_agent"]["action"] == "scope_gate_blocked"
    assert result["stages"]["dev_agent"]["scope_review"]["verdict"] == SCOPE_BLOCK

    # Assert create_pull_request was NEVER called
    router.github_client.create_pull_request.assert_not_called()
