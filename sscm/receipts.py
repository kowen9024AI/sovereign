"""Mission -> ``experience.v0.1`` OBSERVED candidate.

This is the SSCM -> Sovereign-learning seam (WO section 46). The experience is
observed evidence only: ``authority = NONE``, no procedure or training
candidate, no evaluation, no promotion. A successful mission does not
evaluate itself.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

from .blackboard import MissionProjection
from .contracts import EXPERIENCE_SCHEMA_VERSION, artifact_ref, digest, validate_experience


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


_OUTCOME = {"SUCCEEDED": "SUCCEEDED", "FAILED": "FAILED", "BLOCKED": "BLOCKED", "CANCELLED": "CANCELLED"}


def mission_to_experience(
    projection: MissionProjection,
    events: list[Mapping[str, Any]],
    *,
    started_at: float,
    completed_at: float,
    executors_used: list[str],
    artifact_refs: list[Mapping[str, Any]],
    task_class: str = "sscm.dogfood.artifact-bound-handoff",
) -> dict[str, Any]:
    if projection.status not in _OUTCOME:
        raise ValueError(f"mission not terminal: {projection.status}")
    evidence = [f"sscm:event:{e['event_id']}:sha256:{digest(e)}" for e in events]
    evidence.append(f"sscm:projection:sha256:{digest(projection.as_dict())}")
    experience = {
        "schema_version": EXPERIENCE_SCHEMA_VERSION,
        "experience_id": f"sscm:exp:{digest({'mission': projection.mission_id, 'events': evidence})[:32]}",
        "producer": {
            "harness": "sscm:mission-controller",
            "agent": "sscm:roster:" + "+".join(sorted(set(executors_used))),
            "runtime": "sovereign.sscm@0.1.0",
            "model": None,
        },
        "task_class": task_class,
        "scope_ref": f"sscm:mission:{projection.mission_id}",
        "started_at": _iso(started_at),
        "completed_at": _iso(completed_at),
        "outcome": _OUTCOME[projection.status],
        "artifact_refs": [f"sscm:{a['kind'].lower()}:{a['ref']}" + (f":sha256:{a['sha256']}" if a.get("sha256") else "") for a in artifact_refs],
        "evidence_refs": evidence,
        "procedure_candidate_ref": None,
        "training_candidate_ref": None,
        "authority": "NONE",
    }
    validate_experience(experience)
    return experience


def experience_ref(rel_path: str, mission_id: str, sha256: str) -> dict[str, Any]:
    return artifact_ref("EXPERIENCE", f"run:{mission_id}:{rel_path}", sha256)
