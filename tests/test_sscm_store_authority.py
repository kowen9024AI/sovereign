"""WO-SOVEREIGN-SSCM-01C-R1: Store-Level Repair Authority & Terminal Review Candidate Custody Tests."""

import json
from pathlib import Path
import pytest

from sscm.artifacts import RunDir
from sscm.blackboard import (
    Blackboard,
    BlackboardError,
    MissionBudget,
    RetryActorNotAuthorized,
    RetryAuthorityUnconfigured,
    RetryCandidateMismatch,
    RetryTriggerInvalid,
    TerminalCandidateReviewMismatch,
)
from sscm.contracts import EVENT_V2_SCHEMA_VERSION, new_event
from sscm.repair import RepairDogfoodSpec, repair_projection

SHA_A = "1111111111111111111111111111111111111111"
SHA_B = "2222222222222222222222222222222222222222"
FINAL_SHA = "3333333333333333333333333333333333333333"

BUDGET_REPAIR = MissionBudget(
    max_active_actors=2,
    max_coordination_transitions=16,
    max_turns=40,
    max_model_calls=60,
    max_wall_seconds=1800,
    max_repair_loops=1,
    max_consecutive_failures=2,
    max_token_or_cost_units=400.0,
)


def make_spec(repair_policy=None, required_terminal_verifications=None, **kw):
    spec = {
        "repair_policy": repair_policy or {
            "authority_actor_ref": "host:sscm-mission-controller",
            "trigger_actor_ref": "evaluator:sscm-holdout-v0.1",
            "trigger_verb": "REJECT",
            "trigger_status": "FAILED",
        },
        "required_terminal_verifications": (
            required_terminal_verifications
            if required_terminal_verifications is not None
            else [
                {
                    "task_id": "task:review",
                    "verb": "OBSERVE",
                    "status": "SUCCEEDED",
                    "actor_ref": "host:sscm-mission-controller",
                    "bind_terminal_candidate": True,
                }
            ]
        ),
        "required_terminal_tasks": ["task:review"],
    }
    spec.update(kw)
    return spec


def ev(mission="m1", task="task:implement", verb="PUBLISH", actor="actor:coordinator", status="PENDING", pred=None, budget=None, **kw):
    preds = [pred] if isinstance(pred, str) else (pred or None)
    return new_event(
        mission_id=mission,
        task_id=task,
        verb=verb,
        actor_ref=actor,
        status=status,
        budget=(budget or BUDGET_REPAIR).event_budget(EVENT_V2_SCHEMA_VERSION),
        schema_version=EVENT_V2_SCHEMA_VERSION,
        predecessor_event_ids=preds,
        **kw,
    )


# ==============================================================================
# PART B & D: STORE-LEVEL RETRY AUTHORITY ATTACK & CONTROL TESTS
# ==============================================================================


def test_retry_authority_unconfigured(tmp_path):
    """Section 9: RETRY attempted when mission has no configured repair authority -> fail closed."""
    bb = Blackboard(tmp_path / "bb.sqlite")
    # Mission without repair_policy in spec
    bb.create_mission("m1", BUDGET_REPAIR, {"kind": "unconfigured"})
    p = bb.append(ev(mission="m1", task="task:implement", verb="PUBLISH", actor="actor:coordinator", status="PENDING"))
    c = bb.append(ev(mission="m1", task="task:implement", verb="CLAIM", actor="actor:implementer-1", status="ACTIVE", pred=p["event_id"]))
    comp = bb.append(ev(mission="m1", task="task:implement", verb="COMPLETE", actor="actor:implementer-1", status="SUCCEEDED", candidate_revision=SHA_A, pred=c["event_id"]))
    obs = bb.append(ev(mission="m1", task="task:implement", verb="OBSERVE", actor="host:sscm-mission-controller", status="SUCCEEDED", candidate_revision=SHA_A, pred=comp["event_id"]))
    rej = bb.append(ev(mission="m1", task="task:implement", verb="REJECT", actor="evaluator:sscm-holdout-v0.1", status="FAILED", candidate_revision=SHA_A, pred=obs["event_id"]))

    with pytest.raises(RetryAuthorityUnconfigured) as ei:
        bb.append(ev(mission="m1", task="task:implement", verb="RETRY", actor="host:sscm-mission-controller", status="PENDING", candidate_revision=SHA_A, pred=rej["event_id"]))
    assert ei.value.code == "RETRY_AUTHORITY_UNCONFIGURED"
    bb.close()


