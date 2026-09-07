# MIRROR — WO-MSK-AGENT-MESH-01A

Canonical authority:

`Miskatonic-System/miskatonic-control-plane@9db17766606ec5f3ef30aecc6321b319966a44a3:docs/work-orders/WO-MSK-AGENT-MESH-01A.md`

This mirror exists solely for local discoverability by agents working from `kowen9024AI/sovereign`. If this file differs from the exact canonical Control Plane file above, the Control Plane file wins.

---

# WO-MSK-AGENT-MESH-01A
## Stigmergic A2A Blackboard, Executor-Neutral Local Swarm & Artifact-Bound Handoff

### AUTHORITY

This file is the canonical source for `WO-MSK-AGENT-MESH-01A`.

Any issue, chat, Sovereign mirror, handoff note, or agent summary is noncanonical if it differs from this file at the exact Control Plane revision containing it.

Authority of every agent, executor, harness, blackboard event, artifact, and model under test remains `NONE` unless a separate canonical Miskatonic authority explicitly says otherwise.

---

# 1. SOURCE

Required canonical predecessors:

```text
Miskatonic Control Plane:
  a2c02a3d75944b3eb119f43cb43f82ee3e8c777e

Sovereign:
  09372d7a0770d83a4f0a27331298c2f14ce0080e

Agent Collaboration Mesh architecture:
  docs/AGENT_COLLABORATION_MESH.md

Miskatonic blackboard event schema:
  schemas/agent-collaboration-blackboard-event-v1.schema.json

Sovereign SSCM architecture:
  docs/SSCM.md

Sovereign portable collaboration contract:
  contracts/collaboration/a2a-collaboration-event.v0.1.schema.json
```

QM live qualification predecessor remains partial and is not a dependency except for cleanup hygiene and negative evidence.

OpenClaw PR #42 remains parked and is comparison evidence only.

### STATUS

`READY_FOR_EXECUTION`

---

# 2. OBJECTIVE

Implement and live-qualify the first Miskatonic **Agent Collaboration Mesh** using only local executor-native authentication and structured artifact/state handoffs.

Reference workflow:

```text
Claude coordinator
      |
      | PUBLISH implementation task
      v
Control Plane blackboard
      |
      | lease isolated worktree
      v
Codex implementer
      |
      | COMPLETE with candidate artifact
      v
Control Plane host verification
      |
      | freeze candidate SHA
      v
Claude reviewer
      |
      | COMPLETE with review evidence
      v
Control Plane acceptance projection
```

The coordinator, implementer, and reviewer do not need to share transcripts or run through the same harness.

---

# 3. CORE INVARIANTS

```text
COLLABORATION_PROTOCOL != HARNESS
HARNESS != EXECUTOR
EXECUTOR != MODEL_API
WORKSPACE != BLACKBOARD
MODEL_REPORTED_SHA != HOST_OBSERVED_SHA
EXECUTION_SUCCESS != ACCEPTANCE
BLACKBOARD_EVENT != AUTHORITY
SHARED_COORDINATION_STATE != SHARED_WRITABLE_WORKSPACE
```

And:

> Recursive self-improvement must never become recursive self-authorization.

---

# 4. EXPLICITLY OUT OF SCOPE

Do not require or configure:

```text
QM as credential broker
OpenClaw as swarm transport
LiteLLM
new API keys
Claude setup-token
Codex auth copying
public ingress
Cloudflare Tunnel
Codespaces
Hetzner
AWS
Oracle
new hardware
paired remote nodes
DGX/Spark
Workboard mutation
```

Do not patch QM or OpenClaw.

Do not modify Workbench PR #42.

---

# 5. GATE 0 — PRIOR QM EXPERIMENT HYGIENE

Before new live executor runs, verify the prior disposable QM qualification is no longer consuming runtime resources.

Required:

```text
QM_PRIOR_EXPERIMENT_CLEANUP_CONFIRMED:
QM_CORE_RUNNING:
QM_POSTGRES_RUNNING:
QM_LOCAL_SANDBOX_RESOURCES_REMAINING:
QM_CLOUD_RESOURCES_REMAINING:
QM_CREDENTIAL_VALUES_CAPTURED:
```

Positive gate:

