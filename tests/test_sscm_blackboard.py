"""Blackboard: append-only, idempotent, predecessor-validated, exclusive claims, budgets, restart, determinism."""

import json
import sqlite3
import threading
from pathlib import Path

import pytest

from sscm.blackboard import (
    Blackboard,
    BlackboardError,
    BudgetExceeded,
    ClaimConflict,
    EventIdConflict,
    MissionBudget,
    MissionUnknown,
    PredecessorInvalid,
    causal_order,
)
from sscm.contracts import ContractViolation, new_event

B = MissionBudget(max_active_actors=1, max_turns=6, max_repair_loops=0, max_consecutive_failures=1)


@pytest.fixture
def bb(tmp_path):
    store = Blackboard(tmp_path / "bb.sqlite")
    store.create_mission("m1", B, {"kind": "test"})
    yield store
    store.close()


def ev(mission="m1", task="t1", verb="PUBLISH", actor="a", status="PENDING", pred=None, budget=None, **kw):
    return new_event(mission_id=mission, task_id=task, verb=verb, actor_ref=actor, status=status,
                     budget=(budget or B).event_budget(), predecessor_event_id=pred, **kw)


# -- idempotency ---------------------------------------------------------------


def test_same_id_same_payload_is_idempotent(bb):
    e = ev()
    a = bb.append(e)
    b = bb.append(json.loads(json.dumps(e)))
    assert a == b
    assert len(bb.events("m1")) == 1


def test_same_id_different_payload_is_conflict_not_winner(bb):
    e = ev()
    bb.append(e)
    e2 = dict(e, actor_ref="someone-else")
    with pytest.raises(EventIdConflict):
        bb.append(e2)
    assert bb.get_event(e["event_id"])["actor_ref"] == "a"  # original untouched


def test_stored_payload_is_never_rewritten(bb):
    e = bb.append(ev())
    dig = bb.stored_digest(e["event_id"])
    with pytest.raises(EventIdConflict):
        bb.append(dict(e, status="SUCCEEDED"))
    assert bb.stored_digest(e["event_id"]) == dig
    # no UPDATE path exists at all: the only writer is append()
    assert not hasattr(bb, "update_event")


# -- predecessor validation ---------------------------------------------------------


def test_missing_predecessor_rejected(bb):
    with pytest.raises(PredecessorInvalid) as ei:
        bb.append(ev(verb="CLAIM", status="ACTIVE", pred="evt-does-not-exist"))
    assert ei.value.code == "PREDECESSOR_MISSING"


def test_self_predecessor_rejected(bb):
    e = ev(verb="CLAIM", status="ACTIVE")
    e["predecessor_event_id"] = e["event_id"]
    with pytest.raises(PredecessorInvalid) as ei:
        bb.append(e)
    assert ei.value.code == "PREDECESSOR_SELF"


def test_cross_mission_predecessor_rejected(bb):
    bb.create_mission("m2", B, {})
    p = bb.append(ev(mission="m2"))
    with pytest.raises(PredecessorInvalid) as ei:
        bb.append(ev(mission="m1", verb="CLAIM", status="ACTIVE", pred=p["event_id"]))
    assert ei.value.code == "PREDECESSOR_CROSS_MISSION"


def test_transition_must_be_allowed(bb):
    p = bb.append(ev())  # PUBLISH
    with pytest.raises(PredecessorInvalid) as ei:
        bb.append(ev(verb="RETRY", status="PENDING", pred=p["event_id"]))  # RETRY cannot follow PUBLISH
    assert ei.value.code == "PREDECESSOR_TRANSITION_INVALID"
    with pytest.raises(PredecessorInvalid):
        bb.append(ev(verb="CLAIM", status="ACTIVE"))  # CLAIM needs a predecessor


