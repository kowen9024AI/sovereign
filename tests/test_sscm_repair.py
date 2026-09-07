"""WO-SOVEREIGN-SSCM-01C: bounded repair loop, independent evaluation, candidate custody."""

import json
import time
from pathlib import Path

import pytest

from evaluation import AcceptanceProfile, EvaluationInvalid, EvaluationRequest, EvaluationRun, HoldoutEvaluator, admit_evaluation, validate_evaluation
from evaluation.receipts import verify_ref
from sscm.artifacts import RunDir
from sscm.blackboard import Blackboard, BudgetExceeded, EventIdConflict, MissionBudget
from sscm.contracts import EVENT_V2_SCHEMA_VERSION, new_event, validate_experience
from sscm.executors import MockExecutor
from sscm.parallel import RunEnvelope
from sscm.repair import (
    DISPOSITION_REPAIR_BLOCKED,
    DISPOSITION_REPAIR_QUALIFIED,
    DISPOSITION_REVIEW_FAILED,
    DISPOSITION_TRIGGER_NOT_EXERCISED,
    RepairDogfoodSpec,
    RepairMissionController,
    authorize_repair,
    repair_projection,
)
from sscm.testing import implementer_ok, mock_repair_roster, repair_reviewer_ok
from sscm.workspace import git

EXPECTED = ["PUBLISH", "CLAIM", "COMPLETE", "OBSERVE", "REJECT", "RETRY", "CLAIM", "COMPLETE", "OBSERVE", "OBSERVE", "CLAIM", "COMPLETE", "OBSERVE", "COMPLETE"]


