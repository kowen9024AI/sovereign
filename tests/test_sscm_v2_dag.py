"""SSCM-01B: collaboration event v0.2, multi-predecessor DAG semantics, joins, concurrency, reservations."""

import json
import threading
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from sscm import contracts
from sscm.blackboard import (
    Blackboard,
    BlackboardError,
    BudgetExceeded,
    ClaimConflict,
    JoinNotReady,
    MissionBudget,
    PredecessorInvalid,
    ReservationConflict,
    causal_order,
)
from sscm.contracts import EVENT_V2_SCHEMA_VERSION, ContractViolation, event_predecessors, new_event, validate_event
from sscm.executors import OBSERVABLE, USAGE_DIMENSIONS

ROOT = Path(__file__).resolve().parents[1]
V1 = ROOT / "contracts" / "collaboration" / "a2a-collaboration-event.v0.1.schema.json"
V2 = ROOT / "contracts" / "collaboration" / "a2a-collaboration-event.v0.2.schema.json"
B = MissionBudget(max_active_actors=2, max_coordination_transitions=20, max_turns=50, max_model_calls=50, max_token_or_cost_units=500.0)
OBS = {d: OBSERVABLE for d in USAGE_DIMENSIONS}


def ev2(mission="m", task="t", verb="PUBLISH", actor="a", status="SUCCEEDED", preds=None, budget=None, **kw):
    return new_event(mission_id=mission, task_id=task, verb=verb, actor_ref=actor, status=status, budget=(budget or B).event_budget(EVENT_V2_SCHEMA_VERSION),
                     schema_version=EVENT_V2_SCHEMA_VERSION, predecessor_event_ids=list(preds or []), **kw)


def ev1(mission="m", task="t", verb="PUBLISH", actor="a", status="SUCCEEDED", pred=None, **kw):
    return new_event(mission_id=mission, task_id=task, verb=verb, actor_ref=actor, status=status, budget=B.event_budget(), predecessor_event_id=pred, **kw)


@pytest.fixture
def bb(tmp_path):
    store = Blackboard(tmp_path / "bb.sqlite")
    store.create_mission("m", B, {})
    yield store
    store.close()


# -- contract ----------------------------------------------------------------------------------


def test_v1_schema_untouched_and_v2_well_formed():
    v1 = json.loads(V1.read_text()); v2 = json.loads(V2.read_text())
    Draft202012Validator.check_schema(v2)
    assert "predecessor_event_id" in v1["properties"] and "predecessor_event_ids" not in v1["properties"]
    assert "max_token_or_cost_units" in v1["properties"]["budget"]["properties"]
    assert v2["properties"]["schema_version"]["const"] == EVENT_V2_SCHEMA_VERSION
    p = v2["properties"]["predecessor_event_ids"]
    assert p["type"] == "array" and p["uniqueItems"] is True and p["maxItems"] == contracts.MAX_PREDECESSORS_PER_EVENT == 4
    assert "predecessor_event_id" not in v2["properties"] and "predecessor_event_ids" in v2["required"]
    b = v2["properties"]["budget"]["properties"]
    assert "max_usage_units" in b and "max_token_or_cost_units" not in b
    assert v2["properties"]["authority"]["const"] == "NONE"


def test_v2_event_validates_and_v1_normalizes_internally():
    e2 = ev2(preds=["x", "y"], verb="OBSERVE"); validate_event(e2)
    assert event_predecessors(e2) == ["x", "y"]
    e1 = ev1(pred="x"); validate_event(e1)
    assert event_predecessors(e1) == ["x"] and "predecessor_event_ids" not in e1
    assert event_predecessors(ev1()) == []


def test_v2_rejects_duplicates_and_too_many_predecessors():
    e = ev2(preds=["x", "y"], verb="OBSERVE"); e["predecessor_event_ids"] = ["x", "x"]
    with pytest.raises(ContractViolation):
        validate_event(e)
    e = ev2(verb="OBSERVE"); e["predecessor_event_ids"] = [f"p{i}" for i in range(5)]
    with pytest.raises(ContractViolation):
        validate_event(e)


def test_v1_builder_refuses_multiple_predecessors():
    with pytest.raises(ContractViolation):
        new_event(mission_id="m", task_id="t", verb="OBSERVE", actor_ref="a", status="SUCCEEDED", budget=B.event_budget(), predecessor_event_ids=["a", "b"])


