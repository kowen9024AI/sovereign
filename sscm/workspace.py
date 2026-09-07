"""Disposable Git mirror, isolated worktrees, and host-side verification.

Why a mirror: a ``git worktree`` shares the common object store with its
source repository. An executor that must ``git commit`` therefore needs write
access to that common dir. Giving an autonomous executor write access to the
*canonical* checkout's common dir would let it move canonical refs. So each
mission gets its own bare mirror of the canonical base revision, and every
worktree hangs off that mirror. The canonical checkout is read once (to clone)
and is never a writable root for any executor.

Everything reported here is host-observed via ``git`` and the filesystem.
Model/executor claims about revisions are informational only.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping


class WorkspaceError(ValueError):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


def git(args: list[str], cwd: Path | None = None, check: bool = True) -> str:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env["GIT_TERMINAL_PROMPT"] = "0"
    r = subprocess.run(["git", *args], cwd=str(cwd) if cwd else None, capture_output=True, text=True, env=env)
    if check and r.returncode != 0:
        raise WorkspaceError("GIT_FAILED", f"git {' '.join(args)} -> {r.returncode}: {r.stderr.strip()}")
    return r.stdout.strip()


@dataclass(frozen=True)
class WorkspaceFacts:
    realpath: str
    git_common_dir: str
    head_sha: str
    branch: str  # 'HEAD' when detached
    dirty: bool
    is_canonical_checkout: bool
    untracked: list[str]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def observe(path: Path, canonical_realpath: str) -> WorkspaceFacts:
    real = str(Path(path).resolve())
    common = str((Path(path) / git(["rev-parse", "--git-common-dir"], cwd=path)).resolve())
    head = git(["rev-parse", "HEAD"], cwd=path)
    branch = git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=path)
    status = git(["status", "--porcelain", "--untracked-files=all"], cwd=path)
    untracked = [ln[3:] for ln in status.splitlines() if ln.startswith("??")]
    return WorkspaceFacts(real, common, head, branch, bool(status.strip()), real == canonical_realpath, untracked)


class RepoLane:
    """One independent bare mirror (object store + refs) plus its worktrees under ``root``.

    Every lane is cloned from the canonical checkout at mission start and never shares a writable
    common dir with any other lane: PARALLEL ACTORS != SHARED WRITABLE GIT STATE.
    """

    def __init__(self, root: Path, canonical_checkout: Path, mirror_name: str = "mirror.git") -> None:
        self.root = Path(root)
        self.canonical = Path(canonical_checkout).resolve()
        self.mirror = self.root / mirror_name

    # -- mirror ---------------------------------------------------------------

    def create_mirror(self, base_sha: str) -> str:
        self.root.mkdir(parents=True, exist_ok=True)
        if self.mirror.exists():
            raise WorkspaceError("MIRROR_EXISTS", str(self.mirror))
        git(["clone", "--bare", "--quiet", str(self.canonical), str(self.mirror)])
        # the mirror must contain the exact base and nothing is written back to canonical
        git(["cat-file", "-e", f"{base_sha}^{{commit}}"], cwd=self.mirror)
        git(["remote", "remove", "origin"], cwd=self.mirror, check=False)
        return str(self.mirror.resolve())

    # -- worktrees --------------------------------------------------------------

    def add_branch_worktree(self, name: str, branch: str, base_sha: str) -> Path:
        path = self.root / name
        if path.exists():
            raise WorkspaceError("WORKTREE_EXISTS", str(path))
        git(["worktree", "add", "--quiet", "-b", branch, str(path), base_sha], cwd=self.mirror)
        return path

    def add_detached_worktree(self, name: str, sha: str) -> Path:
        path = self.root / name
        if path.exists():
            raise WorkspaceError("WORKTREE_EXISTS", str(path))
        git(["worktree", "add", "--quiet", "--detach", str(path), sha], cwd=self.mirror)
        return path

    def facts(self, path: Path) -> WorkspaceFacts:
        return observe(path, str(self.canonical))

    def admission_gate(self, path: Path, other_writable: list[Path]) -> WorkspaceFacts:
        f = self.facts(path)
        if f.is_canonical_checkout:
            raise WorkspaceError("CANONICAL_CHECKOUT_WRITE", f.realpath)
        if f.dirty:
            raise WorkspaceError("WORKSPACE_DIRTY_AT_ADMISSION", f.realpath)
        for o in other_writable:
            if Path(o).resolve() == Path(f.realpath) or str(Path(f.realpath)).startswith(str(Path(o).resolve()) + os.sep) or str(Path(o).resolve()).startswith(f.realpath + os.sep):
                raise WorkspaceError("WRITABLE_COLLISION", f"{f.realpath} overlaps {o}")
        return f

    # -- host verification -------------------------------------------------------

    def verify_candidate(self, worktree: Path, base_sha: str, expected_files: dict[str, str]) -> dict[str, Any]:
        """Host truth about the candidate. Raises on any deviation."""
        f = self.facts(worktree)
        head = f.head_sha
        if head == base_sha:
            raise WorkspaceError("NO_CANDIDATE_COMMIT", "HEAD still equals base")
        is_ancestor = subprocess.run(["git", "merge-base", "--is-ancestor", base_sha, head], cwd=str(worktree))
        if is_ancestor.returncode != 0:
            raise WorkspaceError("CANDIDATE_NOT_DESCENDANT", f"{head} does not descend from {base_sha}")
        changed = sorted(git(["diff", "--name-only", f"{base_sha}..{head}"], cwd=worktree).splitlines())
        if changed != sorted(expected_files):
            raise WorkspaceError("CHANGED_FILE_SET_MISMATCH", f"expected {sorted(expected_files)}, observed {changed}")
        for rel, content in expected_files.items():
            observed = subprocess.run(["git", "show", f"{head}:{rel}"], cwd=str(worktree), capture_output=True, text=True)
            if observed.returncode != 0 or observed.stdout != content:
                raise WorkspaceError("CONTENT_MISMATCH", rel)
        if f.dirty:
            raise WorkspaceError("WORKTREE_DIRTY_AFTER_COMMIT", "; ".join(git(["status", "--porcelain"], cwd=worktree).splitlines()))
        commits = git(["rev-list", "--count", f"{base_sha}..{head}"], cwd=worktree)
        return {
            "verified_candidate_revision": head,
            "base_revision": base_sha,
            "descends_from_base": True,
            "changed_files": changed,
            "commit_count": int(commits),
            "worktree_clean": True,
            "worktree": f.as_dict(),
            "source": "host git (rev-parse, merge-base, diff --name-only, show, status --porcelain)",
        }

    def verify_reviewer(self, worktree: Path, verified_sha: str) -> dict[str, Any]:
        f = self.facts(worktree)
        if f.head_sha != verified_sha:
            raise WorkspaceError("REVIEW_SHA_MISMATCH", f"reviewer HEAD {f.head_sha} != {verified_sha}")
        return {"reviewer_head": f.head_sha, "detached": f.branch == "HEAD", "dirty": f.dirty, "worktree": f.as_dict()}

    # -- fan-in primitives (SSCM-01B) --------------------------------------------------------

    def import_exact(self, source_common_dir: Path, sha: str, ref_name: str) -> str:
        """Fetch exactly ``sha`` from another lane's object store into a bounded temporary ref and verify it.

        Branch tips are never trusted: the fetched object must equal the expected full SHA.
        """
        if not re.fullmatch(r"[0-9a-f]{40}", sha):
            raise WorkspaceError("REVISION_NOT_FULL_SHA", sha)
        ref = f"refs/sscm/import/{ref_name}"
        r = subprocess.run(["git", "fetch", "--quiet", "--no-tags", str(source_common_dir), f"{sha}:{ref}"],
                           cwd=str(self.mirror), capture_output=True, text=True)
        if r.returncode != 0:
            raise WorkspaceError("IMPORT_FETCH_FAILED", f"{sha} from {source_common_dir}: {r.stderr.strip()}")
        got = git(["rev-parse", "--verify", ref], cwd=self.mirror)
        if got != sha:
            raise WorkspaceError("IMPORT_SHA_MISMATCH", f"fetched {got}, expected {sha}")
        return got

    def integrate(self, worktree_name: str, base_sha: str, ordered_candidates: list[tuple[str, str]]) -> dict[str, Any]:
        """Deterministic host fan-in: detached worktree at base, cherry-pick each verified SHA in the frozen order.

        Any conflict aborts the cherry-pick and raises FANIN_CONFLICT; no model is consulted.
        """
        wt = self.add_detached_worktree(worktree_name, base_sha)
        applied: list[dict[str, str]] = []
        for task_id, sha in ordered_candidates:
            if not re.fullmatch(r"[0-9a-f]{40}", sha):
                raise WorkspaceError("REVISION_NOT_FULL_SHA", sha)
            git(["cat-file", "-e", f"{sha}^{{commit}}"], cwd=wt)
            r = subprocess.run(["git", "-c", "user.name=sscm-host-fanin", "-c", "user.email=sscm-host@sovereign.local",
                                "cherry-pick", "--allow-empty-message", sha], cwd=str(wt), capture_output=True, text=True)
            if r.returncode != 0:
                subprocess.run(["git", "cherry-pick", "--abort"], cwd=str(wt), capture_output=True)
                raise WorkspaceError("FANIN_CONFLICT", f"cherry-pick of {sha} ({task_id}) failed: {r.stderr.strip()[:400]}")
            applied.append({"task_id": task_id, "source_sha": sha, "integrated_step_sha": git(["rev-parse", "HEAD"], cwd=wt)})
        integrated = git(["rev-parse", "HEAD"], cwd=wt)
        # bounded ref so another lane can import the integrated commit by exact SHA (detached HEADs are not advertised)
        git(["update-ref", f"refs/sscm/integrated/{worktree_name}", integrated], cwd=self.mirror)
        return {"worktree": str(wt.resolve()), "integrated_sha": integrated, "steps": applied, "ref": f"refs/sscm/integrated/{worktree_name}"}

    # -- cleanup ---------------------------------------------------------------------

    def destroy(self) -> dict[str, Any]:
        removed: list[str] = []
        if self.mirror.exists():
            for line in git(["worktree", "list", "--porcelain"], cwd=self.mirror, check=False).splitlines():
                if line.startswith("worktree ") and Path(line[9:]).resolve() != self.mirror.resolve():
                    git(["worktree", "remove", "--force", line[9:]], cwd=self.mirror, check=False)
                    removed.append(line[9:])
            shutil.rmtree(self.mirror, ignore_errors=True)
        return {"removed_worktrees": removed, "mirror_removed": not self.mirror.exists()}


def assert_isolated(lanes: Mapping[str, WorkspaceFacts]) -> dict[str, Any]:
    """Pairwise isolation for concurrent mutating actors, on resolved paths.

    realpaths distinct; git common dirs distinct; no writable path containment; none canonical; none dirty.
    """
    items = sorted(lanes.items())
    for name, f in items:
        if f.is_canonical_checkout:
            raise WorkspaceError("CANONICAL_CHECKOUT_WRITE", f"{name}: {f.realpath}")
        if f.dirty:
            raise WorkspaceError("WORKSPACE_DIRTY_AT_ADMISSION", f"{name}: {f.realpath}")
    for i, (na, a) in enumerate(items):
        for nb, b in items[i + 1:]:
            ra, rb = Path(a.realpath).resolve(), Path(b.realpath).resolve()
            if ra == rb:
                raise WorkspaceError("WRITABLE_COLLISION", f"{na} and {nb} share realpath {ra}")
            if str(ra).startswith(str(rb) + os.sep) or str(rb).startswith(str(ra) + os.sep):
                raise WorkspaceError("WRITABLE_COLLISION", f"{na} and {nb} nest: {ra} / {rb}")
            if Path(a.git_common_dir).resolve() == Path(b.git_common_dir).resolve():
                raise WorkspaceError("SHARED_GIT_COMMON_DIR", f"{na} and {nb} share object store {a.git_common_dir}")
    return {"lanes": {n: f.as_dict() for n, f in items}, "isolated": True}


class MissionWorkspaces(RepoLane):
    """All Git state for one mission under ``root``.

    Behaves as the single default lane (``mirror.git``) for the serial 01A controller and manages additional
    independent lanes (``<name>.git``) for parallel work. ``destroy()`` removes everything under ``root``.
    """

    def __init__(self, root: Path, canonical_checkout: Path) -> None:
        super().__init__(root, canonical_checkout, "mirror.git")
        self.lanes: dict[str, RepoLane] = {}

    def lane(self, name: str) -> RepoLane:
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,40}", name):
            raise WorkspaceError("LANE_NAME_INVALID", name)
        if name not in self.lanes:
            self.lanes[name] = RepoLane(self.root, self.canonical, f"{name}.git")
        return self.lanes[name]

    def destroy(self) -> dict[str, Any]:
        removed: list[str] = []
        # discover lanes on disk too, so a fresh manager (e.g. after restart) still detaches every worktree
        for mirror in sorted(self.root.glob("*.git")) if self.root.exists() else []:
            name = mirror.name[:-4]
            if name != "mirror" and name not in self.lanes:
                self.lanes[name] = RepoLane(self.root, self.canonical, mirror.name)
        for lane in [self, *self.lanes.values()]:
            if lane.mirror.exists():
                for line in git(["worktree", "list", "--porcelain"], cwd=lane.mirror, check=False).splitlines():
                    if line.startswith("worktree ") and Path(line[9:]).resolve() != lane.mirror.resolve():
                        git(["worktree", "remove", "--force", line[9:]], cwd=lane.mirror, check=False)
                        removed.append(line[9:])
        if self.root.exists():
            shutil.rmtree(self.root)
        return {"removed_worktrees": removed, "root_removed": not self.root.exists(), "canonical_head": git(["rev-parse", "HEAD"], cwd=self.canonical)}
