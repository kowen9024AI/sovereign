"""Deterministic offline roster used by tests and by ``local_cli dogfood --executors mock``.

The mock implementer really edits and commits in the worktree it is given, so the
host verification path is exercised for real; only the model is absent.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, Callable

from .executors import ExecutorTask, MockExecutor
from .mission import DogfoodSpec


def _git(args: list[str], cwd: Path) -> str:
    return subprocess.run(["git", "-c", "user.name=sscm-mock", "-c", "user.email=sscm-mock@sovereign.local", *args],
                          cwd=str(cwd), capture_output=True, text=True, check=True).stdout.strip()


def coordinator_ok(task: ExecutorTask) -> dict[str, Any]:
    i = task.instruction
    req = i["required_fields"]
    return {
        "schema_version": req["schema_version"], "mission_id": i["mission_id"], "task_id": i["task_id"],
        "role": "COORDINATOR", "target_role": "IMPLEMENTER", "objective": i["mission_objective"],
        "allowed_files": req["allowed_files"], "expected_content": req["expected_content"],
        "verification_requirements": ["changed file set exact", "content exact", "worktree clean", "descends from base"],
        "commit_required": True, "authority": "NONE",
    }


def implementer_ok(task: ExecutorTask) -> dict[str, Any]:
    i = task.instruction
    for rel, content in i["expected_content"].items():
        p = task.cwd / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        _git(["add", rel], task.cwd)
    _git(["commit", "-q", "-m", i["required_commit"]["message"]], task.cwd)
    sha = _git(["rev-parse", "HEAD"], task.cwd)
    return {"role": "IMPLEMENTER", "status": "SUCCEEDED", "claimed_candidate_revision": sha,
            "changed_files": list(i["expected_content"]), "tests_run": [], "blockers": [], "summary": "mock implementer committed", "authority": "NONE"}


def reviewer_ok(task: ExecutorTask) -> dict[str, Any]:
    i = task.instruction
    head = _git(["rev-parse", "HEAD"], task.cwd)
    changed = sorted(_git(["diff", "--name-only", f"{i['base_revision']}..HEAD"], task.cwd).splitlines())
    findings: list[str] = []
    if head != i["verified_candidate_revision"]:
        findings.append(f"HEAD {head} != verified {i['verified_candidate_revision']}")
    if changed != sorted(i["expected_changed_files"]):
        findings.append(f"changed files {changed}")
    for rel, content in i["expected_content"].items():
        if _git(["show", f"HEAD:{rel}"], task.cwd) + "\n" != content:
            findings.append(f"content mismatch {rel}")
    verdict = "ACCEPT" if not findings else "REPAIR_REQUIRED"
    return {"role": "REVIEWER", "reviewed_revision": head, "verdict": verdict, "findings": findings, "blocking_findings": findings,
            "tests_run": ["git rev-parse HEAD", "git diff --name-only", "git show"], "summary": "mock review", "authority": "NONE"}


def mock_roster(spec: DogfoodSpec, *, coordinator: Callable | None = None, implementer: Callable | None = None, reviewer: Callable | None = None) -> dict[str, MockExecutor]:
    return {
        "COORDINATOR": MockExecutor("executor:mock-claude", "mock-claude", {"COORDINATOR": coordinator or coordinator_ok}),
        "IMPLEMENTER": MockExecutor("executor:mock-codex", "mock-codex", {"IMPLEMENTER": implementer or implementer_ok}),
        "REVIEWER": MockExecutor("executor:mock-claude", "mock-claude", {"REVIEWER": reviewer or reviewer_ok}),
    }


# ---------------------------------------------------------------------------
# SSCM-01B parallel mock roster
# ---------------------------------------------------------------------------

import time as _time


def fanout_coordinator_ok(task: ExecutorTask) -> dict[str, Any]:
    i = task.instruction
    return {
        "schema_version": "sovereign.sscm-coordinator-fanout-plan.v0.1", "mission_id": i["mission_id"], "role": "COORDINATOR",
        "objective": i["mission_objective"],
        "tasks": [{"task_id": t["task_id"], "target_role": t["target_role"], "objective": f"create {t['allowed_files'][0]}",
                   "allowed_files": t["allowed_files"], "expected_content": t["expected_content"], "commit_required": True, "authority": "NONE"}
                  for t in i["frozen_tasks"]],
        "verification_requirements": ["per-lane host git checks", "integrated candidate host git checks"],
        "authority": "NONE",
    }


def worker_ok(task: ExecutorTask) -> dict[str, Any]:
    """Mock worker: really edits and commits in its lane; sleeps briefly so a concurrent wave measurably overlaps."""
    _time.sleep(0.3)
    i = task.instruction
    for rel, content in i["expected_content"].items():
        p = task.cwd / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        _git(["add", rel], task.cwd)
    _git(["commit", "-q", "-m", i["required_commit"]["message"]], task.cwd)
    sha = _git(["rev-parse", "HEAD"], task.cwd)
    return {"role": "IMPLEMENTER", "status": "SUCCEEDED", "claimed_candidate_revision": sha, "changed_files": list(i["expected_content"]),
            "tests_run": [], "blockers": [], "summary": f"mock {task.role} committed", "authority": "NONE"}


def integrated_reviewer_ok(task: ExecutorTask) -> dict[str, Any]:
    i = task.instruction
    head = _git(["rev-parse", "HEAD"], task.cwd)
    changed = sorted(_git(["diff", "--name-only", f"{i['base_revision']}..HEAD"], task.cwd).splitlines())
    findings: list[str] = []
    if head != i["verified_integrated_revision"]:
        findings.append(f"HEAD {head} != verified {i['verified_integrated_revision']}")
    if changed != sorted(i["expected_changed_files"]):
        findings.append(f"changed files {changed}")
    for rel, content in i["expected_content"].items():
        if _git(["show", f"HEAD:{rel}"], task.cwd) + "\n" != content:
            findings.append(f"content mismatch {rel}")
    receipt_shas = {c["verified_sha"] for c in i["integration_receipt"]["worker_candidates"]}
    if receipt_shas != {i["verified_worker_a_revision"], i["verified_worker_b_revision"]}:
        findings.append("receipt worker shas differ from verified worker revisions")
    verdict = "ACCEPT" if not findings else "REPAIR_REQUIRED"
    return {"role": "REVIEWER", "reviewed_revision": head, "verdict": verdict, "findings": findings, "blocking_findings": findings,
            "tests_run": ["git rev-parse HEAD", "git diff --name-only", "git show", "receipt cross-check"], "summary": "mock integrated review", "authority": "NONE"}


def mock_parallel_roster(spec, *, coordinator: Callable | None = None, worker_a: Callable | None = None,
                         worker_b: Callable | None = None, reviewer: Callable | None = None) -> dict[str, MockExecutor]:
    return {
        "COORDINATOR": MockExecutor("executor:mock-claude", "mock-claude", {"COORDINATOR": coordinator or fanout_coordinator_ok}),
        "WORKER_A": MockExecutor("executor:mock-codex", "mock-codex", {"WORKER_A": worker_a or worker_ok}),
        "WORKER_B": MockExecutor("executor:mock-codex", "mock-codex", {"WORKER_B": worker_b or worker_ok}),
        "REVIEWER": MockExecutor("executor:mock-claude", "mock-claude", {"REVIEWER": reviewer or integrated_reviewer_ok}),
    }
