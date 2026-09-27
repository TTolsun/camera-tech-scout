"""Enterprise configuration and collection, with no external network access."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import yaml

from scout import cli, github
from scout.config import ConfigError, GitHubConfig, TargetDefaults, load_config
from scout.evidence import EvidenceBuilder
from scout.models import RepoState
from scout.pipeline import CollectionError, RunOptions, _run_llm_layer, _scan_repository, run
from scout.resolver import ResolveReport, ResolvedRepo, resolve_sources
from scout.store import Store

from conftest import requires_git


def write_config(tmp_path, **updates):
    raw = {
        "github": {"web_url": "https://github.example.invalid", "token_env": "SCOUT_GITHUB_TOKEN"},
        "sources": {"repositories": ["https://github.example.invalid/camera/hal.git"]},
    }
    raw.update(updates)
    path = tmp_path / "sources.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    return load_config(path)


def test_enterprise_urls_defaults_and_resolution(tmp_path):
    config = write_config(tmp_path)
    assert config.github.api_url == "https://github.example.invalid/api/v3"
    assert config.defaults.branches == ["main"]
    client = Mock()
    client.get_repo.return_value = {"default_branch": "other"}
    repo = resolve_sources(config, client).repositories[0]
    assert repo.full_name == "camera/hal"
    assert repo.clone_url == "https://github.example.invalid/camera/hal.git"
    assert repo.web_url == config.github.web_url
    assert repo.to_dict()["analysisBranch"] == "main"
    assert repo.to_dict()["defaultBranch"] == "other"


def test_public_config_is_compatible(config_path):
    config = load_config(config_path)
    assert config.github.web_url == "https://github.com"
    assert config.github.api_url == "https://api.github.com"
    assert config.defaults.branches == ["HEAD"]


def test_enterprise_example_is_valid():
    config = load_config(Path(__file__).resolve().parents[2] / "config/sources.enterprise.example.yaml")
    assert config.defaults.branches == ["main"]
    assert not config.defaults.analysis.pull_requests
    assert not config.engine.uses_llm
    assert "vendor/**" in config.defaults.include


def test_per_repository_branch_override_and_empty_exclusions(tmp_path):
    config = write_config(
        tmp_path,
        defaults={"branches": ["main"], "exclude": ["vendor/**"]},
        sources={"repositories": [{
            "url": "https://github.example.invalid/camera/hal",
            "branches": ["development/camera"], "exclude": [],
        }]},
    )
    assert config.repositories[0].overrides.branches == ["development/camera"]
    assert config.repositories[0].overrides.exclude == []


@pytest.mark.parametrize("value", [[], "main", ["main", "other"], ["--all"], ["bad..name"], ["a b"], ["@"]])
def test_invalid_branches_are_rejected(value):
    with pytest.raises(ConfigError, match="branches"):
        TargetDefaults.from_dict({"branches": value})


@pytest.mark.parametrize("url", [
    "https://github.com/camera/hal", "https://token@github.example.invalid/camera/hal",
    "https://github.example.invalid/../hal", "https://github.example.invalid/camera/hal?token=x",
])
def test_sources_must_use_configured_host_without_credentials(tmp_path, url):
    with pytest.raises(ConfigError):
        write_config(tmp_path, sources={"repositories": [url]})


@pytest.mark.parametrize("url", ["http://github.example.invalid", "https://token@github.example.invalid"])
def test_host_requires_https_without_credentials(url):
    with pytest.raises(ConfigError):
        GitHubConfig.from_dict({"web_url": url})


@pytest.mark.parametrize("settings", ["github.example.invalid", {"web_urll": "https://github.example.invalid"}])
def test_invalid_github_section_is_not_silently_ignored(settings):
    with pytest.raises(ConfigError):
        GitHubConfig.from_dict(settings)


def test_enterprise_api_and_private_organization_listing():
    session = Mock()
    response = Mock(status_code=200, headers={})
    response.json.return_value = [{"name": "private-hal", "private": True}]
    session.get.return_value = response
    client = github.GitHubClient(
        token="test-only", session=session, web_url="https://github.example.invalid",
        api_url="https://github.example.invalid/api/v3",
    )
    assert client.list_org_repos("camera")[0]["private"]
    assert session.get.call_args.args[0] == "https://github.example.invalid/api/v3/orgs/camera/repos"
    assert session.get.call_args.kwargs["params"]["type"] == "all"


def test_token_lookup_is_host_specific(monkeypatch):
    monkeypatch.delenv("SCOUT_GITHUB_TOKEN", raising=False)
    monkeypatch.setenv("GH_TOKEN", "public-host-token")
    command = Mock(return_value=SimpleNamespace(returncode=0, stdout="enterprise-token\n"))
    monkeypatch.setattr(github.subprocess, "run", command)
    assert github.discover_token("github.example.invalid", "SCOUT_GITHUB_TOKEN") == "enterprise-token"
    assert command.call_args.args[0] == ["gh", "auth", "token", "--hostname", "github.example.invalid"]
    monkeypatch.setenv("SCOUT_GITHUB_TOKEN", "explicit-token")
    assert github.discover_token("github.example.invalid", "SCOUT_GITHUB_TOKEN") == "explicit-token"


@requires_git
def test_generated_evidence_links_use_enterprise_host(sync_repo):
    repo = ResolvedRepo("camera/hal", "camera", "hal", "https://github.example.invalid/camera/hal", TargetDefaults())
    items = EvidenceBuilder(repo, sync_repo, repo.settings, ref=sync_repo.head_sha()).build(
        changed_files=None, commits=sync_repo.commits(), pulls=[], issues=[], releases=[],
    ).items
    assert {"code", "doc", "commit"} <= {item.kind for item in items}
    assert all(item.url.startswith(repo.url + "/") for item in items)


@pytest.mark.parametrize("builder,tail", [
    (github.pull_url, "pull/1"), (github.issue_url, "issues/1"), (github.release_url, "releases/tag/1"),
])
def test_thread_and_release_links(builder, tail):
    assert builder("camera/hal", 1, web_url="https://github.example.invalid") == f"https://github.example.invalid/camera/hal/{tail}"


@pytest.mark.parametrize("url,branch", [
    ("https://github.com/camera/hal", "main"),
    ("https://github.example.invalid/camera/hal", "other"),
])
def test_different_host_or_branch_requires_separate_state(tmp_path, url, branch):
    config = write_config(tmp_path)
    repo = ResolvedRepo("camera/hal", "camera", "hal", config.repositories[0].url, config.defaults)
    options = RunOptions(config.path, tmp_path / "data", tmp_path / "cache", tmp_path / "state.db")
    store = Store(options.db_path)
    try:
        store.save_repo_state(RepoState(full_name=repo.full_name, org=repo.org, name=repo.name, url=url, default_branch=branch))
        with pytest.raises(ConfigError, match="separate --db"):
            _scan_repository(repo, config, options, store, None, "test")
    finally:
        store.close()


def test_scan_command_returns_failure_for_collection_error(monkeypatch):
    monkeypatch.setattr(cli, "run", lambda options: SimpleNamespace(repositories=[SimpleNamespace(status="error")]))
    monkeypatch.setattr(cli, "_print_summary", lambda result: None)
    assert cli.main(["scan", "--no-llm"]) == 1


def test_default_dry_run_never_constructs_an_llm_client(tmp_path, monkeypatch):
    config = write_config(tmp_path, engine={"scout": "llm", "llm": {"enabled": True}})
    options = RunOptions(config.path, tmp_path / "data", tmp_path / "cache", tmp_path / "state.db", dry_run=True)
    client = Mock(side_effect=AssertionError("dry-run must not contact the model"))
    monkeypatch.setattr("scout.pipeline.LLMClient", client)
    assert _run_llm_layer(config, options, [], {})["skipped"]
    client.assert_not_called()


@pytest.mark.parametrize("failure", ["empty", "missing", "clone"])
def test_failed_collection_preserves_published_results(tmp_path, monkeypatch, failure):
    config = write_config(tmp_path)
    data = tmp_path / "data"
    data.mkdir()
    published = data / "candidates.json"
    published.write_text('{"previous": true}', encoding="utf-8")
    repo = ResolvedRepo("camera/hal", "camera", "hal", config.repositories[0].url, config.defaults)
    report = ResolveReport(
        repositories=[] if failure == "empty" else [repo], organizations=[],
        skipped=[{"target": "camera/missing", "reason": "metadata unavailable"}] if failure == "missing" else [],
    )
    monkeypatch.setattr("scout.pipeline.GitHubClient", Mock(return_value=SimpleNamespace(authenticated=True)))
    monkeypatch.setattr("scout.pipeline.resolve_sources", lambda *args: report)
    monkeypatch.setattr("scout.pipeline._scan_repository", lambda *args: RepoState(
        full_name=repo.full_name, org=repo.org, name=repo.name, url=repo.url, status="error",
    ))
    options = RunOptions(config.path, data, tmp_path / "cache", tmp_path / "state.db")
    with pytest.raises(CollectionError):
        run(options)
    assert published.read_text(encoding="utf-8") == '{"previous": true}'
    assert list(data.iterdir()) == [published]


def test_collection_exception_returns_nonzero_exit(monkeypatch):
    monkeypatch.setattr(cli, "run", Mock(side_effect=CollectionError("no targets")))
    assert cli.main(["scan", "--no-llm"]) == 1
