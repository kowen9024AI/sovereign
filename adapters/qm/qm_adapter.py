"""QM harness receipt -> Sovereign experience.v0.1 mapping.

Design rules (see adapters/qm/README.md):

* The adapter accepts exactly one input shape: ``qm-harness-receipt.v0.1``.
* Mapping is a pure, deterministic function of the receipt bytes.
* The adapter never contacts a QM deployment. Live reads belong to a later
  maturity step and a separately authorized effect.
* Any suspicion of credential material rejects the whole receipt.
* Output ``authority`` is always ``NONE``. Nothing here can promote anything.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

from jsonschema import Draft202012Validator, FormatChecker

ADAPTER_ID = "sovereign.adapter.qm"
ADAPTER_VERSION = "0.1.0"
RECEIPT_SCHEMA_VERSION = "sovereign.adapter.qm-harness-receipt.v0.1"
EXPERIENCE_SCHEMA_VERSION = "sovereign.experience.v0.1"

_REPO_ROOT = Path(__file__).resolve().parents[2]
RECEIPT_SCHEMA_PATH = _REPO_ROOT / "contracts" / "adapters" / "qm-harness-receipt.v0.1.schema.json"
EXPERIENCE_SCHEMA_PATH = _REPO_ROOT / "contracts" / "experience.v0.1.schema.json"


class AdapterError(ValueError):
    """Base class for adapter rejections."""


class ReceiptRejected(AdapterError):
    """The receipt failed structural validation or a mapping precondition."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


class CredentialMaterialSuspected(ReceiptRejected):
    """Something in the receipt looks like a secret. The receipt is refused whole."""

    def __init__(self, path: str, pattern: str) -> None:
        super().__init__(
            "CREDENTIAL_MATERIAL_SUSPECTED",
            f"value at {path} matches credential pattern <{pattern}>",
        )
        self.path = path
        self.pattern = pattern


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


@lru_cache(maxsize=None)
def _load_schema(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


@lru_cache(maxsize=None)
def _validator(path: Path) -> Draft202012Validator:
    schema = _load_schema(path)
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker())


def _validate(instance: Mapping[str, Any], path: Path, code: str) -> None:
    errors = sorted(_validator(path).iter_errors(instance), key=lambda e: list(e.absolute_path))
    if errors:
        first = errors[0]
        where = "/".join(str(p) for p in first.absolute_path) or "<root>"
        raise ReceiptRejected(code, f"{where}: {first.message}")


def validate_receipt(receipt: Mapping[str, Any]) -> None:
    """Structural validation against ``qm-harness-receipt.v0.1``."""
    _validate(receipt, RECEIPT_SCHEMA_PATH, "RECEIPT_SCHEMA_VIOLATION")


def validate_experience(experience: Mapping[str, Any]) -> None:
    """Structural validation against ``experience.v0.1``."""
    _validate(experience, EXPERIENCE_SCHEMA_PATH, "EXPERIENCE_SCHEMA_VIOLATION")


# ---------------------------------------------------------------------------
# Credential rescan
# ---------------------------------------------------------------------------

# Deliberately conservative. False positives reject a receipt, which is the
# safe direction: the reporter can redact and resubmit.
_CREDENTIAL_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("openai_key", re.compile(r"\bsk-[A-Za-z0-9_-]{16,}")),
    ("anthropic_key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{16,}")),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}")),
    ("github_pat", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}")),
    ("slack_token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}")),
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("bearer", re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{16,}", re.IGNORECASE)),
    ("private_key_block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")),
    ("url_userinfo", re.compile(r"[a-z][a-z0-9+.-]*://[^/\s:@]+:[^/\s@]+@", re.IGNORECASE)),
    ("secret_assignment", re.compile(r"(?i)\b(password|passwd|secret|api[_-]?key|access[_-]?token|refresh[_-]?token)\b\s*[:=]\s*\S+")),
)

_FORBIDDEN_KEY_FRAGMENTS = ("keychain", "secret", "token", "password", "passwd", "cookie", "credential")


