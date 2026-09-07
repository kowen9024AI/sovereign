# WO-SOVEREIGN-SSCM-01A

## Local Executor-Neutral A2A Swarm, Stigmergic Blackboard & Artifact-Bound Coding Handoff

Sovereign-native work order. Canonical in this repository (see `docs/work-orders/README.md`).
Execution report: [`WO-SOVEREIGN-SSCM-01A-REPORT.md`](WO-SOVEREIGN-SSCM-01A-REPORT.md).

### STATUS

`EXECUTED` — see report for disposition.

### PRIMARY REPOSITORY

`kowen9024AI/sovereign`

### REQUIRED CANONICAL BASE

`cf6c55fe5fadd389dcd34132514b88e1bc92bcd8`

### BRANCH

`wo/sovereign-sscm-01a`

### PULL REQUEST

One draft PR against `main`. On successful completion: `PR_DRAFT = NO`, `PR_READY_FOR_REVIEW = YES`, `PR_MERGED = NO`. Do not merge.

---

# 1. PURPOSE

Implement and live-qualify the first working **Sovereign Swarm Control Mesh (SSCM)**. Prove that Sovereign can coordinate heterogeneous coding agents locally through structured state and artifact-bound handoffs without requiring one common agent harness, one common model API, copied subscription credentials, full transcript exchange, shared writable repository state, or cloud infrastructure.

Reference live workflow:

```text
Claude Code coordinator -> structured PUBLISH -> SSCM blackboard -> bounded handoff
-> Codex implementer (isolated writable worktree) -> candidate artifact -> host Git verification
-> frozen candidate -> Claude Code reviewer (separate exact-SHA worktree) -> review artifact
-> Sovereign terminal mission state
```

# 2. GOVERNING INVARIANTS

```text
COLLABORATION_PROTOCOL != HARNESS
HARNESS != EXECUTOR
EXECUTOR != MODEL_API
WORKSPACE != BLACKBOARD
SHARED_COORDINATION_STATE != SHARED_WRITABLE_REPOSITORY_STATE
MODEL_REPORTED_SHA != HOST_OBSERVED_SHA
EXECUTION_SUCCESS != EVALUATION
EVALUATION != PROMOTION
PROMOTION != TRAINING_AUTHORITY
BLACKBOARD_EVENT != AUTHORITY
```

Recursive learning must never become recursive self-authorization. Every executor, event, receipt, experience, artifact, and model run has `authority = NONE`.

# 3-4. OWNERSHIP / MISKATONIC

Sovereign-first. Sovereign owns portable A2A event semantics, the local blackboard, executor-neutral coordination, mission/task state, bounded budgets, artifact references, local executor adapters, and experience capture. Sovereign does not own any Miskatonic authority; no Miskatonic repository is a runtime dependency. Later path: `REGISTER -> REFERENCE -> QUALIFY -> BIND` (not implemented in 01A).

# 5-6. EXISTING CONTRACTS AND QM ADAPTER

Preserve the four frozen core contracts. SSCM stays under `contracts/collaboration/`. Preserve `WO-SOVEREIGN-QM-ADAPTER-00A` at `STRUCTURALLY_VALIDATED`; QM is not required.

# 7. PRIOR QM EXPERIMENT

Verify the disposable QM qualification consumes no runtime resources; stop leftovers with supported tooling; no database surgery; no `claude setup-token`; no copied Codex auth tests.

# 8-10. IMPLEMENTATION SURFACE AND BLACKBOARD

Package `sscm/` with separate coordination, executor-adapter and promotion/evaluation modules. Blackboard on stdlib `sqlite3`; the logical contract must not depend on SQLite semantics. Persist coordination facts only (missions, tasks, events, actor assignments, executor/session/run/workspace refs, budgets, retry counters, blockers, artifact refs, digests, timestamps, terminal state). Never transcripts, source trees, large patches, credentials, tokens, whole logs.

# 11-15. EVENTS, APPEND-ONLY, IDEMPOTENCY, PREDECESSORS, BUDGET

Verbs `CLAIM PUBLISH OBSERVE COMPLETE REJECT RETRY CANCEL`; every event validates before persistence. Append-only; correction is another event. Same event_id + same payload = idempotent; same event_id + different payload = `EVENT_ID_CONFLICT`. Predecessor must exist, share the mission, differ from the event, and satisfy the transition; histories may branch. Frozen mission budget with `max_active_actors, max_turns, max_model_calls, max_wall_seconds, max_repair_loops, max_consecutive_failures, max_token_or_cost_units`; 01A uses `max_active_actors = 1`, `max_repair_loops = 0`; exhaustion is a deterministic blocked/terminal state; an agent may not raise its own budget.

# 16-21. EXECUTORS

Executor-neutral capability projection with `authority = NONE`. SSCM is not a credential broker: task + cwd + output contract go to a native executor that authenticates with its own provider. Feature-detect the installed Claude Code and Codex CLIs; use their existing native logins; no `CLAUDE_CODE_OAUTH_TOKEN` handoff, no `claude setup-token`, no copied `~/.codex`, no child auth file, no API-key fallback. Antigravity `EXTERNAL_ONLY`; local open-weight models optional only.

# 22-23. ROSTER AND ORCHESTRATION

