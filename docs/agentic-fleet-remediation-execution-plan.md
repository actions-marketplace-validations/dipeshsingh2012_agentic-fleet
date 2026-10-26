# Agentic Fleet Remediation Execution Plan (v2, tenant-neutral)

The previous version is kept as `agentic-fleet-remediation-execution-plan.v1.md`.

**Purpose:** Give an implementation agent a bounded, verifiable plan to fix (1) Fleet producing work unrelated to its initiative and (2) unreliable delivery of Fleet status to aroadmap.

## Target architecture

```
aroadmap initiative
  -> tenant project (any tenant; LectureScribe is only one example)
  -> PM agent drafts the ticket (task + acceptance criteria)
  -> design -> architect gate -> dev -> security/QA -> PR
  -> Fleet reports status back to aroadmap (per tenant)
  -> loop (feedback / next stage)
```

**Principles**

- **Fleet owns the SDLC and status reporting.** Tenants (LectureScribe and others) are thin clients. They only start a run and show results. They do not run SDLC logic, relay status, or hold SDLC state.
- **Tenant-neutral.** No tenant-specific code, names or paths in Fleet. A tenant is identified by a tenant ID and a per-tenant credential.
- **Three-way decoupling.** Fleet, aroadmap and tenants each know only the shared event contract (section 2.4). Fleet emits milestones, aroadmap maps them to columns using tenant config, and tenants supply a repository and configuration. Transport is direct HTTP behind a swappable interface.
- **The initiative is the source of truth.** The PM-drafted ticket must trace to the initiative. Every later stage is checked against both.
- **Tenant isolation.** A tenant credential may report only for its own initiatives.

**Incident evidence (tenant example):** LectureScribe PR #1 was generated for "Document the LectureScribe Agentic Fleet MCP round-trip" but changed CSV export. The pipeline completed all stages, so stage ordering did not ensure relevance. Separately, a status update failed with an aroadmap MCP 401.

**Execution rules**

- Do not merge PR #1 or start another Fleet run.
- Never request, print or commit secret values; verify by variable name and auth outcome only.
- Preserve existing worktree changes. `src/event_router.py` currently has uncommitted changes adding `_sync_status_to_aroadmap`. Treat it as a draft of Workstream 2, not as done (see 2.2). Do not reset or discard it.
- Local implementation and tests first. No deploy, credential rotation, production replay or production data change without operator approval.
- The two workstreams are independently testable. Workstream 1 has priority.

## Outcomes

1. Every stage receives the same canonical task and acceptance criteria, traced to the initiative.
2. Out-of-scope designs and diffs are blocked before implementation and before a PR is published.
3. Only the architect gates the design. A human merges the final artifact, and Fleet never merges.
4. Fleet reports status to aroadmap with a tenant credential, durably and idempotently, and can retry without re-running the SDLC.

---

## Workstream 1: Relevance and scope gates

### 1.1 Baseline and trace

1. Record branch and worktree status. Preserve existing changes.
2. Use PR #1 as the regression example (documentation task, unrelated CSV diff).
3. Trace the task through: aroadmap trigger payload -> tenant dispatch -> workflow -> `EventRouter.route_event` (`repository_dispatch` branch) -> PM, design, architect, dev, PR creation.

**Known findings (current code):**

- The `repository_dispatch` branch builds the synthetic issue body from `prompt or feedback or title`. It never reads `acceptance_criteria`.
- It falls back to the title "Autonomous Feature Initiative" and ID `mcp-initiative` when fields are missing.
- The architect gate approves on any substring of `APPROVED`, `LGTM` or `PASSED`, and approves automatically in dry-run.
- The architect receives only the issue body, with no criteria.
- Nothing checks the diff before `create_pull_request`.
- Design approval flows straight into dev. This is intended (no human gate before coding), but it makes the architect gate the only design check, so it must be strict.
- `mcp_initiative` and `mcp_spec` route to the PM only. Other actions run the full pipeline. Confirm this matches the intended behaviour of `mcp_start_dev` and `fleet_trigger`.

**Deliverable:** a code-path map in the PR description listing each field's source and consumer.

