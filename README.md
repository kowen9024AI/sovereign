# Sovereign

**A governed learning and promotion loop for intelligence you own.**

Sovereign is a standalone, vendor-neutral project for connecting agent execution, working knowledge, procedural memory, independent evaluation, and model specialization without allowing any of those layers to authorize themselves.

The project starts from one invariant:

> Recursive learning must never become recursive self-authorization.

## Why

Modern agent stacks can accumulate memory, replay successful procedures, run multiple coding harnesses, and fine-tune models. The dangerous shortcut is to treat a successful trajectory as automatically worthy of becoming durable memory, canonical knowledge, a reusable procedure, or training data.

Sovereign inserts an explicit promotion boundary:

```text
experience
   |
   v
candidate memory / procedure / training trajectory
   |
   v
independent evaluation
   |
   v
promotion proposal
   |
   v
human or external governance gate
   |
   +--> durable knowledge
   +--> reusable procedure
   +--> training artifact
```

## Initial ecosystem targets

Sovereign is being designed to interoperate with, not absorb, the following replaceable substrates:

- **QM / Quartermaster** — multiplayer agent harness and scoped execution substrate.
- **OpenClaw** — shared human/agent workspace, sessions, channels, and execution substrate.
- **GBrain** — mutable working/world knowledge substrate.
- **Memorable** — procedural-memory substrate.
- **River AI** — model-specialization/training substrate.

No one integration is required for the core contracts.

## Architecture

```text
                         GOVERNANCE / OPERATOR
                                 |
                                 v
                         Promotion Decision
                                 ^
                                 |
            +--------------------+--------------------+
            |                    |                    |
            |              independent eval           |
            |                    ^                    |
            v                    |                    v
       factual context       candidate result     procedural memory
          (GBrain)                ^               (Memorable)
            \                    / \                    /
             \                  /   \                  /
              +------ agent execution / harness ------+
                         (replaceable adapters)
                                  |
                                  v
                         accepted trajectory
                                  |
                                  v
                         training artifact
                                  |
                                  v
                              River / local
```

The core intentionally distinguishes:

```text
FACTUAL MEMORY      != CANONICAL KNOWLEDGE
PROCEDURAL MEMORY   != VALIDATED PROCEDURE
SUCCESSFUL RUN      != ACCEPTED EVIDENCE
ACCEPTED EVIDENCE   != TRAINING AUTHORITY
MODEL IMPROVEMENT   != AUTHORITY EXPANSION
```

## Sovereign Swarm Control Mesh

Sovereign now defines a portable A2A coordination pattern, **SSCM**, for heterogeneous agents that collaborate through governed state and artifact references rather than full transcript exchange.

```text
agent executor
    -> structured collaboration event
    -> blackboard / artifact refs
    -> next executor
```

SSCM is deliberately executor-neutral: Claude Code, Codex, Antigravity, local models, QM, OpenClaw, and future systems may attach without requiring a common model API or shared credential broker.

See [`docs/SSCM.md`](docs/SSCM.md) and [`contracts/collaboration/a2a-collaboration-event.v0.1.schema.json`](contracts/collaboration/a2a-collaboration-event.v0.1.schema.json).

## Core contracts

The core promotion loop remains deliberately frozen at four portable JSON contracts:

1. `experience.v0.1` — a bounded execution/experience candidate.
2. `evaluation.v0.1` — independent evidence about that candidate.
3. `promotion.v0.1` — an explicit proposal/decision boundary.
4. `training-artifact.v0.1` — an accepted trajectory packaged for model specialization without implying permission to train.

SSCM collaboration contracts live separately under `contracts/collaboration/`. This keeps execution coordination replaceable and prevents the collaboration substrate from silently becoming part of the promotion authority plane.

## Miskatonic Systems relationship

Sovereign is intentionally independent of Miskatonic Systems. It may later be integrated through explicit adapters and governed bindings, but it does not inherit Miskatonic authority, credentials, private state, or repository access by association.

The intended future relationship is:

```text
REGISTER -> REFERENCE -> QUALIFY -> BIND
```

not copy-and-paste coupling.

## Swarm Control Mesh (SSCM)

`sscm/` coordinates heterogeneous local coding agents through an append-only blackboard and artifact-bound handoffs. Executors keep their native provider logins; the mesh stores coordination facts and digests, never transcripts or credentials. First live qualification: [WO-SOVEREIGN-SSCM-01A](docs/work-orders/WO-SOVEREIGN-SSCM-01A-REPORT.md). Design: [docs/SSCM.md](docs/SSCM.md).

## Adapters

| Adapter | Maturity | Work order |
|---|---|---|
| [QM](adapters/qm/README.md) | `STRUCTURALLY_VALIDATED` | [WO-SOVEREIGN-QM-ADAPTER-00A](docs/work-orders/WO-SOVEREIGN-QM-ADAPTER-00A.md) |
| OpenClaw | not started | |
| GBrain | not started | |
| Memorable | not started | |
| River AI | not started | |

Adapter contracts live under `contracts/adapters/`. Run the suite with:

```bash
pip install -r requirements-dev.txt
python3 -m pytest -q
```

## Status

`BOOTSTRAP / CONTRACT DESIGN` + QM adapter structurally validated + SSCM A2A contract bootstrapped

No production authority is implemented. No external provider is trusted by default. No automatic promotion, canonical knowledge write, model training, or recursive permission expansion exists.

## License

MIT. See `LICENSE`.
