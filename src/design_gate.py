"""Strict parsing of the architect-agent verdict.

Only an explicit ``DECISION: DESIGN_APPROVED`` approves a design. Anything else
(missing, empty, malformed or ambiguous output) is reported as ``INVALID`` so the
pipeline halts instead of treating "not changes-requested" as approval.
"""

from __future__ import annotations

import re

DESIGN_APPROVED = "DESIGN_APPROVED"
CHANGES_REQUESTED = "CHANGES_REQUESTED"
INVALID = "INVALID"

_DECISION_RE = re.compile(
    r"(?:DECISION|STATUS)\s*:\s*[*_`\s]*(DESIGN_APPROVED|APPROVED|CHANGES_REQUESTED)\b",
    re.IGNORECASE,
)


def parse_architect_verdict(response: str) -> str:
    """Return DESIGN_APPROVED, CHANGES_REQUESTED or INVALID."""
    raw_matches = {m.upper() for m in _DECISION_RE.findall(response or "")}
    normalized = set()
    for m in raw_matches:
        if m in ("DESIGN_APPROVED", "APPROVED"):
            normalized.add(DESIGN_APPROVED)
        elif m == "CHANGES_REQUESTED":
            normalized.add(CHANGES_REQUESTED)

    if normalized == {DESIGN_APPROVED}:
        return DESIGN_APPROVED
    if normalized == {CHANGES_REQUESTED}:
        return CHANGES_REQUESTED
    # No decision line, or contradictory decisions in one response.
    return INVALID