### 1.2 Canonical task specification

1. Define one normalized object: tenant ID, initiative ID and title, task statement, acceptance criteria as separate items, target repository, request/correlation ID, and (once drafted) ticket ID.
2. The PM agent drafts the ticket from the initiative. The ticket must reference the initiative and restate each initiative requirement as an acceptance criterion. Persist it as the canonical task.
3. Normalize once at the router boundary. Put the task and all criteria into the synthetic issue and every stage prompt.
4. Fail clearly on missing required fields. Remove the generic fallbacks.
5. Each stage must preserve requirements and list the criteria it cannot satisfy.
6. Add tests proving values survive each boundary and appear in the PM, design, architect and dev inputs.

**Acceptance:** a dispatch with several criteria produces context containing every criterion. Missing context is an explicit error. Existing event types keep their routing.

### 1.3 Pre-implementation alignment gate

1. After design and architect review, produce a criterion-to-design traceability result (section or "unresolved") for each criterion.
2. The architect returns a structured verdict (for example JSON with `verdict`, `reason`, `coverage[]`). Only an explicit approval verdict approves. Empty, malformed or unrecognized output halts. Dry-run must not bypass the parser.
3. Tests: all covered -> proceed; missing criteria -> revise or halt; malformed output -> halt; max revisions -> halt without calling dev.
4. No human gate before coding. The architect review is the design gate. A human only merges the final artifact, so the pipeline must never merge on its own.

> LLM judgments are not proof of relevance. Gates must be observable, fail closed, and have a human escalation path.

### 1.4 Pre-PR scope gate

1. Evaluate the diff before the PR is opened or updated, against the task and criteria, the approved design, changed paths and size, and declared out-of-scope areas.
2. Fail closed on: no traceable implementation, clearly unrelated changes, unexplained broad deletions or dependency changes, missing or invalid scope review.
3. On a flagged diff, do not publish. Keep the branch, record the reason, and request human review or one bounded remediation round.
4. Do not use brittle path allowlists. A new path is acceptable if it is explained and linked to a criterion.
5. Tests: the PR #1 mismatch (documentation task + CSV-only diff) is blocked. A justified change to a new path is accepted.

**Acceptance:** an unrelated diff is blocked. Each criterion has linked implementation or a recorded human exception. The result (`pass`, `block` or `needs-human-review`) appears in the pipeline summary and PR metadata, and is never reported as success on failure.

### 1.5 Validation

Run focused tests for dispatch, stage progression, verdict handling, file materialization and PR creation. Existing tests to keep passing: `tests/test_mcp_isolation.py`, `tests/test_sdlc_pipeline_flow.py`. Verify with one relevant documentation change and one deliberately unrelated diff, using a dry-run harness or test repository. Present evidence for operator review.

---

## Workstream 2: Fleet-owned status reporting to aroadmap

### Objective

Fleet reports each run's status to aroadmap using the tenant's credential. Reporting is durable, idempotent and retryable. Tenants take no part.

### 2.1 Replace the existing draft

Decision: start a new implementation, preferably in its own module rather than inside `event_router.py`. Save the uncommitted `_sync_status_to_aroadmap` changes as a patch file before removing them, so nothing is lost. The draft has these defects, which the new code must avoid:

- Hardcoded default URL.
- No authentication, so aroadmap cannot identify the tenant. This is the likely cause of the 401.
- Calls `transition_initiative_stage`, which can move or roll back stages. Status reporting should use a dedicated report tool (or equivalent) that records status without moving the board unexpectedly.
- Ignores JSON-RPC errors (checks only HTTP < 300).
- Failures are only printed. There is no retry and no record.
- The initiative-ID extraction is duplicated in several places.
- Not all exit paths report (design halt, scope-gate block, exceptions).

The draft also sends no milestone, only a stage name, and moves the card to `shipped` when the PR is merely awaiting a human merge.

### 2.2 Tenant-neutral reporting contract

