"""Critic.

The critic argues against every candidate the scout produced. It does not try to
be balanced: each rule is an attempt to show that the candidate is ordinary
engineering rather than an invention or a result.

A rejected candidate is never deleted. It is written to the Rejected Ideas store
together with the reason and the condition under which it should be looked at
again, so that the next scan does not rediscover and re-argue the same thing.

Rule findings are written in Korean because they are displayed on the site.
Symbol names, rule ids and technical terms stay in their original form.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Iterable

from .labels import evidence_kind_label
from .lexicon import LEXICON
from .lexicon.terms import CONFIG_PATH_HINTS
from .models import Candidate, CriticFinding, Evidence, Rejection
from .util import log, stable_id

# A change that only moves numbers around, e.g. `kFoo = 4;` becoming `kFoo = 8;`
_CONSTANT_ONLY_RE = re.compile(
    r"^[^=]{0,60}=\s*[-+]?\d+(?:\.\d+)?[fuUlL]?\s*[;,]?\s*$", re.MULTILINE
)
_TUNING_WORDS = re.compile(
    r"\b(threshold|constant|magic number|tweak|tune[d]?|tuning|adjust the value|"
    r"bump the|default value)\b",
    re.IGNORECASE,
)


@dataclass
class Rule:
    id: str
    title: str
    fatal: bool
    recheck: str
    check: Callable[[Candidate, list[Evidence]], tuple[bool, str, list[str]]]


def _text_of(items: Iterable[Evidence]) -> str:
    """Everything a rule may reason about.

    Signal contexts are included because they carry the real source lines around
    each matched term, which a 320 character snippet alone may not.
    """
    parts: list[str] = []
    for item in items:
        parts.append(f"{item.title}\n{item.snippet}")
        parts.extend(signal.context for signal in item.signals if signal.context)
    return "\n".join(parts)


def _kinds_text(items: Iterable[Evidence]) -> str:
    return ", ".join(evidence_kind_label(k) for k in sorted({i.kind for i in items}))


# ----------------------------------------------------------------------
# Individual rules. Each returns (passed, detail, evidence_ids).
# ----------------------------------------------------------------------
def _r1_not_a_trivial_change(candidate: Candidate, items: list[Evidence]):
    trivial = [i for i in items if any(s.kind == "triviality" for s in i.signals)]
    substantive = [i for i in items if i.kind == "code"]
    if trivial and len(trivial) >= max(2, len(items) // 2):
        return (
            False,
            f"증거 {len(items)}건 중 {len(trivial)}건에 사소한 변경 표지(포매팅, 이름 변경, "
            f"버전 올림)가 붙어 있습니다. 이는 유지보수성 정리 작업의 형태입니다.",
            [i.id for i in trivial[:6]],
        )
    if not substantive:
        return False, "증거 중 실제 코드가 하나도 없습니다.", []
    return (
        True,
        f"코드 증거 {len(substantive)}건이 메커니즘을 담고 있으며, 사소한 변경 표지가 "
        f"증거를 지배하지 않습니다.",
        [i.id for i in substantive[:4]],
    )


def _r2_not_configuration_only(candidate: Candidate, items: list[Evidence]):
    non_config = [
        i for i in items
        if i.kind in ("code", "doc")
        and not (i.path and any(hint in i.path.lower() for hint in CONFIG_PATH_HINTS))
    ]
    if not non_config:
        return (
            False,
            "모든 증거가 빌드 파일이나 설정 파일에 있습니다. 따라서 이 후보는 메커니즘이 아니라 "
            "설정값을 서술하고 있습니다.",
            [i.id for i in items[:6]],
        )
    return True, f"증거 {len(non_config)}건이 빌드 및 설정 파일 밖에 있습니다.", []


def _r3_not_a_generic_pattern(candidate: Candidate, items: list[Evidence]):
    text = _text_of(items)
    patterns = LEXICON.known_pattern_hits(text)
    mechanism_terms = {s.term for i in items for s in i.signals if s.kind == "mechanism"}
    if patterns and len(mechanism_terms) <= 1:
        found = ", ".join(sorted(mechanism_terms)) or "없음"
        return (
            False,
            f"메커니즘 어휘가 {found} 하나뿐인데, 일반적인 설계 패턴이 두드러집니다"
            f"({', '.join(patterns[:4])}). 이 조합은 보통 평범한 객체 지향 구조를 뜻합니다.",
            [i.id for i in items[:4]],
        )
    if patterns:
        return (
            True,
            f"일반적인 설계 패턴이 함께 나타나지만({', '.join(patterns[:3])}), 서로 다른 "
            f"메커니즘 어휘 {len(mechanism_terms)}종이 같이 존재합니다.",
            [],
        )
    return True, "증거를 지배하는 일반적인 설계 패턴이 없습니다.", []


def _r4_not_parameter_tuning(candidate: Candidate, items: list[Evidence]):
    code = [i for i in items if i.kind == "code"]
    code_text = _text_of(code)
    constant_only = [i for i in code
                     if _CONSTANT_ONLY_RE.search(_text_of([i]))]
    tuning_talk = [i for i in items if _TUNING_WORDS.search(_text_of([i]))]
    if code and len(constant_only) == len(code) and tuning_talk:
        return (
            False,
            "모든 코드 증거가 상수 대입이고, 주변 서술은 값 조정을 이야기합니다. 이는 "
            "메커니즘이 아니라 parameter tuning입니다.",
            [i.id for i in constant_only[:6]],
        )
    if code and not _CONSTANT_ONLY_RE.search(code_text):
        return True, "코드 증거에 상수 대입 이외의 로직이 존재합니다.", []
    return True, "코드 증거가 상수 대입만으로 구성되어 있지는 않습니다.", []


def _r5_not_platform_standard(candidate: Candidate, items: list[Evidence]):
    text = _text_of(items)
    platform = LEXICON.platform_standard_hits(text)
    mechanism_terms = {s.term for i in items for s in i.signals if s.kind == "mechanism"}
    if len(platform) >= 3 and len(mechanism_terms) <= 1:
        found = ", ".join(sorted(mechanism_terms)) or "없음"
        return (
            False,
            f"증거가 표준 플랫폼 연결 코드에 지배되고 있습니다({', '.join(platform[:5])}). "
            f"메커니즘 어휘는 {found}뿐입니다. 이런 코드는 모든 Camera Stack에 들어 있습니다.",
            [i.id for i in items[:4]],
        )
    return (
        True,
        f"발견된 플랫폼 연결 코드: {', '.join(platform[:3]) or '없음'}. 증거를 지배하지 "
        f"않습니다.",
        [],
    )


def _r6_sufficient_evidence(candidate: Candidate, items: list[Evidence]):
    code = [i for i in items if i.kind == "code"]
    if len(items) < 2:
        return False, f"증거가 {len(items)}건뿐입니다.", [i.id for i in items]
    if not code:
        return False, "코드 증거가 없으므로 메커니즘이 실제로 구현되지 않았을 수 있습니다.", []
    kinds = sorted({i.kind for i in items})
    if len(kinds) < 2:
        return (
            False,
            f"모든 증거가 한 종류({evidence_kind_label(kinds[0])})이므로 서로 교차 확인되지 "
            f"않습니다.",
            [i.id for i in items[:4]],
        )
    return True, f"증거 {len(items)}건이 {len(kinds)}종에 걸쳐 있습니다({_kinds_text(items)}).", []


def _r7_claim_matches_code(candidate: Candidate, items: list[Evidence]):
    """The mechanism the candidate is named after must appear in code, not only prose."""
    code_terms = {
        s.term for i in items if i.kind == "code"
        for s in i.signals if s.kind == "mechanism"
    }
    if not code_terms:
        return (
            False,
            "메커니즘 어휘가 문서, Commit 메시지, Issue에만 나타나고 코드에는 없습니다. "
            "주장과 구현이 일치하지 않습니다.",
            [i.id for i in items if i.kind != "code"][:4],
        )
    return (
        True,
        f"코드 자체가 메커니즘 어휘를 포함합니다({', '.join(sorted(code_terms)[:4])}).",
        [i.id for i in items if i.kind == "code"][:4],
    )


def _r8_effect_is_more_than_wording(candidate: Candidate, items: list[Evidence]):
    """A soft rule: an effect claimed without any figure stays unproven."""
    if candidate.technical_effect.confidence:
        return (
            False,
            "수집된 자료 어디에도 기술적 효과가 정량화되어 있지 않습니다. 따라서 이점은 "
            "입증된 것이 아니라 주장된 상태입니다.",
            candidate.technical_effect.evidence_ids,
        )
    return (
        True,
        "효과가 측정된 수치와 함께 서술되어 있습니다.",
        candidate.technical_effect.evidence_ids,
    )


RULES: list[Rule] = [
    Rule("R1", "사소한 변경이나 유지보수 작업이 아닌가",
         True,
         "해당 Component에 포매팅이나 이름 변경이 아닌 새로운 코드가 들어오면 다시 검토합니다.",
         _r1_not_a_trivial_change),
    Rule("R2", "설정 변경만으로 이루어진 것이 아닌가",
         True,
         "메커니즘이 빌드 파일이 아니라 소스 파일에 나타나면 다시 검토합니다.",
         _r2_not_configuration_only),
    Rule("R3", "흔한 software design pattern이 아닌가",
         False,
         "같은 Component에 서로 구별되는 두 번째 메커니즘 어휘가 나타나면 다시 검토합니다.",
         _r3_not_a_generic_pattern),
    Rule("R4", "parameter tuning 수준이 아닌가",
         True,
         "해당 Component에서 상수가 아니라 제어 로직이 변경되면 다시 검토합니다.",
         _r4_not_parameter_tuning),
    Rule("R5", "기존 Android, Linux, V4L2에서 일반적인 방법이 아닌가",
         False,
         "메커니즘 어휘가 플랫폼 기본 코드 범위를 넘어 늘어나면 다시 검토합니다.",
         _r5_not_platform_standard),
    Rule("R6", "Evidence가 충분한가",
         True,
         "설계 문서나 Pull Request처럼 두 번째 종류의 증거가 나타나면 다시 검토합니다.",
         _r6_sufficient_evidence),
    Rule("R7", "주장과 실제 코드가 일치하는가",
         True,
         "메커니즘 어휘가 소스 파일 안에 나타나면 다시 검토합니다.",
         _r7_claim_matches_code),
    Rule("R8", "효과가 주장에 그치지 않고 측정되었는가",
         False,
         "해당 영역에 대한 benchmark 결과나 수치가 공개되면 다시 검토합니다.",
         _r8_effect_is_more_than_wording),
]


class Critic:
    def __init__(self, rules: list[Rule] | None = None) -> None:
        self.rules = rules or RULES

    def run(self, candidates: list[Candidate], evidence_by_id: dict[str, Evidence]
            ) -> tuple[list[Candidate], list[Rejection]]:
        accepted: list[Candidate] = []
        rejections: list[Rejection] = []
        now = datetime.now(timezone.utc).isoformat()

        for candidate in candidates:
            items = [evidence_by_id[e] for e in candidate.evidence_ids if e in evidence_by_id]
            findings: list[CriticFinding] = []
            failed_fatal: list[Rule] = []
            failed_soft: list[Rule] = []

            for rule in self.rules:
                passed, detail, evidence_ids = rule.check(candidate, items)
                findings.append(CriticFinding(
                    rule=rule.id, title=rule.title, passed=passed,
                    detail=detail, evidence_ids=evidence_ids,
                ))
                if not passed:
                    (failed_fatal if rule.fatal else failed_soft).append(rule)

            candidate.critic_findings = findings

            if failed_fatal:
                candidate.verdict = "rejected"
                candidate.status = "REJECTED"
                detail_by_rule = {f.rule: f.detail for f in findings}
                rejections.append(Rejection(
                    id=stable_id("rej", candidate.signature),
                    slug=candidate.slug,
                    title=candidate.title,
                    signature=candidate.signature,
                    reason=" ".join(detail_by_rule[r.id] for r in failed_fatal),
                    failed_rules=[f"{r.id}: {r.title}" for r in failed_fatal],
                    evidence_ids=candidate.evidence_ids,
                    repositories=candidate.repositories,
                    recheck_conditions=[r.recheck for r in failed_fatal],
                    rejected_at=now,
                    scores={k: v.to_dict() for k, v in candidate.scores.items()},
                ))
                continue

            candidate.verdict = (
                "accepted_with_reservations" if failed_soft else "accepted"
            )
            if failed_soft:
                candidate.status_reason = (
                    "유보 조건부 통과입니다. 미해결 지적: "
                    + "; ".join(f"{r.id} {r.title}" for r in failed_soft)
                )
            accepted.append(candidate)

        log.info("  critic accepted %d candidates and rejected %d",
                 len(accepted), len(rejections))
        return accepted, rejections
