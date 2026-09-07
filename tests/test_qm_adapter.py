"""WO-MSK-HARNESS-QM-01A: structural validation of the QM harness adapter."""

import copy
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from adapters.qm import (
    CredentialMaterialSuspected,
    ReceiptRejected,
    map_outcome,
    receipt_to_experience,
    validate_experience,
    validate_receipt,
)
from adapters.qm.qm_adapter import RECEIPT_SCHEMA_PATH, receipt_digest, rescan_for_credentials

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "adapters" / "qm" / "fixtures"
VALID = sorted((FIX / "valid").glob("*.json"))
INVALID = sorted((FIX / "invalid").glob("*.json"))


def load(p: Path):
    return json.loads(p.read_text(encoding="utf-8"))


# --- schema ---------------------------------------------------------------


def test_receipt_schema_is_well_formed():
    schema = load(RECEIPT_SCHEMA_PATH)
    Draft202012Validator.check_schema(schema)
    assert schema["properties"]["authority"] == {"const": "NONE"}
    assert schema["properties"]["credential_material_present"]["const"] is False
    assert schema["properties"]["run"]["properties"]["status"]["enum"] == ["done", "failed"]


def test_receipt_schema_tracks_upstream_qm_enums():
    """Pinned to yc-software/qm@95b5a6a src/types.ts, src/harness/harness.ts, src/security/security-posture.ts."""
    schema = load(RECEIPT_SCHEMA_PATH)
    defs = schema["$defs"]
    assert defs["scope_kind"]["enum"] == ["personal", "channel", "team", "org", "group"]
    assert set(defs["posture"]["enum"]) == {"dangerous", "auto", "strict"}
    harness = schema["properties"]["harness"]["properties"]
    assert harness["control_transport"]["enum"] == ["mock", "in-process", "sdk", "http", "json-rpc", "api"]
    assert harness["tool_transport"]["enum"] == ["mock", "in-process", "plugin", "dynamic", "in-process-mcp", "mcp"]
    task_status = schema["properties"]["run"]["properties"]["tasks"]["items"]["properties"]["status"]["enum"]
    assert task_status == ["pending", "in_progress", "completed", "skipped", "failed"]
    assert schema["properties"]["principal"]["properties"]["principal_type"]["enum"] == ["internal", "guest"]


# --- valid fixtures --------------------------------------------------------


@pytest.mark.parametrize("path", VALID, ids=[p.name for p in VALID])
def test_valid_fixture_validates_and_maps(path):
    receipt = load(path)
    validate_receipt(receipt)
    exp = receipt_to_experience(receipt)
    validate_experience(exp)
    assert exp["authority"] == "NONE"
    assert exp["procedure_candidate_ref"] is None
    assert exp["training_candidate_ref"] is None
    assert exp["experience_id"].startswith("qm:exp:")
    assert exp["scope_ref"] == f"qm:scope:{receipt['scope']['scope_id']}"
    assert exp["producer"]["harness"] == f"qm:{receipt['harness']['profile_id']}"
    # every tool ledger cell becomes evidence, by digest only
    assert len(exp["evidence_refs"]) == 1 + len(receipt["tool_receipts"]) + len(receipt.get("audit_refs", []))
    assert all(":sha256:" in ref or ref.startswith("qm:audit:") for ref in exp["evidence_refs"])


EXPECTED_OUTCOME = {
    "done-succeeded.json": "SUCCEEDED",
    "failed.json": "FAILED",
    "paused-on-approval-blocked.json": "BLOCKED",
    "stopped-cancelled.json": "CANCELLED",
    "parked-blocked.json": "BLOCKED",
    "shared-channel-pi-minimal.json": "SUCCEEDED",
}


@pytest.mark.parametrize("name,outcome", sorted(EXPECTED_OUTCOME.items()))
def test_outcome_mapping_table(name, outcome):
    exp = receipt_to_experience(load(FIX / "valid" / name))
    assert exp["outcome"] == outcome


def test_every_valid_fixture_has_an_expected_outcome():
    assert {p.name for p in VALID} == set(EXPECTED_OUTCOME)


