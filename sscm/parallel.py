"""SSCM-01B: bounded two-way parallel fan-out / deterministic host fan-in controller.

Frozen topology (the coordinator may fill in bounded instructions, never change the shape):

    PUBLISH plan -> {CLAIM A, CLAIM B} (concurrent) -> COMPLETE A/B -> OBSERVE A/B (host verify)
    -> JOIN (multi-predecessor) -> OBSERVE integrated (host fan-in) -> CLAIM/COMPLETE/OBSERVE review
    -> credential scan -> COMPLETE mission

Reuses the serial controller for review verification, terminalization, blocking and finishing. Adds
atomic wave reservation, a launch barrier, per-lane object stores, exact-SHA import and cherry-pick fan-in.
"""

from __future__ import annotations

import shutil
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from .artifacts import CredentialMaterialSuspected
from .blackboard import BlackboardError, BudgetExceeded, JoinNotReady, MissionBudget
from .contracts import CONTRACTS, EVENT_V2_SCHEMA_VERSION, ContractViolation, artifact_ref, load_schema, new_event, new_id, validate
from .executors import Executor, ExecutorRun, ExecutorTask
from .mission import (
    DISPOSITION_BLOCKED,
    HOST_ACTOR,
    IMPLEMENTER_SCHEMA,
    REVIEWER_SCHEMA,
    DogfoodSpec,
    MissionAborted,
    MissionController,
)
from .workspace import RepoLane, WorkspaceError, WorkspaceFacts, assert_isolated

FANOUT_PLAN_SCHEMA = CONTRACTS / "collaboration" / "sscm-coordinator-fanout-plan.v0.1.schema.json"
INTEGRATION_RECEIPT_SCHEMA = CONTRACTS / "collaboration" / "sscm-integration-receipt.v0.1.schema.json"

PARALLEL_ROLES = ("COORDINATOR", "WORKER_A", "WORKER_B", "REVIEWER")
DISPOSITION_PARALLEL_QUALIFIED = "SOVEREIGN_SSCM_PARALLEL_FANOUT_FANIN_QUALIFIED"
DISPOSITION_PARALLEL_BLOCKED = "SOVEREIGN_SSCM_PARALLEL_BLOCKED"
DISPOSITION_PARALLEL_REPAIR = "SSCM_PARALLEL_DOGFOOD_REPAIR_REQUIRED"


@dataclass(frozen=True)
class FanoutTask:
    task_id: str
    role: str
    lane: str
    branch: str
    target_file: str
    expected_content: str


FROZEN_TASKS: tuple[FanoutTask, ...] = (
    FanoutTask("task:fanout-a", "WORKER_A", "worker-a", "dogfood/sscm-01b-worker-a", "docs/sscm-fanout-a.txt", "SSCM_FANOUT_A_OK\n"),
    FanoutTask("task:fanout-b", "WORKER_B", "worker-b", "dogfood/sscm-01b-worker-b", "docs/sscm-fanout-b.txt", "SSCM_FANOUT_B_OK\n"),
)
INTEGRATION_ORDER = tuple(t.task_id for t in FROZEN_TASKS)  # frozen by task id, never by completion time


@dataclass(frozen=True)
class RunEnvelope:
    """Host-frozen per-run reservation ceiling. An executor never chooses its own."""
    executor_turns: int
    usage_units: float
    wall_seconds: float
    model_calls: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"executor_turns": self.executor_turns, "usage_units": self.usage_units, "wall_seconds": self.wall_seconds, "model_calls": self.model_calls}


@dataclass
class ParallelDogfoodSpec(DogfoodSpec):
    tasks: tuple[FanoutTask, ...] = FROZEN_TASKS
    envelopes: dict[str, RunEnvelope] = field(default_factory=lambda: {
        "COORDINATOR": RunEnvelope(6, 60.0, 600.0),
        "WORKER_A": RunEnvelope(10, 200.0, 900.0),
        "WORKER_B": RunEnvelope(10, 200.0, 900.0),
        # Live 01B run 1 measured a two-file+receipt review at 133.5 kilotokens (10 turns); the envelope is sized above that.
        "REVIEWER": RunEnvelope(16, 300.0, 900.0),
    })
    budget: MissionBudget = field(default_factory=lambda: MissionBudget(
        max_active_actors=2, max_coordination_transitions=12, max_turns=48, max_model_calls=48,
        max_wall_seconds=1800, max_repair_loops=0, max_consecutive_failures=1, max_token_or_cost_units=800.0,
        required_observable_dimensions=("executor_turns", "token_units", "wall_seconds")))
    barrier_timeout_seconds: float = 30.0

    def as_dict(self) -> dict[str, Any]:
        d = super().as_dict()
        d["tasks"] = [t.__dict__ for t in self.tasks]
        d["envelopes"] = {k: v.as_dict() for k, v in self.envelopes.items()}
        d["integration_order"] = list(INTEGRATION_ORDER)
        return d


