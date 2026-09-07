"""The four core contracts must remain valid Draft 2020-12 schemas."""

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
CORE = sorted((ROOT / "contracts").glob("*.schema.json"))


@pytest.mark.parametrize("path", CORE, ids=[p.name for p in CORE])
def test_core_schema_is_well_formed(path):
    schema = json.loads(path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    assert schema["additionalProperties"] is False
    assert "authority" in schema["required"]


def test_core_contract_set_is_frozen_at_four():
    assert [p.name for p in CORE] == [
        "evaluation.v0.1.schema.json",
        "experience.v0.1.schema.json",
        "promotion.v0.1.schema.json",
        "training-artifact.v0.1.schema.json",
    ]
