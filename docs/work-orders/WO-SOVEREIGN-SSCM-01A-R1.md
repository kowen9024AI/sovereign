# WO-SOVEREIGN-SSCM-01A-R1
## Exact Revision Authority, Consumptive Budget Enforcement & Credential-Safe Terminalization Repair

### STATUS

`REPAIR_REQUIRED`

### REPOSITORY

`kowen9024AI/sovereign`

### EXISTING BRANCH / PR

- Branch: `wo/sovereign-sscm-01a`
- PR: `#5`
- Required predecessor head: `283bd059702c7573fd98512448f7cdfa9683ae43`

Do not create another PR.
Do not merge.
PR #5 remains draft until positive R1 closure.

---

## 1. PURPOSE

The live SSCM qualification is accepted as real evidence that the local executor-neutral collaboration path works:

```text
Claude Code coordinator
-> SSCM blackboard
-> Codex implementer
-> host Git verification
-> Claude Code reviewer
```

R1 repairs three independent fail-open semantics discovered during independent review before the v0.1 implementation can be merged:

1. shortened Git SHA prefixes can satisfy revision comparisons;
2. model-call / cost budgets are declared but not charged against observed executor consumption;
3. final credential scanning occurs after terminal success and experience creation, allowing contradictory `SUCCEEDED` evidence if a late log scan fails.

R1 must repair these without weakening the successful architecture or fabricating another live qualification.

---

## 2. GOVERNING INVARIANTS

```text
MODEL_REPORTED_REVISION != HOST_OBSERVED_REVISION
REVISION_PREFIX_MATCH != EXACT_REVISION_MATCH
DECLARED_BUDGET != CONSUMED_BUDGET
BUDGET_FIELD_PRESENT != BUDGET_ENFORCED
CREDENTIAL_SCAN_PENDING != MISSION_ACCEPTABLE
TERMINAL_SUCCESS != REVERSIBLE_REPORT_FLAG
```

And:

> Recursive learning must never become recursive self-authorization.

Authority remains `NONE` everywhere.

---

## 3. SOURCE FREEZE

Before mutation record:

```text
EXPECTED_HEAD = 283bd059702c7573fd98512448f7cdfa9683ae43
LOCAL_HEAD:
REMOTE_HEAD:
PR_HEAD:
PARITY:
WORKTREE_CLEAN:
PR_DRAFT:
```

Required:

```text
LOCAL_HEAD == REMOTE_HEAD == PR_HEAD == EXPECTED_HEAD
WORKTREE_CLEAN = YES
PR_DRAFT = YES
```

---

# PART A — EXACT REVISION AUTHORITY

## 4. DEFECT

Current result contracts accept Git revisions matching:

```text
^[0-9a-f]{7,40}$
```

and mission verification uses prefix comparison equivalent to:

```python
verified.startswith(model_claim)
```

This permits a model-reported abbreviated SHA to satisfy a gate that claims exact immutable revision equality.

That is not acceptable for SSCM candidate or review custody.

---

## 5. FULL GIT OBJECT ID REQUIRED

For v0.1, require exact 40-hex SHA-1 object IDs for:

```text
claimed_candidate_revision
reviewed_revision
```

Update both collaboration result contracts so these fields, when non-null, match exactly:

```text
^[0-9a-f]{40}$
```

Do not accept abbreviated prefixes in authority-sensitive structured results.

If future repositories use another object format, that requires a versioned contract extension rather than silently widening v0.1.

---

## 6. EXACT COMPARISON

Replace prefix comparisons with exact equality.

Required:

```text
claimed_candidate_revision == VERIFIED_CANDIDATE_REVISION
reviewed_revision == VERIFIED_CANDIDATE_REVISION
```

No `startswith`, prefix normalization, first-match lookup, or Git abbreviation resolution.

If implementer returns null where the contract permits null, host verification may still establish the candidate, but a non-null contradictory claim must fail closed.

---

## 7. ADVERSARIAL REVISION TESTS

Add at minimum:

```text
host = 0123456789abcdef0123456789abcdef01234567
model = 0123456
=> schema reject or exact-comparison reject

host = 0123456789abcdef0123456789abcdef01234567
model = 0123456789abcdef0123456789abcdef01234567
=> pass

host = 0123456789abcdef0123456789abcdef01234567
model = 0123456789abcdef0123456789abcdef01234568
=> fail
```

Cover both IMPLEMENTER and REVIEWER.

Required blockers remain attributable:

```text
CANDIDATE_CLAIM_MISMATCH
REVIEW_SHA_MISMATCH
```

or equally precise existing codes.

---

# PART B — CONSUMPTIVE BUDGET ENFORCEMENT

## 8. DEFECT

`ExecutorRun` records:

```text
model_calls
cost_units
wall_seconds
```

but mission execution currently does not charge observed executor consumption against the frozen mission budget.

