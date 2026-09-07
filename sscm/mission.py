"""The frozen 01A mission controller. Sequencing authority lives here, not in any model.

    COORDINATOR -> IMPLEMENTER -> HOST VERIFY -> REVIEWER -> HOST VERIFY -> TERMINAL

No recursive spawning, no autonomous repair, no additional actor. Every failure
path fails closed into a BLOCKED terminal mission state with a blocker code.
"""

from __future__ import annotations

import shutil
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping

from .artifacts import CredentialMaterialSuspected, RunDir
from .blackboard import Blackboard, BlackboardError, BudgetExceeded, MissionBudget
from .contracts import (
    CONTRACTS,
    ContractViolation,
    artifact_ref,
    load_schema,
    new_event,
    new_id,
    validate,
)
from .executors import Executor, ExecutorRun, ExecutorTask
from .receipts import mission_to_experience
from .workspace import MissionWorkspaces, WorkspaceError

COORDINATOR_SCHEMA = CONTRACTS / "collaboration" / "sscm-coordinator-instruction.v0.1.schema.json"
IMPLEMENTER_SCHEMA = CONTRACTS / "collaboration" / "sscm-implementer-result.v0.1.schema.json"
REVIEWER_SCHEMA = CONTRACTS / "collaboration" / "sscm-reviewer-result.v0.1.schema.json"

ROLES = ("COORDINATOR", "IMPLEMENTER", "REVIEWER")
HOST_ACTOR = "host:sscm-mission-controller"

DISPOSITION_QUALIFIED = "SOVEREIGN_SSCM_LOCAL_A2A_QUALIFIED"
DISPOSITION_BLOCKED = "SOVEREIGN_SSCM_LOCAL_A2A_BLOCKED"
DISPOSITION_REPAIR = "SSCM_DOGFOOD_REPAIR_REQUIRED"


@dataclass
class DogfoodSpec:
    canonical_checkout: Path
    base_sha: str
    dogfood_branch: str = "dogfood/sscm-01a-implementer"
    target_file: str = "docs/sscm-dogfood.txt"
    expected_content: str = "SOVEREIGN_SSCM_OK\n"
    executor_timeout_seconds: int = 900
    budget: MissionBudget = field(default_factory=lambda: MissionBudget(
        max_active_actors=1, max_coordination_transitions=8, max_turns=24, max_model_calls=24,
        max_wall_seconds=1800, max_repair_loops=0, max_consecutive_failures=1, max_token_or_cost_units=400.0,
        required_observable_dimensions=("executor_turns", "token_units", "wall_seconds")))

    def as_dict(self) -> dict[str, Any]:
        return {
            "canonical_checkout": str(Path(self.canonical_checkout).resolve()),
            "base_sha": self.base_sha,
            "dogfood_branch": self.dogfood_branch,
            "target_file": self.target_file,
            "expected_content": self.expected_content,
            "executor_timeout_seconds": self.executor_timeout_seconds,
            "budget": self.budget.as_dict(),
        }


class MissionAborted(Exception):
    def __init__(self, blocker_code: str, detail: str, disposition: str = DISPOSITION_BLOCKED) -> None:
        super().__init__(f"{blocker_code}: {detail}")
        self.blocker_code = blocker_code
        self.detail = detail
        self.disposition = disposition


