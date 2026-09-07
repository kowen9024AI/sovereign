"""Bounded run directory, artifact references, and the independent credential scan.

Artifacts are ordinary files under ``<repo>/.sovereign/runs/<mission-id>/`` (gitignored).
The blackboard only ever stores ``{kind, ref, sha256}`` for them.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterable, Mapping

from adapters.qm.qm_adapter import CREDENTIAL_PATTERNS, FORBIDDEN_KEY_FRAGMENTS

from .contracts import REPO_ROOT, artifact_ref, canonical_json, file_sha256


class CredentialMaterialSuspected(ValueError):
    def __init__(self, where: str, pattern: str) -> None:
        super().__init__(f"CREDENTIAL_MATERIAL_SUSPECTED: {where} matches <{pattern}>")
        self.code = "CREDENTIAL_MATERIAL_SUSPECTED"
        self.where = where
        self.pattern = pattern


# Keys that legitimately contain the word "token" as a *count*, never a value: ``*_tokens``, ``*token_units``,
# the budget dimension names. Values under these keys are still pattern-scanned like any other string.
_KEY_ALLOWLIST = frozenset({"max_token_or_cost_units", "token_or_cost_units", "observed_cost_or_token_units", "token_units"})
_COUNT_KEY_RE = re.compile(r"^(.*_)?tokens(_incl_cached|_used)?$|^(.*_)?token_units$")


def _key_is_count(key: str) -> bool:
    return key in _KEY_ALLOWLIST or bool(_COUNT_KEY_RE.match(key))


def scan_value(text: str, where: str) -> None:
    for name, pattern in CREDENTIAL_PATTERNS:
        if pattern.search(text):
            raise CredentialMaterialSuspected(where, name)


def scan_json(node: Any, where: str = "$") -> None:
    """Reject secret-shaped values and secret-shaped key names. Trusts no self-report."""
    if isinstance(node, Mapping):
        for k, v in node.items():
            key = str(k)
            low = key.lower()
            if not _key_is_count(key) and low != "credential_material_present":
                for frag in FORBIDDEN_KEY_FRAGMENTS:
                    if frag in low:
                        raise CredentialMaterialSuspected(f"{where}.{key}", f"forbidden key fragment '{frag}'")
            scan_json(v, f"{where}.{key}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            scan_json(v, f"{where}[{i}]")
    elif isinstance(node, str):
        scan_value(node, where)


def scan_file(path: Path) -> None:
    data = path.read_bytes()
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return  # binary artifacts are referenced by digest; nothing textual to scan
    if path.suffix == ".json":
        try:
            scan_json(json.loads(text), f"{path.name}:$")
            return
        except json.JSONDecodeError:
            pass
    for lineno, line in enumerate(text.splitlines(), 1):
        scan_value(line, f"{path.name}:{lineno}")


def scan_tree(paths: Iterable[Path]) -> list[str]:
    scanned: list[str] = []
    for p in paths:
        if p.is_file():
            scan_file(p)
            scanned.append(str(p))
    return scanned


class RunDir:
    """``.sovereign/runs/<mission_id>/`` with artifact writers that return refs."""

    def __init__(self, mission_id: str, root: Path | None = None) -> None:
        self.mission_id = mission_id
        self.root = (root or (REPO_ROOT / ".sovereign" / "runs")) / mission_id
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "artifacts").mkdir(exist_ok=True)
        (self.root / "logs").mkdir(exist_ok=True)

    @property
    def blackboard_path(self) -> Path:
        return self.root / "blackboard.sqlite"

    def path(self, rel: str) -> Path:
        return self.root / rel

    def rel(self, path: Path) -> str:
        return str(path.relative_to(self.root))

    def write_json(self, rel: str, obj: Any, kind: str = "FILE", scan: bool = True) -> dict[str, Any]:
        if scan:
            scan_json(obj, f"{rel}:$")
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(canonical_json(obj) + b"\n")
        return artifact_ref(kind, f"run:{self.mission_id}:{rel}", file_sha256(p))

    def write_text(self, rel: str, text: str, kind: str = "LOG", scan: bool = True) -> dict[str, Any]:
        if scan:
            for lineno, line in enumerate(text.splitlines(), 1):
                scan_value(line, f"{rel}:{lineno}")
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        return artifact_ref(kind, f"run:{self.mission_id}:{rel}", file_sha256(p))

    def read_json(self, rel: str) -> Any:
        return json.loads((self.root / rel).read_text(encoding="utf-8"))

    def resolve_ref(self, ref: Mapping[str, Any]) -> Path:
        m = re.fullmatch(r"run:([^:]+):(.+)", ref["ref"])
        if not m or m.group(1) != self.mission_id:
            raise ValueError(f"ref outside this run dir: {ref['ref']}")
        return self.root / m.group(2)

    def verify_ref(self, ref: Mapping[str, Any]) -> bool:
        p = self.resolve_ref(ref)
        return p.is_file() and (ref.get("sha256") is None or file_sha256(p) == ref["sha256"])

    # Subtrees that are not evidence: Git workspaces (source trees, reviewed by Git identity, and they
    # legitimately contain hostile fixtures) and the SQLite store (scanned via its JSON payloads instead).
    _SCAN_EXCLUDE_DIRS = ("workspaces",)

    def scan_all(self) -> list[str]:
        files = []
        for p in sorted(self.root.rglob("*")):
            if not p.is_file():
                continue
            rel = p.relative_to(self.root)
            if rel.parts and rel.parts[0] in self._SCAN_EXCLUDE_DIRS:
                continue
            if p.suffix == ".sqlite" or p.name.endswith("-wal") or p.name.endswith("-shm"):
                continue
            files.append(p)
        return scan_tree(files)
