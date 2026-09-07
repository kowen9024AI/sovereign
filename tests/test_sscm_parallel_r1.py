"""WO-SOVEREIGN-SSCM-01B-R1: session identity custody, evidence-first post-wave ordering."""

import pytest

from sscm.blackboard import Blackboard
from sscm.executors import MockExecutor
from sscm.parallel import DISPOSITION_PARALLEL_BLOCKED, DISPOSITION_PARALLEL_QUALIFIED, ParallelDogfoodSpec, ParallelMissionController
from sscm.testing import fanout_coordinator_ok, integrated_reviewer_ok, mock_parallel_roster, worker_ok
from sscm.workspace import git


@pytest.fixture
def canon(tmp_path):
    repo = tmp_path / "canon"; repo.mkdir()
    git(["init", "-q", "-b", "main"], cwd=repo); (repo / "README.md").write_text("canonical\n")
    git(["add", "."], cwd=repo); git(["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "base"], cwd=repo)
    return repo


def run(tmp_path, canon, roster_fn):
    base = git(["rev-parse", "HEAD"], cwd=canon)
    spec = ParallelDogfoodSpec(canonical_checkout=canon, base_sha=base)
    roster = mock_parallel_roster(spec)
    roster_fn(roster)
    ctl = ParallelMissionController(spec, roster, run_root=tmp_path / "runs")
    rep = ctl.execute(cleanup=False)
    bb = Blackboard(rep["blackboard_path"])
    usage = bb.usage_projection(rep["mission_id"]); res = bb.reservations(rep["mission_id"])
    ctl.ws.destroy()
    return rep, usage, res


def _codes(rep):
    return {b["code"] for b in rep["blockers"]}


def _workers_with_sessions(a, b):
    def fn(roster):
        roster["WORKER_A"] = MockExecutor("executor:mock-codex", "mock-codex", {"WORKER_A": worker_ok}, session={"WORKER_A": a})
        roster["WORKER_B"] = MockExecutor("executor:mock-codex", "mock-codex", {"WORKER_B": worker_ok}, session={"WORKER_B": b})
    return fn


# -- Part A: session identity ------------------------------------------------------------------


@pytest.mark.parametrize("role", ["COORDINATOR", "WORKER_A", "WORKER_B", "REVIEWER"])
def test_executor_without_session_identity_support_fails_before_launch(tmp_path, canon, role):
    fns = {"COORDINATOR": fanout_coordinator_ok, "WORKER_A": worker_ok, "WORKER_B": worker_ok, "REVIEWER": integrated_reviewer_ok}
    fam = "mock-claude" if role in ("COORDINATOR", "REVIEWER") else "mock-codex"
    def fn(roster):
        roster[role] = MockExecutor(f"executor:{fam}", fam, {role: fns[role]}, session_identity_support=False)
    rep, usage, _ = run(tmp_path, canon, fn)
    assert "SESSION_IDENTITY_UNOBSERVABLE" in _codes(rep) and rep["runs"] == {} and usage["observed_executor_runs"] == 0


@pytest.mark.parametrize("a,b,code", [
    (None, None, "SESSION_IDENTITY_MISSING"),
    ("thread-X", None, "SESSION_IDENTITY_MISSING"),
    (None, "thread-Y", "SESSION_IDENTITY_MISSING"),
    ("", "thread-Y", "SESSION_IDENTITY_MISSING"),
    ("thread-X", "thread-X", "SESSION_COLLAPSE"),
])
def test_worker_session_gates_block_after_both_usages_settled(tmp_path, canon, a, b, code):
    rep, usage, res = run(tmp_path, canon, _workers_with_sessions(a, b))
    assert rep["disposition"] == DISPOSITION_PARALLEL_BLOCKED and rep["mission_terminal_status"] == "BLOCKED"
    assert code in _codes(rep)
    # evidence-first: both executed lanes left usage rows and settled reservations before the gate fired
    assert usage["observed_executor_runs"] == 3  # coordinator + both workers
    assert "WORKER_A" in rep["runs"] and "WORKER_B" in rep["runs"]
    assert res["states"]["res:wave-workers:worker_a"] == "SETTLED" and res["states"]["res:wave-workers:worker_b"] == "SETTLED"
    assert "integration" not in rep["parallel"] and "REVIEWER" not in rep["runs"]
    # both lanes still recorded their COMPLETE events
    assert sum(1 for e in rep["events"] if e["verb"] == "COMPLETE") == 2


def test_distinct_worker_sessions_pass(tmp_path, canon):
    rep, _, _ = run(tmp_path, canon, _workers_with_sessions("thread-X", "thread-Y"))
    assert rep["disposition"] == DISPOSITION_PARALLEL_QUALIFIED
    ids = rep["parallel"]["identity"]
    assert ids["session_refs"]["WORKER_A"] == "thread-X" and ids["session_refs"]["WORKER_B"] == "thread-Y"


def test_reviewer_reusing_coordinator_session_is_not_independent(tmp_path, canon):
    def fn(roster):
        roster["COORDINATOR"] = MockExecutor("executor:mock-claude", "mock-claude", {"COORDINATOR": fanout_coordinator_ok}, session={"COORDINATOR": "claude-S"})
        roster["REVIEWER"] = MockExecutor("executor:mock-claude", "mock-claude", {"REVIEWER": integrated_reviewer_ok}, session={"REVIEWER": "claude-S"})
    rep, usage, res = run(tmp_path, canon, fn)
    assert "REVIEW_SESSION_NOT_INDEPENDENT" in _codes(rep) and rep["mission_terminal_status"] == "BLOCKED"
    assert usage["observed_executor_runs"] == 4 and res["states"]["res:wave-reviewer:reviewer"] == "SETTLED"  # reviewer usage retained


def test_reviewer_distinct_from_coordinator_passes(tmp_path, canon):
    def fn(roster):
        roster["COORDINATOR"] = MockExecutor("executor:mock-claude", "mock-claude", {"COORDINATOR": fanout_coordinator_ok}, session={"COORDINATOR": "claude-S1"})
        roster["REVIEWER"] = MockExecutor("executor:mock-claude", "mock-claude", {"REVIEWER": integrated_reviewer_ok}, session={"REVIEWER": "claude-S2"})
    rep, _, _ = run(tmp_path, canon, fn)
    assert rep["disposition"] == DISPOSITION_PARALLEL_QUALIFIED


def test_coordinator_or_reviewer_null_session_blocks(tmp_path, canon):
    def fn(roster):
        roster["REVIEWER"] = MockExecutor("executor:mock-claude", "mock-claude", {"REVIEWER": integrated_reviewer_ok}, session={"REVIEWER": None})
    rep, usage, _ = run(tmp_path, canon, fn)
    assert "SESSION_IDENTITY_MISSING" in _codes(rep) and usage["observed_executor_runs"] == 4
    def fn2(roster):
        roster["COORDINATOR"] = MockExecutor("executor:mock-claude", "mock-claude", {"COORDINATOR": fanout_coordinator_ok}, session={"COORDINATOR": None})
    rep2, usage2, _ = run(tmp_path / "2", canon, fn2)
    assert "SESSION_IDENTITY_MISSING" in _codes(rep2) and usage2["observed_executor_runs"] == 1 and "WORKER_A" not in rep2["runs"]


def test_all_run_ids_pairwise_distinct_and_recorded(tmp_path, canon):
    rep, _, _ = run(tmp_path, canon, lambda r: None)
    ids = rep["parallel"]["identity"]["run_ids"]
    assert set(ids) == {"COORDINATOR", "WORKER_A", "WORKER_B", "REVIEWER"} and len(set(ids.values())) == 4
    sess = rep["parallel"]["identity"]["session_refs"]
    assert len(set(sess.values())) == 4 and all(sess.values())


# -- Part B: evidence-first ordering ------------------------------------------------------------


def test_invalid_worker_result_after_execution_retains_usage(tmp_path, canon):
    def garbage(task):
        worker_ok(task)
        return {"nonsense": True}
    def fn(roster):
        roster["WORKER_B"] = MockExecutor("executor:mock-codex", "mock-codex", {"WORKER_B": garbage})
    rep, usage, res = run(tmp_path, canon, fn)
    assert any(c.startswith("WORKER_B_RESULT_INVALID") for c in _codes(rep))
    assert usage["observed_executor_runs"] == 3 and res["states"]["res:wave-workers:worker_b"] == "SETTLED"


def test_post_wave_order_in_source():
    src = open("sscm/parallel.py").read().split("def _verify_workers")[1].split("def _complete_lane")[0]
    assert src.index("_settle_or_note") < src.index("_record_identity") < src.index("SESSION_IDENTITY_MISSING") < src.index("_complete_lane(lanes, claims, runs, role, failures, record_only=False)")
    assert "SESSION_COLLAPSE" not in open("sscm/parallel.py").read().split("def _run_workers")[1].split("def _verify_workers")[0]