```text
QM_PRIOR_EXPERIMENT_CLEANUP_CONFIRMED = YES
QM_CLOUD_RESOURCES_REMAINING = NONE
QM_CREDENTIAL_VALUES_CAPTURED = NONE
```

If the old agent has not yet completed cleanup, perform only supported local cleanup and verification. Do not use direct database surgery to manufacture a clean state.

Do not block forever on preservation of non-running local source checkout `~/qm-qual/qm`; it may remain as source evidence if inert and credential-safe.

---

# 6. REPOSITORY / BRANCH POLICY

Primary implementation repository:

`Miskatonic-System/miskatonic-control-plane`

Create one implementation branch from exact canonical Control Plane predecessor:

`agent/agent-mesh-01a-local-a2a-v0.1`

Open one draft PR against `main`.

Successful completion:

```text
PR_DRAFT = NO
PR_READY_FOR_REVIEW = YES
PR_MERGED = NO
```

Blocked or repair-required completion remains draft.

Do not merge from this WO.

---

# 7. REUSE EXISTING CONTROL PLANE MACHINERY

Before adding code, inspect and reuse existing canonical components wherever semantically appropriate:

- collaboration plan;
- collaboration scheduler;
- collaboration orchestrator;
- collaboration worktree leases;
- candidate custody/reconciliation;
- execution receipts;
- local validation;
- Git head verification;
- deterministic canonicalization helpers;
- existing SQLite durable-ledger patterns.

Do not build a parallel orchestration stack merely because this WO uses the word `mesh`.

The mesh is a new coordination projection/transport over existing authority boundaries.

---

# 8. PACKAGE LAYOUT

Preferred implementation surface:

```text
src/miskatonic_control/mesh/
    __init__.py
    contracts.py
    blackboard.py
    executor.py
    local_cli.py
    orchestration.py
    receipts.py
```

Equivalent structure is acceptable if ownership remains clear.

Do not put provider-specific code into generic collaboration contracts.

---

# 9. BLACKBOARD STORE

Implement a local durable blackboard using Python stdlib `sqlite3` for 01A.

Reason:

- no new service dependency;
- existing Control Plane SQLite precedent;
- deterministic local-first qualification;
- easy restart/recovery testing;
- Postgres can be a later adapter without changing the logical contract.

The SQLite implementation is a substrate, not semantic authority.

---

# 10. BLACKBOARD EVENT CONTRACT

Use the canonical schema:

`miskatonic.agent-collaboration-blackboard-event.v1`

The implementation must validate every admitted event before durable insertion.

Minimum event verbs:

```text
CLAIM
PUBLISH
OBSERVE
COMPLETE
REJECT
RETRY
CANCEL
```

`authority` is always:

`NONE`

---

# 11. APPEND-ONLY EVENT IDENTITY

Each accepted event must have deterministic identity protection.

At minimum persist:

```text
event_id
mission_id
task_id
predecessor_event_id
verb
actor role/executor/session/runtime
workspace lease ref
base/candidate SHA refs
input/output artifact refs
status
budget
blocker
observed_at
canonical event digest
```

The blackboard may append terminal/corrective events.

It may not rewrite historical event payloads in place.

---

# 12. IDEMPOTENCY

Replaying the exact same event identity + canonical payload must be idempotent.

Required behavior:

```text
same event_id + same canonical payload
=> no duplicate semantic event

same event_id + different payload
=> EVENT_ID_CONFLICT
=> fail closed
```

No first/latest winner rule.

---

# 13. PREDECESSOR CONSISTENCY

If `predecessor_event_id` is supplied, it must resolve within the same mission.

Reject:

```text
missing predecessor
cross-mission predecessor
self predecessor
```

Do not require a single linear chain for the whole mission; multiple task branches are allowed.

But each individual event's declared predecessor must be exact and valid.

---

# 14. COORDINATION STORE IS NOT GIT AUTHORITY

The blackboard may record:

```text
base_sha
candidate_sha
```

but those are references/evidence claims.

Candidate acceptance requires existing host-side Git verification.

Required invariant:

```text
BLACKBOARD_CANDIDATE_SHA
!=
VERIFIED_CANDIDATE_SHA
```

until the Control Plane independently observes Git.

---

# 15. ARTIFACT REFERENCES

Large data does not belong in the SQLite blackboard.

