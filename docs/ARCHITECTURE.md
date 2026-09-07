# Sovereign Architecture v0.1

## Purpose

Sovereign governs the transition from **experience** to **durable learning artifacts**. It is not an agent harness, memory database, evaluator implementation, or training service. Those are replaceable substrates behind adapters.

## Planes

```text
EXECUTION PLANE
  agent harnesses, tools, sandboxes, model calls

WORKING-MEMORY PLANE
  factual/episodic context and mutable knowledge structures

PROCEDURAL-MEMORY PLANE
  reusable task procedures derived from experience

EVALUATION PLANE
  independent tests, graders, review, measurements

PROMOTION PLANE
  candidate -> decision transition

TRAINING-ARTIFACT PLANE
  accepted trajectories packaged for specialization
```

No plane may grant itself authority in another plane.

## Replaceable adapters

Initial targets:

| Surface | Candidate | Initial relationship |
|---|---|---|
| multiplayer harness | QM | `STRUCTURALLY_VALIDATED` (`adapters/qm/`) |
| working knowledge | GBrain | adapter candidate |
| procedural memory | Memorable | adapter candidate |
| model specialization | River AI | adapter candidate |
| evaluation | external / local evaluators | interface only |

An adapter proves interoperability, not trust.

## Promotion state machine

```text
OBSERVED
  -> CANDIDATE
  -> EVALUATED
  -> PROMOTION_PROPOSED
  -> ACCEPTED | REJECTED | DEFERRED
```

Only `ACCEPTED` candidates may produce downstream durable artifacts. Acceptance still does not imply authority to execute model training or to write into an external canonical store; those are separately authorized effects.

## Authority invariants

1. `HARNESS_SUCCESS != PROMOTION_ACCEPTANCE`
2. `MEMORY_RECALL != FACT`
3. `PROCEDURE_REUSE != PROCEDURE_VALIDATION`
4. `EVALUATOR_OUTPUT != SELF_EXECUTING_AUTHORITY`
5. `PROMOTION_ACCEPTED != TRAINING_EXECUTED`
6. `TRAINING_IMPROVEMENT != AUTHORITY_EXPANSION`
7. Every provider and adapter has `authority = NONE` unless an external authority explicitly grants an effect.

## Evidence

Every decision should bind:

- candidate identity;
- source/producer identity;
- exact evaluation evidence;
- evaluator identity;
- decision identity;
- resulting artifact identity;
- supersession/rejection state when applicable.

Provider prose is evidence only when the provider is explicitly the source being measured. It is never authoritative merely because it is confident.

## Miskatonic integration boundary

Sovereign is deliberately portable and standalone. Miskatonic Systems may later consume its contracts or bind an exact release through a governed adapter. No private Miskatonic repository, credential, authority grant, or canonical knowledge store is a prerequisite for Sovereign operation.

The integration sequence is intentionally one-way and earned:

```text
EXTERNAL PROJECT
   -> REGISTER
   -> REFERENCE
   -> QUALIFY
   -> BIND EXACT RELEASE
   -> AUTHORIZE BOUNDED EFFECTS SEPARATELY
```
