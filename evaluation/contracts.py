"""evaluation.v0.1 binding, acceptance profiles, evaluator identity rules."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping

from sscm.contracts import CONTRACTS, ContractViolation, REVISION_RE, canonical_json, validate

EVALUATION_SCHEMA_PATH = CONTRACTS / "evaluation.v0.1.schema.json"
EVALUATION_SCHEMA_VERSION = "sovereign.evaluation.v0.1"
DISPOSITIONS = ("PASS", "FAIL", "BLOCKED", "INCONCLUSIVE")


class EvaluationInvalid(ContractViolation):
    pass


def validate_evaluation(evaluation: Mapping[str, Any]) -> None:
    try:
        validate(evaluation, EVALUATION_SCHEMA_PATH, "EVALUATION_SCHEMA_VIOLATION")
    except ContractViolation as ex:
        raise EvaluationInvalid(ex.code, ex.detail) from ex
    if evaluation.get("authority") != "NONE":
        raise EvaluationInvalid("AUTHORITY_NOT_NONE", "evaluations never carry authority")
    ref = str(evaluation.get("candidate_ref", ""))
    if not ref.startswith("git:") or not REVISION_RE.match(ref[4:]):
        raise EvaluationInvalid("EVALUATION_CANDIDATE_REF_INVALID", f"candidate_ref must be git:<40-hex>, got {ref!r}")


def candidate_ref(sha: str) -> str:
    if not REVISION_RE.match(sha):
        raise EvaluationInvalid("EVALUATION_CANDIDATE_REF_INVALID", sha)
    return f"git:{sha}"


@dataclass(frozen=True)
class AcceptanceProfile:
    """A frozen, digest-bound acceptance oracle. The digest is identity: any change is a different profile."""

    profile_id: str
    target_file: str
    expected_content: str
    description: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"profile_id": self.profile_id, "target_file": self.target_file, "expected_content": self.expected_content, "description": self.description}

    @property
    def digest(self) -> str:
        return hashlib.sha256(canonical_json(self.as_dict())).hexdigest()

    @property
    def expected_digest(self) -> str:
        return hashlib.sha256(self.expected_content.encode("utf-8")).hexdigest()

    def metadata(self) -> dict[str, Any]:
        """What may be disclosed as evidence: identity and digests, never the holdout content itself."""
        return {"profile_id": self.profile_id, "profile_digest": self.digest, "target_file": self.target_file, "expected_digest": self.expected_digest}


def is_independent_evaluator_id(evaluator_id: str, roster_executor_ids: set[str]) -> bool:
    """An executor that implements or reviews can never be admitted as the independent evaluator."""
    return evaluator_id.startswith("evaluator:") and evaluator_id not in roster_executor_ids and not evaluator_id.startswith("executor:")
