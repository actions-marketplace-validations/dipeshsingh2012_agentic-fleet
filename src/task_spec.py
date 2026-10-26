"""Canonical, tenant-neutral task specification for dispatched Fleet runs.

A dispatch ``client_payload`` is normalized exactly once at the router boundary
into a :class:`TaskSpec`. Every downstream stage then reads the same task
statement and acceptance criteria instead of re-deriving them from loose fields.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


class TaskSpecError(ValueError):
    """Raised when a dispatch payload lacks context required to run safely."""


def _clean(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _criteria_list(raw: Any) -> List[str]:
    """Accept a list of strings or a single (possibly multi-line) string."""
    if raw is None:
        return []
    if isinstance(raw, str):
        items = [line.strip(" \t-*") for line in raw.splitlines()]
    elif isinstance(raw, (list, tuple)):
        items = [_clean(item) for item in raw]
    else:
        return []
    seen: List[str] = []
    for item in items:
        if item and item not in seen:
            seen.append(item)
    return seen


@dataclass
class TaskSpec:
    initiative_id: str
    title: str
    task: str = ""
    acceptance_criteria: List[str] = field(default_factory=list)
    tenant_id: str = ""
    request_id: str = ""
    warnings: List[str] = field(default_factory=list)

    @classmethod
    def from_client_payload(cls, client_payload: Optional[Dict[str, Any]]) -> "TaskSpec":
        cp = client_payload or {}
        initiative_id = _clean(
            cp.get("initiative_id") or cp.get("id") or cp.get("item_id")
        )
        title = _clean(cp.get("title"))

        missing = []
        if not initiative_id:
            missing.append("initiative_id (or id / item_id)")
        if not title:
            missing.append("title")
        if missing:
            raise TaskSpecError(
                "repository_dispatch payload is missing required field(s): "
                + ", ".join(missing)
            )

        # Prefer the explicit task statement; fall back to legacy fields.
        task = _clean(cp.get("task")) or _clean(cp.get("prompt")) or _clean(cp.get("feedback"))
        criteria = _criteria_list(cp.get("acceptance_criteria"))

        warnings: List[str] = []
        if not task and not criteria:
            warnings.append("no task statement or acceptance criteria were provided; only the title is available")
        elif not criteria:
            warnings.append("no acceptance criteria were provided")

        return cls(
            initiative_id=initiative_id,
            title=title,
            task=task,
            acceptance_criteria=criteria,
            tenant_id=_clean(cp.get("tenant_id")),
            request_id=_clean(cp.get("request_id")),
            warnings=warnings,
        )

    def to_issue_body(self) -> str:
        """Render the statement every stage sees as the issue body."""
        parts: List[str] = [self.task or self.title]
        if self.acceptance_criteria:
            numbered = "\n".join(f"{i}. {c}" for i, c in enumerate(self.acceptance_criteria, 1))
            parts.append("## Acceptance Criteria\n" + numbered)
        return "\n\n".join(parts)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "initiative_id": self.initiative_id,
            "title": self.title,
            "task": self.task,
            "acceptance_criteria": list(self.acceptance_criteria),
            "tenant_id": self.tenant_id,
            "request_id": self.request_id,
            "warnings": list(self.warnings),
        }
