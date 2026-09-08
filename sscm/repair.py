"""SSCM-01C: single bounded repair loop with independent evaluation.

Frozen sequence (a model never decides any transition):

    PUBLISH -> CLAIM impl-1 -> COMPLETE -> OBSERVE host cand-1 -> evaluator (REJECT FAIL | OBSERVE PASS)
    FAIL:  host repair gate -> RETRY (1/1) -> CLAIM impl-2 (fresh run/session, lane based on cand-1) -> COMPLETE
           -> OBSERVE host cand-2 -> evaluator (OBSERVE PASS | REJECT FAIL -> BLOCKED, no second RETRY)
    PASS:  CLAIM reviewer -> COMPLETE -> OBSERVE host review -> scan -> COMPLETE mission (FINAL_REVIEW_REQUIRED)

EVALUATION_FAIL != REPAIR_AUTHORITY. The evaluator's FAIL makes a repair *eligible*; only the host-owned RETRY
event, admitted by host policy and the blackboard's max_repair_loops, authorizes it.
"""

from __future__ import annotations

import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from evaluation import AcceptanceProfile, EvaluationInvalid, EvaluationRequest, Evaluator, admit_evaluation
from evaluation.receipts import verify_ref, write_evaluation

from .artifacts import CredentialMaterialSuspected, RunDir
from .blackboard import Blackboard, BlackboardError, BudgetExceeded, MissionBudget
from .contracts import CONTRACTS, ContractViolation, artifact_ref, load_schema, new_id, validate
from .executors import Executor, ExecutorRun
from .mission import COORDINATOR_SCHEMA, DISPOSITION_BLOCKED, HOST_ACTOR, IMPLEMENTER_SCHEMA, REVIEWER_SCHEMA, DogfoodSpec, MissionAborted, MissionController
from .parallel import ReservedV2RunsMixin, RunEnvelope
from .workspace import RepoLane, WorkspaceError, WorkspaceFacts, assert_isolated

REPAIR_INSTRUCTION_SCHEMA = CONTRACTS / "collaboration" / "sscm-repair-instruction.v0.1.schema.json"
REPAIR_ROLES = ("COORDINATOR", "IMPLEMENTER", "REPAIR_IMPLEMENTER", "REVIEWER")

DISPOSITION_REPAIR_QUALIFIED = "SOVEREIGN_SSCM_BOUNDED_REPAIR_EVALUATION_QUALIFIED"
DISPOSITION_REPAIR_BLOCKED = "SOVEREIGN_SSCM_REPAIR_BLOCKED"
DISPOSITION_TRIGGER_NOT_EXERCISED = "SSCM_REPAIR_TRIGGER_NOT_EXERCISED"
DISPOSITION_REVIEW_FAILED = "SSCM_REPAIR_DOGFOOD_REVIEW_FAILED"

TASK_IMPLEMENT = "task:implement"
TASK_REVIEW = "task:review"


class Checkpoint(Exception):
    """Test-only stop: leave blackboard/run-dir state exactly as it is at a named stage."""


@dataclass
class RepairDogfoodSpec(DogfoodSpec):
    target_file: str = "docs/sscm-repair-dogfood.txt"
    expected_content: str = "SSCM_REPAIR_SEED\n"          # visible to the coordinator/implementer
    holdout_content: str = "SSCM_REPAIR_OK\n"             # held by the evaluator only
    dogfood_branch: str = "dogfood/sscm-01c-implementer-1"
    repair_branch: str = "dogfood/sscm-01c-repair"
    profile_id: str = "sscm-repair-dogfood.v0.1"
    envelopes: dict[str, RunEnvelope] = field(default_factory=lambda: {
        "COORDINATOR": RunEnvelope(6, 60.0, 600.0),
        "IMPLEMENTER": RunEnvelope(10, 200.0, 900.0),
        "REPAIR_IMPLEMENTER": RunEnvelope(10, 200.0, 900.0),   # measured 01A/01B Codex runs: 1 turn, ~85-106 kilotokens
        "REVIEWER": RunEnvelope(16, 300.0, 900.0),
    })
    budget: MissionBudget = field(default_factory=lambda: MissionBudget(
        max_active_actors=1, max_coordination_transitions=14, max_turns=48, max_model_calls=48,
        max_wall_seconds=1800, max_repair_loops=1, max_consecutive_failures=2, max_token_or_cost_units=800.0,
        required_observable_dimensions=("executor_turns", "token_units", "wall_seconds")))
    repair_policy: dict[str, Any] = field(default_factory=lambda: {
        "authority_actor_ref": HOST_ACTOR,
        "trigger_actor_ref": "evaluator:sscm-holdout-v0.1",
        "trigger_verb": "REJECT",
        "trigger_status": "FAILED",
    })
    required_terminal_verifications: list[dict[str, Any]] = field(default_factory=lambda: [
        {
            "task_id": TASK_REVIEW,
            "verb": "OBSERVE",
            "status": "SUCCEEDED",
            "actor_ref": HOST_ACTOR,
            "bind_terminal_candidate": True,
        }
    ])

    def profile(self) -> AcceptanceProfile:
        return AcceptanceProfile(self.profile_id, self.target_file, self.holdout_content, "frozen holdout acceptance for SSCM-01C")

    def as_dict(self) -> dict[str, Any]:
        d = super().as_dict()
        # coordination state discloses the profile identity and digest, never the holdout content
        d["evaluation_profile"] = {"profile_id": self.profile_id, "profile_digest": self.profile().digest}
        d["envelopes"] = {k: v.as_dict() for k, v in self.envelopes.items()}
        d["repair_policy"] = dict(self.repair_policy)
        d["required_terminal_verifications"] = [dict(v) for v in self.required_terminal_verifications]
        d["required_terminal_tasks"] = [TASK_REVIEW]
        return d


