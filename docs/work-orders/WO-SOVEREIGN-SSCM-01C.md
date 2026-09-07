# WO-SOVEREIGN-SSCM-01C

## Single-Bounded Repair Loop, Independent Evaluation & Candidate-Custody Qualification

Sovereign-native work order, canonical in this repository. Execution report: [`WO-SOVEREIGN-SSCM-01C-REPORT.md`](WO-SOVEREIGN-SSCM-01C-REPORT.md).

**Base:** `c7a4b16b22ff3551802182577953681749a782bd` (merge of 01A, 01A-R1, 01B, 01B-R1). **Branch:** `wo/sovereign-sscm-01c`. One PR against `main`; ready on positive completion; never merged by the agent.

### Question (§1)

Can SSCM perform exactly one governed repair cycle after an independent evaluator rejects a host-verified candidate, without the evaluator, implementer, reviewer, or model granting itself additional attempts, budget, acceptance, promotion, or authority?

### Invariants (§2)

All prior SSCM invariants plus `EVALUATOR != IMPLEMENTER`, `EVALUATOR != REVIEWER`, `EVALUATION_FAIL != REPAIR_AUTHORITY`, `REPAIR_FINDING != REPAIR_PERMISSION`, `RETRY_ELIGIBLE != RETRY_AUTHORIZED`, `REPAIR_COUNT != MODEL_DECISION`, `EVALUATION_PASS != REVIEW_ACCEPT`, `EVALUATION_PASS != PROMOTION`, `REPAIRED_CANDIDATE != ACCEPTED_CANDIDATE`, `FIRST_CANDIDATE_FAILURE != EVIDENCE_TO_ERASE`. Authority `NONE` everywhere.

### Isolation and contracts (§3-8)

Single implementation lane; no parallel workers, dynamic DAGs, QM/OpenClaw/GBrain/Memorable/River/Miskatonic, remote or cloud. Uses the frozen `contracts/evaluation.v0.1` (no second evaluation ontology, no fifth core contract). Evaluation is not review: the evaluator applies a frozen acceptance oracle; the reviewer independently examines the final candidate and evidence chain. Provider-neutral evaluator interface in `evaluation/`; reference evaluator `evaluator:sscm-holdout-v0.1` is deterministic, invokes no model, mutates nothing, evaluates one exact host-verified revision, `independence_class = INDEPENDENT`.

### Evaluator custody and holdout (§9-15)

Evaluator gets a detached read-only worktree whose HEAD must equal the frozen candidate; owns no branch; consumes no transcript. Acceptance profile `sscm-repair-dogfood.v0.1`: target `docs/sscm-repair-dogfood.txt`; visible initial requirement `SSCM_REPAIR_SEED\n`; holdout requirement `SSCM_REPAIR_OK\n`, undisclosed through SSCM handoff state before the first FAIL; the evaluator finding may then reveal it. No evaluator source or unrelated state is forwarded.

### Roster and lanes (§16-18, §30-31, §68-71)

Claude Code coordinator (neutral cwd) produces the initial instruction and knows nothing of repair count, retry budget, evaluator disposition or final acceptance. Codex initial implementer in its own lane from canonical base; host verifies candidate 1 (full SHA, descent, exact changed file, exact content, clean) and freezes `VERIFIED_CANDIDATE_1_SHA`. Repair uses a fresh Codex run and session (`REPAIR_SESSION_REF != INITIAL_SESSION_REF`) in a fresh lane based on candidate 1 (`REPAIR_BASE_SHA = VERIFIED_CANDIDATE_1_SHA`). Separate lanes/object stores `implementer-1.git, evaluator-1.git, repair.git, evaluator-2.git, reviewer.git`; no canonical writes.

### Evaluation, repair gate, retry (§19-29, §33-38, §53-55)