def test_history_may_branch_across_tasks(bb):
    p = bb.append(ev(task="t1"))
    c1 = bb.append(ev(task="t1", verb="CLAIM", status="ACTIVE", pred=p["event_id"]))
    o = bb.append(ev(task="t1", verb="OBSERVE", status="SUCCEEDED", actor="host", pred=c1["event_id"]))
    # t2 branches from t1's OBSERVE while a second, unrelated PUBLISH also hangs off nothing: no global linear chain
    bb.append(ev(task="t3", actor="c"))
    c2 = bb.append(ev(task="t2", verb="CLAIM", status="ACTIVE", actor="b", pred=o["event_id"]))
    proj = bb.project("m1")
    assert set(proj.tasks) == {"t1", "t2", "t3"}
    assert proj.tasks["t1"].claimed_by == "a" and proj.tasks["t2"].claimed_by == "b"


# -- claims -----------------------------------------------------------------------------


def test_claim_collision_deterministic(bb):
    p = bb.append(ev())
    bb.append(ev(verb="CLAIM", status="ACTIVE", actor="a", pred=p["event_id"]))
    with pytest.raises(ClaimConflict) as ei:
        bb.append(ev(verb="CLAIM", status="ACTIVE", actor="b", pred=p["event_id"]))
    assert ei.value.holder == "a"
    assert bb.claim_holder("m1", "t1") == "a"


def test_concurrent_exclusive_claim_exactly_one_winner(tmp_path):
    path = tmp_path / "c.sqlite"
    seed = Blackboard(path)
    seed.create_mission("m1", MissionBudget(max_active_actors=2, max_turns=10), {})
    p = seed.append(ev())
    seed.close()
    results: dict[str, str] = {}
    barrier = threading.Barrier(2)

    def worker(actor: str) -> None:
        store = Blackboard(path)
        try:
            barrier.wait()
            store.append(ev(verb="CLAIM", status="ACTIVE", actor=actor, pred=p["event_id"]))
            results[actor] = "WON"
        except ClaimConflict:
            results[actor] = "CLAIM_CONFLICT"
        finally:
            store.close()

    threads = [threading.Thread(target=worker, args=(a,)) for a in ("alpha", "beta")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results.values()) == ["CLAIM_CONFLICT", "WON"]
    check = Blackboard(path)
    assert check.claim_holder("m1", "t1") == next(a for a, r in results.items() if r == "WON")
    assert sum(1 for e in check.events("m1") if e["verb"] == "CLAIM") == 1


# -- budgets ------------------------------------------------------------------------------


def test_budget_max_turns_blocks_deterministically(tmp_path):
    tight = MissionBudget(max_turns=1, max_active_actors=3)
    bb = Blackboard(tmp_path / "b.sqlite")
    bb.create_mission("m", tight, {})
    p = bb.append(ev(mission="m", budget=tight))
    bb.append(ev(mission="m", verb="CLAIM", status="ACTIVE", pred=p["event_id"], budget=tight))
    with pytest.raises(BudgetExceeded) as ei:
        bb.append(ev(mission="m", task="t2", verb="CLAIM", status="ACTIVE", actor="b", pred=p["event_id"], budget=tight))
    assert ei.value.dimension == "max_turns"


def test_budget_max_active_actors(bb):
    p = bb.append(ev())
    bb.append(ev(task="t1", verb="CLAIM", status="ACTIVE", actor="a", pred=p["event_id"]))
    with pytest.raises(BudgetExceeded) as ei:
        bb.append(ev(task="t2", verb="CLAIM", status="ACTIVE", actor="b", pred=p["event_id"]))
    assert ei.value.dimension == "max_active_actors"


def test_repair_loops_zero_blocks_retry(bb):
    p = bb.append(ev())
    c = bb.append(ev(verb="CLAIM", status="ACTIVE", pred=p["event_id"]))
    r = bb.append(ev(verb="REJECT", status="FAILED", actor="host", pred=c["event_id"]))
    with pytest.raises(BudgetExceeded) as ei:
        bb.append(ev(verb="RETRY", status="PENDING", pred=r["event_id"]))
    assert ei.value.dimension in ("max_repair_loops", "max_consecutive_failures")


def test_actor_cannot_raise_its_own_budget(bb):
    e = ev()
    e["budget"]["max_turns"] = 999
    with pytest.raises(BudgetExceeded) as ei:
        bb.append(e)
    assert ei.value.dimension == "max_turns"


