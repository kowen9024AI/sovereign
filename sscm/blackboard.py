"""Append-only SSCM blackboard on SQLite (stdlib ``sqlite3``).

Design rules (WO-SOVEREIGN-SSCM-01A sections 9-15, 52-54):

* Coordination facts only. Large artifacts are referenced by path/digest.
* Append-only: a stored event payload is never rewritten. Corrections are new events.
* Idempotent replay: same event_id + same canonical payload is a no-op;
  same event_id + different payload is ``EVENT_ID_CONFLICT``. No first/latest winner.
* Predecessor validation: exists, same mission, not self, transition allowed.
* Exclusive claims are decided inside one ``BEGIN IMMEDIATE`` transaction, so
  two concurrent claimants cannot both win. The loser gets ``CLAIM_CONFLICT``.
* Projection orders events causally (predecessor depth, then event_id), never
  by SQLite row order, so equivalent histories project identically.
* Budgets are frozen on the mission row; nothing in this module lets an actor
  raise them.

The logical contract (events, verbs, transitions, projection) has no
SQLite-specific semantics; SQLite is only the first storage backend.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

from .contracts import (
    EVENT_V2_SCHEMA_VERSION,
    HOST_ACTOR_PREFIX,
    MAX_PREDECESSORS_PER_EVENT,
    REVISION_RE,
    STATUSES,
    VERBS,
    ContractViolation,
    canonical_json,
    digest,
    event_predecessors,
    event_usage_ceiling,
    now_iso,
    validate_event,
)

# --------------------------------------------------------------------------
# Errors
# --------------------------------------------------------------------------


class BlackboardError(ValueError):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


class EventIdConflict(BlackboardError):
    def __init__(self, event_id: str) -> None:
        super().__init__("EVENT_ID_CONFLICT", f"event {event_id} already stored with a different payload")


class PredecessorInvalid(BlackboardError):
    pass


class ClaimConflict(BlackboardError):
    def __init__(self, mission_id: str, task_id: str, holder: str) -> None:
        super().__init__("CLAIM_CONFLICT", f"task {task_id} in mission {mission_id} is already claimed by {holder}")
        self.holder = holder


class BudgetExceeded(BlackboardError):
    def __init__(self, dimension: str, limit: Any, observed: Any) -> None:
        super().__init__("BUDGET_EXCEEDED", f"{dimension}: limit {limit}, observed {observed}")
        self.dimension = dimension


class JoinNotReady(BlackboardError):
    """A multi-predecessor join whose lanes are not all present and SUCCEEDED."""


class ReservationConflict(BlackboardError):
    pass


class MissionUnknown(BlackboardError):
    def __init__(self, mission_id: str) -> None:
        super().__init__("MISSION_UNKNOWN", mission_id)


class RetryAuthorityUnconfigured(BlackboardError):
    def __init__(self, detail: str = "mission has no configured repair authority") -> None:
        super().__init__("RETRY_AUTHORITY_UNCONFIGURED", detail)


class RetryActorNotAuthorized(BlackboardError):
    def __init__(self, actor_ref: str, expected: str) -> None:
        super().__init__("RETRY_ACTOR_NOT_AUTHORIZED", f"actor {actor_ref} is not configured repair authority {expected}")


class RetryTriggerInvalid(BlackboardError):
    def __init__(self, detail: str) -> None:
        super().__init__("RETRY_TRIGGER_INVALID", detail)


class RetryCandidateMismatch(BlackboardError):
    def __init__(self, retry_rev: Any, pred_rev: Any) -> None:
        super().__init__("RETRY_CANDIDATE_MISMATCH", f"RETRY candidate {retry_rev} does not match trigger candidate {pred_rev}")


class TerminalCandidateReviewMismatch(BlackboardError):
    def __init__(self, task_id: str, review_rev: Any, mission_rev: Any) -> None:
        super().__init__("TERMINAL_CANDIDATE_REVIEW_MISMATCH", f"task {task_id} candidate {review_rev} does not match mission candidate {mission_rev}")


# --------------------------------------------------------------------------
# Mission budget (frozen structure, section 15)
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class MissionBudget:
    """Frozen mission budget. Nothing at runtime may raise it.

    Dimension semantics (v0.1):

    * ``max_active_actors``            concurrent task claimants (coordination projection)
    * ``max_coordination_transitions`` CLAIM + COMPLETE coordination events (coordination projection)
    * ``max_turns``                    executor-reported turns, charged from ``ExecutorRun.executor_turns``
    * ``max_model_calls``              executor-reported model invocations, charged from ``ExecutorRun.model_calls``
    * ``max_wall_seconds``             real cumulative mission wall time (host clock)
    * ``max_repair_loops``             RETRY events
    * ``max_consecutive_failures``     consecutive FAILED/REJECT before a new CLAIM/RETRY is refused
    * ``max_token_or_cost_units``      normalized usage units = kilotokens (all input incl. cached + output) / 1000,
                                       charged from ``ExecutorRun.token_units``. Monetary cost is evidence only.
    * ``required_observable_dimensions`` usage dimensions every roster executor must be able to report from
                                       runtime output; a roster that cannot is refused before launch
                                       (BUDGET_DIMENSION_UNOBSERVABLE). Dimensions not required are still
                                       enforced whenever observed and recorded UNKNOWN otherwise.
    """

    max_active_actors: int = 1
    max_coordination_transitions: int = 16
    max_turns: int = 40
    max_model_calls: int = 60
    max_wall_seconds: int = 1800
    max_repair_loops: int = 0
    max_consecutive_failures: int = 1
    max_token_or_cost_units: float = 400.0
    required_observable_dimensions: tuple[str, ...] = ("executor_turns", "token_units", "wall_seconds")

    USAGE_LIMITS = {"executor_turns": "max_turns", "model_calls": "max_model_calls",
                    "token_units": "max_token_or_cost_units", "wall_seconds": "max_wall_seconds"}

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["required_observable_dimensions"] = list(self.required_observable_dimensions)
        return d

    @property
    def max_usage_units(self) -> float:
        """v0.2 name for the kilotoken ceiling. Same value as the legacy ``max_token_or_cost_units``."""
        return self.max_token_or_cost_units

    def event_budget(self, schema_version: str = "sovereign.a2a-collaboration-event.v0.1") -> dict[str, Any]:
        """Projection of the mission budget onto the per-event budget object (executor-usage ceilings).

        v0.1 events carry the legacy name ``max_token_or_cost_units``; v0.2 events carry ``max_usage_units``.
        """
        eb = {
            "max_turns": self.max_turns,
            "max_wall_seconds": self.max_wall_seconds,
            "max_repair_loops": self.max_repair_loops,
            "max_model_calls": self.max_model_calls,
        }
        if schema_version == EVENT_V2_SCHEMA_VERSION:
            eb["max_usage_units"] = self.max_token_or_cost_units
        else:
            eb["max_token_or_cost_units"] = self.max_token_or_cost_units
        return eb

    def limit(self, dimension: str) -> float | int:
        return getattr(self, self.USAGE_LIMITS[dimension])

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "MissionBudget":
        d = dict(d)
        if "max_usage_units" in d and "max_token_or_cost_units" not in d:
            d["max_token_or_cost_units"] = d.pop("max_usage_units")
        kw = {k: d[k] for k in cls.__dataclass_fields__ if k in d}  # type: ignore[attr-defined]
        if "required_observable_dimensions" in kw:
            kw["required_observable_dimensions"] = tuple(kw["required_observable_dimensions"])
        return cls(**kw)  # type: ignore[arg-type]


# --------------------------------------------------------------------------
# Transition table (section 14): allowed predecessor verbs per verb.
# ``None`` in the set means "no predecessor is acceptable".
# --------------------------------------------------------------------------

ALLOWED_PREDECESSORS: dict[str, frozenset[str | None]] = {
    "PUBLISH": frozenset({None, "PUBLISH", "OBSERVE", "COMPLETE"}),
    "CLAIM": frozenset({"PUBLISH", "OBSERVE", "RETRY"}),
    "COMPLETE": frozenset({"CLAIM", "OBSERVE", "COMPLETE"}),
    "OBSERVE": frozenset({"PUBLISH", "CLAIM", "COMPLETE", "OBSERVE"}),
    "REJECT": frozenset({None, "PUBLISH", "CLAIM", "COMPLETE", "OBSERVE", "REJECT", "RETRY"}),
    "RETRY": frozenset({"REJECT", "COMPLETE"}),
    "CANCEL": frozenset({None, "PUBLISH", "CLAIM", "COMPLETE", "OBSERVE", "REJECT", "RETRY"}),
}

TERMINAL_STATUSES = frozenset({"SUCCEEDED", "FAILED", "CANCELLED"})

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS missions (
  mission_id     TEXT PRIMARY KEY,
  created_at     TEXT NOT NULL,
  budget_json    TEXT NOT NULL,
  spec_json      TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
  seq                   INTEGER PRIMARY KEY AUTOINCREMENT,
  event_id              TEXT NOT NULL UNIQUE,
  mission_id            TEXT NOT NULL REFERENCES missions(mission_id),
  task_id               TEXT NOT NULL,
  predecessor_event_id  TEXT,
  verb                  TEXT NOT NULL,
  actor_ref             TEXT NOT NULL,
  executor_ref          TEXT,
  session_ref           TEXT,
  target_role           TEXT,
  workspace_ref         TEXT,
  base_revision         TEXT,
  candidate_revision    TEXT,
  status                TEXT NOT NULL,
  blocker_code          TEXT,
  observed_at           TEXT,
  stored_at             TEXT NOT NULL,
  canonical_digest      TEXT NOT NULL,
  payload_json          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS events_mission ON events(mission_id);
CREATE TABLE IF NOT EXISTS event_predecessors (
  event_id              TEXT NOT NULL REFERENCES events(event_id),
  predecessor_event_id  TEXT NOT NULL REFERENCES events(event_id),
  PRIMARY KEY (event_id, predecessor_event_id)
);
CREATE TABLE IF NOT EXISTS reservations (
  reservation_id   TEXT PRIMARY KEY,
  mission_id       TEXT NOT NULL REFERENCES missions(mission_id),
  wave_id          TEXT NOT NULL,
  run_id           TEXT NOT NULL UNIQUE,
  role             TEXT NOT NULL,
  executor_turns   INTEGER NOT NULL,
  model_calls      INTEGER,
  usage_units      REAL NOT NULL,
  wall_seconds     REAL NOT NULL,
  state            TEXT NOT NULL,
  canonical_digest TEXT NOT NULL,
  created_at       TEXT NOT NULL,
  settled_at       TEXT
);
CREATE TABLE IF NOT EXISTS usage (
  mission_id       TEXT NOT NULL REFERENCES missions(mission_id),
  run_id           TEXT NOT NULL UNIQUE,
  role             TEXT NOT NULL,
  executor_ref     TEXT NOT NULL,
  executor_turns   INTEGER,
  model_calls      INTEGER,
  token_units      REAL,
  cost_units       REAL,
  wall_seconds     REAL NOT NULL,
  observability_json TEXT NOT NULL,
  recorded_at      TEXT NOT NULL,
  canonical_digest TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS claims (
  mission_id  TEXT NOT NULL,
  task_id     TEXT NOT NULL,
  actor_ref   TEXT NOT NULL,
  event_id    TEXT NOT NULL,
  PRIMARY KEY (mission_id, task_id)
);
"""


