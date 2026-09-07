"""Digest-bound evaluation artifacts in a mission run directory."""

from __future__ import annotations

from typing import Any

from sscm.artifacts import RunDir
from sscm.contracts import artifact_ref, file_sha256

from .evaluator import EvaluationRun


def write_evaluation(run_dir: RunDir, attempt: int, run: EvaluationRun) -> tuple[dict[str, Any], dict[str, Any]]:
    """Persist the evaluation.v0.1 object and the evaluator receipt; both scanned, both digest-bound."""
    ev_ref = run_dir.write_json(f"artifacts/evaluation-{attempt}.json", run.evaluation, kind="TEST_RESULT")
    rc_ref = run_dir.write_json(f"artifacts/evaluation-{attempt}-receipt.json", run.as_dict(), kind="RECEIPT")
    return ev_ref, rc_ref


def verify_ref(run_dir: RunDir, ref: dict[str, Any]) -> dict[str, Any]:
    """Re-read an evaluation by ref and require the digest to match. Never trust a path alone."""
    p = run_dir.resolve_ref(ref)
    if not p.is_file() or file_sha256(p) != ref.get("sha256"):
        raise ValueError(f"EVALUATION_REF_TAMPERED: {ref['ref']} digest mismatch or missing")
    return run_dir.read_json(run_dir.rel(p))
