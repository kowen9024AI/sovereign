"""Frozen 01A mission controller against a throwaway canonical repo with deterministic mock executors."""

import json
import os
import subprocess
from pathlib import Path

import pytest

from sscm.artifacts import CredentialMaterialSuspected, RunDir, scan_json
from sscm.blackboard import Blackboard, MissionBudget
from sscm.contracts import validate_experience
from sscm.executors import ExecutorCapability, MockExecutor, _extract_json_object, child_env
from sscm.mission import (
    DISPOSITION_BLOCKED,
    DISPOSITION_QUALIFIED,
    DISPOSITION_REPAIR,
    DogfoodSpec,
    MissionController,
)
from sscm.testing import coordinator_ok, implementer_ok, mock_roster, reviewer_ok
from sscm.workspace import git

EXPECTED_CHAIN = ["PUBLISH", "CLAIM", "COMPLETE", "OBSERVE", "CLAIM", "COMPLETE", "OBSERVE", "COMPLETE"]


@pytest.fixture
def canon(tmp_path):
    repo = tmp_path / "canon"
    repo.mkdir()
    git(["init", "-q", "-b", "main"], cwd=repo)
    (repo / "README.md").write_text("canonical\n")
    git(["add", "."], cwd=repo)
    git(["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "base"], cwd=repo)
    return repo


def make(tmp_path, canon, **roster_overrides):
    base = git(["rev-parse", "HEAD"], cwd=canon)
    spec = DogfoodSpec(canonical_checkout=canon, base_sha=base)
    ctl = MissionController(spec, mock_roster(spec, **roster_overrides), run_root=tmp_path / "runs")
    return spec, ctl


def test_happy_path_produces_exact_chain_and_experience(tmp_path, canon):
    spec, ctl = make(tmp_path, canon)
    rep = ctl.execute()
    assert rep["disposition"] == DISPOSITION_QUALIFIED
    assert rep["mission_terminal_status"] == "SUCCEEDED"
    assert [e["verb"] for e in rep["events"]] == EXPECTED_CHAIN
    # host truth, not model claim, is what the reviewer received
    v = rep["candidate_verification"]
    assert v["changed_files"] == ["docs/sscm-dogfood.txt"] and v["worktree_clean"] is True
    assert rep["review_verification"]["reviewer_head"] == v["verified_candidate_revision"]
    # experience candidate: observed only
    exp = rep["experience"]
    validate_experience(exp)
    assert exp["authority"] == "NONE" and exp["outcome"] == "SUCCEEDED"
    assert exp["procedure_candidate_ref"] is None and exp["training_candidate_ref"] is None
    assert rep["evaluation_created"] is False and rep["promotion_created"] is False and rep["training_artifact_created"] is False
    # cleanup and canonical protection
    assert rep["cleanup"]["root_removed"] is True
    assert git(["rev-parse", "HEAD"], cwd=canon) == spec.base_sha
    assert git(["branch", "--list", "dogfood/*"], cwd=canon) == ""
    assert rep["credential_scan"]["result"] == "CLEAN"


def test_workspaces_are_isolated_and_never_canonical(tmp_path, canon):
    spec, ctl = make(tmp_path, canon)
    rep = ctl.execute(cleanup=False)
    impl, rev = rep["implementer_workspace"], rep["reviewer_workspace"]
    assert impl["realpath"] != rev["realpath"]
    assert not impl["is_canonical_checkout"] and not rev["is_canonical_checkout"]
    assert impl["branch"] == spec.dogfood_branch and rev["branch"] == "HEAD"  # reviewer detached
    assert impl["dirty"] is False and rev["dirty"] is False
    assert Path(impl["git_common_dir"]).resolve() != (canon / ".git").resolve()  # mirror, not canonical
    ctl.ws.destroy()


def test_blackboard_state_reopens_identically_after_mission(tmp_path, canon):
    spec, ctl = make(tmp_path, canon)
    rep = ctl.execute()
    again = Blackboard(rep["blackboard_path"])
    proj = again.project(rep["mission_id"]).as_dict()
    assert proj == rep["projection"]
    assert [e["verb"] for e in again.events(rep["mission_id"])] == EXPECTED_CHAIN


# -- fail-closed paths -----------------------------------------------------------------------


def test_implementer_lies_about_sha_is_claim_mismatch(tmp_path, canon):
    def lying(task):
        r = implementer_ok(task)
        r["claimed_candidate_revision"] = "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef"
        return r
    _, ctl = make(tmp_path, canon, implementer=lying)
    rep = ctl.execute()
    assert rep["disposition"] == DISPOSITION_BLOCKED
    assert any(b["code"] == "CANDIDATE_CLAIM_MISMATCH" for b in rep["blockers"])
    assert rep["mission_terminal_status"] == "BLOCKED"
    assert "experience" in rep and rep["experience"]["outcome"] == "BLOCKED"


def test_implementer_touching_extra_file_fails_host_verification(tmp_path, canon):
    def sloppy(task):
        (task.cwd / "extra.txt").write_text("residue\n")
        subprocess.run(["git", "add", "extra.txt"], cwd=task.cwd, check=True)
        return implementer_ok(task)
    _, ctl = make(tmp_path, canon, implementer=sloppy)
    rep = ctl.execute()
    assert any(b["code"] == "CHANGED_FILE_SET_MISMATCH" for b in rep["blockers"])
    assert rep["disposition"] == DISPOSITION_BLOCKED


def test_implementer_wrong_content_fails(tmp_path, canon):
    def wrong(task):
        task.instruction["expected_content"] = {"docs/sscm-dogfood.txt": "SOVEREIGN_SSCM_NOPE\n"}
        return implementer_ok(task)
    _, ctl = make(tmp_path, canon, implementer=wrong)
    rep = ctl.execute()
    assert any(b["code"] == "CONTENT_MISMATCH" for b in rep["blockers"])


def test_implementer_dirty_worktree_fails(tmp_path, canon):
    def dirty(task):
        r = implementer_ok(task)
        (task.cwd / "untracked.tmp").write_text("x")
        return r
    _, ctl = make(tmp_path, canon, implementer=dirty)
    rep = ctl.execute()
    assert any(b["code"] == "WORKTREE_DIRTY_AFTER_COMMIT" for b in rep["blockers"])


def test_implementer_failure_blocks_without_repair(tmp_path, canon):
    def failing(task):
        return {"role": "IMPLEMENTER", "status": "FAILED", "claimed_candidate_revision": None, "changed_files": [],
                "tests_run": [], "blockers": ["simulated"], "summary": "gave up", "authority": "NONE"}
    _, ctl = make(tmp_path, canon, implementer=failing)
    rep = ctl.execute()
    verbs = [e["verb"] for e in rep["events"]]
    assert verbs == ["PUBLISH", "CLAIM", "COMPLETE", "REJECT"]  # no RETRY, no second CLAIM
    assert rep["disposition"] == DISPOSITION_BLOCKED


def test_reviewer_sha_mismatch_fails_closed_even_with_accept(tmp_path, canon):
    def wrong_sha(task):
        r = reviewer_ok(task)
        r["reviewed_revision"] = "0123456789abcdef0123456789abcdef01234567"
        r["verdict"] = "ACCEPT"
        r["blocking_findings"] = []
        return r
    _, ctl = make(tmp_path, canon, reviewer=wrong_sha)
    rep = ctl.execute()
    assert any(b["code"] == "REVIEW_SHA_MISMATCH" for b in rep["blockers"])
    assert rep["mission_terminal_status"] == "BLOCKED"


def test_repair_required_stops_without_autonomous_repair(tmp_path, canon):
    def strict(task):
        r = reviewer_ok(task)
        r["verdict"] = "REPAIR_REQUIRED"
        r["blocking_findings"] = ["commit message style"]
        return r
    _, ctl = make(tmp_path, canon, reviewer=strict)
    rep = ctl.execute()
    assert rep["disposition"] == DISPOSITION_REPAIR
    verbs = [e["verb"] for e in rep["events"]]
    assert verbs.count("CLAIM") == 2 and "RETRY" not in verbs
    assert rep["mission_terminal_status"] == "BLOCKED"


def test_coordinator_scope_drift_is_rejected(tmp_path, canon):
    def greedy(task):
        r = coordinator_ok(task)
        r["allowed_files"] = ["docs/sscm-dogfood.txt", "README.md"]
        return r
    _, ctl = make(tmp_path, canon, coordinator=greedy)
    rep = ctl.execute()
    assert any(b["code"] == "COORDINATOR_SCOPE_DRIFT" for b in rep["blockers"])
    assert [e["verb"] for e in rep["events"]] == ["REJECT"]  # nothing was published


def test_coordinator_invalid_schema_is_rejected(tmp_path, canon):
    def bad(task):
        r = coordinator_ok(task)
        r["authority"] = "COORDINATOR"
        return r
    _, ctl = make(tmp_path, canon, coordinator=bad)
    rep = ctl.execute()
    assert any(b["code"].startswith("COORDINATOR_RESULT_INVALID") for b in rep["blockers"])


def test_credential_shaped_artifact_is_rejected(tmp_path, canon):
    def leaky(task):
        r = reviewer_ok(task)
        r["summary"] = "looked fine, token was ghp_" + "A" * 36
        return r
    _, ctl = make(tmp_path, canon, reviewer=leaky)
    rep = ctl.execute()
    assert any(b["code"] == "CREDENTIAL_MATERIAL_SUSPECTED" for b in rep["blockers"])
    assert rep["disposition"] == DISPOSITION_BLOCKED
    assert not (Path(rep["run_dir"]) / "artifacts" / "reviewer-result.json").exists()


def test_same_runtime_family_for_implementer_and_reviewer_is_refused(tmp_path, canon):
    base = git(["rev-parse", "HEAD"], cwd=canon)
    spec = DogfoodSpec(canonical_checkout=canon, base_sha=base)
    roster = mock_roster(spec)
    roster["IMPLEMENTER"] = MockExecutor("executor:mock-claude", "mock-claude", {"IMPLEMENTER": implementer_ok})
    rep = MissionController(spec, roster, run_root=tmp_path / "runs").execute()
    assert any(b["code"] == "RUNTIME_NOT_DISTINCT" for b in rep["blockers"])


def test_executor_claiming_authority_is_refused(tmp_path, canon):
    base = git(["rev-parse", "HEAD"], cwd=canon)
    spec = DogfoodSpec(canonical_checkout=canon, base_sha=base)
    roster = mock_roster(spec)

    class Bossy(MockExecutor):
        def capability(self):
            c = super().capability().as_dict()
            c["authority"] = "MERGE"
            return ExecutorCapability(**c)
    roster["IMPLEMENTER"] = Bossy("executor:mock-codex", "mock-codex", {"IMPLEMENTER": implementer_ok})
    rep = MissionController(spec, roster, run_root=tmp_path / "runs").execute()
    assert any(b["code"] == "AUTHORITY_NOT_NONE" for b in rep["blockers"])


# -- executors + artifacts ---------------------------------------------------------------------


def test_child_env_drops_provider_and_nesting_variables(monkeypatch):
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    monkeypatch.setenv("OPENAI_API_KEY", "x")
    monkeypatch.setenv("CODEX_HOME", "/nowhere")
    monkeypatch.setenv("PATH", "/usr/bin")
    env = child_env()
    assert "CLAUDECODE" not in env and "ANTHROPIC_API_KEY" not in env and "OPENAI_API_KEY" not in env and "CODEX_HOME" not in env
    assert env["PATH"] == "/usr/bin" and env["SSCM"] == "1"


def test_extract_json_object_tolerates_prose_and_fences():
    assert _extract_json_object('```json\n{"a": 1}\n```') == {"a": 1}
    assert _extract_json_object('note: {"a": {"b": 2}} trailing') == {"a": {"b": 2}}
    assert _extract_json_object("[1,2]") is None
    assert _extract_json_object("") is None


def test_capability_contract_fields_and_authority():
    cap = MockExecutor("executor:x", "x", {}).capability().as_dict()
    for k in ("executor_id", "runtime_family", "availability", "native_auth_class", "working_directory_support", "read_support",
              "write_support", "execution_support", "structured_output_support", "session_identity_support", "resume_support",
              "cancellation_support", "authority"):
        assert k in cap
    assert cap["authority"] == "NONE"


def test_rundir_refs_are_digest_bound(tmp_path):
    rd = RunDir("m-test", tmp_path)
    ref = rd.write_json("artifacts/x.json", {"ok": True})
    assert rd.verify_ref(ref)
    (rd.root / "artifacts" / "x.json").write_text("{}")
    assert not rd.verify_ref(ref)
    with pytest.raises(CredentialMaterialSuspected):
        rd.write_json("artifacts/leak.json", {"access_token": "abc"})


def test_scan_allows_token_counts_but_not_token_values():
    scan_json({"max_token_or_cost_units": 5.0, "usage": {"input_tokens": 12}})
    with pytest.raises(CredentialMaterialSuspected):
        scan_json({"provider_token": "anything"})
    with pytest.raises(CredentialMaterialSuspected):
        scan_json({"note": "Authorization: Bearer abcdefghijklmnopqrstuvwxyz0123"})
