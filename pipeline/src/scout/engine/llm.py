"""Optional LLM enrichment layer.

The weekly run is driven by Hermes, which serves a Qwen model. That is one model,
not two, so the scout and the critic are separated by *prompt and sampling*
rather than by model identity:

* the scout pass may sharpen prose that the rules engine already anchored, and
  runs at a small non-zero temperature;
* the critic pass may only raise objections, and runs at temperature ``0`` so
  that the same candidate is challenged the same way on every run.

``scout_model`` and ``critic_model`` exist so that a deployment which does have
two models can still split the roles, but both default to the single ``model``.

The hard guarantee of this project survives here unchanged: **a model may not
create evidence**. Every sentence a model returns must cite at least one
evidence id that the pipeline actually collected. Sentences that cite nothing, or
that cite an id which does not exist, are discarded rather than displayed. If the
endpoint is unreachable, slow or returns malformed JSON, the pipeline keeps the
deterministic rules output and records the failure.

The endpoint is assumed to be OpenAI-compatible
(``POST {base_url}/chat/completions``), which covers vLLM, Ollama, llama.cpp
server, LM Studio and most internal gateways, including a Hermes deployment that
exposes the OpenAI shape. If Hermes is fronted by a different protocol, ``_chat``
is the only method that has to change.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any

import requests

from ..models import Candidate, CriticFinding, Evidence, Narrative
from ..util import log, truncate

_JSON_BLOCK_RE = re.compile(r"\{.*\}", re.DOTALL)
# Small Qwen models drift into Chinese mid-sentence ("기준线与", "최적화范畴").
# The output is Korean with English terms, so any Han character marks a
# sentence that is not fit to publish, cited or not.
_HAN_RE = re.compile("[\\u3400-\\u4dbf\\u4e00-\\u9fff\\uf900-\\ufaff]")

_SCOUT_SYSTEM = """당신은 Camera Software 분야의 기술 발굴 분석자입니다.

규칙:
1. 제공된 Evidence에 실제로 기재된 내용만 서술합니다. 추측을 서술하지 않습니다.
2. 모든 주장에는 그 근거가 되는 evidence id를 함께 제시합니다. 근거가 없으면 그 문장을 쓰지 않습니다.
3. 근거가 부족하면 UNKNOWN, LOW_CONFIDENCE, NEEDS_VERIFICATION 중 하나를 명시합니다.
4. 한국어로 서술합니다. 다만 심볼명, 파일 경로, Repository 이름, 기술 용어는 원어를 유지합니다. 기술 용어를 한글로 음차하지 않습니다. 예: drift를 "드리프트"로, closed loop를 "클로즈드 루프"로 쓰지 않고 원어로 씁니다.
5. 조사와 어미를 생략하지 않고, 종결어미를 갖춘 완성된 문장으로 끝맺습니다.
6. 오직 JSON만 출력합니다. 설명 문장이나 코드 블록 표시를 덧붙이지 않습니다."""

_CRITIC_SYSTEM = """당신은 Camera Software 분야의 기술 심사자입니다. 제시된 후보를 적극적으로 반박합니다.

다음을 검토합니다.
- 단순 optimization인가
- 흔한 software design pattern인가
- 단순 configuration 변경인가
- parameter tuning 수준인가
- 기존 Android, Linux, V4L2 카메라 시스템에서 일반적인 방법인가
- 발명보다 implementation detail에 가까운가
- Evidence가 충분한가
- 주장과 실제 코드가 일치하는가

