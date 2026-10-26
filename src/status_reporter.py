"""Decoupled status reporter & milestone event emitter for Agentic Fleet (Workstream 2).

Fleet emits milestone events over direct HTTP to a configured endpoint (e.g., aroadmap.dev).
Stores an outbox state marker durably in GitHub Issue / PR comments to guarantee
observability, idempotency, and replayability across short-lived GitHub Actions workflows.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger("agentic-fleet.status_reporter")

MARKER_REGEX = re.compile(r"<!--\s*FLEET_OUTBOX_STATE:\s*(\{.*?\})\s*-->", re.DOTALL)


class FleetMilestone(str, Enum):
    TICKET_DRAFTED = "ticket_drafted"
    DESIGN_APPROVED = "design_approved"
    DEV_STARTED = "dev_started"
    PR_OPENED = "pr_opened"
    SECURITY_STARTED = "security_started"
    QA_STARTED = "qa_started"
    REVIEW_STARTED = "review_started"
    READY_TO_MERGE = "ready_to_merge"
    MERGED = "merged"
    BLOCKED = "blocked"


@dataclass
class MilestoneEvent:
    tenant_id: str
    initiative_id: str
    request_id: str
    milestone: str
    status: str  # in_progress, completed, failed, blocked
    run_url: str = ""
    pr_url: Optional[str] = None
    reason: Optional[str] = None
    seq: int = 0
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None}


class OutboxState:
    """Durably tracks pending and acknowledged milestone reports in comment markers."""

    def __init__(self, data: Optional[Dict[str, Any]] = None):
        self.data: Dict[str, Any] = data or {
            "pending_events": [],
            "delivered_milestones": [],
            "last_seq": 0,
            "last_updated": time.time(),
        }

    def add_pending(self, event: MilestoneEvent) -> None:
        self.data["last_seq"] = max(self.data.get("last_seq", 0), event.seq)
        self.data["pending_events"].append(event.to_dict())
        self.data["last_updated"] = time.time()

    def mark_delivered(self, milestone: str, seq: int) -> None:
        if milestone not in self.data["delivered_milestones"]:
            self.data["delivered_milestones"].append(milestone)
        self.data["pending_events"] = [
            e for e in self.data["pending_events"] if e.get("milestone") != milestone or e.get("seq") != seq
        ]
        self.data["last_updated"] = time.time()

    def render_marker(self) -> str:
        serialized = json.dumps(self.data, separators=(",", ":"))
        return f"\n\n<!-- FLEET_OUTBOX_STATE:{serialized} -->"

    @classmethod
    def parse_from_text(cls, text: str) -> "OutboxState":
        if not text:
            return cls()
        match = MARKER_REGEX.search(text)
        if match:
            try:
                parsed = json.loads(match.group(1))
                return cls(parsed)
            except Exception as e:
                logger.warning(f"Failed parsing outbox state marker JSON: {e}")
        return cls()


class StatusReporter:
    """Tenant-neutral status reporter for Agentic Fleet."""

    def __init__(
        self,
        endpoint_url: Optional[str] = None,
        bearer_token: Optional[str] = None,
        tenant_id: Optional[str] = None,
        dry_run: bool = False,
    ):
        self.endpoint_url = endpoint_url or os.environ.get("AROADMAP_MCP_URL") or os.environ.get("AROADMAP_EVENT_URL")
        self.bearer_token = bearer_token or os.environ.get("AROADMAP_REPORT_TOKEN") or os.environ.get("LECTURESCRIBE_STATUS_SYNC_TOKEN")
        self.tenant_id = tenant_id or os.environ.get("TENANT_ID", "default")
        self.dry_run = dry_run
        self._seq_counter = 0

    def next_seq(self) -> int:
        self._seq_counter += 1
        return self._seq_counter

    async def emit_milestone(
        self,
        initiative_id: str,
        request_id: str,
        milestone: FleetMilestone | str,
        status: str,
        tenant_id: Optional[str] = None,
        run_url: str = "",
        pr_url: Optional[str] = None,
        reason: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Emits a milestone event to aroadmap over direct HTTP. Fails cleanly without breaking pipeline."""
        m_str = milestone.value if isinstance(milestone, FleetMilestone) else str(milestone)
        effective_tenant = tenant_id or self.tenant_id

        event = MilestoneEvent(
            tenant_id=effective_tenant,
            initiative_id=initiative_id,
            request_id=request_id or f"req-{int(time.time())}",
            milestone=m_str,
            status=status,
            run_url=run_url,
            pr_url=pr_url,
            reason=reason,
            seq=self.next_seq(),
        )

        if self.dry_run or not self.endpoint_url:
            print(f"[STATUS-REPORTER (MOCK)] 📡 Milestone '{m_str}' ({status}) recorded for initiative '{initiative_id}'")
            return {"status": "mocked", "event": event.to_dict()}

        return await self._send_http(event)

    async def _send_http(self, event: MilestoneEvent) -> Dict[str, Any]:
        """Sends JSON-RPC report_fleet_status / milestone call to endpoint with retry backoff."""
        import httpx

        # Construct JSON-RPC call compatible with aroadmap MCP server
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "Agentic-Fleet-Reporter/2.0",
        }
        if self.bearer_token:
            headers["Authorization"] = f"Bearer {self.bearer_token.strip()}"

        jsonrpc_payload = {
            "jsonrpc": "2.0",
            "id": event.seq,
            "method": "tools/call",
            "params": {
                "name": "report_fleet_status",
                "arguments": {
                    "tenant_id": event.tenant_id,
                    "initiative_id": event.initiative_id,
                    "request_id": event.request_id,
                    "status": "running" if event.status == "in_progress" else ("succeeded" if event.status == "completed" else "failed"),
                    "milestone": event.milestone,
                    "run_url": event.run_url,
                    "pr_url": event.pr_url,
                    "error_summary": event.reason,
                },
            },
        }

        max_attempts = 3
        for attempt in range(1, max_attempts + 1):
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    resp = await client.post(self.endpoint_url, json=jsonrpc_payload, headers=headers)
                    if resp.status_code == 401 or resp.status_code == 403:
                        print(f"[STATUS-REPORTER] 🛑 Authentication error (HTTP {resp.status_code}) reporting milestone '{event.milestone}'. Will not retry.")
                        return {"status": "auth_error", "code": resp.status_code}

                    if resp.status_code < 300:
                        data = resp.json()
                        if data.get("error"):
                            print(f"[STATUS-REPORTER] ⚠️ JSON-RPC error: {data['error'].get('message')}")
                            return {"status": "rpc_error", "error": data["error"]}
                        print(f"[STATUS-REPORTER] ✅ Synced milestone '{event.milestone}' ({event.status}) to {self.endpoint_url}")
                        return {"status": "delivered", "response": data}
                    else:
                        print(f"[STATUS-REPORTER] ⚠️ HTTP {resp.status_code} on attempt {attempt}/{max_attempts}")

            except Exception as exc:
                print(f"[STATUS-REPORTER] ⚠️ Attempt {attempt}/{max_attempts} error: {exc}")

            if attempt < max_attempts:
                await asyncio.sleep(0.5 * (2 ** (attempt - 1)))

        return {"status": "failed_after_retries", "milestone": event.milestone}
