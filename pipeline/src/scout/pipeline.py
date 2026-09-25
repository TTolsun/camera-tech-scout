"""Pipeline orchestration.

    config -> source resolver -> GitHub collector -> repository scanner
           -> evidence builder -> technology extractor -> idea scout
           -> critic -> candidate store -> JSON

Three execution modes share this one code path, which is the point: a dry run
exercises exactly what a real run does.

``live``      resolve from the GitHub API, clone or fetch, analyse, persist, emit.
``dry-run``   the same, but against a throwaway copy of the database and a
              throwaway output directory, with the LLM layer off by default.
``fixture``   the same, but against bundled git repositories, with no network
              access at all, so CI is deterministic and rate limits cannot
              break the build.
"""

from __future__ import annotations

import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import fixtures
from .collect import Commit, GitError, GitRepo
from .config import ScoutConfig, TargetDefaults, load_config
from .critic import Critic
from .emit import EmitInput, emit_all
from .engine import LLMClient, LLMSettings
from .evidence import EvidenceBuilder, enrich_first_seen
from .github import GitHubClient
from .models import Candidate, Evidence, RepoState
from .resolver import ResolveReport, ResolvedRepo, resolve_sources
from .scouting import Scout, link_related
from .store import Store
from .technology import build_graph
from .util import log, step, warn_if_path_too_long


@dataclass
class RunOptions:
    config_path: Path
    data_dir: Path
    cache_dir: Path
    db_path: Path
    dry_run: bool = False
    fixture: bool = False
    use_llm: bool | None = None       # None follows the configuration
    max_repos: int | None = None
    full: bool = False                # ignore stored state and rescan everything
    rebuild_fixtures: bool = False


@dataclass
class RunResult:
    run_id: str
    mode: str
    dry_run: bool
    data_dir: Path
    repositories: list[RepoState] = field(default_factory=list)
    evidence_count: int = 0
    candidate_count: int = 0
    rejected_count: int = 0
    candidates: list[Candidate] = field(default_factory=list)
    written_files: list[Path] = field(default_factory=list)
    engine_report: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.candidate_count > 0 or self.evidence_count > 0