Evaluation 1 binds `git:<VERIFIED_CANDIDATE_1_SHA>` exactly and is expected to FAIL; evidence refs cover worktree HEAD, profile identity and digest, observed digest, expected digest, result; persisted before any retry decision. FAIL makes repair *eligible*; only host policy (`max_repair_loops = 1`, disposition FAIL, candidate_ref equals current verified candidate, zero loops consumed, budget sufficient for the frozen repair envelope, evidence scan CLEAN, digest-verified refs, findings within allowed files) authorizes it via a host-owned `RETRY` following the evaluator `REJECT`; the blackboard consumes `max_repair_loops`, a second RETRY is refused. BLOCKED / INCONCLUSIVE never repair. A structured `sscm-repair-instruction.v0.1` (repair_attempt 1, base candidate, evaluation ref, allowed files, required changes, verification requirements) is the only handoff. Host verifies candidate 2 (descends from candidate 1, diff exactly the file, content exact, clean) and freezes `VERIFIED_CANDIDATE_2_SHA`; evaluation 2 uses the identical profile digest and binds candidate 2; expected PASS. The evaluator has no model session; its identity is a deterministic runtime invocation.

### Review and terminal (§39-52, §66-67)

Only after PASS: fresh Claude Code reviewer (session ≠ coordinator; runtime ≠ implementer) in a read-only lane at candidate 2, receiving SHAs, evaluation refs, repair authorization, expected file/content and a checklist covering lineage, both evaluations, exactly one RETRY, final content and no residue. `REPAIR_REQUIRED` → `SSCM_REPAIR_DOGFOOD_REVIEW_FAILED`, no second repair. Mission success requires the host-verified review task (`FINAL_REVIEW_REQUIRED`). Both evaluations are retained (FAIL then PASS); the experience references candidate 1, evaluation 1, the repair authorization and instruction, candidate 2, evaluation 2, the review and the event graph, outcome SUCCEEDED, authority NONE; no promotion, no training artifact. Credential scan precedes terminal success; executed usage precedes every later gate.

### Tests, CI, live, cleanup (§56-65, §74-92)

Second-retry attack; pass-first, BLOCKED and INCONCLUSIVE controls; stale, tampered, profile-drift and scope-drift attacks; implementer/reviewer self-evaluation prohibited; evaluator read-only and wrong-SHA; review required after PASS; restart at three checkpoints with exact custody reconstruction and idempotent RETRY; all prior suites and QM regression green; CI offline mock repair dogfood. One bounded live mission (Claude coordinator, Codex initial, Codex repair, Claude reviewer, 2 deterministic evaluator runs). The first live evaluation must fail for the frozen holdout reason or the disposition is `SSCM_REPAIR_TRIGGER_NOT_EXERCISED`; if candidate 2 fails, stop without widening loops, budget or scope. Cleanup of all dogfood branches, refs, worktrees, mirrors; main unmutated.

### Dispositions (§93-95) and freeze (§96-97)

Positive `SOVEREIGN_SSCM_BOUNDED_REPAIR_EVALUATION_QUALIFIED`; `SSCM_REPAIR_TRIGGER_NOT_EXERCISED`; `SOVEREIGN_SSCM_REPAIR_BLOCKED`; `SSCM_REPAIR_DOGFOOD_REVIEW_FAILED`; `REPAIR_REQUIRED`. No successor until 01C is reviewed and merged; the intended pivot is from orchestration mechanics to the experience → evaluation → promotion-proposal learning boundary.

```text
FAILURE MAY EARN A REPAIR OPPORTUNITY. FAILURE DOES NOT AUTHORIZE REPAIR.
THE EVALUATOR MAY REJECT. THE EVALUATOR MAY NOT REPAIR.
THE IMPLEMENTER MAY REPAIR. THE IMPLEMENTER MAY NOT EVALUATE ITSELF.
PASS MAY EARN REVIEW. PASS DOES NOT EARN PROMOTION.
NONE OF THESE STEPS EARNS AUTHORITY.
```