def test_evaluator_self_retry_rejected(tmp_path):
    """Section 22: Evaluator attempts RETRY -> reject, no task reopening, no repair counted."""
    bb = Blackboard(tmp_path / "bb.sqlite")
    bb.create_mission("m1", BUDGET_REPAIR, make_spec())
    p = bb.append(ev(mission="m1", task="task:implement", verb="PUBLISH", actor="actor:coordinator", status="PENDING"))
    c = bb.append(ev(mission="m1", task="task:implement", verb="CLAIM", actor="actor:implementer-1", status="ACTIVE", pred=p["event_id"]))
    comp = bb.append(ev(mission="m1", task="task:implement", verb="COMPLETE", actor="actor:implementer-1", status="SUCCEEDED", candidate_revision=SHA_A, pred=c["event_id"]))
    obs = bb.append(ev(mission="m1", task="task:implement", verb="OBSERVE", actor="host:sscm-mission-controller", status="SUCCEEDED", candidate_revision=SHA_A, pred=comp["event_id"]))
    rej = bb.append(ev(mission="m1", task="task:implement", verb="REJECT", actor="evaluator:sscm-holdout-v0.1", status="FAILED", candidate_revision=SHA_A, pred=obs["event_id"]))

    with pytest.raises(RetryActorNotAuthorized) as ei:
        bb.append(ev(mission="m1", task="task:implement", verb="RETRY", actor="evaluator:sscm-holdout-v0.1", status="PENDING", candidate_revision=SHA_A, pred=rej["event_id"]))
    assert ei.value.code == "RETRY_ACTOR_NOT_AUTHORIZED"

    # Task was NOT reopened: claim holder remains implementer-1
    assert bb.claim_holder("m1", "task:implement") == "actor:implementer-1"
    # No RETRY in events
    assert "RETRY" not in [e["verb"] for e in bb.events("m1")]
    bb.close()


def test_implementer_self_retry_rejected(tmp_path):
    """Section 23: Implementer attempts RETRY -> reject."""
    bb = Blackboard(tmp_path / "bb.sqlite")
    bb.create_mission("m1", BUDGET_REPAIR, make_spec())
    p = bb.append(ev(mission="m1", task="task:implement", verb="PUBLISH", actor="actor:coordinator", status="PENDING"))
    c = bb.append(ev(mission="m1", task="task:implement", verb="CLAIM", actor="actor:implementer-1", status="ACTIVE", pred=p["event_id"]))
    comp = bb.append(ev(mission="m1", task="task:implement", verb="COMPLETE", actor="actor:implementer-1", status="SUCCEEDED", candidate_revision=SHA_A, pred=c["event_id"]))
    obs = bb.append(ev(mission="m1", task="task:implement", verb="OBSERVE", actor="host:sscm-mission-controller", status="SUCCEEDED", candidate_revision=SHA_A, pred=comp["event_id"]))
    rej = bb.append(ev(mission="m1", task="task:implement", verb="REJECT", actor="evaluator:sscm-holdout-v0.1", status="FAILED", candidate_revision=SHA_A, pred=obs["event_id"]))

    with pytest.raises(RetryActorNotAuthorized) as ei:
        bb.append(ev(mission="m1", task="task:implement", verb="RETRY", actor="actor:implementer-1", status="PENDING", candidate_revision=SHA_A, pred=rej["event_id"]))
    assert ei.value.code == "RETRY_ACTOR_NOT_AUTHORIZED"
    bb.close()


def test_wrong_host_retry_rejected(tmp_path):
    """Section 24: Non-configured host actor attempts RETRY -> reject."""
    bb = Blackboard(tmp_path / "bb.sqlite")
    bb.create_mission("m1", BUDGET_REPAIR, make_spec())
    p = bb.append(ev(mission="m1", task="task:implement", verb="PUBLISH", actor="actor:coordinator", status="PENDING"))
    c = bb.append(ev(mission="m1", task="task:implement", verb="CLAIM", actor="actor:implementer-1", status="ACTIVE", pred=p["event_id"]))
    comp = bb.append(ev(mission="m1", task="task:implement", verb="COMPLETE", actor="actor:implementer-1", status="SUCCEEDED", candidate_revision=SHA_A, pred=c["event_id"]))
    obs = bb.append(ev(mission="m1", task="task:implement", verb="OBSERVE", actor="host:sscm-mission-controller", status="SUCCEEDED", candidate_revision=SHA_A, pred=comp["event_id"]))
    rej = bb.append(ev(mission="m1", task="task:implement", verb="REJECT", actor="evaluator:sscm-holdout-v0.1", status="FAILED", candidate_revision=SHA_A, pred=obs["event_id"]))

    with pytest.raises(RetryActorNotAuthorized) as ei:
        bb.append(ev(mission="m1", task="task:implement", verb="RETRY", actor="host:another-controller", status="PENDING", candidate_revision=SHA_A, pred=rej["event_id"]))
    assert ei.value.code == "RETRY_ACTOR_NOT_AUTHORIZED"
    bb.close()