def run(options: RunOptions) -> RunResult:
    started_at = datetime.now(timezone.utc)
    run_id = started_at.strftime("run_%Y%m%dT%H%M%SZ")
    mode = "fixture" if options.fixture else ("dry-run" if options.dry_run else "live")

    config = load_config(options.config_path)
    options.cache_dir.mkdir(parents=True, exist_ok=True)
    warn_if_path_too_long(options.cache_dir, "캐시")
    data_dir, db_path, cleanup = _prepare_paths(options)

    result = RunResult(run_id=run_id, mode=mode, dry_run=options.dry_run, data_dir=data_dir)
    store = Store(db_path)
    store.start_run(run_id, mode)

    try:
        with step(f"source resolution ({mode})"):
            if options.fixture:
                resolve_report = _resolve_fixtures(config, options)
                client = None
            else:
                client = GitHubClient()
                if not client.authenticated:
                    result.warnings.append(
                        "GitHub 토큰을 찾지 못했습니다. 비인증 요청은 시간당 60건으로 제한되므로 "
                        "분석 범위가 축소될 수 있습니다."
                    )
                    log.warning("  no GitHub token found, the rate limit will be 60/hour")
                resolve_report = resolve_sources(config, client)

            repos = resolve_report.repositories
            if options.max_repos is not None:
                repos = repos[: options.max_repos]
                resolve_report.repositories = repos
            log.info("  %d repositories in scope", len(repos))

        repo_states: list[RepoState] = []
        with step("repository scan and evidence building"):
            for repo in repos:
                state = _scan_repository(repo, config, options, store, client, run_id)
                repo_states.append(state)

        all_evidence = store.load_evidence_for_repos([r.full_name for r in repos])
        log.info("  %d evidence records available for analysis", len(all_evidence))

        with step("idea scout"):
            scout = Scout(config.thresholds)
            candidates, dropped = scout.run(all_evidence)
            link_related(candidates)

        with step("evidence dating"):
            evidence_by_id = {e.id: e for e in all_evidence}
            for repo in repos:
                git = _git_for(repo, options)
                if git is None:
                    continue
                attached = [
                    evidence_by_id[eid]
                    for candidate in candidates for eid in candidate.evidence_ids
                    if eid in evidence_by_id
                    and evidence_by_id[eid].repo_full_name == repo.full_name
                ]
                if attached:
                    enrich_first_seen(attached, git)
            store.upsert_evidence(all_evidence, run_id)

        with step("critic"):
            critic = Critic()
            accepted, rejections = critic.run(candidates, evidence_by_id)

        engine_report = _run_llm_layer(config, options, accepted, evidence_by_id)
        result.engine_report = engine_report

        with step("candidate store"):
            for candidate in accepted:
                store.assign_status(candidate, run_id)
                store.upsert_candidate(candidate, run_id)
            for rejection in rejections:
                store.upsert_rejection(rejection, run_id)
            # Rejected candidates are stored too, so their history is kept.
            for candidate in candidates:
                if candidate.verdict == "rejected":
                    store.upsert_candidate(candidate, run_id)

        with step("technology graph"):
            graph = build_graph(all_evidence, accepted)

        with step("JSON emission"):
            emit_input = EmitInput(
                run_id=run_id,
                mode=mode,
                started_at=started_at.isoformat(),
                config_path=options.config_path,
                resolve_report=resolve_report,
                repo_states=repo_states,
                evidence=all_evidence,
                candidates=accepted,
                rejections=store.all_rejections(),
                graph=graph,
                runs=store.runs(),
                dropped_clusters=dropped,
                api_problems=_api_problems(client),
                engine_report=engine_report,
                dry_run=options.dry_run,
            )
            result.written_files = emit_all(data_dir, emit_input)

        store.finish_run(
            run_id,
            repositories=len(repo_states),
            evidence=len(all_evidence),
            candidates=len(accepted),
            rejected=len(rejections),
            notes={"mode": mode, "warnings": result.warnings, "engine": engine_report},
        )

        result.repositories = repo_states
        result.evidence_count = len(all_evidence)
        result.candidate_count = len(accepted)
        result.rejected_count = len(rejections)
        result.candidates = accepted
        return result
    finally:
        store.close()
        cleanup()


# ----------------------------------------------------------------------
def _prepare_paths(options: RunOptions):
    """Return (data_dir, db_path, cleanup).

    A dry run must not touch the committed data directory or the real database,
    but it must still see the previous state so that status transitions such as
    ``STRENGTHENED`` are computed realistically. The database is therefore copied
    into a temporary file and thrown away afterwards.
    """
    if not options.dry_run:
        options.data_dir.mkdir(parents=True, exist_ok=True)
        return options.data_dir, options.db_path, lambda: None

    temp_root = Path(tempfile.mkdtemp(prefix="scout-dryrun-"))
    temp_db = temp_root / "candidates.sqlite3"
    if options.db_path.exists():
        shutil.copy2(options.db_path, temp_db)
        log.info("  dry run: copied the existing database, nothing will be persisted")
    data_dir = options.data_dir.parent / (options.data_dir.name + "-dryrun")
    if data_dir.exists():
        shutil.rmtree(data_dir, ignore_errors=True)
    data_dir.mkdir(parents=True, exist_ok=True)

    def cleanup() -> None:
        shutil.rmtree(temp_root, ignore_errors=True)

    return data_dir, temp_db, cleanup


def _resolve_fixtures(config: ScoutConfig, options: RunOptions) -> ResolveReport:
    root = options.cache_dir / "fixtures"
    paths = fixtures.materialise(root, force=options.rebuild_fixtures)
    repos: list[ResolvedRepo] = []
    for full_name, path in sorted(paths.items()):
        org, _, name = full_name.partition("/")
        repos.append(ResolvedRepo(
            full_name=full_name, org=org, name=name,
            url=f"https://github.com/{full_name}",
            settings=_fixture_settings(config.defaults),
            origins=["fixture"],
            notes="오프라인 fixture입니다. 실제 Repository가 아닙니다.",
            metadata=fixtures.metadata(full_name),
        ))
    return ResolveReport(
        repositories=repos,
        organizations=[{
            "org": "example-camera",
            "url": "https://github.com/example-camera",
            "notes": "오프라인 fixture 조직입니다.",
            "matchPatterns": [],
            "maxRepositories": len(repos),
            "repositoriesListed": len(repos),
            "repositoriesSelected": [r.full_name for r in repos],
            "listingAvailable": True,
        }],
        skipped=[],
    )