Use references for:

- Git commits;
- files;
- logs;
- test results;
- reviews;
- receipts;
- other bounded artifacts.

Where a digest is available, capture it.

Do not store full transcripts in blackboard rows.

---

# 16. EXECUTOR ADAPTER CONTRACT

Introduce a provider-neutral executor interface.

Minimum normalized capabilities:

```text
executor_id
runtime_family
availability
native_auth_class
working_directory_support
read_support
write_support
shell_or_execution_support
structured_output_support
session_identity_support
resume_support
cancellation_support
authority = NONE
```

The upper mesh may not infer availability from provider name alone.

---

# 17. LOCAL CLAUDE EXECUTOR

Feature-detect the installed Claude Code CLI and its real supported invocation contract.

Do not invent flags.

Use the operator's existing provider-supported local authentication.

Do not copy tokens into Control Plane config, environment files, receipts, prompts, logs, or worktrees.

Read-only auth/status discovery is permitted if supported by the CLI and emits no secret material.

Required positive capability for the dogfood:

```text
LOCAL_CLAUDE_AVAILABLE = YES
LOCAL_CLAUDE_NATIVE_AUTH = READY
LOCAL_CLAUDE_WORKING_DIRECTORY = SUPPORTED
```

If not, stop with exact blocker.

---

# 18. LOCAL CODEX EXECUTOR

Feature-detect the installed Codex CLI and its real supported invocation contract.

Use the host's existing ChatGPT/subscription login directly.

Do not copy or transform `~/.codex` auth state into another harness.

No API-key fallback.

Required:

```text
LOCAL_CODEX_AVAILABLE = YES
LOCAL_CODEX_NATIVE_AUTH = READY
LOCAL_CODEX_WORKING_DIRECTORY = SUPPORTED
```

The failure of QM's copied child auth is irrelevant if direct local Codex works.

---

# 19. OPTIONAL ANTIGRAVITY

Antigravity remains `EXTERNAL_ONLY` for 01A unless a stable local noninteractive executor contract already exists and can be proven without additional authorization work.

Do not block 01A on Antigravity.

Do not use its OAuth outside its supported environment.

---

# 20. LOCAL MODEL EXECUTOR

A local model adapter is optional for 01A.

If implemented, it may expose low-risk read/classification work through the existing local inference stack.

It is not required for the positive three-role coding proof.

---

# 21. EXECUTOR CREDENTIAL FIREWALL

The mesh must not become a credential broker.

Required:

```text
executor native auth
      |
      v
provider CLI / local runtime
```

not:

```text
credential
   -> blackboard
   -> generic mesh secret store
   -> another executor
```

No credential values in blackboard or evidence.

---

# 22. REFERENCE ROSTER

The first positive mission uses:

```text
COORDINATOR
  executor = Claude Code local CLI

IMPLEMENTER
  executor = Codex local CLI

REVIEWER
  executor = Claude Code local CLI
```

Coordinator and reviewer must be distinct execution sessions/runs.

Implementer and reviewer runtime families must be distinct:

```text
Codex != Claude Code
```

---

# 23. WHO ORCHESTRATES

The deterministic Control Plane orchestrator owns sequencing.

The Claude coordinator may generate or refine a task specification, but it may not dynamically grant new roles, budgets, tools, or authority.

For the dogfood, the Control Plane sequence is frozen:

```text
COORDINATE
-> IMPLEMENT
-> HOST VERIFY
-> REVIEW
-> FINAL VERIFY
```

No model decides to spawn additional actors.

---

# 24. COORDINATOR OUTPUT

Coordinator receives a bounded mission prompt and must produce a structured implementation instruction artifact.

Minimum content:

```text
mission_id
role = COORDINATOR
target_role = IMPLEMENTER
objective
allowed_files
expected_content
verification_requirements
authority = NONE
```

If the installed Claude CLI supports structured JSON output, use it.

Otherwise use a separately validated output file/artifact contract.

Do not infer task state from unstructured prose if a structured artifact can be required.

---

# 25. DOGFOOD TARGET

Use:

`kowen9024AI/sovereign`

from then-current canonical `main`.

Create a disposable implementation worktree/branch.

Suggested branch:

`dogfood/agent-mesh-01a-implementer`

The branch is never merged.