The blackboard only prevents an event from *declaring* a larger per-event ceiling than the mission ceiling.

Therefore:

```text
max_model_calls
max_token_or_cost_units
```

can be exceeded in reality while the mission remains green.

Additionally, current blackboard `max_turns` consumption is derived from CLAIM/COMPLETE coordination events, not actual executor turns/model steps.

---

## 9. SEPARATE COORDINATION EVENTS FROM EXECUTOR USAGE

Do not overload coordination-event counts as executor turn accounting.

Introduce an explicit mission usage projection or ledger with at least:

```text
executor_runs
executor_turns
model_calls
cost_units
wall_seconds
```

Exact names may differ.

Usage must be host/runtime-observed from `ExecutorRun`, not model prose.

---

## 10. MODEL CALL ENFORCEMENT

Before launching each executor, compute remaining model-call budget from already observed completed runs.

After each executor returns, charge its observed `model_calls`.

Required:

```text
observed cumulative model_calls > max_model_calls
=> BUDGET_EXCEEDED
=> mission cannot terminal-success
```

Unknown usage must not be silently treated as zero when the configured executor claims it can report the dimension.

For an executor that genuinely cannot report model calls, record the dimension as `UNOBSERVABLE` / `UNKNOWN` and define a fail-closed policy for missions that require that budget dimension.

For SSCM-01A's Claude/Codex roster, use the actual available usage fields from the installed CLIs where supported.

---

## 11. COST / TOKEN-UNIT ENFORCEMENT

`max_token_or_cost_units` must not remain decorative.

Define the v0.1 semantics precisely.

Acceptable R1 choices:

### Option A — cost units

Use observed provider monetary cost when available.

### Option B — normalized usage units

Define an explicit deterministic normalization from available executor usage.

### Option C — unobservable dimension

If exact cross-provider cost/token accounting cannot be honestly normalized in this release, rename/reclassify the field so it is not presented as enforced, and expose:

```text
budget_dimension_observability
```

with fail-closed admission when a mission requires an unavailable budget dimension.

Do not retain a field named as a hard limit while not enforcing it.

---

## 12. MAX TURNS SEMANTICS

Clarify `max_turns`.

If it means executor/model turns, charge actual executor-reported turns.

If it means SSCM coordination transitions, rename it to an unambiguous coordination dimension.

Do not call CLAIM/COMPLETE event count `turns` while executor CLIs independently report model turns.

Compatibility may be preserved with a versioned projection if needed.

---

## 13. WALL CLOCK

Preserve current real wall-clock budget enforcement.

Add tests proving cumulative mission wall time cannot launch a new executor after exhaustion.

Do not rely only on each subprocess timeout independently.

---

## 14. BUDGET EVIDENCE

Mission report / projection must expose observed consumption at closure, at least:

```text
observed_executor_runs
observed_executor_turns
observed_model_calls
observed_cost_or_token_units
observed_wall_seconds
budget_limits
budget_dimensions_observable
budget_exceeded
```

No credential values.

---

## 15. BUDGET ADVERSARIAL TESTS

Add deterministic mock-executor tests for at least:

```text
model calls exactly at limit -> pass
model calls over limit -> block
cost/usage exactly at limit -> pass when observable
cost/usage over limit -> block when observable
unknown required usage dimension -> block
wall budget exhausted before next executor -> block
executor event budget cannot self-raise -> preserve existing pass
```

No live model rerun is required merely to test budget mechanics.

---

# PART C — CREDENTIAL-SAFE TERMINALIZATION

## 16. DEFECT

Current mission finalization order is effectively:

```text
mission terminal COMPLETE
-> projection = SUCCEEDED
-> create SUCCEEDED experience
-> scan durable logs/artifacts
-> if scan finds credential-shaped material, report disposition becomes BLOCKED
```

Because terminal blackboard state rejects later corrective events, a late credential finding can produce contradictory evidence:

```text
blackboard mission = SUCCEEDED
experience = SUCCEEDED
report disposition = BLOCKED
```

This is fail-open evidence ordering.

---

## 17. ACCEPTANCE SCAN MUST PRECEDE TERMINAL SUCCESS

Reorder the positive path so all durable evidence required for acceptance is credential-scanned before appending mission-level terminal success and before creating a SUCCEEDED experience.

Conceptually:

```text
review host verification
-> flush/write all acceptance evidence
-> credential scan
-> scan CLEAN
-> terminal COMPLETE
-> terminal projection
-> experience creation
```

Required:

```text
credential scan not CLEAN
=> no mission SUCCEEDED terminal event
=> no SUCCEEDED experience
```

---

## 18. FAILED/BLOCKED EXPERIENCE POLICY

It is acceptable for a genuinely terminal BLOCKED/FAILED mission to produce an observed experience candidate describing failure, provided the evidence used to create that candidate itself passed credential scanning.