def test_no_fail_retry_rejected(tmp_path):
    """Section 25: Host RETRY without configured failure trigger -> reject."""
    bb = Blackboard(tmp_path / "bb.sqlite")
    bb.create_mission("m1", BUDGET_REPAIR, make_spec())
    p = bb.append(ev(mission="m1", task="task:implement", verb="PUBLISH", actor="actor:coordinator", status="PENDING"))
    c = bb.append(ev(mission="m1", task="task:implement", verb="CLAIM", actor="actor:implementer-1", status="ACTIVE", pred=p["event_id"]))
    comp = bb.append(ev(mission="m1", task="task:implement", verb="COMPLETE", actor="actor:implementer-1", status="SUCCEEDED", candidate_revision=SHA_A, pred=c["event_id"]))
    # Predecessor is COMPLETE (or OBSERVE PASS), not evaluator REJECT/FAILED
    with pytest.raises(BlackboardError) as ei:
        bb.append(ev(mission="m1", task="task:implement", verb="RETRY", actor="host:sscm-mission-controller", status="PENDING", candidate_revision=SHA_A, pred=comp["event_id"]))
    assert ei.value.code == "RETRY_TRIGGER_INVALID"
    bb.close()


def test_wrong_evaluator_retry_rejected(tmp_path):
    """Section 26: Host RETRY after rejection from non-configured evaluator -> reject."""
    bb = Blackboard(tmp_path / "bb.sqlite")
    bb.create_mission("m1", BUDGET_REPAIR, make_spec())
    p = bb.append(ev(mission="m1", task="task:implement", verb="PUBLISH", actor="actor:coordinator", status="PENDING"))
    c = bb.append(ev(mission="m1", task="task:implement", verb="CLAIM", actor="actor:implementer-1", status="ACTIVE", pred=p["event_id"]))
    comp = bb.append(ev(mission="m1", task="task:implement", verb="COMPLETE", actor="actor:implementer-1", status="SUCCEEDED", candidate_revision=SHA_A, pred=c["event_id"]))
    obs = bb.append(ev(mission="m1", task="task:implement", verb="OBSERVE", actor="host:sscm-mission-controller", status="SUCCEEDED", candidate_revision=SHA_A, pred=comp["event_id"]))
    # REJECT from evaluator:other instead of evaluator:sscm-holdout-v0.1
    rej = bb.append(ev(mission="m1", task="task:implement", verb="REJECT", actor="evaluator:other", status="FAILED", candidate_revision=SHA_A, pred=obs["event_id"]))

    with pytest.raises(RetryTriggerInvalid) as ei:
        bb.append(ev(mission="m1", task="task:implement", verb="RETRY", actor="host:sscm-mission-controller", status="PENDING", candidate_revision=SHA_A, pred=rej["event_id"]))
    assert ei.value.code == "RETRY_TRIGGER_INVALID"
    bb.close()


def test_retry_candidate_mismatch_rejected(tmp_path):
    """Section 27: Host RETRY candidate does not match trigger candidate -> reject."""
    bb = Blackboard(tmp_path / "bb.sqlite")
    bb.create_mission("m1", BUDGET_REPAIR, make_spec())
    p = bb.append(ev(mission="m1", task="task:implement", verb="PUBLISH", actor="actor:coordinator", status="PENDING"))
    c = bb.append(ev(mission="m1", task="task:implement", verb="CLAIM", actor="actor:implementer-1", status="ACTIVE", pred=p["event_id"]))
    comp = bb.append(ev(mission="m1", task="task:implement", verb="COMPLETE", actor="actor:implementer-1", status="SUCCEEDED", candidate_revision=SHA_A, pred=c["event_id"]))
    obs = bb.append(ev(mission="m1", task="task:implement", verb="OBSERVE", actor="host:sscm-mission-controller", status="SUCCEEDED", candidate_revision=SHA_A, pred=comp["event_id"]))
    rej = bb.append(ev(mission="m1", task="task:implement", verb="REJECT", actor="evaluator:sscm-holdout-v0.1", status="FAILED", candidate_revision=SHA_A, pred=obs["event_id"]))

    with pytest.raises(RetryCandidateMismatch) as ei:
        bb.append(ev(mission="m1", task="task:implement", verb="RETRY", actor="host:sscm-mission-controller", status="PENDING", candidate_revision=SHA_B, pred=rej["event_id"]))
    assert ei.value.code == "RETRY_CANDIDATE_MISMATCH"
    bb.close()


