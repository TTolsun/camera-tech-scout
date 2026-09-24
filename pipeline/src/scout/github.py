"""Thin GitHub REST client.

Only the endpoints the pipeline actually needs are wrapped. The client is
deliberately conservative: it respects the documented rate limit, it degrades to
an empty result instead of raising when a resource is absent or forbidden, and
it records every failure so that the site can display a repository as ``partial``
rather than silently pretending the data was complete.
"""

from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass, field
from typing import Any, Iterator

import requests

from .util import log

API_ROOT = "https://api.github.com"
USER_AGENT = "camera-tech-scout/0.1"


def discover_token() -> str | None:
    """Find a usable token: environment first, then the GitHub CLI."""
    for name in ("GITHUB_TOKEN", "GH_TOKEN"):
        value = os.environ.get(name)
        if value:
            log.debug("using token from %s", name)
            return value.strip()
    try:
        result = subprocess.run(
            ["gh", "auth", "token"],
            capture_output=True, text=True, timeout=20, check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            log.debug("using token from `gh auth token`")
            return result.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return None


@dataclass
class ApiProblem:
    """A request that did not return usable data."""

    endpoint: str
    status: int
    detail: str


@dataclass
class GitHubClient:
    token: str | None = None
    session: requests.Session = field(default_factory=requests.Session)
    problems: list[ApiProblem] = field(default_factory=list)
    requests_made: int = 0
    rate_limit_remaining: int | None = None
    rate_limit_reset: int | None = None

    def __post_init__(self) -> None:
        if self.token is None:
            self.token = discover_token()
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": USER_AGENT,
        }
        if self.token:
            headers["Authorization"] = "Bearer " + self.token
        self.session.headers.update(headers)

    @property
    def authenticated(self) -> bool:
        return bool(self.token)

    # ------------------------------------------------------------------
    # Low level
    # ------------------------------------------------------------------
    def get(self, path: str, params: dict[str, Any] | None = None,
            retries: int = 3) -> Any | None:
        url = path if path.startswith("http") else API_ROOT + path
        for attempt in range(1, retries + 1):
            try:
                response = self.session.get(url, params=params, timeout=45)
            except requests.RequestException as exc:
                if attempt == retries:
                    self.problems.append(ApiProblem(path, 0, str(exc)))
                    return None
                time.sleep(1.5 * attempt)
                continue

            self.requests_made += 1
            self._note_rate_limit(response)

            if response.status_code == 200:
                return response.json()
            if response.status_code in (301, 302, 307) and "Location" in response.headers:
                url = response.headers["Location"]
                continue
            if response.status_code == 404:
                self.problems.append(ApiProblem(path, 404, "not found"))
                return None
            if response.status_code in (403, 429):
                if self._wait_for_rate_limit(response, attempt, retries):
                    continue
                self.problems.append(
                    ApiProblem(path, response.status_code, _short_error(response))
                )
                return None
            if 500 <= response.status_code < 600 and attempt < retries:
                time.sleep(2.0 * attempt)
                continue
            self.problems.append(ApiProblem(path, response.status_code, _short_error(response)))
            return None
        return None

    def paginate(self, path: str, params: dict[str, Any] | None = None,
                 limit: int = 200, per_page: int = 100) -> list[Any]:
        """Collect up to ``limit`` items following ``Link: rel="next"`` headers."""
        collected: list[Any] = []
        page = 1
        while len(collected) < limit:
            batch = self.get(
                path,
                params={**(params or {}), "per_page": min(per_page, limit - len(collected)),
                        "page": page},
            )
            if not batch:
                break
            if not isinstance(batch, list):
                break
            collected.extend(batch)
            if len(batch) < per_page:
                break
            page += 1
            if page > 20:       # hard stop, keeps a misconfigured target cheap
                break
        return collected[:limit]

    def _note_rate_limit(self, response: requests.Response) -> None:
        remaining = response.headers.get("X-RateLimit-Remaining")
        reset = response.headers.get("X-RateLimit-Reset")
        if remaining is not None and remaining.isdigit():
            self.rate_limit_remaining = int(remaining)
        if reset is not None and reset.isdigit():
            self.rate_limit_reset = int(reset)

    def _wait_for_rate_limit(self, response: requests.Response, attempt: int,
                             retries: int) -> bool:
        """Sleep through a secondary or primary rate limit, if that is feasible."""
        retry_after = response.headers.get("Retry-After")
        if retry_after and retry_after.isdigit():
            delay = min(int(retry_after), 60)
        elif self.rate_limit_remaining == 0 and self.rate_limit_reset:
            delay = max(0, self.rate_limit_reset - int(time.time())) + 2
        else:
            return False
        if delay > 120 or attempt >= retries:
            log.warning("rate limited for %ss, giving up on this endpoint", delay)
            return False
        log.warning("rate limited, sleeping %ss", delay)
        time.sleep(delay)
        return True

    # ------------------------------------------------------------------
    # Endpoints
    # ------------------------------------------------------------------
    def get_repo(self, full_name: str) -> dict[str, Any] | None:
        data = self.get("/repos/" + full_name)
        return data if isinstance(data, dict) else None

    def list_org_repos(self, org: str, limit: int = 100) -> list[dict[str, Any]]:
        """List an organization's repositories, falling back to the user route."""
        repos = self.paginate(
            f"/orgs/{org}/repos", params={"type": "public", "sort": "pushed"}, limit=limit
        )
        if repos:
            return [r for r in repos if isinstance(r, dict)]
        repos = self.paginate(
            f"/users/{org}/repos", params={"type": "owner", "sort": "pushed"}, limit=limit
        )
        return [r for r in repos if isinstance(r, dict)]

    def list_pull_requests(self, full_name: str, limit: int = 100) -> list[dict[str, Any]]:
        data = self.paginate(
            f"/repos/{full_name}/pulls",
            params={"state": "all", "sort": "updated", "direction": "desc"},
            limit=limit,
        )
        return [d for d in data if isinstance(d, dict)]

    def list_issues(self, full_name: str, limit: int = 100) -> list[dict[str, Any]]:
        """Issues only. The REST issues endpoint also returns pull requests."""
        data = self.paginate(
            f"/repos/{full_name}/issues",
            params={"state": "all", "sort": "updated", "direction": "desc"},
            limit=limit * 2,
        )
        issues = [d for d in data if isinstance(d, dict) and "pull_request" not in d]
        return issues[:limit]

    def list_releases(self, full_name: str, limit: int = 30) -> list[dict[str, Any]]:
        data = self.paginate(f"/repos/{full_name}/releases", limit=limit)
        return [d for d in data if isinstance(d, dict)]

    def compare(self, full_name: str, base: str, head: str) -> dict[str, Any] | None:
        data = self.get(f"/repos/{full_name}/compare/{base}...{head}")
        return data if isinstance(data, dict) else None

    def rate_limit_summary(self) -> str:
        if self.rate_limit_remaining is None:
            return "rate limit unknown"
        return f"{self.rate_limit_remaining} core requests remaining"

    def iter_problems(self) -> Iterator[ApiProblem]:
        yield from self.problems


def _short_error(response: requests.Response) -> str:
    try:
        payload = response.json()
        if isinstance(payload, dict) and payload.get("message"):
            return str(payload["message"])[:200]
    except ValueError:
        pass
    return (response.text or "")[:200]


# ----------------------------------------------------------------------
# URL builders. Kept here so every deep link in the site has one source.
# ----------------------------------------------------------------------
def blob_url(full_name: str, ref: str, path: str,
             line_start: int | None = None, line_end: int | None = None) -> str:
    url = f"https://github.com/{full_name}/blob/{ref}/{path}"
    if line_start:
        url += f"#L{line_start}"
        if line_end and line_end != line_start:
            url += f"-L{line_end}"
    return url


def commit_url(full_name: str, sha: str) -> str:
    return f"https://github.com/{full_name}/commit/{sha}"


def pull_url(full_name: str, number: int) -> str:
    return f"https://github.com/{full_name}/pull/{number}"


def issue_url(full_name: str, number: int) -> str:
    return f"https://github.com/{full_name}/issues/{number}"


def release_url(full_name: str, tag: str) -> str:
    return f"https://github.com/{full_name}/releases/tag/{tag}"
