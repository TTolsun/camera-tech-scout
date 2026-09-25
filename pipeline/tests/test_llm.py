"""The LLM layer may refine prose but may never publish an uncited sentence.

The model's reply is injected directly, so these tests pin the rule without an
endpoint: every sentence the layer accepts cites a real evidence id, and every
sentence that does not is counted as discarded (#3).
"""

from __future__ import annotations

from scout.engine.llm import LLMClient, LLMReport, LLMSettings, _parse_json
from scout.models import Candidate, Evidence, Narrative

EV = "ev_real"


def _candidate() -> Candidate:
    blank = Narrative(text="규칙 기반 서술입니다.", evidence_ids=[EV], confidence="LOW_CONFIDENCE")
    return Candidate(
        id="c1", slug="c1", title="t", type="patent", signature="s", topic="isp",
        mechanism_family="prediction", summary="규칙 기반 요약입니다.",
        problem=blank, existing_approach=blank, proposed_technique=blank,
        difference=blank, technical_effect=blank, patent_angle={}, paper_angle={},
        prior_art_keywords=[], evidence_ids=[EV], repositories=["r"],
    )


def _client(reply: dict | None, **settings) -> LLMClient:
    client = LLMClient(LLMSettings(enabled=True, **settings))
    client._chat = lambda *args, **kwargs: reply  # type: ignore[method-assign]
    return client


EVIDENCE = [Evidence(id=EV, org="o", repo="r", kind="code")]


def test_cited_prose_is_accepted_and_keeps_its_confidence():
    candidate = _candidate()
    client = _client({"problem": {"text": "새 서술입니다.", "evidenceIds": [EV]}})

    client.enrich_candidate(candidate, EVIDENCE)

    assert candidate.problem.text == "새 서술입니다."
    assert candidate.problem.confidence == "LOW_CONFIDENCE"
    assert client.report.claims_accepted == 1
    assert client.report.candidates_enriched == 1


def test_uncited_or_invented_ids_are_discarded():
    candidate = _candidate()
    client = _client({
        "problem": {"text": "근거 없는 서술입니다.", "evidenceIds": []},
        "difference": {"text": "없는 id를 인용합니다.", "evidenceIds": ["ev_made_up"]},
    })

    client.enrich_candidate(candidate, EVIDENCE)

    assert candidate.problem.text == "규칙 기반 서술입니다."
    assert candidate.difference.text == "규칙 기반 서술입니다."
    assert client.report.claims_discarded == 2
    assert client.report.candidates_enriched == 0


def test_a_bare_string_summary_is_discarded():
    """The summary used to be the one sentence published without a citation."""
    candidate = _candidate()
    client = _client({"summary": "근거 없는 요약입니다."})

    client.enrich_candidate(candidate, EVIDENCE)

    assert candidate.summary == "규칙 기반 요약입니다."
    assert client.report.claims_discarded == 1


def test_a_cited_summary_is_accepted():
    candidate = _candidate()
    client = _client({"summary": {"text": "근거 있는 요약입니다.", "evidenceIds": [EV]}})

    client.enrich_candidate(candidate, EVIDENCE)

    assert candidate.summary == "근거 있는 요약입니다."
    assert client.report.claims_accepted == 1


def test_objections_without_citations_are_dropped():
    client = _client({"objections": [
        {"title": "근거 있음", "detail": "반박입니다.", "evidenceIds": [EV]},
        {"title": "근거 없음", "detail": "반박입니다.", "evidenceIds": []},
    ]})

    findings = client.challenge_candidate(_candidate(), EVIDENCE)

    assert [f.title for f in findings] == ["근거 있음"]
    assert client.report.claims_accepted == 1
    assert client.report.claims_discarded == 1


def test_extra_body_is_sent_but_cannot_override_the_request():
    sent = {}

    class Response:
        status_code = 200

        @staticmethod
        def json():
            return {"choices": [{"message": {"content": "{}"}}]}

    client = LLMClient(LLMSettings(
        enabled=True, extra_body={"reasoning_effort": "none", "model": "wrong"},
    ))
    client.session.post = lambda url, json, timeout: sent.update(json) or Response()

    client._chat("qwen", "system", "user", 0.0)

    assert sent["reasoning_effort"] == "none"
    assert sent["model"] == "qwen"


def test_parse_json_tolerates_a_code_fence():
    report = LLMReport()

    assert _parse_json('```json\n{"objections": []}\n```', "m", report) == {"objections": []}
    assert report.failures == []


def test_evidence_block_labels_removed_code_and_adds_source_lines():
    from scout.engine.llm import _evidence_block
    from scout.models import Signal

    item = Evidence(id=EV, org="o", repo="r", kind="commit", snippet="predict start of frame", signals=[
        Signal(kind="prior", term="lookup table", family="f", weight=1.0,
               context="vblank = calibrationTable[mode];"),
        Signal(kind="mechanism", term="predict", family="f", weight=1.0,
               context="predictedSofNs_ = lastSofNs_ + frameDurationNs_;"),
    ])

    block = _evidence_block([item])

    assert "prior (삭제된 이전 코드): vblank = calibrationTable[mode];" in block
    assert "line: predictedSofNs_ = lastSofNs_ + frameDurationNs_;" in block


def test_cited_text_mixing_chinese_is_not_published():
    """A small Qwen model wrote "기준线与" into an otherwise Korean sentence."""
    candidate = _candidate()
    client = _client({
        "summary": {"text": "고정 vblank 기준线与 적응형 컨트롤러를 비교합니다.", "evidenceIds": [EV]},
        "problem": {"text": "단순 최적화范畴에 속합니다.", "evidenceIds": [EV]},
    })

    client.enrich_candidate(candidate, EVIDENCE)

    assert candidate.summary == "규칙 기반 요약입니다."
    assert candidate.problem.text == "규칙 기반 서술입니다."
    assert client.report.claims_off_language == 2
    assert client.report.claims_discarded == 0


def test_objections_mixing_chinese_are_dropped():
    client = _client({"objections": [
        {"title": "t", "detail": "단순 최적화范畴에 속합니다.", "evidenceIds": [EV]},
    ]})

    assert client.challenge_candidate(_candidate(), EVIDENCE) == []
    assert client.report.claims_off_language == 1