def test_valid_host_retry_admitted(tmp_path):
    """Section 28: Valid host RETRY following evaluator FAIL -> admitted, task reopened."""
    bb = Blackboard(tmp_path / "bb.sqlite")
    bb.create_mission("m1", BUDGET_REPAIR, make_spec())
    p = bb.append(ev(mission="m1", task="task:implement", verb="PUBLISH", actor="actor:coordinator", status="PENDING"))
    c = bb.append(ev(mission="m1", task="task:implement", verb="CLAIM", actor="actor:implementer-1", status="ACTIVE", pred=p["event_id"]))
    comp = bb.append(ev(mission="m1", task="task:implement", verb="COMPLETE", actor="actor:implementer-1", status="SUCCEEDED", candidate_revision=SHA_A, pred=c["event_id"]))
    obs = bb.append(ev(mission="m1", task="task:implement", verb="OBSERVE", actor="host:sscm-mission-controller", status="SUCCEEDED", candidate_revision=SHA_A, pred=comp["event_id"]))
    rej = bb.append(ev(mission="m1", task="task:implement", verb="REJECT", actor="evaluator:sscm-holdout-v0.1", status="FAILED", candidate_revision=SHA_A, pred=obs["event_id"]))

    retry = bb.append(ev(mission="m1", task="task:implement", verb="RETRY", actor="host:sscm-mission-controller", status="PENDING", candidate_revision=SHA_A, pred=rej["event_id"]))
    assert retry["verb"] == "RETRY" and retry["status"] == "PENDING"
    assert retry["actor_ref"] == "host:sscm-mission-controller"
    # Task claim was cleared: fresh implementer can CLAIM
    assert bb.claim_holder("m1", "task:implement") is None
    c2 = bb.append(ev(mission="m1", task="task:implement", verb="CLAIM", actor="actor:implementer-2", status="ACTIVE", pred=retry["event_id"]))
    assert c2["verb"] == "CLAIM"
    assert bb.claim_holder("m1", "task:implement") == "actor:implementer-2"
    bb.close()


# ==============================================================================
# PART C: TERMINAL REVIEW AUTHORITY & CANDIDATE BINDING TESTS
# ==============================================================================


def _setup_to_review(bb, mission_id="m1", candidate=FINAL_SHA):
    p = bb.append(ev(mission=mission_id, task="task:implement", verb="PUBLISH", actor="actor:coordinator", status="PENDING"))
    c = bb.append(ev(mission=mission_id, task="task:implement", verb="CLAIM", actor="actor:implementer", status="ACTIVE", pred=p["event_id"]))
    comp = bb.append(ev(mission=mission_id, task="task:implement", verb="COMPLETE", actor="actor:implementer", status="SUCCEEDED", candidate_revision=candidate, pred=c["event_id"]))
    obs = bb.append(ev(mission=mission_id, task="task:implement", verb="OBSERVE", actor="host:sscm-mission-controller", status="SUCCEEDED", candidate_revision=candidate, pred=comp["event_id"]))
    ev_obs = bb.append(ev(mission=mission_id, task="task:implement", verb="OBSERVE", actor="evaluator:sscm-holdout-v0.1", status="SUCCEEDED", candidate_revision=candidate, pred=obs["event_id"]))
    r_claim = bb.append(ev(mission=mission_id, task="task:review", verb="CLAIM", actor="actor:reviewer", status="ACTIVE", candidate_revision=candidate, pred=ev_obs["event_id"]))
    r_comp = bb.append(ev(mission=mission_id, task="task:review", verb="COMPLETE", actor="actor:reviewer", status="SUCCEEDED", candidate_revision=candidate, pred=r_claim["event_id"]))
    return r_comp