class ParallelMissionController(MissionController):
    ROLES = PARALLEL_ROLES

    def __init__(self, spec: ParallelDogfoodSpec, executors: Mapping[str, Executor], **kw: Any) -> None:
        missing = [r for r in PARALLEL_ROLES if r not in executors]
        if missing:
            raise ValueError(f"executors missing for roles {missing}")
        # bypass the serial base's role check by handing it the roles it expects under their names
        base_map = dict(executors)
        base_map.setdefault("IMPLEMENTER", executors["WORKER_A"])
        super().__init__(spec, base_map, **kw)
        self.executors = dict(executors)
        self.spec: ParallelDogfoodSpec = spec
        self.report["parallel"] = {}

    # -- v0.2 events ---------------------------------------------------------------------------

    def _eb(self) -> dict[str, Any]:
        return self.spec.budget.event_budget(EVENT_V2_SCHEMA_VERSION)

    def _append(self, **kw: Any) -> dict[str, Any]:
        preds = kw.pop("predecessor_event_ids", None)
        single = kw.pop("predecessor_event_id", None)
        if preds is None:
            preds = [single] if single else []
        ev = new_event(mission_id=self.mission_id, budget=self._eb(), schema_version=EVENT_V2_SCHEMA_VERSION,
                       predecessor_event_ids=list(preds), **kw)
        stored = self.bb.append(ev)
        self._last_event_id = stored["event_id"]
        self.report["events"].append({"event_id": stored["event_id"], "verb": stored["verb"], "task_id": stored["task_id"],
                                      "actor_ref": stored["actor_ref"], "status": stored["status"],
                                      "predecessor_event_ids": list(stored["predecessor_event_ids"])})
        return stored

    # -- reservations + launching --------------------------------------------------------------

    def _reserve_wave(self, wave_id: str, roles: list[str]) -> dict[str, str]:
        run_ids = {role: new_id(f"run:{role.lower()}") for role in roles}
        entries = []
        for role in roles:
            env = self.spec.envelopes[role]
            entries.append({"reservation_id": f"res:{wave_id}:{role.lower()}", "run_id": run_ids[role], "role": role, **env.as_dict()})
        try:
            self.bb.reserve_wave(self.mission_id, wave_id, entries, elapsed_wall_seconds=self._elapsed())
        except BudgetExceeded as ex:
            raise MissionAborted("BUDGET_EXCEEDED", f"wave {wave_id} not admitted: {ex.detail}") from ex
        self.report["parallel"].setdefault("waves", {})[wave_id] = {"roles": roles, "run_ids": run_ids, "envelopes": {r: self.spec.envelopes[r].as_dict() for r in roles}}
        return run_ids

    def _task_for(self, role: str, run_id: str, instruction: dict[str, Any], schema_path: Path, cwd: Path,
                  write_allowed: bool, extra_writable: list[Path]) -> ExecutorTask:
        env = self.spec.envelopes[role]
        timeout = min(int(env.wall_seconds), self._remaining_seconds())
        return ExecutorTask(role=role, run_id=run_id, cwd=cwd, instruction=instruction, output_schema=load_schema(schema_path),
                            timeout_seconds=timeout, write_allowed=write_allowed, extra_writable_dirs=list(extra_writable),
                            log_dir=self.run.path("logs"))

    def _settle(self, role: str, result: ExecutorRun) -> None:
        receipt = self.run.write_json(f"artifacts/{role.lower()}-run.json", result.as_dict(), kind="RECEIPT")
        self.report["runs"][role] = {**result.as_dict(), "receipt": receipt}
        usage = result.usage()
        try:
            self.report["budget"] = self.bb.settle(self.mission_id, run_id=result.run_id, role=role, executor_ref=result.executor_id,
                                                  usage=usage, observability=self.executors[role].capability().usage_observability)
        except BudgetExceeded as ex:
            self.report["budget"] = self.bb.usage_projection(self.mission_id)
            raise MissionAborted("BUDGET_EXCEEDED", f"after {role}: {ex.detail}") from ex
        except BlackboardError as ex:
            self.report["budget"] = self.bb.usage_projection(self.mission_id)
            raise MissionAborted(ex.code, ex.detail) from ex

    def _launch_wave(self, tasks: dict[str, ExecutorTask]) -> dict[str, ExecutorRun]:
        """Launch all lanes concurrently; a barrier makes every lane runnable before any lane is awaited."""
        barrier = threading.Barrier(len(tasks))
        results: dict[str, ExecutorRun] = {}

        def lane(role: str, task: ExecutorTask) -> tuple[str, ExecutorRun]:
            t0 = time.time()
            try:
                barrier.wait(timeout=self.spec.barrier_timeout_seconds)
                return role, self.executors[role].run(task)
            except BaseException as ex:  # noqa: BLE001 - a broken barrier or executor crash is a failed run, never a hang
                barrier.abort()
                return role, ExecutorRun(self.executors[role].capability().executor_id, task.run_id, None, 1, t0, time.time(), None,
                                         None, None, None, None, f"lane failed before/while running: {type(ex).__name__}: {ex}")

        with ThreadPoolExecutor(max_workers=len(tasks)) as pool:
            futures = [pool.submit(lane, role, task) for role, task in tasks.items()]
            for fut in futures:
                role, run = fut.result()
                results[role] = run
        return results

    # -- session / run identity custody (01B-R1 Part A) -----------------------------------------

    @staticmethod
    def _require_session(role: str, run: ExecutorRun) -> str:
        """A required role must carry a non-empty provider/runtime session identity. No synthetic fallback."""
        if not run.session_ref or not str(run.session_ref).strip():
            raise MissionAborted("SESSION_IDENTITY_MISSING", f"{role} run {run.run_id} returned no session identity", DISPOSITION_PARALLEL_BLOCKED)
        return str(run.session_ref)

    def _record_identity(self, role: str, run: ExecutorRun) -> None:
        ids = self.report["parallel"].setdefault("identity", {"run_ids": {}, "session_refs": {}})
        ids["run_ids"][role] = run.run_id
        ids["session_refs"][role] = run.session_ref
        seen = [r for r, rid in ids["run_ids"].items() if rid == run.run_id and r != role]
        if seen:
            raise MissionAborted("RUN_ID_COLLISION", f"{role} run id equals {seen[0]}", DISPOSITION_PARALLEL_BLOCKED)

    # -- the frozen DAG ------------------------------------------------------------------------

    def execute(self, cleanup: bool = True) -> dict[str, Any]:
        spec = self.spec
        self.bb.create_mission(self.mission_id, spec.budget, spec.as_dict())
        caps = {role: self.executors[role].capability().as_dict() for role in PARALLEL_ROLES}
        self.report["capabilities"] = caps
        self.run.write_json("artifacts/executor-capabilities.json", caps)
        try:
            self._gate_parallel_roster(caps)
            plan_ref, publish = self._coordinate_fanout()
            lanes = self._admit_workers()
            claims = self._claim_workers(lanes, plan_ref, publish)
            runs = self._run_workers(lanes, claims, plan_ref)
            observes = self._verify_workers(lanes, claims, runs)
            join = self._join(observes)
            integrated, observe_int = self._fan_in(lanes, join)
            rev_wt, claim_rev = self._admit_reviewer_lane(lanes, integrated, observe_int)
            complete_rev, review = self._review_integrated(rev_wt, integrated, claim_rev, lanes)
            observe_rev = self._host_verify_review(rev_wt, integrated, review, complete_rev)
            self._terminal(review, observe_rev)
            if self.report.get("disposition") == "SOVEREIGN_SSCM_LOCAL_A2A_QUALIFIED":
                self.report["disposition"] = DISPOSITION_PARALLEL_QUALIFIED
        except MissionAborted as ex:
            if ex.disposition == "SSCM_DOGFOOD_REPAIR_REQUIRED":
                ex.disposition = DISPOSITION_PARALLEL_REPAIR
            elif ex.disposition == DISPOSITION_BLOCKED:
                ex.disposition = DISPOSITION_PARALLEL_BLOCKED
            self._block(ex)
        except (BlackboardError, WorkspaceError, ContractViolation, CredentialMaterialSuspected) as ex:
            code = getattr(ex, "code", type(ex).__name__)
            self._block(MissionAborted(code, str(ex), DISPOSITION_PARALLEL_BLOCKED))
        finally:
            self._finish(cleanup)
            if self.report.get("disposition") == DISPOSITION_BLOCKED:
                self.report["disposition"] = DISPOSITION_PARALLEL_BLOCKED
        return self.report

    def _gate_parallel_roster(self, caps: Mapping[str, Mapping[str, Any]]) -> None:
        for role in PARALLEL_ROLES:
            c = caps[role]
            if c["availability"] != "AVAILABLE" or not c["native_auth_ready"]:
                raise MissionAborted("EXECUTOR_UNAVAILABLE", f"{role}: {c['executor_id']}")
            if c["authority"] != "NONE":
                raise MissionAborted("AUTHORITY_NOT_NONE", f"{role} executor claims authority {c['authority']}")
            obs = c.get("usage_observability", {})
            for dim in self.spec.budget.required_observable_dimensions:
                if obs.get(dim) != "OBSERVABLE":
                    raise MissionAborted("BUDGET_DIMENSION_UNOBSERVABLE", f"{role} cannot report {dim}")
            # 01B-R1 Part A: every role's distinctness is qualified by provider/runtime session identity, so the
            # executor must be able to surface one. PID, order, family, cwd or prompt are never a substitute.
            if not c.get("session_identity_support"):
                raise MissionAborted("SESSION_IDENTITY_UNOBSERVABLE", f"{role} executor {c['executor_id']} cannot surface a per-run session identity")
        for w in ("WORKER_A", "WORKER_B"):
            if caps[w]["runtime_family"] == caps["REVIEWER"]["runtime_family"]:
                raise MissionAborted("RUNTIME_NOT_DISTINCT", f"{w} and REVIEWER share a runtime family")
        # the whole roster's envelopes must fit the mission budget up front (no partial-wave discovery later)
        env = self.spec.envelopes
        if sum(e.executor_turns for e in env.values()) > self.spec.budget.max_turns or sum(e.usage_units for e in env.values()) > self.spec.budget.max_usage_units:
            raise MissionAborted("BUDGET_EXCEEDED", "frozen run envelopes exceed the mission budget")

    def _coordinate_fanout(self) -> tuple[dict[str, Any], dict[str, Any]]:
        spec = self.spec
        instruction_in = {
            "mission_id": self.mission_id, "role": "COORDINATOR",
            "mission_objective": "Produce a bounded two-task fan-out plan. The topology is frozen by the host: exactly the two tasks below, "
                                 "each creating one file with exact content and committing on its own isolated branch. Do not add, remove, or reorder tasks.",
            "frozen_tasks": [{"task_id": t.task_id, "target_role": t.role, "allowed_files": [t.target_file],
                              "expected_content": {t.target_file: t.expected_content}, "commit_required": True} for t in spec.tasks],
            "required_fields": {"schema_version": "sovereign.sscm-coordinator-fanout-plan.v0.1", "role": "COORDINATOR", "authority": "NONE"},
            "constraints": ["Do not run tools.", "Exactly two tasks with the frozen task_id/target_role/allowed_files/expected_content.",
                            "verification_requirements must describe host-side Git checks per lane and for the integrated candidate."],
        }
        run_ids = self._reserve_wave("wave-coordinator", ["COORDINATOR"])
        neutral = Path(tempfile.mkdtemp(prefix="sscm-coordinator-"))
        try:
            task = self._task_for("COORDINATOR", run_ids["COORDINATOR"], instruction_in, FANOUT_PLAN_SCHEMA, neutral, False, [])
            result = self.executors["COORDINATOR"].run(task)
        finally:
            shutil.rmtree(neutral, ignore_errors=True)
        self._settle("COORDINATOR", result)           # executed usage is evidence before any other gate
        self._record_identity("COORDINATOR", result)
        self._require_session("COORDINATOR", result)
        plan = self._validate_result("COORDINATOR", result, FANOUT_PLAN_SCHEMA, "COORDINATOR_RESULT_INVALID")
        if plan["mission_id"] != self.mission_id:
            raise MissionAborted("COORDINATOR_RESULT_INVALID", "mission identity mismatch")
        got = sorted(plan["tasks"], key=lambda t: t["task_id"])
        want = sorted(spec.tasks, key=lambda t: t.task_id)
        if [t["task_id"] for t in got] != [t.task_id for t in want]:
            raise MissionAborted("COORDINATOR_TOPOLOGY_DRIFT", f"tasks {[t['task_id'] for t in got]} != frozen {[t.task_id for t in want]}")
        for g, w in zip(got, want):
            if g["target_role"] != w.role or sorted(g["allowed_files"]) != [w.target_file] or g["expected_content"] != {w.target_file: w.expected_content}:
                raise MissionAborted("COORDINATOR_SCOPE_DRIFT", f"{w.task_id} instruction deviates from the frozen topology")
        plan_ref = self.run.write_json("artifacts/coordinator-fanout-plan.json", plan)
        publish = self._append(task_id="task:fanout-plan", verb="PUBLISH", actor_ref="actor:coordinator", executor_ref=result.executor_id,
                               session_ref=result.session_ref, target_role="WORKER_A+WORKER_B", base_revision=spec.base_sha,
                               output_refs=[plan_ref, self.report["runs"]["COORDINATOR"]["receipt"]], status="SUCCEEDED",
                               expected_output_schema="sovereign.sscm-implementer-result.v0.1")
        return plan_ref, publish

    def _admit_workers(self) -> dict[str, dict[str, Any]]:
        lanes: dict[str, dict[str, Any]] = {}
        facts: dict[str, WorkspaceFacts] = {}
        for t in self.spec.tasks:
            lane = self.ws.lane(t.lane)
            lane.create_mirror(self.spec.base_sha)
            wt = lane.add_branch_worktree(f"{t.lane}-wt", t.branch, self.spec.base_sha)
            f = lane.facts(wt)
            facts[t.role] = f
            lanes[t.role] = {"task": t, "lane": lane, "worktree": wt, "facts": f}
        iso = assert_isolated(facts)  # raises on any collision / shared object store / canonical / dirty
        canon_git = str((self.ws.canonical / ".git").resolve())
        for role, f in facts.items():
            if Path(f.git_common_dir).resolve() == Path(canon_git):
                raise WorkspaceError("CANONICAL_CHECKOUT_WRITE", f"{role} object store is the canonical .git")
        self.report["parallel"]["workspaces"] = iso
        self.run.write_json("artifacts/worker-workspace-admission.json", iso)
        return lanes

    def _claim_workers(self, lanes, plan_ref, publish) -> dict[str, dict[str, Any]]:
        run_ids = self._reserve_wave("wave-workers", ["WORKER_A", "WORKER_B"])  # atomic: both or neither
        claims = {}
        for role, info in lanes.items():
            t = info["task"]
            claims[role] = self._append(task_id=t.task_id, verb="CLAIM", actor_ref=f"actor:{role.lower()}", executor_ref=self.executors[role].capability().executor_id,
                                        target_role=role, workspace_ref=info["facts"].realpath, base_revision=self.spec.base_sha, input_refs=[plan_ref],
                                        status="ACTIVE", predecessor_event_ids=[publish["event_id"]])
            claims[role]["_run_id"] = run_ids[role]
        return claims

    def _run_workers(self, lanes, claims, plan_ref) -> dict[str, ExecutorRun]:
        plan = self.run.read_json(self.run.rel(self.run.resolve_ref(plan_ref)))
        tasks: dict[str, ExecutorTask] = {}
        for role, info in lanes.items():
            t = info["task"]
            plan_task = next(p for p in plan["tasks"] if p["task_id"] == t.task_id)
            instruction = {
                "mission_id": self.mission_id, "task_id": t.task_id, "role": role, "coordinator_task": plan_task,
                "base_revision": self.spec.base_sha, "worktree": str(info["worktree"]), "branch": t.branch,
                "allowed_files": [t.target_file], "expected_content": {t.target_file: t.expected_content},
                "required_commit": {"message": f"dogfood: SSCM fan-out {t.task_id}", "must_commit": True, "must_not_push": True, "must_leave_worktree_clean": True},
                "forbidden": ["editing any other file", "creating branches", "pushing", "modifying git config", "leaving untracked files"],
                "result_note": "Report the full 40-hex commit SHA from `git rev-parse HEAD`; the host re-verifies it independently.",
            }
            tasks[role] = self._task_for(role, claims[role]["_run_id"], instruction, IMPLEMENTER_SCHEMA, info["worktree"], True, [info["lane"].mirror])
        runs = self._launch_wave(tasks)
        a, b = runs["WORKER_A"], runs["WORKER_B"]
        overlap = max(0.0, min(a.finished_at, b.finished_at) - max(a.started_at, b.started_at))
        self.report["parallel"]["timing"] = {
            "worker_a_started_at": a.started_at, "worker_a_finished_at": a.finished_at,
            "worker_b_started_at": b.started_at, "worker_b_finished_at": b.finished_at,
            "parallel_overlap_seconds": round(overlap, 3),
            "worker_a_run_id": a.run_id, "worker_b_run_id": b.run_id,
            "worker_a_session_ref": a.session_ref, "worker_b_session_ref": b.session_ref,
        }
        return runs

    def _verify_workers(self, lanes, claims, runs) -> dict[str, dict[str, Any]]:
        """Evidence-first post-wave ordering (01B-R1 Part B):

        receipts + usage settlement for BOTH lanes -> session identity gates -> structured results -> host
        verification. A model run that happened always leaves a usage row before any identity or result gate
        can block the mission.
        """
        failures: list[str] = []
        # 1. settle every executed lane first (receipts are written inside _settle); a failed settlement is
        #    recorded and the other lane is still settled.
        for role in ("WORKER_A", "WORKER_B"):
            self._settle_or_note(role, runs[role], failures)
        # 2. identity gates, only now that executed usage is durable
        for role in ("WORKER_A", "WORKER_B"):
            self._record_identity(role, runs[role])
        missing = [role for role in ("WORKER_A", "WORKER_B") if not runs[role].session_ref or not str(runs[role].session_ref).strip()]
        if missing:
            self.report["blockers"].append({"stage": "WAVE", "code": "SESSION_IDENTITY_MISSING", "detail": ", ".join(missing)})
            failures.append("SESSION_IDENTITY_MISSING")
        elif runs["WORKER_A"].session_ref == runs["WORKER_B"].session_ref:
            self.report["blockers"].append({"stage": "WAVE", "code": "SESSION_COLLAPSE", "detail": "both workers report the same session identity"})
            failures.append("SESSION_COLLAPSE")
        if failures:
            # record what each lane produced (COMPLETE events) so the evidence graph is complete, then block
            for role in ("WORKER_A", "WORKER_B"):
                self._complete_lane(lanes, claims, runs, role, failures, record_only=True)
            code = "SESSION_IDENTITY_MISSING" if "SESSION_IDENTITY_MISSING" in failures else ("SESSION_COLLAPSE" if "SESSION_COLLAPSE" in failures else "FANOUT_LANE_FAILED")
            raise MissionAborted(code, "; ".join(failures), DISPOSITION_PARALLEL_BLOCKED)
        # 3. structured results + host verification, frozen order (completion timing is irrelevant)
        observes: dict[str, dict[str, Any]] = {}
        for role in ("WORKER_A", "WORKER_B"):
            observe = self._complete_lane(lanes, claims, runs, role, failures, record_only=False)
            if observe is not None:
                observes[role] = observe
        if failures:
            raise MissionAborted("FANOUT_LANE_FAILED", "; ".join(failures), DISPOSITION_PARALLEL_BLOCKED)
        return observes

    def _complete_lane(self, lanes, claims, runs, role: str, failures: list[str], *, record_only: bool) -> dict[str, Any] | None:
        info, run, claim = lanes[role], runs[role], claims[role]
        t = info["task"]
        result, status, claimed = None, "FAILED", None
        try:
            result = self._validate_result(role, run, IMPLEMENTER_SCHEMA, f"{role}_RESULT_INVALID")
            status = "SUCCEEDED" if result["status"] == "SUCCEEDED" else "FAILED"
            claimed = result.get("claimed_candidate_revision")
        except MissionAborted as ex:
            failures.append(f"{role}:{ex.blocker_code}")
            self.report["blockers"].append({"stage": role, "code": ex.blocker_code, "detail": ex.detail})
        result_ref = self.run.write_json(f"artifacts/{role.lower()}-result.json", result if result is not None else {"unparsed": True, "exit_code": run.exit_code, "error": run.error})
        complete = self._append(task_id=t.task_id, verb="COMPLETE", actor_ref=f"actor:{role.lower()}", executor_ref=run.executor_id, session_ref=run.session_ref,
                                workspace_ref=claim["workspace_ref"], base_revision=self.spec.base_sha, candidate_revision=claimed,
                                output_refs=[result_ref, self.report["runs"][role]["receipt"]], status=status,
                                predecessor_event_ids=[claim["event_id"]], blocker_code=None if status == "SUCCEEDED" else f"{role}_FAILED")
        if record_only or status != "SUCCEEDED":
            if status != "SUCCEEDED":
                failures.append(f"{role}:FAILED")
            return None
        try:
            verification = info["lane"].verify_candidate(info["worktree"], self.spec.base_sha, {t.target_file: t.expected_content})
        except WorkspaceError as ex:
            failures.append(f"{role}:{ex.code}")
            self.report["blockers"].append({"stage": role, "code": ex.code, "detail": ex.detail})
            self._append(task_id=t.task_id, verb="REJECT", actor_ref=HOST_ACTOR, status="FAILED", blocker_code=ex.code, predecessor_event_ids=[complete["event_id"]])
            return None
        verified = verification["verified_candidate_revision"]
        if claimed is not None and claimed != verified:
            failures.append(f"{role}:CANDIDATE_CLAIM_MISMATCH")
            self.report["blockers"].append({"stage": role, "code": "CANDIDATE_CLAIM_MISMATCH", "detail": f"claimed {claimed}, host {verified}"})
            self._append(task_id=t.task_id, verb="REJECT", actor_ref=HOST_ACTOR, status="FAILED", blocker_code="CANDIDATE_CLAIM_MISMATCH", predecessor_event_ids=[complete["event_id"]])
            return None
        verification["claimed_candidate_revision"] = claimed
        ref = self.run.write_json(f"artifacts/host-verification-{role.lower()}.json", verification)
        self.report["parallel"][f"verified_{role.lower()}"] = verification
        return self._append(task_id=t.task_id, verb="OBSERVE", actor_ref=HOST_ACTOR, workspace_ref=claim["workspace_ref"], base_revision=self.spec.base_sha,
                            candidate_revision=verified, output_refs=[ref, artifact_ref("REVISION", f"git:{verified}")], status="SUCCEEDED",
                            predecessor_event_ids=[complete["event_id"]])

    def _settle_or_note(self, role: str, run: ExecutorRun, failures: list[str]) -> None:
        try:
            self._settle(role, run)
        except MissionAborted as ex:
            failures.append(f"{role}:{ex.blocker_code}")
            self.report["blockers"].append({"stage": role, "code": ex.blocker_code, "detail": ex.detail})

    def _join(self, observes: dict[str, dict[str, Any]]) -> dict[str, Any]:
        preds = [observes["WORKER_A"]["event_id"], observes["WORKER_B"]["event_id"]]
        refs = [artifact_ref("REVISION", f"git:{observes[r]['candidate_revision']}") for r in ("WORKER_A", "WORKER_B")]
        try:
            join = self._append(task_id="task:fanin", verb="OBSERVE", actor_ref=HOST_ACTOR, base_revision=self.spec.base_sha,
                                input_refs=refs, status="SUCCEEDED", predecessor_event_ids=preds)
        except JoinNotReady as ex:
            raise MissionAborted(ex.code, ex.detail, DISPOSITION_PARALLEL_BLOCKED) from ex
        self.report["parallel"]["join"] = {"event_id": join["event_id"], "predecessor_event_ids": preds, "state": "JOIN_READY"}
        return join

    def _fan_in(self, lanes, join) -> tuple[str, dict[str, Any]]:
        integ = self.ws.lane("integration")
        integ.create_mirror(self.spec.base_sha)
        verified = {info["task"].task_id: self.report["parallel"][f"verified_{role.lower()}"]["verified_candidate_revision"] for role, info in lanes.items()}
        candidates = []
        for task_id in INTEGRATION_ORDER:
            role = next(r for r, i in lanes.items() if i["task"].task_id == task_id)
            src = lanes[role]["lane"].mirror
            ref_name = task_id.split(":")[-1]
            imported = integ.import_exact(src, verified[task_id], ref_name)
            candidates.append({"task_id": task_id, "verified_sha": verified[task_id], "source_git_common_dir": str(src.resolve()),
                               "imported_ref": f"refs/sscm/import/{ref_name}", "imported_sha": imported})
        result = integ.integrate("integration-wt", self.spec.base_sha, [(c["task_id"], c["verified_sha"]) for c in candidates])
        expected = {t.target_file: t.expected_content for t in self.spec.tasks}
        verification = integ.verify_candidate(Path(result["worktree"]), self.spec.base_sha, expected)
        integrated = verification["verified_candidate_revision"]
        receipt = {
            "schema_version": "sovereign.sscm-integration-receipt.v0.1", "mission_id": self.mission_id, "base_sha": self.spec.base_sha,
            "worker_candidates": candidates, "integration_order": list(INTEGRATION_ORDER), "integration_method": "git cherry-pick, frozen task-id order",
            "integrated_sha": integrated, "changed_files": verification["changed_files"],
            "verification": {"descends_from_base": True, "changed_files_exact": True, "content_exact": True, "worktree_clean": True},
            "authority": "NONE",
        }
        validate(receipt, INTEGRATION_RECEIPT_SCHEMA, "INTEGRATION_RECEIPT_INVALID")
        receipt_ref = self.run.write_json("artifacts/integration-receipt.json", receipt, kind="RECEIPT")
        self.run.write_json("artifacts/host-verification-integrated.json", verification)
        self.report["parallel"]["integration"] = {**receipt, "receipt_ref": receipt_ref, "integration_git_common_dir": str(integ.mirror.resolve())}
        observe = self._append(task_id="task:fanin", verb="OBSERVE", actor_ref=HOST_ACTOR, workspace_ref=result["worktree"], base_revision=self.spec.base_sha,
                               candidate_revision=integrated, output_refs=[receipt_ref, artifact_ref("REVISION", f"git:{integrated}")], status="SUCCEEDED",
                               predecessor_event_ids=[join["event_id"]])
        return integrated, observe

    def _admit_reviewer_lane(self, lanes, integrated: str, observe_int) -> tuple[Path, dict[str, Any]]:
        rev = self.ws.lane("reviewer")
        rev.create_mirror(self.spec.base_sha)
        rev.import_exact(self.ws.lane("integration").mirror, integrated, "integrated")
        wt = rev.add_detached_worktree("reviewer-wt", integrated)
        facts = rev.facts(wt)
        others = {role: info["facts"] for role, info in lanes.items()}
        assert_isolated({**others, "REVIEWER": facts})
        if facts.head_sha != integrated:
            raise MissionAborted("REVIEW_SHA_MISMATCH", f"reviewer worktree at {facts.head_sha}, expected {integrated}")
        self.report["reviewer_workspace"] = facts.as_dict()
        self.run.write_json("artifacts/reviewer-workspace-admission.json", facts.as_dict())
        run_ids = self._reserve_wave("wave-reviewer", ["REVIEWER"])
        claim = self._append(task_id="task:review", verb="CLAIM", actor_ref="actor:reviewer", executor_ref=self.executors["REVIEWER"].capability().executor_id,
                             target_role="REVIEWER", workspace_ref=facts.realpath, base_revision=self.spec.base_sha, candidate_revision=integrated,
                             input_refs=list(observe_int["output_refs"]), status="ACTIVE", predecessor_event_ids=[observe_int["event_id"]])
        claim["_run_id"] = run_ids["REVIEWER"]
        return wt, claim

    def _review_integrated(self, wt: Path, integrated: str, claim, lanes) -> tuple[dict[str, Any], dict[str, Any]]:
        integ = self.report["parallel"]["integration"]
        instruction = {
            "mission_id": self.mission_id, "task_id": "task:review", "role": "REVIEWER",
            "base_revision": self.spec.base_sha,
            "verified_worker_a_revision": self.report["parallel"]["verified_worker_a"]["verified_candidate_revision"],
            "verified_worker_b_revision": self.report["parallel"]["verified_worker_b"]["verified_candidate_revision"],
            "verified_integrated_revision": integrated, "review_worktree": str(wt),
            "expected_changed_files": [t.target_file for t in self.spec.tasks],
            "expected_content": {t.target_file: t.expected_content for t in self.spec.tasks},
            "integration_receipt": {k: integ[k] for k in ("base_sha", "worker_candidates", "integration_order", "integration_method", "integrated_sha", "changed_files")},
            "checklist": [
                "git rev-parse HEAD equals verified_integrated_revision",
                "git merge-base --is-ancestor <base> HEAD succeeds",
                "git diff --name-only <base>..HEAD lists exactly the two expected files",
                "git show HEAD:<file> equals expected content byte-for-byte for both files",
                "the integration receipt's worker_candidates refer to exactly the verified worker revisions",
                "git status --porcelain is empty",
            ],
            "rules": ["Read-only. Do not modify, commit, or repair anything.", "Return REPAIR_REQUIRED with blocking_findings if any check fails."],
        }
        task = self._task_for("REVIEWER", claim["_run_id"], instruction, REVIEWER_SCHEMA, wt, False, [])
        run = self.executors["REVIEWER"].run(task)
        self._settle("REVIEWER", run)                 # executed usage first
        self._record_identity("REVIEWER", run)
        self._require_session("REVIEWER", run)
        coord_session = self.report["parallel"]["identity"]["session_refs"].get("COORDINATOR")
        if run.session_ref == coord_session:
            raise MissionAborted("REVIEW_SESSION_NOT_INDEPENDENT", "reviewer reused the coordinator session", DISPOSITION_PARALLEL_BLOCKED)
        review, status = None, "FAILED"
        try:
            review = self._validate_result("REVIEWER", run, REVIEWER_SCHEMA, "REVIEWER_RESULT_INVALID")
            status = "SUCCEEDED"
        except MissionAborted as ex:
            self.report["blockers"].append({"stage": "REVIEWER", "code": ex.blocker_code, "detail": ex.detail})
        review_ref = self.run.write_json("artifacts/reviewer-result.json", review if review is not None else {"unparsed": True, "exit_code": run.exit_code, "error": run.error}, kind="REVIEW")
        complete = self._append(task_id="task:review", verb="COMPLETE", actor_ref="actor:reviewer", executor_ref=run.executor_id, session_ref=run.session_ref,
                                workspace_ref=claim["workspace_ref"], base_revision=self.spec.base_sha, candidate_revision=integrated, input_refs=list(claim["input_refs"]),
                                output_refs=[review_ref, self.report["runs"]["REVIEWER"]["receipt"]], status=status,
                                predecessor_event_ids=[claim["event_id"]], blocker_code=None if status == "SUCCEEDED" else "REVIEWER_FAILED")
        if review is None:
            raise MissionAborted("REVIEWER_FAILED", run.error or "reviewer produced no valid result", DISPOSITION_PARALLEL_BLOCKED)
        return complete, review

    def _host_verify_review(self, wt: Path, verified: str, review: dict[str, Any], complete: dict[str, Any]) -> dict[str, Any]:
        host = self.ws.lane("reviewer").verify_reviewer(wt, verified)
        if review["reviewed_revision"] != verified:
            raise MissionAborted("REVIEW_SHA_MISMATCH", f"reviewer reported {review['reviewed_revision']}, host verified {verified}")
        host.update({"reviewed_revision_matches": True, "verdict": review["verdict"], "blocking_findings": len(review["blocking_findings"])})
        ref = self.run.write_json("artifacts/host-review-verification.json", host)
        self.report["review_verification"] = host
        return self._append(task_id="task:review", verb="OBSERVE", actor_ref=HOST_ACTOR, workspace_ref=complete["workspace_ref"], base_revision=self.spec.base_sha,
                            candidate_revision=verified, output_refs=[ref], status="SUCCEEDED", predecessor_event_ids=[complete["event_id"]])

    def _terminal(self, review: dict[str, Any], observe: dict[str, Any]) -> None:
        timing = self.report["parallel"].get("timing", {})
        if timing.get("parallel_overlap_seconds", 0) <= 0:
            raise MissionAborted("PARALLEL_OVERLAP_ABSENT", "worker executions did not overlap in wall-clock time", DISPOSITION_PARALLEL_BLOCKED)
        super()._terminal(review, observe)
        self.report["disposition"] = DISPOSITION_PARALLEL_QUALIFIED
