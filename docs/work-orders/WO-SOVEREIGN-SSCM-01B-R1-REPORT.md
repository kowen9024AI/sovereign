# WO-SOVEREIGN-SSCM-01B-R1 — Repair Report

| Field | Value |
|---|---|
| Work order | `WO-SOVEREIGN-SSCM-01B-R1` — session identity custody, verified-join admission, executed-usage evidence ordering |
| Predecessor head | `9970056290bd92574668b0f3f44100e3302d2339` |
| Disposition | **`SOVEREIGN_SSCM_PARALLEL_R1_READY`** |
| Live rerun | `NOT_REQUIRED_SEMANTIC_REPAIR_ONLY` — Claude/Codex invocation, launcher, workspaces, reservation semantics, fan-in algorithm, reviewer prompt, credential terminalization and the positive DAG are unchanged. The retained live-2 event graph replays through the repaired blackboard unchanged (13 events, projection SUCCEEDED; join parents are host `OBSERVE` events binding `7531f803…` and `6a8c1a21…`, referenced exactly by the join). live-1 and live-2 remain as recorded in [`WO-SOVEREIGN-SSCM-01B-REPORT.md`](WO-SOVEREIGN-SSCM-01B-REPORT.md). |

## Part A — session identity custody

- Roster admission requires `session_identity_support` on all four roles; otherwise `SESSION_IDENTITY_UNOBSERVABLE` before any launch. Nothing is inferred from PID, order, family, cwd or prompt.
- After each executed run's usage is settled, a non-empty provider/runtime `session_ref` is required (`SESSION_IDENTITY_MISSING`); no executor-id or synthetic fallback.
- Worker comparison is exact string inequality after both are present: `null/null`, `X/null`, `null/Y`, `""/Y` → `SESSION_IDENTITY_MISSING`; `X/X` → `SESSION_COLLAPSE`; `X/Y` → pass.
- `COORDINATOR_SESSION_REF != REVIEWER_SESSION_REF` enforced (`REVIEW_SESSION_NOT_INDEPENDENT`).
- Run ids recorded per role and checked pairwise (`RUN_ID_COLLISION`); session and run identity are separate axes and both are reported.

## Part B — executed usage survives identity failure

Post-wave order is now: worker processes return → receipts persisted and usage settled for lane A then lane B (a failed settlement is recorded and the other lane is still settled) → run-id and session gates → structured-result validation → host verification. Tests prove that with same-session or missing-session workers the mission blocks **after** both usage rows exist and both reservations are `SETTLED`, both `COMPLETE` events are recorded, and neither fan-in nor the reviewer runs. An invalid structured result after execution likewise retains usage. `WORKER_PROCESS_STARTED ⇒ USAGE_ROW`.

## Part C — verified-join admission

Blackboard fan-in policy (Option B + revision binding, deterministic, model-free): every parent of a multi-predecessor event must be `verb = OBSERVE`, `actor_ref` under `host:`, `status = SUCCEEDED`, and bind a full 40-hex `candidate_revision`; the join must reference exactly those revisions as `REVISION git:<sha>` input refs. Codes: `JOIN_INCOMPLETE` (missing lane), `JOIN_PREDECESSOR_UNVERIFIED` (raw COMPLETE, non-host observer, missing/short revision), `JOIN_BLOCKED` (verified lane not SUCCEEDED), `JOIN_REVISION_MISMATCH` (join references other revisions). Two SUCCEEDED worker `COMPLETE` parents are rejected; one verified + one raw is rejected; two host-verified parents with full SHAs are admitted.

## Validation

| Suite | Result |
|---|---|
| SSCM v0.1 (contracts, blackboard, mission, 01A-R1) | 83 passed |
| SSCM v0.2 / DAG / reservations (incl. 7 new join-admission tests) | 31 passed |
| parallel controller / isolation / fan-in | 19 passed |
| 01B-R1 session + evidence-order tests | 16 passed |
| core contracts (frozen four) | 5 passed |
| QM adapter | 39 passed |
| full `python3 -m pytest -q` | 193 passed |
| CI | see PR #6 checks (offline mock parallel dogfood unchanged) |

No test was weakened: v0.2 fixtures were updated to construct joins from host-verified observations, which is the rule the WO requires. No credential values in evidence. Authority `NONE` everywhere.
