# WO-MSK-HARNESS-QM-01A — QM harness adapter, structural validation

| Field | Value |
|---|---|
| Work order | `WO-MSK-HARNESS-QM-01A` |
| Repository | `kowen9024AI/sovereign` |
| Increment | first of the `HARNESS-QM` series |
| Result | `STRUCTURALLY_VALIDATED` adapter for QM; `CANDIDATE_READY`, PR #1, hosted CI green; merge is human-gated |
| Date | 2026-09-06 |

## Scope derivation

The work-order text itself was not present in the repository, on the local
filesystem, in any reachable GitHub repository, or in prior session history.
Scope was therefore derived from the repository's own roadmap:

- `README.md` names QM as the first adapter candidate and the harness plane as its surface.
- `adapters/README.md` already enumerates the QM adapter concerns (scope identity, harness/model identity, sandbox identity, run/task identity, tool receipts, completion, posture, credential-free refs).
- `adapters/README.md` defines the maturity ladder; the first rung achievable without live access is `STRUCTURALLY_VALIDATED`.

If the authoritative WO text differs, reconcile against this document. Nothing
here claims more than structural validation.

## Deliverables

| # | Deliverable | Path |
|---|---|---|
| 1 | QM harness receipt contract | `contracts/adapters/qm-harness-receipt.v0.1.schema.json` |
| 2 | Adapter: validation, credential rescan, deterministic mapping to `experience.v0.1` | `adapters/qm/qm_adapter.py`, `adapters/qm/__main__.py` |
| 3 | Adapter specification and field mapping | `adapters/qm/README.md` |
| 4 | Maturity record | `adapters/qm/MATURITY.json` |
| 5 | Fixtures: 6 valid (one per outcome path plus a shared-channel minimal case), 10 invalid (every rejection code) | `adapters/qm/fixtures/` |
| 6 | Tests: 44, including core-contract well-formedness | `tests/` |
| 7 | CI workflow (pytest + CLI accept/reject smoke) | `.github/workflows/ci.yml` |

## Upstream grounding

Receipt vocabulary is pinned to `yc-software/qm@95b5a6a9941ce517f478147b1c6fa8365a16afa0`
(`qm@0.1.0`): scope kinds, principal types, run and task statuses, harness
transport enums, security postures, tool ledger cell identity, and audit
event shape. The pin is terminology only. No QM code is vendored, imported,
or executed. A test asserts the enums so upstream drift surfaces as a test
failure rather than silent divergence.

## Invariants enforced

| Invariant (from `docs/ARCHITECTURE.md`) | Enforcement |
|---|---|
| `HARNESS_SUCCESS != PROMOTION_ACCEPTANCE` | adapter emits only `experience.v0.1`; `procedure_candidate_ref` and `training_candidate_ref` are always `null` |
| Provider `authority = NONE` | `authority` is a schema constant on both receipt and experience; test asserts it on every fixture |
| Durable refs without credential material | `credential_material_present` is the constant `false`, plus an independent rescan (secret-shaped values, forbidden key fragments, URL userinfo) that rejects the whole receipt |
| QM admin policy is not Sovereign authority | postures are recorded as evidence only; a loosened posture is rejected, never acted on |
| Evidence binds identity | `experience_id` is a digest of the canonical receipt; each tool ledger cell becomes an evidence ref by output digest |

## Evidence

```
python3 -m pytest -q          -> 44 passed
python3 -m adapters.qm adapters/qm/fixtures/valid/done-succeeded.json  -> exit 0, outcome SUCCEEDED
python3 -m adapters.qm <invalid url-userinfo receipt>                  -> exit 1, CREDENTIAL_MATERIAL_SUSPECTED
```

## Gaps and non-claims

- No live QM deployment was read. `LIVE_READ_VALIDATED` is not claimed.
- No receipt exporter exists on the QM side. Receipts in this WO are hand-authored fixtures whose shape follows upstream types, not captured production output.
- `task_class` is reporter-declared; QM has no native task taxonomy.
- Credential rescan is pattern-based and conservative; false positives are accepted as the safe direction.
- Hosted CI: run `34071855654` on PR #1 head `8e55fc8`, `ci` job success (14s).

## Next

- `WO-MSK-HARNESS-QM-01B`: receipt exporter against a local QM instance (`mock` harness), capture real receipts, promote to `LIVE_READ_VALIDATED`. Requires operator-owned QM deployment; no credential enters Sovereign.
- Independent review of this candidate before merge to `main`.
