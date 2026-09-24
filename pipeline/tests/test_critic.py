"""Critic rules.

Each rule is an attempt to show that a candidate is ordinary engineering. These
tests check both directions for every rule: it fires on the input it is meant to
catch, and it stays quiet on a healthy candidate. A rule that never fires is
useless, and a rule that always fires is worse.
"""

from __future__ import annotations

import pytest

from scout.critic import RULES, Critic
from scout.lexicon import LEXICON
from scout.models import Candidate, Evidence, Narrative, Score


def make_evidence(
    *,
    id: str,
    kind: str = "code",
    path: str | None = "src/sync.cpp",
    symbol: str | None = "SyncController::update",
    title: str = "function SyncController::update",
    snippet: str = "",
) -> Evidence:
    """Build one evidence record, with its signals derived from the snippet.

    Signals come from the real lexicon rather than being hand-written, so a
    change to the term tables shows up in these tests too.
    """
    item = Evidence(
        id=id,
        org="example-camera",
        repo="sensor-sync-hal",
        kind=kind,
        path=path,
        symbol=symbol,
        line_start=10,
        line_end=40,
        title=title,
        snippet=snippet,
        url="https://example.invalid",
    )
    item.signals = LEXICON.match(f"{title}\n{snippet}")
    return item


def make_candidate(
    evidence: list[Evidence],
    *,
    effect_confidence: str = "",
) -> Candidate:
    empty = Narrative(text="", evidence_ids=[])
    return Candidate(
        id="cand_test",
        slug="test",
        title="테스트 후보",
        type="patent",
        signature="sensor-sync|prediction",
        topic="sensor-sync",
        mechanism_family="prediction",
        summary="",
        problem=empty,
        existing_approach=empty,
        proposed_technique=empty,
        difference=empty,
        technical_effect=Narrative(
            text="", evidence_ids=[e.id for e in evidence[:1]],
            confidence=effect_confidence,
        ),
        patent_angle={},
        paper_angle={},
        prior_art_keywords=[],
        evidence_ids=[e.id for e in evidence],
        repositories=["example-camera/sensor-sync-hal"],
        scores={
            "patent_potential": Score(value=60, label="Patent"),
            "paper_potential": Score(value=40, label="Paper"),
            "evidence_strength": Score(value=70, label="Evidence"),
            "novelty_confidence": Score(value=60, label="Novelty"),
        },
    )


def rule(rule_id: str):
    return next(r for r in RULES if r.id == rule_id)


def check(rule_id: str, evidence: list[Evidence], **kwargs) -> tuple[bool, str]:
    candidate = make_candidate(evidence, **kwargs)
    passed, detail, _ = rule(rule_id).check(candidate, evidence)
    return passed, detail


# A candidate that should survive every rule.
def healthy_evidence() -> list[Evidence]:
    return [
        make_evidence(
            id="ev_code",
            kind="code",
            snippet=(
                "Predicts the next start of frame from a monotonic clock and applies "
                "drift compensation, reducing jitter from 3.2 ms to 0.4 ms."
            ),
        ),
        make_evidence(
            id="ev_doc",
            kind="doc",
            path="docs/sync.md",
            symbol="Mechanism",
            snippet="The controller predicts the start of frame and compensates drift.",
        ),
        make_evidence(
            id="ev_commit",
            kind="commit",
            path=None,
            symbol=None,
            snippet="camera: predict start of frame and compensate sensor drift",
        ),
    ]


# ---------------------------------------------------------------------------
# Every rule leaves a healthy candidate alone.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("rule_id", [r.id for r in RULES])
def test_healthy_candidate_passes_every_rule(rule_id):
    passed, detail = check(rule_id, healthy_evidence())

    assert passed, f"{rule_id} should not fire on a healthy candidate: {detail}"


# ---------------------------------------------------------------------------
# R1 trivial change
# ---------------------------------------------------------------------------
def test_r1_fires_when_triviality_markers_dominate():
    evidence = [
        make_evidence(id="ev_code", snippet="adaptive metering helper for auto exposure"),
        make_evidence(
            id="ev_c1", kind="commit", path=None, symbol=None,
            snippet="run clang-format over the adaptive auto exposure helper, whitespace only",
        ),
        make_evidence(
            id="ev_c2", kind="commit", path=None, symbol=None,
            snippet="bump version and update the changelog for the adaptive auto exposure helper",
        ),
    ]
    passed, detail = check("R1", evidence)

    assert not passed
    assert "사소한 변경" in detail


def test_r1_fires_when_there_is_no_code():
    evidence = [
        make_evidence(id="ev_d1", kind="doc", snippet="adaptive prediction of the sof"),
        make_evidence(id="ev_d2", kind="doc", snippet="adaptive prediction again"),
    ]
    passed, _ = check("R1", evidence)

    assert not passed


# ---------------------------------------------------------------------------
# R2 configuration only
# ---------------------------------------------------------------------------
def test_r2_fires_on_build_files_only():
    evidence = [
        make_evidence(
            id="ev_cfg", kind="config", path="CMakeLists.txt", symbol=None,
            snippet="option(ENABLE_ADAPTIVE_METERING ...)",
        ),
    ]
    passed, detail = check("R2", evidence)

    assert not passed
    assert "설정 파일" in detail


# ---------------------------------------------------------------------------
# R3 generic design pattern
# ---------------------------------------------------------------------------
def test_r3_fires_when_patterns_dominate_a_single_mechanism_term():
    evidence = [
        make_evidence(
            id="ev_code",
            snippet="A singleton factory pattern holding a shared_ptr, with one adaptive knob.",
        ),
        make_evidence(
            id="ev_doc", kind="doc",
            snippet="Uses the observer pattern and a singleton registry for the isp.",
        ),
    ]
    passed, detail = check("R3", evidence)

    assert not passed
    assert "설계 패턴" in detail