# ---------------------------------------------------------------------------------------------
# repair custody projection (evidence, never authority)
# ---------------------------------------------------------------------------------------------


def repair_projection(bb: Blackboard, run_dir: RunDir, mission_id: str) -> dict[str, Any]:
    events = bb.events(mission_id)
    history: list[dict[str, Any]] = []
    current: str | None = None
    authorized = consumed = 0
    m = bb.mission(mission_id)
    spec = m.get("spec", {})
    repair_policy = spec.get("repair_policy", {})
    auth_actor = repair_policy.get("authority_actor_ref")
    exp_verb = repair_policy.get("trigger_verb", "REJECT")
    exp_status = repair_policy.get("trigger_status", "FAILED")
    exp_trigger_actor = repair_policy.get("trigger_actor_ref")

    for e in events:
        if e["verb"] == "OBSERVE" and str(e["actor_ref"]).startswith("host:") and e["task_id"] == TASK_IMPLEMENT and e.get("candidate_revision"):
            current = e["candidate_revision"]
        if e["verb"] == "RETRY":
            is_authorized = False
            if auth_actor and e.get("actor_ref") == auth_actor:
                preds = bb.predecessors_of(e["event_id"])
                if len(preds) == 1:
                    p_ev = bb.get_event(preds[0])
                    if p_ev:
                        if p_ev.get("verb") == exp_verb and p_ev.get("status") == exp_status:
                            if not exp_trigger_actor or p_ev.get("actor_ref") == exp_trigger_actor:
                                if e.get("candidate_revision") == p_ev.get("candidate_revision"):
                                    is_authorized = True
            if is_authorized:
                authorized += 1
        if e["verb"] == "CLAIM" and e["task_id"] == TASK_IMPLEMENT and any(bb.get_event(p)["verb"] == "RETRY" for p in bb.predecessors_of(e["event_id"])):
            consumed += 1
        if str(e["actor_ref"]).startswith("evaluator:"):
            for ref in e.get("output_refs", []):
                if ref["kind"] == "TEST_RESULT":
                    p = run_dir.resolve_ref(ref)
                    if p.is_file():
                        ev = run_dir.read_json(run_dir.rel(p))
                        history.append({"evaluation_id": ev["evaluation_id"], "candidate_ref": ev["candidate_ref"], "disposition": ev["disposition"],
                                        "event_id": e["event_id"], "ref": ref})
    last = history[-1] if history else None
    return {
        "current_candidate_sha": current,
        "repair_attempts_authorized": authorized,
        "repair_attempts_consumed": consumed,
        "last_evaluation_id": last["evaluation_id"] if last else None,
        "last_evaluation_disposition": last["disposition"] if last else None,
        "evaluation_history": history,
        "authority": "NONE",
    }


def authorize_repair(*, bb: Blackboard, run_dir: RunDir, mission_id: str, evaluation_ref: Mapping[str, Any], receipt_ref: Mapping[str, Any],
                     current_candidate_sha: str, allowed_files: list[str], repair_envelope: RunEnvelope, elapsed_wall_seconds: float) -> dict[str, Any]:
    """Host repair policy (WO §24). Returns the re-read, digest-verified evaluation; raises MissionAborted otherwise.

    Nothing in an evaluation grants retry: this gate does, and only under every condition below.
    """
    try:
        ev = verify_ref(run_dir, evaluation_ref)      # EVALUATION_REF_TAMPERED on digest mismatch
        rc = verify_ref(run_dir, receipt_ref)
    except ValueError as ex:
        raise MissionAborted("EVALUATION_REF_TAMPERED", str(ex), DISPOSITION_REPAIR_BLOCKED) from ex
    if ev["disposition"] != "FAIL":
        raise MissionAborted("REPAIR_NOT_ELIGIBLE", f"evaluation disposition {ev['disposition']} does not make a candidate repair-eligible", DISPOSITION_REPAIR_BLOCKED)
    if ev["candidate_ref"] != f"git:{current_candidate_sha}":
        raise MissionAborted("EVALUATION_CANDIDATE_MISMATCH", f"evaluation binds {ev['candidate_ref']}, current candidate git:{current_candidate_sha}", DISPOSITION_REPAIR_BLOCKED)
    proj = repair_projection(bb, run_dir, mission_id)
    budget = bb.budget(mission_id)
    if proj["repair_attempts_authorized"] >= budget.max_repair_loops:
        raise MissionAborted("BUDGET_EXCEEDED", f"max_repair_loops {budget.max_repair_loops} already authorized ({proj['repair_attempts_authorized']})", DISPOSITION_REPAIR_BLOCKED)
    affected = rc.get("receipt", {}).get("affected_files", [])
    outside = sorted(set(affected) - set(allowed_files))
    if outside:
        raise MissionAborted("REPAIR_SCOPE_DRIFT", f"evaluation findings touch files outside the allowed set: {outside}", DISPOSITION_REPAIR_BLOCKED)
    try:
        bb.assert_headroom(mission_id, elapsed_wall_seconds=elapsed_wall_seconds)
    except BudgetExceeded as ex:
        raise MissionAborted("BUDGET_EXCEEDED", f"no headroom for repair: {ex.detail}", DISPOSITION_REPAIR_BLOCKED) from ex
    usage = bb.usage_projection(mission_id)
    if usage["observed_executor_turns"] + repair_envelope.executor_turns > budget.max_turns or usage["observed_cost_or_token_units"] + repair_envelope.usage_units > budget.max_usage_units:
        raise MissionAborted("BUDGET_EXCEEDED", "remaining mission budget cannot cover the frozen repair envelope", DISPOSITION_REPAIR_BLOCKED)
    scan_result = run_dir.scan_all()  # evaluation evidence + everything durable so far must be CLEAN before repair becomes actionable
    return {"evaluation": ev, "receipt": rc, "scanned_files": len(scan_result)}