규칙:
1. 반박은 제공된 Evidence에 근거해야 합니다. 각 반박에 evidence id를 제시합니다.
2. 근거 없는 반박은 제시하지 않습니다.
3. quote는 원본의 일부만 발췌한 것입니다. quote에 보이지 않는다는 이유로 구현이 없다거나 로직이 없다고 주장하지 않습니다. 부재를 주장하려면 그 부재를 직접 보여 주는 Evidence가 있어야 합니다.
4. 이전 방식과 현재 방식을 구분합니다. prior로 표시된 줄은 삭제된 이전 코드이며, 현재 기법이 아닙니다.
5. 한국어로 서술하되 심볼명과 기술 용어는 원어를 유지하고, 한글로 음차하지 않습니다.
6. 오직 JSON만 출력합니다."""


@dataclass
class LLMSettings:
    enabled: bool = False
    runner: str = "hermes"
    base_url: str = "http://localhost:8000/v1"
    api_key_env: str = "SCOUT_LLM_API_KEY"
    model: str = "qwen"
    # Left empty, both roles use `model`. A deployment with two models can set
    # them separately.
    scout_model: str = ""
    critic_model: str = ""
    scout_temperature: float = 0.3
    critic_temperature: float = 0.0
    timeout_seconds: int = 120
    max_candidates: int = 40
    # Extra fields merged into every request body, for server specific switches
    # such as turning off Qwen's thinking output (`reasoning_effort: none` on
    # Ollama). The fields this client sets itself always win.
    extra_body: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "LLMSettings":
        known = {f: data[f] for f in cls.__dataclass_fields__ if f in data}
        return cls(**known)

    @property
    def effective_scout_model(self) -> str:
        return self.scout_model or self.model

    @property
    def effective_critic_model(self) -> str:
        return self.critic_model or self.model


@dataclass
class LLMReport:
    """What the LLM layer actually did, shown on the Analysis History page."""

    enabled: bool = False
    reachable: bool = False
    runner: str = ""
    scout_model: str = ""
    critic_model: str = ""
    scout_temperature: float = 0.0
    critic_temperature: float = 0.0
    base_url: str = ""
    candidates_enriched: int = 0
    objections_added: int = 0
    # Accepted and discarded together give the citation rate, which is what
    # says whether the prompts need work (#3).
    claims_accepted: int = 0
    claims_discarded: int = 0
    # Cited, but written partly in another script. Kept apart from
    # claims_discarded, which measures citation, so each rate stays readable.
    claims_off_language: int = 0
    failures: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "reachable": self.reachable,
            "runner": self.runner,
            "scoutModel": self.scout_model,
            "criticModel": self.critic_model,
            "scoutTemperature": self.scout_temperature,
            "criticTemperature": self.critic_temperature,
            "baseUrl": self.base_url,
            "candidatesEnriched": self.candidates_enriched,
            "objectionsAdded": self.objections_added,
            "claimsAccepted": self.claims_accepted,
            "claimsDiscarded": self.claims_discarded,
            "claimsOffLanguage": self.claims_off_language,
            "failures": self.failures[:10],
            "note": ("LLM은 증거를 새로 만들 수 없습니다. evidence id를 인용하지 않은 문장은 "
                     "버립니다. Scout과 Critic은 같은 모델을 쓰지만 system prompt와 "
                     "temperature가 다릅니다."),
        }


class LLMClient:
    def __init__(self, settings: LLMSettings) -> None:
        self.settings = settings
        self.report = LLMReport(
            enabled=settings.enabled,
            runner=settings.runner,
            scout_model=settings.effective_scout_model,
            critic_model=settings.effective_critic_model,
            scout_temperature=settings.scout_temperature,
            critic_temperature=settings.critic_temperature,
            base_url=settings.base_url,
        )
        self.session = requests.Session()
        key = os.environ.get(settings.api_key_env, "")
        headers = {"Content-Type": "application/json"}
        if key:
            headers["Authorization"] = "Bearer " + key
        self.session.headers.update(headers)

    # ------------------------------------------------------------------
    def probe(self) -> bool:
        """Check the endpoint once, so a dead endpoint costs one request."""
        if not self.settings.enabled:
            return False
        try:
            response = self.session.get(
                self.settings.base_url.rstrip("/") + "/models", timeout=15
            )
            self.report.reachable = response.status_code < 500
        except requests.RequestException as exc:
            self.report.reachable = False
            self.report.failures.append(f"endpoint probe failed: {exc}")
        if not self.report.reachable:
            log.warning("  LLM endpoint %s is not reachable, keeping the rules output",
                        self.settings.base_url)
        return self.report.reachable

    def _chat(self, model: str, system: str, user: str,
              temperature: float) -> dict[str, Any] | None:
        payload = {
            **(self.settings.extra_body or {}),
            "model": model,
            "temperature": temperature,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        try:
            response = self.session.post(
                self.settings.base_url.rstrip("/") + "/chat/completions",
                json=payload, timeout=self.settings.timeout_seconds,
            )
        except requests.RequestException as exc:
            self.report.failures.append(f"{model}: request failed: {exc}")
            return None
        if response.status_code != 200:
            self.report.failures.append(
                f"{model}: HTTP {response.status_code}: {truncate(response.text, 160)}"
            )
            return None
        try:
            content = response.json()["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError) as exc:
            self.report.failures.append(f"{model}: unexpected response shape: {exc}")
            return None
        return _parse_json(content, model, self.report)

    # ------------------------------------------------------------------
    def enrich_candidate(self, candidate: Candidate,
                         evidence: list[Evidence]) -> None:
        """Let the scout model sharpen grounded narratives, in place."""
        valid_ids = {e.id for e in evidence}
        prompt = _scout_prompt(candidate, evidence)
        result = self._chat(self.settings.effective_scout_model, _SCOUT_SYSTEM, prompt,
                            self.settings.scout_temperature)
        if not result:
            return

        changed = False
        for field_name, attribute in (
            ("problem", "problem"),
            ("proposedTechnique", "proposed_technique"),
            ("difference", "difference"),
            ("technicalEffect", "technical_effect"),
        ):
            block = result.get(field_name)
            if not isinstance(block, dict):
                continue
            text = str(block.get("text") or "").strip()
            cited = [i for i in (block.get("evidenceIds") or []) if i in valid_ids]
            if not text:
                continue
            if not cited:
                # A rewrite that cites nothing is exactly what this layer must
                # not publish.
                self.report.claims_discarded += 1
                continue
            if _HAN_RE.search(text):
                self.report.claims_off_language += 1
                continue
            current: Narrative = getattr(candidate, attribute)
            # The model may not upgrade a marker into a confident claim.
            confidence = current.confidence
            setattr(candidate, attribute, Narrative(
                text=text, evidence_ids=cited, confidence=confidence,
            ))
            self.report.claims_accepted += 1
            changed = True

        # The summary is held to the same rule as every other sentence. It used
        # to be taken as a bare string, so it was the one place a model could
        # publish text that cited nothing.
        summary = result.get("summary")
        if isinstance(summary, dict):
            text = str(summary.get("text") or "").strip()
            cited = [i for i in (summary.get("evidenceIds") or []) if i in valid_ids]
        else:
            text, cited = str(summary or "").strip(), []
        if text:
            if cited and _HAN_RE.search(text):
                self.report.claims_off_language += 1
            elif cited:
                candidate.summary = text
                self.report.claims_accepted += 1
                changed = True
            else:
                self.report.claims_discarded += 1

        if changed:
            self.report.candidates_enriched += 1

    # ------------------------------------------------------------------
    def challenge_candidate(self, candidate: Candidate,
                            evidence: list[Evidence]) -> list[CriticFinding]:
        """Let the critic model add objections on top of the rule findings."""
        valid_ids = {e.id for e in evidence}
        prompt = _critic_prompt(candidate, evidence)
        result = self._chat(self.settings.effective_critic_model, _CRITIC_SYSTEM, prompt,
                            self.settings.critic_temperature)
        if not result:
            return []

        findings: list[CriticFinding] = []
        for index, raw in enumerate(result.get("objections") or [], start=1):
            if not isinstance(raw, dict):
                continue
            detail = str(raw.get("detail") or "").strip()
            cited = [i for i in (raw.get("evidenceIds") or []) if i in valid_ids]
            if not detail:
                continue
            if not cited:
                self.report.claims_discarded += 1
                continue
            if _HAN_RE.search(detail):
                self.report.claims_off_language += 1
                continue
            findings.append(CriticFinding(
                rule=f"LLM{index}",
                title=truncate(str(raw.get("title") or "모델이 제기한 반박"), 90),
                passed=False,
                detail=detail,
                evidence_ids=cited,
            ))
        self.report.objections_added += len(findings)
        self.report.claims_accepted += len(findings)
        return findings


# ----------------------------------------------------------------------
def _evidence_block(evidence: list[Evidence], limit: int = 24) -> str:
    lines = []
    for item in evidence[:limit]:
        location = item.path or item.ref or item.commit_sha or item.repo_full_name
        lines.append(
            f"- id={item.id} kind={item.kind} repo={item.repo_full_name} "
            f"location={location} symbol={item.symbol or '-'} "
            f"lines={item.line_start or '-'}..{item.line_end or '-'}\n"
            f"  quote: {truncate(item.snippet, 260)}"
        )
        # The quote is a 260 character excerpt, and a model reading only the
        # excerpt concluded that code it could not see did not exist. The source
        # lines behind the mechanism terms narrow that gap, and removed lines are
        # labelled so that the replaced approach is not read as the current one.
        seen = {item.snippet}
        shown = 0
        for signal in item.signals:
            if not signal.context or signal.context in seen:
                continue
            if signal.kind == "prior":
                lines.append(f"  prior (삭제된 이전 코드): {truncate(signal.context, 160)}")
            elif signal.kind == "mechanism" and shown < 2:
                lines.append(f"  line: {truncate(signal.context, 160)}")
                shown += 1
            else:
                continue
            seen.add(signal.context)
    return "\n".join(lines)


def _scout_prompt(candidate: Candidate, evidence: list[Evidence]) -> str:
    return f"""다음 후보의 서술을 Evidence에 근거하여 다듬어 주십시오.

