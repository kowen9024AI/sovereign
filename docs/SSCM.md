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

Every mission should support explicit limits such as:

```text
max_active_actors
max_turns
max_model_calls
max_wall_seconds
max_repair_loops
max_consecutive_failures
max_token_or_cost_budget
```

Budget exhaustion produces a bounded terminal/blocking state. Agents cannot grant themselves additional budget or authority.

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