def _fixture_settings(defaults: TargetDefaults) -> TargetDefaults:
    """Fixtures are tiny, so the include filter is relaxed to cover every file."""
    relaxed = TargetDefaults(
        branches=defaults.branches,
        include=["src/**", "docs/**", "test/**", "*.md", "CMakeLists.txt"],
        exclude=defaults.exclude,
        analysis=defaults.analysis,
        limits=defaults.limits,
    )
    return relaxed


def _git_for(repo: ResolvedRepo, options: RunOptions) -> GitRepo | None:
    if options.fixture:
        path = options.cache_dir / "fixtures" / repo.org / repo.name
    else:
        path = options.cache_dir / "repos" / repo.org / repo.name
    if not (path / ".git").exists():
        return None
    return GitRepo(full_name=repo.full_name, clone_url=repo.clone_url, path=path)


def _scan_repository(repo: ResolvedRepo, config: ScoutConfig, options: RunOptions,
                     store: Store, client: GitHubClient | None, run_id: str) -> RepoState:
    settings = repo.settings if not options.fixture else _fixture_settings(config.defaults)
    previous = store.get_repo_state(repo.full_name)
    metadata = repo.metadata or {}

    state = RepoState(
        full_name=repo.full_name, org=repo.org, name=repo.name, url=repo.url,
        default_branch=metadata.get("default_branch") or "HEAD",
        last_scanned_sha=previous.last_scanned_sha if previous else None,
        description=metadata.get("description") or "",
        stars=int(metadata.get("stargazers_count") or 0),
        language=metadata.get("language") or "",
        pushed_at=metadata.get("pushed_at"),
    )

    if options.fixture:
        path = options.cache_dir / "fixtures" / repo.org / repo.name
        git = GitRepo(full_name=repo.full_name, clone_url=repo.clone_url, path=path)
    else:
        path = options.cache_dir / "repos" / repo.org / repo.name
        git = GitRepo(full_name=repo.full_name, clone_url=repo.clone_url, path=path)
        try:
            action = git.ensure(depth=settings.limits.clone_depth)
            log.info("  %s: %s", repo.full_name, action)
        except GitError as exc:
            state.status = "error"
            state.status_detail = str(exc)
            store.save_repo_state(state)
            log.warning("  %s: %s", repo.full_name, exc)
            return state

    state.head_sha = git.head_sha()
    state.default_branch = git.current_branch()

    changed_files: list[str] | None = None
    if not options.full and state.last_scanned_sha and state.last_scanned_sha != state.head_sha:
        changed_files = git.changed_files(state.last_scanned_sha)
        if changed_files is None:
            state.status_detail = (
                "이전 Commit이 얕은 clone 범위를 벗어나 있어서 전체 재분석으로 전환했습니다."
            )
        else:
            state.scan_mode = "incremental"
            # Evidence for changed files is rebuilt, so the stale rows go first.
            store.delete_evidence_for_paths(repo.full_name, changed_files)
    elif not options.full and state.last_scanned_sha == state.head_sha and state.head_sha:
        state.scan_mode = "incremental"
        changed_files = []
        state.status_detail = "HEAD가 이전 스캔과 동일하여 새로 수집한 파일이 없습니다."

    commits = _collect_commits(git, settings, state, options)
    payloads = _collect_github(repo, settings, client, options)

    builder = EvidenceBuilder(repo, git, settings, ref=state.head_sha or "HEAD")
    repo_evidence = builder.build(
        changed_files=changed_files,
        commits=commits,
        pulls=payloads["pulls"],
        issues=payloads["issues"],
        releases=payloads["releases"],
    )
    store.upsert_evidence(repo_evidence.items, run_id)

    stored = store.load_evidence_for_repos([repo.full_name])
    state.files_scanned = repo_evidence.files_scanned
    state.evidence_count = len(stored)
    state.last_scanned_sha = state.head_sha
    state.last_scan_time = datetime.now(timezone.utc).isoformat()
    state.status = "ok" if not repo_evidence.notes else "partial"
    if repo_evidence.notes:
        state.status_detail = " / ".join(
            [state.status_detail] + repo_evidence.notes
        ).strip(" /")
    state.topics = sorted({
        signal.family
        for item in stored for signal in item.signals if signal.kind == "domain"
    })[:8]
    store.save_repo_state(state)
    log.info("  %s: %d files, %d evidence records (%s)",
             repo.full_name, state.files_scanned, state.evidence_count, state.scan_mode)
    return state


