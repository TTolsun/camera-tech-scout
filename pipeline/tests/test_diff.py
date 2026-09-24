"""Commit diff reading and the baseline it recovers.

The Existing Approach section was ungrounded for almost every candidate because
the pipeline only read commit messages, and most authors do not write down what
they replaced. The removed lines do record it. These tests cover the reading,
the filtering that keeps it honest, and the narrative it produces.
"""

from __future__ import annotations

import pytest

from scout.collect import DiffText
from scout.evidence import _prior_signals
from scout.lexicon import LEXICON
from scout.models import Evidence, Narrative
from scout.scouting import Scout
from scout.util import looks_like_comment

from conftest import requires_git


# ---------------------------------------------------------------------------
# Reading a diff
# ---------------------------------------------------------------------------
@requires_git
def test_diff_text_splits_removed_from_added(sync_repo):
    commits = sync_repo.commits(max_count=10)
    replacement = next(c for c in commits if "predict start of frame" in c.subject)

    diff = sync_repo.diff_text(replacement.sha)

    assert diff.removed, "the replacement commit deletes the lookup table version"
    assert diff.added
    assert "lookup table" in diff.removed_text.lower()
    assert "predict" in diff.added_text.lower()


@requires_git
def test_diff_text_records_the_paths_it_read(sync_repo):
    commits = sync_repo.commits(max_count=10)
    replacement = next(c for c in commits if "predict start of frame" in c.subject)

    diff = sync_repo.diff_text(replacement.sha)

    assert "src/sensor_sync_controller.cpp" in diff.paths


@requires_git
def test_diff_text_drops_headers_and_hunk_markers(sync_repo):
    commits = sync_repo.commits(max_count=10)
    diff = sync_repo.diff_text(commits[0].sha)

    for line in diff.removed + diff.added:
        assert not line.startswith(("---", "+++", "@@", "diff --git"))


@requires_git
def test_diff_text_honours_the_line_budget(sync_repo):
    commits = sync_repo.commits(max_count=10)
    replacement = next(c for c in commits if "predict start of frame" in c.subject)

    diff = sync_repo.diff_text(replacement.sha, max_lines=4)

    assert len(diff.removed) + len(diff.added) <= 4
    assert diff.truncated


@requires_git
def test_diff_text_applies_the_path_filter(sync_repo):
    """Without this, the diff would read trees the file scan deliberately skips."""
    commits = sync_repo.commits(max_count=10)
    replacement = next(c for c in commits if "predict start of frame" in c.subject)

    everything = sync_repo.diff_text(replacement.sha)
    nothing = sync_repo.diff_text(replacement.sha, accept_path=lambda path: False)
    docs_only = sync_repo.diff_text(
        replacement.sha, accept_path=lambda path: path.startswith("docs/")
    )

    assert everything.removed or everything.added
    assert not nothing.removed and not nothing.added
    assert all(path.startswith("docs/") for path in docs_only.paths)


@requires_git
def test_diff_text_of_an_unknown_commit_is_empty(sync_repo):
    diff = sync_repo.diff_text("0" * 40)

    assert not diff


# ---------------------------------------------------------------------------
# Turning removed lines into prior-approach signals
# ---------------------------------------------------------------------------
def make_diff(removed: list[str], added: list[str]) -> DiffText:
    return DiffText(sha="a" * 40, removed=removed, added=added)


def test_prior_signal_is_produced_for_a_removed_mechanism():
    diff = make_diff(
        removed=["int32_t v = kVblankLookupTable[i];", "// calibration table lookup"],
        added=["int32_t v = predictNextSof(now);"],
    )

    signals = _prior_signals(diff)

    assert {s.kind for s in signals} == {"prior"}
    assert "calibrat" in {s.term for s in signals}


def test_a_term_present_on_both_sides_is_not_a_prior_signal():
    """Moved code says nothing about a change of approach."""
    diff = make_diff(
        removed=["applyCalibration(oldTable);"],
        added=["applyCalibration(newTable);"],
    )

    assert _prior_signals(diff) == []


def test_a_comment_only_deletion_produces_nothing():
    """Rewording documentation is not replacing a mechanism."""
    diff = make_diff(
        removed=["// uses a calibration lookup table", "* interpolation notes"],
        added=["// updated wording"],
    )

    assert _prior_signals(diff) == []


