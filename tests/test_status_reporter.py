"""Tests for StatusReporter, MilestoneEvent, and OutboxState marker tracking (Workstream 2).
"""

from __future__ import annotations

import json
import pytest
from unittest.mock import AsyncMock, patch

from src.status_reporter import (
    FleetMilestone,
    MilestoneEvent,
    OutboxState,
    StatusReporter,
)


def test_milestone_event_to_dict():
    event = MilestoneEvent(
        tenant_id="tenant-1",
        initiative_id="init-100",
        request_id="req-200",
        milestone=FleetMilestone.DEV_STARTED.value,
        status="in_progress",
        run_url="https://github.com/org/repo/actions/runs/1",
        seq=1,
    )
    d = event.to_dict()
    assert d["tenant_id"] == "tenant-1"
    assert d["initiative_id"] == "init-100"
    assert d["milestone"] == "dev_started"
    assert d["status"] == "in_progress"
    assert d["seq"] == 1
    assert "timestamp" in d
    assert "pr_url" not in d  # None fields omitted


def test_outbox_state_marker_round_trip():
    outbox = OutboxState()
    ev1 = MilestoneEvent(
        tenant_id="t1",
        initiative_id="init-1",
        request_id="r1",
        milestone=FleetMilestone.DESIGN_APPROVED.value,
        status="completed",
        seq=1,
    )
    outbox.add_pending(ev1)
    marker = outbox.render_marker()
    assert "<!-- FLEET_OUTBOX_STATE:{" in marker

    comment_body = f"## Stage Progress\nDesign approved.{marker}"
    restored = OutboxState.parse_from_text(comment_body)
    assert len(restored.data["pending_events"]) == 1
    assert restored.data["pending_events"][0]["milestone"] == "design_approved"

    # Mark delivered
    restored.mark_delivered("design_approved", seq=1)
    assert len(restored.data["pending_events"]) == 0
    assert "design_approved" in restored.data["delivered_milestones"]


@pytest.mark.asyncio
async def test_status_reporter_dry_run_emission():
    reporter = StatusReporter(dry_run=True, tenant_id="tenant-acme")
    res = await reporter.emit_milestone(
        initiative_id="init-42",
        request_id="req-42",
        milestone=FleetMilestone.TICKET_DRAFTED,
        status="completed",
    )
    assert res["status"] == "mocked"
    assert res["event"]["tenant_id"] == "tenant-acme"
    assert res["event"]["milestone"] == "ticket_drafted"


@pytest.mark.asyncio
async def test_status_reporter_http_auth_error_no_retry():
    reporter = StatusReporter(
        endpoint_url="https://mock.aroadmap.dev/api/mcp",
        bearer_token="invalid_token",
        tenant_id="tenant-1",
        dry_run=False,
    )

    class MockResponse:
        status_code = 401
        text = "Unauthorized"

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = MockResponse()
        res = await reporter.emit_milestone(
            initiative_id="init-1",
            request_id="req-1",
            milestone=FleetMilestone.DEV_STARTED,
            status="in_progress",
        )
        assert res["status"] == "auth_error"
        assert res["code"] == 401
        # Crucial: 401 must NOT be retried multiple times
        assert mock_post.call_count == 1


@pytest.mark.asyncio
async def test_status_reporter_http_successful_delivery():
    reporter = StatusReporter(
        endpoint_url="https://mock.aroadmap.dev/api/mcp",
        bearer_token="valid_token",
        tenant_id="tenant-1",
        dry_run=False,
    )

    class MockResponse:
        status_code = 200
        def json(self):
            return {"jsonrpc": "2.0", "result": {"status": "recorded"}}

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = MockResponse()
        res = await reporter.emit_milestone(
            initiative_id="init-1",
            request_id="req-1",
            milestone=FleetMilestone.READY_TO_MERGE,
            status="completed",
            pr_url="https://github.com/org/repo/pull/1",
        )
        assert res["status"] == "delivered"
        assert mock_post.call_count == 1
        args, kwargs = mock_post.call_args
        sent_body = kwargs["json"]
        assert sent_body["params"]["arguments"]["milestone"] == "ready_to_merge"
        assert sent_body["params"]["arguments"]["pr_url"] == "https://github.com/org/repo/pull/1"
        assert kwargs["headers"]["Authorization"] == "Bearer valid_token"