---

# 26. DOGFOOD TASK

The implementation instruction shall require creation of exactly:

`docs/agent-mesh-dogfood.txt`

with exact content:

```text
MSK_AGENT_MESH_OK
```

The Codex implementer must commit the result.

No other intended mutation.

---

# 27. WORKTREE LEASE — IMPLEMENTER

Before Codex starts, allocate and attest an isolated writable worktree through the existing collaboration/worktree lease semantics.

Required host-observed facts:

```text
repository identity
git common dir
canonical realpath
branch
HEAD SHA
dirty state
writable ownership
```

Reject:

```text
canonical checkout writable
unexpected dirty state
overlapping writable path
shared writable lease
branch collision
```

---

# 28. IMPLEMENTER INPUT

Codex receives only bounded inputs needed to implement:

- coordinator artifact;
- exact base SHA;
- worktree path;
- expected output/result contract;
- allowed file set;
- budget.

Do not forward the coordinator's full transcript.

---

# 29. IMPLEMENTER OUTPUT

Require a structured result artifact equivalent to:

```text
role = IMPLEMENTER
status
claimed_candidate_sha
changed_files
tests_run
blockers
summary
authority = NONE
```

The claimed SHA remains informational until host verification.

---

# 30. HOST CANDIDATE FREEZE

After Codex completes:

Control Plane independently verifies:

```text
candidate SHA
base ancestry
exact changed file set
exact file content
worktree clean after commit
```

Required:

```text
changed files = [docs/agent-mesh-dogfood.txt]
content = MSK_AGENT_MESH_OK\n
candidate descends from exact base
```

Then freeze:

`VERIFIED_CANDIDATE_SHA`

---

# 31. REVIEWER WORKSPACE

Create a separate reviewer worktree at exactly `VERIFIED_CANDIDATE_SHA`.

Preferred state:

`detached HEAD`

Reviewer does not own the implementation branch.

No shared writable path with implementer.

---

# 32. REVIEWER INPUT

Claude reviewer receives:

- exact frozen candidate SHA;
- exact base SHA;
- expected file/content;
- review checklist;
- reviewer worktree;
- bounded budget.

Do not forward Codex's full transcript.

---

# 33. REVIEWER OUTPUT

Require structured review evidence:

```text
role = REVIEWER
reviewed_sha
verdict = ACCEPT | REPAIR_REQUIRED
findings
blocking_findings
tests_run
summary
authority = NONE
```

Reviewer may not repair its own finding in 01A.

---

# 34. REVIEW SHA VERIFICATION

The Control Plane independently observes reviewer worktree HEAD.

Positive requires:

```text
REVIEWER_WORKTREE_HEAD == VERIFIED_CANDIDATE_SHA
reviewed_sha == VERIFIED_CANDIDATE_SHA
```

A model assertion alone is insufficient.

---

# 35. NO REPAIR LOOP IN 01A

For this first tiny dogfood, do not implement autonomous repair loops.

If reviewer returns `REPAIR_REQUIRED`:

stop with:

`AGENT_MESH_DOGFOOD_REPAIR_REQUIRED`

Repair loops belong to a later WO after one clean pass is proven.

---

# 36. BLACKBOARD EVENT SEQUENCE

The live mission must produce a durable event chain at least equivalent to:

```text
1 PUBLISH  coordinator task
2 CLAIM    implementer lease/work
3 COMPLETE implementer result
4 OBSERVE  host candidate verification
5 CLAIM    reviewer work
6 COMPLETE reviewer result
7 OBSERVE  host review verification
8 COMPLETE mission terminal projection
```

Exact event count may differ if required for valid state transitions, but the semantic sequence must remain reconstructable without transcript parsing.

---

# 37. EVENT / ARTIFACT CORRELATION

Every role result must be correlated to:

```text
mission_id
task_id
executor_id
session/run id where available
workspace lease ref
input refs
output refs
candidate/review identity
```

Unknown values are explicit `null`/UNKNOWN where contract permits.

Never invent a provider session id.

---

# 38. BLACKBOARD RESTART TEST

Demonstrate that after closing and reopening the SQLite blackboard:

- prior events remain intact;
- event digests remain identical;
- terminal state reconstructs deterministically;
- no duplicate event is inserted by replay;
- no event is silently dropped.