class MissionController:
    def __init__(
        self,
        spec: DogfoodSpec,
        executors: Mapping[str, Executor],
        *,
        mission_id: str | None = None,
        run_root: Path | None = None,
        workspace_root: Path | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        missing = [r for r in ROLES if r not in executors]
        if missing:
            raise ValueError(f"executors missing for roles {missing}")
        self.spec = spec
        self.executors = dict(executors)
        self.mission_id = mission_id or new_id("mission")
        self.run = RunDir(self.mission_id, run_root)
        self.bb = Blackboard(self.run.blackboard_path)
        self.ws = MissionWorkspaces(workspace_root or (self.run.root / "workspaces"), spec.canonical_checkout)
        self.clock = clock
        self.started_at = clock()
        self.report: dict[str, Any] = {"mission_id": self.mission_id, "runs": {}, "events": [], "blockers": []}
        self._last_event_id: str | None = None

    # -- helpers -------------------------------------------------------------

    def _eb(self) -> dict[str, Any]:
        return self.spec.budget.event_budget()

    def _elapsed(self) -> float:
        return self.clock() - self.started_at

    def _remaining_seconds(self) -> int:
        remaining = int(self.spec.budget.max_wall_seconds - self._elapsed())
        if remaining <= 0:
            raise MissionAborted("BUDGET_EXCEEDED", "max_wall_seconds exhausted before launching the next executor")
        return max(1, min(remaining, self.spec.executor_timeout_seconds))

    def _append(self, **kw: Any) -> dict[str, Any]:
        ev = new_event(mission_id=self.mission_id, budget=self._eb(), **kw)
        stored = self.bb.append(ev)
        self._last_event_id = stored["event_id"]
        self.report["events"].append({"event_id": stored["event_id"], "verb": stored["verb"], "task_id": stored["task_id"], "actor_ref": stored["actor_ref"], "status": stored["status"]})
        return stored

    def _executor_run(self, role: str, task_id: str, instruction: dict[str, Any], schema_path: Path, cwd: Path,
                      write_allowed: bool, extra_writable: list[Path]) -> tuple[ExecutorRun, dict[str, Any]]:
        # pre-launch: cumulative wall clock and every observed usage dimension must still have headroom
        try:
            self.bb.assert_headroom(self.mission_id, elapsed_wall_seconds=self._elapsed())
        except BudgetExceeded as ex:
            raise MissionAborted("BUDGET_EXCEEDED", f"before {role}: {ex.detail}") from ex
        run_id = new_id(f"run:{role.lower()}")
        task = ExecutorTask(
            role=role, run_id=run_id, cwd=cwd, instruction=instruction, output_schema=load_schema(schema_path),
            timeout_seconds=self._remaining_seconds(), write_allowed=write_allowed,
            extra_writable_dirs=list(extra_writable), log_dir=self.run.path("logs"),
        )
        executor = self.executors[role]
        result = executor.run(task)
        # Mock executors report a synthetic wall time; charge the host-observed one so wall accounting is real.
        usage = result.usage()
        usage["wall_seconds"] = round(max(usage["wall_seconds"], 0.0), 3)
        receipt = self.run.write_json(f"artifacts/{role.lower()}-run.json", result.as_dict(), kind="RECEIPT")
        self.report["runs"][role] = {**result.as_dict(), "receipt": receipt}
        # post-run: charge observed consumption; the usage row is durable even when the charge exceeds the budget
        try:
            self.report["budget"] = self.bb.record_usage(
                self.mission_id, run_id=run_id, role=role, executor_ref=result.executor_id,
                usage=usage, observability=executor.capability().usage_observability)
        except BudgetExceeded as ex:
            self.report["budget"] = self.bb.usage_projection(self.mission_id)
            raise MissionAborted("BUDGET_EXCEEDED", f"after {role}: {ex.detail}") from ex
        except BlackboardError as ex:
            self.report["budget"] = self.bb.usage_projection(self.mission_id)
            raise MissionAborted(ex.code, ex.detail) from ex
        return result, receipt

    def _validate_result(self, role: str, run: ExecutorRun, schema_path: Path, code: str) -> dict[str, Any]:
        if run.error or run.structured_result is None:
            raise MissionAborted(code, run.error or f"{role} produced no structured result (exit {run.exit_code})")
        try:
            validate(run.structured_result, schema_path, f"{code}_SCHEMA")
        except ContractViolation as ex:
            raise MissionAborted(ex.code, ex.detail) from ex
        return run.structured_result

    # -- the frozen sequence -----------------------------------------------------

    def execute(self, cleanup: bool = True) -> dict[str, Any]:
        spec = self.spec
        self.bb.create_mission(self.mission_id, spec.budget, spec.as_dict())
        caps = {role: ex.capability().as_dict() for role, ex in self.executors.items()}
        self.report["capabilities"] = caps
        self.run.write_json("artifacts/executor-capabilities.json", caps)
        try:
            self._gate_roster(caps)
            instruction_ref, publish = self._coordinate()
            impl_wt, claim_impl = self._admit_implementer(instruction_ref, publish)
            complete_impl, claimed = self._implement(impl_wt, instruction_ref, claim_impl)
            verified, observe_impl = self._host_verify_candidate(impl_wt, complete_impl, claimed)
            rev_wt, claim_rev = self._admit_reviewer(verified, observe_impl, impl_wt)
            complete_rev, review = self._review(rev_wt, verified, claim_rev)
            observe_rev = self._host_verify_review(rev_wt, verified, review, complete_rev)
            self._terminal(review, observe_rev)
        except MissionAborted as ex:
            self._block(ex)
        except (BlackboardError, WorkspaceError, ContractViolation, CredentialMaterialSuspected) as ex:
            code = getattr(ex, "code", type(ex).__name__)
            self._block(MissionAborted(code, str(ex)))
        finally:
            self._finish(cleanup)
        return self.report

    def _gate_roster(self, caps: Mapping[str, Mapping[str, Any]]) -> None:
        for role in ROLES:
            c = caps[role]
            if c["availability"] != "AVAILABLE" or not c["native_auth_ready"]:
                raise MissionAborted("EXECUTOR_UNAVAILABLE", f"{role}: {c['executor_id']} availability={c['availability']} auth_ready={c['native_auth_ready']}")
            if c["authority"] != "NONE":
                raise MissionAborted("AUTHORITY_NOT_NONE", f"{role} executor claims authority {c['authority']}")
        if caps["IMPLEMENTER"]["runtime_family"] == caps["REVIEWER"]["runtime_family"]:
            raise MissionAborted("RUNTIME_NOT_DISTINCT", "implementer and reviewer share a runtime family")
        # every required budget dimension must be runtime-observable on every roster executor (fail closed)
        for role in ROLES:
            obs = caps[role].get("usage_observability", {})
            for dim in self.spec.budget.required_observable_dimensions:
                if obs.get(dim) != "OBSERVABLE":
                    raise MissionAborted("BUDGET_DIMENSION_UNOBSERVABLE",
                                         f"{role} executor {caps[role]['executor_id']} cannot report required budget dimension {dim}")

    def _coordinate(self) -> tuple[dict[str, Any], dict[str, Any]]:
        spec = self.spec
        task_id = "task:implement"
        instruction_in = {
            "mission_id": self.mission_id,
            "task_id": task_id,
            "role": "COORDINATOR",
            "mission_objective": f"Produce a bounded implementation instruction for an IMPLEMENTER: create the file {spec.target_file} containing exactly the line {spec.expected_content.strip()} (with one trailing newline) and commit it on the implementer's isolated branch. No other file may change.",
            "required_fields": {"schema_version": "sovereign.sscm-coordinator-instruction.v0.1", "role": "COORDINATOR", "target_role": "IMPLEMENTER", "allowed_files": [spec.target_file], "expected_content": {spec.target_file: spec.expected_content}, "commit_required": True, "authority": "NONE"},
            "constraints": ["Do not run tools.", "Do not widen allowed_files.", "verification_requirements must describe host-side Git checks (changed file set, exact content, clean worktree, descent from base)."],
        }
        # The coordinator has no tools and must not inherit any repository context: run it from a
        # neutral empty directory outside every checkout (a CLI may auto-collect git status of its cwd).
        neutral = Path(tempfile.mkdtemp(prefix="sscm-coordinator-"))
        try:
            run, receipt = self._executor_run("COORDINATOR", task_id, instruction_in, COORDINATOR_SCHEMA, neutral, False, [])
        finally:
            shutil.rmtree(neutral, ignore_errors=True)
        result = self._validate_result("COORDINATOR", run, COORDINATOR_SCHEMA, "COORDINATOR_RESULT_INVALID")
        if result["mission_id"] != self.mission_id or result["task_id"] != task_id:
            raise MissionAborted("COORDINATOR_RESULT_INVALID", "mission/task identity mismatch")
        if sorted(result["allowed_files"]) != [spec.target_file] or result["expected_content"] != {spec.target_file: spec.expected_content}:
            raise MissionAborted("COORDINATOR_SCOPE_DRIFT", "instruction widened or altered the frozen dogfood scope")
        instruction_ref = self.run.write_json("artifacts/coordinator-instruction.json", result)
        publish = self._append(
            task_id=task_id, verb="PUBLISH", actor_ref="actor:coordinator", executor_ref=run.executor_id, session_ref=run.session_ref,
            target_role="IMPLEMENTER", base_revision=spec.base_sha, output_refs=[instruction_ref, receipt], status="PENDING",
            expected_output_schema="sovereign.sscm-implementer-result.v0.1",
        )
        return instruction_ref, publish

    def _admit_implementer(self, instruction_ref: dict[str, Any], publish: dict[str, Any]) -> tuple[Path, dict[str, Any]]:
        spec = self.spec
        self.ws.create_mirror(spec.base_sha)
        wt = self.ws.add_branch_worktree("implementer", spec.dogfood_branch, spec.base_sha)
        facts = self.ws.admission_gate(wt, other_writable=[])
        self.report["implementer_workspace"] = facts.as_dict()
        self.run.write_json("artifacts/implementer-workspace-admission.json", facts.as_dict())
        claim = self._append(
            task_id="task:implement", verb="CLAIM", actor_ref="actor:implementer", executor_ref=self.executors["IMPLEMENTER"].capability().executor_id,
            target_role="IMPLEMENTER", workspace_ref=facts.realpath, base_revision=spec.base_sha, input_refs=[instruction_ref],
            status="ACTIVE", predecessor_event_id=publish["event_id"],
        )
        return wt, claim

    def _implement(self, wt: Path, instruction_ref: dict[str, Any], claim: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
        spec = self.spec
        coordinator_instruction = self.run.read_json(self.run.rel(self.run.resolve_ref(instruction_ref)))
        instruction = {
            "mission_id": self.mission_id, "task_id": "task:implement", "role": "IMPLEMENTER",
            "coordinator_instruction": coordinator_instruction,
            "base_revision": spec.base_sha, "worktree": str(wt), "branch": spec.dogfood_branch,
            "allowed_files": [spec.target_file], "expected_content": {spec.target_file: spec.expected_content},
            "required_commit": {"message": "dogfood: SSCM artifact-bound handoff", "must_commit": True, "must_not_push": True, "must_leave_worktree_clean": True},
            "forbidden": ["editing any other file", "creating branches", "pushing", "modifying git config", "leaving untracked files"],
            "result_note": "Report the commit SHA you observe from `git rev-parse HEAD`; the host re-verifies it independently.",
        }
        run, receipt = self._executor_run("IMPLEMENTER", "task:implement", instruction, IMPLEMENTER_SCHEMA, wt, True, [self.ws.mirror])
        status = "FAILED"
        claimed: str | None = None
        result: dict[str, Any] | None = None
        try:
            result = self._validate_result("IMPLEMENTER", run, IMPLEMENTER_SCHEMA, "IMPLEMENTER_RESULT_INVALID")
            status = "SUCCEEDED" if result["status"] == "SUCCEEDED" else "FAILED"
            claimed = result.get("claimed_candidate_revision")
        except MissionAborted as ex:
            self.report["blockers"].append({"stage": "IMPLEMENTER", "code": ex.blocker_code, "detail": ex.detail})
        result_ref = self.run.write_json("artifacts/implementer-result.json", result if result is not None else {"unparsed": True, "exit_code": run.exit_code, "error": run.error})
        complete = self._append(
            task_id="task:implement", verb="COMPLETE", actor_ref="actor:implementer", executor_ref=run.executor_id, session_ref=run.session_ref,
            workspace_ref=claim["workspace_ref"], base_revision=spec.base_sha, candidate_revision=claimed, input_refs=[instruction_ref],
            output_refs=[result_ref, receipt], status=status, predecessor_event_id=claim["event_id"],
            blocker_code=None if status == "SUCCEEDED" else "IMPLEMENTER_FAILED",
        )
        if status != "SUCCEEDED":
            raise MissionAborted("IMPLEMENTER_FAILED", (result or {}).get("summary") or run.error or "implementer did not succeed")
        return complete, claimed

    def _host_verify_candidate(self, wt: Path, complete: dict[str, Any], claimed: str | None) -> tuple[str, dict[str, Any]]:
        spec = self.spec
        verification = self.ws.verify_candidate(wt, spec.base_sha, {spec.target_file: spec.expected_content})
        verified = verification["verified_candidate_revision"]
        # exact object-id equality; a non-null contradictory claim fails closed (no prefix/abbreviation logic)
        if claimed is not None and claimed != verified:
            raise MissionAborted("CANDIDATE_CLAIM_MISMATCH", f"implementer claimed {claimed}, host observed {verified}")
        verification["claimed_candidate_revision"] = claimed
        ref = self.run.write_json("artifacts/host-candidate-verification.json", verification)
        self.report["candidate_verification"] = verification
        observe = self._append(
            task_id="task:implement", verb="OBSERVE", actor_ref=HOST_ACTOR, workspace_ref=complete["workspace_ref"],
            base_revision=spec.base_sha, candidate_revision=verified, output_refs=[ref, artifact_ref("REVISION", f"git:{verified}")],
            status="SUCCEEDED", predecessor_event_id=complete["event_id"],
        )
        return verified, observe

    def _admit_reviewer(self, verified: str, observe: dict[str, Any], impl_wt: Path) -> tuple[Path, dict[str, Any]]:
        wt = self.ws.add_detached_worktree("reviewer", verified)
        facts = self.ws.admission_gate(wt, other_writable=[impl_wt])
        if facts.head_sha != verified:
            raise MissionAborted("REVIEW_SHA_MISMATCH", f"reviewer worktree at {facts.head_sha}, expected {verified}")
        self.report["reviewer_workspace"] = facts.as_dict()
        self.run.write_json("artifacts/reviewer-workspace-admission.json", facts.as_dict())
        claim = self._append(
            task_id="task:review", verb="CLAIM", actor_ref="actor:reviewer", executor_ref=self.executors["REVIEWER"].capability().executor_id,
            target_role="REVIEWER", workspace_ref=facts.realpath, base_revision=self.spec.base_sha, candidate_revision=verified,
            input_refs=list(observe["output_refs"]), status="ACTIVE", predecessor_event_id=observe["event_id"],
        )
        return wt, claim

    def _review(self, wt: Path, verified: str, claim: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any] | None]:
        spec = self.spec
        instruction = {
            "mission_id": self.mission_id, "task_id": "task:review", "role": "REVIEWER",
            "base_revision": spec.base_sha, "verified_candidate_revision": verified, "review_worktree": str(wt),
            "expected_changed_files": [spec.target_file], "expected_content": {spec.target_file: spec.expected_content},
            "checklist": [
                "git rev-parse HEAD equals verified_candidate_revision",
                "git merge-base --is-ancestor <base> HEAD succeeds",
                "git diff --name-only <base>..HEAD lists exactly the expected changed files",
                "git show HEAD:<file> equals expected content byte-for-byte (one trailing newline)",
                "git status --porcelain is empty (no residue)",
            ],
            "rules": ["Read-only. Do not modify, commit, or repair anything.", "Return REPAIR_REQUIRED with blocking_findings if any check fails."],
        }
        run, receipt = self._executor_run("REVIEWER", "task:review", instruction, REVIEWER_SCHEMA, wt, False, [])
        review: dict[str, Any] | None = None
        status = "FAILED"
        try:
            review = self._validate_result("REVIEWER", run, REVIEWER_SCHEMA, "REVIEWER_RESULT_INVALID")
            status = "SUCCEEDED"
        except MissionAborted as ex:
            self.report["blockers"].append({"stage": "REVIEWER", "code": ex.blocker_code, "detail": ex.detail})
        review_ref = self.run.write_json("artifacts/reviewer-result.json", review if review is not None else {"unparsed": True, "exit_code": run.exit_code, "error": run.error}, kind="REVIEW")
        complete = self._append(
            task_id="task:review", verb="COMPLETE", actor_ref="actor:reviewer", executor_ref=run.executor_id, session_ref=run.session_ref,
            workspace_ref=claim["workspace_ref"], base_revision=spec.base_sha, candidate_revision=verified, input_refs=list(claim["input_refs"]),
            output_refs=[review_ref, receipt], status=status, predecessor_event_id=claim["event_id"],
            blocker_code=None if status == "SUCCEEDED" else "REVIEWER_FAILED",
        )
        if review is None:
            raise MissionAborted("REVIEWER_FAILED", run.error or "reviewer produced no valid result")
        return complete, review

    def _host_verify_review(self, wt: Path, verified: str, review: dict[str, Any], complete: dict[str, Any]) -> dict[str, Any]:
        host = self.ws.verify_reviewer(wt, verified)  # raises REVIEW_SHA_MISMATCH
        if review["reviewed_revision"] != verified:  # exact equality, never prefix
            raise MissionAborted("REVIEW_SHA_MISMATCH", f"reviewer reported {review['reviewed_revision']}, host verified {verified}")
        host["reviewed_revision_matches"] = True
        host["verdict"] = review["verdict"]
        host["blocking_findings"] = len(review["blocking_findings"])
        ref = self.run.write_json("artifacts/host-review-verification.json", host)
        self.report["review_verification"] = host
        return self._append(
            task_id="task:review", verb="OBSERVE", actor_ref=HOST_ACTOR, workspace_ref=complete["workspace_ref"],
            base_revision=self.spec.base_sha, candidate_revision=verified, output_refs=[ref], status="SUCCEEDED",
            predecessor_event_id=complete["event_id"],
        )

    def _terminal(self, review: dict[str, Any], observe: dict[str, Any]) -> None:
        if review["verdict"] != "ACCEPT" or review["blocking_findings"]:
            raise MissionAborted("REVIEW_REPAIR_REQUIRED", "; ".join(review["blocking_findings"]) or "reviewer verdict REPAIR_REQUIRED", DISPOSITION_REPAIR)
        proj = self.bb.project(self.mission_id)
        required = {"PUBLISH": 1, "CLAIM": 2, "COMPLETE": 2, "OBSERVE": 2}
        for verb, n in required.items():
            if proj.verbs.get(verb, 0) < n:
                raise MissionAborted("EVENT_CHAIN_INCOMPLETE", f"expected >= {n} {verb}, saw {proj.verbs.get(verb, 0)}")
        usage = self.bb.usage_projection(self.mission_id)
        if usage["budget_exceeded"]:
            raise MissionAborted("BUDGET_EXCEEDED", f"dimensions over limit at terminalization: {usage['budget_exceeded']}")
        # Acceptance gate: every durable artifact AND provider stdout/stderr log written so far is scanned
        # before terminal success exists anywhere. Not CLEAN => no SUCCEEDED event, no SUCCEEDED experience.
        scanned = self.run.scan_all()  # raises CredentialMaterialSuspected
        self.report["credential_scan"] = {"files_scanned": len(scanned), "result": "CLEAN", "order": "BEFORE_TERMINAL_SUCCESS"}
        self._append(task_id=self.mission_id, verb="COMPLETE", actor_ref=HOST_ACTOR, candidate_revision=observe["candidate_revision"],
                     base_revision=self.spec.base_sha, input_refs=list(observe["output_refs"]), status="SUCCEEDED",
                     predecessor_event_id=observe["event_id"])
        self.report["disposition"] = DISPOSITION_QUALIFIED

    def _block(self, ex: MissionAborted) -> None:
        self.report["blockers"].append({"stage": "MISSION", "code": ex.blocker_code, "detail": ex.detail})
        self.report["disposition"] = ex.disposition
        try:
            proj = self.bb.project(self.mission_id)
            if proj.status not in ("SUCCEEDED", "FAILED", "CANCELLED"):
                self._append(task_id=self.mission_id, verb="REJECT", actor_ref=HOST_ACTOR, status="BLOCKED", blocker_code=ex.blocker_code,
                             predecessor_event_id=self._last_event_id)
        except BlackboardError as inner:  # blocked twice; record, never raise past the controller
            self.report["blockers"].append({"stage": "MISSION", "code": inner.code, "detail": inner.detail})

    def _finish(self, cleanup: bool) -> None:
        proj = self.bb.project(self.mission_id)
        self.report["projection"] = proj.as_dict()
        self.report["mission_terminal_status"] = proj.status
        self.report["budget"] = self.bb.usage_projection(self.mission_id)
        events = self.bb.events(self.mission_id)
        self.report["evaluation_created"] = False
        self.report["promotion_created"] = False
        self.report["training_artifact_created"] = False
        self.report["experience_created"] = False
        self.report["blackboard_path"] = str(self.run.blackboard_path)
        self.report["run_dir"] = str(self.run.root)
        # Final gate over everything durable (artifacts + provider logs). A SUCCEEDED mission already passed this
        # gate before its terminal event; a BLOCKED/FAILED mission may still yield an observed experience, but only
        # from evidence that itself scans CLEAN (section 18).
        try:
            scanned = self.run.scan_all()
            prior = self.report.get("credential_scan", {})
            self.report["credential_scan"] = {"files_scanned": len(scanned), "result": "CLEAN",
                                              "order": prior.get("order", "BEFORE_EXPERIENCE")}
            clean = True
        except CredentialMaterialSuspected as ex:
            self.report["credential_scan"] = {"result": "REJECTED", "detail": str(ex), "order": "BEFORE_EXPERIENCE"}
            self.report["disposition"] = DISPOSITION_BLOCKED
            self.report["blockers"].append({"stage": "EVIDENCE", "code": ex.code, "detail": str(ex)})
            clean = False
        if clean and proj.status in ("SUCCEEDED", "BLOCKED", "FAILED", "CANCELLED"):
            refs = [r for e in events for r in e.get("output_refs", [])]
            exp = mission_to_experience(proj, events, started_at=self.started_at, completed_at=self.clock(),
                                        executors_used=[c["executor_id"] for c in self.report.get("capabilities", {}).values()], artifact_refs=refs)
            self.report["experience"] = exp
            self.report["experience_ref"] = self.run.write_json("artifacts/experience.json", exp, kind="EXPERIENCE")
            self.report["experience_created"] = True
        if cleanup:
            self.report["cleanup"] = self.ws.destroy()
        self.report.setdefault("disposition", DISPOSITION_BLOCKED)
        self.run.write_json("report.json", self.report, scan=False)
        self.bb.close()