def test_non_host_review_terminal_rejected(tmp_path):
    """Section 18: Non-host review OBSERVE -> terminal reject (FINAL_REVIEW_REQUIRED)."""
    bb = Blackboard(tmp_path / "bb.sqlite")
    bb.create_mission("m1", BUDGET_REPAIR, make_spec())
    r_comp = _setup_to_review(bb, "m1", FINAL_SHA)

    # Review OBSERVE authored by evaluator:test instead of host
    r_obs = bb.append(ev(mission="m1", task="task:review", verb="OBSERVE", actor="evaluator:test", status="SUCCEEDED", candidate_revision=FINAL_SHA, pred=r_comp["event_id"]))

    with pytest.raises(BlackboardError) as ei:
        bb.append(ev(mission="m1", task="m1", verb="COMPLETE", actor="host:sscm-mission-controller", status="SUCCEEDED", candidate_revision=FINAL_SHA, pred=r_obs["event_id"]))
    assert ei.value.code == "FINAL_REVIEW_REQUIRED"
    bb.close()


def test_wrong_host_review_terminal_rejected(tmp_path):
    """Section 19: Wrong host review OBSERVE -> terminal reject (FINAL_REVIEW_REQUIRED)."""
    bb = Blackboard(tmp_path / "bb.sqlite")
    bb.create_mission("m1", BUDGET_REPAIR, make_spec())
    r_comp = _setup_to_review(bb, "m1", FINAL_SHA)

    # Review OBSERVE authored by host:other-controller instead of configured host
    r_obs = bb.append(ev(mission="m1", task="task:review", verb="OBSERVE", actor="host:other-controller", status="SUCCEEDED", candidate_revision=FINAL_SHA, pred=r_comp["event_id"]))

    with pytest.raises(BlackboardError) as ei:
        bb.append(ev(mission="m1", task="m1", verb="COMPLETE", actor="host:sscm-mission-controller", status="SUCCEEDED", candidate_revision=FINAL_SHA, pred=r_obs["event_id"]))
    assert ei.value.code == "FINAL_REVIEW_REQUIRED"
    bb.close()


def test_stale_review_sha_terminal_rejected(tmp_path):
    """Section 20: Stale review SHA -> terminal reject (TERMINAL_CANDIDATE_REVIEW_MISMATCH)."""
    bb = Blackboard(tmp_path / "bb.sqlite")
    bb.create_mission("m1", BUDGET_REPAIR, make_spec())
    r_comp = _setup_to_review(bb, "m1", SHA_A)

    # Valid host review for SHA_A
    r_obs = bb.append(ev(mission="m1", task="task:review", verb="OBSERVE", actor="host:sscm-mission-controller", status="SUCCEEDED", candidate_revision=SHA_A, pred=r_comp["event_id"]))

    # Mission COMPLETE attempts to bind SHA_B
    with pytest.raises(TerminalCandidateReviewMismatch) as ei:
        bb.append(ev(mission="m1", task="m1", verb="COMPLETE", actor="host:sscm-mission-controller", status="SUCCEEDED", candidate_revision=SHA_B, pred=r_obs["event_id"]))
    assert ei.value.code == "TERMINAL_CANDIDATE_REVIEW_MISMATCH"
    bb.close()


def test_valid_host_final_review_terminal_admitted(tmp_path):
    """Section 21: Valid host review and matching candidate -> terminal SUCCEEDED."""
    bb = Blackboard(tmp_path / "bb.sqlite")
    bb.create_mission("m1", BUDGET_REPAIR, make_spec())
    r_comp = _setup_to_review(bb, "m1", FINAL_SHA)

    r_obs = bb.append(ev(mission="m1", task="task:review", verb="OBSERVE", actor="host:sscm-mission-controller", status="SUCCEEDED", candidate_revision=FINAL_SHA, pred=r_comp["event_id"]))
    m_comp = bb.append(ev(mission="m1", task="m1", verb="COMPLETE", actor="host:sscm-mission-controller", status="SUCCEEDED", candidate_revision=FINAL_SHA, pred=r_obs["event_id"]))

    assert m_comp["verb"] == "COMPLETE" and m_comp["status"] == "SUCCEEDED"
    proj = bb.project("m1")
    assert proj.status == "SUCCEEDED"
    bb.close()


# ==============================================================================
# REPAIR PROJECTION AUTHORITY FILTERING TEST
# ==============================================================================


