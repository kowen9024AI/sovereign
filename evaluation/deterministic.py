"""Reference deterministic holdout evaluator: ``evaluator:sscm-holdout-v0.1``.

Evaluates exactly one host-verified revision in a detached, read-only worktree. It invokes no model, writes
nothing, and reads the candidate file from the Git object (``git show HEAD:<file>``), never the working tree.
"""

from __future__ import annotations

import hashlib
import subprocess
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .contracts import EVALUATION_SCHEMA_VERSION, AcceptanceProfile, candidate_ref, validate_evaluation
from .evaluator import EvaluationRequest, EvaluationRun

HOLDOUT_EVALUATOR_ID = "evaluator:sscm-holdout-v0.1"
HOLDOUT_RUNTIME = "sovereign.deterministic-evaluator"


def _git(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True)


class HoldoutEvaluator:
    evaluator_id = HOLDOUT_EVALUATOR_ID
    evaluator_runtime = HOLDOUT_RUNTIME

    def __init__(self, profile: AcceptanceProfile) -> None:
        self.profile = profile

    def profile_metadata(self) -> dict[str, Any]:
        return self.profile.metadata()

    def evaluate(self, request: EvaluationRequest) -> EvaluationRun:
        t0 = time.time()
        wt = Path(request.worktree)
        receipt: dict[str, Any] = {**self.profile.metadata(), "evaluator_runtime": self.evaluator_runtime,
                                   "evaluation_run_id": request.evaluation_run_id, "affected_files": [self.profile.target_file]}
        head = _git(["rev-parse", "HEAD"], wt).stdout.strip()
        receipt["worktree_head"] = head
        if head != request.candidate_sha:
            return EvaluationRun(self.evaluator_id, self.evaluator_runtime, request.evaluation_run_id, t0, time.time(), None, receipt,
                                 f"EVALUATION_SHA_MISMATCH: worktree HEAD {head} != requested candidate {request.candidate_sha}")
        status = _git(["status", "--porcelain", "--untracked-files=all"], wt).stdout
        if status.strip():
            return EvaluationRun(self.evaluator_id, self.evaluator_runtime, request.evaluation_run_id, t0, time.time(), None, receipt,
                                 "EVALUATOR_WORKTREE_DIRTY: evaluator worktree is not clean")
        shown = _git(["show", f"HEAD:{self.profile.target_file}"], wt)
        if shown.returncode != 0:
            observed: str | None = None
        else:
            observed = shown.stdout
        observed_digest = hashlib.sha256((observed or "").encode("utf-8")).hexdigest() if observed is not None else None
        receipt["observed_digest"] = observed_digest
        findings: list[str] = []
        if observed is None:
            findings.append(f"missing file {self.profile.target_file}")
        elif observed != self.profile.expected_content:
            findings.append(f"exact-content mismatch for {self.profile.target_file}: observed sha256 {observed_digest[:12]} != expected sha256 {self.profile.expected_digest[:12]}")
        disposition = "PASS" if not findings else "FAIL"
        evaluation = {
            "schema_version": EVALUATION_SCHEMA_VERSION,
            "evaluation_id": f"eval:{uuid.uuid4()}",
            "candidate_ref": candidate_ref(request.candidate_sha),
            "evaluator": {"evaluator_id": self.evaluator_id, "independence_class": "INDEPENDENT"},
            "disposition": disposition,
            "findings": findings,
            "evidence_refs": [
                f"sscm:evaluator-worktree-head:{head}",
                f"sscm:acceptance-profile:{self.profile.profile_id}:sha256:{self.profile.digest}",
                f"sscm:observed-file:{self.profile.target_file}:sha256:{observed_digest or 'missing'}",
                f"sscm:expected-condition:sha256:{self.profile.expected_digest}",
                f"sscm:evaluation-run:{request.evaluation_run_id}:runtime:{self.evaluator_runtime}",
                f"sscm:evaluation-result:{disposition}",
            ],
            "evaluated_at": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "authority": "NONE",
        }
        validate_evaluation(evaluation)
        return EvaluationRun(self.evaluator_id, self.evaluator_runtime, request.evaluation_run_id, t0, time.time(), evaluation, receipt)
