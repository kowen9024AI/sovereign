# QM Harness Adapter v0.1

**Maturity claimed:** `STRUCTURALLY_VALIDATED` (see [`MATURITY.json`](./MATURITY.json)).
**Authority:** `NONE`, always.

This adapter translates one terminal QM (Quartermaster) run into a Sovereign
`experience.v0.1` candidate. It does nothing else. It does not talk to a QM
deployment, does not hold QM credentials, does not evaluate, and does not
promote.

## Upstream reference

| Item | Value |
|---|---|
| Project | [`yc-software/qm`](https://github.com/yc-software/qm), MIT |
| Pinned reference commit | `95b5a6a9941ce517f478147b1c6fa8365a16afa0` (package `qm@0.1.0`) |
| Terminology sources | `src/types.ts`, `src/runs/run-store.ts`, `src/tasks/task-store.ts`, `src/runs/tool-ledger.ts`, `src/harness/harness.ts`, `src/security/security-posture.ts`, `src/audit/audit-log.ts` |

The pin is a *terminology* pin. It records which upstream vocabulary the
receipt schema tracks. It is not a dependency, a vendored copy, or a trust
grant.

## Input: `qm-harness-receipt.v0.1`

Schema: [`contracts/adapters/qm-harness-receipt.v0.1.schema.json`](../../contracts/adapters/qm-harness-receipt.v0.1.schema.json).

A receipt is produced *outside* Sovereign by whoever operates the QM
deployment (a QM plugin, an export job, or a human). It describes a run that
has already reached a terminal state. It carries:

| Receipt section | QM concept | Adapter concern from `adapters/README.md` |
|---|---|---|
| `deployment` | deployment repo / org scope | provider identity |
| `scope` | `ScopeId` = `<kind>:<ref>`, kind in `personal, channel, team, org, group` | scope identity and ownership |
| `principal` | `principalId`, `PrincipalType` | ownership |
| `harness` | `HarnessAdapterProfile` (`id`, transports), model | harness/model identity |
| `sandbox` | per-scope durable sandbox | sandbox/workspace identity |
| `session`, `run`, `run.tasks` | `Run` (`pending, running, done, failed`), `Task` statuses | task/run identity, completion status |
| `tool_receipts` | `ToolLedger` cells `(runId, attempt, callIndex)` | tool/effect receipts |
| `security` | `SecurityPosture` (`strict, auto, dangerous`), command policy | provider-side permission posture |
| `audit_refs`, `durable_refs` | `AuditLog` events, commits/PRs/files/messages | durable references |
| `credential_material_present: false` | keychain is out of band | no credential material |

Rules the schema enforces:

- `run.status` must be terminal (`done` or `failed`). A running run is not a receipt.
- Tool output is referenced by `output_sha256` only. Inlined output is a schema violation.
- `additionalProperties: false` everywhere, so no field can be smuggled in.
- `credential_material_present` is the constant `false` and `authority` is the constant `NONE`.

## Output: `experience.v0.1`

| Experience field | Derivation |
|---|---|
| `experience_id` | `qm:exp:` + first 32 hex of SHA-256 over the canonical JSON of the whole receipt |
| `producer.harness` | `qm:<harness.profile_id>` |
| `producer.agent` | `qm:scope:<scope.scope_id>` (in QM the agent acts *as the scope's principal*) |
| `producer.runtime` | `qm:<deployment_ref>@<qm_version>` |
| `producer.model` | `harness.model_ref` or `null` |
| `task_class` | `receipt.task_class`, default `qm.turn` |
| `scope_ref` | `qm:scope:<scope.scope_id>` |
| `started_at`, `completed_at` | `run.started_at_ms`, `run.finished_at_ms` as RFC 3339 UTC |
| `outcome` | see table below |
| `artifact_refs` | `qm:<kind>:<ref>` for each `durable_refs` entry |
| `evidence_refs` | receipt digest, then one `qm:tool-ledger:<run>:<attempt>:<call>:sha256:<digest>` per tool receipt, then `qm:audit:<ref>` |
| `procedure_candidate_ref`, `training_candidate_ref` | always `null`; the adapter proposes nothing downstream |
| `authority` | `NONE` |

### Outcome mapping

Precedence is total and fixed, top wins:

| Condition | Outcome | Why |
|---|---|---|
| `run.stopped` | `CANCELLED` | principal aborted the turn |
| `run.status == failed` | `FAILED` | harness reported failure |
| `paused_on_approval` or `pending_approval_count > 0` | `BLOCKED` | ended waiting on a human |
| `reap_outcome == parked` | `BLOCKED` | queue gave up without a result |
| `run.status == done` | `SUCCEEDED` | harness reported completion |

`SUCCEEDED` means the harness said it finished. It is not evidence of
correctness. `HARNESS_SUCCESS != PROMOTION_ACCEPTANCE`.

## Rejections

The adapter emits a full experience or nothing. Rejection codes:

| Code | Trigger |
|---|---|
| `RECEIPT_SCHEMA_VIOLATION` | structural validation failed |
| `CREDENTIAL_MATERIAL_SUSPECTED` | independent rescan found a secret-shaped value or a forbidden key name. The reporter's own `false` flag is not trusted. |
| `POSTURE_LOOSENED` | `effective_posture` is looser than `org_posture`. QM only lets narrower scopes tighten. |
| `TIME_ORDER_VIOLATION` | `finished_at_ms < started_at_ms` or `started_at_ms < created_at_ms` |
| `UNMAPPABLE_RUN_STATUS` | defensive; unreachable through the schema |

## What this adapter does not do

- No live read of any QM deployment. That is `LIVE_READ_VALIDATED`, a later step.
- No effect back into QM (no memory writes, no skill grants, no approvals). That is `LIVE_EFFECT_VALIDATED`, separately authorized.
- No evaluation. An experience becomes `EVALUATED` only through an independent `evaluation.v0.1`.
- No mapping of QM administrator policy onto Sovereign promotion authority.

## Usage

```bash
python3 -m adapters.qm adapters/qm/fixtures/valid/done-succeeded.json
python3 -m pytest -q
```