def test_repair_projection_authority_filtered(tmp_path):
    """Section 13: repair_projection counts only RETRY satisfying configured repair authority."""
    bb = Blackboard(tmp_path / "bb.sqlite")
    rd = RunDir("m1", tmp_path)
    bb.create_mission("m1", BUDGET_REPAIR, make_spec())

    p = bb.append(ev(mission="m1", task="task:implement", verb="PUBLISH", actor="actor:coordinator", status="PENDING"))
    c = bb.append(ev(mission="m1", task="task:implement", verb="CLAIM", actor="actor:implementer-1", status="ACTIVE", pred=p["event_id"]))
    comp = bb.append(ev(mission="m1", task="task:implement", verb="COMPLETE", actor="actor:implementer-1", status="SUCCEEDED", candidate_revision=SHA_A, pred=c["event_id"]))
    obs = bb.append(ev(mission="m1", task="task:implement", verb="OBSERVE", actor="host:sscm-mission-controller", status="SUCCEEDED", candidate_revision=SHA_A, pred=comp["event_id"]))
    rej = bb.append(ev(mission="m1", task="task:implement", verb="REJECT", actor="evaluator:sscm-holdout-v0.1", status="FAILED", candidate_revision=SHA_A, pred=obs["event_id"]))

    # Before RETRY
    proj_before = repair_projection(bb, rd, "m1")
    assert proj_before["repair_attempts_authorized"] == 0

    # Authorized host RETRY
    bb.append(ev(mission="m1", task="task:implement", verb="RETRY", actor="host:sscm-mission-controller", status="PENDING", candidate_revision=SHA_A, pred=rej["event_id"]))

    proj_after = repair_projection(bb, rd, "m1")
    assert proj_after["repair_attempts_authorized"] == 1
    bb.close()


# ==============================================================================
# HISTORICAL LIVE 01C REPLAY TEST
# ==============================================================================


def test_historical_live_01c_event_replay(tmp_path):
    """Section 31: Replay historical live 01C events through repaired blackboard."""
    evidence_path = Path("docs/work-orders/WO-SOVEREIGN-SSCM-01C-evidence/blackboard-events.json")
    assert evidence_path.is_file(), f"missing live evidence at {evidence_path}"
    events = json.load(evidence_path.open())
    assert len(events) == 14, f"expected 14 live events, got {len(events)}"

    # Replay through fresh blackboard with repaired structured spec
    bb = Blackboard(tmp_path / "replay.sqlite")
    spec = RepairDogfoodSpec(canonical_checkout=tmp_path, base_sha="c7a4b16bc0b986872a08990d71597d39352e8055").as_dict()
    budget = MissionBudget(
        max_active_actors=1,
        max_coordination_transitions=14,
        max_turns=48,
        max_model_calls=48,
        max_wall_seconds=1800,
        max_repair_loops=1,
        max_consecutive_failures=2,
        max_token_or_cost_units=800.0,
        required_observable_dimensions=("executor_turns", "token_units", "wall_seconds"),
    )
    bb.create_mission("mission-sscm-01c-live-1", budget, spec)

    for i, e in enumerate(events):
        res = bb.append(e)
        assert res["event_id"] == e["event_id"]

    # Invariants per Section 31
    retry_event = events[5]
    reject_event = events[4]
    final_review_obs = events[12]
    mission_complete = events[13]

    assert retry_event["verb"] == "RETRY"
    assert retry_event["actor_ref"] == "host:sscm-mission-controller"

    assert reject_event["verb"] == "REJECT"
    assert reject_event["status"] == "FAILED"
    assert reject_event["actor_ref"] == "evaluator:sscm-holdout-v0.1"

    assert retry_event["candidate_revision"] == reject_event["candidate_revision"]
    assert len(retry_event["candidate_revision"]) == 40

    assert final_review_obs["verb"] == "OBSERVE"
    assert final_review_obs["task_id"] == "task:review"
    assert final_review_obs["actor_ref"] == "host:sscm-mission-controller"
    assert final_review_obs["status"] == "SUCCEEDED"

    assert mission_complete["verb"] == "COMPLETE"
    assert mission_complete["status"] == "SUCCEEDED"
    assert final_review_obs["candidate_revision"] == mission_complete["candidate_revision"]
    assert len(mission_complete["candidate_revision"]) == 40

    proj = bb.project("mission-sscm-01c-live-1")
    assert proj.status == "SUCCEEDED"
    assert proj.event_count == 14
    bb.close()