def test_budget_names_by_version():
    assert "max_usage_units" in B.event_budget(EVENT_V2_SCHEMA_VERSION) and "max_token_or_cost_units" not in B.event_budget(EVENT_V2_SCHEMA_VERSION)
    assert "max_token_or_cost_units" in B.event_budget() and B.max_usage_units == B.max_token_or_cost_units == 500.0
    assert MissionBudget.from_dict({"max_usage_units": 7.0}).max_token_or_cost_units == 7.0


# -- multi-parent semantics --------------------------------------------------------------------


SHA_A = "a" * 40
SHA_B = "b" * 40
HOST = "host:test-controller"


def rev(sha):
    return {"kind": "REVISION", "ref": f"git:{sha}", "sha256": None}


def _fanout(bb, a_status="SUCCEEDED", b_status="SUCCEEDED", a_rev=SHA_A, b_rev=SHA_B):
    p = bb.append(ev2(task="plan"))
    ca = bb.append(ev2(task="a", verb="CLAIM", status="ACTIVE", actor="wa", preds=[p["event_id"]]))
    cb = bb.append(ev2(task="b", verb="CLAIM", status="ACTIVE", actor="wb", preds=[p["event_id"]]))
    da = bb.append(ev2(task="a", verb="COMPLETE", status="SUCCEEDED", actor="wa", preds=[ca["event_id"]], candidate_revision=SHA_A))
    db = bb.append(ev2(task="b", verb="COMPLETE", status="SUCCEEDED", actor="wb", preds=[cb["event_id"]], candidate_revision=SHA_B))
    oa = bb.append(ev2(task="a", verb="OBSERVE", status=a_status, actor=HOST, preds=[da["event_id"]], candidate_revision=a_rev))
    ob = bb.append(ev2(task="b", verb="OBSERVE", status=b_status, actor=HOST, preds=[db["event_id"]], candidate_revision=b_rev))
    bb._last = {"da": da, "db": db}
    return p, oa, ob


def join(bb, preds, revs=(SHA_A, SHA_B), **kw):
    return bb.append(ev2(task="fanin", verb="OBSERVE", actor=HOST, preds=preds, input_refs=[rev(r) for r in revs], **kw))


def test_join_with_two_valid_predecessors_allowed(bb):
    _, oa, ob = _fanout(bb)
    j = join(bb, [oa["event_id"], ob["event_id"]])
    assert sorted(bb.predecessors_of(j["event_id"])) == sorted([oa["event_id"], ob["event_id"]])
    proj = bb.project("m")
    assert proj.event_ids_causal[-1] == j["event_id"]  # deepest node


def test_join_with_missing_predecessor_is_join_incomplete(bb):
    _, oa, _ = _fanout(bb)
    with pytest.raises(JoinNotReady) as ei:
        join(bb, [oa["event_id"], "evt-not-there"])
    assert ei.value.code == "JOIN_INCOMPLETE"


def test_join_with_failed_lane_is_join_blocked(bb):
    _, oa, ob = _fanout(bb, b_status="FAILED")
    with pytest.raises(JoinNotReady) as ei:
        join(bb, [oa["event_id"], ob["event_id"]])
    assert ei.value.code == "JOIN_BLOCKED"


def test_cross_mission_self_duplicate_predecessors_rejected(bb):
    bb.create_mission("m2", B, {})
    other = bb.append(ev2(mission="m2", task="x"))
    _, oa, ob = _fanout(bb)
    with pytest.raises(PredecessorInvalid) as ei:
        join(bb, [oa["event_id"], other["event_id"]])
    assert ei.value.code == "PREDECESSOR_CROSS_MISSION"
    e = ev2(task="fanin", verb="OBSERVE", actor=HOST, preds=[oa["event_id"]]); e["predecessor_event_ids"].append(e["event_id"])
    with pytest.raises(PredecessorInvalid) as ei:
        bb.append(e)
    assert ei.value.code == "PREDECESSOR_SELF"
    e = ev2(task="fanin", verb="OBSERVE", actor=HOST, preds=[oa["event_id"]]); e["predecessor_event_ids"] = [oa["event_id"], oa["event_id"]]
    with pytest.raises((PredecessorInvalid, ContractViolation)):
        bb.append(e)


