# WO-SOVEREIGN-SSCM-01B

## Bounded Parallel Fan-Out/Fan-In, Multi-Predecessor DAG Coordination & Deterministic Git Integration

Sovereign-native work order, canonical in this repository. Execution report: [`WO-SOVEREIGN-SSCM-01B-REPORT.md`](WO-SOVEREIGN-SSCM-01B-REPORT.md).

**Status:** `EXECUTED` — `SOVEREIGN_SSCM_PARALLEL_FANOUT_FANIN_QUALIFIED`. **Base:** `97d33978e2b416334194b27bba1ae03f514b661a`. **Branch:** `wo/sovereign-sscm-01b`. One PR against `main`; ready on positive completion; never merged by the agent.

### Research question (§1-3)

Can Sovereign safely execute two coding actors concurrently, then deterministically fan their independently verified outputs back into one frozen candidate for independent review? Parallelism + fan-out + fan-in + causal join. Not dynamic planning, not repair.

### Invariants (§2)

All 01A/R1 invariants plus `PARALLEL ACTORS != SHARED WRITABLE GIT STATE`, `FAN-IN != MODEL-IMPROVISED MERGE`, `ONE PREDECESSOR OBSERVED != ALL JOIN PREDECESSORS SATISFIED`, `WORKER SUCCESS != INTEGRATION SUCCESS`. Authority `NONE` everywhere.

### Out of scope (§4)

Dynamic topology, >2 workers, recursive spawning, repair loops, model conflict resolution, automatic merge, QM/OpenClaw/GBrain/Memorable/River/Miskatonic integration, remote or cloud workers, new API keys, public ingress.

### Contract and blackboard (§6-14)

`a2a-collaboration-event.v0.2` with `predecessor_event_ids` (unique array, max 4); v0.1 preserved and normalized internally without rewriting payloads or digests. Predecessor rules: exist, same mission, not self, unique, transition-valid; `PREDECESSOR_CYCLE` rejected. Join: all lanes present and SUCCEEDED, else `JOIN_INCOMPLETE` / `JOIN_BLOCKED`. DAG depth = max(parent depths) + 1, tie-break by event id, storage order nonsemantic. v0.2 budget name `max_usage_units` = kilotokens. Storage: `event_predecessors` edge table. Preserve append-only, idempotency, restart, WAL, transactional claims, usage ledger.

### Topology, roster, workspaces (§15-22)

Frozen `task:fanout-a` (`docs/sscm-fanout-a.txt` = `SSCM_FANOUT_A_OK\n`) and `task:fanout-b` (`docs/sscm-fanout-b.txt` = `SSCM_FANOUT_B_OK\n`). Claude Code coordinator from a neutral cwd emits a fan-out plan the host validates against the frozen topology. Workers A and B = Codex, distinct runs and threads, native login. Independent per-worker bare mirrors and worktrees; realpaths, common dirs and writable paths pairwise distinct on resolved paths; never canonical; clean at admission.

### Parallel execution and budgets (§23-31)

`max_active_actors = 2`; a third exclusive claim fails. Stdlib concurrent launcher with a barrier; real overlap proven by host timestamps (`parallel_overlap_seconds > 0`). Atomic per-wave usage reservation over turns, usage units and wall (model calls when observable and required); reservation is host-owned; combined reservation must fit remaining headroom or the whole wave is refused; settlement charges observed usage, releases the remainder, and `RUN_RESERVATION_EXCEEDED` blocks the mission while keeping evidence.

### Verification, join, fan-in, review (§32-50)

Workers return the implementer-result contract with full 40-hex claims or null; host verifies each lane (SHA, ancestry, changed files, content, clean). No fan-in before both verifications. Host-owned join event with both host-verify predecessors. Third independent `integration.git`: import exact verified SHAs into bounded refs, verify, cherry-pick in frozen task-id order; any conflict → `FANIN_CONFLICT`, mission BLOCKED, no model. Integrated candidate host-verified; digest-bound integration receipt. Fourth `reviewer.git` detached at the integrated SHA; Claude Code reviewer (runtime ≠ workers) receives only SHAs, expected files/contents, receipt, checklist; host verifies reviewer HEAD == reviewed == integrated exactly. `max_repair_loops = 0`.

### Evidence, tests, CI, live, cleanup (§51-74)

Required DAG shape; join order independence; symmetric lane-failure rules; both-fail; one-worker budget overshoot; R1 credential-safe terminalization; experience referencing worker candidates, receipt, integrated SHA, review, event graph. Tests: concurrent blackboard safety, multi-parent adversarial, workspace adversarial, fan-in adversarial, reservation tests, serial regression, core-contract freeze, QM adapter regression; CI offline mock parallel dogfood. One bounded live mission (4 real model runs; extra runs counted and justified). Cleanup: delete all mirrors, worktrees, dogfood branches and temp refs; main unmutated.

### Dispositions (§75-79) and freeze (§80)

Positive `SOVEREIGN_SSCM_PARALLEL_FANOUT_FANIN_QUALIFIED`; runtime blocker `SOVEREIGN_SSCM_PARALLEL_BLOCKED`; reviewer finding `SSCM_PARALLEL_DOGFOOD_REPAIR_REQUIRED`; defect `REPAIR_REQUIRED`. Do not begin SSCM-01C (bounded repair loop + independent evaluator) until 01B is independently reviewed and merged.

```text
FAN-OUT CREATES INDEPENDENT CANDIDATES. HOST VERIFICATION EARNS JOIN ELIGIBILITY.
FAN-IN CREATES ONE CANDIDATE. INDEPENDENT REVIEW EARNS ACCEPTANCE EVIDENCE.
NONE OF THESE STEPS EARNS AUTHORITY.
```
