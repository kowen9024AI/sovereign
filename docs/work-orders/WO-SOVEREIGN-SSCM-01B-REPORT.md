# WO-SOVEREIGN-SSCM-01B — Execution Report

| Field | Value |
|---|---|
| Disposition | **`SOVEREIGN_SSCM_PARALLEL_FANOUT_FANIN_QUALIFIED`** |
| Live mission | `mission-sscm-01b-live-2` (2026-09-07 UTC) |
| Canonical base | `97d33978e2b416334194b27bba1ae03f514b661a` (sovereign `main`, unchanged) |
| Evidence | [`WO-SOVEREIGN-SSCM-01B-evidence/`](WO-SOVEREIGN-SSCM-01B-evidence/) — sanitized artifacts, v0.2 event export with edge table, projection, usage + reservations; scan CLEAN |

## What was proven

Two real Codex coding agents executed **concurrently** in independent Git object stores, each candidate was host-verified by exact 40-hex SHA, a true two-predecessor causal join gated the fan-in, deterministic host Git integration produced one candidate in frozen task-id order, and an independent Claude Code reviewer accepted the exact integrated SHA. Budgets survived concurrency through atomic wave reservation and settlement. Terminal evidence was credential-scanned before success. No promotion or authority occurred.

## Live DAG (13 events, v0.2)

```text
PUBLISH(fanout-plan|0p) -> CLAIM(fanout-b) -> CLAIM(fanout-a) -> COMPLETE(fanout-b) -> COMPLETE(fanout-a) -> OBSERVE(fanout-b) -> OBSERVE(fanout-a) -> OBSERVE(fanin|2p) -> OBSERVE(fanin) -> CLAIM(review) -> COMPLETE(review) -> OBSERVE(review) -> COMPLETE(mission-sscm-01b-live-2)
```

Join event `evt-ce73dbf6-df47-4e71-ae71-cb7fa30958cb` has exactly 2 predecessors: the host OBSERVE events of lane A and lane B (see `blackboard-edges.json`).

## Roster and parallelism (host-observed)

| Role | Executor | Session | Start | Finish | Wall (s) | Turns / kilotokens |
|---|---|---|---|---|---|---|
| COORDINATOR | `executor:claude-code` | `20e294f7-9778-4cf3-9ad9-50087b427b82` | | | 19.241 | 2 / 20.949 |
| WORKER_A | `executor:codex` | `codex-thread:01a07a41-c199-71f3-9225-e49f4589aa6b` | 1788757524.017 | 1788757560.585 | 36.568 | 1 / 105.464 |
| WORKER_B | `executor:codex` | `codex-thread:01a07a41-c143-7f43-b658-49cb17a71085` | 1788757524.016 | 1788757562.343 | 38.327 | 1 / 105.103 |
| REVIEWER | `executor:claude-code` | `fb335f31-9cf1-4639-b3d1-62acc47ddaed` | | | 67.294 | 9 / 110.996 |

**`parallel_overlap_seconds = 36.568`** (both workers started within 1 ms of each other after the launch barrier; overlap = min(finish) − max(start)). Distinct run ids, distinct Codex threads, same runtime (`executor:codex`).

## Workspace isolation (resolved paths)

| | WORKER_A | WORKER_B | integration | reviewer |
|---|---|---|---|---|
| worktree | `…/workspaces/worker-a-wt` | `…/workspaces/worker-b-wt` | `…/workspaces/integration-wt` | `…/workspaces/reviewer-wt` |
| git common dir | `worker-a.git` | `worker-b.git` | `integration.git` | `reviewer.git` |
| dirty at admission | False | False | — | False |
| canonical checkout | False | False | no | False |

Four independent bare mirrors of the canonical base; no two lanes share a writable object store; the canonical `.git` is never a writable root.

## Host verification and fan-in

