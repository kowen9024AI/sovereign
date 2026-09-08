# Sovereign Swarm Control Mesh (SSCM)

## Status

`v0.1 IMPLEMENTED + LOCALLY QUALIFIED` — see [`work-orders/WO-SOVEREIGN-SSCM-01A-REPORT.md`](work-orders/WO-SOVEREIGN-SSCM-01A-REPORT.md) (`SOVEREIGN_SSCM_LOCAL_A2A_QUALIFIED`). The `sscm/` package provides the SQLite blackboard, executor contract with Claude Code and Codex adapters, isolated Git workspaces, the frozen 01A mission controller, and experience conversion. No provider credential, training authority, knowledge authority, or automatic promotion is created.

```bash
python3 -m sscm.local_cli capabilities                      # read-only executor discovery
python3 -m sscm.local_cli dogfood --executors mock --base $(git rev-parse HEAD)   # offline wiring check
python3 -m sscm.local_cli dogfood --executors live --base <canonical-sha>         # real Claude/Codex roster
```

## Thesis

Most multi-agent systems make models talk to one another. SSCM makes agents collaborate through governed state.

The core pattern is stigmergic: an actor publishes a bounded structured artifact or state transition; that transition makes another actor eligible to act. The receiving actor does not need the sender's hidden chain of thought or full transcript.

```text
AGENT A
  -> structured event + artifact refs
        |
        v
   CONTROL MESH
        |
        v
AGENT B
```

## Product boundary

SSCM is not a model router and not a universal credential broker.

```text
COLLABORATION PROTOCOL != HARNESS
HARNESS != EXECUTOR
EXECUTOR != MODEL API
SUCCESSFUL EXECUTION != PROMOTION
```

Claude Code, Codex, Antigravity, local models, QM, OpenClaw, and future systems may participate through adapters without surrendering their native authentication or execution model.

## Reference architecture

```text
                     OPERATOR / GOVERNANCE
                              |
                              v
                       SSCM COORDINATOR
                              |
                    structured blackboard
                              |
          +-------------------+-------------------+
          |                                       |
          v                                       v
  coordination state                       artifact plane
  task graph                                Git commits
  leases                                    worktrees
  assignments                               files/logs
  budgets                                   evaluations
  handoffs                                  receipts
          |
          v
 +---------------------------------------------------------+
 |                   EXECUTOR ADAPTERS                     |
 | Claude Code | Codex | local models | QM | OpenClaw ... |
 +---------------------------------------------------------+
```

## Blackboard, not transcript bus

The blackboard stores coordination facts and references. It should not become a warehouse for whole transcripts or source trees.

Typical handoff:

```text
mission_id
task_id
from actor
target role
base/candidate SHA
workspace lease ref
input artifact refs
expected output contract
budget
status
authority = NONE
```

Large outputs stay in Git or content-addressed artifact storage.

## Writable workspace rule

Shared coordination state is allowed. Shared autonomous writable repository state is not.

```text
SHARED BLACKBOARD = YES
SHARED WRITABLE WORKTREE = NO
```

Each mutating actor receives an isolated workspace. Review consumes the exact frozen candidate identity rather than a moving branch.

## Portable event verbs

SSCM v0.1 uses a small coordination vocabulary:

```text
CLAIM
PUBLISH
OBSERVE
COMPLETE
REJECT
RETRY
CANCEL
```

These are workflow transitions, never authority grants.

## Executor adapters

An executor adapter is responsible for launching or addressing one execution substrate and returning structured evidence.

Examples:

```text
executor:claude-code
executor:codex
executor:antigravity
executor:local-openweights
executor:qm
executor:openclaw
```

The adapter owns no promotion authority. A local CLI can keep its existing subscription login; a local inference server can keep its own model endpoint; a harness can keep its own scope/session model.

## QM relationship

QM is valuable for multi-user scopes, durable sandboxes, scoped permissions, memory, scheduling, and organizational collaboration. SSCM does not require QM to broker every executor's credentials.

A future QM adapter may expose QM scope/session/run facts to SSCM while leaving executor authentication provider-native.

## OpenClaw relationship

OpenClaw is valuable for rich human/agent workspaces, session UX, plugins, channels, and execution integration. SSCM does not require OpenClaw's internal swarm primitive to be the universal A2A protocol.

A future OpenClaw adapter may expose sessions/executions or use OpenClaw as a presentation/execution substrate.

## Sovereign learning relationship

SSCM produces bounded execution experience. Sovereign may then evaluate and propose promotion of that experience through its existing contracts:

```text
SSCM execution
   -> experience.v0.1
   -> independent evaluation.v0.1
   -> promotion.v0.1
   -> optional training-artifact.v0.1
```

The mesh never promotes its own output automatically.

## Budget discipline

Every mission carries a frozen budget. v0.1 dimensions and their exact semantics:

| Dimension | Meaning | Charged from |
|---|---|---|
| `max_active_actors` | concurrent task claimants | coordination projection |
| `max_coordination_transitions` | CLAIM + COMPLETE coordination events | coordination projection |
| `max_turns` | executor-reported turns | `ExecutorRun.executor_turns` (Claude `num_turns`; Codex `turn.completed` count) |
| `max_model_calls` | executor-reported model invocations | `ExecutorRun.model_calls` (Claude `num_turns`; Codex `UNOBSERVABLE`) |
| `max_wall_seconds` | cumulative real mission wall time | host clock, checked before every launch |
| `max_repair_loops` | RETRY events | coordination projection |
| `max_consecutive_failures` | consecutive failures before a new CLAIM/RETRY is refused | coordination projection |
| `max_token_or_cost_units` (v0.1) / `max_usage_units` (v0.2) | normalized usage units = kilotokens (all input incl. cached + output) / 1000 | `ExecutorRun.token_units`; monetary cost is evidence only |