1. Configuration per tenant, by variable name only: aroadmap endpoint URL, tenant ID, tenant credential. No defaults that point at production. Missing configuration is a clear config error that does not fail the run.
2. Fleet emits the milestone events defined in 2.4 (not stage names), each with the run URL, request ID, initiative ID, tenant ID and a sequence number. Fleet never names a column.
3. The pipeline's own result and the sync result are separate. A report failure never changes the run's recorded outcome.
4. Check both the HTTP status and the JSON-RPC/tool-level `error` or `isError`. Classify: auth (401/403), client error, transient (5xx, timeout), malformed.
5. Log with structured fields (tenant, request ID, status, outcome category). Never log tokens, headers or full task text.

### 2.3 Durability, retry and ordering

1. Fleet runs in short-lived Actions, so the outbox needs durable storage. Decision: start with a hidden marker (for example an HTML comment) in an issue/PR comment, holding the pending and last-delivered report per request ID. Revisit later if `state_manager.py` or another store fits better. Fleet must not depend on any tenant's database.
2. Write the pending report to the outbox before sending. Mark it as delivered on success.
3. Retry transient failures with bounded exponential backoff and jitter, and a terminal `sync_failed` state. Do not rapid-retry 401/403. Record them as configuration failures.
4. Idempotency: use an idempotency key (tenant ID, request ID, run ID, status). aroadmap must treat repeats as no-ops.
5. Ordering: events carry a sequence or timestamp so a late `running` cannot overwrite a terminal status. Alternatively, send latest-state only and document it.
6. Manual replay by request ID (a Fleet CLI command or workflow dispatch). It re-sends the stored report and never starts the SDLC.

### 2.4 Decoupled card movement: milestone events and tenant-driven columns

Fleet, aroadmap and each tenant know only a small shared contract, not each other's internals.

| Component | Owns | Must not know |
| --- | --- | --- |
| Fleet | The SDLC and emitting milestone events | Column names, aroadmap stages, tenant specifics |
| aroadmap | Cards, columns, and the milestone-to-column map per tenant | How Fleet works, any tenant's code |
| Tenant | Its repo, column config and credentials | Fleet's pipeline, aroadmap internals |

**Event contract (Fleet -> aroadmap, direct HTTP):**

```
{ tenant_id, initiative_id, request_id, milestone, status, run_url, pr_url?, reason?, seq }
```