`COORDINATOR = Claude Code`, `IMPLEMENTER = Codex`, `REVIEWER = Claude Code`; coordinator run != reviewer run; implementer runtime family != reviewer runtime family. A deterministic Sovereign controller owns the frozen sequence `COORDINATOR -> IMPLEMENTER -> HOST VERIFY -> REVIEWER -> HOST VERIFY -> TERMINAL`; no recursive spawning, autonomous repair, or extra actors.

# 24-45. DOGFOOD PROTOCOL

Dogfood against `kowen9024AI/sovereign`; canonical `main` observation-only; disposable branch `dogfood/sscm-01a-implementer`, never merged; `CANONICAL_CHECKOUT_AUTONOMOUS_WRITE = NO`; isolated worktrees with host-observed realpath, common dir, base SHA, branch, dirty state. Coordinator emits a structured instruction (mission_id, task_id, role, target_role, objective, allowed_files, expected_content, verification_requirements, authority NONE) for exactly `docs/sscm-dogfood.txt` = `SOVEREIGN_SSCM_OK\n`; `PUBLISH`. Implementer `CLAIM`; receives only the structured artifact, exact base SHA, cwd, allowed files, expected content, commit behaviour, result schema; creates, writes, commits; `COMPLETE` with an informational claimed revision. Host verifies SHA, ancestry, changed files, content, clean worktree; freezes `VERIFIED_CANDIDATE_REVISION`; `OBSERVE`. Reviewer worktree detached at that SHA; `CLAIM`; reviewer receives only base, verified candidate, expected file/content, checklist, worktree path, result schema; returns `ACCEPT | REPAIR_REQUIRED`; `COMPLETE`. Host verifies reviewer HEAD == reviewed_revision == verified; `OBSERVE`; SHA mismatch fails closed. `REPAIR_REQUIRED` stops (`SSCM_DOGFOOD_REPAIR_REQUIRED`). Positive terminal `COMPLETE` (SUCCEEDED) needs valid predecessors, host-verified candidate, exact review, ACCEPT, zero blocking findings, budgets intact. Required chain: `PUBLISH, CLAIM, COMPLETE, OBSERVE, CLAIM, COMPLETE, OBSERVE, COMPLETE`.

# 46-51. EXPERIENCE, NO EVALUATION/PROMOTION, ARTIFACTS, CREDENTIAL SCAN, TRANSCRIPTS

Convert the terminal mission into an `experience.v0.1` OBSERVED candidate (`authority NONE`, `procedure_candidate_ref null`, `training_candidate_ref null`). No `evaluation.v0.1`, no promotion, no training authorization. Artifacts under `.sovereign/runs/<mission-id>/` (ignored); commit only a sanitized report. Independently scan all durable evidence for secret-shaped material; reject on hit; never trust `credentials_exposed = false`. Provider transcripts are diagnostic and noncanonical.

# 52-58. TESTS AND CI

Restart test; bounded concurrent claim test (one wins, one deterministic conflict); order determinism; failure cases (invalid schema, duplicate id same/different payload, missing/cross-mission/self predecessor, budget exceeded, claim collision, candidate claim != host candidate, review SHA mismatch, credential-shaped artifact, terminal mission before review, authority != NONE); collaboration-contract tests outside the frozen core set; all existing tests green; CI green on the hosted runner with no provider credentials.

# 59-61. BUDGET, CLEANUP, QM/OPENCLAW

Preferred live budget 1/1/1; no retry for prose; no repair loop. Delete the dogfood branch and worktrees (`DOGFOOD_BRANCH_MERGED = NO`, `DOGFOOD_BRANCH_RETAINED = NO`, `DOGFOOD_WORKTREES_RETAINED = NO`, `SOVEREIGN_MAIN_MUTATED_BY_DOGFOOD = NO`). QM and OpenClaw recorded as optional future SSCM adapters only.

# 62-66. DISPOSITION, PR, CLOSURE, FREEZE

Positive: `SOVEREIGN_SSCM_LOCAL_A2A_QUALIFIED` (SSCM coordination works locally; Claude and Codex collaborate without sharing a harness; native auth stays native; artifact handoff, isolated worktrees, host verification, independent review work; execution yields an experience candidate; no promotion/authority). Not implied: production readiness, parallel swarming, recursive repair, QM/OpenClaw adapters, Miskatonic integration, autonomous merge. Runtime blocker: `SOVEREIGN_SSCM_LOCAL_A2A_BLOCKED`. Defect: `REPAIR_REQUIRED`. Closure report fields per section 64. Next-phase freeze: no parallel agents, repair loops, dynamic DAGs, Miskatonic binding, QM/OpenClaw runtime adapters, memory-plane or training integration, remote/cloud workers. Successors: SSCM-01B (parallel claims + DAG), 01C (bounded repair + evaluator), 02A (QM), 02B (OpenClaw), 03A (GBrain/Memorable), 04A (River/local specialization), then Miskatonic exact-release qualification.

```text
SOVEREIGN PROVES THE PROTOCOL FIRST. MISKATONIC MAY CONSUME IT LATER.
THE COLLABORATION PROTOCOL IS PORTABLE. THE EXECUTOR IS REPLACEABLE. AUTHORITY REMAINS EXTERNAL AND EXPLICIT.
```
