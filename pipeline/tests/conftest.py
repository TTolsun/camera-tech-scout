"""Shared test fixtures.

The pipeline is exercised against the bundled offline fixtures, which are real
git repositories. That keeps every test deterministic and offline: no GitHub
API, no network, no rate limit.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from scout import fixtures as fixture_module
from scout.collect import GitRepo


def _git_available() -> bool:
    try:
        subprocess.run(["git", "--version"], capture_output=True, check=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return False
    return True


requires_git = pytest.mark.skipif(not _git_available(), reason="git is not installed")


@pytest.fixture(scope="session")
def fixture_root(tmp_path_factory) -> Path:
    """Materialise the offline fixture repositories once per test session.

    They are real git repositories with real commits, so tests that read history
    exercise the same code path a live scan does.
    """
    root = tmp_path_factory.mktemp("fixtures")
    fixture_module.materialise(root, force=True)
    return root


@pytest.fixture
def cam_utils_repo(fixture_root: Path) -> GitRepo:
    """The fixture whose history is three commits, used for log parsing tests."""
    return GitRepo(
        full_name="example-camera/cam-utils",
        clone_url="",
        path=fixture_root / "example-camera" / "cam-utils",
    )


@pytest.fixture
def sync_repo(fixture_root: Path) -> GitRepo:
    return GitRepo(
        full_name="example-camera/sensor-sync-hal",
        clone_url="",
        path=fixture_root / "example-camera" / "sensor-sync-hal",
    )


@pytest.fixture
def config_path() -> Path:
    path = Path(__file__).resolve().parents[2] / "config" / "sources.yaml"
    if not path.exists():
        pytest.skip("config/sources.yaml is not present")
    return path