# --------------------------------------------------------------------------
# Projection
# --------------------------------------------------------------------------


@dataclass
class TaskState:
    task_id: str
    claimed_by: str | None = None
    executor_ref: str | None = None
    workspace_ref: str | None = None
    status: str = "PENDING"
    last_verb: str | None = None
    last_event_id: str | None = None
    candidate_revision: str | None = None
    blocker_code: str | None = None
    event_count: int = 0


@dataclass
class MissionProjection:
    mission_id: str
    status: str
    budget: dict[str, Any]
    tasks: dict[str, TaskState] = field(default_factory=dict)
    event_ids_causal: list[str] = field(default_factory=list)
    event_count: int = 0
    verbs: dict[str, int] = field(default_factory=dict)
    terminal_event_id: str | None = None
    coordination_transitions: int = 0
    failures_consecutive: int = 0
    distinct_actors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "mission_id": self.mission_id,
            "status": self.status,
            "budget": self.budget,
            "tasks": {k: asdict(v) for k, v in sorted(self.tasks.items())},
            "event_ids_causal": self.event_ids_causal,
            "event_count": self.event_count,
            "verbs": dict(sorted(self.verbs.items())),
            "terminal_event_id": self.terminal_event_id,
            "coordination_transitions": self.coordination_transitions,
            "failures_consecutive": self.failures_consecutive,
            "distinct_actors": self.distinct_actors,
        }


