# WO-MSK-HARNESS-QM-01A
## Discoverability mirror — QM Multiplayer Coding-Harness Qualification & OpenClaw Comparative Baseline

```text
canonical_repository = Miskatonic-System/miskatonic-control-plane
canonical_revision   = 8a15958f62c959c7e82a11322226ad447d7fcb64
canonical_path       = docs/work-orders/WO-MSK-HARNESS-QM-01A.md
mirror_authority     = NONE
```

**Execution rule:** before performing this work order, resolve and inspect the exact canonical file above. If anything in this mirror differs from the canonical file, the canonical file wins.

This mirror exists because the first coding agent launched from Sovereign could not discover the authoritative Miskatonic WO and therefore derived a different structural adapter scope from roadmap prose. That structural work was preserved separately as `WO-SOVEREIGN-QM-ADAPTER-00A`; it is **not** fulfillment of this work order.

---

## Purpose

Live-qualify exact pinned QM as a candidate Miskatonic multiplayer coding-agent harness, and compare it against the already-observed OpenClaw 2026.8.2 baseline.

Frozen live roster:

```text
Claude coordinator
      -> Codex implementer
      -> independent Claude reviewer
```

QM source:

`yc-software/qm@95b5a6a9941ce517f478147b1c6fa8365a16afa0`

Sovereign structural adapter already merged:

`WO-SOVEREIGN-QM-ADAPTER-00A`

That adapter is evidence infrastructure only and claims `STRUCTURALLY_VALIDATED`, not live QM qualification.

---

## Hard invariants

```text
HARNESS != MODEL_RUNTIME
HARNESS != WORKSPACE
HARNESS != AUTHORITY
SOURCE_INSPECTED != LIVE_QUALIFIED
STRUCTURALLY_VALIDATED != LIVE_QUALIFIED
ADMIN_ROLE != MISKATONIC_GOVERNANCE
HARNESS_SUCCESS != ACCEPTANCE
MODEL_REPORTED_SHA != HOST_OBSERVED_SHA
```

Recursive or adaptive harness state must never become recursive self-authorization.

---

## Gate 0 — economics and deployment

Before installing or provisioning anything, establish from the pinned source:

```text
LOCAL_DISPOSABLE_MODE_AVAILABLE
LOCAL_MODE_REQUIREMENTS
SELF_HOSTED_EXISTING_INFRA_MODE_AVAILABLE
CLOUD_ACCOUNT_REQUIRED
PAYMENT_METHOD_REQUIRED
NEW_FINANCIAL_COMMITMENT_REQUIRED
MINIMUM_EXPECTED_COST
SELECTED_QUALIFICATION_MODE
```

Preference:

1. current workstation / WSL local disposable mode;
2. already-owned or already-free infrastructure;
3. free tier requiring no payment method;
4. otherwise STOP for operator decision.

```text
NEW_FINANCIAL_COMMITMENT_REQUIRED = YES
=> STOP / OPERATOR_DECISION_REQUIRED
```

No cloud resource before this gate passes.

---

## Required source verification

Independently verify exact pinned QM support/semantics for:

- personal/shared scopes;
- Claude Code and Codex harnesses on the common core;
- scope files/memory/permissions/keychain view;
- durable sandbox;
- web/operator surface;
- run/task/session structured state;
- security posture and command policy;
- local dev/sandbox mode;
- relevant credential handling.

Upstream marketing is not qualification evidence.

---

## Live coding target

Use `kowen9024AI/sovereign` only as a disposable dogfood target.

Canonical main must remain unchanged.

Suggested dogfood branch:

`dogfood/qm-multiplayer-harness-01a`

Codex implementer creates exactly:

`docs/qm-harness-dogfood.txt`

with:

```text
MSK_QM_HARNESS_OK
```

and commits it.

The dogfood branch is never merged.

---

## Workspace gate

Before mutation prove from host Git/filesystem facts:

