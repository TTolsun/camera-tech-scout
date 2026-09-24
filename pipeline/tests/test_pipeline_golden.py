"""End-to-end golden test.

Runs the whole pipeline against the bundled fixtures and pins the result. This
is the test that would have caught the git log parsing bug: a broken parser
changes the commit evidence, which changes the candidates.

The expectations below are not arbitrary. Each fixture repository was written to
produce a specific outcome:

* ``sensor-sync-hal``      two candidates, one of them Patent + Paper because the
                           history quantifies the effect.
* ``isp-buffer-pipeline``  two candidates that share a technology with the first
                           repository, which exercises cross-repository linking.
* ``cam-utils``            rejected by R1, because its history is formatting and
                           a tuning constant.

The sensor-sync history is also a replacement: the first commit corrects vblank
from a calibration lookup table and the second deletes it in favour of
prediction, which is what lets the diff reader recover the baseline.

When a change moves these numbers, that is the point at which someone has to
decide whether the change improved the analysis or broke it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scout.pipeline import RunOptions, run

from conftest import requires_git

EXPECTED_ACCEPTED = {
    "isp|adaptive-control",
    "isp|buffer-mechanism",
    "sensor-sync|prediction",
    "sensor-sync|synchronization-mechanism",
}
EXPECTED_REJECTED = {"3a|adaptive-control"}
# Rose from 24 when commit diffs began to be read: three commits whose
# messages carry no camera vocabulary turned out to have it in their diffs.
EXPECTED_EVIDENCE_TOTAL = 27
EXPECTED_EVIDENCE_KINDS = {"code", "doc", "commit", "pull_request", "issue", "release", "config"}


@pytest.fixture(scope="module")
def golden_run(tmp_path_factory, request):
    """Run the fixture pipeline once and expose its output directory."""
    root = tmp_path_factory.mktemp("golden")
    config = Path(__file__).resolve().parents[2] / "config" / "sources.yaml"
    if not config.exists():
        pytest.skip("config/sources.yaml is not present")

    options = RunOptions(
        config_path=config,
        data_dir=root / "data",
        cache_dir=root / "cache",
        db_path=root / "cache" / "candidates.sqlite3",
        dry_run=True,
        fixture=True,
        use_llm=False,
        rebuild_fixtures=True,
    )
    result = run(options)
    return result


def read(result, name: str):
    return json.loads((result.data_dir / name).read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Counts
# ---------------------------------------------------------------------------
@requires_git
def test_run_completes(golden_run):
    assert golden_run.mode == "fixture"
    assert golden_run.dry_run is True
    assert len(golden_run.repositories) == 3


@requires_git
def test_every_fixture_repository_is_scanned(golden_run):
    names = {state.full_name for state in golden_run.repositories}

    assert names == {
        "example-camera/cam-utils",
        "example-camera/isp-buffer-pipeline",
        "example-camera/sensor-sync-hal",
    }
    assert all(state.status == "ok" for state in golden_run.repositories)


@requires_git
def test_evidence_total_and_kinds(golden_run):
    evidence = read(golden_run, "evidence.json")

    assert evidence["counts"]["total"] == EXPECTED_EVIDENCE_TOTAL
    assert set(evidence["counts"]["byKind"]) == EXPECTED_EVIDENCE_KINDS


@requires_git
def test_accepted_candidates(golden_run):
    candidates = read(golden_run, "candidates.json")["candidates"]

    assert {c["signature"] for c in candidates} == EXPECTED_ACCEPTED


@requires_git
def test_rejected_candidates_are_kept_with_a_reason(golden_run):
    rejected = read(golden_run, "rejected.json")["rejected"]

    assert {r["signature"] for r in rejected} == EXPECTED_REJECTED
    for record in rejected:
        assert record["reason"], "a rejection must state why"
        assert record["failedRules"], "a rejection must name the rules it failed"
        assert record["recheckConditions"], "a rejection must say when to look again"


# ---------------------------------------------------------------------------
# The evidence-first guarantee
# ---------------------------------------------------------------------------
@requires_git
def test_every_candidate_is_backed_by_code(golden_run):
    """A candidate may not exist on prose alone."""
    candidates = read(golden_run, "candidates.json")["candidates"]

    for candidate in candidates:
        kinds = {e["kind"] for e in candidate["evidence"]}
        assert "code" in kinds, f"{candidate['signature']} has no code evidence"


@requires_git
def test_every_cited_evidence_id_exists(golden_run):
    """A narrative may not cite an evidence record that was never collected."""
    data = read(golden_run, "candidates.json")
    known = {e["id"] for e in read(golden_run, "evidence.json")["evidence"]}

    for candidate in data["candidates"]:
        for section in ("problem", "existingApproach", "proposedTechnique",
                        "difference", "technicalEffect"):
            for evidence_id in candidate[section]["evidence_ids"]:
                assert evidence_id in known, (
                    f"{candidate['signature']}.{section} cites unknown {evidence_id}"
                )


@requires_git
def test_ungrounded_sections_carry_a_marker(golden_run):
    """A section without citations has to say so rather than read as a finding."""
    candidates = read(golden_run, "candidates.json")["candidates"]

    for candidate in candidates:
        for section in ("problem", "existingApproach", "technicalEffect"):
            block = candidate[section]
            if not block["evidence_ids"]:
                assert block["confidence"] in {
                    "UNKNOWN", "LOW_CONFIDENCE", "NEEDS_VERIFICATION"
                }, f"{candidate['signature']}.{section} is ungrounded but unmarked"


@requires_git
def test_code_evidence_points_at_a_symbol_and_a_line(golden_run):
    evidence = read(golden_run, "evidence.json")["evidence"]
    code = [e for e in evidence if e["kind"] == "code"]

    assert code
    for item in code:
        assert item["path"], "code evidence needs a file path"
        assert item["symbol"], "code evidence needs a symbol name"
        assert item["line_start"], "code evidence needs a line number"
        assert item["url"].startswith("https://github.com/")


@requires_git
def test_generated_variation_ideas_are_labelled_as_generated(golden_run):
    """Suggestions the pipeline invented must never read as findings."""
    candidates = read(golden_run, "candidates.json")["candidates"]

    for candidate in candidates:
        variations = candidate["patentAngle"]["variations"]
        assert variations["generated"] is True
        assert variations["note"]


# ---------------------------------------------------------------------------
# Scores
# ---------------------------------------------------------------------------
@requires_git
def test_every_score_carries_its_reasons(golden_run):
    candidates = read(golden_run, "candidates.json")["candidates"]

    for candidate in candidates:
        for key, score in candidate["scores"].items():
            assert score["reasons"], f"{candidate['signature']}.{key} has no reasons"
            assert 0 <= score["value"] <= 100


@requires_git
def test_novelty_confidence_states_that_it_is_not_a_novelty_finding(golden_run):
    candidates = read(golden_run, "candidates.json")["candidates"]

    for candidate in candidates:
        caveat = candidate["scores"]["novelty_confidence"]["caveat"]
        assert caveat and "신규성" in caveat


# ---------------------------------------------------------------------------
# Cross-repository linking
# ---------------------------------------------------------------------------
@requires_git
def test_technologies_shared_between_repositories_are_linked(golden_run):
    technology = read(golden_run, "technology-map.json")

    cross = technology["crossRepositoryTechnologies"]
    assert cross, "the fixtures deliberately share a technology across repositories"
    for row in cross:
        assert len(row["repositories"]) > 1


@requires_git
def test_graph_covers_every_layer(golden_run):
    counts = read(golden_run, "technology-map.json")["graph"]["counts"]

    for layer in ("organization", "repository", "component", "technology",
                  "problem", "mechanism", "candidate"):
        assert counts.get(layer, 0) > 0, f"the {layer} layer is empty"


# ---------------------------------------------------------------------------
# Emission
# ---------------------------------------------------------------------------
@requires_git
def test_every_page_has_its_json(golden_run):
    expected = {
        "meta.json", "overview.json", "sources.json", "repositories.json",
        "candidates.json", "technology-map.json", "recent.json", "rejected.json",
        "evidence.json", "history.json", "search-index.json",
    }
    written = {path.name for path in golden_run.data_dir.glob("*.json")}

    assert expected <= written


@requires_git
def test_dry_run_reports_itself_as_a_dry_run(golden_run):
    meta = read(golden_run, "meta.json")

    assert meta["dryRun"] is True
    assert meta["mode"] == "fixture"


@requires_git
def test_llm_layer_is_skipped_without_an_endpoint(golden_run):
    """With the model off, the rules output has to stand on its own."""
    engine = read(golden_run, "meta.json")["engine"]

    assert engine["skipped"] is True
    assert engine["enabled"] is False