def causal_order(events: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Order events by DAG depth (max parent depth + 1), then event_id. Row order is never consulted."""
    by_id = {e["event_id"]: dict(e) for e in events}
    depth: dict[str, int] = {}

    def d(eid: str, seen: tuple[str, ...] = ()) -> int:
        if eid in depth:
            return depth[eid]
        if eid in seen:
            raise BlackboardError("PREDECESSOR_CYCLE", eid)
        parents = [p for p in event_predecessors(by_id[eid]) if p in by_id]
        val = 0 if not parents else max(d(p, seen + (eid,)) for p in parents) + 1
        depth[eid] = val
        return val

    for eid in by_id:
        d(eid)
    return sorted(by_id.values(), key=lambda e: (depth[e["event_id"]], e["event_id"]))


def project(mission_id: str, budget: Mapping[str, Any], events: Iterable[Mapping[str, Any]]) -> MissionProjection:
    ordered = causal_order(events)
    proj = MissionProjection(mission_id=mission_id, status="PENDING", budget=dict(budget))
    actors: set[str] = set()
    for e in ordered:
        proj.event_ids_causal.append(e["event_id"])
        proj.event_count += 1
        proj.verbs[e["verb"]] = proj.verbs.get(e["verb"], 0) + 1
        actors.add(e["actor_ref"])
        t = proj.tasks.setdefault(e["task_id"], TaskState(task_id=e["task_id"]))
        t.event_count += 1
        t.last_verb = e["verb"]
        t.last_event_id = e["event_id"]
        t.status = e["status"]
        t.blocker_code = e.get("blocker_code")
        if e.get("candidate_revision"):
            t.candidate_revision = e["candidate_revision"]
        if e["verb"] == "CLAIM":
            t.claimed_by = e["actor_ref"]
            t.executor_ref = e.get("executor_ref")
            t.workspace_ref = e.get("workspace_ref")
        if e["verb"] == "RETRY":  # host re-opened the task: prior claimant no longer holds it
            t.claimed_by = None
        if e["verb"] in ("CLAIM", "COMPLETE"):
            proj.coordination_transitions += 1
        if e["status"] == "FAILED" or e["verb"] == "REJECT":
            proj.failures_consecutive += 1
        elif e["verb"] == "COMPLETE" and e["status"] == "SUCCEEDED":
            proj.failures_consecutive = 0
        if e["task_id"] == mission_id and e["verb"] in ("COMPLETE", "CANCEL", "REJECT") and e["status"] in TERMINAL_STATUSES | {"BLOCKED"}:
            proj.status = e["status"]
            proj.terminal_event_id = e["event_id"]
    if proj.status == "PENDING" and proj.event_count:
        proj.status = "ACTIVE"
    proj.distinct_actors = sorted(actors)
    return proj


# --------------------------------------------------------------------------
# Store
# --------------------------------------------------------------------------


class Blackboard:
    """One SQLite file. Safe for use from several threads via per-thread connections."""

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        self._local = threading.local()
        with self._conn() as c:
            c.executescript(_SCHEMA_SQL)

    # -- connection handling ------------------------------------------------

    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.path, timeout=30, isolation_level=None, check_same_thread=False)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA busy_timeout=30000")
            conn.row_factory = sqlite3.Row
            self._local.conn = conn
        return conn

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None

    # -- missions -----------------------------------------------------------

    def create_mission(self, mission_id: str, budget: MissionBudget, spec: Mapping[str, Any]) -> None:
        c = self._conn()
        try:
            c.execute(
                "INSERT INTO missions(mission_id, created_at, budget_json, spec_json) VALUES (?,?,?,?)",
                (mission_id, now_iso(), canonical_json(budget.as_dict()).decode(), canonical_json(spec).decode()),
            )
        except sqlite3.IntegrityError:
            existing = self.mission(mission_id)
            if existing["budget"] != budget.as_dict() or existing["spec"] != dict(spec):
                raise BlackboardError("MISSION_CONFLICT", f"mission {mission_id} exists with a different budget/spec")

    def mission(self, mission_id: str) -> dict[str, Any]:
        row = self._conn().execute("SELECT * FROM missions WHERE mission_id=?", (mission_id,)).fetchone()
        if row is None:
            raise MissionUnknown(mission_id)
        return {
            "mission_id": row["mission_id"],
            "created_at": row["created_at"],
            "budget": json.loads(row["budget_json"]),
            "spec": json.loads(row["spec_json"]),
        }

    def budget(self, mission_id: str) -> MissionBudget:
        return MissionBudget.from_dict(self.mission(mission_id)["budget"])

    # -- events -------------------------------------------------------------

    def get_event(self, event_id: str) -> dict[str, Any] | None:
        row = self._conn().execute("SELECT payload_json FROM events WHERE event_id=?", (event_id,)).fetchone()
        return json.loads(row["payload_json"]) if row else None

    def events(self, mission_id: str, order: str = "causal") -> list[dict[str, Any]]:
        rows = self._conn().execute("SELECT payload_json FROM events WHERE mission_id=?", (mission_id,)).fetchall()
        payloads = [json.loads(r["payload_json"]) for r in rows]
        if order == "causal":
            return causal_order(payloads)
        if order == "stored":  # diagnostic only; never semantic
            rows = self._conn().execute(
                "SELECT payload_json FROM events WHERE mission_id=? ORDER BY seq", (mission_id,)
            ).fetchall()
            return [json.loads(r["payload_json"]) for r in rows]
        raise ValueError(order)

    def stored_digest(self, event_id: str) -> str | None:
        row = self._conn().execute("SELECT canonical_digest FROM events WHERE event_id=?", (event_id,)).fetchone()
        return row["canonical_digest"] if row else None

    def claim_holder(self, mission_id: str, task_id: str) -> str | None:
        row = self._conn().execute(
            "SELECT actor_ref FROM claims WHERE mission_id=? AND task_id=?", (mission_id, task_id)
        ).fetchone()
        return row["actor_ref"] if row else None

    def project(self, mission_id: str) -> MissionProjection:
        m = self.mission(mission_id)
        return project(mission_id, m["budget"], self.events(mission_id))

    def append(self, event: Mapping[str, Any]) -> dict[str, Any]:
        """Validate and persist one event atomically. Returns the stored payload.

        Replay of an identical event is a no-op returning the stored payload.
        """
        validate_event(event)
        payload = dict(event)
        if payload["verb"] not in VERBS or payload["status"] not in STATUSES:
            raise ContractViolation("EVENT_ENUM_VIOLATION", "verb/status outside contract")
        canon = canonical_json(payload).decode()
        dig = digest(payload)
        c = self._conn()
        c.execute("BEGIN IMMEDIATE")
        try:
            mission_row = c.execute("SELECT budget_json, spec_json FROM missions WHERE mission_id=?", (payload["mission_id"],)).fetchone()
            if mission_row is None:
                raise MissionUnknown(payload["mission_id"])
            budget = MissionBudget.from_dict(json.loads(mission_row["budget_json"]))
            spec = json.loads(mission_row["spec_json"])

            # idempotency
            existing = c.execute(
                "SELECT canonical_digest, payload_json FROM events WHERE event_id=?", (payload["event_id"],)
            ).fetchone()
            if existing is not None:
                if existing["canonical_digest"] == dig:
                    c.execute("COMMIT")
                    return json.loads(existing["payload_json"])
                raise EventIdConflict(payload["event_id"])

            # predecessors (v0.1 normalized to [x]; v0.2 explicit list). Each must exist, share the mission,
            # differ from the event, be unique, and satisfy the transition table for the receiving verb.
            preds = event_predecessors(payload)
            if len(preds) > MAX_PREDECESSORS_PER_EVENT:
                raise PredecessorInvalid("PREDECESSOR_LIMIT", f"{len(preds)} > {MAX_PREDECESSORS_PER_EVENT}")
            if len(set(preds)) != len(preds):
                raise PredecessorInvalid("PREDECESSOR_DUPLICATE", ",".join(preds))
            pred_rows: list[sqlite3.Row] = []
            missing = [p for p in preds if p == payload["event_id"] or c.execute("SELECT 1 FROM events WHERE event_id=?", (p,)).fetchone() is None]
            for pred_id in preds:
                if pred_id == payload["event_id"]:
                    raise PredecessorInvalid("PREDECESSOR_SELF", pred_id)
            if missing:
                if len(preds) > 1:
                    raise JoinNotReady("JOIN_INCOMPLETE", f"join lanes missing: {','.join(missing)}")
                raise PredecessorInvalid("PREDECESSOR_MISSING", missing[0])
            for pred_id in preds:
                pred = c.execute("SELECT mission_id, verb, status, actor_ref, candidate_revision FROM events WHERE event_id=?", (pred_id,)).fetchone()
                if pred["mission_id"] != payload["mission_id"]:
                    raise PredecessorInvalid("PREDECESSOR_CROSS_MISSION", pred_id)
                if pred["verb"] not in ALLOWED_PREDECESSORS[payload["verb"]]:
                    raise PredecessorInvalid("PREDECESSOR_TRANSITION_INVALID", f"{payload['verb']} cannot follow {pred['verb']}")
                pred_rows.append(pred)
            if not preds and None not in ALLOWED_PREDECESSORS[payload["verb"]]:
                raise PredecessorInvalid("PREDECESSOR_TRANSITION_INVALID", f"{payload['verb']} requires a predecessor")
            if len(preds) > 1:
                self._admit_join(payload, preds, pred_rows)
            pred_id = preds[0] if len(preds) == 1 else None  # legacy column keeps the single-parent case queryable

            # mission terminal? nothing but replay may follow a terminal mission event
            existing_events = [json.loads(r["payload_json"]) for r in c.execute(
                "SELECT payload_json FROM events WHERE mission_id=?", (payload["mission_id"],)
            ).fetchall()]
            proj = project(payload["mission_id"], budget.as_dict(), existing_events)
            if proj.status in TERMINAL_STATUSES:
                raise BlackboardError("MISSION_TERMINAL", f"mission {payload['mission_id']} is {proj.status}")

            # exclusive claim: an already-claimed single-owner task is a conflict regardless of budget
            if payload["verb"] == "CLAIM":
                holder = c.execute(
                    "SELECT actor_ref FROM claims WHERE mission_id=? AND task_id=?",
                    (payload["mission_id"], payload["task_id"]),
                ).fetchone()
                if holder is not None:
                    raise ClaimConflict(payload["mission_id"], payload["task_id"], holder["actor_ref"])

            # budget (deterministic; an actor cannot raise it)
            self._enforce_budget(budget, proj, payload)

            if payload["verb"] == "CLAIM":
                c.execute(
                    "INSERT INTO claims(mission_id, task_id, actor_ref, event_id) VALUES (?,?,?,?)",
                    (payload["mission_id"], payload["task_id"], payload["actor_ref"], payload["event_id"]),
                )
            if payload["verb"] == "RETRY":
                # Store-level RETRY admission checks (WO-SOVEREIGN-SSCM-01C-R1 Part B)
                repair_policy = spec.get("repair_policy")
                if not repair_policy or not repair_policy.get("authority_actor_ref"):
                    raise RetryAuthorityUnconfigured("mission has no configured repair authority")

                auth_actor = repair_policy["authority_actor_ref"]
                if payload["actor_ref"] != auth_actor:
                    raise RetryActorNotAuthorized(payload["actor_ref"], auth_actor)

                if len(preds) != 1:
                    raise RetryTriggerInvalid(f"RETRY must have exactly 1 predecessor, got {len(preds)}")
                pred = pred_rows[0]
                exp_verb = repair_policy.get("trigger_verb", "REJECT")
                exp_status = repair_policy.get("trigger_status", "FAILED")
                exp_trigger_actor = repair_policy.get("trigger_actor_ref")
                if pred["verb"] != exp_verb or pred["status"] != exp_status:
                    raise RetryTriggerInvalid(
                        f"predecessor {preds[0]} has verb {pred['verb']}, status {pred['status']}; expected {exp_verb} {exp_status}"
                    )
                if exp_trigger_actor and pred["actor_ref"] != exp_trigger_actor:
                    raise RetryTriggerInvalid(
                        f"predecessor {preds[0]} actor {pred['actor_ref']} does not match configured trigger actor {exp_trigger_actor}"
                    )

                retry_cand = payload.get("candidate_revision")
                pred_cand = pred["candidate_revision"]
                if retry_cand != pred_cand:
                    raise RetryCandidateMismatch(retry_cand, pred_cand)
                if retry_cand is not None:
                    if not isinstance(retry_cand, str) or not REVISION_RE.match(retry_cand):
                        raise RetryCandidateMismatch(retry_cand, "not a full 40-hex SHA")
                if pred_cand is not None:
                    if not isinstance(pred_cand, str) or not REVISION_RE.match(pred_cand):
                        raise RetryCandidateMismatch(pred_cand, "not a full 40-hex SHA")

                # a host RETRY re-opens the task for a fresh claimant; the prior custody stays in the event history
                c.execute("DELETE FROM claims WHERE mission_id=? AND task_id=?", (payload["mission_id"], payload["task_id"]))

            # a mission-level SUCCEEDED completion needs host observation and no task still active
            if payload["task_id"] == payload["mission_id"] and payload["verb"] == "COMPLETE" and payload["status"] == "SUCCEEDED":
                if proj.verbs.get("OBSERVE", 0) == 0:
                    raise BlackboardError("MISSION_COMPLETE_UNOBSERVED", "mission COMPLETE requires at least one host OBSERVE")
                active = [t.task_id for t in proj.tasks.values() if t.task_id != payload["mission_id"] and t.status in ("ACTIVE", "PENDING")]
                if active:
                    raise BlackboardError("MISSION_COMPLETE_WITH_ACTIVE_TASKS", ",".join(sorted(active)))
                # the mission spec may declare structured terminal verifications (01C-R1 Part C)
                # or legacy required_terminal_tasks: an evaluation PASS alone can never terminal-succeed a mission
                verified_tasks: set[str] = set()
                if "required_terminal_verifications" in spec:
                    for rule in spec["required_terminal_verifications"]:
                        req_task = rule["task_id"]
                        verified_tasks.add(req_task)
                        req_verb = rule.get("verb", "OBSERVE")
                        req_status = rule.get("status", "SUCCEEDED")
                        req_actor = rule.get("actor_ref")
                        bind_cand = rule.get("bind_terminal_candidate", False)
                        err_code = "FINAL_REVIEW_REQUIRED" if "review" in req_task else "REQUIRED_TASK_UNVERIFIED"

                        t = proj.tasks.get(req_task)
                        if t is None or not t.last_event_id:
                            raise BlackboardError(err_code, f"mission COMPLETE requires verified task {req_task}")

                        last_ev = c.execute(
                            "SELECT verb, status, actor_ref, candidate_revision FROM events WHERE event_id=?",
                            (t.last_event_id,),
                        ).fetchone()
                        if last_ev["verb"] != req_verb:
                            raise BlackboardError(err_code, f"task {req_task} last verb is {last_ev['verb']}, expected {req_verb}")
                        if last_ev["status"] != req_status:
                            raise BlackboardError(err_code, f"task {req_task} status is {last_ev['status']}, expected {req_status}")
                        if req_actor and last_ev["actor_ref"] != req_actor:
                            raise BlackboardError(err_code, f"task {req_task} verified by {last_ev['actor_ref']}, expected {req_actor}")
                        if bind_cand:
                            review_rev = last_ev["candidate_revision"]
                            mission_rev = payload.get("candidate_revision")
                            if (
                                not review_rev
                                or not mission_rev
                                or not isinstance(review_rev, str)
                                or not isinstance(mission_rev, str)
                                or not REVISION_RE.match(review_rev)
                                or not REVISION_RE.match(mission_rev)
                                or review_rev != mission_rev
                            ):
                                raise TerminalCandidateReviewMismatch(req_task, review_rev, mission_rev)

                for required in spec.get("required_terminal_tasks", []):
                    if required not in verified_tasks:
                        t = proj.tasks.get(required)
                        if t is None or t.status != "SUCCEEDED" or t.last_verb != "OBSERVE":
                            raise BlackboardError(
                                "FINAL_REVIEW_REQUIRED" if "review" in required else "REQUIRED_TASK_UNVERIFIED",
                                f"mission COMPLETE requires host-verified SUCCEEDED task {required}",
                            )

            c.execute(
                """INSERT INTO events(event_id, mission_id, task_id, predecessor_event_id, verb, actor_ref,
                   executor_ref, session_ref, target_role, workspace_ref, base_revision, candidate_revision,
                   status, blocker_code, observed_at, stored_at, canonical_digest, payload_json)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    payload["event_id"], payload["mission_id"], payload["task_id"], pred_id, payload["verb"],
                    payload["actor_ref"], payload.get("executor_ref"), payload.get("session_ref"),
                    payload.get("target_role"), payload.get("workspace_ref"), payload.get("base_revision"),
                    payload.get("candidate_revision"), payload["status"], payload.get("blocker_code"),
                    payload.get("observed_at"), now_iso(), dig, canon,
                ),
            )
            for p in preds:
                c.execute("INSERT INTO event_predecessors(event_id, predecessor_event_id) VALUES (?,?)", (payload["event_id"], p))
            c.execute("COMMIT")
        except BaseException:
            c.execute("ROLLBACK")
            raise
        return payload

    @staticmethod
    def _admit_join(payload: Mapping[str, Any], preds: list[str], pred_rows: list[sqlite3.Row]) -> None:
        """Fan-in admission (SSCM-01B-R1 Part C): HOST VERIFICATION EARNS JOIN ELIGIBILITY.

        Every parent of a multi-predecessor event must be a host-owned verified observation: verb OBSERVE,
        actor under ``host:``, status SUCCEEDED, binding a full 40-hex candidate revision. The join itself must
        reference exactly those frozen revisions as REVISION input refs. A SUCCEEDED worker COMPLETE is never
        join-eligible.
        """
        frozen: list[str] = []
        for pred_id, r in zip(preds, pred_rows):
            if r["verb"] != "OBSERVE" or not str(r["actor_ref"]).startswith(HOST_ACTOR_PREFIX):
                raise JoinNotReady("JOIN_PREDECESSOR_UNVERIFIED", f"{pred_id} is {r['verb']} by {r['actor_ref']}, not a host verification")
            if r["status"] != "SUCCEEDED":
                raise JoinNotReady("JOIN_BLOCKED", f"join lane not SUCCEEDED: {pred_id}")
            rev = r["candidate_revision"]
            if not rev or not REVISION_RE.match(str(rev)):
                raise JoinNotReady("JOIN_PREDECESSOR_UNVERIFIED", f"{pred_id} carries no full verified candidate revision")
            frozen.append(rev)
        referenced = sorted(ref["ref"][4:] for ref in payload.get("input_refs", []) if ref.get("kind") == "REVISION" and str(ref.get("ref", "")).startswith("git:"))
        if referenced != sorted(frozen):
            raise JoinNotReady("JOIN_REVISION_MISMATCH", f"join references {referenced}, parents froze {sorted(frozen)}")

    def predecessors_of(self, event_id: str) -> list[str]:
        rows = self._conn().execute(
            "SELECT predecessor_event_id FROM event_predecessors WHERE event_id=? ORDER BY predecessor_event_id", (event_id,)
        ).fetchall()
        return [r["predecessor_event_id"] for r in rows]

    @staticmethod
    def _enforce_budget(budget: MissionBudget, proj: MissionProjection, payload: Mapping[str, Any]) -> None:
        if payload["verb"] in ("CLAIM", "COMPLETE") and proj.coordination_transitions + 1 > budget.max_coordination_transitions:
            raise BudgetExceeded("max_coordination_transitions", budget.max_coordination_transitions, proj.coordination_transitions + 1)
        if payload["verb"] == "CLAIM":
            active = {t.claimed_by for t in proj.tasks.values() if t.claimed_by and t.status in ("ACTIVE", "PENDING")}
            if payload["actor_ref"] not in active and len(active) + 1 > budget.max_active_actors:
                raise BudgetExceeded("max_active_actors", budget.max_active_actors, len(active) + 1)
        if payload["verb"] == "RETRY" and proj.verbs.get("RETRY", 0) + 1 > budget.max_repair_loops:
            raise BudgetExceeded("max_repair_loops", budget.max_repair_loops, proj.verbs.get("RETRY", 0) + 1)
        if payload["status"] == "FAILED" and proj.failures_consecutive + 1 > budget.max_consecutive_failures:
            # the failure itself is recorded; the *next* attempt is what must stop. Enforce at CLAIM/RETRY time:
            pass
        if payload["verb"] in ("CLAIM", "RETRY") and proj.failures_consecutive >= budget.max_consecutive_failures and proj.failures_consecutive > 0:
            raise BudgetExceeded("max_consecutive_failures", budget.max_consecutive_failures, proj.failures_consecutive)
        # per-event budget object must not exceed the frozen mission budget
        eb = payload["budget"]
        for key in ("max_turns", "max_wall_seconds", "max_repair_loops"):
            if eb[key] > getattr(budget, key):
                raise BudgetExceeded(key, getattr(budget, key), eb[key])
        if eb.get("max_model_calls") is not None and eb["max_model_calls"] > budget.max_model_calls:
            raise BudgetExceeded("max_model_calls", budget.max_model_calls, eb["max_model_calls"])
        ceiling = event_usage_ceiling(eb)
        if ceiling is not None and ceiling > budget.max_token_or_cost_units:
            raise BudgetExceeded("max_usage_units", budget.max_token_or_cost_units, ceiling)


    # -- executor usage ledger (Part B) ---------------------------------------------------------

    def usage_projection(self, mission_id: str) -> dict[str, Any]:
        """Runtime-observed consumption summed over recorded executor runs, with observability per dimension."""
        budget = self.budget(mission_id)
        rows = self._conn().execute(
            "SELECT * FROM usage WHERE mission_id=? ORDER BY run_id", (mission_id,)
        ).fetchall()
        totals: dict[str, float] = {"executor_turns": 0, "model_calls": 0, "token_units": 0.0, "cost_units": 0.0, "wall_seconds": 0.0}
        unknown: dict[str, list[str]] = {d: [] for d in totals}
        for r in rows:
            for d in totals:
                v = r[d]
                if v is None:
                    unknown[d].append(r["run_id"])
                else:
                    totals[d] += v
        totals = {k: (round(v, 3) if isinstance(v, float) else v) for k, v in totals.items()}
        observable = {d: ("OBSERVED" if not unknown[d] else "UNKNOWN") for d in totals}
        exceeded = [d for d in ("executor_turns", "model_calls", "token_units", "wall_seconds")
                    if not unknown[d] and totals[d] > budget.limit(d)]
        return {
            "observed_executor_runs": len(rows),
            "observed_executor_turns": totals["executor_turns"],
            "observed_model_calls": totals["model_calls"],
            "observed_cost_or_token_units": totals["token_units"],
            "observed_cost_usd_evidence_only": totals["cost_units"],
            "observed_wall_seconds": totals["wall_seconds"],
            "budget_limits": budget.as_dict(),
            "budget_dimensions_observable": observable,
            "unknown_runs_by_dimension": {d: v for d, v in unknown.items() if v},
            "budget_exceeded": exceeded,
        }

    def record_usage(self, mission_id: str, *, run_id: str, role: str, executor_ref: str,
                     usage: Mapping[str, Any], observability: Mapping[str, str]) -> dict[str, Any]:
        """Append one executor run's observed usage, then enforce cumulative limits.

        Idempotent on (run_id, same payload). A dimension the executor declared OBSERVABLE but did not report
        is refused (BUDGET_DIMENSION_UNREPORTED): unknown is never silently zero. Exceeding any observed
        dimension raises BudgetExceeded after the usage row is durably recorded, so the evidence survives.
        """
        budget = self.budget(mission_id)
        for dim in ("executor_turns", "model_calls", "token_units", "wall_seconds"):
            if observability.get(dim) == "OBSERVABLE" and usage.get(dim) is None:
                raise BlackboardError("BUDGET_DIMENSION_UNREPORTED", f"{role}/{executor_ref} claims {dim} observable but reported none")
        payload = {"mission_id": mission_id, "run_id": run_id, "role": role, "executor_ref": executor_ref,
                   **{d: usage.get(d) for d in ("executor_turns", "model_calls", "token_units", "cost_units", "wall_seconds")},
                   "observability": dict(observability)}
        if payload["wall_seconds"] is None:
            raise BlackboardError("BUDGET_DIMENSION_UNREPORTED", "wall_seconds is always host-observed and must be present")
        dig = digest(payload)
        c = self._conn()
        c.execute("BEGIN IMMEDIATE")
        try:
            existing = c.execute("SELECT canonical_digest FROM usage WHERE run_id=?", (run_id,)).fetchone()
            if existing is not None:
                if existing["canonical_digest"] != dig:
                    raise BlackboardError("USAGE_RUN_CONFLICT", f"run {run_id} already recorded with different usage")
            else:
                c.execute(
                    """INSERT INTO usage(mission_id, run_id, role, executor_ref, executor_turns, model_calls, token_units,
                       cost_units, wall_seconds, observability_json, recorded_at, canonical_digest) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (mission_id, run_id, role, executor_ref, payload["executor_turns"], payload["model_calls"], payload["token_units"],
                     payload["cost_units"], payload["wall_seconds"], canonical_json(payload["observability"]).decode(), now_iso(), dig),
                )
            c.execute("COMMIT")
        except BaseException:
            c.execute("ROLLBACK")
            raise
        proj = self.usage_projection(mission_id)
        if proj["budget_exceeded"]:
            d = proj["budget_exceeded"][0]
            raise BudgetExceeded(budget.USAGE_LIMITS[d], budget.limit(d), proj[{"executor_turns": "observed_executor_turns", "model_calls": "observed_model_calls", "token_units": "observed_cost_or_token_units", "wall_seconds": "observed_wall_seconds"}[d]])
        return proj

    def assert_headroom(self, mission_id: str, *, elapsed_wall_seconds: float) -> None:
        """Pre-launch gate: refuse a new executor when any observed dimension has no headroom left."""
        budget = self.budget(mission_id)
        if elapsed_wall_seconds >= budget.max_wall_seconds:
            raise BudgetExceeded("max_wall_seconds", budget.max_wall_seconds, round(elapsed_wall_seconds, 3))
        proj = self.usage_projection(mission_id)
        if proj["budget_exceeded"]:
            d = proj["budget_exceeded"][0]
            raise BudgetExceeded(budget.USAGE_LIMITS[d], budget.limit(d), "already exceeded")
        active = self._active_reservations(self._conn(), mission_id)
        for dim, key in (("executor_turns", "observed_executor_turns"), ("model_calls", "observed_model_calls"), ("token_units", "observed_cost_or_token_units")):
            if proj["budget_dimensions_observable"][dim] == "OBSERVED" and proj["observed_executor_runs"] and proj[key] + active[dim] >= budget.limit(dim):
                raise BudgetExceeded(budget.USAGE_LIMITS[dim], budget.limit(dim), f"{proj[key]} consumed + {active[dim]} reserved; no headroom for another run")


    # -- pre-launch usage reservations (SSCM-01B Part: parallel budget) ---------------------------

    def _active_reservations(self, c: sqlite3.Connection, mission_id: str) -> dict[str, float]:
        row = c.execute(
            "SELECT COALESCE(SUM(executor_turns),0) t, COALESCE(SUM(usage_units),0) u, COALESCE(SUM(wall_seconds),0) w, "
            "COALESCE(SUM(model_calls),0) m FROM reservations WHERE mission_id=? AND state='RESERVED'", (mission_id,)
        ).fetchone()
        return {"executor_turns": row["t"], "token_units": row["u"], "wall_seconds": row["w"], "model_calls": row["m"]}

    def reserve_wave(self, mission_id: str, wave_id: str, reservations: list[Mapping[str, Any]], *, elapsed_wall_seconds: float) -> dict[str, Any]:
        """Atomically reserve per-run ceilings for a whole wave, or reserve nothing.

        Each entry: {reservation_id, run_id, role, executor_turns, usage_units, wall_seconds, model_calls?}.
        remaining = mission limit - observed usage - still-active reservations. If the wave's combined
        reservation exceeds remaining on any dimension the entire wave is rejected before launch
        (WAVE_ADMISSION_ATOMIC). Same reservation_id + same payload replays idempotently; different payload
        is RESERVATION_CONFLICT.
        """
        budget = self.budget(mission_id)
        c = self._conn()
        c.execute("BEGIN IMMEDIATE")
        try:
            proj = self.usage_projection(mission_id)
            active = self._active_reservations(c, mission_id)
            observed = {"executor_turns": proj["observed_executor_turns"], "token_units": proj["observed_cost_or_token_units"],
                        "wall_seconds": max(proj["observed_wall_seconds"], elapsed_wall_seconds), "model_calls": proj["observed_model_calls"]}
            new_entries = []
            for r in reservations:
                payload = {"reservation_id": r["reservation_id"], "mission_id": mission_id, "wave_id": wave_id, "run_id": r["run_id"],
                           "role": r["role"], "executor_turns": int(r["executor_turns"]), "usage_units": float(r["usage_units"]),
                           "wall_seconds": float(r["wall_seconds"]), "model_calls": (None if r.get("model_calls") is None else int(r["model_calls"]))}
                dig = digest(payload)
                existing = c.execute("SELECT canonical_digest, state FROM reservations WHERE reservation_id=?", (payload["reservation_id"],)).fetchone()
                if existing is not None:
                    if existing["canonical_digest"] != dig:
                        raise ReservationConflict("RESERVATION_CONFLICT", f"{payload['reservation_id']} exists with a different payload")
                    continue  # idempotent replay; already counted in active if still RESERVED
                if c.execute("SELECT 1 FROM reservations WHERE run_id=?", (payload["run_id"],)).fetchone():
                    raise ReservationConflict("RESERVATION_CONFLICT", f"run {payload['run_id']} already has a reservation")
                new_entries.append((payload, dig))
            want = {"executor_turns": sum(p["executor_turns"] for p, _ in new_entries),
                    "token_units": sum(p["usage_units"] for p, _ in new_entries),
                    "wall_seconds": max([p["wall_seconds"] for p, _ in new_entries], default=0.0),  # a wave runs concurrently
                    "model_calls": sum(p["model_calls"] or 0 for p, _ in new_entries)}
            for dim in ("executor_turns", "token_units", "wall_seconds", "model_calls"):
                if dim == "model_calls" and not any(p["model_calls"] is not None for p, _ in new_entries):
                    continue
                remaining = budget.limit(dim) - observed[dim] - active[dim]
                if want[dim] > remaining + 1e-9:
                    raise BudgetExceeded(budget.USAGE_LIMITS[dim], budget.limit(dim), f"wave {wave_id} wants {want[dim]} but remaining {round(remaining, 3)} (WAVE_ADMISSION_ATOMIC)")
            for payload, dig in new_entries:
                c.execute(
                    """INSERT INTO reservations(reservation_id, mission_id, wave_id, run_id, role, executor_turns, model_calls, usage_units,
                       wall_seconds, state, canonical_digest, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (payload["reservation_id"], mission_id, wave_id, payload["run_id"], payload["role"], payload["executor_turns"],
                     payload["model_calls"], payload["usage_units"], payload["wall_seconds"], "RESERVED", dig, now_iso()))
            c.execute("COMMIT")
        except BaseException:
            c.execute("ROLLBACK")
            raise
        return self.reservations(mission_id)

    def reservations(self, mission_id: str) -> dict[str, Any]:
        rows = self._conn().execute("SELECT * FROM reservations WHERE mission_id=? ORDER BY reservation_id", (mission_id,)).fetchall()
        return {"reservations": [dict(r) for r in rows],
                "active": self._active_reservations(self._conn(), mission_id),
                "states": {r["reservation_id"]: r["state"] for r in rows}}

    def settle(self, mission_id: str, *, run_id: str, role: str, executor_ref: str, usage: Mapping[str, Any],
               observability: Mapping[str, str]) -> dict[str, Any]:
        """Settle a reservation against observed usage: charge the ledger, release the unused remainder.

        Observed usage is recorded durably first (it is evidence). Then, if any observed dimension exceeds the
        run's reserved ceiling, RUN_RESERVATION_EXCEEDED is raised; the reservation row is marked SETTLED either
        way and never rewritten to hide overshoot.
        """
        c = self._conn()
        res = c.execute("SELECT * FROM reservations WHERE run_id=? AND mission_id=?", (run_id, mission_id)).fetchone()
        if res is None:
            raise ReservationConflict("RESERVATION_MISSING", f"no reservation for run {run_id}")
        charge_error: BlackboardError | None = None
        try:
            proj = self.record_usage(mission_id, run_id=run_id, role=role, executor_ref=executor_ref, usage=usage, observability=observability)
        except BudgetExceeded as ex:
            charge_error = ex
            proj = self.usage_projection(mission_id)
        c.execute("UPDATE reservations SET state='SETTLED', settled_at=? WHERE run_id=? AND state='RESERVED'", (now_iso(), run_id))
        over = []
        for dim, col in (("executor_turns", "executor_turns"), ("token_units", "usage_units"), ("wall_seconds", "wall_seconds"), ("model_calls", "model_calls")):
            observed_v = usage.get(dim)
            limit_v = res[col]
            if observed_v is not None and limit_v is not None and observed_v > limit_v + 1e-9:
                over.append(f"{dim} observed {observed_v} > reserved {limit_v}")
        if over:
            raise BlackboardError("RUN_RESERVATION_EXCEEDED", "; ".join(over))
        if charge_error is not None:
            raise charge_error
        return proj

    def release(self, mission_id: str, run_id: str) -> None:
        """Release an unsettled reservation for a run that never launched (wave aborted)."""
        self._conn().execute("UPDATE reservations SET state='RELEASED', settled_at=? WHERE run_id=? AND mission_id=? AND state='RESERVED'",
                             (now_iso(), run_id, mission_id))