**Fixed milestones (tenant-neutral, in Fleet's order):** `ticket_drafted`, `design_approved`, `dev_started`, `pr_opened`, `security_started`, `qa_started`, `review_started`, `ready_to_merge`, `merged`, plus `blocked` (a flag with a reason, not a column move).

**Default columns and what moves a card there:**

| Column | Moved by |
| --- | --- |
| 1. Backlog / To do | Human |
| 2. Ready for dev | Human sign-off (this is what starts Fleet) |
| 3. Design approved | `design_approved` |
| 4. In development | `dev_started` |
| 5. Security review | `security_started` |
| 6. QA review | `qa_started` |
| 7. Code review | `review_started` |
| 8. Shipped / Done | `merged` (human merge) |

`ticket_drafted`, `pr_opened` and `ready_to_merge` do not move the card. They attach status (the drafted ticket, the PR URL, a "ready to merge" badge). A blocked run leaves the card in its column with the `blocked` flag and reason.

**Columns are tenant-config driven (eventually).** Each tenant stores its column list and a milestone-to-column map. If a tenant has no column for a milestone, the card stays where it is. Step one uses the default set above for every tenant, kept in config rather than code.

**Transport:** direct HTTP from Fleet to a configurable aroadmap endpoint with a per-tenant token. The sender sits behind a small interface (`send(event)`) so a queue or webhook relay can replace it later without touching the pipeline.

**Decision: columns follow Fleet's order.** Fleet runs dev, then security, then QA, then senior code review, so the columns are ordered the same way. Fleet's pipeline is not reordered.

**What aroadmap needs (verified in the code):**

1. Stages are hardcoded in `lib/types.ts` (`RoadmapStage`, `STAGE_CONFIG`), the MCP tool enums in `lib/mcp-server.ts`, `components/KanbanBoard.tsx` and `components/PRDDrawer.tsx`. The DB column is a plain `varchar(32)`, so no schema change is needed.
2. Add one milestone-event tool (or extend `report_fleet_status`, which already has SQL ordering so a late `running` cannot override a terminal status). It must never trigger a Fleet dispatch. Do not reuse `transition_initiative_stage`: for the `lecturescribe` tenant, moving to `development` dispatches Fleet.
3. Remove tenant-specific code: the `tenantId === "lecturescribe"` branches, the hardcoded `dipeshsingh2012/lecturescribe` repository (use `Tenant.github_repo`), and the LectureScribe-named tokens (`AROADMAP_FLEET_TRIGGER_TOKEN`, `LECTURESCRIBE_STATUS_SYNC_TOKEN`). Replace them with a per-tenant credential that can only act on that tenant's cards.
4. Starting a run is the reverse direction. aroadmap sends a generic start request (initiative, criteria, target repository) and does not call a LectureScribe-specific tool.
5. Moves are forward-only and idempotent. Repeated or late events never move a card backwards or recreate a deleted card.
6. Errors must be detectable. `report_fleet_status` currently returns HTTP 200 with `isError: true` for failures, so the client must check the tool-level error as well as the HTTP status. The route returns 401 for bad tokens.
7. Test URL normalization (trailing period, wrong path, empty) as a configuration error without leaking tokens.

### 2.5 Tenant client responsibilities (minimal)

- Tenants only validate their own request, start Fleet, and optionally display the status they read from aroadmap.
- Tenants do not relay or persist SDLC status.
- Existing tenant callback code (for example in LectureScribe) is deprecated once Fleet reporting is verified. Handle that as a follow-up in the tenant's repository.

### 2.6 Tests

- Success, and aroadmap 401, 403, 5xx, timeout, malformed JSON, and JSON-RPC error.
- Pending state is stored after each failure.
- Retry succeeds after the credential is fixed. Retries exhaust to `sync_failed`.
- Manual replay never calls GitHub dispatch or runs any agent.
- Duplicate and out-of-order events.
- A report from one tenant for another tenant's initiative is rejected (aroadmap side).
- Every pipeline exit path (success, design halt, scope block, security or QA halt, exception) produces one terminal report.

### 2.7 Recovery runbook (document)

1. Find the request ID and inspect the stored run status and sync state.
2. Verify configuration names and the deployed revision. Correct the credential through the normal secret process.
3. Replay the report for that request ID.
4. Confirm aroadmap shows the same status and run URL.
5. Do not start a new run.

---

## Completion checklist

- [ ] PR #1 is not merged as the implementation of its initiative.
- [ ] The router passes the task and all criteria to every stage, with tests.
- [ ] The ticket drafted by the PM traces to the initiative.
- [ ] Architect and pre-PR gates fail closed on missing requirements and unrelated diffs.
- [ ] Human-approval behaviour is explicit and follows confirmed policy.
- [ ] Fleet reports status to aroadmap with a per-tenant credential. There are no tenant-specific code paths.
- [ ] Reports are durable, idempotent, ordered, retryable and replayable without re-running the SDLC.
- [ ] The uncommitted draft is hardened or replaced, and nothing was lost.
- [ ] Tests pass in each changed repository.
- [ ] Docs and env examples list variable names only.
- [ ] No merge, deploy or credential operation happened without approval.

## Suggested order

1. Workstream 1, 1.1 to 1.3: trace, propagate, add the architect gate.
2. Workstream 2, 2.1 to 2.2: harden the draft and define the tenant-neutral contract (including the aroadmap-side check).
3. Workstream 1, 1.4: pre-PR scope gate.
4. Workstream 2, 2.3 to 2.7: durability, retry, replay, tests, runbook.
5. Cross-repository validation and operator review before any rollout.

## Decisions (resolved by operator)

- Outbox: start with a marker on the issue/PR (2.3).
- Existing draft sync: replace it with a new implementation (2.1).
- Human gate: none before coding. The architect reviews the design. A human only merges the final artifact (1.3).
- aroadmap: the only requirement is that Fleet moves the card automatically (2.4). The MCP server code was not found in the local `aroadmap` checkout, so its tool and auth contract still has to be verified.
