# Agentic Fleet Remediation Execution Plan

**Purpose:** Give an implementation agent a bounded, verifiable plan to address (1) Fleet producing work unrelated to an aroadmap initiative and (2) Fleet status callbacks being persisted by LectureScribe but failing to synchronize to aroadmap.

**Repositories in scope:**

- `dipeshsingh2012/lecturescribe`
- `dipeshsingh2012/agentic-fleet`
- `dipeshsingh2012/aroadmap` only if inspection proves the status-report contract or authentication behavior must change

**Incident evidence:** LectureScribe PR #1 was generated for the initiative “Document the LectureScribe Agentic Fleet MCP round-trip,” but its implementation changed CSV export functionality. The observed Fleet run completed its PM → design → architect → implementation sequence; therefore, stage ordering alone did not ensure that the implementation matched the initiative. Separately, the workflow callbacks reached LectureScribe, but the callback request returned 502 after the persisted Fleet status could not be synchronized to aroadmap; aroadmap logged an MCP 401.

**Execution rules:**

- Do not merge PR #1 or start another Fleet run as part of this remediation.
- Do not rerun implementation work merely to repair status synchronization.
- Do not request, print, or commit secret values. Verify secret configuration by variable name and authentication outcomes only.
- Inspect and preserve any existing worktree changes in each repository. Do not reset, discard, amend, or commit unrelated changes.
- Implement and test locally first. Do not deploy, rotate production credentials, replay production callbacks, or change production data without explicit operator approval.
- Keep the two workstreams independently testable. Workstream 1 is the priority for preventing unrelated code changes; Workstream 2 makes status delivery reliable.

## Outcomes

1. Every Fleet stage receives the same canonical task and acceptance criteria, and the pipeline detects missing or materially out-of-scope work before opening/updating a PR.
2. Design approval and human authorization are not conflated. If implementation is configured to require a human gate, no code-generation or PR-publication step can run before that gate is satisfied.
3. LectureScribe records Fleet callback state durably and reports aroadmap synchronization state separately. A temporary aroadmap outage/authentication error cannot erase or misrepresent Fleet’s actual result.
4. Failed aroadmap updates can be retried by request ID without dispatching Fleet again.

---

## Workstream 1 — Initiative-to-implementation relevance and scope gates

### Objective

Make initiative requirements survive the complete aroadmap → LectureScribe → GitHub `repository_dispatch` → Agentic Fleet path, then check proposed design and generated changes against those requirements before implementation is accepted or a PR is published.

### Repositories and likely files

- `agentic-fleet/src/event_router.py` — repository-dispatch normalization, synthetic issue construction, pipeline stages, and PR creation.
- `agentic-fleet` prompt templates and agent handlers — identify by tracing the prompts loaded for PM, design, architect, and dev; do not assume filenames.
- `agentic-fleet` tests — add focused routing, prompt-context, gate, and PR-scope tests alongside existing event-router tests.
- `lecturescribe/backend/mcp_integration.py` — validates and builds the dispatch `client_payload`; it currently includes `acceptance_criteria`, while `task` is copied into `feedback` and `prompt`.
- `lecturescribe/.github/workflows/agentic-sdlc.yml` — verify that the event payload is available to the Fleet action; avoid adding redundant workflow transformations unless required.
- `lecturescribe/docs/agentic-fleet-mcp-integration.md` and relevant repository documentation — document the canonical request contract and any human gate.

### Phase 1.1 — Establish baseline and trace the real path

1. Record the current branch and worktree status in all three repositories. Preserve all pre-existing modifications.
2. Read the current PR #1 diff and use it as a regression example: an initiative about MCP integration must not be considered satisfied by unrelated CSV-export changes.
3. Trace the task fields through:
   - aroadmap’s trigger tool arguments and payload,
   - LectureScribe’s Pydantic validation, persisted payload, and GitHub dispatch,
   - the workflow’s action invocation,
   - `EventRouter.route_event` and the `repository_dispatch` handler,
   - the PM, design, architect, development, and PR-creation prompts/handlers.
4. Confirm whether the event router uses `task`, `prompt`, `feedback`, `acceptance_criteria`, `title`, and any generated PRD/design artifacts at every relevant stage.
5. Add or identify a focused regression fixture representing the MCP integration documentation initiative and a deliberately unrelated CSV-export diff.

**Deliverable:** a short code-path map in the implementation PR description (not a new planning document), including each field’s source and consumer.

### Phase 1.2 — Define and propagate a canonical task specification

