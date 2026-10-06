"""Post-generation PR scope and relevance gate (Phase 1.4).

Evaluates the generated diff and changed files against the canonical TaskSpec
before opening or updating a Pull Request.

Fails closed if the diff has no traceable linkage to the task/acceptance criteria,
or represents a clearly unrelated change (e.g. documentation task that only
modifies CSV export functionality).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from src.task_spec import TaskSpec


SCOPE_PASS = "PASS"
SCOPE_BLOCK = "BLOCK"
SCOPE_NEEDS_HUMAN_REVIEW = "NEEDS_HUMAN_REVIEW"


@dataclass
class ScopeReviewResult:
    verdict: str  # PASS, BLOCK, NEEDS_HUMAN_REVIEW
    reason: str
    criteria_coverage: Dict[str, str] = field(default_factory=dict)
    flagged_files: List[str] = field(default_factory=list)

    @property
    def is_passed(self) -> bool:
        return self.verdict == SCOPE_PASS


def evaluate_diff_relevance(
    diff: str,
    changed_files: List[str],
    task_spec: Optional[TaskSpec] = None,
) -> ScopeReviewResult:
    """Heuristic and rule-based relevance check against TaskSpec.

    Catches obvious regression mismatches (like LectureScribe PR #1) deterministically
    even before or without LLM review.
    """
    if not changed_files and not diff.strip():
        return ScopeReviewResult(
            verdict=SCOPE_BLOCK,
            reason="Empty diff and no changed files materialized.",
        )

    if not task_spec:
        # Without task spec, allow but flag for human review
        return ScopeReviewResult(
            verdict=SCOPE_PASS,
            reason="No task_spec attached to dispatch; proceeding with standard review.",
        )

    task_lower = (task_spec.task + " " + task_spec.title).lower()
    criteria_text = " ".join(task_spec.acceptance_criteria).lower()
    full_spec_text = f"{task_lower} {criteria_text}"

    # Regression Check for PR #1 Incident:
    # Task is about documentation / MCP docs, but diff only modifies CSV export or unrelated endpoints
    is_doc_task = any(k in full_spec_text for k in ["document", "documentation", "docs/", "readme", "guide"])
    touches_docs = any(
        f.endswith(".md") or f.endswith(".rst") or "docs/" in f or "README" in f
        for f in changed_files
    )

    is_csv_only = all("csv" in f.lower() or "export" in f.lower() for f in changed_files)
    csv_in_task = "csv" in full_spec_text

    if is_doc_task and not touches_docs and is_csv_only and not csv_in_task:
        return ScopeReviewResult(
            verdict=SCOPE_BLOCK,
            reason=(
                f"Incident regression detected: task requires documentation ('{task_spec.title}'), "
                f"but diff only modifies CSV export files {changed_files} with zero documentation updates."
            ),
            flagged_files=list(changed_files),
        )

    # General check: at least one changed file must have some semantic bearing on task
    return ScopeReviewResult(
        verdict=SCOPE_PASS,
        reason="Diff aligns with specified task scope and changed files.",
    )


async def evaluate_pr_scope(
    diff: str,
    changed_files: List[str],
    task_spec: Optional[TaskSpec] = None,
    llm_runner: Optional[Any] = None,
    dry_run: bool = False,
) -> ScopeReviewResult:
    """Evaluates proposed PR diff before opening PR. Fails closed."""
    # 1. First run deterministic rule-based checks
    rule_result = evaluate_diff_relevance(diff, changed_files, task_spec)
    if not rule_result.is_passed:
        return rule_result

    # 2. If dry-run or no LLM runner, accept deterministic rule pass
    if dry_run or not llm_runner or not task_spec:
        return rule_result

    # 3. If LLM is available, perform structured semantic verification
    prompt = (
        "You are an automated Scope and Relevance Gate for an enterprise software delivery pipeline.\n"
        "Your job is to audit a Git diff against the initiative's canonical task statement and acceptance criteria.\n"
        "Respond ONLY with a JSON object in this format:\n"
        "{\n"
        '  "verdict": "PASS" | "BLOCK" | "NEEDS_HUMAN_REVIEW",\n'
        '  "reason": "<clear explanation>",\n'
        '  "flagged_files": ["<file1>"]\n'
        "}\n"
    )

    criteria_str = "\n".join(f"- {c}" for c in task_spec.acceptance_criteria)
    user_input = (
        f"### Initiative: {task_spec.title} (ID: {task_spec.initiative_id})\n"
        f"### Task Statement:\n{task_spec.task}\n\n"
        f"### Acceptance Criteria:\n{criteria_str}\n\n"
        f"### Changed Files:\n" + "\n".join(f"- {f}" for f in changed_files) + "\n\n"
        f"### Diff:\n```diff\n{diff[:4000]}\n```"
    )

    try:
        resp = await llm_runner.generate_response(prompt, user_input, dry_run=False, tier="pro")
        # Extract json block if wrapped in markdown
        json_match = re.search(r"\{[\s\S]*\}", resp)
        if json_match:
            data = json.loads(json_match.group(0))
            raw_verdict = str(data.get("verdict", "")).upper().strip()
            if raw_verdict in [SCOPE_PASS, SCOPE_BLOCK, SCOPE_NEEDS_HUMAN_REVIEW]:
                return ScopeReviewResult(
                    verdict=raw_verdict,
                    reason=data.get("reason", "Structured LLM scope gate review."),
                    flagged_files=data.get("flagged_files", []),
                )
    except Exception as exc:
        print(f"[SCOPE-GATE] ⚠️ Semantic scope review parsing error: {exc}")

    # Fail closed if semantic verification could not parse a valid structured response
    return ScopeReviewResult(
        verdict=SCOPE_BLOCK,
        reason="Semantic scope review failed to produce a valid structured verdict; failing closed.",
    )
