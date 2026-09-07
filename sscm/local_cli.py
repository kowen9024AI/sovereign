"""Operator entry point for SSCM.

    python -m sscm.local_cli capabilities
    python -m sscm.local_cli dogfood --base <sha> [--canonical <path>] [--executors live|mock] [--keep]
    python -m sscm.local_cli report <mission-id>
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .blackboard import MissionBudget
from .contracts import REPO_ROOT
from .executors import ClaudeCodeExecutor, CodexExecutor, MockExecutor
from .mission import DogfoodSpec, MissionController
from .workspace import git


def live_executors() -> dict[str, object]:
    claude = ClaudeCodeExecutor()
    return {"COORDINATOR": claude, "IMPLEMENTER": CodexExecutor(), "REVIEWER": ClaudeCodeExecutor()}


def mock_executors(spec: DogfoodSpec) -> dict[str, object]:
    """Offline roster that performs the dogfood mechanically (used for wiring checks and tests)."""
    from .testing import mock_roster

    return mock_roster(spec)


def cmd_capabilities(_: argparse.Namespace) -> int:
    for ex in (ClaudeCodeExecutor(), CodexExecutor()):
        print(json.dumps(ex.capability().as_dict(), indent=1, sort_keys=True))
    return 0


def cmd_dogfood(args: argparse.Namespace) -> int:
    canonical = Path(args.canonical or REPO_ROOT).resolve()
    base = args.base or git(["rev-parse", "HEAD"], cwd=canonical)
    spec = DogfoodSpec(canonical_checkout=canonical, base_sha=base, executor_timeout_seconds=args.timeout,
                       budget=MissionBudget(max_active_actors=1, max_repair_loops=0, max_wall_seconds=args.timeout * 3 + 120))
    executors = live_executors() if args.executors == "live" else mock_executors(spec)
    ctl = MissionController(spec, executors, mission_id=args.mission_id)
    report = ctl.execute(cleanup=not args.keep)
    print(json.dumps({k: report.get(k) for k in ("mission_id", "disposition", "mission_terminal_status", "blockers", "run_dir")}, indent=1))
    return 0 if report.get("disposition") == "SOVEREIGN_SSCM_LOCAL_A2A_QUALIFIED" else 1


def cmd_dogfood_parallel(args: argparse.Namespace) -> int:
    from .parallel import ParallelDogfoodSpec, ParallelMissionController
    from .testing import mock_parallel_roster

    canonical = Path(args.canonical or REPO_ROOT).resolve()
    base = args.base or git(["rev-parse", "HEAD"], cwd=canonical)
    spec = ParallelDogfoodSpec(canonical_checkout=canonical, base_sha=base, executor_timeout_seconds=args.timeout)
    if args.executors == "live":
        executors = {"COORDINATOR": ClaudeCodeExecutor(), "WORKER_A": CodexExecutor(), "WORKER_B": CodexExecutor(), "REVIEWER": ClaudeCodeExecutor()}
    else:
        executors = mock_parallel_roster(spec)
    ctl = ParallelMissionController(spec, executors, mission_id=args.mission_id)
    report = ctl.execute(cleanup=not args.keep)
    summary = {k: report.get(k) for k in ("mission_id", "disposition", "mission_terminal_status", "blockers", "run_dir")}
    summary["parallel_overlap_seconds"] = report.get("parallel", {}).get("timing", {}).get("parallel_overlap_seconds")
    print(json.dumps(summary, indent=1))
    return 0 if report.get("disposition") == "SOVEREIGN_SSCM_PARALLEL_FANOUT_FANIN_QUALIFIED" else 1


def cmd_report(args: argparse.Namespace) -> int:
    p = REPO_ROOT / ".sovereign" / "runs" / args.mission_id / "report.json"
    print(p.read_text())
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="sscm")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("capabilities").set_defaults(fn=cmd_capabilities)
    d = sub.add_parser("dogfood")
    d.add_argument("--base")
    d.add_argument("--canonical")
    d.add_argument("--executors", choices=["live", "mock"], default="live")
    d.add_argument("--timeout", type=int, default=900)
    d.add_argument("--mission-id")
    d.add_argument("--keep", action="store_true", help="retain dogfood worktrees/branch (default: destroy after evidence capture)")
    d.set_defaults(fn=cmd_dogfood)
    dp = sub.add_parser("dogfood-parallel", help="SSCM-01B two-way fan-out / host fan-in")
    for a, kw in (("--base", {}), ("--canonical", {}), ("--executors", {"choices": ["live", "mock"], "default": "live"}),
                  ("--timeout", {"type": int, "default": 900}), ("--mission-id", {}), ("--keep", {"action": "store_true"})):
        dp.add_argument(a, **kw)
    dp.set_defaults(fn=cmd_dogfood_parallel)
    r = sub.add_parser("report")
    r.add_argument("mission_id")
    r.set_defaults(fn=cmd_report)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
