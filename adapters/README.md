# Adapter Boundary

Sovereign adapters translate external systems into portable contracts. They do **not** transfer provider authority into Sovereign.

## Initial candidates

### QM

Role: multiplayer agent harness / scoped execution substrate.

Expected adapter concerns:

- scope identity and ownership;
- harness/model identity;
- sandbox/workspace identity;
- task/run identity;
- tool/effect receipts;
- completion status;
- provider-side permission posture;
- durable references without credential material.

Do not equate QM administrator policy with Sovereign promotion authority.

### GBrain

Role: working factual/episodic knowledge.

Expected adapter concerns:

- memory item identity;
- source provenance;
- capture/update timestamps;
- backlinks/relationships;
- retrieval evidence;
- mutation/supersession identity.

Working memory is mutable context, not canonical truth.

### Memorable

Role: procedural memory.

Expected adapter concerns:

- procedure identity;
- source experience IDs;
- applicability conditions;
- execution history;
- evaluation references;
- supersession/revocation.

A repeated procedure is not automatically a validated procedure.

### River AI

Role: model specialization/training provider.

Expected adapter concerns:

- immutable training-artifact identity;
- exact dataset digest;
- base model identity;
- training method/configuration;
- checkpoint/output identity;
- provider job identity;
- status and measured evaluation.

Owning a checkpoint does not imply the training occurred locally.

## Adapter maturity

Adapters should report one of:

- `DECLARED`
- `STRUCTURALLY_VALIDATED`
- `LIVE_READ_VALIDATED`
- `LIVE_EFFECT_VALIDATED`
- `QUALIFIED`

`QUALIFIED` must never be inferred from package installation or API reachability alone.