| | Value |
|---|---|
| VERIFIED_WORKER_A_SHA | `7531f8031097dcec47100da161809eede33d763c` (claimed identical; file `docs/sscm-fanout-a.txt`; clean) |
| VERIFIED_WORKER_B_SHA | `6a8c1a21f45c8e1a827fe64adc88ee051597c4aa` (claimed identical; file `docs/sscm-fanout-b.txt`; clean) |
| join | `JOIN_READY` after both host OBSERVE events; no model prose consulted |
| import | exact SHAs fetched from `worker-a.git` / `worker-b.git` into `refs/sscm/import/*`, fetched objects verified equal |
| integration | `git cherry-pick` onto base in frozen order `['task:fanout-a', 'task:fanout-b']` (worker B finished after A here, but order is by task id, never timing) |
| VERIFIED_INTEGRATED_SHA | `916826117fcd3cdf729966ebf7db5c063ed65583` — descends from base, changed files `['docs/sscm-fanout-a.txt', 'docs/sscm-fanout-b.txt']`, both contents byte-exact, worktree clean |
| FANIN_CONFLICT | none |
| integration receipt | `integration-receipt.json`, digest-bound, validated against `sscm-integration-receipt.v0.1` |

## Review

Reviewer worktree `reviewer.git` detached at `916826117fcd3cdf729966ebf7db5c063ed65583`; reviewer reported `916826117fcd3cdf729966ebf7db5c063ed65583`; host equality exact; verdict **`ACCEPT`**, blocking findings 0. Reviewer received only base/worker/integrated SHAs, expected files and contents, the integration receipt, and the checklist. No transcripts crossed any lane.

## Budget under concurrency

Frozen envelopes: coordinator 6 turns / 60 units, each worker 10 / 200, reviewer 16 / 300; mission 48 turns / 800 units / 1800 s. Waves: coordinator, workers (atomic two-run reservation), reviewer. Observed: 4 runs, 13 turns, 342.512 kilotokens, 161.43 s summed wall; `budget_exceeded = []`; all reservations SETTLED. Observability: turns/tokens/wall OBSERVED on every executor; Codex model calls and cost UNKNOWN (never zero).

## Experience

`sscm:exp:4916a56a018cac920cbb057da6209387` — outcome `SUCCEEDED`, authority `NONE`, procedure/training refs null, 15 artifact refs (worker candidates, integration receipt, integrated revision, review artifact) and one evidence ref per event plus the projection digest. No evaluation, promotion, or training artifact.

## Live run ledger (honest count)

| Mission | Outcome | Real model runs |
|---|---|---|
| live-1 | every parallel property held (overlap 33.131 s, join ready, integrated `36bcc2aa185e`, reviewer verdict ACCEPT in its receipt) but **blocked** at reviewer settlement: `RUN_RESERVATION_EXCEEDED` (133.5 kilotokens observed > 120 reserved). Cause: the host-frozen reviewer envelope was sized from the 01A one-file review. Retained as evidence (`live-1-blocked-summary.json`). | 1 Claude, 2 Codex, 1 Claude |
| **live-2** | **QUALIFIED** with reviewer envelope 300 units / 16 turns (mission ceiling 800 / 48) | 1 Claude, 2 Codex, 1 Claude |

Totals: `REAL_CLAUDE_COORDINATOR_RUNS = 2`, `REAL_CODEX_WORKER_A_RUNS = 2`, `REAL_CODEX_WORKER_B_RUNS = 2`, `REAL_CLAUDE_REVIEWER_RUNS = 2`, `OTHER_MODEL_RUNS = 0`. The extra wave is justified by one distinct discovered cause (envelope sizing), not by output wording; the reservation gate blocking live-1 is itself positive evidence that budgets are enforced under concurrency.

## Tests and CI

`python3 -m pytest -q` → 170 passed: SSCM v0.1 regression 80 (unchanged), v0.2 contract + DAG + concurrency + reservations 24, parallel controller + workspace isolation + fan-in 19, core contracts 5, QM adapter 39 (+3 collaboration-schema parametrizations). CI adds an offline mock parallel dogfood (concurrent launcher, host fan-in, review, cleanup) with no provider credentials.

## Cleanup

All four mirrors and worktrees destroyed for live-1 and live-2; no `dogfood/*` branch on the canonical checkout; single canonical worktree; `main == 97d33978`. Not merged. Temp refs lived only inside destroyed mirrors.

## Not claimed

N-way swarms, dynamic DAGs, conflict-resolution agents, automatic repair, remote workers, QM/OpenClaw/Miskatonic integration, memory promotion, training, autonomous merge.