1. Define one normalized task object for Fleet dispatch, containing at minimum:
   - initiative ID and title,
   - task/problem statement,
   - acceptance criteria as individually addressable items,
   - source/tenant and target repository,
   - correlation/request ID.
2. At the LectureScribe boundary:
   - retain existing size and field validation;
   - reject empty or whitespace-only task/title/criteria values where required;
   - preserve compatibility with existing callers unless a versioned contract change is necessary;
   - avoid placing secrets or credentials in the task payload.
3. At the Fleet event-router boundary:
   - normalize the accepted payload fields once;
   - include the task and all acceptance criteria in the synthetic issue/context, not merely in an unused payload field;
   - preserve the original initiative/request identifiers for logs and PR metadata;
   - fail clearly on malformed required context instead of silently replacing it with a generic initiative.
4. Pass the normalized specification to every relevant agent prompt. Each stage should be instructed to preserve requirements and identify criteria it cannot satisfy.
5. Keep generated PM/design artifacts tied to the initiative and criteria. The implementation agent must receive the approved design and acceptance criteria, not just a short title or a generic “implement” instruction.
6. Add tests proving the values survive each boundary and appear in the inputs supplied to the PM, design, architect, and dev stages.

**Acceptance criteria:**

- A test dispatch containing multiple acceptance criteria produces a synthetic issue/prompt context containing every criterion exactly once or in a documented, stable representation.
- All implementation prompts have access to the task statement and acceptance criteria.
- Missing required criteria/context is surfaced explicitly; it is not replaced with an unrelated generic feature request.
- Existing supported event types (`mcp_initiative`, `mcp_start_dev`, `fleet_trigger`) retain their intended routing behavior.

### Phase 1.3 — Add pre-implementation alignment gate

1. After design creation and architect review, generate an explicit criterion-to-design traceability result: for each acceptance criterion, identify the design section or mark it unresolved.
2. Require the architect gate to reject or request revisions when criteria are absent, contradicted, or not addressed. Keep this as a decision with a structured verdict/reason, not a log-only message.
3. Ensure `CHANGES_REQUESTED` or an unrecognized/empty verdict halts before Stage 4. Treat only the explicitly recognized approval verdict as approval; do not use “anything other than changes requested” as approval.
4. Add tests for:
   - all criteria covered → pipeline can proceed;
   - one or more criteria missing → design revision or halt;
   - malformed/unrecognized architect response → halt safely;
   - maximum design revisions reached → halt without dev invocation.
5. If product policy is that a person must inspect the design before code begins, add an explicit human approval state between architect approval and Stage 4. If that product decision is not confirmed, do not silently make the entire pipeline wait indefinitely: implement the machine-checkable gate, document the current automated handoff, and leave human-gate behavior behind an explicitly approved policy/configuration.

**Important limitation:** LLM-based semantic judgments are not proof of relevance. The gate must be observable, fail closed on missing/invalid structured results, and have a human escalation path.

### Phase 1.4 — Add post-generation PR scope/relevance gate

1. Identify the exact point where changed files and the proposed PR body are available but the PR has not yet been opened or updated.
2. At that point, evaluate the diff against:
   - the original task and acceptance criteria,
   - approved design/PRD,
   - changed file paths and change magnitude,
   - explicitly declared out-of-scope areas.
3. Fail closed for:
   - no traceable implementation of required criteria,
   - clearly unrelated changes,
   - unexpected broad deletions or dependency changes without justification,
   - missing or invalid structured scope review.
4. On a flagged diff, do not publish/update the PR automatically. Preserve the branch/workspace, record the reason, and request human review or a bounded remediation iteration.
5. Do not reject a valid implementation solely because it touches an unexpected path; require an explanation and criterion linkage to avoid brittle path allowlists.
6. Add a regression test using the PR #1 mismatch: with the MCP documentation task as input, a CSV-export-only diff must be flagged and must not be published as a successful implementation.
7. Add positive tests where a valid change touches a new but justified file path and is accepted with criterion coverage.

**Acceptance criteria:**

- A demonstrably unrelated diff is blocked before PR publication.
- Every acceptance criterion has an explicit implementation/test linkage or an explicit human-approved exception.
- Scope-gate output is stored in the pipeline summary/PR metadata with a clear pass, block, or needs-human-review result.
- Gate failures do not get rewritten as pipeline success.

### Phase 1.5 — Validation and rollout

