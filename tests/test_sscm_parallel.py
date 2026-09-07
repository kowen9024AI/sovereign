"""SSCM-01B: parallel controller, workspace isolation, fan-in adversarial cases, credential terminalization."""

import os
import subprocess
import time
from pathlib import Path

import pytest

from sscm.blackboard import Blackboard, MissionBudget
from sscm.contracts import validate_experience
from sscm.executors import MockExecutor
from sscm.parallel import (
    DISPOSITION_PARALLEL_BLOCKED,
    DISPOSITION_PARALLEL_QUALIFIED,
    DISPOSITION_PARALLEL_REPAIR,
    INTEGRATION_ORDER,
    ParallelDogfoodSpec,
    ParallelMissionController,
    RunEnvelope,
)
from sscm.testing import fanout_coordinator_ok, integrated_reviewer_ok, mock_parallel_roster, worker_ok
from sscm.workspace import MissionWorkspaces, WorkspaceError, WorkspaceFacts, assert_isolated, git, observe

EXPECTED_VERBS = ["PUBLISH", "CLAIM", "CLAIM", "COMPLETE", "OBSERVE", "COMPLETE", "OBSERVE", "OBSERVE", "OBSERVE", "CLAIM", "COMPLETE", "OBSERVE", "COMPLETE"]