---

# 39. CONCURRENCY / CLAIM TEST

Add a deterministic test showing that two workers cannot both successfully claim the same exclusive task/lease when exclusivity is required.

Expected:

```text
one claim admitted
one claim rejected/conflicted
```

Do not resolve by nondeterministic last-write-wins.

---

# 40. BUDGET GATE

Implement mission/task budgets sufficient to stop runaway loops.

At minimum enforce:

```text
max_turns
max_wall_seconds
max_repair_loops
```

Where measurable, also support:

```text
max_model_calls
max_token_or_cost_units
```

Unknown token/cost telemetry must not be fabricated.

---

# 41. BUDGET EXHAUSTION

When a budget is exhausted:

```text
status = BLOCKED or FAILED per declared policy
blocker_code = BUDGET_EXHAUSTED
```

An agent cannot request or grant itself a larger budget through blackboard output.

---

# 42. EXECUTOR FAILURE

Normalize executor failures without parsing optimistic prose.

Minimum failure classes:

```text
EXECUTOR_UNAVAILABLE
AUTH_NOT_READY
INVOCATION_FAILED
TIMEOUT
OUTPUT_CONTRACT_VIOLATION
WORKSPACE_VIOLATION
CANCELLED
```

Raw stdout/stderr is evidence, not lifecycle authority.

---

# 43. CREDENTIAL DEFENSE

Any stored executor artifact/receipt intended for durable evidence must be rescanned for credential-shaped values before publication.

Reuse existing secret-defense patterns where available.

Do not store provider login files or environment dumps.

---

# 44. SOVEREIGN PORTABLE CONTRACT CROSS-CHECK

After the Miskatonic mission succeeds, emit or derive one non-authoritative portable event projection compatible in meaning with:

`sovereign.a2a-collaboration-event.v0.1`

Do not copy Miskatonic-private fields into Sovereign.

This is a compatibility check only.

No Sovereign promotion/evaluation is required in 01A.

---

# 45. SOVEREIGN EXPERIENCE OPTIONAL CHECK

If the completed mission can be represented honestly as `sovereign.experience.v0.1` without inventing fields, produce a local candidate artifact for test purposes.

Do not commit a promoted experience to canonical Sovereign state.

`experience != evaluation != promotion`

---

# 46. WORKBENCH

No new Workbench UI is required in 01A.

Produce a read-only projection schema/data shape sufficient for a later Workbench cockpit to show:

```text
mission
actors
current task
status
handoffs
candidate SHA
review state
blockers
budgets
```

A later WO owns visual integration.

---

# 47. QM / OPENCLAW ADAPTERS

Do not implement full QM or OpenClaw adapters in 01A.

At most define interface-compatible fixture/skeleton tests proving they could attach later without changing mesh semantics.

No live harness execution required.

---

# 48. TEST MATRIX — CONTRACTS

Add deterministic tests for:

1. valid blackboard event;
2. unknown field rejected;
3. authority other than NONE rejected;
4. invalid Git SHA rejected where applicable;
5. invalid artifact digest rejected;
6. unsupported verb rejected;
7. invalid budget rejected.

---

# 49. TEST MATRIX — LEDGER

Add tests for:

1. append + read;
2. restart persistence;
3. deterministic canonical digest;
4. duplicate identical event idempotent;
5. duplicate conflicting event rejected;
6. missing predecessor rejected;
7. cross-mission predecessor rejected;
8. self predecessor rejected;
9. order-independent replay reconstruction;
10. terminal mission reconstruction.

---

# 50. TEST MATRIX — CLAIMS / WORKSPACES

Add tests for:

1. unique exclusive claim passes;
2. concurrent duplicate claim fails closed;
3. canonical checkout autonomous write rejected;
4. dirty worktree rejected;
5. overlapping writable workspace rejected;
6. reviewer exact SHA binding enforced.

Reuse existing lease/admission logic rather than duplicate if already covered.

---

# 51. TEST MATRIX — EXECUTORS

Using fakes/mocks for unit tests, prove:

1. executor unavailable -> explicit failure;
2. auth not ready -> explicit failure;
3. timeout -> explicit failure;
4. malformed structured output -> contract failure;
5. model-reported SHA cannot bypass host verification;
6. credential-shaped output is rejected from durable evidence;
7. native-auth class is metadata only, not credential material.

