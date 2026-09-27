"""Loading and validation of ``config/sources.yaml``.

Adding or removing an analysis target must never require a code change, so all
target selection, filtering and budget knobs live in the YAML file. Per-source
values fall back to the ``defaults`` block.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yaml

SOURCE_PART_RE = re.compile(r"^[A-Za-z0-9._-]+$")


class ConfigError(ValueError):
    """Raised when the configuration file cannot be used as written."""


def _known_fields(cls: type, data: dict[str, Any], section: str) -> dict[str, Any]:
    """Return ``data`` restricted to the dataclass fields, rejecting the rest.

    A key that is silently dropped looks like a setting that works. Setting
    `discussions: true` used to do nothing, with no hint why (#5), so an
    unknown key is an error that names the keys that do exist.
    """
    fields = cls.__dataclass_fields__
    unknown = sorted(set(data) - set(fields))
    if unknown:
        raise ConfigError(
            f"unknown key(s) under `{section}`: {', '.join(unknown)}; "
            f"expected one of: {', '.join(fields)}"
        )
    return dict(data)


@dataclass
class GitHubConfig:
    web_url: str = "https://github.com"
    api_url: str = "https://api.github.com"
    token_env: str = "GITHUB_TOKEN"
    repository_type: str = "all"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "GitHubConfig":
        web = str(data.get("web_url", "https://github.com")).rstrip("/")
        api = str(data.get("api_url") or (
            "https://api.github.com" if web == "https://github.com" else web + "/api/v3"
        )).rstrip("/")
        for label, value in (("web_url", web), ("api_url", api)):
            parts = urlsplit(value)
            if (parts.scheme != "https" or not parts.hostname or parts.username
                    or parts.password or parts.query or parts.fragment
                    or (label == "web_url" and parts.path)):
                raise ConfigError(f"github.{label} must be an HTTPS URL without credentials")
        kind = str(data.get("repository_type", "all"))
        if kind not in {"all", "public", "private"}:
            raise ConfigError("github.repository_type must be all, public, or private")
        token_env = str(data.get("token_env", "GITHUB_TOKEN"))
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", token_env):
            raise ConfigError("github.token_env must name an environment variable")
        return cls(web, api, token_env, kind)


def _branches(value: Any) -> list[str]:
    # The store holds one analysis branch per repository, not multiple branches.
    if (not isinstance(value, list) or len(value) != 1
            or not isinstance(value[0], str) or not value[0]
            or value[0].startswith(("-", "/")) or value[0].endswith(("/", "."))
            or value[0] == "@"
            or any(c.isspace() or ord(c) < 32 or ord(c) == 127 or c in "~^:?*[\\" for c in value[0])
            or any(s in value[0] for s in ("..", "@{", "//"))
            or any(p.startswith(".") or p.endswith(".lock") for p in value[0].split("/"))):
        raise ConfigError("branches must contain exactly one branch name, e.g. [main]")
    return value.copy()


@dataclass
class Limits:
    clone_depth: int = 400
    max_files: int = 4000
    max_file_bytes: int = 400_000
    max_commits: int = 400
    max_pull_requests: int = 120
    max_issues: int = 120
    max_releases: int = 30
    # Reading a diff costs one `git show` per commit, so it is budgeted
    # separately from the commit list itself.
    max_diff_commits: int = 150
    max_diff_lines: int = 80

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Limits":
        return cls(**_known_fields(cls, data, "limits"))


@dataclass
class AnalysisToggles:
    code: bool = True
    documents: bool = True
    commits: bool = True
    pull_requests: bool = True
    issues: bool = True
    releases: bool = True

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AnalysisToggles":
        known = _known_fields(cls, data, "analysis")
        return cls(**{key: bool(value) for key, value in known.items()})


@dataclass
class TargetDefaults:
    branches: list[str] = field(default_factory=lambda: ["main"])
    include: list[str] = field(default_factory=list)
    exclude: list[str] = field(default_factory=list)
    analysis: AnalysisToggles = field(default_factory=AnalysisToggles)
    limits: Limits = field(default_factory=Limits)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TargetDefaults":
        return cls(
            branches=_branches(data.get("branches", ["main"])),
            include=list(data.get("include") or []),
            exclude=list(data.get("exclude") or []),
            analysis=AnalysisToggles.from_dict(data.get("analysis") or {}),
            limits=Limits.from_dict(data.get("limits") or {}),
        )

    def merged_with(self, override: dict[str, Any]) -> "TargetDefaults":
        """Return a copy where keys present in ``override`` win."""
        merged = TargetDefaults(
            branches=_branches(override.get("branches", self.branches)),
            include=list(override.get("include", self.include)),
            exclude=list(override.get("exclude", self.exclude)),
            analysis=AnalysisToggles.from_dict(
                {**self.analysis.__dict__, **(override.get("analysis") or {})}
            ),
            limits=Limits.from_dict({**self.limits.__dict__, **(override.get("limits") or {})}),
        )
        return merged


@dataclass
class OrganizationSource:
    url: str
    org: str
    enabled: bool = True
    match: list[str] = field(default_factory=list)
    max_repositories: int = 20
    notes: str = ""
    overrides: TargetDefaults | None = None


@dataclass
class RepositorySource:
    url: str
    org: str
    name: str
    enabled: bool = True
    notes: str = ""
    overrides: TargetDefaults | None = None

    @property
    def full_name(self) -> str:
        return self.org + "/" + self.name


@dataclass
class EngineConfig:
    """Which engine drives each role.

    ``rules`` is the deterministic, evidence-anchored analysis and is always
    executed. ``llm`` additionally runs the model layer on top of it: the scout
    model refines grounded prose and the critic model adds objections. The two
    roles intentionally use different models so that the critic does not simply
    agree with the scout.
    """

    scout: str = "rules"
    critic: str = "rules"
    llm: dict[str, Any] = field(default_factory=dict)

    @property
    def llm_enabled(self) -> bool:
        return bool(self.llm.get("enabled", False))

    @property
    def uses_llm(self) -> bool:
        return self.llm_enabled and ("llm" in (self.scout, self.critic))


@dataclass
class Thresholds:
    min_evidence_score: float = 3.0
    min_signal_kinds: int = 2


@dataclass
class ScoutConfig:
    path: Path
    organizations: list[OrganizationSource]
    repositories: list[RepositorySource]
    defaults: TargetDefaults
    engine: EngineConfig
    thresholds: Thresholds
    github: GitHubConfig = field(default_factory=GitHubConfig)

    def enabled_organizations(self) -> list[OrganizationSource]:
        return [o for o in self.organizations if o.enabled]

    def enabled_repositories(self) -> list[RepositorySource]:
        return [r for r in self.repositories if r.enabled]


def _source_parts(url: str, web_url: str, count: int) -> list[str]:
    parsed, base = urlsplit(str(url).strip()), urlsplit(web_url)
    parts = parsed.path.strip("/").split("/")
    if (parsed.scheme != base.scheme or parsed.netloc.lower() != base.netloc.lower()
            or parsed.query or parsed.fragment or len(parts) != count
            or any(not SOURCE_PART_RE.fullmatch(p) or p in {".", ".."} for p in parts)):
        raise ConfigError("source URL must belong to github.web_url and name an organization/repository")
    return parts


def _parse_org_url(url: str, web_url: str = "https://github.com") -> str:
    return _source_parts(url, web_url, 1)[0]


def _parse_repo_url(url: str, web_url: str = "https://github.com") -> tuple[str, str]:
    org, name = _source_parts(url, web_url, 2)
    name = name.removesuffix(".git")
    if not name or name in {".", ".."}:
        raise ConfigError("repository name must not be empty or a relative path")
    return org, name


def load_config(path: str | Path) -> ScoutConfig:
    path = Path(path)
    if not path.exists():
        raise ConfigError("configuration file not found: " + str(path))

    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ConfigError("configuration root must be a mapping")

    github = GitHubConfig.from_dict(raw.get("github") or {})
    defaults = TargetDefaults.from_dict(raw.get("defaults") or {})
    sources = raw.get("sources") or {}
    if not isinstance(sources, dict):
        raise ConfigError("`sources` must be a mapping with `organizations` and `repositories`")

    organizations: list[OrganizationSource] = []
    for entry in sources.get("organizations") or []:
        entry = _as_entry(entry)
        org = _parse_org_url(entry["url"], github.web_url)
        organizations.append(
            OrganizationSource(
                url=f"{github.web_url}/{org}",
                org=org,
                enabled=bool(entry.get("enabled", True)),
                match=[str(m) for m in (entry.get("match") or [])],
                max_repositories=int(entry.get("max_repositories", 20)),
                notes=str(entry.get("notes", "")),
                overrides=defaults.merged_with(entry) if _has_overrides(entry) else None,
            )
        )

    repositories: list[RepositorySource] = []
    for entry in sources.get("repositories") or []:
        entry = _as_entry(entry)
        org, name = _parse_repo_url(entry["url"], github.web_url)
        repositories.append(
            RepositorySource(
                url=f"{github.web_url}/{org}/{name}",
                org=org,
                name=name,
                enabled=bool(entry.get("enabled", True)),
                notes=str(entry.get("notes", "")),
                overrides=defaults.merged_with(entry) if _has_overrides(entry) else None,
            )
        )

    if not organizations and not repositories:
        raise ConfigError("no analysis target is configured under `sources`")

    engine_raw = raw.get("engine") or {}
    engine = EngineConfig(
        scout=str(engine_raw.get("scout", "rules")),
        critic=str(engine_raw.get("critic", "rules")),
        llm=dict(engine_raw.get("llm") or {}),
    )
    if engine.scout not in {"rules", "llm"} or engine.critic not in {"rules", "llm"}:
        raise ConfigError("engine.scout and engine.critic must be either `rules` or `llm`")

    thresholds_raw = raw.get("thresholds") or {}
    thresholds = Thresholds(
        min_evidence_score=float(thresholds_raw.get("min_evidence_score", 3.0)),
        min_signal_kinds=int(thresholds_raw.get("min_signal_kinds", 2)),
    )

    return ScoutConfig(
        path=path,
        organizations=organizations,
        repositories=repositories,
        defaults=defaults,
        engine=engine,
        thresholds=thresholds,
        github=github,
    )


def _as_entry(entry: Any) -> dict[str, Any]:
    """Accept both a bare url string and a mapping with a `url` key."""
    if isinstance(entry, str):
        return {"url": entry}
    if isinstance(entry, dict) and entry.get("url"):
        return entry
    raise ConfigError("each source entry needs a `url`, got: " + repr(entry))


_OVERRIDE_KEYS = ("branches", "include", "exclude", "analysis", "limits")


def _has_overrides(entry: dict[str, Any]) -> bool:
    return any(key in entry for key in _OVERRIDE_KEYS)
