"""Evidence construction.

This is the only stage allowed to assert that something exists in a repository.
Every later stage may group, score and describe evidence, but may not add a new
fact. An evidence record always carries enough location information for a reader
to open the exact place it came from.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from . import github as gh
from .collect import Commit, DiffText, GitRepo
from .config import TargetDefaults
from .lexicon import LEXICON, split_identifier
from .lexicon.terms import CONFIG_EXTENSIONS, CONFIG_PATH_HINTS, DOC_EXTENSIONS
from .models import Evidence, Signal
from .resolver import ResolvedRepo
from .symbols import Symbol, extract_symbols, fallback_symbols, language_for
from .util import log, looks_like_comment, match_any, read_text, stable_id, truncate


@dataclass
class RepoEvidence:
    repo: ResolvedRepo
    items: list[Evidence] = field(default_factory=list)
    files_scanned: int = 0
    files_skipped: int = 0
    symbols_seen: int = 0
    notes: list[str] = field(default_factory=list)


def _kinds_present(signals: Iterable[Signal]) -> set[str]:
    return {s.kind for s in signals}


def _is_interesting(signals: list[Signal]) -> bool:
    """Decide whether a location is worth recording as evidence.

    A mechanism inside the camera domain is interesting. So is a stated problem
    inside the camera domain. Anything else is ordinary code.
    """
    kinds = _kinds_present(signals)
    if "domain" not in kinds:
        return False
    return "mechanism" in kinds or "problem" in kinds


def _prior_signals(diff: DiffText) -> list[Signal]:
    """Mechanism vocabulary a commit removed and did not put back.

    A term that appears in the removed lines but not in the added lines is the
    strongest available evidence of what the code used to do. A term present on
    both sides was merely moved, so it says nothing about a change of approach.

    The context of each signal is the removed line itself, so a narrative can
    quote the replaced code verbatim instead of describing it.
    """
    if not diff.removed:
        return []

    # A commit that deleted only comments reworded its documentation; it did not
    # replace a mechanism. Requiring at least one deleted line of real code is
    # what separates "this approach was removed" from "this sentence was edited".
    code_lines = [line for line in diff.removed if not looks_like_comment(line)]
    if not code_lines:
        return []

    added_terms = {
        s.term for s in LEXICON.match(_searchable(diff.added_text), kinds=("mechanism",))
    }
    out: list[Signal] = []
    for signal in LEXICON.match(_searchable(diff.removed_text), kinds=("mechanism",)):
        if signal.term in added_terms:
            continue
        # Prefer quoting the deleted code over the deleted comment about it.
        line = (
            _line_containing(code_lines, signal.term)
            or _line_containing(diff.removed, signal.term)
            or signal.context
        )
        out.append(
            Signal(kind="prior", term=signal.term, family=signal.family,
                   weight=signal.weight, context=truncate(line, 200))
        )
    return out


def _searchable(text: str) -> str:
    """Raw text plus its identifier-split form.

    Most mechanism vocabulary in C++ lives inside camelCase identifiers, where
    the lexicon cannot see it: the word boundary before `Interpolated` in
    `getInterpolated` is a letter, so the term never matches. Appending the
    split form makes those terms visible without losing the raw line, which is
    what gets quoted.
    """
    if not text:
        return ""
    return text + "\n" + split_identifier(text)


def _line_containing(lines: list[str], term: str) -> str | None:
    """Find the line a term came from, matching camelCase as well as prose."""
    lowered = term.lower()
    for line in lines:
        if lowered in line.lower() or lowered in split_identifier(line):
            return line
    return None


def _best_context(signals: list[Signal]) -> str:
    ranked = sorted(
        signals,
        key=lambda s: ({"mechanism": 0, "problem": 1, "effect": 2}.get(s.kind, 3), -s.weight),
    )
    for signal in ranked:
        if signal.context:
            return truncate(signal.context, 300)
    return ""


class EvidenceBuilder:
    """Builds evidence for one repository."""

    def __init__(self, repo: ResolvedRepo, git: GitRepo, settings: TargetDefaults,
                 ref: str = "HEAD") -> None:
        self.repo = repo
        self.git = git
        self.settings = settings
        self.ref = ref
        self.result = RepoEvidence(repo=repo)

    # ------------------------------------------------------------------
    def build(self, changed_files: list[str] | None, commits: list[Commit],
              pulls: list[dict[str, Any]], issues: list[dict[str, Any]],
              releases: list[dict[str, Any]]) -> RepoEvidence:
        if self.settings.analysis.code or self.settings.analysis.documents:
            self._scan_files(changed_files)
        if self.settings.analysis.commits:
            self._scan_commits(commits)
        if self.settings.analysis.pull_requests:
            self._scan_threads(pulls, "pull_request")
        if self.settings.analysis.issues:
            self._scan_threads(issues, "issue")
        if self.settings.analysis.releases:
            self._scan_releases(releases)
        return self.result

    # ------------------------------------------------------------------
    # Files
    # ------------------------------------------------------------------
    def _candidate_paths(self, changed_files: list[str] | None) -> list[str]:
        include = self.settings.include
        exclude = self.settings.exclude
        limits = self.settings.limits

        if changed_files is not None:
            pool = changed_files
            self.result.notes.append(
                f"incremental file scan over {len(changed_files)} changed paths"
            )
        else:
            pool = []
            root = self.git.path
            for path in root.rglob("*"):
                if not path.is_file():
                    continue
                rel = path.relative_to(root).as_posix()
                if rel.startswith(".git/"):
                    continue
                pool.append(rel)

        selected: list[str] = []
        for rel in pool:
            if exclude and match_any(rel, exclude):
                self.result.files_skipped += 1
                continue
            if include and not match_any(rel, include):
                self.result.files_skipped += 1
                continue
            selected.append(rel)
            if len(selected) >= limits.max_files:
                self.result.notes.append(
                    f"file budget of {limits.max_files} reached, scan is partial"
                )
                break
        return selected

    def _scan_files(self, changed_files: list[str] | None) -> None:
        limits = self.settings.limits
        for rel in self._candidate_paths(changed_files):
            absolute = self.git.path / rel
            if not absolute.exists():
                continue
            text = read_text(absolute, limits.max_file_bytes)
            if text is None:
                self.result.files_skipped += 1
                continue
            self.result.files_scanned += 1

            suffix = Path(rel).suffix.lower()
            # Configuration is checked first on purpose: `CMakeLists.txt` ends in
            # `.txt` and would otherwise be treated as a document.
            if suffix in CONFIG_EXTENSIONS or _looks_like_config(rel):
                self._scan_config(rel, text)
            elif suffix in DOC_EXTENSIONS:
                if self.settings.analysis.documents:
                    self._scan_document(rel, text)
            elif self.settings.analysis.code:
                self._scan_code(rel, text)

    def _scan_code(self, rel: str, text: str) -> None:
        language = language_for(rel)
        symbols: list[Symbol]
        if language:
            symbols = extract_symbols(text, language)
            if not symbols:
                symbols = fallback_symbols(text)
        else:
            symbols = fallback_symbols(text)
        self.result.symbols_seen += len(symbols)

        produced = 0
        for symbol in symbols:
            haystack = split_identifier(symbol.name) + "\n" + symbol.searchable
            signals = LEXICON.match(haystack)
            if not _is_interesting(signals):
                continue
            self._add(
                kind="code",
                path=rel,
                symbol=symbol.name,
                symbol_kind=symbol.kind,
                line_start=symbol.line_start,
                line_end=symbol.line_end,
                title=f"{symbol.kind} {symbol.name}",
                snippet=truncate(symbol.doc or _best_context(signals) or symbol.text, 320),
                signals=signals,
                url=gh.blob_url(self.repo.full_name, self.ref, rel,
                                symbol.line_start, symbol.line_end),
            )
            produced += 1
            if produced >= 25:      # one file should not dominate a repository
                break

    def _scan_document(self, rel: str, text: str) -> None:
        for section_title, start_line, body in _split_sections(text):
            signals = LEXICON.match(section_title + "\n" + body)
            if not _is_interesting(signals):
                continue
            self._add(
                kind="doc",
                path=rel,
                symbol=section_title or None,
                symbol_kind="section",
                line_start=start_line,
                line_end=start_line,
                title=section_title or Path(rel).name,
                snippet=truncate(_best_context(signals) or body, 320),
                signals=signals,
                url=gh.blob_url(self.repo.full_name, self.ref, rel, start_line),
            )

    def _scan_config(self, rel: str, text: str) -> None:
        signals = LEXICON.match(text, kinds=("domain", "mechanism"))
        if "mechanism" not in _kinds_present(signals):
            return
        self._add(
            kind="config",
            path=rel,
            symbol=None,
            symbol_kind=None,
            line_start=None,
            line_end=None,
            title=Path(rel).name,
            snippet=truncate(_best_context(signals), 240),
            signals=signals,
            url=gh.blob_url(self.repo.full_name, self.ref, rel),
        )

    # ------------------------------------------------------------------
    # History and conversations
    # ------------------------------------------------------------------
    def _in_scope(self, path: str) -> bool:
        """The include and exclude rules, reused by the diff reader."""
        if self.settings.exclude and match_any(path, self.settings.exclude):
            return False
        if self.settings.include and not match_any(path, self.settings.include):
            return False
        return True

    def _scan_commits(self, commits: list[Commit]) -> None:
        """Build evidence from commit messages and, where affordable, diffs.

        The diff matters because the commit message is usually the only place a
        repository records what the code used to do, and most authors do not
        write it down. The removed lines do record it, so they are read for the
        newest commits and mined for mechanism vocabulary that the added lines
        no longer contain.
        """
        limits = self.settings.limits
        for index, commit in enumerate(commits):
            diff = None
            if index < limits.max_diff_commits:
                diff = self.git.diff_text(
                    commit.sha,
                    max_lines=limits.max_diff_lines,
                    accept_path=self._in_scope,
                )

            haystack = commit.message
            if diff:
                haystack = "\n".join([commit.message, diff.removed_text, diff.added_text])

            signals = LEXICON.match(haystack)
            prior = _prior_signals(diff) if diff else []
            if not _is_interesting(signals):
                continue

            snippet = truncate(commit.message, 320)
            if prior:
                snippet = truncate(
                    f"{commit.message}\n[제거된 코드] {prior[0].context}", 400
                )

            self._add(
                kind="commit",
                path=commit.files[0] if commit.files else None,
                symbol=None,
                symbol_kind=None,
                line_start=None,
                line_end=None,
                commit_sha=commit.sha,
                title=truncate(commit.subject, 140),
                snippet=snippet,
                observed_at=commit.date,
                signals=signals + prior,
                url=gh.commit_url(self.repo.full_name, commit.sha),
                extra_id_parts=(commit.sha,),
            )

    def _scan_threads(self, threads: list[dict[str, Any]], kind: str) -> None:
        for thread in threads:
            number = thread.get("number")
            if number is None:
                continue
            body = thread.get("body") or ""
            title = thread.get("title") or ""
            signals = LEXICON.match(title + "\n" + body)
            if not _is_interesting(signals):
                continue
            url = (gh.pull_url if kind == "pull_request" else gh.issue_url)(
                self.repo.full_name, int(number)
            )
            label = "PR" if kind == "pull_request" else "Issue"
            self._add(
                kind=kind,
                path=None,
                symbol=None,
                symbol_kind=None,
                line_start=None,
                line_end=None,
                ref=f"{label} #{number}",
                title=truncate(title, 160),
                snippet=truncate(_best_context(signals) or body, 320),
                observed_at=thread.get("updated_at") or thread.get("created_at"),
                signals=signals,
                url=url,
                extra_id_parts=(str(number),),
            )

    def _scan_releases(self, releases: list[dict[str, Any]]) -> None:
        for release in releases:
            tag = release.get("tag_name") or release.get("name")
            if not tag:
                continue
            body = release.get("body") or ""
            signals = LEXICON.match((release.get("name") or "") + "\n" + body)
            if not _is_interesting(signals):
                continue
            self._add(
                kind="release",
                path=None,
                symbol=None,
                symbol_kind=None,
                line_start=None,
                line_end=None,
                ref=str(tag),
                title=truncate(release.get("name") or str(tag), 140),
                snippet=truncate(_best_context(signals) or body, 320),
                observed_at=release.get("published_at") or release.get("created_at"),
                signals=signals,
                url=gh.release_url(self.repo.full_name, str(tag)),
                extra_id_parts=(str(tag),),
            )

    # ------------------------------------------------------------------
    def _add(self, *, kind: str, path: str | None, symbol: str | None,
             symbol_kind: str | None, line_start: int | None, line_end: int | None,
             title: str, snippet: str, signals: list[Signal], url: str,
             commit_sha: str | None = None, ref: str | None = None,
             observed_at: str | None = None,
             extra_id_parts: tuple[str, ...] = ()) -> None:
        identity = (self.repo.full_name, kind, path or "", symbol or "", *extra_id_parts)
        self.result.items.append(
            Evidence(
                id=stable_id("ev", *identity),
                org=self.repo.org,
                repo=self.repo.name,
                kind=kind,
                path=path,
                symbol=symbol,
                symbol_kind=symbol_kind,
                line_start=line_start,
                line_end=line_end,
                commit_sha=commit_sha,
                ref=ref,
                title=title,
                snippet=snippet,
                url=url,
                observed_at=observed_at,
                signals=signals,
            )
        )


def enrich_first_seen(evidence: list[Evidence], git: GitRepo, budget: int = 250) -> int:
    """Date code evidence with the commit that last touched its first line.

    Running ``git blame`` is expensive, so it is applied only to evidence that
    survived into a candidate, and only up to ``budget`` records.
    """
    done = 0
    for item in evidence:
        if done >= budget:
            break
        if item.kind != "code" or not item.path or not item.line_start:
            continue
        if item.first_seen_commit:
            continue
        found = git.first_commit_for_path(item.path)
        if found:
            item.first_seen_commit, item.first_seen_at = found
            if not item.observed_at:
                item.observed_at = found[1]
            done += 1
    return done


def _looks_like_config(rel: str) -> bool:
    lowered = rel.lower()
    return any(hint in lowered for hint in CONFIG_PATH_HINTS)


def _split_sections(text: str, max_sections: int = 60) -> list[tuple[str, int, str]]:
    """Split Markdown or reStructuredText into (heading, line, body) tuples."""
    lines = text.splitlines()
    sections: list[tuple[str, int, list[str]]] = []
    current_title = ""
    current_line = 1
    current_body: list[str] = []

    for index, line in enumerate(lines):
        stripped = line.strip()
        is_md_heading = stripped.startswith("#")
        is_rst_heading = (
            index > 0
            and len(stripped) >= 3
            and len(set(stripped)) == 1
            and stripped[0] in "=-~^\"'`*+#"
            and lines[index - 1].strip()
            and len(stripped) >= len(lines[index - 1].strip()) - 2
        )
        if is_md_heading or is_rst_heading:
            if current_body or current_title:
                sections.append((current_title, current_line, current_body))
            current_title = stripped.lstrip("#").strip() if is_md_heading else lines[index - 1].strip()
            current_line = index + 1
            current_body = []
            if len(sections) >= max_sections:
                break
            continue
        current_body.append(line)

    if current_body or current_title:
        sections.append((current_title, current_line, current_body))
    return [(title, line, "\n".join(body)) for title, line, body in sections if body or title]
