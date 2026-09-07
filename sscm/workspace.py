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
import shutil
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


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


class MissionWorkspaces:
    """All Git state for one mission lives under ``root``; ``destroy()`` removes it."""

    def __init__(self, root: Path, canonical_checkout: Path) -> None:
        self.root = Path(root)
        self.canonical = Path(canonical_checkout).resolve()
        self.mirror = self.root / "mirror.git"

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

    # -- cleanup ---------------------------------------------------------------------

    def destroy(self) -> dict[str, Any]:
        removed: list[str] = []
        if self.mirror.exists():
            for line in git(["worktree", "list", "--porcelain"], cwd=self.mirror, check=False).splitlines():
                if line.startswith("worktree ") and Path(line[9:]).resolve() != self.mirror.resolve():
                    git(["worktree", "remove", "--force", line[9:]], cwd=self.mirror, check=False)
                    removed.append(line[9:])
        if self.root.exists():
            shutil.rmtree(self.root)
        return {"removed_worktrees": removed, "root_removed": not self.root.exists(), "canonical_head": git(["rev-parse", "HEAD"], cwd=self.canonical)}
