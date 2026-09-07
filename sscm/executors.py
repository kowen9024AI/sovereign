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
    # Which budget dimensions this executor can report from runtime-observed output (never model prose).
    usage_observability: dict[str, str] = field(default_factory=lambda: dict(UNOBSERVABLE_USAGE))

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


USAGE_DIMENSIONS = ("executor_turns", "model_calls", "token_units", "cost_units", "wall_seconds")
OBSERVABLE = "OBSERVABLE"
UNOBSERVABLE = "UNOBSERVABLE"
UNOBSERVABLE_USAGE = {d: UNOBSERVABLE for d in USAGE_DIMENSIONS}


def token_units(input_tokens: int | None, output_tokens: int | None) -> float | None:
    """v0.1 normalized usage unit: kilotokens = (all input tokens incl. cached + all output tokens) / 1000.

    Deterministic and provider-neutral; monetary cost is recorded separately as evidence only.
    """
    if input_tokens is None or output_tokens is None:
        return None
    return round((int(input_tokens) + int(output_tokens)) / 1000.0, 3)


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
    executor_turns: int | None = None
    token_units: float | None = None
    usage_detail: dict[str, Any] = field(default_factory=dict)

    @property
    def wall_seconds(self) -> float:
        return self.finished_at - self.started_at

    def usage(self) -> dict[str, Any]:
        """Runtime-observed consumption in budget dimensions. None = not reported by this run."""
        return {
            "executor_turns": self.executor_turns,
            "model_calls": self.model_calls,
            "token_units": self.token_units,
            "cost_units": self.cost_units,
            "wall_seconds": round(self.wall_seconds, 3),
        }

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["wall_seconds"] = round(self.wall_seconds, 3)
        d["usage"] = self.usage()
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
            # --output-format json envelope: num_turns, usage.{input,output,cache_*}_tokens, total_cost_usd
            usage_observability={"executor_turns": OBSERVABLE, "model_calls": OBSERVABLE, "token_units": OBSERVABLE,
                                 "cost_units": OBSERVABLE, "wall_seconds": OBSERVABLE},
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