def _walk(node: Any, path: str = "$"):
    if isinstance(node, Mapping):
        for k, v in node.items():
            yield f"{path}.{k}", k, True
            yield from _walk(v, f"{path}.{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _walk(v, f"{path}[{i}]")
    else:
        yield path, node, False


def rescan_for_credentials(receipt: Mapping[str, Any]) -> None:
    """Independent scan; the reporter's ``credential_material_present=false`` is not trusted."""
    for path, value, is_key in _walk(receipt):
        if is_key:
            key = str(value).lower()
            if key == "credential_material_present":
                continue
            for frag in _FORBIDDEN_KEY_FRAGMENTS:
                if frag in key:
                    raise CredentialMaterialSuspected(path, f"forbidden key fragment '{frag}'")
            continue
        if not isinstance(value, str):
            continue
        for name, pattern in _CREDENTIAL_PATTERNS:
            if pattern.search(value):
                raise CredentialMaterialSuspected(path, name)


# ---------------------------------------------------------------------------
# Mapping
# ---------------------------------------------------------------------------


def map_outcome(run: Mapping[str, Any]) -> str:
    """QM run terminal state -> Sovereign experience outcome.

    Precedence is fixed and total:

    1. ``stopped``            -> CANCELLED  (principal aborted the turn)
    2. ``status == failed``   -> FAILED
    3. ``paused_on_approval`` -> BLOCKED    (ended waiting on a human)
    4. ``reap_outcome == parked`` -> BLOCKED (queue gave up, no result)
    5. ``status == done``     -> SUCCEEDED

    Note that SUCCEEDED here means *the harness reported completion*. It is
    not evidence of correctness and never implies promotion.
    """
    if run.get("stopped", False):
        return "CANCELLED"
    status = run["status"]
    if status == "failed":
        return "FAILED"
    if run.get("paused_on_approval", False) or run.get("pending_approval_count", 0) > 0:
        return "BLOCKED"
    if run.get("reap_outcome") == "parked":
        return "BLOCKED"
    if status == "done":
        return "SUCCEEDED"
    raise ReceiptRejected("UNMAPPABLE_RUN_STATUS", f"run.status={status!r}")


def _iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def canonical_json(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def receipt_digest(receipt: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json(receipt)).hexdigest()


def receipt_to_experience(receipt: Mapping[str, Any]) -> dict[str, Any]:
    """Pure mapping. Raises ``ReceiptRejected`` rather than emitting a partial candidate."""
    validate_receipt(receipt)
    rescan_for_credentials(receipt)

    run = receipt["run"]
    if run["finished_at_ms"] < run["started_at_ms"]:
        raise ReceiptRejected("TIME_ORDER_VIOLATION", "run.finished_at_ms precedes run.started_at_ms")
    if run["started_at_ms"] < run["created_at_ms"]:
        raise ReceiptRejected("TIME_ORDER_VIOLATION", "run.started_at_ms precedes run.created_at_ms")
    if receipt["security"]["effective_posture"] not in _tightenings(receipt["security"]["org_posture"]):
        raise ReceiptRejected(
            "POSTURE_LOOSENED",
            "effective_posture is looser than org_posture; narrower scopes may only tighten",
        )

    dep = receipt["deployment"]
    scope = receipt["scope"]
    harness = receipt["harness"]
    digest = receipt_digest(receipt)

    evidence_refs: list[str] = [f"qm:receipt:{receipt['receipt_id']}:sha256:{digest}"]
    for tr in receipt["tool_receipts"]:
        evidence_refs.append(
            f"qm:tool-ledger:{run['run_id']}:{tr['attempt']}:{tr['call_index']}:sha256:{tr['output_sha256']}"
        )
    evidence_refs.extend(f"qm:audit:{ref}" for ref in receipt.get("audit_refs", []))

    artifact_refs = [f"qm:{d['kind']}:{d['ref']}" for d in receipt["durable_refs"]]

    experience = {
        "schema_version": EXPERIENCE_SCHEMA_VERSION,
        "experience_id": f"qm:exp:{digest[:32]}",
        "producer": {
            "harness": f"qm:{harness['profile_id']}",
            "agent": f"qm:scope:{scope['scope_id']}",
            "runtime": f"qm:{dep['deployment_ref']}@{dep['qm_version']}",
            "model": harness.get("model_ref"),
        },
        "task_class": receipt.get("task_class", "qm.turn"),
        "scope_ref": f"qm:scope:{scope['scope_id']}",
        "started_at": _iso(run["started_at_ms"]),
        "completed_at": _iso(run["finished_at_ms"]),
        "outcome": map_outcome(run),
        "artifact_refs": artifact_refs,
        "evidence_refs": evidence_refs,
        "procedure_candidate_ref": None,
        "training_candidate_ref": None,
        "authority": "NONE",
    }
    validate_experience(experience)
    return experience


_POSTURE_RANK = {"dangerous": 0, "auto": 1, "strict": 2}


def _tightenings(org_posture: str) -> set[str]:
    floor = _POSTURE_RANK[org_posture]
    return {p for p, r in _POSTURE_RANK.items() if r >= floor}


def _main(argv: list[str]) -> int:  # pragma: no cover - thin CLI
    import sys

    if len(argv) != 2:
        print("usage: python -m adapters.qm <receipt.json>", file=sys.stderr)
        return 2
    with open(argv[1], "r", encoding="utf-8") as fh:
        receipt = json.load(fh)
    try:
        experience = receipt_to_experience(receipt)
    except ReceiptRejected as exc:
        print(json.dumps({"rejected": True, "code": exc.code, "detail": exc.detail}, indent=2))
        return 1
    print(json.dumps(experience, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover
    import sys

    raise SystemExit(_main(sys.argv))