Do not create any durable experience object from evidence known or suspected to contain credentials.

---

## 19. LOGS ARE IN SCOPE

The final credential gate must cover provider stdout/stderr logs and all durable structured artifacts that will be retained or referenced by the experience.

Do not assume earlier `write_json()` scanning covers subprocess log files.

---

## 20. CREDENTIAL TERMINALIZATION TEST

Add a deterministic test where an executor writes secret-shaped material only into a durable stdout/stderr log while returning an otherwise valid structured result.

Expected:

```text
credential scan = REJECTED
mission terminal status != SUCCEEDED
no SUCCEEDED experience emitted
final disposition = BLOCKED
```

Also verify no later `MISSION_TERMINAL` rule prevents recording the blocked outcome because success must never have been appended.

---

# PART D — PRESERVE THE VALID LIVE QUALIFICATION

## 21. HISTORICAL LIVE EVIDENCE

Do not relabel the successful historical mission as fake or failed.

The historical run established:

```text
Claude native local auth works
Codex native ChatGPT auth works
artifact-only handoff works
isolated workspaces work
host Git candidate verification works
independent Claude review works
8-event happy-path chain works
no transcript handoff
no shared writable workspace
credential scan was CLEAN in that actual run
```

R1 repairs generic semantics discovered afterward.

---

## 22. LIVE RERUN POLICY

A second real Claude/Codex dogfood run is NOT automatically required if all R1 changes are purely fail-closed semantic repairs and deterministic tests prove them without changing executor invocation or happy-path orchestration.

Perform one bounded live rerun only if R1 materially changes:

- Claude invocation;
- Codex invocation;
- workspace behavior;
- live event sequence;
- experience mapping on a clean positive mission.

If no live rerun is performed, closure must say:

```text
LIVE_RERUN = NOT_REQUIRED_SEMANTIC_REPAIR_ONLY
```

and retain the original live receipt as historical evidence.

---

# PART E — VALIDATION

## 23. REQUIRED TESTS

Run at minimum:

```text
SSCM focused tests
core contract tests
QM adapter tests
full pytest suite
CI
```

Preserve the frozen four-contract core invariant.

---

## 24. PR READY POLICY

On positive R1 completion only:

```text
PR_DRAFT = NO
PR_READY_FOR_REVIEW = YES
PR_MERGED = NO
```

Do not merge.

If any defect remains, PR stays draft.

---

## 25. REQUIRED CLOSURE REPORT

Return:

```text
SOURCE_PREDECESSOR:
BRANCH:
CANDIDATE_SHA:
REMOTE_SHA:
PARITY:
WORKTREE_CLEAN:

PR:
PR_DRAFT:
PR_READY_FOR_REVIEW:
PR_MERGED:

IMPLEMENTER_REVISION_PATTERN:
REVIEWER_REVISION_PATTERN:
REVISION_COMPARISON_MODE:
SHORT_SHA_ACCEPTED:
SHORT_SHA_TEST:
WRONG_FULL_SHA_TEST:
EXACT_FULL_SHA_TEST:

BUDGET_USAGE_LEDGER:
MAX_TURNS_SEMANTICS:
MODEL_CALLS_OBSERVED:
MODEL_CALLS_ENFORCED:
COST_OR_TOKEN_UNITS_SEMANTICS:
COST_OR_TOKEN_UNITS_OBSERVABLE:
COST_OR_TOKEN_UNITS_ENFORCED:
WALL_TIME_ENFORCED:
UNKNOWN_REQUIRED_BUDGET_FAILS_CLOSED:
BUDGET_REPORT_FIELDS:

CREDENTIAL_SCAN_ORDER:
PROVIDER_LOGS_SCANNED:
TERMINAL_SUCCESS_BEFORE_SCAN:
LATE_LOG_SECRET_TEST:
LATE_LOG_SECRET_MISSION_STATUS:
LATE_LOG_SECRET_EXPERIENCE_CREATED:

HISTORICAL_LIVE_RUN_RETAINED:
HISTORICAL_LIVE_DISPOSITION:
LIVE_RERUN:

SSCM_TESTS:
CORE_CONTRACT_TESTS:
QM_ADAPTER_TESTS:
FULL_TEST_SUITE:
CI:

CREDENTIAL_VALUES_EXPOSED:
AUTHORITY_CHANGE:

FINAL_DISPOSITION:
```

Positive:

`SOVEREIGN_SSCM_LOCAL_A2A_R1_READY`

Remaining semantic defect:

`REPAIR_REQUIRED`

---

## 26. NEXT-PHASE FREEZE

Do not start SSCM-01B parallel fan-out/fan-in until PR #5 R1 is independently reviewed and merged.

Do not integrate Miskatonic yet.

Do not implement QM/OpenClaw adapters yet.

The first ratchet must be a trustworthy single-lane collaboration kernel.
