# WO-SOVEREIGN-SSCM-01A — Execution Report

| Field | Value |
|---|---|
| Disposition | **`SOVEREIGN_SSCM_LOCAL_A2A_QUALIFIED`** |
| Live mission | `mission-sscm-01a-live-4` (2026-09-07 UTC) |
| Canonical base | `cf6c55fe5fadd389dcd34132514b88e1bc92bcd8` (sovereign `main`, unchanged) |
| Evidence | [`WO-SOVEREIGN-SSCM-01A-evidence/`](WO-SOVEREIGN-SSCM-01A-evidence/) — sanitized copies of the run artifacts, blackboard event export, projection; credential scan CLEAN |

## What was proven

A Claude Code coordinator, a Codex implementer and a Claude Code reviewer completed one bounded coding mission through the SSCM blackboard with **no shared harness, no shared model API, no credential handoff, no transcript exchange, no shared writable checkout, and no cloud resource**. Every revision that mattered was observed by host Git, not taken from a model.

```text
PUBLISH -> CLAIM -> COMPLETE -> OBSERVE -> CLAIM -> COMPLETE -> OBSERVE -> COMPLETE
```

| Role | Executor | Provider session | Exit | Wall (s) | Model calls / cost |
|---|---|---|---|---|---|
| COORDINATOR | `executor:claude-code` | `5e16cf71-2b26-480b-844c-ce959534f061` | 0 | 19.975 | 2 turns / USD 0.442 |
| IMPLEMENTER | `executor:codex` | `codex-thread:01a079d4-db7c-7af1-befb-1670155aca7a` | 0 | 32.463 | 1 turn / subscription |
| REVIEWER | `executor:claude-code` | `37ffd027-794a-4d02-989e-e059e01a86ab` | 0 | 27.837 | 6 turns / USD 0.301 |

Coordinator and reviewer are distinct Claude sessions; implementer (Codex) and reviewer (Claude Code) are distinct runtime families.

## Host-observed workspace facts

| | Implementer | Reviewer |
|---|---|---|
| realpath | `…/runs/mission-sscm-01a-live-4/workspaces/implementer` | `…/runs/mission-sscm-01a-live-4/workspaces/reviewer` |
| git common dir | per-mission bare mirror `workspaces/mirror.git` | same mirror |
| branch | `dogfood/sscm-01a-implementer` | `HEAD` (detached) |
| HEAD at admission | `cf6c55fe5fadd389dcd34132514b88e1bc92bcd8` (= base) | `3de5b82552f39d6c319f5171ed22911b4ca89d75` (= verified candidate) |
| dirty at admission | False | False |
| is canonical checkout | False | False |

Why a mirror: a `git worktree` shares its object store with the source repository, and a committing executor needs write access to that common dir. Each mission therefore clones its own bare mirror of the canonical base; the canonical checkout is never a writable root for any executor (`CANONICAL_CHECKOUT_AUTONOMOUS_WRITE = NO`).

## Candidate and review verification (host Git)

| Check | Result |
|---|---|
| verified candidate revision | `3de5b82552f39d6c319f5171ed22911b4ca89d75` |
| implementer-claimed revision | `3de5b82552f39d6c319f5171ed22911b4ca89d75` (matches; informational only) |
| descends from base | True (1 commit) |
| changed files | `['docs/sscm-dogfood.txt']` |
| content | `SOVEREIGN_SSCM_OK\n` byte-exact |
| implementer worktree clean after commit | True |
| reviewer worktree HEAD | `3de5b82552f39d6c319f5171ed22911b4ca89d75` (detached True, dirty False) |
| reviewer-reported revision matches | True |
| verdict / blocking findings | `ACCEPT` / 0 |

Reviewer checks it ran itself (read-only tools): `git rev-parse HEAD`, `git merge-base --is-ancestor cf6c55fe5fadd389dcd34132514b88e1bc92bcd8 HEAD`, `git diff --name-only cf6c55fe5fadd389dcd34132514b88e1bc92bcd8..HEAD`, `git show HEAD:docs/sscm-dogfood.txt | od -c`, `git show HEAD:docs/sscm-dogfood.txt | sha256sum (compared against printf 'SOVEREIGN_SSCM_OK\n' | sha256sum)`, `git status --porcelain`.

## Experience candidate

`sscm:exp:f348d0bb05477a3b69adeef3e447f281` — outcome `SUCCEEDED`, authority `NONE`, `procedure_candidate_ref` None, `training_candidate_ref` None, 9 evidence refs (every event by digest + projection digest). No `evaluation.v0.1`, `promotion.v0.1`, or `training-artifact.v0.1` was created.

## Executor discovery (read-only, installed CLIs)