1. Run focused Fleet tests for event dispatch, stage progression, architect verdict handling, file materialization, and PR creation.
2. Run the LectureScribe MCP integration tests for request validation and dispatch payload construction.
3. Run the smallest relevant lint/type/test commands in each modified repository, then broader suites if the targeted tests reveal regressions.
4. Review logs and summaries to ensure task text is useful but does not expose secrets or unnecessarily sensitive user content.
5. Prepare a test-only dispatch with a harmless documentation task in a non-production test repository or dry-run harness.
6. Verify the gates with both:
   - a relevant documentation implementation that covers its criteria;
   - a deliberately unrelated diff modeled on the CSV-export mismatch.
7. Present the diff and test evidence for operator review. Do not merge or deploy without approval.

---

## Workstream 2 — Durable Fleet callback and aroadmap synchronization

### Objective

Keep GitHub Actions callbacks reliable and truthful: persist Fleet run state independently from aroadmap synchronization, durably queue failed status updates, and retry synchronization without rerunning Fleet.

### Repositories and likely files

- `lecturescribe/backend/mcp_integration.py` — callback validation, Fleet state persistence, and synchronous `_notify_aroadmap` call.
- `lecturescribe/backend/database.py` — Fleet-run persistence; inspect schema and migration conventions before changing.
- `lecturescribe/tests/test_mcp_integration.py` — callback and outbound-sync tests.
- `lecturescribe/.github/workflows/agentic-sdlc.yml` — started/final callback delivery and retry policy.
- aroadmap `app/api/mcp/route.ts` and `lib/mcp-server.ts` — verify the `report_fleet_status` auth/tool contract and JSON-RPC error behavior; modify only if evidence shows a server-side defect.
- `lecturescribe/docs/agentic-fleet-mcp-integration.md` and `.env.example` — document endpoint/token names, state semantics, and recovery process without values.

### Phase 2.1 — Reproduce and classify the failure

1. Confirm that callback authentication to LectureScribe succeeded and that the callback updated the persisted Fleet run.
2. Confirm that LectureScribe’s outbound `report_fleet_status` request reaches the configured aroadmap `/api/mcp` URL.
3. Verify that the configured LectureScribe bearer credential is intended for the aroadmap `report_fleet_status` tool and matches the corresponding aroadmap-side expected credential. Check names and deployment revisions; never reveal values.
4. Verify aroadmap’s required authorization rule for `report_fleet_status`, JSON-RPC method/tool name, required arguments, and error response.
5. Record the status sequence and callback payloads for `running` and terminal states, checking for out-of-order retries and duplicate callbacks.

**Expected diagnosis for the known incident:** the Fleet status was persisted in LectureScribe; downstream aroadmap MCP auth returned 401; LectureScribe then returned 502 to the workflow. This must be revalidated against current revisions before making a production change.

### Phase 2.2 — Decouple callback acceptance from aroadmap availability

1. Preserve current callback authentication, request-ID validation, run-ID validation, and allowed status checks.
2. Make state persistence and creation of a durable outbound-sync record one atomic database operation, or use the repository’s established transaction/outbox pattern.
3. Return a successful callback response once the callback has been durably recorded and queued, even if aroadmap is temporarily unavailable. Return an explicit sync state such as `pending`/`queued`; do not claim that aroadmap has already been updated.
4. Keep Fleet status and status-sync state distinct in storage and API results. A `succeeded` Fleet run with a pending sync must remain a successful Fleet run.
5. Make repeated callbacks idempotent. Duplicate deliveries for the same request/run/status must not create duplicate run records or corrupt state.
6. Reject or safely ignore stale callbacks so an old `running` event cannot overwrite a terminal result. Use run attempt and/or a persisted event/version ordering consistent with GitHub retry behavior.
7. Keep logs structured with request ID, status, sync outcome, and sanitized error category. Never log auth headers, tokens, or full sensitive task payloads.

**Acceptance criteria:**

- Aroadmap 401/5xx/timeout does not make the workflow callback report that the Fleet run itself failed.
- LectureScribe can report, for a request ID, both the actual Fleet status and the aroadmap synchronization state.
- Duplicate/out-of-order callbacks are idempotent and cannot downgrade a terminal run.
- The callback response clearly distinguishes “recorded and queued” from “synchronized.”

### Phase 2.3 — Add bounded retry and manual replay

1. Implement a retry worker or repository-standard scheduled/background mechanism that reads pending/failed sync records and retries only the aroadmap notification.
2. Use bounded exponential backoff with jitter, a maximum attempt count, and a visible terminal `sync_failed`/dead-letter state. Keep retry count and next-attempt time durable.
3. Retry only transient failures automatically. Treat 401/403 as configuration/auth failures: record the response category, stop rapid retries, and alert/document operator correction before replay.
4. Add a protected operator/MCP action to retry synchronization by `request_id`, or document an equivalent safe administrative command. It must:
   - verify the request exists;
   - resend the persisted status snapshot/outbox event;
   - avoid calling GitHub repository dispatch;
   - be idempotent;
   - expose the resulting sync attempt/status.
