# WO-SOVEREIGN-SSCM-01C — Execution Report

| Field | Value |
|---|---|
| Disposition | **`SOVEREIGN_SSCM_BOUNDED_REPAIR_EVALUATION_QUALIFIED`** |
| Live mission | `mission-sscm-01c-live-1` (2026-09-07 UTC), first and only live attempt |
| Canonical base | `c7a4b16b22ff3551802182577953681749a782bd` (sovereign `main`, unchanged) |
| Evidence | [`WO-SOVEREIGN-SSCM-01C-evidence/`](WO-SOVEREIGN-SSCM-01C-evidence/) — both `evaluation.v0.1` objects and receipts, repair instruction, host verifications, event graph with edges, repair projection, usage + reservations; scan CLEAN |

## What was proven

A host-verified Codex candidate was rejected by an independent deterministic evaluator for the frozen holdout reason; the failure did not authorize anything. Host policy admitted exactly one repair, recorded as a host-owned `RETRY` that consumed `max_repair_loops = 1`. A fresh Codex run and session repaired in a fresh lane based on candidate 1. The identical evaluator profile passed candidate 2. Only then did a fresh Claude Code reviewer examine the whole chain and accept the exact final SHA. Both evaluations are retained; no promotion or training object exists.

## Live event graph (14 events, v0.2)

```text
PUBLISH(coordinator) -> CLAIM(implementer-1) -> COMPLETE(implementer) -> OBSERVE(host) -> REJECT(evaluator) -> RETRY(host) -> CLAIM(implementer-2) -> COMPLETE(repair_implementer) -> OBSERVE(host) -> OBSERVE(evaluator) -> CLAIM(reviewer) -> COMPLETE(reviewer) -> OBSERVE(host) -> COMPLETE(host)
```

Exactly one RETRY (`evt-0c442ed4-aa42-406a-988a-230350c4be1d`), authored by `host:sscm-mission-controller`, predecessor = the evaluator `REJECT`, input = evaluation 1, output = the repair instruction.

## Roster (host-observed)

| Role | Executor | Session | Wall (s) | Turns / kilotokens |
|---|---|---|---|---|
| COORDINATOR | `executor:claude-code` | `1b4530e2-c4d8-40c8-af53-cf81409e3937` | 17.129 | 2 / 7.595 |
| IMPLEMENTER (initial) | `executor:codex` | `codex-thread:01a07d08-6b45-7232-aa0d-fdf332993f97` | 520.287 | 1 / 94.24 |
| REPAIR_IMPLEMENTER | `executor:codex` | `codex-thread:01a07d10-6306-74f2-8a11-3a9d2ecb4760` | 44.288 | 1 / 89.827 |
| REVIEWER | `executor:claude-code` | `0ce36e82-a779-49e8-af4f-860389d9516b` | 68.562 | 13 / 160.45 |
| evaluator (x2) | `evaluator:sscm-holdout-v0.1` | runtime `sovereign.deterministic-evaluator` (no model session) | — | 0 model calls |

Initial and repair sessions are distinct Codex threads; coordinator and reviewer are distinct Claude sessions. Five independent object stores: `implementer-1.git`, `evaluator-1.git`, `repair.git`, `evaluator-2.git`, `reviewer.git`; none canonical, none dirty at admission.

## Candidate lineage and evaluations

