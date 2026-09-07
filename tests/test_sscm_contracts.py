"""Collaboration contracts: well-formed, authority-pinned, and outside the frozen core set."""

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from sscm import contracts
from sscm.contracts import ContractViolation, new_event, validate_event

ROOT = Path(__file__).resolve().parents[1]
COLLAB = sorted((ROOT / "contracts" / "collaboration").glob("*.schema.json"))
CORE = sorted((ROOT / "contracts").glob("*.schema.json"))


@pytest.mark.parametrize("path", COLLAB, ids=[p.name for p in COLLAB])
def test_collaboration_schema_well_formed_and_authority_none(path):
    schema = json.loads(path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    assert schema["additionalProperties"] is False
    assert schema["properties"]["authority"]["const"] == "NONE"
    assert "authority" in schema["required"]


def test_collaboration_contracts_are_outside_the_frozen_core_set():
    core_names = {p.name for p in CORE}
    assert len(core_names) == 4
    for p in COLLAB:
        assert p.name not in core_names
        assert p.parent.name == "collaboration"


def test_event_schema_verbs_and_statuses_match_module_constants():
    schema = json.loads(contracts.EVENT_SCHEMA_PATH.read_text())
    assert tuple(schema["properties"]["verb"]["enum"]) == contracts.VERBS
    assert tuple(schema["properties"]["status"]["enum"]) == contracts.STATUSES
    assert tuple(schema["$defs"]["artifact_ref"]["properties"]["kind"]["enum"]) == contracts.ARTIFACT_KINDS


def _ev(**kw):
    base = dict(mission_id="m", task_id="t", verb="PUBLISH", actor_ref="a", status="PENDING",
                budget=contracts.event_budget(1, 10, 0))
    base.update(kw)
    return new_event(**base)


def test_new_event_validates_and_pins_authority():
    e = _ev()
    validate_event(e)
    assert e["authority"] == "NONE"
    assert e["schema_version"] == contracts.EVENT_SCHEMA_VERSION


def test_invalid_event_schema_rejected():
    e = _ev()
    e["verb"] = "APPROVE"  # not a coordination verb
    with pytest.raises(ContractViolation) as ei:
        validate_event(e)
    assert ei.value.code == "EVENT_SCHEMA_VIOLATION"


def test_authority_other_than_none_rejected():
    e = _ev()
    e["authority"] = "HARNESS"
    with pytest.raises(ContractViolation):
        validate_event(e)


def test_canonical_digest_is_key_order_independent():
    a = {"x": 1, "y": [1, 2], "z": {"k": "v"}}
    b = {"z": {"k": "v"}, "y": [1, 2], "x": 1}
    assert contracts.digest(a) == contracts.digest(b)
    assert contracts.digest({"x": 2}) != contracts.digest(a)