# ---------------------------------------------------------------------------------------------
# controller
# ---------------------------------------------------------------------------------------------


class RepairMissionController(ReservedV2RunsMixin, MissionController):
    ROLES = REPAIR_ROLES

    def __init__(self, spec: RepairDogfoodSpec, executors: Mapping[str, Executor], evaluator: Evaluator, *,
                 checkpoints: set[str] | None = None, **kw: Any) -> None:
        missing = [r for r in REPAIR_ROLES if r not in executors]
        if missing:
            raise ValueError(f"executors missing for roles {missing}")
        super().__init__(spec, dict(executors), **kw)
        self.spec: RepairDogfoodSpec = spec
        self.evaluator = evaluator
        if "trigger_actor_ref" in self.spec.repair_policy and self.spec.repair_policy["trigger_actor_ref"] == "evaluator:sscm-holdout-v0.1":
            self.spec.repair_policy["trigger_actor_ref"] = self.evaluator.evaluator_id
        self.checkpoints = set(checkpoints or ())
        self.report["repair"] = {"evaluations": [], "lanes": {}}
        self._lane_facts: dict[str, WorkspaceFacts] = {}
        self._profile_digest: str | None = None

    # -- helpers ---------------------------------------------------------------------------------

    def _checkpoint(self, name: str) -> None:
        if name in self.checkpoints:
            raise Checkpoint(name)

    def _new_lane(self, name: str, *, import_from: RepoLane | None = None, sha: str | None = None) -> RepoLane:
        lane = self.ws.lane(name)
        lane.create_mirror(self.spec.base_sha)
        if import_from is not None and sha is not None:
            lane.import_exact(import_from.mirror, sha, name)
        return lane

    def _admit_lane(self, role_label: str, wt: Path, lane: RepoLane, expect_head: str | None = None) -> WorkspaceFacts:
        facts = lane.facts(wt)
        if expect_head and facts.head_sha != expect_head:
            raise MissionAborted("EVALUATION_SHA_MISMATCH" if "evaluator" in role_label else "REVIEW_SHA_MISMATCH",
                                 f"{role_label} worktree at {facts.head_sha}, expected {expect_head}", DISPOSITION_REPAIR_BLOCKED)
        self._lane_facts[role_label] = facts
        assert_isolated(self._lane_facts)  # every lane distinct from every other (realpath, object store, canonical, dirty)
        self.report["repair"]["lanes"][role_label] = facts.as_dict()
        return facts

    def _run_role(self, role: str, wave: str, instruction: dict[str, Any], schema: Path, cwd: Path, write: bool, extra: list[Path]) -> ExecutorRun:
        run_ids = self._reserve_wave(wave, [role])
        task = self._task_for(role, run_ids[role], instruction, schema, cwd, write, extra)
        result = self.executors[role].run(task)
        self._settle(role, result)            # executed usage first (01B-R1 Part B)
        self._record_identity(role, result)
        self._require_session(role, result)
        return result

    # -- the frozen sequence -----------------------------------------------------------------------

    def execute(self, cleanup: bool = True) -> dict[str, Any]:
        spec = self.spec
        self.bb.create_mission(self.mission_id, spec.budget, spec.as_dict())
        caps = {role: self.executors[role].capability().as_dict() for role in REPAIR_ROLES}
        self.report["capabilities"] = caps
        self.run.write_json("artifacts/executor-capabilities.json", caps)
        self.report["repair"]["evaluator"] = {"evaluator_id": self.evaluator.evaluator_id, "evaluator_runtime": self.evaluator.evaluator_runtime, **self.evaluator.profile_metadata()}
        try:
            self._gate_repair_roster(caps)
            instruction_ref, publish = self._coordinate_v2()
            lane1, wt1, claim1 = self._admit_initial_lane(instruction_ref, publish)
            observe1, cand1 = self._implement_lane("IMPLEMENTER", lane1, wt1, claim1, instruction_ref, base=spec.base_sha, expected=spec.expected_content)
            eval1_event, eval1 = self._evaluate(1, cand1, lane1, observe1)
            self._checkpoint("eval1")
            if eval1["disposition"] == "FAIL":
                self.report["repair"]["repair_exercised"] = True
                retry, repair_ref = self._authorize_and_instruct(eval1_event, cand1)
                self._checkpoint("retry")
                lane2, wt2, claim2 = self._admit_repair_lane(lane1, cand1, retry, repair_ref)
                observe2, cand2 = self._implement_lane("REPAIR_IMPLEMENTER", lane2, wt2, claim2, repair_ref, base=cand1, expected=spec.holdout_content, repair=True)
                self._checkpoint("candidate2")
                eval2_event, eval2 = self._evaluate(2, cand2, lane2, observe2)
                if eval2["disposition"] != "PASS":
                    # host policy: the single repair loop is consumed; a second RETRY is refused, mission blocks
                    self._refuse_second_retry(eval2_event, cand2)
                final_lane, final, final_event = lane2, cand2, eval2_event
            else:
                self.report["repair"]["repair_exercised"] = False
                final_lane, final, final_event = lane1, cand1, eval1_event
            rev_wt, claim_rev = self._admit_reviewer_lane(final_lane, final, final_event)
            complete_rev, review = self._review_final(rev_wt, final, claim_rev)
            observe_rev = self._host_verify_review_lane(rev_wt, final, review, complete_rev)
            self._terminal(review, observe_rev)
            exercised = self.report["repair"].get("repair_exercised", True)
            self.report["disposition"] = DISPOSITION_REPAIR_QUALIFIED if exercised else DISPOSITION_TRIGGER_NOT_EXERCISED
        except Checkpoint as cp:
            self.report["checkpoint"] = str(cp)
            self.report["blackboard_path"] = str(self.run.blackboard_path)
            self.report["run_dir"] = str(self.run.root)
            self.report["repair"]["projection"] = repair_projection(self.bb, self.run, self.mission_id)
            self.run.write_json("report.json", self.report, scan=False)
            self.bb.close()
            return self.report
        except MissionAborted as ex:
            if ex.disposition == "SSCM_DOGFOOD_REPAIR_REQUIRED":
                ex.disposition = DISPOSITION_REVIEW_FAILED
            elif ex.disposition == DISPOSITION_BLOCKED:
                ex.disposition = DISPOSITION_REPAIR_BLOCKED
            self._block(ex)
        except (BlackboardError, WorkspaceError, ContractViolation, EvaluationInvalid, CredentialMaterialSuspected) as ex:
            self._block(MissionAborted(getattr(ex, "code", type(ex).__name__), str(ex), DISPOSITION_REPAIR_BLOCKED))
        finally:
            if "checkpoint" not in self.report:
                self.report["repair"]["projection"] = repair_projection(self.bb, self.run, self.mission_id)
                self._finish(cleanup)
                if self.report.get("disposition") == DISPOSITION_BLOCKED:
                    self.report["disposition"] = DISPOSITION_REPAIR_BLOCKED
        return self.report

    def _gate_repair_roster(self, caps: Mapping[str, Mapping[str, Any]]) -> None:
        for role in REPAIR_ROLES:
            c = caps[role]
            if c["availability"] != "AVAILABLE" or not c["native_auth_ready"]:
                raise MissionAborted("EXECUTOR_UNAVAILABLE", f"{role}: {c['executor_id']}")
            if c["authority"] != "NONE":
                raise MissionAborted("AUTHORITY_NOT_NONE", f"{role} executor claims authority {c['authority']}")
            if not c.get("session_identity_support"):
                raise MissionAborted("SESSION_IDENTITY_UNOBSERVABLE", f"{role} cannot surface a session identity")
            for dim in self.spec.budget.required_observable_dimensions:
                if c.get("usage_observability", {}).get(dim) != "OBSERVABLE":
                    raise MissionAborted("BUDGET_DIMENSION_UNOBSERVABLE", f"{role} cannot report {dim}")
        for w in ("IMPLEMENTER", "REPAIR_IMPLEMENTER"):
            if caps[w]["runtime_family"] == caps["REVIEWER"]["runtime_family"]:
                raise MissionAborted("RUNTIME_NOT_DISTINCT", f"{w} and REVIEWER share a runtime family")
        roster_ids = {c["executor_id"] for c in caps.values()}
        eid = self.evaluator.evaluator_id
        if not eid.startswith("evaluator:") or eid in roster_ids or eid.startswith("executor:"):
            raise MissionAborted("EVALUATOR_INDEPENDENCE_VIOLATION", f"evaluator {eid} is not independent of the roster")
        env = self.spec.envelopes
        if sum(e.executor_turns for e in env.values()) > self.spec.budget.max_turns or sum(e.usage_units for e in env.values()) > self.spec.budget.max_usage_units:
            raise MissionAborted("BUDGET_EXCEEDED", "frozen run envelopes exceed the mission budget")

    def _coordinate_v2(self) -> tuple[dict[str, Any], dict[str, Any]]:
        spec = self.spec
        instruction_in = {
            "mission_id": self.mission_id, "task_id": TASK_IMPLEMENT, "role": "COORDINATOR",
            "mission_objective": f"Produce a bounded implementation instruction for an IMPLEMENTER: create the file {spec.target_file} containing exactly the line {spec.expected_content.strip()} (with one trailing newline) and commit it on the implementer's isolated branch. No other file may change.",
            "required_fields": {"schema_version": "sovereign.sscm-coordinator-instruction.v0.1", "role": "COORDINATOR", "target_role": "IMPLEMENTER",
                                "allowed_files": [spec.target_file], "expected_content": {spec.target_file: spec.expected_content}, "commit_required": True, "authority": "NONE"},
            "constraints": ["Do not run tools.", "Do not widen allowed_files.", "verification_requirements must describe host-side Git checks."],
        }
        neutral = Path(tempfile.mkdtemp(prefix="sscm-coordinator-"))
        try:
            run = self._run_role("COORDINATOR", "wave-coordinator", instruction_in, COORDINATOR_SCHEMA, neutral, False, [])
        finally:
            shutil.rmtree(neutral, ignore_errors=True)
        result = self._validate_result("COORDINATOR", run, COORDINATOR_SCHEMA, "COORDINATOR_RESULT_INVALID")
        if result["mission_id"] != self.mission_id or result["task_id"] != TASK_IMPLEMENT:
            raise MissionAborted("COORDINATOR_RESULT_INVALID", "mission/task identity mismatch")
        if sorted(result["allowed_files"]) != [spec.target_file] or result["expected_content"] != {spec.target_file: spec.expected_content}:
            raise MissionAborted("COORDINATOR_SCOPE_DRIFT", "instruction widened or altered the frozen scope")
        ref = self.run.write_json("artifacts/coordinator-instruction.json", result)
        publish = self._append(task_id=TASK_IMPLEMENT, verb="PUBLISH", actor_ref="actor:coordinator", executor_ref=run.executor_id, session_ref=run.session_ref,
                               target_role="IMPLEMENTER", base_revision=spec.base_sha, output_refs=[ref, self.report["runs"]["COORDINATOR"]["receipt"]],
                               status="PENDING", expected_output_schema="sovereign.sscm-implementer-result.v0.1")
        return ref, publish

    def _admit_initial_lane(self, instruction_ref, publish):
        lane = self._new_lane("implementer-1")
        wt = lane.add_branch_worktree("implementer-1-wt", self.spec.dogfood_branch, self.spec.base_sha)
        facts = self._admit_lane("IMPLEMENTER", wt, lane)
        claim = self._append(task_id=TASK_IMPLEMENT, verb="CLAIM", actor_ref="actor:implementer-1", executor_ref=self.executors["IMPLEMENTER"].capability().executor_id,
                             target_role="IMPLEMENTER", workspace_ref=facts.realpath, base_revision=self.spec.base_sha, input_refs=[instruction_ref],
                             status="ACTIVE", predecessor_event_ids=[publish["event_id"]])
        return lane, wt, claim

    def _implement_lane(self, role: str, lane: RepoLane, wt: Path, claim, instruction_ref, *, base: str, expected: str, repair: bool = False):
        spec = self.spec
        handoff = self.run.read_json(self.run.rel(self.run.resolve_ref(instruction_ref)))
        instruction = {
            "mission_id": self.mission_id, "task_id": TASK_IMPLEMENT, "role": "IMPLEMENTER",
            ("repair_instruction" if repair else "coordinator_instruction"): handoff,
            "base_revision": base, "worktree": str(wt), "branch": spec.repair_branch if repair else spec.dogfood_branch,
            "allowed_files": [spec.target_file], "expected_content": {spec.target_file: expected},
            "required_commit": {"message": "dogfood: SSCM repair candidate 2" if repair else "dogfood: SSCM repair candidate 1", "must_commit": True, "must_not_push": True, "must_leave_worktree_clean": True},
            "forbidden": ["editing any other file", "creating branches", "pushing", "modifying git config", "leaving untracked files"],
            "result_note": "Report the full 40-hex commit SHA from `git rev-parse HEAD`; the host re-verifies it independently.",
        }
        run = self._run_role(role, f"wave-{role.lower()}", instruction, IMPLEMENTER_SCHEMA, wt, True, [lane.mirror])
        if repair:
            initial = self.report["parallel"]["identity"]["session_refs"].get("IMPLEMENTER")
            if run.session_ref == initial:
                raise MissionAborted("REPAIR_SESSION_NOT_FRESH", "repair run reused the initial implementer session", DISPOSITION_REPAIR_BLOCKED)
        result, status, claimed = None, "FAILED", None
        try:
            result = self._validate_result(role, run, IMPLEMENTER_SCHEMA, f"{role}_RESULT_INVALID")
            status = "SUCCEEDED" if result["status"] == "SUCCEEDED" else "FAILED"
            claimed = result.get("claimed_candidate_revision")
        except MissionAborted as ex:
            self.report["blockers"].append({"stage": role, "code": ex.blocker_code, "detail": ex.detail})
        result_ref = self.run.write_json(f"artifacts/{role.lower()}-result.json", result if result is not None else {"unparsed": True, "exit_code": run.exit_code, "error": run.error})
        complete = self._append(task_id=TASK_IMPLEMENT, verb="COMPLETE", actor_ref=f"actor:{role.lower()}", executor_ref=run.executor_id, session_ref=run.session_ref,
                                workspace_ref=claim["workspace_ref"], base_revision=base, candidate_revision=claimed, input_refs=[instruction_ref],
                                output_refs=[result_ref, self.report["runs"][role]["receipt"]], status=status, predecessor_event_ids=[claim["event_id"]],
                                blocker_code=None if status == "SUCCEEDED" else f"{role}_FAILED")
        if status != "SUCCEEDED":
            raise MissionAborted(f"{role}_FAILED", (result or {}).get("summary") or run.error or "implementer did not succeed", DISPOSITION_REPAIR_BLOCKED)
        verification = lane.verify_candidate(wt, base, {spec.target_file: expected})
        verified = verification["verified_candidate_revision"]
        if claimed is not None and claimed != verified:
            raise MissionAborted("CANDIDATE_CLAIM_MISMATCH", f"{role} claimed {claimed}, host observed {verified}", DISPOSITION_REPAIR_BLOCKED)
        if repair and verified == base:
            raise MissionAborted("REPAIR_NO_CHANGE", "candidate 2 equals candidate 1", DISPOSITION_REPAIR_BLOCKED)
        verification["claimed_candidate_revision"] = claimed
        ref = self.run.write_json(f"artifacts/host-verification-{'candidate-2' if repair else 'candidate-1'}.json", verification)
        self.report["repair"]["verified_candidate_2" if repair else "verified_candidate_1"] = verification
        observe = self._append(task_id=TASK_IMPLEMENT, verb="OBSERVE", actor_ref=HOST_ACTOR, workspace_ref=claim["workspace_ref"], base_revision=base,
                               candidate_revision=verified, output_refs=[ref, artifact_ref("REVISION", f"git:{verified}")], status="SUCCEEDED",
                               predecessor_event_ids=[complete["event_id"]])
        return observe, verified

    def _evaluate(self, attempt: int, candidate: str, source: RepoLane, predecessor) -> tuple[dict[str, Any], dict[str, Any]]:
        lane = self._new_lane(f"evaluator-{attempt}", import_from=source, sha=candidate)
        wt = lane.add_detached_worktree(f"evaluator-{attempt}-wt", candidate)
        self._admit_lane(f"evaluator-{attempt}", wt, lane, expect_head=candidate)
        req = EvaluationRequest(self.mission_id, candidate, self.spec.base_sha, wt, self.spec.profile_id,
                                f"sscm:acceptance-profile:{self.spec.profile_id}", new_id("evalrun"))
        run = self.evaluator.evaluate(req)
        roster_ids = {c["executor_id"] for c in self.report["capabilities"].values()}
        try:
            evaluation = admit_evaluation(run, expected_evaluator_id=self.evaluator.evaluator_id, expected_candidate_sha=candidate,
                                          roster_executor_ids=roster_ids, expected_profile_digest=self._profile_digest)
        except EvaluationInvalid as ex:
            code = ex.code
            if run.error and run.error.startswith(("EVALUATION_SHA_MISMATCH", "EVALUATOR_WORKTREE_DIRTY")):
                code = run.error.split(":")[0]
            raise MissionAborted(code, ex.detail if not run.error else run.error, DISPOSITION_REPAIR_BLOCKED) from ex
        self._profile_digest = run.receipt["profile_digest"]
        ev_ref, rc_ref = write_evaluation(self.run, attempt, run)   # scanned + digest-bound before any transition
        entry = {"attempt": attempt, "evaluation_id": evaluation["evaluation_id"], "candidate_ref": evaluation["candidate_ref"],
                 "disposition": evaluation["disposition"], "findings": evaluation["findings"], "evaluation_ref": ev_ref, "receipt_ref": rc_ref,
                 "evaluator_run_id": run.evaluation_run_id, "profile_digest": run.receipt["profile_digest"]}
        self.report["repair"]["evaluations"].append(entry)
        actor = self.evaluator.evaluator_id
        if evaluation["disposition"] == "PASS":
            event = self._append(task_id=TASK_IMPLEMENT, verb="OBSERVE", actor_ref=actor, candidate_revision=candidate, base_revision=self.spec.base_sha,
                                 input_refs=[artifact_ref("REVISION", f"git:{candidate}")], output_refs=[ev_ref, rc_ref], status="SUCCEEDED",
                                 predecessor_event_ids=[predecessor["event_id"]])
        else:
            event = self._append(task_id=TASK_IMPLEMENT, verb="REJECT", actor_ref=actor, candidate_revision=candidate, base_revision=self.spec.base_sha,
                                 input_refs=[artifact_ref("REVISION", f"git:{candidate}")], output_refs=[ev_ref, rc_ref], status="FAILED",
                                 blocker_code=f"EVALUATION_{evaluation['disposition']}", predecessor_event_ids=[predecessor["event_id"]])
            if evaluation["disposition"] in ("BLOCKED", "INCONCLUSIVE"):
                raise MissionAborted(f"EVALUATION_{evaluation['disposition']}", "evaluation did not fail deterministically; no repair is authorized for uncertainty",
                                     DISPOSITION_REPAIR_BLOCKED)
        return event, evaluation

    def _authorize_and_instruct(self, reject_event, cand1: str):
        entry = self.report["repair"]["evaluations"][-1]
        decision = authorize_repair(bb=self.bb, run_dir=self.run, mission_id=self.mission_id, evaluation_ref=entry["evaluation_ref"], receipt_ref=entry["receipt_ref"],
                                    current_candidate_sha=cand1, allowed_files=[self.spec.target_file], repair_envelope=self.spec.envelopes["REPAIR_IMPLEMENTER"],
                                    elapsed_wall_seconds=self._elapsed())
        instruction = {
            "schema_version": "sovereign.sscm-repair-instruction.v0.1", "mission_id": self.mission_id, "task_id": TASK_IMPLEMENT, "repair_attempt": 1,
            "base_candidate_sha": cand1,
            "evaluation_ref": {"evaluation_id": entry["evaluation_id"], "artifact_ref": entry["evaluation_ref"]["ref"], "sha256": entry["evaluation_ref"]["sha256"], "disposition": "FAIL"},
            "allowed_files": [self.spec.target_file],
            "required_changes": [{"file": self.spec.target_file, "expected_content": self.spec.holdout_content, "finding": f} for f in entry["findings"]],
            "verification_requirements": ["candidate 2 descends from candidate 1", f"diff candidate1..candidate2 touches only {self.spec.target_file}", "content byte-exact", "worktree clean"],
            "commit_required": True, "authority": "NONE",
        }
        validate(instruction, REPAIR_INSTRUCTION_SCHEMA, "REPAIR_INSTRUCTION_INVALID")
        ref = self.run.write_json("artifacts/repair-instruction.json", instruction)
        self.report["repair"]["repair_instruction_ref"] = ref
        # the host-owned RETRY is the authorization; it consumes max_repair_loops at the blackboard and carries the instruction
        try:
            retry = self._append(task_id=TASK_IMPLEMENT, verb="RETRY", actor_ref=HOST_ACTOR, candidate_revision=cand1, base_revision=self.spec.base_sha,
                                 input_refs=[entry["evaluation_ref"]], output_refs=[ref], status="PENDING", predecessor_event_ids=[reject_event["event_id"]])
        except BudgetExceeded as ex:
            raise MissionAborted("BUDGET_EXCEEDED", ex.detail, DISPOSITION_REPAIR_BLOCKED) from ex
        self.report["repair"]["repair_authorization"] = {"event_id": retry["event_id"], "authorized_by": HOST_ACTOR, "evaluation_id": entry["evaluation_id"], "scanned_files": decision["scanned_files"]}
        return retry, ref

    def _refuse_second_retry(self, reject_event, cand2: str) -> None:
        entry = self.report["repair"]["evaluations"][-1]
        try:
            authorize_repair(bb=self.bb, run_dir=self.run, mission_id=self.mission_id, evaluation_ref=entry["evaluation_ref"], receipt_ref=entry["receipt_ref"],
                             current_candidate_sha=cand2, allowed_files=[self.spec.target_file], repair_envelope=self.spec.envelopes["REPAIR_IMPLEMENTER"],
                             elapsed_wall_seconds=self._elapsed())
        except MissionAborted as ex:
            self.report["repair"]["second_retry_refused"] = {"code": ex.blocker_code, "detail": ex.detail}
            raise
        raise MissionAborted("BUDGET_EXCEEDED", "second repair must never be authorized", DISPOSITION_REPAIR_BLOCKED)

    def _admit_repair_lane(self, lane1: RepoLane, cand1: str, retry, repair_ref):
        lane = self._new_lane("repair", import_from=lane1, sha=cand1)
        wt = lane.add_branch_worktree("repair-wt", self.spec.repair_branch, cand1)
        facts = self._admit_lane("REPAIR_IMPLEMENTER", wt, lane, expect_head=cand1)
        claim = self._append(task_id=TASK_IMPLEMENT, verb="CLAIM", actor_ref="actor:implementer-2", executor_ref=self.executors["REPAIR_IMPLEMENTER"].capability().executor_id,
                             target_role="REPAIR_IMPLEMENTER", workspace_ref=facts.realpath, base_revision=cand1, input_refs=[repair_ref],
                             status="ACTIVE", predecessor_event_ids=[retry["event_id"]])
        return lane, wt, claim

    def _admit_reviewer_lane(self, source: RepoLane, final: str, final_event):
        lane = self._new_lane("reviewer", import_from=source, sha=final)
        wt = lane.add_detached_worktree("reviewer-wt", final)
        facts = self._admit_lane("REVIEWER", wt, lane, expect_head=final)
        self.report["reviewer_workspace"] = facts.as_dict()
        run_ids = self._reserve_wave("wave-reviewer", ["REVIEWER"])
        claim = self._append(task_id=TASK_REVIEW, verb="CLAIM", actor_ref="actor:reviewer", executor_ref=self.executors["REVIEWER"].capability().executor_id,
                             target_role="REVIEWER", workspace_ref=facts.realpath, base_revision=self.spec.base_sha, candidate_revision=final,
                             input_refs=list(final_event["output_refs"]), status="ACTIVE", predecessor_event_ids=[final_event["event_id"]])
        claim["_run_id"] = run_ids["REVIEWER"]
        return wt, claim

    def _review_final(self, wt: Path, final: str, claim):
        rp = self.report["repair"]
        evs = rp["evaluations"]
        instruction = {
            "mission_id": self.mission_id, "task_id": TASK_REVIEW, "role": "REVIEWER",
            "canonical_base_revision": self.spec.base_sha,
            "candidate_1_revision": rp["verified_candidate_1"]["verified_candidate_revision"],
            "evaluation_1": {k: evs[0][k] for k in ("evaluation_id", "candidate_ref", "disposition", "findings")},
            "repair_authorization": rp.get("repair_authorization"),
            "repair_instruction_ref": rp.get("repair_instruction_ref"),
            "candidate_2_revision": rp.get("verified_candidate_2", {}).get("verified_candidate_revision"),
            "evaluation_2": ({k: evs[1][k] for k in ("evaluation_id", "candidate_ref", "disposition", "findings")} if len(evs) > 1 else None),
            "verified_final_revision": final, "review_worktree": str(wt),
            "expected_changed_files": [self.spec.target_file], "expected_content": {self.spec.target_file: self.spec.holdout_content},
            "checklist": [
                "git rev-parse HEAD equals verified_final_revision",
                "candidate_1 descends from canonical base; candidate_2 (if any) descends from candidate_1",
                "evaluation_1 bound candidate_1 and its disposition is as stated; exactly one repair authorization exists when a repair occurred",
                "evaluation_2 (if any) bound candidate_2 with disposition PASS",
                "git diff --name-only <base>..HEAD lists exactly the expected file; content byte-exact; git status --porcelain empty",
            ],
            "rules": ["Read-only. Do not modify, commit, or repair anything.", "Return REPAIR_REQUIRED with blocking_findings if any check fails."],
        }
        task = self._task_for("REVIEWER", claim["_run_id"], instruction, REVIEWER_SCHEMA, wt, False, [])
        run = self.executors["REVIEWER"].run(task)
        self._settle("REVIEWER", run)
        self._record_identity("REVIEWER", run)
        self._require_session("REVIEWER", run)
        if run.session_ref == self.report["parallel"]["identity"]["session_refs"].get("COORDINATOR"):
            raise MissionAborted("REVIEW_SESSION_NOT_INDEPENDENT", "reviewer reused the coordinator session", DISPOSITION_REPAIR_BLOCKED)
        review, status = None, "FAILED"
        try:
            review = self._validate_result("REVIEWER", run, REVIEWER_SCHEMA, "REVIEWER_RESULT_INVALID")
            status = "SUCCEEDED"
        except MissionAborted as ex:
            self.report["blockers"].append({"stage": "REVIEWER", "code": ex.blocker_code, "detail": ex.detail})
        review_ref = self.run.write_json("artifacts/reviewer-result.json", review if review is not None else {"unparsed": True, "exit_code": run.exit_code, "error": run.error}, kind="REVIEW")
        complete = self._append(task_id=TASK_REVIEW, verb="COMPLETE", actor_ref="actor:reviewer", executor_ref=run.executor_id, session_ref=run.session_ref,
                                workspace_ref=claim["workspace_ref"], base_revision=self.spec.base_sha, candidate_revision=final, input_refs=list(claim["input_refs"]),
                                output_refs=[review_ref, self.report["runs"]["REVIEWER"]["receipt"]], status=status, predecessor_event_ids=[claim["event_id"]],
                                blocker_code=None if status == "SUCCEEDED" else "REVIEWER_FAILED")
        if review is None:
            raise MissionAborted("REVIEWER_FAILED", run.error or "reviewer produced no valid result", DISPOSITION_REPAIR_BLOCKED)
        return complete, review

    def _host_verify_review_lane(self, wt: Path, final: str, review, complete):
        host = self.ws.lane("reviewer").verify_reviewer(wt, final)
        if review["reviewed_revision"] != final:
            raise MissionAborted("REVIEW_SHA_MISMATCH", f"reviewer reported {review['reviewed_revision']}, host verified {final}", DISPOSITION_REPAIR_BLOCKED)
        host.update({"reviewed_revision_matches": True, "verdict": review["verdict"], "blocking_findings": len(review["blocking_findings"])})
        ref = self.run.write_json("artifacts/host-review-verification.json", host)
        self.report["review_verification"] = host
        return self._append(task_id=TASK_REVIEW, verb="OBSERVE", actor_ref=HOST_ACTOR, workspace_ref=complete["workspace_ref"], base_revision=self.spec.base_sha,
                            candidate_revision=final, output_refs=[ref], status="SUCCEEDED", predecessor_event_ids=[complete["event_id"]])

    def _terminal(self, review, observe) -> None:
        if review["verdict"] != "ACCEPT" or review["blocking_findings"]:
            raise MissionAborted("REVIEW_REPAIR_REQUIRED", "; ".join(review["blocking_findings"]) or "reviewer verdict REPAIR_REQUIRED", DISPOSITION_REVIEW_FAILED)
        super()._terminal(review, observe)