def test_outcome_precedence_is_total():
    # stopped beats everything
    assert map_outcome({"status": "failed", "stopped": True}) == "CANCELLED"
    # failed beats approval pause
    assert map_outcome({"status": "failed", "paused_on_approval": True}) == "FAILED"
    # approval pause beats done
    assert map_outcome({"status": "done", "paused_on_approval": True}) == "BLOCKED"
    assert map_outcome({"status": "done", "pending_approval_count": 2}) == "BLOCKED"
    # parked beats done
    assert map_outcome({"status": "done", "reap_outcome": "parked"}) == "BLOCKED"
    assert map_outcome({"status": "done"}) == "SUCCEEDED"
    with pytest.raises(ReceiptRejected) as ei:
        map_outcome({"status": "running"})
    assert ei.value.code == "UNMAPPABLE_RUN_STATUS"


# --- determinism -----------------------------------------------------------


def test_mapping_is_deterministic_and_key_order_independent():
    receipt = load(FIX / "valid" / "done-succeeded.json")
    reordered = json.loads(json.dumps(dict(reversed(list(receipt.items())))))
    a = receipt_to_experience(receipt)
    b = receipt_to_experience(reordered)
    assert a == b
    assert receipt_digest(receipt) == receipt_digest(reordered)


def test_any_receipt_change_changes_experience_id():
    receipt = load(FIX / "valid" / "done-succeeded.json")
    changed = copy.deepcopy(receipt)
    changed["run"]["model_calls"] = (changed["run"]["model_calls"] or 0) + 1
    assert receipt_to_experience(receipt)["experience_id"] != receipt_to_experience(changed)["experience_id"]


def test_timestamps_are_rfc3339_utc_from_epoch_ms():
    exp = receipt_to_experience(load(FIX / "valid" / "done-succeeded.json"))
    assert exp["started_at"] == "2025-09-06T22:50:01.000Z"
    assert exp["completed_at"] == "2025-09-06T23:04:05.000Z"


# --- invalid fixtures ------------------------------------------------------


@pytest.mark.parametrize("path", INVALID, ids=[p.name for p in INVALID])
def test_invalid_fixture_is_rejected_with_expected_code(path):
    case = load(path)
    with pytest.raises(ReceiptRejected) as ei:
        receipt_to_experience(case["receipt"])
    assert ei.value.code == case["_expect"], ei.value


def test_invalid_fixtures_cover_every_rejection_code():
    codes = {load(p)["_expect"] for p in INVALID}
    assert codes == {
        "RECEIPT_SCHEMA_VIOLATION",
        "CREDENTIAL_MATERIAL_SUSPECTED",
        "POSTURE_LOOSENED",
        "TIME_ORDER_VIOLATION",
    }


# --- credential rescan does not trust the reporter --------------------------


@pytest.mark.parametrize(
    "value,pattern",
    [
        ("sk-ant-" + "a" * 40, "openai_key"),  # anthropic keys also match the broader sk- rule first
        ("xoxb-1234567890-abcdefghij", "slack_token"),
        ("AKIAABCDEFGHIJKLMNOP", "aws_access_key"),
        ("Authorization: Bearer abcdefghijklmnopqrstuvwxyz", "bearer"),
        ("-----BEGIN RSA PRIVATE KEY-----", "private_key_block"),
        ("eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.c2lnbmF0dXJlc2lnbmF0dXJl", "jwt"),
        ("password=hunter2", "secret_assignment"),
    ],
)
def test_rescan_catches_secret_shaped_values(value, pattern):
    receipt = load(FIX / "valid" / "done-succeeded.json")
    receipt["durable_refs"].append({"kind": "other", "ref": value, "sha256": None})
    with pytest.raises(CredentialMaterialSuspected) as ei:
        rescan_for_credentials(receipt)
    assert ei.value.pattern == pattern


def test_rescan_rejects_forbidden_key_names_even_when_schema_would_not_see_them():
    # The schema already forbids unknown keys, so this guards the rescan on its own.
    receipt = {"keychain_view": "anything", "credential_material_present": False}
    with pytest.raises(CredentialMaterialSuspected):
        rescan_for_credentials(receipt)
    rescan_for_credentials({"credential_material_present": False, "ok": "sha256 only"})


# --- authority invariants ---------------------------------------------------


def test_adapter_output_never_carries_authority_or_promotion_state():
    for p in VALID:
        exp = receipt_to_experience(load(p))
        assert exp["authority"] == "NONE"
        assert set(exp) <= set(load(ROOT / "contracts" / "experience.v0.1.schema.json")["properties"])
        assert "decision" not in exp and "promotion" not in json.dumps(exp).lower()
