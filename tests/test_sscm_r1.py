"""WO-SOVEREIGN-SSCM-01A-R1: exact revision authority, consumptive budgets, credential-safe terminalization."""

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from sscm.blackboard import Blackboard, BlackboardError, BudgetExceeded, MissionBudget
from sscm.contracts import load_schema
from sscm.executors import OBSERVABLE, UNOBSERVABLE, USAGE_DIMENSIONS, MockExecutor, _parse_claude, _parse_codex, token_units
from sscm.mission import DISPOSITION_BLOCKED, DISPOSITION_QUALIFIED, IMPLEMENTER_SCHEMA, REVIEWER_SCHEMA, DogfoodSpec, MissionController
from sscm.testing import implementer_ok, mock_roster, reviewer_ok
from sscm.workspace import git

HOST = "0123456789abcdef0123456789abcdef01234567"
WRONG = "0123456789abcdef0123456789abcdef01234568"
SHORT = "0123456"


@pytest.fixture
def canon(tmp_path):
    repo = tmp_path / "canon"
    repo.mkdir()
    git(["init", "-q", "-b", "main"], cwd=repo)
    (repo / "README.md").write_text("canonical\n")
    git(["add", "."], cwd=repo)
    git(["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "base"], cwd=repo)
    return repo


def make(tmp_path, canon, budget=None, clock=None, **roster):
    base = git(["rev-parse", "HEAD"], cwd=canon)
    spec = DogfoodSpec(canonical_checkout=canon, base_sha=base, **({"budget": budget} if budget else {}))
    executors = mock_roster(spec, **{k: v for k, v in roster.items() if k in ("coordinator", "implementer", "reviewer")})
    for role, ex in roster.get("executors", {}).items():
        executors[role] = ex
    kw = {"run_root": tmp_path / "runs"}
    if clock:
        kw["clock"] = clock
    return spec, MissionController(spec, executors, **kw)


# ---------------------------------------------------------------------------
# PART A — exact revision authority
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("schema,field", [(IMPLEMENTER_SCHEMA, "claimed_candidate_revision"), (REVIEWER_SCHEMA, "reviewed_revision")])
def test_result_contracts_require_full_40_hex_object_ids(schema, field):
    s = load_schema(schema)
    assert s["properties"][field]["pattern"] == "^[0-9a-f]{40}$"
    v = Draft202012Validator(s)
    base = {"role": "IMPLEMENTER", "status": "SUCCEEDED", "changed_files": [], "tests_run": [], "blockers": [], "summary": "", "authority": "NONE"} \
        if field == "claimed_candidate_revision" else \
        {"role": "REVIEWER", "verdict": "ACCEPT", "findings": [], "blocking_findings": [], "tests_run": [], "summary": "", "authority": "NONE"}
    assert list(v.iter_errors({**base, field: HOST})) == []
    assert list(v.iter_errors({**base, field: SHORT}))  # abbreviated prefix rejected by schema
    assert list(v.iter_errors({**base, field: HOST.upper()}))  # not lowercase hex


def _impl_claiming(sha_fn):
    def impl(task):
        r = implementer_ok(task)
        r["claimed_candidate_revision"] = sha_fn(r["claimed_candidate_revision"])
        return r
    return impl


def _rev_reporting(sha_fn):
    def rev(task):
        r = reviewer_ok(task)
        r["reviewed_revision"] = sha_fn(r["reviewed_revision"])
        return r
    return rev


def test_implementer_short_sha_rejected(tmp_path, canon):
    _, ctl = make(tmp_path, canon, implementer=_impl_claiming(lambda h: h[:7]))
    rep = ctl.execute()
    assert rep["disposition"] == DISPOSITION_BLOCKED
    codes = {b["code"] for b in rep["blockers"]}
    assert codes & {"IMPLEMENTER_RESULT_INVALID_SCHEMA", "CANDIDATE_CLAIM_MISMATCH"}
    assert "SUCCEEDED" != rep["mission_terminal_status"]


def test_implementer_wrong_full_sha_rejected(tmp_path, canon):
    _, ctl = make(tmp_path, canon, implementer=_impl_claiming(lambda h: h[:-1] + ("8" if h[-1] != "8" else "9")))
    rep = ctl.execute()
    assert any(b["code"] == "CANDIDATE_CLAIM_MISMATCH" for b in rep["blockers"])


def test_implementer_exact_full_sha_passes(tmp_path, canon):
    _, ctl = make(tmp_path, canon, implementer=_impl_claiming(lambda h: h))
    rep = ctl.execute()
    assert rep["disposition"] == DISPOSITION_QUALIFIED
    assert len(rep["candidate_verification"]["verified_candidate_revision"]) == 40


def test_implementer_null_claim_is_allowed_host_establishes_candidate(tmp_path, canon):
    def impl(task):
        r = implementer_ok(task)
        r["claimed_candidate_revision"] = None
        return r
    _, ctl = make(tmp_path, canon, implementer=impl)
    rep = ctl.execute()
    assert rep["disposition"] == DISPOSITION_QUALIFIED
    assert rep["candidate_verification"]["claimed_candidate_revision"] is None


def test_reviewer_short_sha_rejected(tmp_path, canon):
    _, ctl = make(tmp_path, canon, reviewer=_rev_reporting(lambda h: h[:7]))
    rep = ctl.execute()
    codes = {b["code"] for b in rep["blockers"]}
    assert codes & {"REVIEWER_RESULT_INVALID_SCHEMA", "REVIEW_SHA_MISMATCH", "REVIEWER_FAILED"}
    assert rep["mission_terminal_status"] != "SUCCEEDED"


def test_reviewer_wrong_full_sha_rejected(tmp_path, canon):
    _, ctl = make(tmp_path, canon, reviewer=_rev_reporting(lambda h: h[:-1] + ("8" if h[-1] != "8" else "9")))
    rep = ctl.execute()
    assert any(b["code"] == "REVIEW_SHA_MISMATCH" for b in rep["blockers"])


def test_reviewer_exact_full_sha_passes(tmp_path, canon):
    _, ctl = make(tmp_path, canon, reviewer=_rev_reporting(lambda h: h))
    assert ctl.execute()["disposition"] == DISPOSITION_QUALIFIED


def test_no_prefix_logic_in_mission_module():
    src = Path("sscm/mission.py").read_text()
    assert "startswith" not in src


# ---------------------------------------------------------------------------
# PART B — consumptive budgets
# ---------------------------------------------------------------------------


def test_token_units_normalization_is_deterministic():
    assert token_units(84541, 769) == 85.31
    assert token_units(2 + 18862 + 0, 1248) == 20.112
    assert token_units(None, 5) is None


def test_claude_parser_reports_turns_tokens_cost():
    env = {"type": "result", "session_id": "s1", "num_turns": 6, "total_cost_usd": 0.3005,
           "usage": {"input_tokens": 6, "cache_creation_input_tokens": 22382, "cache_read_input_tokens": 42060, "output_tokens": 2153},
           "structured_output": {"ok": True}}
    structured, session, usage = _parse_claude(json.dumps(env), None)
    assert structured == {"ok": True} and session == "s1"
    assert usage["executor_turns"] == 6 and usage["model_calls"] == 6
    assert usage["token_units"] == round((6 + 22382 + 42060 + 2153) / 1000, 3)
    assert usage["cost_units"] == 0.3005


def test_codex_parser_reports_turns_tokens_and_unobservable_calls(tmp_path):
    last = tmp_path / "last.json"
    last.write_text(json.dumps({"role": "IMPLEMENTER"}))
    lines = [
        {"type": "thread.started", "thread_id": "T1"},
        {"type": "turn.started"},
        {"type": "item.completed", "item": {"type": "command_execution"}},
        {"type": "turn.completed", "usage": {"input_tokens": 84541, "cached_input_tokens": 66304, "output_tokens": 769}},
    ]
    structured, session, usage = _parse_codex("\n".join(json.dumps(l) for l in lines), None, last)
    assert structured == {"role": "IMPLEMENTER"} and session == "codex-thread:T1"
    assert usage["executor_turns"] == 1 and usage["token_units"] == 85.31
    assert usage["model_calls"] is None and usage["cost_units"] is None


def _budget(**kw):
    base = dict(max_active_actors=1, max_coordination_transitions=8, max_turns=24, max_model_calls=24,
                max_wall_seconds=1800, max_repair_loops=0, max_consecutive_failures=1, max_token_or_cost_units=400.0)
    base.update(kw)
    return MissionBudget(**base)


def _roster_usage(spec, per_role):
    r = mock_roster(spec)
    for role, ex in r.items():
        ex.usage_by_role[role] = dict(per_role)
    return r


def _run_with_usage(tmp_path, canon, budget, per_role):
    base = git(["rev-parse", "HEAD"], cwd=canon)
    spec = DogfoodSpec(canonical_checkout=canon, base_sha=base, budget=budget)
    return MissionController(spec, _roster_usage(spec, per_role), run_root=tmp_path / "runs").execute()


def test_model_calls_exactly_at_limit_passes(tmp_path, canon):
    rep = _run_with_usage(tmp_path, canon, _budget(max_model_calls=3), {"executor_turns": 1, "model_calls": 1, "token_units": 1.0})
    assert rep["disposition"] == DISPOSITION_QUALIFIED
    assert rep["budget"]["observed_model_calls"] == 3 and rep["budget"]["budget_exceeded"] == []


def test_model_calls_over_limit_blocks(tmp_path, canon):
    rep = _run_with_usage(tmp_path, canon, _budget(max_model_calls=2), {"executor_turns": 1, "model_calls": 1, "token_units": 1.0})
    assert rep["disposition"] == DISPOSITION_BLOCKED
    assert any(b["code"] == "BUDGET_EXCEEDED" and "max_model_calls" in b["detail"] for b in rep["blockers"])
    assert rep["mission_terminal_status"] == "BLOCKED"
    # the third run was never launched: headroom gate refused it
    assert rep["budget"]["observed_executor_runs"] == 2


def test_executor_turns_over_limit_blocks(tmp_path, canon):
    rep = _run_with_usage(tmp_path, canon, _budget(max_turns=5), {"executor_turns": 3, "model_calls": 1, "token_units": 1.0})
    assert rep["disposition"] == DISPOSITION_BLOCKED
    assert any("max_turns" in b["detail"] for b in rep["blockers"])


def test_token_units_exactly_at_limit_passes(tmp_path, canon):
    rep = _run_with_usage(tmp_path, canon, _budget(max_token_or_cost_units=30.0), {"executor_turns": 1, "model_calls": 1, "token_units": 10.0})
    assert rep["disposition"] == DISPOSITION_QUALIFIED
    assert rep["budget"]["observed_cost_or_token_units"] == 30.0


def test_token_units_over_limit_blocks(tmp_path, canon):
    rep = _run_with_usage(tmp_path, canon, _budget(max_token_or_cost_units=25.0), {"executor_turns": 1, "model_calls": 1, "token_units": 10.0})
    assert rep["disposition"] == DISPOSITION_BLOCKED
    assert any("max_token_or_cost_units" in b["detail"] for b in rep["blockers"])


def test_unknown_required_dimension_fails_closed_before_any_launch(tmp_path, canon):
    base = git(["rev-parse", "HEAD"], cwd=canon)
    spec = DogfoodSpec(canonical_checkout=canon, base_sha=base)
    roster = mock_roster(spec)
    roster["IMPLEMENTER"] = MockExecutor("executor:mock-codex", "mock-codex", {"IMPLEMENTER": implementer_ok},
                                         observability={**{d: OBSERVABLE for d in USAGE_DIMENSIONS}, "token_units": UNOBSERVABLE})
    rep = MissionController(spec, roster, run_root=tmp_path / "runs").execute()
    assert any(b["code"] == "BUDGET_DIMENSION_UNOBSERVABLE" for b in rep["blockers"])
    assert rep["runs"] == {}  # nothing launched


def test_unreported_but_declared_observable_dimension_fails_closed(tmp_path, canon):
    base = git(["rev-parse", "HEAD"], cwd=canon)
    spec = DogfoodSpec(canonical_checkout=canon, base_sha=base)
    roster = mock_roster(spec)
    roster["COORDINATOR"].usage_by_role["COORDINATOR"] = {"executor_turns": None}  # claims observable, reports nothing
    rep = MissionController(spec, roster, run_root=tmp_path / "runs").execute()
    assert any(b["code"] == "BUDGET_DIMENSION_UNREPORTED" for b in rep["blockers"])
    assert rep["disposition"] == DISPOSITION_BLOCKED


def test_optional_unobservable_dimension_is_recorded_unknown_not_zero(tmp_path, canon):
    base = git(["rev-parse", "HEAD"], cwd=canon)
    spec = DogfoodSpec(canonical_checkout=canon, base_sha=base)  # model_calls not required
    roster = mock_roster(spec)
    roster["IMPLEMENTER"] = MockExecutor("executor:mock-codex", "mock-codex", {"IMPLEMENTER": implementer_ok},
                                         observability={**{d: OBSERVABLE for d in USAGE_DIMENSIONS}, "model_calls": UNOBSERVABLE, "cost_units": UNOBSERVABLE},
                                         usage={"IMPLEMENTER": {"executor_turns": 1, "model_calls": None, "token_units": 2.0, "cost_units": None}})
    rep = MissionController(spec, roster, run_root=tmp_path / "runs").execute()
    assert rep["disposition"] == DISPOSITION_QUALIFIED
    b = rep["budget"]
    assert b["budget_dimensions_observable"]["model_calls"] == "UNKNOWN"
    assert b["budget_dimensions_observable"]["token_units"] == "OBSERVED"
    assert "IMPLEMENTER" in " ".join(b["unknown_runs_by_dimension"]["model_calls"]).upper() or b["unknown_runs_by_dimension"]["model_calls"]


def test_wall_budget_exhausted_blocks_next_executor(tmp_path, canon):
    now = [1000.0]

    def clock():
        return now[0]

    def slow_coordinator(task):
        from sscm.testing import coordinator_ok
        now[0] += 120.0  # the coordinator alone consumes the whole wall budget
        return coordinator_ok(task)

    _, ctl = make(tmp_path, canon, budget=_budget(max_wall_seconds=100), clock=clock, coordinator=slow_coordinator)
    rep = ctl.execute()
    assert rep["disposition"] == DISPOSITION_BLOCKED
    assert any(b["code"] == "BUDGET_EXCEEDED" and "wall" in b["detail"] for b in rep["blockers"])
    assert "IMPLEMENTER" not in rep["runs"]  # never launched


def test_budget_report_fields_present(tmp_path, canon):
    _, ctl = make(tmp_path, canon)
    rep = ctl.execute()
    for k in ("observed_executor_runs", "observed_executor_turns", "observed_model_calls", "observed_cost_or_token_units",
              "observed_wall_seconds", "budget_limits", "budget_dimensions_observable", "budget_exceeded"):
        assert k in rep["budget"], k
    assert rep["budget"]["observed_executor_runs"] == 3
    assert rep["budget"]["budget_limits"]["required_observable_dimensions"] == ["executor_turns", "token_units", "wall_seconds"]


def test_usage_ledger_is_persisted_and_idempotent(tmp_path):
    bb = Blackboard(tmp_path / "u.sqlite")
    bb.create_mission("m", _budget(max_model_calls=2), {})
    obs = {d: OBSERVABLE for d in USAGE_DIMENSIONS}
    u = {"executor_turns": 1, "model_calls": 1, "token_units": 1.0, "cost_units": 0.0, "wall_seconds": 1.0}
    bb.record_usage("m", run_id="r1", role="X", executor_ref="e", usage=u, observability=obs)
    bb.record_usage("m", run_id="r1", role="X", executor_ref="e", usage=u, observability=obs)  # replay
    assert bb.usage_projection("m")["observed_executor_runs"] == 1
    with pytest.raises(BlackboardError) as ei:
        bb.record_usage("m", run_id="r1", role="X", executor_ref="e", usage={**u, "model_calls": 5}, observability=obs)
    assert ei.value.code == "USAGE_RUN_CONFLICT"
    bb.record_usage("m", run_id="r2", role="Y", executor_ref="e", usage=u, observability=obs)
    with pytest.raises(BudgetExceeded):
        bb.assert_headroom("m", elapsed_wall_seconds=0.0)  # at the model-call limit: no headroom for a third run
    bb.close()
    again = Blackboard(tmp_path / "u.sqlite")
    assert again.usage_projection("m")["observed_model_calls"] == 2


def test_event_budget_cannot_self_raise_is_preserved(tmp_path):
    from sscm.contracts import new_event
    bb = Blackboard(tmp_path / "e.sqlite")
    b = _budget()
    bb.create_mission("m", b, {})
    e = new_event(mission_id="m", task_id="t", verb="PUBLISH", actor_ref="a", status="PENDING", budget=b.event_budget())
    e["budget"]["max_model_calls"] = 9999
    with pytest.raises(BudgetExceeded) as ei:
        bb.append(e)
    assert ei.value.dimension == "max_model_calls"


# ---------------------------------------------------------------------------
# PART C — credential-safe terminalization
# ---------------------------------------------------------------------------


def test_secret_only_in_provider_log_blocks_before_terminal_success(tmp_path, canon):
    base = git(["rev-parse", "HEAD"], cwd=canon)
    spec = DogfoodSpec(canonical_checkout=canon, base_sha=base)
    roster = mock_roster(spec)
    leaky_log = {"REVIEWER": lambda task: "provider chatter... token=ghp_" + "A" * 36 + "\n"}
    roster["REVIEWER"] = MockExecutor("executor:mock-claude", "mock-claude", {"REVIEWER": reviewer_ok}, log_writer=leaky_log)
    rep = MissionController(spec, roster, run_root=tmp_path / "runs").execute()
    assert rep["credential_scan"]["result"] == "REJECTED"
    assert rep["mission_terminal_status"] == "BLOCKED"
    assert rep["mission_terminal_status"] != "SUCCEEDED"
    assert rep["experience_created"] is False and "experience" not in rep
    assert rep["disposition"] == DISPOSITION_BLOCKED
    verbs = [e["verb"] for e in rep["events"]]
    assert verbs[-1] == "REJECT" and "COMPLETE" in verbs  # blocked outcome recorded; success never appended
    bb = Blackboard(rep["blackboard_path"])
    assert bb.project(rep["mission_id"]).status == "BLOCKED"
    assert not (Path(rep["run_dir"]) / "artifacts" / "experience.json").exists()


def test_clean_mission_scans_before_terminal_success(tmp_path, canon):
    _, ctl = make(tmp_path, canon)
    rep = ctl.execute()
    assert rep["credential_scan"]["order"] == "BEFORE_TERMINAL_SUCCESS"
    assert rep["mission_terminal_status"] == "SUCCEEDED" and rep["experience_created"] is True


def test_blocked_mission_with_clean_evidence_still_yields_blocked_experience(tmp_path, canon):
    def failing(task):
        return {"role": "IMPLEMENTER", "status": "FAILED", "claimed_candidate_revision": None, "changed_files": [],
                "tests_run": [], "blockers": ["simulated"], "summary": "gave up", "authority": "NONE"}
    _, ctl = make(tmp_path, canon, implementer=failing)
    rep = ctl.execute()
    assert rep["mission_terminal_status"] == "BLOCKED" and rep["experience_created"] is True
    assert rep["experience"]["outcome"] == "BLOCKED" and rep["experience"]["authority"] == "NONE"
