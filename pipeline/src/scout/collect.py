"""Repository acquisition and git history extraction.

Clones are shallow and cached under ``.cache/repos``. A second run fetches
instead of cloning, which is what makes the incremental scan cheap.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .util import log

_RECORD_SEP = "\x1e"
_FIELD_SEP = "\x1f"
# The record separator has to come FIRST. With `--numstat`, git prints the format
# string and then the file statistics of that same commit, so a trailing
# separator would attach every commit's statistics to the next record.
_LOG_FORMAT = "%x1e" + _FIELD_SEP.join(["%H", "%an", "%aI", "%s", "%b"])


class GitError(RuntimeError):
    pass


@dataclass
class Commit:
    sha: str
    author: str
    date: str
    subject: str
    body: str
    files: list[str] = field(default_factory=list)
    insertions: int = 0
    deletions: int = 0

    @property
    def message(self) -> str:
        return (self.subject + "\n" + self.body).strip()

    def to_dict(self) -> dict[str, Any]:
        return {
            "sha": self.sha,
            "author": self.author,
            "date": self.date,
            "subject": self.subject,
            "body": self.body,
            "files": self.files,
            "insertions": self.insertions,
            "deletions": self.deletions,
        }


def _run(args: list[str], cwd: Path | None = None, timeout: int = 900) -> tuple[int, str, str]:
    result = subprocess.run(
        args, cwd=str(cwd) if cwd else None, capture_output=True,
        text=True, encoding="utf-8", errors="replace", timeout=timeout, check=False,
    )
    return result.returncode, result.stdout or "", result.stderr or ""


@dataclass
class DiffText:
    """The lines a commit removed and the lines it added.

    Removed lines are the interesting half: they are what the author replaced,
    which is the only place the previous behaviour is recorded when the commit
    message does not describe it.
    """

    sha: str
    removed: list[str] = field(default_factory=list)
    added: list[str] = field(default_factory=list)
    paths: list[str] = field(default_factory=list)
    truncated: bool = False

    @property
    def removed_text(self) -> str:
        return "\n".join(self.removed)

    @property
    def added_text(self) -> str:
        return "\n".join(self.added)

    def __bool__(self) -> bool:
        return bool(self.removed or self.added)


@dataclass
class GitRepo:
    full_name: str
    clone_url: str
    path: Path

    # ------------------------------------------------------------------
    def ensure(self, depth: int = 400) -> str:
        """Clone or update the working copy. Returns ``"clone"`` or ``"fetch"``."""
        if (self.path / ".git").exists():
            code, _, err = _run(
                ["git", "fetch", "--depth", str(depth), "--force", "origin", "HEAD"],
                cwd=self.path,
            )
            if code != 0:
                log.warning("fetch failed for %s: %s", self.full_name, err.strip()[:200])
            else:
                _run(["git", "checkout", "--force", "FETCH_HEAD"], cwd=self.path)
                return "fetch"
            # A broken cache is cheaper to discard than to repair.
            shutil.rmtree(self.path, ignore_errors=True)

        self.path.parent.mkdir(parents=True, exist_ok=True)
        code, _, err = _run([
            "git", "clone", "--depth", str(depth), "--single-branch",
            "--no-tags", self.clone_url, str(self.path),
        ])
        if code != 0:
            raise GitError(f"clone failed for {self.full_name}: {err.strip()[:300]}")
        return "clone"

    # ------------------------------------------------------------------
    def head_sha(self) -> str | None:
        code, out, _ = _run(["git", "rev-parse", "HEAD"], cwd=self.path)
        return out.strip() if code == 0 else None

    def current_branch(self) -> str:
        code, out, _ = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=self.path)
        name = out.strip() if code == 0 else ""
        return name if name and name != "HEAD" else "HEAD"

    def has_commit(self, sha: str) -> bool:
        if not sha:
            return False
        code, _, _ = _run(["git", "cat-file", "-e", sha + "^{commit}"], cwd=self.path)
        return code == 0

    # ------------------------------------------------------------------
    def commits(self, max_count: int = 400, since_sha: str | None = None) -> list[Commit]:
        """Read commit metadata, optionally only what is new since ``since_sha``."""
        args = ["git", "log", f"--max-count={max_count}", f"--format={_LOG_FORMAT}",
                "--numstat", "--no-merges"]
        if since_sha and self.has_commit(since_sha):
            args.append(f"{since_sha}..HEAD")
        code, out, err = _run(args, cwd=self.path)
        if code != 0:
            log.warning("git log failed for %s: %s", self.full_name, err.strip()[:200])
            return []
        return _parse_log(out)

    def changed_files(self, base_sha: str) -> list[str] | None:
        """Files touched between ``base_sha`` and HEAD, or ``None`` if unknown."""
        if not self.has_commit(base_sha):
            return None
        code, out, _ = _run(["git", "diff", "--name-only", f"{base_sha}..HEAD"], cwd=self.path)
        if code != 0:
            return None
        return [line.strip() for line in out.splitlines() if line.strip()]

    def first_commit_for_path(self, rel_path: str) -> tuple[str, str] | None:
        """Oldest commit in the available history that touched ``rel_path``."""
        code, out, _ = _run(
            ["git", "log", "--reverse", "--max-count=1", "--format=%H%x1f%aI", "--", rel_path],
            cwd=self.path,
        )
        if code != 0 or not out.strip():
            return None
        parts = out.strip().split("\x1f")
        return (parts[0], parts[1]) if len(parts) == 2 else None

    def blame_commit(self, rel_path: str, line: int) -> str | None:
        """Commit that last touched a line. Used to date a piece of code."""
        code, out, _ = _run(
            ["git", "blame", "-L", f"{line},{line}", "--porcelain", "--", rel_path],
            cwd=self.path,
        )
        if code != 0 or not out.strip():
            return None
        return out.split()[0][:40] or None

    def is_shallow(self) -> bool:
        return (self.path / ".git" / "shallow").exists()

    # ------------------------------------------------------------------
    def diff_text(self, sha: str, max_lines: int = 80,
                  max_line_length: int = 200,
                  accept_path: Callable[[str], bool] | None = None) -> DiffText:
        """Read what a commit removed and added.

        Uses ``-U0`` so only changed lines come back, not surrounding context:
        context lines are unchanged code and would dilute the signal that the
        removed lines carry. Output is capped, because one refactor commit can
        otherwise dominate an entire scan.

        ``accept_path`` applies the same include and exclude rules the file scan
        uses. Without it the diff would read generated metadata and vendored
        trees that the rest of the pipeline deliberately ignores, and a term
        inside an enumeration there would be mistaken for a replaced mechanism.
        """
        result = DiffText(sha=sha)
        code, out, _ = _run(
            ["git", "show", "--format=", "--unified=0", "--no-color",
             "--no-renames", sha],
            cwd=self.path,
        )
        if code != 0:
            return result

        in_scope = True
        for line in out.splitlines():
            if line.startswith("diff --git "):
                # `diff --git a/<path> b/<path>`
                parts = line.split(" b/", 1)
                path = parts[1] if len(parts) == 2 else ""
                in_scope = accept_path(path) if (accept_path and path) else True
                if in_scope and path and path not in result.paths:
                    result.paths.append(path)
                continue
            if not in_scope:
                continue
            if line.startswith(("---", "+++", "@@", "index ", "similarity ",
                                "new file", "deleted file", "old mode",
                                "new mode", "Binary files")):
                continue

            if len(result.removed) + len(result.added) >= max_lines:
                result.truncated = True
                break

            if line.startswith("-"):
                body = line[1:].strip()
                if body:
                    result.removed.append(body[:max_line_length])
            elif line.startswith("+"):
                body = line[1:].strip()
                if body:
                    result.added.append(body[:max_line_length])

        return result


def _parse_log(raw: str) -> list[Commit]:
    commits: list[Commit] = []
    for chunk in raw.split(_RECORD_SEP):
        chunk = chunk.strip("\n")
        if not chunk.strip():
            continue
        head, _, numstat = chunk.partition("\n")
        fields = head.split(_FIELD_SEP)
        if len(fields) < 4:
            continue
        sha, author, date, subject = fields[0], fields[1], fields[2], fields[3]
        body = fields[4] if len(fields) > 4 else ""
        # The body may itself contain newlines; anything after it is numstat.
        body_lines: list[str] = []
        stat_lines: list[str] = []
        for line in ([body] if body else []) + numstat.splitlines():
            parts = line.split("\t")
            if len(parts) == 3 and (parts[0].isdigit() or parts[0] == "-"):
                stat_lines.append(line)
            elif line.strip():
                body_lines.append(line)

        commit = Commit(
            sha=sha.strip(), author=author.strip(), date=date.strip(),
            subject=subject.strip(), body="\n".join(body_lines).strip(),
        )
        for line in stat_lines:
            added, removed, path = line.split("\t")
            commit.files.append(path)
            commit.insertions += int(added) if added.isdigit() else 0
            commit.deletions += int(removed) if removed.isdigit() else 0
        commits.append(commit)
    return commits