def test_prior_signal_prefers_quoting_deleted_code_over_a_deleted_comment():
    diff = make_diff(
        removed=[
            "// the old path used drift compensation",
            "applyDriftCompensation(offset);",
        ],
        added=["applyPrediction(offset);"],
    )

    signals = _prior_signals(diff)
    drift = next(s for s in signals if s.term == "drift compensat")

    assert not looks_like_comment(drift.context)
    assert "applyDriftCompensation" in drift.context


def test_no_removed_lines_produces_nothing():
    assert _prior_signals(make_diff(removed=[], added=["something new"])) == []


# ---------------------------------------------------------------------------
# The narrative built from those signals
# ---------------------------------------------------------------------------
def evidence_with_prior(lines: list[str], added: list[str]) -> Evidence:
    item = Evidence(
        id="ev_commit", org="example-camera", repo="sensor-sync-hal", kind="commit",
        commit_sha="a" * 40, title="camera: replace the table with a prediction",
        snippet="camera: replace the table with a prediction",
        url="https://example.invalid",
    )
    item.signals = LEXICON.match(item.snippet) + _prior_signals(
        make_diff(removed=lines, added=added)
    )
    return item


def existing(items: list[Evidence]) -> Narrative:
    return Scout.__dict__["_existing_from_diff"].__func__(items)


def test_narrative_from_removed_code_is_grounded():
    item = evidence_with_prior(
        ["currentCcm_ = ccm_.getInterpolated(ct);"],
        ["currentCcm_ = ccm_.getPredicted(ct);"],
    )

    narrative = existing([item])

    assert narrative is not None
    assert narrative.confidence == "", "removed code is direct evidence"
    assert narrative.evidence_ids == ["ev_commit"]
    assert "getInterpolated" in narrative.text


def test_narrative_from_a_removed_comment_is_marked_low_confidence():
    """The approach may still be there; only the sentence about it went away."""
    item = evidence_with_prior(
        ["// applied drift compensation here", "someUnrelatedCall();"],
        ["someOtherCall();"],
    )

    narrative = existing([item])

    assert narrative is not None
    assert narrative.confidence == "LOW_CONFIDENCE"
    assert "확인이 필요합니다" in narrative.text


def test_a_comment_term_does_not_downgrade_a_grounded_baseline():
    """The quote decides the confidence, so it must be the strongest signal.

    Taking whichever prior signal matched first would mark the whole section
    LOW_CONFIDENCE because some unrelated term happened to be mentioned in a
    deleted comment, even though deleted code proves the replacement.
    """
    item = evidence_with_prior(
        [
            "// mentions calibration in passing",
            "applyDriftCompensation(offset);",
        ],
        ["applyPrediction(offset);"],
    )
    terms = [s.term for s in item.signals if s.kind == "prior"]
    assert "calibrat" in terms and "drift compensat" in terms, "both must be present"

    narrative = existing([item])

    assert narrative is not None
    assert narrative.confidence == "", "deleted code outranks a deleted comment"
    assert "applyDriftCompensation" in narrative.text


def test_no_prior_signals_yields_no_narrative():
    item = Evidence(id="ev", org="o", repo="r", kind="commit", snippet="plain message")
    item.signals = []

    assert existing([item]) is None


# ---------------------------------------------------------------------------
# End to end against the fixture history
# ---------------------------------------------------------------------------
@requires_git
def test_fixture_baseline_is_recovered_from_the_diff(golden_run):
    """sensor-sync replaced a lookup table with prediction; the diff shows it."""
    import json

    data = json.loads(
        (golden_run.data_dir / "candidates.json").read_text(encoding="utf-8")
    )
    candidate = next(
        c for c in data["candidates"] if c["signature"] == "sensor-sync|prediction"
    )
    existing_approach = candidate["existingApproach"]

    assert existing_approach["evidence_ids"], "the baseline has to cite something"
    assert "lookup table" in existing_approach["text"] or "제거되었고" in existing_approach["text"]


@pytest.fixture(scope="module")
def golden_run(tmp_path_factory):
    """A fixture run scoped to this module, mirroring the golden test."""
    from pathlib import Path

    from scout.pipeline import RunOptions, run

    config = Path(__file__).resolve().parents[2] / "config" / "sources.yaml"
    if not config.exists():
        pytest.skip("config/sources.yaml is not present")

    root = tmp_path_factory.mktemp("diff-golden")
    return run(
        RunOptions(
            config_path=config,
            data_dir=root / "data",
            cache_dir=root / "cache",
            db_path=root / "cache" / "candidates.sqlite3",
            dry_run=True,
            fixture=True,
            use_llm=False,
            rebuild_fixtures=True,
        )
    )