후보 제목: {candidate.title}
기술 영역: {candidate.topic}
메커니즘 계열: {candidate.mechanism_family}

현재 서술:
- summary: {candidate.summary}
- problem: {candidate.problem.text}
- proposedTechnique: {candidate.proposed_technique.text}
- difference: {candidate.difference.text}
- technicalEffect: {candidate.technical_effect.text}

Evidence 목록:
{_evidence_block(evidence)}

다음 JSON 형식으로만 답하십시오. 근거가 없는 항목은 아예 생략하십시오.
{{
  "summary": {{"text": "한 문장에서 세 문장", "evidenceIds": ["ev_..."]}},
  "problem": {{"text": "...", "evidenceIds": ["ev_..."]}},
  "proposedTechnique": {{"text": "...", "evidenceIds": ["ev_..."]}},
  "difference": {{"text": "...", "evidenceIds": ["ev_..."]}},
  "technicalEffect": {{"text": "...", "evidenceIds": ["ev_..."]}}
}}"""


def _critic_prompt(candidate: Candidate, evidence: list[Evidence]) -> str:
    rule_findings = "\n".join(
        f"- {f.rule} {f.title}: {'통과' if f.passed else '실패'} / {f.detail}"
        for f in candidate.critic_findings
    )
    return f"""다음 후보를 반박해 주십시오.

후보 제목: {candidate.title}
요약: {candidate.summary}
제안 기법: {candidate.proposed_technique.text}
주장하는 효과: {candidate.technical_effect.text}
차별점: {candidate.difference.text}

규칙 기반 심사 결과:
{rule_findings}

Evidence 목록:
{_evidence_block(evidence)}

규칙 기반 심사가 이미 지적한 내용은 반복하지 마십시오. 새로운 반박만 제시하십시오.
반박할 근거가 없으면 빈 배열을 반환하십시오.

다음 JSON 형식으로만 답하십시오.
{{
  "objections": [
    {{"title": "짧은 제목", "detail": "반박 내용", "evidenceIds": ["ev_..."]}}
  ]
}}"""


def _parse_json(content: str, model: str, report: LLMReport) -> dict[str, Any] | None:
    """Parse a model reply that should be JSON but might be wrapped in prose."""
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.DOTALL)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        match = _JSON_BLOCK_RE.search(text)
        if not match:
            report.failures.append(f"{model}: reply was not JSON")
            return None
        try:
            parsed = json.loads(match.group(0))
        except json.JSONDecodeError as exc:
            report.failures.append(f"{model}: JSON parse failed: {exc}")
            return None
    return parsed if isinstance(parsed, dict) else None