---

# 52. LIVE RUN BUDGET

Preferred positive live model calls:

```text
Claude coordinator: 1
Codex implementer:   1
Claude reviewer:     1
```

No additional model run unless required to diagnose an executor invocation defect after deterministic inspection.

No autonomous retries.

---

# 53. CLEANUP

After evidence capture:

- delete disposable Sovereign dogfood branch;
- remove implementation/reviewer worktrees;
- leave canonical Sovereign main unchanged by dogfood;
- close executor processes launched only for this mission;
- preserve no credentials in artifacts;
- leave blackboard evidence fixture/report only where intentionally committed to the Control Plane candidate.

Do not delete ordinary user CLI login state.

---

# 54. DAILY PATH REGRESSION

Verify after live qualification:

```text
Claude CLI normal usage healthy
Codex CLI normal usage healthy
Control Plane tests healthy
no QM/OpenClaw config mutation
no cloud resources
no public ingress
```

---

# 55. REPORTING / EVIDENCE

Create a bounded report under:

`reports/work-orders/WO-MSK-AGENT-MESH-01A/`

Do not commit raw provider transcripts containing unreviewed external data or secrets.

Prefer normalized receipts, hashes, test summaries, and exact command identities.

---

# 56. REQUIRED VALIDATION

Run the repository's full test suite plus all new focused tests.

At minimum report:

```text
FOCUSED_MESH_TESTS:
FULL_CONTROL_PLANE_TESTS:
SCHEMA_VALIDATION:
TYPECHECK_OR_STATIC_CHECKS_IF_PRESENT:
```

Use authoritative exit codes.

---

# 57. SUCCESS CRITERIA

Positive 01A requires ALL:

```text
prior QM disposable runtime cleaned
SQLite blackboard implemented
blackboard events schema-valid
append-only/idempotent behavior proven
executor-neutral adapter contract implemented
existing local Claude auth reused
existing local Codex auth reused
no new credential handoff
three distinct live role runs
Codex implementer writes isolated worktree
host verifies/fixes candidate identity
Claude reviewer uses separate exact-SHA worktree
review exact candidate attested
structured event chain reconstructs mission
no transcript required for lifecycle reconstruction
budget enforcement tested
no shared writable workspace
no cloud/public ingress
cleanup confirmed
full tests green
```

---

# 58. POSITIVE DISPOSITION

`LOCAL_AGENT_COLLABORATION_MESH_QUALIFIED`

This means the local executor-neutral A2A mechanism works.

It does NOT mean:

- autonomous merge is allowed;
- QM/OpenClaw are retired;
- Sovereign is bound into Miskatonic;
- repair loops are authorized;
- remote workers are qualified;
- blackboard state is institutional knowledge;
- executor output is acceptance authority.

---

# 59. FAILURE DISPOSITIONS

Use exact blocker-specific closure where appropriate:

```text
LOCAL_AGENT_MESH_BLOCKED
REPAIR_REQUIRED
EXECUTOR_AUTH_NOT_READY
WORKSPACE_ADMISSION_BLOCKED
BLACKBOARD_SEMANTICS_BLOCKED
AGENT_MESH_DOGFOOD_REPAIR_REQUIRED
```

Do not downgrade a real failure into success because the architecture tests pass.

---

# 60. REQUIRED CLOSURE REPORT

Return exactly:

