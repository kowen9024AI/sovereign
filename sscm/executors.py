"""Executor-neutral contract plus local adapters (Claude Code, Codex, mock).

SSCM hands an executor a task, a working directory, and an output contract.
The executor authenticates with its provider on its own. SSCM never reads,
copies, transforms, or stores credential material (WO section 17).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol

# Environment keys that must never reach a child executor from this process.
# (Nothing here is *read*; the names are dropped from the inherited env.)
_ENV_DROP_PREFIXES = ("CLAUDECODE", "CLAUDE_CODE_", "ANTHROPIC_", "OPENAI_", "CODEX_", "GITHUB_TOKEN", "GH_TOKEN", "AWS_")
_ENV_DROP_EXACT = {"CLAUDE_CONFIG_DIR", "npm_config_prefix"}


def child_env(extra: Mapping[str, str] | None = None) -> dict[str, str]:
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in _ENV_DROP_EXACT and not any(k.startswith(p) for p in _ENV_DROP_PREFIXES)
    }
    env["SSCM"] = "1"
    if extra:
        env.update(extra)
    return env


@dataclass(frozen=True)
class ExecutorCapability:
    executor_id: str
    runtime_family: str
    availability: str  # AVAILABLE | UNAVAILABLE | UNKNOWN
    native_auth_class: str  # PROVIDER_NATIVE_LOGIN | API_KEY | NONE | UNKNOWN
    native_auth_ready: bool
    version: str | None
    working_directory_support: bool
    read_support: bool
    write_support: bool
    execution_support: bool
    structured_output_support: bool
    session_identity_support: bool
    resume_support: bool
    cancellation_support: bool
    authority: str = "NONE"
    detail: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ExecutorTask:
    role: str
    run_id: str
    cwd: Path
    instruction: dict[str, Any]  # structured; never a transcript
    output_schema: dict[str, Any]
    timeout_seconds: int
    write_allowed: bool
    extra_writable_dirs: list[Path] = field(default_factory=list)
    log_dir: Path | None = None


@dataclass
class ExecutorRun:
    executor_id: str
    run_id: str
    session_ref: str | None
    exit_code: int
    started_at: float
    finished_at: float
    structured_result: dict[str, Any] | None
    model_calls: int | None
    cost_units: float | None
    stdout_path: str | None
    stderr_path: str | None
    error: str | None = None
    authority: str = "NONE"

    @property
    def wall_seconds(self) -> float:
        return self.finished_at - self.started_at

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["wall_seconds"] = round(self.wall_seconds, 3)
        return d


class Executor(Protocol):
    def capability(self) -> ExecutorCapability: ...
    def run(self, task: ExecutorTask) -> ExecutorRun: ...


def _which(name: str) -> str | None:
    return shutil.which(name)


def _run(cmd: list[str], cwd: Path | None = None, timeout: int = 60, env: Mapping[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=str(cwd) if cwd else None, capture_output=True, text=True, timeout=timeout, env=dict(env or child_env()))


def executor_schema(schema: Mapping[str, Any]) -> dict[str, Any]:
    """Schema as handed to an executor CLI.

    Drops meta keys some CLI validators cannot resolve and gives every const/enum an
    explicit ``type`` (strict provider validators require it). Semantics are unchanged.
    """
    def norm(node: Any) -> Any:
        if isinstance(node, dict):
            out = {k: norm(v) for k, v in node.items() if k not in ("$schema", "$id")}
            if ("const" in out or "enum" in out) and "type" not in out:
                vals = [out["const"]] if "const" in out else list(out["enum"])
                if all(isinstance(v, str) for v in vals):
                    out["type"] = "string"
                elif all(isinstance(v, bool) for v in vals):
                    out["type"] = "boolean"
            return out
        if isinstance(node, list):
            return [norm(v) for v in node]
        return node
    return norm(dict(schema))


def _instruction_prompt(task: ExecutorTask) -> str:
    return (
        f"You are the {task.role} in a Sovereign SSCM mission. Follow the structured instruction below exactly. "
        "Your final response MUST be a single JSON object conforming to the given result schema, with no prose around it. "
        "Set authority to \"NONE\". Do not exceed the stated scope.\n\n"
        f"INSTRUCTION (JSON):\n{json.dumps(task.instruction, indent=2, sort_keys=True)}\n\n"
        f"RESULT SCHEMA (JSON Schema):\n{json.dumps(executor_schema(task.output_schema), indent=2, sort_keys=True)}\n"
    )


def _extract_json_object(text: str) -> dict[str, Any] | None:
    text = text.strip()
    if not text:
        return None
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        pass
    # tolerate a fenced block or trailing prose
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        try:
            obj = json.loads(text[start : end + 1])
            return obj if isinstance(obj, dict) else None
        except json.JSONDecodeError:
            return None
    return None


# --------------------------------------------------------------------------
# Claude Code
# --------------------------------------------------------------------------


class ClaudeCodeExecutor:
    executor_id = "executor:claude-code"
    runtime_family = "claude-code"

    def __init__(self, binary: str = "claude", model: str | None = None) -> None:
        self.binary = binary
        self.model = model

    def capability(self) -> ExecutorCapability:
        path = _which(self.binary)
        if not path:
            return ExecutorCapability(self.executor_id, self.runtime_family, "UNAVAILABLE", "UNKNOWN", False, None,
                                      False, False, False, False, False, False, False, False)
        version = None
        auth_ready = False
        detail: dict[str, Any] = {"binary": path}
        try:
            version = _run([path, "--version"]).stdout.strip() or None
            status = _run([path, "auth", "status"])
            try:
                st = json.loads(status.stdout)
                auth_ready = bool(st.get("loggedIn"))
                detail["auth_method"] = st.get("authMethod")  # a label, not a credential
            except json.JSONDecodeError:
                auth_ready = "logged in" in (status.stdout + status.stderr).lower()
            help_text = _run([path, "--help"]).stdout
            for flag in ("--print", "--output-format", "--json-schema", "--tools", "--allowedTools", "--permission-mode", "--session-id", "--restricted"):
                detail[f"flag:{flag}"] = flag in help_text
        except (OSError, subprocess.TimeoutExpired) as ex:
            detail["probe_error"] = str(ex)
        return ExecutorCapability(
            executor_id=self.executor_id, runtime_family=self.runtime_family, availability="AVAILABLE",
            native_auth_class="PROVIDER_NATIVE_LOGIN", native_auth_ready=auth_ready, version=version,
            working_directory_support=True,  # cwd of the process
            read_support=True, write_support=True, execution_support=True,
            structured_output_support=detail.get("flag:--json-schema", False),
            session_identity_support=detail.get("flag:--session-id", False),
            resume_support=True, cancellation_support=True, detail=detail,
        )

    def run(self, task: ExecutorTask) -> ExecutorRun:
        session_id = str(uuid.uuid4())
        if task.write_allowed:
            tools = ["Bash", "Read", "Write", "Edit", "Glob", "Grep"]
            allowed = ["Bash(git:*)", "Bash(cat:*)", "Bash(ls:*)", "Bash(printf:*)", "Bash(echo:*)", "Read", "Write", "Edit", "Glob", "Grep"]
        elif task.role == "COORDINATOR":
            tools, allowed = [""], []
        else:  # reviewer: read-only inspection
            tools = ["Bash", "Read", "Glob", "Grep"]
            allowed = ["Bash(git:*)", "Bash(cat:*)", "Bash(ls:*)", "Bash(wc:*)", "Bash(sha256sum:*)", "Read", "Glob", "Grep"]
        cmd = [
            self.binary, "-p", "--output-format", "json", "--json-schema", json.dumps(executor_schema(task.output_schema)),
            "--permission-mode", "dontAsk", "--session-id", session_id, "--no-session-persistence",
            "--max-turns", str(max(4, min(40, task.timeout_seconds // 15))),
        ]
        # variadic options (--tools, --allowedTools, --add-dir) would swallow a trailing prompt
        # argument, so every option uses the --opt=value form and the prompt goes in on stdin.
        if tools == [""]:
            cmd += ["--tools="]
        else:
            cmd += ["--tools=" + ",".join(tools), "--allowedTools=" + ",".join(allowed), "--restricted"]
        if self.model:
            cmd += ["--model=" + self.model]
        for d in task.extra_writable_dirs:
            cmd += ["--add-dir=" + str(d)]
        return _spawn(self.executor_id, task, cmd, session_hint=session_id, parse=_parse_claude, stdin_text=_instruction_prompt(task))


def _parse_claude(stdout: str, session_hint: str | None) -> tuple[dict[str, Any] | None, str | None, int | None, float | None]:
    envelope = _extract_json_object(stdout)
    if not envelope:
        return None, session_hint, None, None
    structured = envelope.get("structured_output")
    if not isinstance(structured, dict):
        structured = _extract_json_object(str(envelope.get("result", "")))
    session = envelope.get("session_id") or session_hint
    turns = envelope.get("num_turns")
    cost = envelope.get("total_cost_usd")
    return structured, session, (int(turns) if isinstance(turns, (int, float)) else None), (float(cost) if isinstance(cost, (int, float)) else None)


# --------------------------------------------------------------------------
# Codex
# --------------------------------------------------------------------------


class CodexExecutor:
    executor_id = "executor:codex"
    runtime_family = "codex"

    def __init__(self, binary: str = "codex", model: str | None = None) -> None:
        self.binary = binary
        self.model = model

    def capability(self) -> ExecutorCapability:
        path = _which(self.binary)
        if not path:
            return ExecutorCapability(self.executor_id, self.runtime_family, "UNAVAILABLE", "UNKNOWN", False, None,
                                      False, False, False, False, False, False, False, False)
        version = None
        auth_ready = False
        detail: dict[str, Any] = {"binary": path}
        try:
            version = _run([path, "--version"]).stdout.strip() or None
            status = _run([path, "login", "status"])
            text = (status.stdout + status.stderr).lower()
            auth_ready = "logged in" in text and "not logged in" not in text
            detail["auth_label"] = "chatgpt" if "chatgpt" in text else ("api key" if "api key" in text else "unknown")
            help_text = _run([path, "exec", "--help"]).stdout
            for flag in ("--cd", "--add-dir", "--sandbox", "--output-schema", "--output-last-message", "--json", "--ephemeral"):
                detail[f"flag:{flag}"] = flag in help_text
        except (OSError, subprocess.TimeoutExpired) as ex:
            detail["probe_error"] = str(ex)
        return ExecutorCapability(
            executor_id=self.executor_id, runtime_family=self.runtime_family, availability="AVAILABLE",
            native_auth_class="PROVIDER_NATIVE_LOGIN", native_auth_ready=auth_ready, version=version,
            working_directory_support=detail.get("flag:--cd", False),
            read_support=True, write_support=True, execution_support=True,
            structured_output_support=detail.get("flag:--output-schema", False),
            session_identity_support=detail.get("flag:--json", False),
            resume_support=True, cancellation_support=True, detail=detail,
        )

    def run(self, task: ExecutorTask) -> ExecutorRun:
        log_dir = task.log_dir or task.cwd
        schema_path = log_dir / f"{task.run_id}.output-schema.json"
        last_path = log_dir / f"{task.run_id}.last-message.json"
        schema_path.write_text(json.dumps(executor_schema(task.output_schema)), encoding="utf-8")
        cmd = [
            self.binary, "exec", "-C", str(task.cwd),
            "--sandbox", "workspace-write" if task.write_allowed else "read-only",
            "-c", 'approval_policy="never"',
            "--output-schema", str(schema_path), "-o", str(last_path), "--json", "--color", "never",
        ]
        if self.model:
            cmd += ["-m", self.model]
        for d in task.extra_writable_dirs:
            cmd += ["--add-dir", str(d)]
        cmd.append(_instruction_prompt(task))

        def parse(stdout: str, hint: str | None) -> tuple[dict[str, Any] | None, str | None, int | None, float | None]:
            session = hint
            model_calls = 0
            for line in stdout.splitlines():
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                t = ev.get("type", "")
                if t in ("thread.started",) and ev.get("thread_id"):
                    session = f"codex-thread:{ev['thread_id']}"
                if t == "turn.completed" or t.endswith("turn.completed"):
                    model_calls += 1
            structured = None
            if last_path.exists():
                structured = _extract_json_object(last_path.read_text(encoding="utf-8"))
            return structured, session, (model_calls or None), None

        return _spawn(self.executor_id, task, cmd, session_hint=None, parse=parse)


# --------------------------------------------------------------------------
# Mock (deterministic, offline; used by the test-suite)
# --------------------------------------------------------------------------


class MockExecutor:
    """Deterministic stand-in. ``behaviour`` maps role -> callable(task) -> result dict."""

    def __init__(self, executor_id: str, runtime_family: str, behaviour: Mapping[str, Callable[[ExecutorTask], dict[str, Any]]]) -> None:
        self.executor_id = executor_id
        self.runtime_family = runtime_family
        self.behaviour = dict(behaviour)

    def capability(self) -> ExecutorCapability:
        return ExecutorCapability(self.executor_id, self.runtime_family, "AVAILABLE", "NONE", True, "mock-0",
                                  True, True, True, True, True, True, False, True, detail={"mock": True})

    def run(self, task: ExecutorTask) -> ExecutorRun:
        t0 = time.time()
        try:
            result = self.behaviour[task.role](task)
            err = None
            code = 0
        except Exception as ex:  # noqa: BLE001 - surfaced as a failed run, never raised through
            result, err, code = None, f"{type(ex).__name__}: {ex}", 1
        return ExecutorRun(self.executor_id, task.run_id, f"mock-session:{task.run_id}", code, t0, time.time(), result, 1, 0.0, None, None, err)


# --------------------------------------------------------------------------
# shared spawn
# --------------------------------------------------------------------------


def _spawn(executor_id: str, task: ExecutorTask, cmd: list[str], *, session_hint: str | None, parse, stdin_text: str | None = None) -> ExecutorRun:
    log_dir = task.log_dir or task.cwd
    log_dir.mkdir(parents=True, exist_ok=True)
    out_p = log_dir / f"{task.run_id}.stdout.log"
    err_p = log_dir / f"{task.run_id}.stderr.log"
    t0 = time.time()
    try:
        with out_p.open("w", encoding="utf-8") as out, err_p.open("w", encoding="utf-8") as err:
            proc = subprocess.run(cmd, cwd=str(task.cwd), stdout=out, stderr=err,
                                  input=stdin_text, stdin=None if stdin_text is not None else subprocess.DEVNULL,
                                  env=child_env(), timeout=task.timeout_seconds, text=True)
        code = proc.returncode
        error = None
    except subprocess.TimeoutExpired:
        code, error = 124, f"timeout after {task.timeout_seconds}s"
    except OSError as ex:
        code, error = 127, str(ex)
    t1 = time.time()
    stdout = out_p.read_text(encoding="utf-8", errors="replace") if out_p.exists() else ""
    structured, session, calls, cost = parse(stdout, session_hint)
    if structured is None and error is None and code == 0:
        error = "executor exited 0 without a parsable structured result"
    return ExecutorRun(executor_id, task.run_id, session, code, t0, t1, structured, calls, cost, str(out_p), str(err_p), error)
