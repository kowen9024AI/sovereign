# Work Orders

This directory may contain two different classes of work order:

1. **Sovereign-native work orders** — canonical in this repository.
2. **External work-order mirrors** — discoverability copies or manifests for work orders canonically owned elsewhere.

## External mirror rule

A coding agent must never derive an external work order from roadmap prose when a mirror file exists here.

Every external mirror must state:

```text
canonical_repository
canonical_path
canonical_revision
mirror_authority = NONE
```

If the local mirror and the named canonical file differ, the exact canonical file wins.

A mirror may summarize the canonical body for local discoverability, but the coding agent must resolve and inspect the canonical file before execution.

## Miskatonic work orders

Miskatonic work orders executed against or using Sovereign are canonically owned by their named Miskatonic repository, normally `Miskatonic-System/miskatonic-control-plane` for execution/harness qualification.

The local mirror exists specifically so a coding agent launched from this standalone repository can discover the task without access to prior chat history.

## Sovereign-native work orders in this repository

| Work order | Status |
|---|---|
| [`WO-SOVEREIGN-QM-ADAPTER-00A`](WO-SOVEREIGN-QM-ADAPTER-00A.md) | `STRUCTURALLY_VALIDATED` |
| [`WO-SOVEREIGN-SSCM-01A`](WO-SOVEREIGN-SSCM-01A.md) | `SOVEREIGN_SSCM_LOCAL_A2A_QUALIFIED` — [report](WO-SOVEREIGN-SSCM-01A-REPORT.md); R1 `SOVEREIGN_SSCM_LOCAL_A2A_R1_READY` — [report](WO-SOVEREIGN-SSCM-01A-R1-REPORT.md) |
| [`WO-SOVEREIGN-SSCM-01B`](WO-SOVEREIGN-SSCM-01B.md) | `SOVEREIGN_SSCM_PARALLEL_FANOUT_FANIN_QUALIFIED` — [report](WO-SOVEREIGN-SSCM-01B-REPORT.md) |