# ---------------------------------------------------------------------------
# R4 parameter tuning
# ---------------------------------------------------------------------------
def test_r4_fires_on_constant_assignment_with_tuning_talk():
    evidence = [
        make_evidence(
            id="ev_code",
            snippet=(
                "Adaptive metering threshold for auto exposure. "
                "int threshold = 96;"
            ),
        ),
    ]
    passed, detail = check("R4", evidence)

    assert not passed
    assert "parameter tuning" in detail


def test_r4_stays_quiet_when_there_is_logic():
    evidence = [
        make_evidence(
            id="ev_code",
            snippet=(
                "predicted = observed + duration - drift; "
                "slope = 0.85 * slope + 0.15 * drift;"
            ),
        ),
    ]
    passed, _ = check("R4", evidence)

    assert passed


# ---------------------------------------------------------------------------
# R5 platform plumbing
# ---------------------------------------------------------------------------
def test_r5_fires_when_platform_plumbing_dominates():
    evidence = [
        make_evidence(
            id="ev_code",
            snippet=(
                "Calls VIDIOC_QBUF through v4l2_ioctl, wired up in CMakeLists and "
                "Android.bp, exposed over hidl. One adaptive step remains."
            ),
        ),
    ]
    passed, detail = check("R5", evidence)

    assert not passed
    assert "플랫폼" in detail


# ---------------------------------------------------------------------------
# R6 sufficient evidence
# ---------------------------------------------------------------------------
def test_r6_fires_on_a_single_record():
    evidence = [make_evidence(id="ev_code", snippet="predicts the next sof adaptively")]
    passed, detail = check("R6", evidence)

    assert not passed
    assert "1건" in detail


def test_r6_fires_when_every_record_is_the_same_kind():
    evidence = [
        make_evidence(id="ev_a", snippet="predicts the next sof"),
        make_evidence(id="ev_b", snippet="applies drift compensation"),
    ]
    passed, detail = check("R6", evidence)

    assert not passed
    assert "한 종류" in detail


def test_r6_fires_without_code():
    evidence = [
        make_evidence(id="ev_doc", kind="doc", snippet="predicts the sof"),
        make_evidence(id="ev_commit", kind="commit", path=None, symbol=None,
                      snippet="predicts the sof"),
    ]
    passed, detail = check("R6", evidence)

    assert not passed
    assert "코드 증거" in detail


# ---------------------------------------------------------------------------
# R7 claim matches code
# ---------------------------------------------------------------------------
def test_r7_fires_when_the_mechanism_is_only_in_prose():
    evidence = [
        make_evidence(
            id="ev_code",
            snippet="Plain accessor for the sensor mode with no mechanism vocabulary.",
        ),
        make_evidence(
            id="ev_doc", kind="doc",
            snippet="The design predicts the start of frame and compensates drift.",
        ),
    ]
    passed, detail = check("R7", evidence)

    assert not passed
    assert "일치하지 않습니다" in detail


# ---------------------------------------------------------------------------
# R8 measured effect
# ---------------------------------------------------------------------------
def test_r8_fires_when_the_effect_is_unquantified():
    passed, detail = check("R8", healthy_evidence(), effect_confidence="LOW_CONFIDENCE")

    assert not passed
    assert "정량화" in detail


# ---------------------------------------------------------------------------
# Verdicts
# ---------------------------------------------------------------------------
def test_fatal_failure_rejects_and_soft_failure_only_reserves():
    trivial = [
        make_evidence(id="ev_code", snippet="adaptive metering helper for auto exposure"),
        make_evidence(id="ev_c1", kind="commit", path=None, symbol=None,
                      snippet="clang-format the adaptive auto exposure helper, whitespace only"),
        make_evidence(id="ev_c2", kind="commit", path=None, symbol=None,
                      snippet="bump version, changelog for the adaptive auto exposure helper"),
    ]
    rejected_candidate = make_candidate(trivial)
    healthy = healthy_evidence()
    reserved_candidate = make_candidate(healthy, effect_confidence="LOW_CONFIDENCE")

    by_id = {e.id: e for e in trivial + healthy}
    accepted, rejections = Critic().run([rejected_candidate, reserved_candidate], by_id)

    assert len(rejections) == 1
    assert rejections[0].failed_rules
    assert rejections[0].recheck_conditions, "a rejection must say when to look again"

    assert len(accepted) == 1
    assert accepted[0].verdict == "accepted_with_reservations"


def test_rejection_records_are_not_empty():
    evidence = [make_evidence(id="ev_only", snippet="predicts the next sof")]
    candidate = make_candidate(evidence)

    _, rejections = Critic().run([candidate], {e.id: e for e in evidence})

    assert len(rejections) == 1
    rejection = rejections[0]
    assert rejection.reason
    assert rejection.signature == "sensor-sync|prediction"
    assert rejection.evidence_ids == ["ev_only"]
    assert rejection.scores, "the scores at rejection time are kept for later review"


def test_every_rule_is_reported_even_when_it_passes():
    evidence = healthy_evidence()
    candidate = make_candidate(evidence)

    Critic().run([candidate], {e.id: e for e in evidence})

    assert len(candidate.critic_findings) == len(RULES)
    assert all(f.detail for f in candidate.critic_findings), "a finding must explain itself"
