# WO-SOVEREIGN-SSCM-01A-R1 — Repair Report

| Field | Value |
|---|---|
| Work order | [`WO-SOVEREIGN-SSCM-01A-R1`](WO-SOVEREIGN-SSCM-01A-R1.md) |
| Predecessor head | `283bd059702c7573fd98512448f7cdfa9683ae43` (code) + `32ed0a0e` (R1 WO text, docs only) |
| Disposition | **`SOVEREIGN_SSCM_LOCAL_A2A_R1_READY`** |
| Live rerun | `NOT_REQUIRED_SEMANTIC_REPAIR_ONLY` — executor invocation, workspace behaviour, live event sequence and experience mapping are unchanged; the historical live run in [`WO-SOVEREIGN-SSCM-01A-REPORT.md`](WO-SOVEREIGN-SSCM-01A-REPORT.md) stands |

## Part A — exact revision authority

- `claimed_candidate_revision` and `reviewed_revision` now require `^[0-9a-f]{40}$` in both collaboration result contracts. Abbreviated prefixes fail schema validation.
- Host comparison is exact equality (`claimed == VERIFIED_CANDIDATE_REVISION`, `reviewed == VERIFIED_CANDIDATE_REVISION`). No `startswith`, normalization, or Git abbreviation resolution remains in `sscm/mission.py` (asserted by test).
- A null implementer claim lets host verification establish the candidate; a non-null contradictory claim fails closed with `CANDIDATE_CLAIM_MISMATCH`; a reviewer mismatch fails closed with `REVIEW_SHA_MISMATCH`.
- Adversarial tests for both roles: 7-char prefix → rejected; wrong 40-hex → rejected; exact 40-hex → passes.

## Part B — consumptive budget enforcement

Usage is runtime-observed from each `ExecutorRun`, never model prose, and appended to a persisted, idempotent usage ledger on the blackboard (`usage` table, replay-safe by `run_id`, conflicting re-record refused).

| Dimension | Semantics | Claude Code | Codex |
|---|---|---|---|
| `max_turns` | executor-reported turns | `num_turns` | count of `turn.completed` |
| `max_model_calls` | executor-reported model invocations | `num_turns` (one response per turn) | `UNOBSERVABLE` (not in `exec --json`) |
| `max_token_or_cost_units` | **Option B**: kilotokens = (all input incl. cached + output) / 1000 | `usage.*_tokens` | `turn.completed.usage` |
| `max_wall_seconds` | cumulative host wall clock | host | host |
| cost (USD) | evidence only, not enforced | `total_cost_usd` | `UNOBSERVABLE` |
| `max_coordination_transitions` | CLAIM + COMPLETE events (was misnamed `max_turns`) | blackboard | blackboard |

Enforcement: pre-launch headroom gate (wall clock and every observed dimension) before each executor; post-run charge after each executor; cumulative over-limit → `BUDGET_EXCEEDED` → mission `BLOCKED`, never terminal success. Every executor declares `usage_observability`; a mission's `required_observable_dimensions` (01A: `executor_turns, token_units, wall_seconds`) are checked at admission and an unobservable required dimension fails closed before any launch (`BUDGET_DIMENSION_UNOBSERVABLE`). An executor that declares a dimension observable but reports none fails closed (`BUDGET_DIMENSION_UNREPORTED`). Optional unobservable dimensions are recorded `UNKNOWN`, never zero. The report exposes `observed_executor_runs, observed_executor_turns, observed_model_calls, observed_cost_or_token_units, observed_wall_seconds, budget_limits, budget_dimensions_observable, budget_exceeded`.

Offline replay of the repaired parsers over the retained live-4 provider logs: coordinator 2 turns / 20.112 kilotokens / USD 0.4416; reviewer 6 turns / 66.601 kilotokens / USD 0.3005; Codex 1 turn / 85.31 kilotokens. All within the 01A budget (24 turns, 400 kilotokens).

## Part C — credential-safe terminalization

Positive path is now: review host verification → all acceptance evidence flushed → credential scan over every durable artifact **and every provider stdout/stderr log** → CLEAN → mission `COMPLETE/SUCCEEDED` → projection → experience. Not CLEAN → `REJECT/BLOCKED`, no SUCCEEDED event, no SUCCEEDED experience. The final gate before experience creation runs again over everything durable; a BLOCKED mission yields an observed experience only from evidence that scanned CLEAN. Test: a secret written only into a reviewer stdout log with an otherwise valid result → scan `REJECTED`, mission `BLOCKED`, experience not created, disposition `BLOCKED`, blocked outcome recorded (success never appended, so no `MISSION_TERMINAL` conflict).

## Validation

| Suite | Result |
|---|---|
| SSCM focused (contracts, blackboard, mission, r1) | 80 passed |
| core contracts (frozen four) | 5 passed |
| QM adapter | 39 passed |
| full `python3 -m pytest -q` | 124 passed |
| CI | see PR #5 checks (offline mock-roster dogfood included) |

Frozen four-contract core unchanged. `WO-SOVEREIGN-QM-ADAPTER-00A` preserved at `STRUCTURALLY_VALIDATED`. No credential values in evidence. Authority `NONE` everywhere.