def test_multi_parent_cycle_rejected_in_causal_order():
    a = {"event_id": "a", "predecessor_event_ids": ["c"]}
    b = {"event_id": "b", "predecessor_event_ids": ["a"]}
    c = {"event_id": "c", "predecessor_event_ids": ["a", "b"]}
    with pytest.raises(BlackboardError) as ei:
        causal_order([a, b, c])
    assert ei.value.code == "PREDECESSOR_CYCLE"


def test_dag_depth_is_max_parent_plus_one_and_order_independent(tmp_path):
    def build(order):
        bb = Blackboard(tmp_path / f"d{order}.sqlite"); bb.create_mission("m", B, {})
        p = ev2(task="plan", event_id="p")
        ca = ev2(task="a", verb="CLAIM", status="ACTIVE", actor="wa", preds=["p"], event_id="ca")
        cb = ev2(task="b", verb="CLAIM", status="ACTIVE", actor="wb", preds=["p"], event_id="cb")
        da = ev2(task="a", verb="COMPLETE", actor="wa", preds=["ca"], event_id="da")
        db = ev2(task="b", verb="COMPLETE", actor="wb", preds=["cb"], event_id="db")
        oa = ev2(task="a", verb="OBSERVE", actor=HOST, preds=["da"], event_id="oa", candidate_revision=SHA_A)
        # lane b is verified one step later so parent depths differ
        ob = ev2(task="b", verb="OBSERVE", actor=HOST, preds=["db"], event_id="ob", candidate_revision=SHA_B)
        ob2 = ev2(task="b", verb="OBSERVE", actor=HOST, preds=["ob"], event_id="ob2", candidate_revision=SHA_B)
        j = ev2(task="fanin", verb="OBSERVE", actor=HOST, preds=["oa", "ob2"], event_id="j", input_refs=[rev(SHA_A), rev(SHA_B)])
        seqs = {0: [p, ca, cb, da, db, oa, ob, ob2, j], 1: [p, cb, db, ob, ob2, ca, da, oa, j], 2: [p, ca, da, oa, cb, db, ob, ob2, j]}
        for e in seqs[order]:
            bb.append(e)
        return bb.project("m").as_dict(), [e["event_id"] for e in bb.events("m", order="stored")]
    outs = [build(i) for i in range(3)]
    assert outs[0][0] == outs[1][0] == outs[2][0]
    assert len({tuple(o[1]) for o in outs}) == 3
    ids = outs[0][0]["event_ids_causal"]
    assert ids.index("j") > ids.index("oa") and ids.index("j") > ids.index("ob2")


def test_join_order_reversed_projects_identically(tmp_path):
    def run(first_b):
        bb = Blackboard(tmp_path / f"r{first_b}.sqlite"); bb.create_mission("m", B, {})
        p = bb.append(ev2(task="plan", event_id="p"))
        lanes = [("a", "wa"), ("b", "wb")]
        if first_b:
            lanes.reverse()
        obs = {}
        shas = {"a": SHA_A, "b": SHA_B}
        for t, w in lanes:
            c = bb.append(ev2(task=t, verb="CLAIM", status="ACTIVE", actor=w, preds=["p"], event_id=f"c{t}"))
            d = bb.append(ev2(task=t, verb="COMPLETE", actor=w, preds=[f"c{t}"], event_id=f"d{t}"))
            obs[t] = bb.append(ev2(task=t, verb="OBSERVE", actor=HOST, preds=[f"d{t}"], event_id=f"o{t}", candidate_revision=shas[t]))
        preds = [obs["a"]["event_id"], obs["b"]["event_id"]] if not first_b else [obs["b"]["event_id"], obs["a"]["event_id"]]
        bb.append(ev2(task="fanin", verb="OBSERVE", actor=HOST, preds=preds, event_id="j", input_refs=[rev(SHA_A), rev(SHA_B)]))
        return bb.project("m").as_dict()
    assert run(False) == run(True)


def test_v1_and_v2_events_coexist_without_rewriting_v1(bb):
    e1 = bb.append(ev1(task="legacy"))
    dig = bb.stored_digest(e1["event_id"])
    e2 = bb.append(ev2(task="new", verb="OBSERVE", actor="host", preds=[e1["event_id"]]))
    assert bb.get_event(e1["event_id"]) == e1 and bb.stored_digest(e1["event_id"]) == dig
    assert bb.get_event(e1["event_id"])["schema_version"] == contracts.EVENT_SCHEMA_VERSION
    assert bb.predecessors_of(e2["event_id"]) == [e1["event_id"]]