```text
IMPLEMENTER_WRITABLE_WORKSPACE != REVIEWER_WORKSPACE
CANONICAL_CHECKOUT_AUTONOMOUS_WRITE = NO
IMPLEMENTER_WORKSPACE_DIRTY_AT_ADMISSION = NO
```

No shared or overlapping writable workspace between autonomous roles.

Host-observed paths, repository identity, branch/ref, dirty state and SHA outrank model/harness assertions.

---

## Candidate freeze and review

After Codex completes:

- host-observe exact changed files and Git candidate SHA;
- require exact dogfood file/content and clean post-commit worktree;
- freeze candidate SHA;
- prepare an independent reviewer workspace at exactly that SHA;
- launch Claude reviewer through QM;
- reviewer may only return `ACCEPT` or `REPAIR_REQUIRED` plus findings;
- host-observe reviewer HEAD.

Required:

```text
REVIEWED_SHA == CANDIDATE_SHA_FROZEN
```

Reviewer does not repair its own findings.

---

## Shared operator and programmatic evidence

Prove QM exposes one coherent operator surface for the relevant participants/sessions/runs.

Also identify structured, non-transcript sources for:

```text
scope identity
principal/participant identity
harness profile
session identity
run identity
run status
task status
sandbox/workspace identity
security posture
audit/effect references
cleanup/terminal state
```

Any lifecycle fact available only from model prose is `UNQUALIFIED` for adapter purposes.

---

## Secrets and authority

Do not provide production email, Slack, Miskatonic secret, knowledge-write, Workboard-mutation, cloud, merge, or release credentials.

Provider-supported existing Claude/Codex subscription authentication may be used if QM genuinely supports it. Do not invent an API-key fallback.

Never print, display-hash, persist, commit, or serialize credential values into evidence.

All QM scope/admin/harness authority remains `NONE` with respect to Miskatonic.

---

## Sovereign adapter compatibility

If structured live QM state can honestly produce the credential-free receipt required by `WO-SOVEREIGN-QM-ADAPTER-00A`, run the adapter as a secondary compatibility check.

Do not weaken the schema or fabricate absent fields to make live QM fit.

An emitted `experience.v0.1` remains an observed experience candidate only.

---

## OpenClaw comparison

Use existing evidence where possible:

```text
OpenClaw 2026.8.2
rich shared session/operator surface = YES
Claude integration = YES
Codex integration = YES
collector swarm scheduling = YES
awaitable collector Codex coding = BLOCKED in installed release
ordinary tool-bearing subagent coding = designed/supported, not yet accepted by parked Workbench PR #42
```

Do not classify OpenClaw as globally failed.

Required comparative dimensions:

```text
multi-user/scope model
Claude + Codex common core
workspace isolation
structured observability
shared operator UX
session continuation
credential separation
effect fencing
background work
cleanup/reconciliation
local/self-host economics
Miskatonic adapter complexity
```

Allowed comparative dispositions:

```text
QM_PREFERRED_FOR_MULTIPLAYER_HARNESS
OPENCLAW_PREFERRED_FOR_MULTIPLAYER_HARNESS
COMPLEMENTARY_SUBSTRATES
NEITHER_QUALIFIED
```

---

## Cleanup

After evidence capture:

- delete dogfood branch locally/remotely;
- remove temporary worktrees;
- stop/delete disposable QM local instance;
- remove disposable scope/session state where supported and safe;
- leave Sovereign canonical main unchanged;
- leave no cloud/recurring resource;
- leave no credential material in evidence.

Do not perform direct state-store surgery to manufacture clean status.

---

## Maturity ceiling

This WO may advance the Control Plane QM harness-substrate profile at most to:

`LIVE_EFFECT_QUALIFIED`

It may **not** earn `BOUND`.

Binding is a later Miskatonic authorization decision.

---

## Positive disposition

`QM_MULTIPLAYER_HARNESS_LIVE_QUALIFIED`

This does not make QM canonical, retire OpenClaw, grant QM Miskatonic authority, or bind QM to production tasks.

For the complete required closure field list and repository-mutation policy, use the canonical Control Plane file named at the top of this mirror.