@pytest.fixture
def canon(tmp_path):
    repo = tmp_path / "canon"
    repo.mkdir()
    git(["init", "-q", "-b", "main"], cwd=repo)
    (repo / "README.md").write_text("canonical\n")
    git(["add", "."], cwd=repo)
    git(["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "base"], cwd=repo)
    return repo


def make(tmp_path, canon, spec_kw=None, **roster):
    base = git(["rev-parse", "HEAD"], cwd=canon)
    spec = ParallelDogfoodSpec(canonical_checkout=canon, base_sha=base, **(spec_kw or {}))
    executors = mock_parallel_roster(spec, **{k: v for k, v in roster.items() if k in ("coordinator", "worker_a", "worker_b", "reviewer")})
    for role, ex in roster.get("executors", {}).items():
        executors[role] = ex
    return spec, ParallelMissionController(spec, executors, run_root=tmp_path / "runs")


# -- happy path ---------------------------------------------------------------------------------


def test_parallel_happy_path_dag_join_fanin_review(tmp_path, canon):
    spec, ctl = make(tmp_path, canon)
    rep = ctl.execute(cleanup=False)
    assert rep["disposition"] == DISPOSITION_PARALLEL_QUALIFIED and rep["mission_terminal_status"] == "SUCCEEDED"
    assert [e["verb"] for e in rep["events"]] == EXPECTED_VERBS
    join = rep["parallel"]["join"]
    assert len(join["predecessor_event_ids"]) == 2 and join["state"] == "JOIN_READY"
    bb = Blackboard(rep["blackboard_path"])
    assert sorted(bb.predecessors_of(join["event_id"])) == sorted(join["predecessor_event_ids"])
    # overlap measured from host timestamps
    assert rep["parallel"]["timing"]["parallel_overlap_seconds"] > 0
    assert rep["parallel"]["timing"]["worker_a_session_ref"] != rep["parallel"]["timing"]["worker_b_session_ref"]
    # independent object stores
    ws = rep["parallel"]["workspaces"]["lanes"]
    assert ws["WORKER_A"]["git_common_dir"] != ws["WORKER_B"]["git_common_dir"]
    assert ws["WORKER_A"]["realpath"] != ws["WORKER_B"]["realpath"]
    integ = rep["parallel"]["integration"]
    assert integ["integration_order"] == list(INTEGRATION_ORDER)
    assert integ["changed_files"] == ["docs/sscm-fanout-a.txt", "docs/sscm-fanout-b.txt"]
    assert {c["verified_sha"] for c in integ["worker_candidates"]} == {rep["parallel"]["verified_worker_a"]["verified_candidate_revision"], rep["parallel"]["verified_worker_b"]["verified_candidate_revision"]}
    assert integ["integration_git_common_dir"] not in (ws["WORKER_A"]["git_common_dir"], ws["WORKER_B"]["git_common_dir"])
    assert rep["reviewer_workspace"]["git_common_dir"] != integ["integration_git_common_dir"]
    assert rep["review_verification"]["reviewer_head"] == integ["integrated_sha"] == rep["reviewer_workspace"]["head_sha"]
    exp = rep["experience"]; validate_experience(exp)
    assert exp["authority"] == "NONE" and exp["procedure_candidate_ref"] is None and exp["training_candidate_ref"] is None
    assert rep["evaluation_created"] is False and rep["promotion_created"] is False
    assert rep["credential_scan"]["order"] == "BEFORE_TERMINAL_SUCCESS"
    assert set(bb.reservations(rep["mission_id"])["states"].values()) == {"SETTLED"}
    ctl.ws.destroy()
    assert git(["rev-parse", "HEAD"], cwd=canon) == spec.base_sha and git(["branch", "--list", "dogfood/*"], cwd=canon) == ""


def test_completion_order_does_not_change_integration(tmp_path, canon):
    def slow_a(task):
        time.sleep(0.6)
        return worker_ok(task)
    def fast_b(task):
        return worker_ok(task)
    _, ctl = make(tmp_path, canon, worker_a=slow_a, worker_b=fast_b)
    rep = ctl.execute()
    t = rep["parallel"]["timing"]
    assert t["worker_b_finished_at"] < t["worker_a_finished_at"]  # B finished first
    assert rep["parallel"]["integration"]["integration_order"] == ["task:fanout-a", "task:fanout-b"]  # A still first
    assert rep["disposition"] == DISPOSITION_PARALLEL_QUALIFIED


# -- lane failures ------------------------------------------------------------------------------


def _failing(task):
    return {"role": "IMPLEMENTER", "status": "FAILED", "claimed_candidate_revision": None, "changed_files": [], "tests_run": [], "blockers": ["simulated"], "summary": "no", "authority": "NONE"}


@pytest.mark.parametrize("bad", ["worker_a", "worker_b"])
def test_one_lane_failure_blocks_without_fanin_or_review(tmp_path, canon, bad):
    _, ctl = make(tmp_path, canon, **{bad: _failing})
    rep = ctl.execute()
    assert rep["disposition"] == DISPOSITION_PARALLEL_BLOCKED and rep["mission_terminal_status"] == "BLOCKED"
    assert "integration" not in rep["parallel"] and "REVIEWER" not in rep["runs"]
    good = "worker_b" if bad == "worker_a" else "worker_a"
    assert f"verified_{good}" in rep["parallel"]  # the good lane's evidence survives
    assert any(b["code"] == "FANOUT_LANE_FAILED" for b in rep["blockers"])


def test_both_lanes_fail_blocked_experience_from_clean_evidence(tmp_path, canon):
    _, ctl = make(tmp_path, canon, worker_a=_failing, worker_b=_failing)
    rep = ctl.execute()
    assert rep["mission_terminal_status"] == "BLOCKED" and rep["experience_created"] is True and rep["experience"]["outcome"] == "BLOCKED"


def test_worker_claim_mismatch_and_short_sha_rejected(tmp_path, canon):
    def liar(task):
        r = worker_ok(task); r["claimed_candidate_revision"] = "deadbeef" * 5; return r
    _, ctl = make(tmp_path, canon, worker_b=liar)
    rep = ctl.execute()
    assert any(b["code"] == "CANDIDATE_CLAIM_MISMATCH" for b in rep["blockers"]) and "integration" not in rep["parallel"]

    def short(task):
        r = worker_ok(task); r["claimed_candidate_revision"] = r["claimed_candidate_revision"][:7]; return r
    _, ctl2 = make(tmp_path / "2", canon, worker_a=short)
    rep2 = ctl2.execute()
    assert any(b["code"].startswith("WORKER_A_RESULT_INVALID") for b in rep2["blockers"])


def test_worker_touching_extra_file_fails_lane_verification(tmp_path, canon):
    def sloppy(task):
        (task.cwd / "extra.txt").write_text("x\n"); subprocess.run(["git", "add", "extra.txt"], cwd=task.cwd, check=True)
        return worker_ok(task)
    _, ctl = make(tmp_path, canon, worker_a=sloppy)
    rep = ctl.execute()
    assert any(b["code"] == "CHANGED_FILE_SET_MISMATCH" for b in rep["blockers"])


def test_one_worker_exceeds_reservation_blocks_keeps_other_evidence(tmp_path, canon):
    base = git(["rev-parse", "HEAD"], cwd=canon)
    spec = ParallelDogfoodSpec(canonical_checkout=canon, base_sha=base)
    roster = mock_parallel_roster(spec)
    roster["WORKER_B"].usage_by_role["WORKER_B"] = {"executor_turns": 99, "model_calls": 1, "token_units": 1.0}  # over its envelope of 10 turns
    rep = ParallelMissionController(spec, roster, run_root=tmp_path / "runs").execute()
    assert rep["disposition"] == DISPOSITION_PARALLEL_BLOCKED
    assert any(b["code"] == "RUN_RESERVATION_EXCEEDED" for b in rep["blockers"])
    assert "WORKER_A" in rep["runs"] and rep["budget"]["observed_executor_turns"] >= 99  # evidence kept, not erased


def test_wave_not_admitted_when_envelopes_exceed_headroom(tmp_path, canon):
    tight = MissionBudget(max_active_actors=2, max_coordination_transitions=12, max_turns=40, max_model_calls=40, max_wall_seconds=1800,
                          max_repair_loops=0, max_consecutive_failures=1, max_token_or_cost_units=100.0)
    envelopes = {"COORDINATOR": RunEnvelope(6, 10.0, 600.0), "WORKER_A": RunEnvelope(10, 60.0, 900.0), "WORKER_B": RunEnvelope(10, 60.0, 900.0), "REVIEWER": RunEnvelope(12, 10.0, 900.0)}
    base = git(["rev-parse", "HEAD"], cwd=canon)
    spec = ParallelDogfoodSpec(canonical_checkout=canon, base_sha=base, budget=tight, envelopes=envelopes)
    rep = ParallelMissionController(spec, mock_parallel_roster(spec), run_root=tmp_path / "runs").execute()
    assert rep["disposition"] == DISPOSITION_PARALLEL_BLOCKED
    assert any(b["code"] == "BUDGET_EXCEEDED" for b in rep["blockers"])
    assert "WORKER_A" not in rep["runs"] and "WORKER_B" not in rep["runs"]  # neither lane launched


def test_repair_required_stops_without_repair(tmp_path, canon):
    def strict(task):
        r = integrated_reviewer_ok(task); r["verdict"] = "REPAIR_REQUIRED"; r["blocking_findings"] = ["style"]; return r
    _, ctl = make(tmp_path, canon, reviewer=strict)
    rep = ctl.execute()
    assert rep["disposition"] == DISPOSITION_PARALLEL_REPAIR and "RETRY" not in [e["verb"] for e in rep["events"]]


def test_reviewer_wrong_sha_fails_closed(tmp_path, canon):
    def wrong(task):
        r = integrated_reviewer_ok(task); r["reviewed_revision"] = "0" * 40; r["verdict"] = "ACCEPT"; r["blocking_findings"] = []; return r
    _, ctl = make(tmp_path, canon, reviewer=wrong)
    rep = ctl.execute()
    assert any(b["code"] == "REVIEW_SHA_MISMATCH" for b in rep["blockers"]) and rep["mission_terminal_status"] == "BLOCKED"


def test_coordinator_topology_drift_rejected(tmp_path, canon):
    def third(task):
        r = fanout_coordinator_ok(task); r["tasks"] = r["tasks"][:1]; return r  # schema needs exactly 2 -> invalid
    def swap(task):
        r = fanout_coordinator_ok(task); r["tasks"][0]["allowed_files"] = ["README.md"]; return r
    for fn, code in ((third, "COORDINATOR_RESULT_INVALID"), (swap, "COORDINATOR_SCOPE_DRIFT")):
        _, ctl = make(tmp_path / code, canon, coordinator=fn)
        rep = ctl.execute()
        assert any(b["code"].startswith(code) for b in rep["blockers"]), rep["blockers"]
        assert "WORKER_A" not in rep["runs"]


def test_secret_in_worker_log_blocks_before_success(tmp_path, canon):
    base = git(["rev-parse", "HEAD"], cwd=canon)
    spec = ParallelDogfoodSpec(canonical_checkout=canon, base_sha=base)
    roster = mock_parallel_roster(spec)
    roster["WORKER_B"] = MockExecutor("executor:mock-codex", "mock-codex", {"WORKER_B": worker_ok}, log_writer={"WORKER_B": lambda t: "xoxb-1234567890-abcdefghij\n"})
    rep = ParallelMissionController(spec, roster, run_root=tmp_path / "runs").execute()
    assert rep["credential_scan"]["result"] == "REJECTED" and rep["mission_terminal_status"] == "BLOCKED"
    assert rep["experience_created"] is False and rep["disposition"] == DISPOSITION_PARALLEL_BLOCKED


def test_same_runtime_family_reviewer_refused(tmp_path, canon):
    base = git(["rev-parse", "HEAD"], cwd=canon)
    spec = ParallelDogfoodSpec(canonical_checkout=canon, base_sha=base)
    roster = mock_parallel_roster(spec)
    roster["REVIEWER"] = MockExecutor("executor:mock-codex", "mock-codex", {"REVIEWER": integrated_reviewer_ok})
    rep = ParallelMissionController(spec, roster, run_root=tmp_path / "runs").execute()
    assert any(b["code"] == "RUNTIME_NOT_DISTINCT" for b in rep["blockers"])


# -- workspace isolation --------------------------------------------------------------------------


def _facts(path, canon):
    return observe(path, str(Path(canon).resolve()))


def test_assert_isolated_rejects_shared_realpath_symlink_nesting_common_dir_canonical_dirty(tmp_path, canon):
    base = git(["rev-parse", "HEAD"], cwd=canon)
    ws = MissionWorkspaces(tmp_path / "ws", canon)
    la, lb = ws.lane("worker-a"), ws.lane("worker-b")
    la.create_mirror(base); lb.create_mirror(base)
    wa = la.add_branch_worktree("a-wt", "x/a", base); wb = lb.add_branch_worktree("b-wt", "x/b", base)
    assert assert_isolated({"A": _facts(wa, canon), "B": _facts(wb, canon)})["isolated"]
    # same realpath
    with pytest.raises(WorkspaceError) as ei:
        assert_isolated({"A": _facts(wa, canon), "B": _facts(wa, canon)})
    assert ei.value.code == "WRITABLE_COLLISION"
    # symlink alias resolves to the same place
    link = tmp_path / "alias"; os.symlink(wa, link)
    with pytest.raises(WorkspaceError):
        assert_isolated({"A": _facts(wa, canon), "B": _facts(link, canon)})
    # nested writable paths (synthesized facts: a real nested worktree would already trip the dirty gate)
    fa = _facts(wa, canon)
    nested = WorkspaceFacts(str(Path(fa.realpath) / "nested"), str(lb.mirror.resolve()), fa.head_sha, "x/n", False, False, [])
    with pytest.raises(WorkspaceError) as ei:
        assert_isolated({"A": fa, "N": nested})
    assert ei.value.code == "WRITABLE_COLLISION"
    # shared git common dir (two worktrees of one lane)
    wa2 = la.add_branch_worktree("a2-wt", "x/a2", base)
    with pytest.raises(WorkspaceError) as ei:
        assert_isolated({"A": _facts(wa, canon), "A2": _facts(wa2, canon)})
    assert ei.value.code == "SHARED_GIT_COMMON_DIR"
    # canonical checkout
    with pytest.raises(WorkspaceError) as ei:
        assert_isolated({"A": _facts(wa, canon), "C": _facts(canon, canon)})
    assert ei.value.code == "CANONICAL_CHECKOUT_WRITE"
    # dirty
    (wb / "junk").write_text("x")
    with pytest.raises(WorkspaceError) as ei:
        assert_isolated({"A": _facts(wa, canon), "B": _facts(wb, canon)})
    assert ei.value.code == "WORKSPACE_DIRTY_AT_ADMISSION"


# -- fan-in adversarial -----------------------------------------------------------------------------


def _two_lanes(tmp_path, canon):
    base = git(["rev-parse", "HEAD"], cwd=canon)
    ws = MissionWorkspaces(tmp_path / "ws", canon)
    shas = {}
    for name, rel, content in (("worker-a", "docs/sscm-fanout-a.txt", "SSCM_FANOUT_A_OK\n"), ("worker-b", "docs/sscm-fanout-b.txt", "SSCM_FANOUT_B_OK\n")):
        lane = ws.lane(name); lane.create_mirror(base); wt = lane.add_branch_worktree(f"{name}-wt", f"d/{name}", base)
        p = wt / rel; p.parent.mkdir(parents=True, exist_ok=True); p.write_text(content)
        git(["add", "."], cwd=wt); git(["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", name], cwd=wt)
        shas[name] = git(["rev-parse", "HEAD"], cwd=wt)
    integ = ws.lane("integration"); integ.create_mirror(base)
    return base, ws, shas, integ


def test_fanin_pass_and_wrong_sha_imports_rejected(tmp_path, canon):
    base, ws, shas, integ = _two_lanes(tmp_path, canon)
    assert integ.import_exact(ws.lane("worker-a").mirror, shas["worker-a"], "a") == shas["worker-a"]
    with pytest.raises(WorkspaceError) as ei:
        integ.import_exact(ws.lane("worker-a").mirror, shas["worker-b"], "wrong")  # B's sha is not in A's store
    assert ei.value.code == "IMPORT_FETCH_FAILED"
    with pytest.raises(WorkspaceError) as ei:
        integ.import_exact(ws.lane("worker-b").mirror, shas["worker-b"][:7], "short")
    assert ei.value.code == "REVISION_NOT_FULL_SHA"
    integ.import_exact(ws.lane("worker-b").mirror, shas["worker-b"], "b")
    res = integ.integrate("integration-wt", base, [("task:fanout-a", shas["worker-a"]), ("task:fanout-b", shas["worker-b"])])
    v = integ.verify_candidate(Path(res["worktree"]), base, {"docs/sscm-fanout-a.txt": "SSCM_FANOUT_A_OK\n", "docs/sscm-fanout-b.txt": "SSCM_FANOUT_B_OK\n"})
    assert v["changed_files"] == ["docs/sscm-fanout-a.txt", "docs/sscm-fanout-b.txt"] and v["worktree_clean"]


def test_fanin_conflict_aborts_without_model(tmp_path, canon):
    base = git(["rev-parse", "HEAD"], cwd=canon)
    ws = MissionWorkspaces(tmp_path / "ws", canon)
    shas = {}
    for name, content in (("worker-a", "A\n"), ("worker-b", "B\n")):
        lane = ws.lane(name); lane.create_mirror(base); wt = lane.add_branch_worktree(f"{name}-wt", f"d/{name}", base)
        (wt / "same.txt").write_text(content); git(["add", "."], cwd=wt); git(["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", name], cwd=wt)
        shas[name] = git(["rev-parse", "HEAD"], cwd=wt)
    integ = ws.lane("integration"); integ.create_mirror(base)
    for n in ("worker-a", "worker-b"):
        integ.import_exact(ws.lane(n).mirror, shas[n], n)
    with pytest.raises(WorkspaceError) as ei:
        integ.integrate("integration-wt", base, [("task:fanout-a", shas["worker-a"]), ("task:fanout-b", shas["worker-b"])])
    assert ei.value.code == "FANIN_CONFLICT"
    assert git(["status", "--porcelain"], cwd=integ.root / "integration-wt") == ""  # cherry-pick aborted cleanly


def test_integrated_verification_rejects_content_or_file_drift(tmp_path, canon):
    base, ws, shas, integ = _two_lanes(tmp_path, canon)
    for n in ("worker-a", "worker-b"):
        integ.import_exact(ws.lane(n).mirror, shas[n], n)
    res = integ.integrate("integration-wt", base, [("task:fanout-a", shas["worker-a"]), ("task:fanout-b", shas["worker-b"])])
    with pytest.raises(WorkspaceError) as ei:
        integ.verify_candidate(Path(res["worktree"]), base, {"docs/sscm-fanout-a.txt": "SSCM_FANOUT_A_OK\n", "docs/sscm-fanout-b.txt": "WRONG\n"})
    assert ei.value.code == "CONTENT_MISMATCH"
    with pytest.raises(WorkspaceError) as ei:
        integ.verify_candidate(Path(res["worktree"]), base, {"docs/sscm-fanout-a.txt": "SSCM_FANOUT_A_OK\n"})
    assert ei.value.code == "CHANGED_FILE_SET_MISMATCH"


def test_integration_order_is_frozen_constant_not_timing():
    assert INTEGRATION_ORDER == ("task:fanout-a", "task:fanout-b")
    src = Path("sscm/parallel.py").read_text()
    assert "finished_at" not in src.split("def _fan_in")[1].split("def _admit_reviewer_lane")[0]