def test_edge_table_not_comma_string(bb):
    import sqlite3
    _, oa, ob = _fanout(bb)
    j = join(bb, [oa["event_id"], ob["event_id"]])
    rows = sqlite3.connect(bb.path).execute("SELECT predecessor_event_id FROM event_predecessors WHERE event_id=?", (j["event_id"],)).fetchall()
    assert len(rows) == 2 and all("," not in r[0] for r in rows)


# -- concurrency -------------------------------------------------------------------------------


def test_two_active_claims_allowed_third_actor_refused(bb):
    p = bb.append(ev2(task="plan"))
    bb.append(ev2(task="a", verb="CLAIM", status="ACTIVE", actor="wa", preds=[p["event_id"]]))
    bb.append(ev2(task="b", verb="CLAIM", status="ACTIVE", actor="wb", preds=[p["event_id"]]))
    with pytest.raises(BudgetExceeded) as ei:
        bb.append(ev2(task="c", verb="CLAIM", status="ACTIVE", actor="wc", preds=[p["event_id"]]))
    assert ei.value.dimension == "max_active_actors"
    with pytest.raises(ClaimConflict):
        bb.append(ev2(task="a", verb="CLAIM", status="ACTIVE", actor="wb", preds=[p["event_id"]]))  # duplicate same-task claim


