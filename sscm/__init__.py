"""Sovereign Swarm Control Mesh (SSCM) v0.1.

Executor-neutral, artifact-bound coordination of heterogeneous local coding
agents through an append-only blackboard. Nothing in this package grants,
brokers, or expands authority: every event, artifact, executor, and receipt
carries ``authority = NONE``.

Modules (kept deliberately separate, see WO-SOVEREIGN-SSCM-01A section 8):

* ``contracts``  - schema loading, validation, canonical digests
* ``blackboard`` - SQLite append-only event store, idempotency, predecessor
                   validation, exclusive claims, deterministic projection
* ``artifacts``  - bounded run directory, artifact refs, credential scan
* ``executors``  - executor capability contract + Claude Code / Codex / mock
* ``workspace``  - disposable Git mirror, isolated worktrees, host verification
* ``mission``    - the frozen 01A mission controller (sequencing authority)
* ``receipts``   - mission -> experience.v0.1 candidate (observed only)
* ``local_cli``  - operator entry point
"""

__version__ = "0.1.0"