5. Decide and implement whether each lifecycle event (`running`, terminal status) is queued as an immutable event or whether only the latest state is sent. Prefer immutable ordered events if aroadmap requires a full lifecycle; otherwise document latest-state semantics and ensure stale events cannot overwrite newer ones.
6. Add alerting/diagnostic output for exhausted retries without exposing credentials.

### Phase 2.4 — Align and verify the aroadmap contract

1. Compare LectureScribe’s outbound JSON-RPC request and token requirements with aroadmap’s actual `report_fleet_status` tool schema and route authorization.
2. Fix only the proven mismatch. Keep trigger and status-report permissions appropriately separated; do not broaden aroadmap’s auth checks as a shortcut.
3. Ensure aroadmap’s tool response uses a detectable JSON-RPC error and that LectureScribe checks both HTTP status and JSON-RPC/tool-level errors.
4. Verify that status reporting does not unexpectedly move an initiative from a deliberately rolled-back stage or recreate a deleted initiative. If the current behavior conflicts with intended product semantics, record it as a distinct follow-up unless it is required to safely process callbacks.
5. Test endpoint normalization so a trailing period, wrong path, or empty URL is reported as configuration failure without leaking token values.

### Phase 2.5 — Tests and operational recovery

Add/extend tests covering:

- valid `running` and terminal callbacks;
- invalid/missing callback credentials;
- unknown request IDs and invalid run IDs;
- aroadmap success;
- aroadmap 401, 403, 5xx, timeout, malformed JSON, and JSON-RPC `error`/`isError`;
- durable pending state after each downstream failure;
- retry success after simulated credential correction;
- retry exhaustion and protected manual replay;
- repeated callback delivery and out-of-order start/final callbacks;
- confirmation that retry/manual replay never invokes GitHub dispatch.

Document the recovery runbook:

1. Find the existing `request_id` and inspect LectureScribe’s persisted Fleet status and sync state.
2. Verify configured variable names and the deployed revision; correct the aroadmap URL/credential through the normal secret-management process.
3. Replay status synchronization for the existing request ID.
4. Confirm aroadmap records the same status and the GitHub run URL.
5. Do not dispatch another Fleet run unless implementation work itself is intentionally being restarted.

### Phase 2.6 — Rollout

1. Run focused callback/database tests, then relevant backend tests and lint/type checks.
2. Verify migration behavior against an existing database snapshot or isolated test database; migrations must preserve existing Fleet-run records.
3. Test with mocked aroadmap responses first, including 401 and timeout recovery.
4. Deploy to a non-production environment if available. Exercise a harmless test request and verify:
   - callback is acknowledged after durable recording;
   - temporary aroadmap failure leaves a pending sync;
   - retry updates aroadmap without a second GitHub dispatch.
5. Obtain operator approval before production deploy or production replay.
6. After production deployment, verify one approved test request end-to-end and inspect both LectureScribe and aroadmap state.

---

## Cross-workstream completion checklist

- [ ] PR #1 is not merged as the implementation of the MCP documentation initiative.
- [ ] The Fleet router receives the task and every acceptance criterion, with regression coverage.
- [ ] Design/architect and pre-PR checks block missing requirements and unrelated diffs.
- [ ] Human approval semantics before coding are explicit and implemented only according to confirmed policy.
- [ ] Callback persistence is independent of aroadmap availability.
- [ ] Status synchronization is durable, retryable, observable, and separate from Fleet execution status.
- [ ] Retrying status synchronization cannot trigger another Fleet run.
- [ ] Relevant tests pass in every changed repository.
- [ ] Documentation and environment examples describe the final behavior without secret values.
- [ ] No production deployment, merge, or credential operation occurred without operator approval.

## Suggested implementation order

1. Workstream 1, Phases 1.1–1.3: trace and propagate requirements; add the pre-implementation gate.
2. Workstream 2, Phases 2.1–2.2: reproduce the auth failure and decouple callback acknowledgement from synchronization.
3. Workstream 1, Phase 1.4: implement and test the pre-PR scope/relevance gate.
4. Workstream 2, Phases 2.3–2.5: durable retry/manual replay, contract tests, and recovery documentation.
5. Run cross-repository validation and operator review before any rollout.

The workstreams may proceed in parallel only after baseline status and relevant contracts have been inspected. Changes to shared status semantics, human-approval policy, or production rollout require explicit operator confirmation.