def test_concurrent_appends_on_separate_tasks_all_land(tmp_path):
    path = tmp_path / "c.sqlite"
    wide = MissionBudget(max_active_actors=8, max_coordination_transitions=100, max_turns=50, max_model_calls=50, max_token_or_cost_units=500.0)
    seed = Blackboard(path); seed.create_mission("m", wide, {})
    p = seed.append(ev2(task="plan", budget=wide)); seed.close()
    errors: list[str] = []
    barrier = threading.Barrier(6)

    def worker(i):
        store = Blackboard(path)
        try:
            barrier.wait()
            c = store.append(ev2(task=f"t{i}", verb="CLAIM", status="ACTIVE", actor=f"w{i}", preds=[p["event_id"]], budget=wide))
            store.append(ev2(task=f"t{i}", verb="COMPLETE", actor=f"w{i}", preds=[c["event_id"]], budget=wide))
        except Exception as ex:  # noqa: BLE001
            errors.append(f"{type(ex).__name__}: {ex}")
        finally:
            store.close()
    ts = [threading.Thread(target=worker, args=(i,)) for i in range(6)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert errors == [], errors  # no 'database is locked' swallowed or surfaced
    check = Blackboard(path)
    assert check.project("m").verbs == {"PUBLISH": 1, "CLAIM": 6, "COMPLETE": 6}


def test_concurrent_usage_settlement_sums_exactly(tmp_path):
    path = tmp_path / "u.sqlite"
    seed = Blackboard(path); seed.create_mission("m", MissionBudget(max_turns=100, max_model_calls=100, max_token_or_cost_units=1000.0), {})
    entries = [{"reservation_id": f"res:{i}", "run_id": f"run:{i}", "role": f"W{i}", "executor_turns": 5, "usage_units": 10.0, "wall_seconds": 10.0} for i in range(4)]
    seed.reserve_wave("m", "w1", entries, elapsed_wall_seconds=0.0); seed.close()
    barrier = threading.Barrier(4); errors = []

    def settle(i):
        store = Blackboard(path)
        try:
            barrier.wait()
            store.settle("m", run_id=f"run:{i}", role=f"W{i}", executor_ref="e", usage={"executor_turns": 3, "model_calls": 3, "token_units": 7.5, "cost_units": 0.0, "wall_seconds": 2.0}, observability=OBS)
        except Exception as ex:  # noqa: BLE001
            errors.append(str(ex))
        finally:
            store.close()
    ts = [threading.Thread(target=settle, args=(i,)) for i in range(4)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert errors == []
    proj = Blackboard(path).usage_projection("m")
    assert proj["observed_executor_turns"] == 12 and proj["observed_cost_or_token_units"] == 30.0 and proj["observed_executor_runs"] == 4
    assert set(Blackboard(path).reservations("m")["states"].values()) == {"SETTLED"}


# -- reservations ------------------------------------------------------------------------------


def _res(i, turns=5, units=60.0, wall=100.0):
    return {"reservation_id": f"res:{i}", "run_id": f"run:{i}", "role": f"W{i}", "executor_turns": turns, "usage_units": units, "wall_seconds": wall}


def test_wave_reservation_below_and_at_limit_pass(tmp_path):
    bb = Blackboard(tmp_path / "r.sqlite"); bb.create_mission("m", MissionBudget(max_turns=10, max_token_or_cost_units=100.0, max_wall_seconds=1000), {})
    bb.reserve_wave("m", "w1", [_res("a", 5, 40.0), _res("b", 5, 60.0)], elapsed_wall_seconds=0.0)  # exactly at both limits
    st = bb.reservations("m")
    assert st["active"]["token_units"] == 100.0 and st["active"]["executor_turns"] == 10


def test_wave_reservation_over_limit_rejects_entire_wave(tmp_path):
    bb = Blackboard(tmp_path / "r.sqlite"); bb.create_mission("m", MissionBudget(max_turns=100, max_token_or_cost_units=100.0), {})
    with pytest.raises(BudgetExceeded) as ei:
        bb.reserve_wave("m", "w1", [_res("a", 1, 60.0), _res("b", 1, 60.0)], elapsed_wall_seconds=0.0)
    assert "WAVE_ADMISSION_ATOMIC" in ei.value.detail
    assert bb.reservations("m")["reservations"] == []  # neither lane reserved


def test_reservation_idempotent_and_conflict(tmp_path):
    bb = Blackboard(tmp_path / "r.sqlite"); bb.create_mission("m", MissionBudget(max_turns=100, max_token_or_cost_units=1000.0), {})
    bb.reserve_wave("m", "w1", [_res("a")], elapsed_wall_seconds=0.0)
    bb.reserve_wave("m", "w1", [_res("a")], elapsed_wall_seconds=0.0)  # same id, same payload
    assert len(bb.reservations("m")["reservations"]) == 1
    with pytest.raises(ReservationConflict):
        bb.reserve_wave("m", "w1", [_res("a", units=61.0)], elapsed_wall_seconds=0.0)


def test_settlement_within_reservation_releases_unused(tmp_path):
    bb = Blackboard(tmp_path / "r.sqlite"); bb.create_mission("m", MissionBudget(max_turns=10, max_token_or_cost_units=100.0), {})
    bb.reserve_wave("m", "w1", [_res("a", 5, 60.0)], elapsed_wall_seconds=0.0)
    bb.settle("m", run_id="run:a", role="Wa", executor_ref="e", usage={"executor_turns": 2, "model_calls": 2, "token_units": 20.0, "cost_units": 0.0, "wall_seconds": 3.0}, observability=OBS)
    st = bb.reservations("m")
    assert st["states"]["res:a"] == "SETTLED" and st["active"]["token_units"] == 0
    # the released headroom is available again: 100 - 20 observed = 80 remaining
    bb.reserve_wave("m", "w2", [_res("b", 5, 80.0)], elapsed_wall_seconds=0.0)


def test_settlement_over_reservation_blocks_but_keeps_evidence(tmp_path):
    bb = Blackboard(tmp_path / "r.sqlite"); bb.create_mission("m", MissionBudget(max_turns=100, max_token_or_cost_units=1000.0), {})
    bb.reserve_wave("m", "w1", [_res("a", 5, 60.0)], elapsed_wall_seconds=0.0)
    with pytest.raises(BlackboardError) as ei:
        bb.settle("m", run_id="run:a", role="Wa", executor_ref="e", usage={"executor_turns": 9, "model_calls": 9, "token_units": 61.0, "cost_units": 0.0, "wall_seconds": 3.0}, observability=OBS)
    assert ei.value.code == "RUN_RESERVATION_EXCEEDED"
    proj = bb.usage_projection("m")
    assert proj["observed_executor_turns"] == 9 and proj["observed_cost_or_token_units"] == 61.0  # durable evidence
    assert bb.reservations("m")["states"]["res:a"] == "SETTLED"  # never rewritten to hide overshoot


def test_reservations_reconstruct_after_restart(tmp_path):
    path = tmp_path / "r.sqlite"
    bb = Blackboard(path); bb.create_mission("m", MissionBudget(max_turns=100, max_token_or_cost_units=1000.0), {})
    bb.reserve_wave("m", "w1", [_res("a"), _res("b")], elapsed_wall_seconds=0.0)
    bb.settle("m", run_id="run:a", role="Wa", executor_ref="e", usage={"executor_turns": 1, "model_calls": 1, "token_units": 1.0, "cost_units": 0.0, "wall_seconds": 1.0}, observability=OBS)
    before = bb.reservations("m"); bb.close()
    again = Blackboard(path)
    assert again.reservations("m") == before
    assert again.reservations("m")["states"] == {"res:a": "SETTLED", "res:b": "RESERVED"}


def test_restart_after_partial_fanout_reconstructs(tmp_path):
    path = tmp_path / "p.sqlite"
    bb = Blackboard(path); bb.create_mission("m", B, {})
    p = bb.append(ev2(task="plan"))
    ca = bb.append(ev2(task="a", verb="CLAIM", status="ACTIVE", actor="wa", preds=[p["event_id"]]))
    bb.append(ev2(task="b", verb="CLAIM", status="ACTIVE", actor="wb", preds=[p["event_id"]]))
    bb.append(ev2(task="a", verb="COMPLETE", actor="wa", preds=[ca["event_id"]]))
    before = bb.project("m").as_dict(); bb.close()
    again = Blackboard(path)
    proj = again.project("m")
    assert proj.as_dict() == before
    assert proj.tasks["a"].status == "SUCCEEDED" and proj.tasks["b"].status == "ACTIVE" and proj.tasks["b"].claimed_by == "wb"


# -- R1 Part C: verified-join admission --------------------------------------------------------------


def test_two_raw_complete_parents_rejected_even_when_succeeded(bb):
    _fanout(bb)
    da, db = bb._last["da"], bb._last["db"]
    with pytest.raises(JoinNotReady) as ei:
        join(bb, [da["event_id"], db["event_id"]])
    assert ei.value.code == "JOIN_PREDECESSOR_UNVERIFIED"


def test_mixed_verified_and_raw_parent_rejected(bb):
    _, oa, _ = _fanout(bb)
    db = bb._last["db"]
    with pytest.raises(JoinNotReady) as ei:
        join(bb, [oa["event_id"], db["event_id"]])
    assert ei.value.code == "JOIN_PREDECESSOR_UNVERIFIED"


def test_verified_observe_without_revision_rejected(bb):
    _, oa, ob = _fanout(bb, b_rev=None)
    with pytest.raises(JoinNotReady) as ei:
        join(bb, [oa["event_id"], ob["event_id"]])
    assert ei.value.code == "JOIN_PREDECESSOR_UNVERIFIED"


def test_verified_observe_with_short_revision_rejected(bb):
    _, oa, ob = _fanout(bb, b_rev="b" * 7)
    with pytest.raises(JoinNotReady) as ei:
        join(bb, [oa["event_id"], ob["event_id"]])
    assert ei.value.code == "JOIN_PREDECESSOR_UNVERIFIED"


def test_non_host_observe_parent_rejected(bb):
    p = bb.append(ev2(task="plan"))
    ca = bb.append(ev2(task="a", verb="CLAIM", status="ACTIVE", actor="wa", preds=[p["event_id"]]))
    cb = bb.append(ev2(task="b", verb="CLAIM", status="ACTIVE", actor="wb", preds=[p["event_id"]]))
    oa = bb.append(ev2(task="a", verb="OBSERVE", actor="actor:worker-a", preds=[ca["event_id"]], candidate_revision=SHA_A))  # a model "observing" itself
    ob = bb.append(ev2(task="b", verb="OBSERVE", actor=HOST, preds=[cb["event_id"]], candidate_revision=SHA_B))
    with pytest.raises(JoinNotReady) as ei:
        join(bb, [oa["event_id"], ob["event_id"]])
    assert ei.value.code == "JOIN_PREDECESSOR_UNVERIFIED"


def test_join_must_reference_exactly_the_frozen_revisions(bb):
    _, oa, ob = _fanout(bb)
    with pytest.raises(JoinNotReady) as ei:
        join(bb, [oa["event_id"], ob["event_id"]], revs=(SHA_A, "c" * 40))
    assert ei.value.code == "JOIN_REVISION_MISMATCH"
    with pytest.raises(JoinNotReady) as ei:
        join(bb, [oa["event_id"], ob["event_id"]], revs=(SHA_A,))
    assert ei.value.code == "JOIN_REVISION_MISMATCH"


def test_verified_parents_with_full_shas_allowed(bb):
    _, oa, ob = _fanout(bb)
    j = join(bb, [oa["event_id"], ob["event_id"]])
    assert len(bb.predecessors_of(j["event_id"])) == 2