@pytest.fixture
def canon(tmp_path):
    repo = tmp_path / "canon"; repo.mkdir()
    git(["init", "-q", "-b", "main"], cwd=repo); (repo / "README.md").write_text("canonical\n")
    git(["add", "."], cwd=repo); git(["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "base"], cwd=repo)
    return repo


class ForcedEvaluator:
    """Wraps the holdout evaluator and overrides the emitted evaluation for attack/control tests."""
    evaluator_id = "evaluator:sscm-holdout-v0.1"
    evaluator_runtime = "sovereign.deterministic-evaluator"

    def __init__(self, inner, mutate):
        self.inner, self.mutate, self.calls = inner, mutate, 0

    def profile_metadata(self):
        return self.inner.profile_metadata()

    def evaluate(self, request):
        self.calls += 1
        run = self.inner.evaluate(request)
        return self.mutate(self.calls, run) or run


def make(tmp_path, canon, *, evaluator=None, spec_kw=None, checkpoints=None, **roster):
    base = git(["rev-parse", "HEAD"], cwd=canon)
    spec = RepairDogfoodSpec(canonical_checkout=canon, base_sha=base, **(spec_kw or {}))
    executors = mock_repair_roster(spec, **{k: v for k, v in roster.items() if k in ("coordinator", "implementer", "repair_implementer", "reviewer")})
    for role, ex in roster.get("executors", {}).items():
        executors[role] = ex
    ev = evaluator if evaluator is not None else HoldoutEvaluator(spec.profile())
    return spec, RepairMissionController(spec, executors, ev, run_root=tmp_path / "runs", checkpoints=set(checkpoints or ()))


def codes(rep):
    return {b["code"] for b in rep["blockers"]}


# -- §79 happy repair ---------------------------------------------------------------------------------


def test_happy_repair_fail_retry_pass_review(tmp_path, canon):
    spec, ctl = make(tmp_path, canon)
    rep = ctl.execute(cleanup=False)
    assert rep["disposition"] == DISPOSITION_REPAIR_QUALIFIED and rep["mission_terminal_status"] == "SUCCEEDED"
    assert [e["verb"] for e in rep["events"]] == EXPECTED
    evs = rep["repair"]["evaluations"]
    assert [e["disposition"] for e in evs] == ["FAIL", "PASS"] and evs[0]["profile_digest"] == evs[1]["profile_digest"]
    c1 = rep["repair"]["verified_candidate_1"]["verified_candidate_revision"]; c2 = rep["repair"]["verified_candidate_2"]["verified_candidate_revision"]
    assert evs[0]["candidate_ref"] == f"git:{c1}" and evs[1]["candidate_ref"] == f"git:{c2}" and c1 != c2
    assert rep["repair"]["verified_candidate_2"]["base_revision"] == c1  # lineage base -> c1 -> c2
    proj = rep["repair"]["projection"]
    assert proj["repair_attempts_authorized"] == 1 and proj["repair_attempts_consumed"] == 1 and proj["current_candidate_sha"] == c2
    assert sum(1 for e in rep["events"] if e["verb"] == "RETRY") == 1
    # failed first evaluation retained; evaluation objects are real evaluation.v0.1
    for e in evs:
        validate_evaluation(ctl.run.read_json(ctl.run.rel(ctl.run.resolve_ref(e["evaluation_ref"]))))
    assert (Path(rep["run_dir"]) / "artifacts" / "evaluation-1.json").exists()
    exp = rep["experience"]; validate_experience(exp)
    assert exp["outcome"] == "SUCCEEDED" and exp["authority"] == "NONE"
    refs = " ".join(exp["artifact_refs"])
    assert "evaluation-1.json" in refs and "evaluation-2.json" in refs and "repair-instruction.json" in refs and c1 in refs and c2 in refs
    assert rep["promotion_created"] is False and rep["training_artifact_created"] is False
    assert rep["review_verification"]["reviewer_head"] == c2 and rep["review_verification"]["verdict"] == "ACCEPT"
    # sessions: initial != repair; coordinator != reviewer
    s = rep["parallel"]["identity"]["session_refs"]
    assert s["IMPLEMENTER"] != s["REPAIR_IMPLEMENTER"] and s["COORDINATOR"] != s["REVIEWER"]
    lanes = rep["repair"]["lanes"]
    assert len({l["git_common_dir"] for l in lanes.values()}) == 5 and not any(l["is_canonical_checkout"] for l in lanes.values())
    assert rep["credential_scan"]["order"] == "BEFORE_TERMINAL_SUCCESS"
    ctl.ws.destroy()
    assert git(["rev-parse", "HEAD"], cwd=canon) == spec.base_sha and git(["branch", "--list", "dogfood/*"], cwd=canon) == ""


# -- §80 / §56 second failure, no third run -------------------------------------------------------------


def test_second_failure_blocks_no_third_run(tmp_path, canon):
    spec = RepairDogfoodSpec(canonical_checkout=canon, base_sha=git(["rev-parse", "HEAD"], cwd=canon))
    def fail_again(n, run):
        if n == 2:
            run.evaluation["disposition"] = "FAIL"; run.evaluation["findings"] = ["holdout condition still unmet"]
    _, ctl = make(tmp_path, canon, evaluator=ForcedEvaluator(HoldoutEvaluator(spec.profile()), fail_again))
    rep = ctl.execute()
    assert rep["disposition"] == DISPOSITION_REPAIR_BLOCKED and rep["mission_terminal_status"] == "BLOCKED"
    assert [e["disposition"] for e in rep["repair"]["evaluations"]] == ["FAIL", "FAIL"]
    assert sum(1 for e in rep["events"] if e["verb"] == "RETRY") == 1
    assert rep["budget"]["observed_executor_runs"] == 3  # coordinator + 2 implementers, no third
    assert "REVIEWER" not in rep["runs"] and rep["repair"]["second_retry_refused"]["code"] == "BUDGET_EXCEEDED"
    assert "max_repair_loops" in rep["repair"]["second_retry_refused"]["detail"]
    assert rep["experience_created"] is True and rep["experience"]["outcome"] == "BLOCKED"


def test_wrong_repair_content_fails_host_gate_before_evaluation(tmp_path, canon):
    def stubborn(task):
        task.instruction["expected_content"] = {"docs/sscm-repair-dogfood.txt": "STILL_WRONG\n"}
        return implementer_ok(task)
    _, ctl = make(tmp_path, canon, repair_implementer=stubborn)
    rep = ctl.execute()
    assert "CONTENT_MISMATCH" in codes(rep) and [e["disposition"] for e in rep["repair"]["evaluations"]] == ["FAIL"]
    assert rep["budget"]["observed_executor_runs"] == 3 and "REVIEWER" not in rep["runs"]


def test_blackboard_refuses_second_retry_directly(tmp_path, canon):
    _, ctl = make(tmp_path, canon, checkpoints={"candidate2"})
    rep = ctl.execute()
    bb = Blackboard(rep["blackboard_path"]); mid = rep["mission_id"]
    last = bb.events(mid)[-1]
    rej = bb.append(new_event(mission_id=mid, task_id="task:implement", verb="REJECT", actor_ref="evaluator:x", status="FAILED",
                              budget=ctl.spec.budget.event_budget(EVENT_V2_SCHEMA_VERSION), schema_version=EVENT_V2_SCHEMA_VERSION, predecessor_event_ids=[last["event_id"]]))
    with pytest.raises(BudgetExceeded) as ei:
        bb.append(new_event(mission_id=mid, task_id="task:implement", verb="RETRY", actor_ref="host:sscm-mission-controller", status="PENDING",
                            budget=ctl.spec.budget.event_budget(EVENT_V2_SCHEMA_VERSION), schema_version=EVENT_V2_SCHEMA_VERSION, predecessor_event_ids=[rej["event_id"]]))
    assert ei.value.dimension in ("max_repair_loops", "max_consecutive_failures")
    ctl.ws.destroy()


# -- §57-59 controls ------------------------------------------------------------------------------------


def test_pass_first_control_no_retry_direct_to_review(tmp_path, canon):
    _, ctl = make(tmp_path, canon, spec_kw={"expected_content": "SSCM_REPAIR_OK\n"})  # visible == holdout
    rep = ctl.execute()
    assert rep["mission_terminal_status"] == "SUCCEEDED" and rep["disposition"] == DISPOSITION_TRIGGER_NOT_EXERCISED
    verbs = [e["verb"] for e in rep["events"]]
    assert "RETRY" not in verbs and "REPAIR_IMPLEMENTER" not in rep["runs"] and "REVIEWER" in rep["runs"]
    assert [e["disposition"] for e in rep["repair"]["evaluations"]] == ["PASS"]


@pytest.mark.parametrize("disp", ["BLOCKED", "INCONCLUSIVE"])
def test_blocked_and_inconclusive_evaluations_block_without_retry(tmp_path, canon, disp):
    def force(n, run):
        run.evaluation["disposition"] = disp
    spec = RepairDogfoodSpec(canonical_checkout=canon, base_sha=git(["rev-parse", "HEAD"], cwd=canon))
    _, ctl = make(tmp_path, canon, evaluator=ForcedEvaluator(HoldoutEvaluator(spec.profile()), force))
    rep = ctl.execute()
    assert f"EVALUATION_{disp}" in codes(rep) and rep["mission_terminal_status"] == "BLOCKED"
    assert "RETRY" not in [e["verb"] for e in rep["events"]] and rep["budget"]["observed_executor_runs"] == 2


# -- §60-64 attacks ---------------------------------------------------------------------------------------


def test_stale_evaluation_candidate_mismatch(tmp_path, canon):
    def stale(n, run):
        run.evaluation["candidate_ref"] = "git:" + "1" * 40
    spec = RepairDogfoodSpec(canonical_checkout=canon, base_sha=git(["rev-parse", "HEAD"], cwd=canon))
    _, ctl = make(tmp_path, canon, evaluator=ForcedEvaluator(HoldoutEvaluator(spec.profile()), stale))
    rep = ctl.execute()
    assert "EVALUATION_CANDIDATE_MISMATCH" in codes(rep) and "RETRY" not in [e["verb"] for e in rep["events"]]


def test_tampered_evaluation_ref_rejected_by_repair_gate(tmp_path, canon):
    _, ctl = make(tmp_path, canon, checkpoints={"eval1"})
    rep = ctl.execute()
    entry = rep["repair"]["evaluations"][0]
    p = Path(rep["run_dir"]) / "artifacts" / "evaluation-1.json"
    d = json.loads(p.read_text()); d["disposition"] = "FAIL"; d["findings"] = ["tampered"]; p.write_text(json.dumps(d))
    bb = Blackboard(rep["blackboard_path"]); rd = RunDir(rep["mission_id"], tmp_path / "runs")
    with pytest.raises(Exception) as ei:
        authorize_repair(bb=bb, run_dir=rd, mission_id=rep["mission_id"], evaluation_ref=entry["evaluation_ref"], receipt_ref=entry["receipt_ref"],
                         current_candidate_sha=rep["repair"]["verified_candidate_1"]["verified_candidate_revision"], allowed_files=["docs/sscm-repair-dogfood.txt"],
                         repair_envelope=RunEnvelope(10, 200.0, 900.0), elapsed_wall_seconds=1.0)
    assert getattr(ei.value, "blocker_code", "") == "EVALUATION_REF_TAMPERED"
    ctl.ws.destroy()


def test_profile_drift_between_evaluations_blocks(tmp_path, canon):
    spec = RepairDogfoodSpec(canonical_checkout=canon, base_sha=git(["rev-parse", "HEAD"], cwd=canon))
    inner = HoldoutEvaluator(spec.profile())
    def drift(n, run):
        if n == 2:
            run.receipt["profile_digest"] = "0" * 64  # goalposts moved after the failure
    _, ctl = make(tmp_path, canon, evaluator=ForcedEvaluator(inner, drift))
    rep = ctl.execute()
    assert "EVALUATOR_PROFILE_DRIFT" in codes(rep) and rep["mission_terminal_status"] != "SUCCEEDED" and "REVIEWER" not in rep["runs"]


def test_repair_scope_drift_blocks_launch(tmp_path, canon):
    spec = RepairDogfoodSpec(canonical_checkout=canon, base_sha=git(["rev-parse", "HEAD"], cwd=canon))
    def widen(n, run):
        run.receipt["affected_files"] = ["docs/sscm-repair-dogfood.txt", "README.md"]
    _, ctl = make(tmp_path, canon, evaluator=ForcedEvaluator(HoldoutEvaluator(spec.profile()), widen))
    rep = ctl.execute()
    assert "REPAIR_SCOPE_DRIFT" in codes(rep) and "RETRY" not in [e["verb"] for e in rep["events"]] and "REPAIR_IMPLEMENTER" not in rep["runs"]


def test_implementer_self_evaluation_prohibited(tmp_path, canon):
    spec = RepairDogfoodSpec(canonical_checkout=canon, base_sha=git(["rev-parse", "HEAD"], cwd=canon))
    class SelfEval(HoldoutEvaluator):
        evaluator_id = "executor:codex"
    rep = make(tmp_path, canon, evaluator=SelfEval(spec.profile()))[1].execute()
    assert "EVALUATOR_INDEPENDENCE_VIOLATION" in codes(rep) and rep["runs"] == {}
    # and an evaluation *object* claiming a roster executor as evaluator is refused at admission
    run = HoldoutEvaluator(spec.profile()).evaluate  # noqa: F841 - admission tested directly below
    fake = EvaluationRun("executor:mock-codex", "x", "r", 0, 1, {
        "schema_version": "sovereign.evaluation.v0.1", "evaluation_id": "e", "candidate_ref": "git:" + "a" * 40,
        "evaluator": {"evaluator_id": "executor:mock-codex", "independence_class": "SAME_RUNTIME"}, "disposition": "PASS",
        "findings": [], "evidence_refs": ["x"], "evaluated_at": "2026-09-07T00:00:00Z", "authority": "NONE"}, {"profile_digest": "d"})
    with pytest.raises(EvaluationInvalid) as ei:
        admit_evaluation(fake, expected_evaluator_id="evaluator:sscm-holdout-v0.1", expected_candidate_sha="a" * 40, roster_executor_ids={"executor:mock-codex"}, expected_profile_digest=None)
    assert ei.value.code == "EVALUATOR_INDEPENDENCE_VIOLATION"


# -- §81-84 evaluator custody, review required ---------------------------------------------------------------


def test_evaluator_worktree_dirty_or_wrong_sha_rejected(tmp_path, canon):
    repo = tmp_path / "r"; repo.mkdir(); git(["init", "-q", "-b", "main"], cwd=repo)
    (repo / "docs").mkdir(); (repo / "docs/sscm-repair-dogfood.txt").write_text("SSCM_REPAIR_OK\n"); git(["add", "."], cwd=repo)
    git(["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "c"], cwd=repo); sha = git(["rev-parse", "HEAD"], cwd=repo)
    prof = AcceptanceProfile("p", "docs/sscm-repair-dogfood.txt", "SSCM_REPAIR_OK\n"); ev = HoldoutEvaluator(prof)
    (repo / "junk").write_text("x")
    run = ev.evaluate(EvaluationRequest("m", sha, sha, repo, "p", "f", "r1"))
    assert run.evaluation is None and run.error.startswith("EVALUATOR_WORKTREE_DIRTY")
    (repo / "junk").unlink()
    run = ev.evaluate(EvaluationRequest("m", "0" * 40, sha, repo, "p", "f", "r2"))
    assert run.evaluation is None and run.error.startswith("EVALUATION_SHA_MISMATCH")
    run = ev.evaluate(EvaluationRequest("m", sha, sha, repo, "p", "f", "r3"))
    assert run.evaluation["disposition"] == "PASS" and run.evaluation["findings"] == []
    assert (repo / "docs/sscm-repair-dogfood.txt").read_text() == "SSCM_REPAIR_OK\n"  # evaluator never mutated anything


def test_review_required_after_pass(tmp_path, canon):
    _, ctl = make(tmp_path, canon, checkpoints={"candidate2"})
    rep = ctl.execute()
    bb = Blackboard(rep["blackboard_path"]); mid = rep["mission_id"]; eb = ctl.spec.budget.event_budget(EVENT_V2_SCHEMA_VERSION)
    last = bb.events(mid)[-1]  # host OBSERVE candidate 2
    ok = bb.append(new_event(mission_id=mid, task_id="task:implement", verb="OBSERVE", actor_ref="evaluator:sscm-holdout-v0.1", status="SUCCEEDED",
                             candidate_revision=last["candidate_revision"], budget=eb, schema_version=EVENT_V2_SCHEMA_VERSION, predecessor_event_ids=[last["event_id"]]))
    with pytest.raises(Exception) as ei:  # PASS -> terminal COMPLETE without review
        bb.append(new_event(mission_id=mid, task_id=mid, verb="COMPLETE", actor_ref="host:sscm-mission-controller", status="SUCCEEDED",
                            budget=eb, schema_version=EVENT_V2_SCHEMA_VERSION, predecessor_event_ids=[ok["event_id"]]))
    assert getattr(ei.value, "code", "") == "FINAL_REVIEW_REQUIRED"
    ctl.ws.destroy()


def test_reviewer_repair_required_stops_without_second_repair(tmp_path, canon):
    def strict(task):
        r = repair_reviewer_ok(task); r["verdict"] = "REPAIR_REQUIRED"; r["blocking_findings"] = ["style"]; return r
    _, ctl = make(tmp_path, canon, reviewer=strict)
    rep = ctl.execute()
    assert rep["disposition"] == DISPOSITION_REVIEW_FAILED and sum(1 for e in rep["events"] if e["verb"] == "RETRY") == 1
    assert rep["budget"]["observed_executor_runs"] == 4  # no extra Codex run


def test_repair_session_must_be_fresh(tmp_path, canon):
    base = git(["rev-parse", "HEAD"], cwd=canon)
    spec = RepairDogfoodSpec(canonical_checkout=canon, base_sha=base)
    roster = mock_repair_roster(spec)
    roster["IMPLEMENTER"] = MockExecutor("executor:mock-codex", "mock-codex", {"IMPLEMENTER": implementer_ok}, session={"IMPLEMENTER": "codex-T"})
    roster["REPAIR_IMPLEMENTER"] = MockExecutor("executor:mock-codex", "mock-codex", {"REPAIR_IMPLEMENTER": implementer_ok}, session={"REPAIR_IMPLEMENTER": "codex-T"})
    rep = RepairMissionController(spec, roster, HoldoutEvaluator(spec.profile()), run_root=tmp_path / "runs").execute()
    assert "REPAIR_SESSION_NOT_FRESH" in codes(rep) and rep["budget"]["observed_executor_runs"] == 3  # repair usage retained


# -- §74-75 restart safety, idempotent RETRY -----------------------------------------------------------------


@pytest.mark.parametrize("stage,authorized,consumed,last", [("eval1", 0, 0, "FAIL"), ("retry", 1, 0, "FAIL"), ("candidate2", 1, 1, "FAIL")])
def test_restart_reconstructs_repair_custody(tmp_path, canon, stage, authorized, consumed, last):
    _, ctl = make(tmp_path, canon, checkpoints={stage})
    rep = ctl.execute()
    assert rep["checkpoint"] == stage
    before = rep["repair"]["projection"]
    bb = Blackboard(rep["blackboard_path"]); rd = RunDir(rep["mission_id"], tmp_path / "runs")
    after = repair_projection(bb, rd, rep["mission_id"])
    assert after == before
    assert after["repair_attempts_authorized"] == authorized and after["repair_attempts_consumed"] == consumed and after["last_evaluation_disposition"] == last
    if stage == "candidate2":
        assert after["current_candidate_sha"] == rep["repair"]["verified_candidate_2"]["verified_candidate_revision"]
    if stage in ("retry", "candidate2"):
        # no duplicate retry after restart: the same RETRY replays idempotently, a different payload conflicts, a new one exceeds budget
        retry = next(e for e in bb.events(rep["mission_id"]) if e["verb"] == "RETRY")
        assert bb.append(retry) == retry
        with pytest.raises(EventIdConflict):
            bb.append({**retry, "actor_ref": "host:someone-else"})
        with pytest.raises(BudgetExceeded):
            bb.append(new_event(mission_id=rep["mission_id"], task_id="task:implement", verb="RETRY", actor_ref="host:sscm-mission-controller", status="PENDING",
                                budget=ctl.spec.budget.event_budget(EVENT_V2_SCHEMA_VERSION), schema_version=EVENT_V2_SCHEMA_VERSION,
                                predecessor_event_ids=[bb.events(rep["mission_id"])[4]["event_id"]]))
    ctl.ws.destroy()


def test_evaluation_ref_verify_roundtrip(tmp_path):
    rd = RunDir("m", tmp_path)
    ref = rd.write_json("artifacts/evaluation-9.json", {"x": 1}, kind="TEST_RESULT")
    assert verify_ref(rd, ref) == {"x": 1}
    (tmp_path / "m" / "artifacts" / "evaluation-9.json").write_text("{}")
    with pytest.raises(ValueError):
        verify_ref(rd, ref)


def test_profile_digest_is_identity_and_hides_holdout():
    a = AcceptanceProfile("p", "f", "A\n"); b = AcceptanceProfile("p", "f", "B\n")
    assert a.digest != b.digest and "A" not in json.dumps(a.metadata()) and a.metadata()["expected_digest"] != b.metadata()["expected_digest"]