# -- terminal / mission rules -----------------------------------------------------------------


def test_terminal_mission_before_review_fails_closed(bb):
    p = bb.append(ev())
    c = bb.append(ev(verb="CLAIM", status="ACTIVE", pred=p["event_id"]))
    with pytest.raises(BlackboardError) as ei:
        bb.append(ev(task="m1", verb="COMPLETE", status="SUCCEEDED", actor="host", pred=c["event_id"]))
    assert ei.value.code == "MISSION_COMPLETE_UNOBSERVED"


def test_nothing_follows_a_terminal_mission(bb):
    p = bb.append(ev())
    c = bb.append(ev(verb="CLAIM", status="ACTIVE", pred=p["event_id"]))
    d = bb.append(ev(verb="COMPLETE", status="SUCCEEDED", pred=c["event_id"]))
    o = bb.append(ev(verb="OBSERVE", status="SUCCEEDED", actor="host", pred=d["event_id"]))
    bb.append(ev(task="m1", verb="COMPLETE", status="SUCCEEDED", actor="host", pred=o["event_id"]))
    assert bb.project("m1").status == "SUCCEEDED"
    with pytest.raises(BlackboardError) as ei:
        bb.append(ev(task="t9", actor="late"))
    assert ei.value.code == "MISSION_TERMINAL"


def test_unknown_mission_and_invalid_schema(bb):
    with pytest.raises(MissionUnknown):
        bb.append(ev(mission="nope"))
    e = ev()
    e["status"] = "APPROVED"
    with pytest.raises(ContractViolation):
        bb.append(e)


# -- restart + determinism ------------------------------------------------------------------------


def test_state_survives_restart(tmp_path):
    path = tmp_path / "r.sqlite"
    bb = Blackboard(path)
    bb.create_mission("m1", B, {"k": 1})
    p = bb.append(ev())
    c = bb.append(ev(verb="CLAIM", status="ACTIVE", pred=p["event_id"]))
    bb.append(ev(verb="COMPLETE", status="SUCCEEDED", pred=c["event_id"]))
    before = bb.project("m1").as_dict()
    events_before = bb.events("m1")
    bb.close()
    del bb
    again = Blackboard(path)
    assert again.project("m1").as_dict() == before
    assert again.events("m1") == events_before
    assert again.mission("m1")["spec"] == {"k": 1}


def test_projection_is_independent_of_row_order(tmp_path):
    """Same causal structure, different insertion order -> identical projection."""
    def build(order):
        bb = Blackboard(tmp_path / f"o{order}.sqlite")
        bb.create_mission("m1", MissionBudget(max_active_actors=2, max_turns=10), {})
        p1 = ev(task="t1", event_id="evt-p1")
        p2 = ev(task="t2", event_id="evt-p2")
        c1 = ev(task="t1", verb="CLAIM", status="ACTIVE", actor="a", pred="evt-p1", event_id="evt-c1")
        c2 = ev(task="t2", verb="CLAIM", status="ACTIVE", actor="b", pred="evt-p2", event_id="evt-c2")
        seqs = {0: [p1, p2, c1, c2], 1: [p2, p1, c2, c1], 2: [p1, c1, p2, c2]}
        for e in seqs[order]:
            bb.append(e)
        return bb.project("m1").as_dict(), [e["event_id"] for e in bb.events("m1", order="stored")]

    projections = [build(i) for i in range(3)]
    assert projections[0][0] == projections[1][0] == projections[2][0]
    assert len({tuple(p[1]) for p in projections}) == 3  # storage orders really differed


def test_causal_order_rejects_cycles():
    a = {"event_id": "a", "predecessor_event_id": "b"}
    b = {"event_id": "b", "predecessor_event_id": "a"}
    with pytest.raises(BlackboardError):
        causal_order([a, b])


def test_only_coordination_facts_are_stored(bb):
    cols = {r[1] for r in sqlite3.connect(bb.path).execute("PRAGMA table_info(events)")}
    for forbidden in ("transcript", "patch", "source", "token", "secret", "credential"):
        assert not any(forbidden in c for c in cols)
