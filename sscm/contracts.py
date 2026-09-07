"""Schema loading, validation and canonical digests for SSCM."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

from jsonschema import Draft202012Validator, FormatChecker

REPO_ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = REPO_ROOT / "contracts"

EVENT_SCHEMA_PATH = CONTRACTS / "collaboration" / "a2a-collaboration-event.v0.1.schema.json"
EVENT_V2_SCHEMA_PATH = CONTRACTS / "collaboration" / "a2a-collaboration-event.v0.2.schema.json"
EXPERIENCE_SCHEMA_PATH = CONTRACTS / "experience.v0.1.schema.json"

EVENT_SCHEMA_VERSION = "sovereign.a2a-collaboration-event.v0.1"
EVENT_V2_SCHEMA_VERSION = "sovereign.a2a-collaboration-event.v0.2"
EVENT_SCHEMAS = {EVENT_SCHEMA_VERSION: EVENT_SCHEMA_PATH, EVENT_V2_SCHEMA_VERSION: EVENT_V2_SCHEMA_PATH}
MAX_PREDECESSORS_PER_EVENT = 4
HOST_ACTOR_PREFIX = "host:"  # actors that may freeze a verified candidate (mission controller / host git), never a model
REVISION_RE = re.compile(r"^[0-9a-f]{40}$")
EXPERIENCE_SCHEMA_VERSION = "sovereign.experience.v0.1"

VERBS = ("CLAIM", "PUBLISH", "OBSERVE", "COMPLETE", "REJECT", "RETRY", "CANCEL")
STATUSES = ("PENDING", "ACTIVE", "BLOCKED", "SUCCEEDED", "FAILED", "CANCELLED")
ARTIFACT_KINDS = ("REVISION", "FILE", "LOG", "TEST_RESULT", "REVIEW", "RECEIPT", "EXPERIENCE", "OTHER")


class ContractViolation(ValueError):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


@lru_cache(maxsize=None)
def load_schema(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


@lru_cache(maxsize=None)
def validator(path: Path) -> Draft202012Validator:
    schema = load_schema(path)
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker())


def validate(instance: Mapping[str, Any], path: Path, code: str) -> None:
    errors = sorted(validator(path).iter_errors(instance), key=lambda e: list(e.absolute_path))
    if errors:
        first = errors[0]
        where = "/".join(str(p) for p in first.absolute_path) or "<root>"
        raise ContractViolation(code, f"{where}: {first.message}")


def validate_event(event: Mapping[str, Any]) -> None:
    """Validate against the schema named by the event's own schema_version (v0.1 or v0.2)."""
    path = EVENT_SCHEMAS.get(str(event.get("schema_version")))
    if path is None:
        raise ContractViolation("EVENT_SCHEMA_VIOLATION", f"unknown schema_version {event.get('schema_version')!r}")
    validate(event, path, "EVENT_SCHEMA_VIOLATION")
    if event.get("authority") != "NONE":  # belt and braces; schema already pins the const
        raise ContractViolation("AUTHORITY_NOT_NONE", "events never carry authority")


def event_predecessors(event: Mapping[str, Any]) -> list[str]:
    """Internal normalization only: v0.1 ``predecessor_event_id`` -> [x] / []; v0.2 -> its list. Payloads are never rewritten."""
    if "predecessor_event_ids" in event:
        return list(event["predecessor_event_ids"])
    p = event.get("predecessor_event_id")
    return [p] if p else []


def event_usage_ceiling(budget: Mapping[str, Any]) -> float | None:
    """Kilotoken ceiling under either name: v0.1 ``max_token_or_cost_units`` or v0.2 ``max_usage_units``."""
    if "max_usage_units" in budget:
        return budget["max_usage_units"]
    return budget.get("max_token_or_cost_units")


def validate_experience(experience: Mapping[str, Any]) -> None:
    validate(experience, EXPERIENCE_SCHEMA_PATH, "EXPERIENCE_SCHEMA_VIOLATION")


def canonical_json(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def digest(obj: Any) -> str:
    return hashlib.sha256(canonical_json(obj)).hexdigest()


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4()}"


def artifact_ref(kind: str, ref: str, sha256: str | None = None) -> dict[str, Any]:
    if kind not in ARTIFACT_KINDS:
        raise ContractViolation("ARTIFACT_KIND_UNKNOWN", kind)
    return {"kind": kind, "ref": ref, "sha256": sha256}


def event_budget(
    max_turns: int,
    max_wall_seconds: int,
    max_repair_loops: int,
    max_model_calls: int | None = None,
    max_token_or_cost_units: float | None = None,
) -> dict[str, Any]:
    return {
        "max_turns": max_turns,
        "max_wall_seconds": max_wall_seconds,
        "max_repair_loops": max_repair_loops,
        "max_model_calls": max_model_calls,
        "max_token_or_cost_units": max_token_or_cost_units,
    }


def new_event(
    *,
    mission_id: str,
    schema_version: str = EVENT_SCHEMA_VERSION,
    predecessor_event_ids: list[str] | None = None,
    task_id: str,
    verb: str,
    actor_ref: str,
    status: str,
    budget: Mapping[str, Any],
    event_id: str | None = None,
    predecessor_event_id: str | None = None,
    executor_ref: str | None = None,
    session_ref: str | None = None,
    target_role: str | None = None,
    workspace_ref: str | None = None,
    base_revision: str | None = None,
    candidate_revision: str | None = None,
    input_refs: list[dict[str, Any]] | None = None,
    output_refs: list[dict[str, Any]] | None = None,
    expected_output_schema: str | None = None,
    blocker_code: str | None = None,
    observed_at: str | None = None,
) -> dict[str, Any]:
    """Build a fully-populated event (v0.1 by default, v0.2 when requested). Validation happens at the blackboard."""
    ev: dict[str, Any] = {
        "schema_version": schema_version,
        "event_id": event_id or new_id("evt"),
        "mission_id": mission_id,
        "task_id": task_id,
    }
    if schema_version == EVENT_V2_SCHEMA_VERSION:
        preds = list(predecessor_event_ids or ([] if predecessor_event_id is None else [predecessor_event_id]))
        ev["predecessor_event_ids"] = preds
    else:
        if predecessor_event_ids and len(predecessor_event_ids) > 1:
            raise ContractViolation("EVENT_SCHEMA_VIOLATION", "v0.1 events carry at most one predecessor; use v0.2")
        ev["predecessor_event_id"] = predecessor_event_id if predecessor_event_id is not None else (predecessor_event_ids[0] if predecessor_event_ids else None)
    ev.update({
        "verb": verb,
        "actor_ref": actor_ref,
        "executor_ref": executor_ref,
        "session_ref": session_ref,
        "target_role": target_role,
        "workspace_ref": workspace_ref,
        "base_revision": base_revision,
        "candidate_revision": candidate_revision,
        "input_refs": list(input_refs or []),
        "output_refs": list(output_refs or []),
        "expected_output_schema": expected_output_schema,
        "status": status,
        "blocker_code": blocker_code,
        "budget": dict(budget),
        "observed_at": observed_at or now_iso(),
        "authority": "NONE",
    })
    return ev
