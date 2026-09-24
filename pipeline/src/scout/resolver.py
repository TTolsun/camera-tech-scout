"""Source resolution.

Turns the configuration into one de-duplicated list of repositories to analyse.
Organization listings and directly named repositories are merged; a repository
reached through both routes is kept once, and it remembers every route that
produced it so the Sources page can explain why it is in scope.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from typing import Any

from .config import OrganizationSource, RepositorySource, ScoutConfig, TargetDefaults
from .github import GitHubClient
from .util import log


@dataclass
class ResolvedRepo:
    """One repository in scope, with its effective settings already merged."""

    full_name: str
    org: str
    name: str
    url: str
    settings: TargetDefaults
    origins: list[str] = field(default_factory=list)
    notes: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def clone_url(self) -> str:
        return f"https://github.com/{self.full_name}.git"

    def to_dict(self) -> dict[str, Any]:
        return {
            "fullName": self.full_name,
            "org": self.org,
            "name": self.name,
            "url": self.url,
            "origins": self.origins,
            "notes": self.notes,
            "description": self.metadata.get("description") or "",
            "stars": self.metadata.get("stargazers_count") or 0,
            "language": self.metadata.get("language") or "",
            "defaultBranch": self.metadata.get("default_branch") or "HEAD",
            "pushedAt": self.metadata.get("pushed_at"),
            "archived": bool(self.metadata.get("archived")),
        }


@dataclass
class ResolveReport:
    repositories: list[ResolvedRepo]
    organizations: list[dict[str, Any]]
    skipped: list[dict[str, str]]


def resolve_sources(config: ScoutConfig, client: GitHubClient) -> ResolveReport:
    resolved: dict[str, ResolvedRepo] = {}
    org_reports: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []

    for org_source in config.enabled_organizations():
        report, repos = _expand_organization(org_source, config, client)
        org_reports.append(report)
        for repo in repos:
            _merge(resolved, repo)

    for repo_source in config.enabled_repositories():
        repo = _repo_from_source(repo_source, config, client)
        if repo is None:
            skipped.append({
                "target": repo_source.full_name,
                "reason": "repository metadata could not be read from the GitHub API",
            })
            continue
        _merge(resolved, repo)

    for repo_source in config.repositories:
        if not repo_source.enabled:
            skipped.append({"target": repo_source.full_name, "reason": "disabled in configuration"})
    for org_source in config.organizations:
        if not org_source.enabled:
            skipped.append({"target": org_source.org, "reason": "disabled in configuration"})

    ordered = sorted(resolved.values(), key=lambda r: r.full_name)
    log.info("  resolved %d repositories from %d organizations and %d direct entries",
             len(ordered), len(config.enabled_organizations()),
             len(config.enabled_repositories()))
    return ResolveReport(repositories=ordered, organizations=org_reports, skipped=skipped)


def _merge(resolved: dict[str, ResolvedRepo], repo: ResolvedRepo) -> None:
    existing = resolved.get(repo.full_name)
    if existing is None:
        resolved[repo.full_name] = repo
        return
    # Same repository reached twice. Keep one entry, remember both routes, and
    # let the more specific route (a direct entry) supply the settings.
    for origin in repo.origins:
        if origin not in existing.origins:
            existing.origins.append(origin)
    if repo.notes and not existing.notes:
        existing.notes = repo.notes
    if any(o.startswith("repository:") for o in repo.origins):
        existing.settings = repo.settings
    if repo.metadata and not existing.metadata:
        existing.metadata = repo.metadata


def _expand_organization(
    source: OrganizationSource, config: ScoutConfig, client: GitHubClient
) -> tuple[dict[str, Any], list[ResolvedRepo]]:
    listing = client.list_org_repos(source.org, limit=max(source.max_repositories * 4, 40))
    settings = source.overrides or config.defaults

    considered = len(listing)
    repos: list[ResolvedRepo] = []
    for item in listing:
        name = item.get("name")
        if not name:
            continue
        if source.match and not _matches(name, source.match):
            continue
        if item.get("archived") and not source.match:
            continue
        repos.append(
            ResolvedRepo(
                full_name=f"{source.org}/{name}",
                org=source.org,
                name=name,
                url=item.get("html_url") or f"https://github.com/{source.org}/{name}",
                settings=settings,
                origins=[f"organization:{source.org}"],
                notes=source.notes,
                metadata=item,
            )
        )
        if len(repos) >= source.max_repositories:
            break

    report = {
        "org": source.org,
        "url": source.url,
        "notes": source.notes,
        "matchPatterns": source.match,
        "maxRepositories": source.max_repositories,
        "repositoriesListed": considered,
        "repositoriesSelected": [r.full_name for r in repos],
        "listingAvailable": considered > 0,
    }
    if considered == 0:
        log.warning("organization %s returned no repositories from the API", source.org)
    return report, repos


def _repo_from_source(
    source: RepositorySource, config: ScoutConfig, client: GitHubClient
) -> ResolvedRepo | None:
    metadata = client.get_repo(source.full_name) or {}
    if not metadata:
        # Without metadata the repository may still be clonable, so it is kept
        # but flagged. Only a hard 404 removes it from scope.
        problems = [p for p in client.problems if source.full_name in p.endpoint]
        if any(p.status == 404 for p in problems):
            return None
    return ResolvedRepo(
        full_name=source.full_name,
        org=source.org,
        name=source.name,
        url=source.url,
        settings=source.overrides or config.defaults,
        origins=[f"repository:{source.full_name}"],
        notes=source.notes,
        metadata=metadata,
    )


def _matches(name: str, patterns: list[str]) -> bool:
    lowered = name.lower()
    for pattern in patterns:
        pattern = pattern.lower()
        if lowered == pattern or fnmatch.fnmatch(lowered, pattern):
            return True
        if pattern in lowered:
            return True
    return False
