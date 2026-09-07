"""Sovereign independent evaluation (v0.1 binding).

Produces frozen ``evaluation.v0.1`` objects about exact host-verified Git revisions. An evaluator may reject;
it may not repair, authorize retry, promote, or hold authority. ``authority = NONE`` always.

* ``contracts``      - evaluation.v0.1 validation, profile digests, evaluator identity rules
* ``evaluator``      - provider-neutral evaluator interface + admission checks on returned evaluations
* ``deterministic``  - the reference holdout evaluator (no model, read-only, exact revision)
* ``receipts``       - digest-bound evaluation artifacts and evidence refs
"""

from .contracts import EVALUATION_SCHEMA_VERSION, AcceptanceProfile, EvaluationInvalid, validate_evaluation
from .deterministic import HOLDOUT_EVALUATOR_ID, HOLDOUT_RUNTIME, HoldoutEvaluator
from .evaluator import EvaluationRequest, EvaluationRun, Evaluator, admit_evaluation

__all__ = [
    "EVALUATION_SCHEMA_VERSION", "AcceptanceProfile", "EvaluationInvalid", "validate_evaluation",
    "HOLDOUT_EVALUATOR_ID", "HOLDOUT_RUNTIME", "HoldoutEvaluator",
    "EvaluationRequest", "EvaluationRun", "Evaluator", "admit_evaluation",
]
