"""Configuration keys that do nothing must not look like they work.

`analysis.discussions` existed in the YAML and in `AnalysisToggles` without any
collection behind it, and unknown keys were dropped silently, so setting it to
true changed nothing and gave no reason (#5). Unknown keys are now errors.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scout.config import AnalysisToggles, ConfigError, Limits, TargetDefaults, load_config


def test_known_analysis_keys_are_read():
    toggles = AnalysisToggles.from_dict({"code": False, "issues": 0})

    assert toggles.code is False
    assert toggles.issues is False
    assert toggles.commits is True


def test_discussions_is_no_longer_a_setting():
    with pytest.raises(ConfigError, match="discussions"):
        AnalysisToggles.from_dict({"discussions": True})


def test_unknown_limit_is_rejected_with_the_valid_keys():
    with pytest.raises(ConfigError, match="max_commits"):
        Limits.from_dict({"max_comits": 10})


def test_unknown_key_in_a_per_repository_override_is_rejected():
    defaults = TargetDefaults.from_dict({})

    with pytest.raises(ConfigError, match="analysis"):
        defaults.merged_with({"analysis": {"discussion": True}})


def test_bundled_configuration_loads():
    config = Path(__file__).resolve().parents[2] / "config" / "sources.yaml"
    if not config.exists():
        pytest.skip("config/sources.yaml is not present")

    load_config(config)