def _parse_claude(stdout: str, session_hint: str | None) -> tuple[dict[str, Any] | None, str | None, dict[str, Any]]:
    envelope = _extract_json_object(stdout)
    if not envelope:
        return None, session_hint, {}
    structured = envelope.get("structured_output")
    if not isinstance(structured, dict):
        structured = _extract_json_object(str(envelope.get("result", "")))
    session = envelope.get("session_id") or session_hint
    turns = envelope.get("num_turns")
    turns = int(turns) if isinstance(turns, (int, float)) else None
    cost = envelope.get("total_cost_usd")
    u = envelope.get("usage") if isinstance(envelope.get("usage"), dict) else {}
    tin = None
    if all(isinstance(u.get(k), (int, float)) for k in ("input_tokens", "output_tokens")):
        tin = int(u["input_tokens"]) + int(u.get("cache_creation_input_tokens") or 0) + int(u.get("cache_read_input_tokens") or 0)
    usage = {
        # Claude Code reports assistant turns; each is one model response, so it doubles as the model-call count.
        "executor_turns": turns,
        "model_calls": turns,
        "token_units": token_units(tin, u.get("output_tokens") if tin is not None else None),
        "cost_units": float(cost) if isinstance(cost, (int, float)) else None,
        "detail": {k: u.get(k) for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens") if k in u},
    }
    return structured, session, usage


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
            # --json events expose turn.completed{usage} only: turns and tokens are observable, per-call
            # model invocations and monetary cost are not (honestly UNOBSERVABLE, never assumed zero).
            usage_observability={"executor_turns": OBSERVABLE, "model_calls": UNOBSERVABLE, "token_units": OBSERVABLE,
                                 "cost_units": UNOBSERVABLE, "wall_seconds": OBSERVABLE},
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

        return _spawn(self.executor_id, task, cmd, session_hint=None, parse=lambda out, hint: _parse_codex(out, hint, last_path))


def _parse_codex(stdout: str, hint: str | None, last_path: Path) -> tuple[dict[str, Any] | None, str | None, dict[str, Any]]:
    session = hint
    turns = 0
    tin = tout = 0
    saw_usage = False
    for line in stdout.splitlines():
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        t = ev.get("type", "")
        if t == "thread.started" and ev.get("thread_id"):
            session = f"codex-thread:{ev['thread_id']}"
        if t == "turn.completed":
            turns += 1
            u = ev.get("usage") if isinstance(ev.get("usage"), dict) else None
            if u and isinstance(u.get("input_tokens"), (int, float)) and isinstance(u.get("output_tokens"), (int, float)):
                saw_usage = True
                tin += int(u["input_tokens"])  # Codex input_tokens already includes cached input
                tout += int(u["output_tokens"])
    structured = None
    if last_path.exists():
        structured = _extract_json_object(last_path.read_text(encoding="utf-8"))
    usage = {
        "executor_turns": turns if turns else None,
        "model_calls": None,  # UNOBSERVABLE from codex exec --json
        "token_units": token_units(tin, tout) if saw_usage else None,
        "cost_units": None,  # UNOBSERVABLE (subscription)
        "detail": {"input_tokens_incl_cached": tin, "output_tokens": tout} if saw_usage else {},
    }
    return structured, session, usage


# --------------------------------------------------------------------------
# Mock (deterministic, offline; used by the test-suite)
# --------------------------------------------------------------------------


class MockExecutor:
    """Deterministic stand-in. ``behaviour`` maps role -> callable(task) -> result dict.

    ``usage`` maps role -> usage dict (executor_turns, model_calls, token_units, cost_units); missing keys
    are reported as None. ``observability`` overrides the capability's declared dimensions.
    ``log_writer`` maps role -> callable(task) -> text written to the run's durable stdout log, so tests can
    exercise the late-log credential gate.
    """

    DEFAULT_USAGE = {"executor_turns": 1, "model_calls": 1, "token_units": 0.5, "cost_units": 0.0}

    def __init__(self, executor_id: str, runtime_family: str, behaviour: Mapping[str, Callable[[ExecutorTask], dict[str, Any]]],
                 *, usage: Mapping[str, Mapping[str, Any]] | None = None, observability: Mapping[str, str] | None = None,
                 log_writer: Mapping[str, Callable[[ExecutorTask], str]] | None = None) -> None:
        self.executor_id = executor_id
        self.runtime_family = runtime_family
        self.behaviour = dict(behaviour)
        self.usage_by_role = {k: dict(v) for k, v in (usage or {}).items()}
        self.observability = dict(observability) if observability is not None else {d: OBSERVABLE for d in USAGE_DIMENSIONS}
        self.log_writer = dict(log_writer or {})

    def capability(self) -> ExecutorCapability:
        return ExecutorCapability(self.executor_id, self.runtime_family, "AVAILABLE", "NONE", True, "mock-0",
                                  True, True, True, True, True, True, False, True, detail={"mock": True},
                                  usage_observability=dict(self.observability))

    def run(self, task: ExecutorTask) -> ExecutorRun:
        t0 = time.time()
        stdout_path = None
        if task.role in self.log_writer:
            log_dir = task.log_dir or task.cwd
            log_dir.mkdir(parents=True, exist_ok=True)
            p = log_dir / f"{task.run_id}.stdout.log"
            p.write_text(self.log_writer[task.role](task), encoding="utf-8")
            stdout_path = str(p)
        try:
            result = self.behaviour[task.role](task)
            err = None
            code = 0
        except Exception as ex:  # noqa: BLE001 - surfaced as a failed run, never raised through
            result, err, code = None, f"{type(ex).__name__}: {ex}", 1
        u = {**self.DEFAULT_USAGE, **self.usage_by_role.get(task.role, {})}
        return ExecutorRun(self.executor_id, task.run_id, f"mock-session:{task.run_id}", code, t0, time.time(), result,
                           u.get("model_calls"), u.get("cost_units"), stdout_path, None, err,
                           executor_turns=u.get("executor_turns"), token_units=u.get("token_units"), usage_detail={"mock": True})


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
    structured, session, usage = parse(stdout, session_hint)
    if structured is None and error is None and code == 0:
        error = "executor exited 0 without a parsable structured result"
    return ExecutorRun(executor_id, task.run_id, session, code, t0, t1, structured,
                       usage.get("model_calls"), usage.get("cost_units"), str(out_p), str(err_p), error,
                       executor_turns=usage.get("executor_turns"), token_units=usage.get("token_units"),
                       usage_detail=dict(usage.get("detail") or {}))