def _collect_commits(git: GitRepo, settings: TargetDefaults, state: RepoState,
                     options: RunOptions) -> list[Commit]:
    if not settings.analysis.commits:
        return []
    since = None if options.full else state.last_scanned_sha
    # On an incremental run the previous sha has already been analysed, so only
    # what came after it is read.
    return git.commits(max_count=settings.limits.max_commits, since_sha=since)


def _collect_github(repo: ResolvedRepo, settings: TargetDefaults,
                    client: GitHubClient | None,
                    options: RunOptions) -> dict[str, list[dict[str, Any]]]:
    if options.fixture:
        return fixtures.github_payloads(repo.full_name)
    if client is None:
        return {"pulls": [], "issues": [], "releases": []}
    return {
        "pulls": (client.list_pull_requests(repo.full_name, settings.limits.max_pull_requests)
                  if settings.analysis.pull_requests else []),
        "issues": (client.list_issues(repo.full_name, settings.limits.max_issues)
                   if settings.analysis.issues else []),
        "releases": (client.list_releases(repo.full_name, settings.limits.max_releases)
                     if settings.analysis.releases else []),
    }


def _run_llm_layer(config: ScoutConfig, options: RunOptions, candidates: list[Candidate],
                   evidence_by_id: dict[str, Evidence]) -> dict[str, Any]:
    settings = LLMSettings.from_dict(config.engine.llm)
    wanted = config.engine.uses_llm if options.use_llm is None else options.use_llm

    if not wanted:
        reason = ("dry-run에서는 기본적으로 모델을 호출하지 않습니다."
                  if options.dry_run and options.use_llm is None
                  else "설정에서 LLM 계층이 비활성화되어 있습니다.")
        return {
            **LLMSettings(enabled=False).__dict__,
            "enabled": False,
            "skipped": True,
            "skipReason": reason,
            "rulesEngine": "always active",
        }

    settings.enabled = True
    client = LLMClient(settings)
    with step(f"LLM layer ({settings.runner}, model {settings.model})"):
        if not client.probe():
            report = client.report.to_dict()
            report.update(_engine_roles(config))
            report["skipped"] = True
            report["skipReason"] = "엔드포인트에 접속할 수 없어 규칙 기반 결과를 그대로 유지했습니다."
            return report

        targets = candidates[: settings.max_candidates]
        for candidate in targets:
            items = [evidence_by_id[e] for e in candidate.evidence_ids if e in evidence_by_id]
            if config.engine.scout == "llm":
                client.enrich_candidate(candidate, items)
            if config.engine.critic == "llm":
                candidate.critic_findings.extend(
                    client.challenge_candidate(candidate, items)
                )
        report = client.report.to_dict()
        report.update(_engine_roles(config))
        report["candidatesConsidered"] = len(targets)
        report["skipped"] = False
        return report


def _engine_roles(config: ScoutConfig) -> dict[str, str]:
    """Which engine each role actually ran, so the history page does not name a
    critic model when the critic is the rules engine (#3)."""
    return {"scoutEngine": config.engine.scout, "criticEngine": config.engine.critic}


def _api_problems(client: GitHubClient | None) -> list[dict[str, Any]]:
    if client is None:
        return []
    return [
        {"endpoint": p.endpoint, "status": p.status, "detail": p.detail}
        for p in client.problems[:40]
    ]
