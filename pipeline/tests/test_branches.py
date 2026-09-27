"""Exercise clone/fetch against a local remote with a non-main default branch."""

import subprocess

import pytest

from scout.collect import GitError, GitRepo
from scout.config import load_config, TargetDefaults
from scout.pipeline import RunOptions, _scan_repository
from scout.resolver import ResolvedRepo
from scout.store import Store

from conftest import requires_git


def git(path, *args):
    return subprocess.run(["git", *args], cwd=path, capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture
def remote(tmp_path):
    path = tmp_path / "remote"
    path.mkdir()
    git(path, "init", "--initial-branch=other")
    git(path, "config", "user.name", "Test")
    git(path, "config", "user.email", "test@example.invalid")
    git(path, "config", "commit.gpgsign", "false")
    (path / "branch.txt").write_text("other")
    git(path, "add", ".")
    git(path, "commit", "-m", "other branch")
    git(path, "checkout", "-b", "main")
    (path / "branch.txt").write_text("main")
    git(path, "commit", "-am", "main branch")
    git(path, "checkout", "other")
    return path


@requires_git
def test_clone_and_fetch_follow_configured_branch(remote, tmp_path):
    repo = GitRepo("camera/hal", remote.as_uri(), tmp_path / "clone")
    assert repo.ensure(branch="main") == "clone"
    assert (repo.path / "branch.txt").read_text() == "main"
    git(remote, "checkout", "main")
    (remote / "branch.txt").write_text("main updated")
    git(remote, "commit", "-am", "update main")
    git(remote, "checkout", "other")
    assert repo.ensure(branch="main") == "fetch"
    assert repo.head_sha() == git(remote, "rev-parse", "main")
    assert (repo.path / "branch.txt").read_text() == "main updated"
    repo.ensure(branch="other")
    assert (repo.path / "branch.txt").read_text() == "other"


@requires_git
def test_missing_branch_never_falls_back_to_remote_head(remote, tmp_path):
    repo = GitRepo("camera/hal", remote.as_uri(), tmp_path / "clone")
    with pytest.raises(GitError, match="clone failed"):
        repo.ensure(branch="missing")
    repo.ensure(branch="main")
    before = repo.head_sha()
    with pytest.raises(GitError, match="fetch failed"):
        repo.ensure(branch="missing")
    assert repo.head_sha() == before


@requires_git
def test_head_mode_remains_available(remote, tmp_path):
    repo = GitRepo("camera/hal", remote.as_uri(), tmp_path / "clone")
    repo.ensure(branch="HEAD")
    assert (repo.path / "branch.txt").read_text() == "other"
    repo.ensure(branch="HEAD")
    assert repo.head_sha() == git(remote, "rev-parse", "HEAD")


@requires_git
def test_cached_origin_cannot_silently_reuse_another_host(remote, tmp_path):
    repo = GitRepo("camera/hal", remote.as_uri(), tmp_path / "clone")
    repo.ensure()
    repo.clone_url = "https://github.example.invalid/camera/hal.git"
    with pytest.raises(GitError, match="cached origin differs"):
        repo.ensure()


@requires_git
def test_tag_is_not_accepted_as_an_analysis_branch(remote, tmp_path):
    git(remote, "tag", "tag-only")
    repo = GitRepo("camera/hal", remote.as_uri(), tmp_path / "clone")
    with pytest.raises(GitError):
        repo.ensure(branch="tag-only")


@requires_git
def test_pipeline_records_and_reuses_the_selected_branch(remote, tmp_path, config_path, monkeypatch):
    config = load_config(config_path)
    repo = ResolvedRepo("camera/hal", "camera", "hal", "https://github.example.invalid/camera/hal", TargetDefaults())
    monkeypatch.setattr(ResolvedRepo, "clone_url", property(lambda self: remote.as_uri()))
    options = RunOptions(config_path, tmp_path / "data", tmp_path / "cache", tmp_path / "state.db")
    store = Store(options.db_path)
    try:
        store.start_run("first", "live")
        first = _scan_repository(repo, config, options, store, None, "first")
        assert first.status == "ok"
        assert first.default_branch == "main"
        assert first.head_sha == git(remote, "rev-parse", "main")
        store.start_run("second", "live")
        second = _scan_repository(repo, config, options, store, None, "second")
        assert second.default_branch == "main"
        assert second.head_sha == first.head_sha
        assert second.scan_mode == "incremental"
    finally:
        store.close()