```text
CLAUDE_EXECUTOR_AVAILABLE:            YES
CLAUDE_EXECUTOR_VERSION:              2.1.263 (Claude Code)
CLAUDE_NATIVE_AUTH_READY:             TRUE  (claude.ai login; PROVIDER_NATIVE_LOGIN)
CLAUDE_WORKING_DIRECTORY_SUPPORTED:   YES  (process cwd; --add-dir; --restricted confines file tools)
CLAUDE_STRUCTURED_RESULT_SUPPORTED:   YES  (-p --output-format json --json-schema)

CODEX_EXECUTOR_AVAILABLE:             YES
CODEX_EXECUTOR_VERSION:               codex-cli 0.152.1
CODEX_NATIVE_AUTH_READY:              TRUE  (Logged in using ChatGPT; PROVIDER_NATIVE_LOGIN)
CODEX_WORKING_DIRECTORY_SUPPORTED:    YES  (exec -C <dir> --add-dir <mirror> --sandbox workspace-write)
CODEX_STRUCTURED_RESULT_SUPPORTED:    YES  (--output-schema, -o last-message, --json events)
```

No `CLAUDE_CODE_OAUTH_TOKEN`, no `claude setup-token`, no `~/.codex` copy, no child auth file, no API-key fallback. Provider-owned env variables are dropped from the executor environment by name; nothing is read.

## Live run ledger (honest count)

| Mission | Outcome | Real model runs |
|---|---|---|
| live-1 | blocked before any model call: Claude CLI rejected the `$schema` meta key in `--json-schema` | 0 |
| live-2 | blocked before any model call: variadic `--tools` swallowed the prompt argument | 0 |
| live-3 | coordinator OK (1 Claude run); Codex blocked before any model work: strict schema needs `type` beside `const` | 1 Claude |
| **live-4** | **QUALIFIED** | 1 Claude coordinator, 1 Codex implementer, 1 Claude reviewer |

Totals: `REAL_CLAUDE_COORDINATOR_RUNS = 2`, `REAL_CODEX_IMPLEMENTER_RUNS = 1`, `REAL_CLAUDE_REVIEWER_RUNS = 1`, `OTHER_MODEL_RUNS = 0`. The three earlier failures were CLI-argument/schema infrastructure faults fixed by deterministic inspection, not prose retries.

One observation from live-3 shaped the design: the coordinator had run with its cwd inside the canonical checkout and the CLI's automatic context surfaced canonical Git status into its notes. The coordinator now runs from a neutral empty directory outside every repository.

## Section 7 — prior QM experiment

```text
QM_PRIOR_INSTANCE_RUNNING:      NO   (scripts/dev/cli.ts down; supervisor/core/web/admin/portal gone; no QM ports listening)
QM_POSTGRES_RUNNING:            NO   (qm-qual-postgres container and its anonymous volume removed via podman)
QM_SANDBOX_RESOURCES_RUNNING:   NO   (no qm.sandbox containers existed; images remain inert on disk)
QM_CLOUD_RESOURCES:             NONE
QM_CREDENTIAL_VALUES_CAPTURED:  NONE
```

The QM source checkout at `~/qm-qual/qm` remains inert. `WO-SOVEREIGN-QM-ADAPTER-00A` is preserved at `STRUCTURALLY_VALIDATED`; its 31 tests still pass.

## Tests and CI

`python3 -m pytest -q` → 96 passed (31 QM adapter, 5 core contract, 60 SSCM: contracts, blackboard, mission). Covers restart, concurrent exclusive claim, order determinism, and every section 55 failure case. CI adds an offline mock-roster dogfood run so the mission controller and host verification execute on the hosted runner without provider credentials.

## Cleanup

```text
DOGFOOD_BRANCH_MERGED = NO
DOGFOOD_BRANCH_RETAINED = NO        (branch lived only in the per-mission mirror; mirror destroyed)
DOGFOOD_WORKTREES_RETAINED = NO     (implementer + reviewer worktrees removed for live-3 and live-4)
SOVEREIGN_MAIN_MUTATED_BY_DOGFOOD = NO   (main == cf6c55fe…; `git branch --list dogfood/*` empty; single canonical worktree)
```

## QM / OpenClaw

```text
QM       = optional future SSCM adapter (SSCM-02A)
OpenClaw = optional future SSCM adapter / presentation substrate (SSCM-02B)
```

Neither was touched at runtime.

## Not claimed

Production readiness; parallel swarming; recursive repair; QM/OpenClaw adapters; Miskatonic integration; autonomous merge. The reviewer's ACCEPT is evidence, not an `evaluation.v0.1`.

## R1 addendum (WO-SOVEREIGN-SSCM-01A-R1)

The live run above is retained unchanged as historical evidence; its credential scan was CLEAN in that actual run
and its revisions were full 40-hex ids that also satisfy the stricter R1 comparison. Independent review of the
v0.1 code found three generic fail-open semantics, repaired in
[`WO-SOVEREIGN-SSCM-01A-R1-REPORT.md`](WO-SOVEREIGN-SSCM-01A-R1-REPORT.md): abbreviated SHA prefixes could
satisfy revision gates; model-call/usage budgets were declared but not charged from observed executor
consumption; the final credential scan ran after terminal success. No live rerun was performed
(`LIVE_RERUN = NOT_REQUIRED_SEMANTIC_REPAIR_ONLY`); the repaired parsers were replayed offline against this
run's retained provider logs (coordinator 2 turns / 20.112 kilotokens / USD 0.4416; reviewer 6 turns /
66.601 kilotokens / USD 0.3005; Codex 1 turn / 85.31 kilotokens).