```text
SOURCE_MAIN:
BRANCH:
CANDIDATE_SHA:
REMOTE_SHA:
PARITY:
WORKTREE_CLEAN:

PR:
PR_DRAFT:
PR_READY_FOR_REVIEW:
PR_MERGED:

QM_PRIOR_EXPERIMENT_CLEANUP_CONFIRMED:
QM_RESIDUAL_RUNTIME:
QM_CLOUD_RESOURCES:

BLACKBOARD_SCHEMA:
BLACKBOARD_STORAGE_ENGINE:
BLACKBOARD_PATH:
BLACKBOARD_APPEND_ONLY:
BLACKBOARD_RESTART_PASS:
BLACKBOARD_IDEMPOTENCY_PASS:
BLACKBOARD_CONFLICT_PASS:
BLACKBOARD_PREDECESSOR_PASS:
BLACKBOARD_CONCURRENT_CLAIM_PASS:

EXECUTOR_CONTRACT:
CLAUDE_CLI:
CLAUDE_VERSION:
CLAUDE_NATIVE_AUTH_READY:
CLAUDE_NEW_CREDENTIAL_MATERIAL_REQUIRED:

CODEX_CLI:
CODEX_VERSION:
CODEX_NATIVE_AUTH_READY:
CODEX_NEW_CREDENTIAL_MATERIAL_REQUIRED:

COORDINATOR_EXECUTOR:
COORDINATOR_SESSION_OR_RUN:
COORDINATOR_RESULT_CONTRACT:

IMPLEMENTER_EXECUTOR:
IMPLEMENTER_SESSION_OR_RUN:
IMPLEMENTER_WORKTREE:
IMPLEMENTER_BRANCH:
IMPLEMENTER_CLAIMED_SHA:

VERIFIED_CANDIDATE_SHA:
CANDIDATE_SHA_SOURCE:
CANDIDATE_DESCENDS_FROM_BASE:
CHANGED_FILE_SET_EXACT:
DOGFOOD_CONTENT_EXACT:
IMPLEMENTER_WORKTREE_CLEAN:

REVIEWER_EXECUTOR:
REVIEWER_SESSION_OR_RUN:
REVIEWER_WORKTREE:
REVIEWER_WORKTREE_HEAD:
REVIEWED_SHA:
REVIEW_VERDICT:
REVIEW_BLOCKING_FINDINGS:

SESSION_DISTINCTNESS:
IMPLEMENTER_REVIEWER_RUNTIME_DISTINCTNESS:
WRITABLE_WORKSPACE_COLLISIONS:
CANONICAL_CHECKOUT_AUTONOMOUS_WRITE:

BLACKBOARD_EVENT_COUNT:
BLACKBOARD_TERMINAL_STATE:
LIFECYCLE_RECONSTRUCTABLE_WITHOUT_TRANSCRIPT:

BUDGET_MAX_TURNS:
BUDGET_MAX_WALL_SECONDS:
BUDGET_MAX_REPAIR_LOOPS:
BUDGET_EXHAUSTION_TEST:

SOVEREIGN_PORTABLE_EVENT_COMPATIBILITY:
SOVEREIGN_EXPERIENCE_CANDIDATE_CREATED:
SOVEREIGN_PROMOTION_PERFORMED:

DOGFOOD_BRANCH_DELETED:
DOGFOOD_WORKTREES_DELETED:
SOVEREIGN_MAIN_MUTATED_BY_DOGFOOD:

QM_CONFIG_MUTATED:
OPENCLAW_CONFIG_MUTATED:
PUBLIC_INGRESS:
CLOUD_RESOURCES:
NEW_API_KEYS:
CREDENTIAL_VALUES_EXPOSED:
AUTHORITY_CHANGE:

FOCUSED_MESH_TESTS:
FULL_CONTROL_PLANE_TESTS:
SCHEMA_VALIDATION:
STATIC_CHECKS:

REAL_CLAUDE_COORDINATOR_RUNS:
REAL_CODEX_IMPLEMENTER_RUNS:
REAL_CLAUDE_REVIEWER_RUNS:
OTHER_MODEL_RUNS:

FINAL_DISPOSITION:
```

---

# 61. PR READY POLICY

On positive completion only, mark the implementation PR ready for review.

Required:

```text
PR_DRAFT = NO
PR_READY_FOR_REVIEW = YES
PR_MERGED = NO
```

Do not merge.

---

# 62. NEXT PHASE FREEZE

Do not implement these in 01A:

```text
autonomous repair loop
multi-mission scheduler
Postgres blackboard
Workbench cockpit UI
QM mesh adapter
OpenClaw mesh adapter
Antigravity native adapter
remote laptop/desktop nodes
Oracle worker
Crabbox elastic worker
DGX/Spark compute node
Sovereign automatic promotion
```

After successful 01A, choose the next phase from evidence.

Likely candidates:

```text
01B repair loop + multi-task DAG
01C Workbench mission/cockpit projection
01D QM/OpenClaw adapter comparison on the mesh
01E distributed executor nodes
```

The local executor-neutral mesh comes first.
