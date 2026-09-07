"""Provider-neutral evaluator interface and admission checks for returned evaluations."""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping, Protocol

from .contracts import DISPOSITIONS, EvaluationInvalid, candidate_ref, is_independent_evaluator_id, validate_evaluation


@dataclass(frozen=True)
class EvaluationRequest:
    mission_id: str
    candidate_sha: str
    base_sha: str
    worktree: Path
    evaluation_profile_id: str
    acceptance_fixture_ref: str
    evaluation_run_id: str
    authority: str = "NONE"

    @property
    def candidate_ref(self) -> str:
        return candidate_ref(self.candidate_sha)


@dataclass
class EvaluationRun:
    """Deterministic runtime invocation record. No provider session is fabricated for a non-model evaluator."""

    evaluator_id: str
    evaluator_runtime: str
    evaluation_run_id: str
    started_at: float
    finished_at: float
    evaluation: dict[str, Any] | None
    receipt: dict[str, Any] = field(default_factory=dict)  # profile metadata, observed digests, affected files
    error: str | None = None
    authority: str = "NONE"

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["wall_seconds"] = round(self.finished_at - self.started_at, 3)
        return d


class Evaluator(Protocol):
    evaluator_id: str
    evaluator_runtime: str

    def profile_metadata(self) -> dict[str, Any]: ...
    def evaluate(self, request: EvaluationRequest) -> EvaluationRun: ...


def admit_evaluation(run: EvaluationRun, *, expected_evaluator_id: str, expected_candidate_sha: str,
                     roster_executor_ids: set[str], expected_profile_digest: str | None) -> dict[str, Any]:
    """Host-side admission of an evaluator's output. Raises EvaluationInvalid with a precise code.

    * schema + authority NONE
    * evaluator identity is the configured independent evaluator, never a roster executor
    * candidate_ref binds exactly the current host-verified revision (no stale evaluation)
    * profile digest equals the one used before (no goalpost movement)
    """
    if run.error or run.evaluation is None:
        raise EvaluationInvalid("EVALUATION_RUN_FAILED", run.error or "evaluator returned no evaluation")
    ev = run.evaluation
    validate_evaluation(ev)
    eid = ev["evaluator"]["evaluator_id"]
    if eid != expected_evaluator_id or not is_independent_evaluator_id(eid, roster_executor_ids) or ev["evaluator"]["independence_class"] != "INDEPENDENT":
        raise EvaluationInvalid("EVALUATOR_INDEPENDENCE_VIOLATION", f"evaluator {eid} ({ev['evaluator']['independence_class']}) is not the configured independent evaluator {expected_evaluator_id}")
    if run.evaluator_id != eid:
        raise EvaluationInvalid("EVALUATOR_INDEPENDENCE_VIOLATION", f"run identity {run.evaluator_id} != evaluation evaluator_id {eid}")
    if ev["candidate_ref"] != candidate_ref(expected_candidate_sha):
        raise EvaluationInvalid("EVALUATION_CANDIDATE_MISMATCH", f"evaluation binds {ev['candidate_ref']}, current verified candidate is git:{expected_candidate_sha}")
    if ev["disposition"] not in DISPOSITIONS:
        raise EvaluationInvalid("EVALUATION_SCHEMA_VIOLATION", ev["disposition"])
    pd = run.receipt.get("profile_digest")
    if not pd:
        raise EvaluationInvalid("EVALUATOR_PROFILE_MISSING", "evaluator receipt carries no profile digest")
    if expected_profile_digest is not None and pd != expected_profile_digest:
        raise EvaluationInvalid("EVALUATOR_PROFILE_DRIFT", f"profile digest {pd} != {expected_profile_digest}")
    return ev
