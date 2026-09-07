# WO-SOVEREIGN-QM-ADAPTER-00A — QM receipt adapter structural bootstrap

| Field | Value |
|---|---|
| Work order | `WO-SOVEREIGN-QM-ADAPTER-00A` |
| Repository | `kowen9024AI/sovereign` |
| Result | `STRUCTURALLY_VALIDATED` QM receipt adapter; no live QM qualification claimed |
| Date | 2026-09-06 |

## Provenance correction

This implementation was originally opened as `WO-MSK-HARNESS-QM-01A` because the coding agent could not locate the authoritative Miskatonic qualification work order. The actual `WO-MSK-HARNESS-QM-01A` was maintained outside this repository and required a live multiplayer QM qualification, deployment/economics Gate 0, Claude/Codex/Claude dogfood run, exact candidate/review evidence, and comparative OpenClaw adjudication.

Those requirements were not executed here.

This candidate is therefore reclassified as a prior **Sovereign-local structural adapter bootstrap** rather than accepted as fulfillment of the Miskatonic qualification WO.

No scientific or operational evidence is discarded by this correction.

## Scope actually executed

Scope was derived from Sovereign's bootstrap roadmap and adapter boundary:

- define a credential-free QM terminal-run receipt contract;
- pin terminology to exact upstream QM source;
- deterministically map an accepted receipt into `sovereign.experience.v0.1`;
- independently rescan receipt contents for credential material;
- keep provider authority at `NONE`;
- add hostile fixtures and CI;
- claim only structural validation.

## Deliverables

| # | Deliverable | Path |
|---|---|---|
| 1 | QM harness receipt contract | `contracts/adapters/qm-harness-receipt.v0.1.schema.json` |
| 2 | Adapter validation, credential rescan, deterministic experience mapping | `adapters/qm/qm_adapter.py` |
| 3 | Adapter specification | `adapters/qm/README.md` |
| 4 | Maturity record | `adapters/qm/MATURITY.json` |
| 5 | Six valid and ten invalid fixtures | `adapters/qm/fixtures/` |
| 6 | 44 tests | `tests/` |
| 7 | CI workflow | `.github/workflows/ci.yml` |

## Upstream grounding

Terminology pin:

`yc-software/qm@95b5a6a9941ce517f478147b1c6fa8365a16afa0`

No QM code is vendored, imported, installed, or executed by this work.

## Invariants enforced

- `HARNESS_SUCCESS != PROMOTION_ACCEPTANCE`
- `PROVIDER_AUTHORITY = NONE`
- credential material causes whole-receipt rejection
- QM security posture is evidence, not Sovereign authority
- output is an experience candidate only
- no procedure or training candidate is minted by this adapter

## Evidence

Candidate evidence reported by the implementation:

```text
python3 -m pytest -q -> 44 passed
adapter valid-fixture CLI smoke -> exit 0
credential-bearing fixture -> rejected
hosted CI -> green
```

## Maturity

Claimed:

`STRUCTURALLY_VALIDATED`

Explicitly not claimed:

- `LIVE_READ_VALIDATED`
- `LIVE_EFFECT_VALIDATED`
- `QUALIFIED`
- `QM_MULTIPLAYER_HARNESS_LIVE_QUALIFIED`

## Next boundary

The authoritative `WO-MSK-HARNESS-QM-01A` is a separate live Miskatonic qualification and must be stored canonically in this repository before execution so future agents do not derive scope from roadmap prose.

Authority: `NONE`.