`DECLARED_BUDGET != CONSUMED_BUDGET`: coordination events are not executor usage. Observed consumption is
appended to a usage ledger on the blackboard after every executor run and enforced cumulatively; the pre-launch
headroom gate refuses a new executor when any observed dimension is exhausted. Each executor declares which
dimensions it can report from runtime output; a mission lists `required_observable_dimensions` and refuses a
roster that cannot report one of them (`BUDGET_DIMENSION_UNOBSERVABLE`). Unknown usage is recorded `UNKNOWN`,
never treated as zero, and an executor that claims a dimension but does not report it fails closed
(`BUDGET_DIMENSION_UNREPORTED`). Budget exhaustion produces a bounded terminal/blocking state. Agents cannot
grant themselves additional budget or authority.

## Bounded parallel fan-out / fan-in (v0.2, SSCM-01B)

`a2a-collaboration-event.v0.2` carries `predecessor_event_ids` (unique, max 4) so a fan-in join can name every
lane it depends on; a join is admissible only when all named lanes exist and are SUCCEEDED (`JOIN_INCOMPLETE`,
`JOIN_BLOCKED` otherwise). Causal depth is max(parent depths) + 1. A join parent must be a host-owned `OBSERVE` (actor `host:*`) that is
SUCCEEDED and binds a full 40-hex candidate revision, and the join must reference exactly those revisions
(`JOIN_PREDECESSOR_UNVERIFIED`, `JOIN_REVISION_MISMATCH`): a worker's own SUCCEEDED COMPLETE never earns join
eligibility. Every qualified role must surface a provider/runtime session identity; worker sessions must be
non-null and distinct, reviewer and coordinator sessions must differ, and executed usage is settled before any
identity gate can block. Each concurrent worker gets its own bare
mirror and worktree (no shared writable object store); waves are admitted only when their combined host-frozen
reservations fit the remaining budget, and settled against observed usage. Fan-in is host-owned Git
(exact-SHA import into bounded refs, cherry-pick in frozen task-id order, `FANIN_CONFLICT` aborts without any
model). Live proof: [`work-orders/WO-SOVEREIGN-SSCM-01B-REPORT.md`](work-orders/WO-SOVEREIGN-SSCM-01B-REPORT.md).

```bash
python3 -m sscm.local_cli dogfood-parallel --executors mock --base $(git rev-parse HEAD)   # offline
python3 -m sscm.local_cli dogfood-parallel --executors live --base <canonical-sha>         # Claude + 2x Codex + Claude
```

## Bounded repair with independent evaluation (SSCM-01C)

`evaluation/` produces frozen `evaluation.v0.1` objects about exact host-verified revisions. The reference
evaluator `evaluator:sscm-holdout-v0.1` is deterministic, model-free and read-only. An evaluation FAIL makes a
candidate repair-*eligible*; only the host repair gate (disposition FAIL, candidate bound, zero loops consumed,
budget headroom for the frozen repair envelope, digest-verified refs, findings within the allowed files, evidence
scan CLEAN) authorizes it through a host-owned `RETRY` that consumes `max_repair_loops`. BLOCKED and INCONCLUSIVE
never repair. The repair runs as a fresh executor session in a fresh lane based on the failed candidate, the
identical profile re-evaluates, and only a PASS admits the independent reviewer; mission success additionally
requires the host-verified review task (`FINAL_REVIEW_REQUIRED`). The failed first evaluation is retained in the
experience: the accepted trajectory is FAIL → one governed repair → PASS. PASS never earns promotion. Live proof:
[`work-orders/WO-SOVEREIGN-SSCM-01C-REPORT.md`](work-orders/WO-SOVEREIGN-SSCM-01C-REPORT.md).

```bash
python3 -m sscm.local_cli dogfood-repair --executors mock --base $(git rev-parse HEAD)   # offline
python3 -m sscm.local_cli dogfood-repair --executors live --base <canonical-sha>         # Claude, Codex x2 fresh, Claude
```

## Revision authority

Structured results may carry a revision only as a full 40-hex Git object id, compared for exact equality with
the host-observed revision. `REVISION_PREFIX_MATCH != EXACT_REVISION_MATCH`. A non-null contradictory claim fails
closed; a null claim lets host verification establish the candidate.

## Credential-safe terminalization

All durable artifacts and provider stdout/stderr logs are credential-scanned before a mission-level SUCCEEDED
event is appended and before any experience is created. `CREDENTIAL_SCAN_PENDING != MISSION_ACCEPTABLE`. A
BLOCKED mission may yield an observed experience describing failure only from evidence that itself scanned CLEAN.

## Interoperability goal

A minimal SSCM implementation should be able to coordinate heterogeneous local executors using only:

1. structured event exchange;
2. isolated filesystem/Git workspaces;
3. host-observed artifact identities;
4. executor-native authentication;
5. deterministic budgets and state transitions.

No shared vendor cloud or common model API is required.

## Relationship to Miskatonic Systems

Sovereign remains standalone. Miskatonic Systems may consume compatible SSCM contracts through explicit adapters and exact-release bindings.

The public protocol carries no Miskatonic authority by association.

## Governing invariant

> The collaboration protocol is portable; authority remains external and explicit.

And:

> Recursive learning must never become recursive self-authorization.
