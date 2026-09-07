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
    STATUSES,
    VERBS,
    ContractViolation,
    canonical_json,
    digest,
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


class MissionUnknown(BlackboardError):
    def __init__(self, mission_id: str) -> None:
        super().__init__("MISSION_UNKNOWN", mission_id)


# --------------------------------------------------------------------------
# Mission budget (frozen structure, section 15)
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class MissionBudget:
    max_active_actors: int = 1
    max_turns: int = 8
    max_model_calls: int = 30
    max_wall_seconds: int = 1800
    max_repair_loops: int = 0
    max_consecutive_failures: int = 1
    max_token_or_cost_units: float = 5.0

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    def event_budget(self) -> dict[str, Any]:
        """Projection of the mission budget onto the per-event budget object."""
        return {
            "max_turns": self.max_turns,
            "max_wall_seconds": self.max_wall_seconds,
            "max_repair_loops": self.max_repair_loops,
            "max_model_calls": self.max_model_calls,
            "max_token_or_cost_units": self.max_token_or_cost_units,
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "MissionBudget":
        return cls(**{k: d[k] for k in cls.__dataclass_fields__ if k in d})  # type: ignore[arg-type]


# --------------------------------------------------------------------------
# Transition table (section 14): allowed predecessor verbs per verb.
# ``None`` in the set means "no predecessor is acceptable".
# --------------------------------------------------------------------------

ALLOWED_PREDECESSORS: dict[str, frozenset[str | None]] = {
    "PUBLISH": frozenset({None, "PUBLISH", "OBSERVE", "COMPLETE"}),
    "CLAIM": frozenset({"PUBLISH", "OBSERVE", "RETRY"}),
    "COMPLETE": frozenset({"CLAIM", "OBSERVE", "COMPLETE"}),
    "OBSERVE": frozenset({"PUBLISH", "CLAIM", "COMPLETE", "OBSERVE"}),
    "REJECT": frozenset({None, "PUBLISH", "CLAIM", "COMPLETE", "OBSERVE"}),
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
    turns: int = 0
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
            "turns": self.turns,
            "failures_consecutive": self.failures_consecutive,
            "distinct_actors": self.distinct_actors,
        }


def causal_order(events: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Order events by predecessor depth, then event_id. Row order is never consulted."""
    by_id = {e["event_id"]: dict(e) for e in events}
    depth: dict[str, int] = {}

    def d(eid: str, seen: tuple[str, ...] = ()) -> int:
        if eid in depth:
            return depth[eid]
        if eid in seen:
            raise BlackboardError("PREDECESSOR_CYCLE", eid)
        pred = by_id[eid].get("predecessor_event_id")
        val = 0 if not pred or pred not in by_id else d(pred, seen + (eid,)) + 1
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
        if e["verb"] in ("CLAIM", "COMPLETE"):
            proj.turns += 1
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
            mission_row = c.execute("SELECT budget_json FROM missions WHERE mission_id=?", (payload["mission_id"],)).fetchone()
            if mission_row is None:
                raise MissionUnknown(payload["mission_id"])
            budget = MissionBudget.from_dict(json.loads(mission_row["budget_json"]))

            # idempotency
            existing = c.execute(
                "SELECT canonical_digest, payload_json FROM events WHERE event_id=?", (payload["event_id"],)
            ).fetchone()
            if existing is not None:
                if existing["canonical_digest"] == dig:
                    c.execute("COMMIT")
                    return json.loads(existing["payload_json"])
                raise EventIdConflict(payload["event_id"])

            # predecessor
            pred_id = payload.get("predecessor_event_id")
            pred_verb: str | None = None
            if pred_id is not None:
                if pred_id == payload["event_id"]:
                    raise PredecessorInvalid("PREDECESSOR_SELF", pred_id)
                pred = c.execute(
                    "SELECT mission_id, verb, status FROM events WHERE event_id=?", (pred_id,)
                ).fetchone()
                if pred is None:
                    raise PredecessorInvalid("PREDECESSOR_MISSING", pred_id)
                if pred["mission_id"] != payload["mission_id"]:
                    raise PredecessorInvalid("PREDECESSOR_CROSS_MISSION", pred_id)
                pred_verb = pred["verb"]
            if pred_verb not in ALLOWED_PREDECESSORS[payload["verb"]]:
                raise PredecessorInvalid(
                    "PREDECESSOR_TRANSITION_INVALID",
                    f"{payload['verb']} cannot follow {pred_verb}",
                )

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

            # a mission-level SUCCEEDED completion needs host observation and no task still active
            if payload["task_id"] == payload["mission_id"] and payload["verb"] == "COMPLETE" and payload["status"] == "SUCCEEDED":
                if proj.verbs.get("OBSERVE", 0) == 0:
                    raise BlackboardError("MISSION_COMPLETE_UNOBSERVED", "mission COMPLETE requires at least one host OBSERVE")
                active = [t.task_id for t in proj.tasks.values() if t.task_id != payload["mission_id"] and t.status in ("ACTIVE", "PENDING")]
                if active:
                    raise BlackboardError("MISSION_COMPLETE_WITH_ACTIVE_TASKS", ",".join(sorted(active)))

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
            c.execute("COMMIT")
        except BaseException:
            c.execute("ROLLBACK")
            raise
        return payload

    @staticmethod
    def _enforce_budget(budget: MissionBudget, proj: MissionProjection, payload: Mapping[str, Any]) -> None:
        if payload["verb"] in ("CLAIM", "COMPLETE") and proj.turns + 1 > budget.max_turns:
            raise BudgetExceeded("max_turns", budget.max_turns, proj.turns + 1)
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
        if eb.get("max_token_or_cost_units") is not None and eb["max_token_or_cost_units"] > budget.max_token_or_cost_units:
            raise BudgetExceeded("max_token_or_cost_units", budget.max_token_or_cost_units, eb["max_token_or_cost_units"])