| | Value |
|---|---|
| canonical base | `c7a4b16b22ff3551802182577953681749a782bd` |
| VERIFIED_CANDIDATE_1_SHA | `6edb3a2ac4dd1ce7a2595fe47c5ba8d8c7a47f21` (claimed identical; content `SSCM_REPAIR_SEED\n`; clean) |
| evaluation 1 | `eval:ca370a16-a94f-4c23-a5a8-2d4194a50d88` binds `git:6edb3a2ac4dd1ce7a2595fe47c5ba8d8c7a47f21`, **FAIL**: exact-content mismatch for docs/sscm-repair-dogfood.txt: observed sha256 875b389ff915 != expected sha256 f195483e251a |
| evaluator profile | `sscm-repair-dogfood.v0.1` digest `1d52206e5c69a809…` (identical for both evaluations: True) |
| repair authorization | host `RETRY` after host gate: disposition FAIL, candidate bound, 0 loops consumed, budget headroom, digest-verified refs, findings within `['docs/sscm-repair-dogfood.txt']`, evidence scan CLEAN |
| repair instruction | `sscm-repair-instruction.v0.1`, attempt 1, base `6edb3a2ac4dd…`, required change to `SSCM_REPAIR_OK\n`, evaluation ref digest-bound |
| REPAIR_BASE_SHA | `6edb3a2ac4dd1ce7a2595fe47c5ba8d8c7a47f21` (= candidate 1) |
| VERIFIED_CANDIDATE_2_SHA | `2a93e1d66b7a3c3edb4dc069e0fe2ff5c3d97f06` (claimed identical; 1 commit from candidate 1; only `docs/sscm-repair-dogfood.txt`; content `SSCM_REPAIR_OK\n`; clean) |
| evaluation 2 | `eval:e76a438d-a6ba-48ea-bd97-37beaef4eb94` binds `git:2a93e1d66b7a3c3edb4dc069e0fe2ff5c3d97f06`, **PASS**, findings [] |
| review | reviewer HEAD `2a93e1d66b7a3c3edb4dc069e0fe2ff5c3d97f06` == reviewed == candidate 2; verdict **ACCEPT**, blocking findings 0 |

Repair custody projection at closure: current candidate `2a93e1d66b7a…`, authorized 1, consumed 1, last evaluation PASS, history of 2.

## Budget

Frozen envelopes: coordinator 6/60, initial 10/200, repair 10/200, reviewer 16/300 (turns/kilotokens); mission 48 turns / 800 kilotokens / 1800 s, `max_repair_loops = 1`, `max_consecutive_failures = 2` (one FAIL may precede the single RETRY). Observed: 4 runs, 17 turns, 352.112 kilotokens, 650.266 s summed wall (the initial Codex run took 520 s, within its 900 s envelope); `budget_exceeded = []`; all reservations SETTLED.

## Experience

`sscm:exp:bcab9e337d47fad5c1d98288975cad0a` — outcome `SUCCEEDED`, authority `NONE`, procedure/training refs null. Artifact refs include candidate 1, evaluation 1 (FAIL) and receipt, repair instruction, candidate 2, evaluation 2 (PASS) and receipt, review; evidence refs cover every event and the projection. The trajectory is FAIL → one governed repair → PASS, not a rewritten single success. `EVALUATION_COUNT = 2`; `PROMOTION_CREATED = NO`; `TRAINING_ARTIFACT_CREATED = NO`.

## Live run ledger

One live mission. Real model runs: Claude coordinator 1, Codex initial 1, Codex repair 1, Claude reviewer 1; deterministic evaluator 2 local runs; other 0. No retries, no widening of loops, budget or scope.

## Tests and CI

`python3 -m pytest -q` → 215 passed: 01A/R1 serial 83, v0.2/DAG/reservations/joins 31, parallel 19, 01B-R1 session/evidence 16, **repair + evaluation 22**, core contracts 5, QM adapter 39. Controls and attacks: pass-first (no RETRY, review still required), second failure (no third run), BLOCKED and INCONCLUSIVE (no RETRY), stale evaluation, tampered evaluation ref, profile drift, repair scope drift, implementer self-evaluation, evaluator dirty/wrong-SHA, review required after PASS, reviewer REPAIR_REQUIRED without second repair, repair session freshness, restart at three checkpoints with exact custody, idempotent RETRY. CI adds an offline mock repair dogfood.

## Cleanup

All five lanes and worktrees destroyed; no `dogfood/*` branch; single canonical worktree; `main == c7a4b16b`. Not merged.

## Not claimed

Multiple repair loops, parallel repair, dynamic budgets, learned evaluators, LLM-as-judge validity, evaluator ensembles, promotion, training, memory integration, QM/OpenClaw adapters, Miskatonic integration, autonomous merge.
