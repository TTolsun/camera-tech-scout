"""Idea Scout.

The scout groups evidence into candidates and describes each one. It is
deliberately generous: its job is to surface anything that shows the shape

    problem -> non-trivial mechanism -> technical effect -> reusable idea

and to let the critic argue against it afterwards.

Two rules constrain everything here:

1. A candidate may only exist if real code implements the mechanism. Evidence
   drawn purely from documents or commit messages can support a candidate but
   cannot create one.
2. Every narrative sentence either cites evidence or carries one of ``UNKNOWN``,
   ``LOW_CONFIDENCE`` and ``NEEDS_VERIFICATION``. Text the pipeline generated
   rather than observed is flagged as generated.

Generated prose is Korean, following the project's writing rules. Quoted
evidence, symbol names, paths and established technical terms stay in their
original form on purpose: translating them would break the link back to the
repositories.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any, Iterable

from .config import Thresholds
from .labels import (
    ALGORITHMIC_MECHANISMS,
    HIGH_SPECIFICITY_MECHANISMS,
    display_term,
    domain_label,
    domain_metrics,
    evidence_kind_label,
    mechanism_label,
    problem_label,
    score_label,
    variation_ideas,
)
from .lexicon import LEXICON
from .models import (
    LOW_CONFIDENCE,
    NEEDS_VERIFICATION,
    UNKNOWN,
    Candidate,
    Evidence,
    Narrative,
    Score,
    ScoreReason,
)
from .technology import component_of
from .util import (
    clamp,
    dedupe_keep_order,
    log,
    looks_like_comment,
    slugify,
    stable_id,
    truncate,
)

# Phrases an author uses when describing what the code used to do. These give us
# the "existing approach" without inventing it.
_PRIOR_APPROACH_RE = re.compile(
    r"(?:previously|used to|instead of|rather than|replaces?|replaced|before this|"
    r"historically|originally|was implemented|old (?:code|approach|behaviour|behavior)|"
    r"in the past|until now|currently we|we used)",
    re.IGNORECASE,
)

_MAX_SUPPORTING_PROBLEM_EVIDENCE = 8


@dataclass
class Cluster:
    """Evidence that shares one topic and one mechanism family."""

    topic: str
    mechanism_family: str
    core: list[Evidence] = field(default_factory=list)       # has mechanism + domain
    support: list[Evidence] = field(default_factory=list)    # problem or context only

    @property
    def all_evidence(self) -> list[Evidence]:
        return self.core + self.support

    @property
    def signature(self) -> str:
        return f"{self.topic}|{self.mechanism_family}"


def _primary_family(item: Evidence, kind: str) -> str | None:
    best = None
    best_weight = -1.0
    for signal in item.signals:
        if signal.kind == kind and signal.weight > best_weight:
            best, best_weight = signal.family, signal.weight
    return best


def _families(items: Iterable[Evidence], kind: str) -> Counter:
    counter: Counter = Counter()
    for item in items:
        for signal in item.signals:
            if signal.kind == kind:
                counter[signal.family] += 1
    return counter


def _terms(items: Iterable[Evidence], kind: str, family: str | None = None) -> Counter:
    counter: Counter = Counter()
    for item in items:
        for signal in item.signals:
            if signal.kind != kind:
                continue
            if family and signal.family != family:
                continue
            counter[signal.term] += 1
    return counter


def _evidence_text(items: Iterable[Evidence]) -> str:
    return "\n".join(f"{i.title}\n{i.snippet}" for i in items)


def _join_kinds(items: Iterable[Evidence]) -> str:
    return ", ".join(evidence_kind_label(k) for k in sorted({i.kind for i in items}))


def cluster_evidence(evidence: list[Evidence]) -> list[Cluster]:
    """Group evidence by (topic, mechanism family), then attach problem evidence."""
    clusters: dict[tuple[str, str], Cluster] = {}
    by_topic_leftovers: dict[str, list[Evidence]] = defaultdict(list)

    for item in evidence:
        topic = _primary_family(item, "domain")
        if topic is None:
            continue
        mechanism = _primary_family(item, "mechanism")
        if mechanism is None:
            by_topic_leftovers[topic].append(item)
            continue
        key = (topic, mechanism)
        cluster = clusters.get(key)
        if cluster is None:
            cluster = Cluster(topic=topic, mechanism_family=mechanism)
            clusters[key] = cluster
        cluster.core.append(item)

    # Attach the strongest problem statements of the same topic as support, so a
    # candidate can explain the problem even when the implementing symbol does
    # not mention it.
    for cluster in clusters.values():
        pool = by_topic_leftovers.get(cluster.topic, [])
        ranked = sorted(
            (p for p in pool if any(s.kind == "problem" for s in p.signals)),
            key=lambda p: -sum(s.weight for s in p.signals),
        )
        cluster.support = ranked[:_MAX_SUPPORTING_PROBLEM_EVIDENCE]

    return list(clusters.values())


class Scout:
    def __init__(self, thresholds: Thresholds) -> None:
        self.thresholds = thresholds

    # ------------------------------------------------------------------
    def run(self, evidence: list[Evidence]) -> tuple[list[Candidate], list[dict[str, Any]]]:
        clusters = cluster_evidence(evidence)
        signature_counts = Counter(c.signature for c in clusters)
        total_clusters = max(1, len(clusters))

        candidates: list[Candidate] = []
        dropped: list[dict[str, Any]] = []

        for cluster in clusters:
            reason = self._reject_early(cluster)
            if reason:
                dropped.append({
                    "signature": cluster.signature,
                    "reason": reason,
                    "evidenceCount": len(cluster.all_evidence),
                })
                continue
            candidates.append(
                self._build_candidate(cluster, signature_counts, total_clusters)
            )

        candidates.sort(
            key=lambda c: -(c.scores["patent_potential"].value + c.scores["paper_potential"].value)
        )
        log.info("  scout produced %d candidates, dropped %d weak clusters",
                 len(candidates), len(dropped))
        return candidates, dropped

    # ------------------------------------------------------------------
    def _reject_early(self, cluster: Cluster) -> str | None:
        """Drop clusters that cannot become a usable candidate.

        This runs before the critic so that the Rejected page stays readable: it
        records arguments about real candidates, not statistical noise.
        """
        items = cluster.all_evidence
        if not any(i.kind == "code" for i in cluster.core):
            return "이 메커니즘을 구현한 코드가 없고, 문장으로만 언급되어 있습니다."
        kinds = {s.kind for i in items for s in i.signals}
        if len(kinds) < self.thresholds.min_signal_kinds:
            return (f"신호 종류가 {len(kinds)}종뿐이며, "
                    f"{self.thresholds.min_signal_kinds}종 이상이 필요합니다.")
        score = sum(s.weight for i in items for s in i.signals) / 10.0
        if score < self.thresholds.min_evidence_score:
            return (f"증거 점수가 {score:.1f}이며, 기준값 "
                    f"{self.thresholds.min_evidence_score}에 미치지 못합니다.")
        return None

    # ------------------------------------------------------------------
    def _build_candidate(self, cluster: Cluster, signature_counts: Counter,
                         total_clusters: int) -> Candidate:
        items = cluster.all_evidence
        repositories = dedupe_keep_order([i.repo_full_name for i in items])
        components = dedupe_keep_order(
            [f"{i.repo_full_name}:{component_of(i.path)}" for i in cluster.core]
        )
        mechanism_terms = _terms(cluster.core, "mechanism", cluster.mechanism_family)
        problem_families = _families(items, "problem")
        effect_terms = _terms(items, "effect")
        evaluation_terms = _terms(items, "evaluation")
        triviality_terms = _terms(items, "triviality")
        corpus_text = _evidence_text(items)
        measured = LEXICON.has_measured_effect(corpus_text)

        top_mechanism_term = mechanism_terms.most_common(1)[0][0] if mechanism_terms else ""
        title = self._title(cluster, top_mechanism_term, problem_families)
        candidate_id = stable_id("cand", cluster.topic, cluster.mechanism_family)

        problem = self._problem_narrative(cluster, problem_families)
        existing = self._existing_narrative(items)
        proposed = self._proposed_narrative(cluster, mechanism_terms)
        difference = self._difference_narrative(cluster, existing, mechanism_terms)
        effect = self._effect_narrative(items, effect_terms, measured)

        scores = {
            "evidence_strength": self._score_evidence(items, repositories),
            "patent_potential": self._score_patent(
                cluster, items, problem_families, effect_terms, measured,
                components, repositories, triviality_terms,
            ),
            "paper_potential": self._score_paper(
                cluster, items, evaluation_terms, measured, problem_families
            ),
            "novelty_confidence": self._score_novelty(
                cluster, corpus_text, signature_counts, total_clusters, triviality_terms
            ),
        }

        candidate_type = self._candidate_type(scores)
        observed_dates = sorted(i.observed_at for i in items if i.observed_at)

        return Candidate(
            id=candidate_id,
            slug=slugify(f"{cluster.topic}-{cluster.mechanism_family}"),
            title=title,
            type=candidate_type,
            signature=cluster.signature,
            topic=cluster.topic,
            mechanism_family=cluster.mechanism_family,
            summary=self._summary(cluster, top_mechanism_term, problem_families, measured),
            problem=problem,
            existing_approach=existing,
            proposed_technique=proposed,
            difference=difference,
            technical_effect=effect,
            patent_angle=self._patent_angle(cluster, mechanism_terms, problem_families,
                                            components, repositories, measured),
            paper_angle=self._paper_angle(cluster, existing, measured, evaluation_terms),
            prior_art_keywords=self._prior_art_keywords(cluster, mechanism_terms,
                                                        problem_families),
            evidence_ids=[i.id for i in items],
            repositories=repositories,
            technologies=sorted(_families(items, "domain")),
            scores=scores,
            timeline=self._timeline(items),
            first_seen_at=observed_dates[0] if observed_dates else None,
            last_updated_at=observed_dates[-1] if observed_dates else None,
        )

    # ------------------------------------------------------------------
    # Narratives
    # ------------------------------------------------------------------
    def _title(self, cluster: Cluster, top_term: str, problems: Counter) -> str:
        """Title shaped as ``<domain>: <problem> 대응을 위한 <mechanism>``.

        The colon form avoids Korean particle agreement entirely, which keeps the
        generated headline grammatical without a morphological analyser.
        """
        mechanism = display_term(top_term) if top_term else mechanism_label(
            cluster.mechanism_family)
        domain = domain_label(cluster.topic)
        if problems:
            problem = problem_label(problems.most_common(1)[0][0])
            return f"{domain}: {problem} 대응을 위한 {mechanism}"
        return f"{domain}: {mechanism}"

    def _summary(self, cluster: Cluster, top_term: str, problems: Counter,
                 measured: str | None) -> str:
        mechanism = display_term(top_term) if top_term else mechanism_label(
            cluster.mechanism_family)
        sentences = [
            f"{domain_label(cluster.topic)} 영역의 코드가 {mechanism} 기법을 구현하고 있습니다."
        ]
        if problems:
            problem = problem_label(problems.most_common(1)[0][0])
            sentences.append(
                f"그리고 해당 Repository는 {problem} 문제를 스스로 보고하고 있습니다."
            )
        if measured:
            sentences.append(
                f"주변 서술에는 {measured}라는 측정값이 함께 기재되어 있습니다."
            )
        return " ".join(sentences)

    @staticmethod
    def _problem_quote_rank(item: Evidence) -> tuple:
        """Order problem evidence so the quote shown is a real problem statement.

        Signal weight alone is not enough on a large corpus: a vendored header
        whose comments happen to contain several problem words outranks the issue
        that actually describes the failure. Three corrections are applied, in
        order of importance:

        1. The quoted snippet must itself contain one of the matched problem
           terms. A record whose problem words sit elsewhere in the symbol quotes
           badly.
        2. People state problems in issues, commit messages and pull requests.
           Those kinds are preferred over code and reference documentation.
        3. Only then does the accumulated problem weight decide.
        """
        problem_terms = [s.term for s in item.signals if s.kind == "problem"]
        snippet = (item.snippet or "").lower()
        quotable = any(term in snippet for term in problem_terms)

        kind_rank = {
            "issue": 0, "commit": 0, "pull_request": 0,
            "doc": 1, "release": 1,
            "code": 2, "config": 3,
        }.get(item.kind, 3)

        weight = sum(s.weight for s in item.signals if s.kind == "problem")
        return (0 if quotable else 1, kind_rank, -weight)

    def _problem_narrative(self, cluster: Cluster, problems: Counter) -> Narrative:
        sources = sorted(
            (i for i in cluster.all_evidence
             if any(s.kind == "problem" for s in i.signals)),
            key=self._problem_quote_rank,
        )
        if not sources:
            return Narrative(
                text=("수집된 자료에서 문제 서술을 찾지 못했습니다. 메커니즘 자체는 존재하지만, "
                      "그것이 어떤 실패를 해결하려는 것인지 Repository가 밝히지 않습니다."),
                evidence_ids=[],
                confidence=UNKNOWN,
            )
        families = ", ".join(problem_label(f) for f, _ in problems.most_common(3))
        quoted = truncate(sources[0].snippet, 240)
        return Narrative(
            text=(f"수집된 자료는 {families} 문제를 보고하고 있습니다. "
                  f"그 내용이 기재된 위치 중 하나는 다음과 같습니다: “{quoted}”"),
            evidence_ids=[i.id for i in sources[:6]],
        )

    def _existing_narrative(self, items: list[Evidence]) -> Narrative:
        """Describe what the code did before, using the strongest source available.

        Removed code outranks prose. A commit that deleted a mechanism shows the
        previous approach directly, whereas a sentence in a commit message only
        claims it, and most authors write no such sentence at all.
        """
        from_diff = self._existing_from_diff(items)
        if from_diff is not None:
            return from_diff

        for item in items:
            if item.kind not in ("commit", "pull_request", "issue", "doc"):
                continue
            if not _PRIOR_APPROACH_RE.search(item.snippet or ""):
                continue
            return Narrative(
                text=(f"변경 이력이 이전 동작을 직접 서술하고 있습니다: "
                      f"“{truncate(item.snippet, 260)}”"),
                evidence_ids=[item.id],
            )
        return Narrative(
            text=("이 메커니즘이 도입되기 전에 무엇을 했는지 수집된 자료가 서술하지 않습니다. "
                  "기준선을 확정하려면 전체 Commit 이력을 읽거나 작성자에게 확인해야 합니다."),
            evidence_ids=[],
            confidence=NEEDS_VERIFICATION,
        )

    @staticmethod
    def _existing_from_diff(items: list[Evidence]) -> Narrative | None:
        """Build the baseline from mechanism vocabulary a commit deleted."""
        sources = [i for i in items if any(s.kind == "prior" for s in i.signals)]
        if not sources:
            return None

        # Heaviest first: the commit that removed the most mechanism vocabulary
        # is the one that most likely replaced an approach.
        sources.sort(key=lambda i: -sum(s.weight for s in i.signals if s.kind == "prior"))
        best = sources[0]
        removed = [s for s in best.signals if s.kind == "prior"]
        listed = ", ".join(
            dedupe_keep_order([display_term(s.term) for s in removed])[:3]
        )
        quote = next((s.context for s in removed if s.context), "")

        # A term found only in a deleted comment is weaker than one found in
        # deleted code. The commit may have reworded documentation while leaving
        # the approach in place, so the section says so instead of asserting it.
        from_comment = bool(quote) and looks_like_comment(quote)

        if from_comment:
            text = (f"변경 이력에서 {listed} 기법을 언급하던 주석이 삭제되었고, 추가된 코드에는 "
                    f"다시 나타나지 않습니다. 다만 해당 어휘가 삭제된 주석에서만 확인되므로, "
                    f"실제로 기법이 대체되었는지는 확인이 필요합니다. 삭제된 줄은 다음과 "
                    f"같습니다: “{truncate(quote, 200)}”")
            return Narrative(
                text=text,
                evidence_ids=[i.id for i in sources[:4]],
                confidence=LOW_CONFIDENCE,
            )

        text = (f"변경 이력에서 {listed} 기법이 제거되었고, 추가된 코드에는 다시 나타나지 "
                f"않습니다. 따라서 이것이 대체된 이전 방식입니다.")
        if quote:
            text += f" 제거된 코드는 다음과 같습니다: “{truncate(quote, 200)}”"
        return Narrative(text=text, evidence_ids=[i.id for i in sources[:4]])

    def _proposed_narrative(self, cluster: Cluster, mechanism_terms: Counter) -> Narrative:
        code = [i for i in cluster.core if i.kind == "code"]
        symbols = dedupe_keep_order([f"{i.symbol} ({i.path})" for i in code if i.symbol])[:6]
        listed = ", ".join(display_term(t) for t, _ in mechanism_terms.most_common(4))
        text = f"구현은 {domain_label(cluster.topic)} 내부에서 {listed} 기법을 결합하고 있습니다."
        if symbols:
            text += " 해당 구현은 다음 위치에서 확인됩니다: " + "; ".join(symbols) + "."
        return Narrative(text=text, evidence_ids=[i.id for i in code[:10]])

    def _difference_narrative(self, cluster: Cluster, existing: Narrative,
                              mechanism_terms: Counter) -> Narrative:
        mechanism = ", ".join(display_term(t) for t, _ in mechanism_terms.most_common(2))
        if existing.confidence:
            return Narrative(
                text=(f"Repository만으로는 차별점을 확정할 수 없습니다. 관측 가능한 사실은 현재 "
                      f"코드가 {mechanism} 기법을 사용한다는 점이며, 그것이 무엇을 대체했는지는 "
                      f"수집된 자료에 기록되어 있지 않습니다."),
                evidence_ids=existing.evidence_ids,
                confidence=LOW_CONFIDENCE,
            )
        return Narrative(
            text=(f"이전 동작이 변경 이력에 서술되어 있고, 현재 코드는 그 대신 {mechanism} 기법을 "
                  f"사용합니다. 이 대조가 본 후보의 차별점 근거입니다."),
            evidence_ids=existing.evidence_ids,
        )

    def _effect_narrative(self, items: list[Evidence], effect_terms: Counter,
                          measured: str | None) -> Narrative:
        sources = [i for i in items if any(s.kind == "effect" for s in i.signals)]
        if measured and sources:
            # The figure is reported as "a number appears alongside the effect",
            # not as "the improvement equals this number". Which side of the
            # comparison the number belongs to is only decidable by reading the
            # quote, so the quote is shown and no claim is made about it.
            return Narrative(
                text=(f"효과를 서술하는 문장에 수치가 함께 등장합니다. 수집된 자료에서 처음 "
                      f"확인되는 수치는 {measured}이며, 그것이 개선 후의 값인지 개선 전의 "
                      f"값인지는 아래 인용문에서 직접 확인해야 합니다: "
                      f"“{truncate(sources[0].snippet, 240)}”"),
                evidence_ids=[i.id for i in sources[:4]],
            )
        if sources:
            listed = ", ".join(t for t, _ in effect_terms.most_common(3))
            return Narrative(
                text=(f"효과가 문장으로 주장되어 있습니다({listed}). 다만 수집된 자료에는 이를 "
                      f"뒷받침하는 측정값이 함께 기재되어 있지 않습니다."),
                evidence_ids=[i.id for i in sources[:4]],
                confidence=LOW_CONFIDENCE,
            )
        return Narrative(
            text=("수집된 자료에는 기술적 효과가 서술되어 있지 않습니다. 본 후보를 주장하려면 "
                  "효과를 먼저 측정해야 합니다."),
            evidence_ids=[],
            confidence=UNKNOWN,
        )

    # ------------------------------------------------------------------
    # Angles
    # ------------------------------------------------------------------
    def _patent_angle(self, cluster: Cluster, mechanism_terms: Counter, problems: Counter,
                      components: list[str], repositories: list[str],
                      measured: str | None) -> dict[str, Any]:
        mechanism = ", ".join(display_term(t) for t, _ in mechanism_terms.most_common(3))
        domain = domain_label(cluster.topic)
        problem = problem_label(problems.most_common(1)[0][0]) if problems else UNKNOWN
        claim_elements = [
            f"{domain} 내부의 런타임 수치를 관측하는 단계",
            f"{mechanism} 기법을 사용하여 제어 결정을 도출하는 단계",
            "다음 프레임이 생성되기 전에 그 결정을 Camera Pipeline에 적용하는 단계",
        ]
        if measured:
            claim_elements.append(
                f"그 결과로 발생하는 편차를 한정하는 단계 (수집된 자료에 등장하는 수치는 "
                f"{measured}이며, 이 값이 목표치인지 개선 전의 값인지는 확인이 필요합니다)"
            )
        else:
            claim_elements.append(
                f"Pipeline에 대한 측정 가능한 효과 ({NEEDS_VERIFICATION}: 수치를 찾지 못했습니다)"
            )
        return {
            "coreConcept": (f"{domain} 내부에서 {problem} 문제에 대응하기 위해 {mechanism} 기법을 "
                            f"사용합니다."),
            "claimElements": claim_elements,
            "variations": {
                "generated": True,
                "note": ("아래 항목은 검토자를 위해 생성한 제안입니다. 발견 사항이 아니며, 이를 "
                         "뒷받침하는 증거도 없습니다."),
                "ideas": variation_ideas(cluster.mechanism_family),
            },
            "observedScope": {
                "repositories": repositories,
                "components": components[:10],
                "generalisation": NEEDS_VERIFICATION,
                "note": ("적용 범위는 스캔이 실제로 관측한 범위입니다. 이 아이디어가 해당 "
                         "Component를 넘어 일반화되는지는 확인되지 않았습니다."),
            },
        }

    def _paper_angle(self, cluster: Cluster, existing: Narrative, measured: str | None,
                     evaluation_terms: Counter) -> dict[str, Any]:
        domain = domain_label(cluster.topic)
        mechanism = mechanism_label(cluster.mechanism_family)
        metrics_measured = bool(measured)
        metrics = ([measured] if measured else []) + domain_metrics(cluster.topic)
        ablation = [
            f"{display_term(term)} 요소만 비활성화하고 나머지 메커니즘은 유지합니다."
            for term, _ in _terms(cluster.core, "mechanism").most_common(3)
        ]
        return {
            "researchQuestion": (f"{domain}에서 {mechanism} 기법은 그것이 대체한 구성보다 "
                                 f"보고된 실패 양상을 더 많이 줄이는가?"),
            "hypothesis": (f"{mechanism} 기법을 적용하면, 고정 정책이 한정하지 못하는 편차를 "
                           f"한정할 수 있다."),
            "baseline": (existing.text if not existing.confidence
                         else f"{NEEDS_VERIFICATION}: 대체된 정책이 수집된 자료에 기록되어 "
                              f"있지 않으므로, 기준선으로 사용하려면 먼저 복원해야 합니다."),
            "baselineConfidence": existing.confidence or "",
            "metrics": metrics[:5],
            "metricsConfidence": "" if metrics_measured else LOW_CONFIDENCE,
            "experiments": [
                f"{domain}에서 동일한 캡처 워크로드를 메커니즘 활성 상태와 비활성 상태로 각각 "
                f"실행합니다.",
                "동작점(프레임 레이트, Sensor mode, 카메라 개수)을 변화시키면서 단일 평균이 아니라 "
                "지표의 분포를 기록합니다.",
                "Repository가 보고한 실패를 주입하여 메커니즘이 실제로 반응하는지 확인합니다.",
            ],
            "ablation": ablation or [f"{UNKNOWN}: 분리 가능한 하위 메커니즘을 식별하지 못했습니다."],
            "reproducibility": (
                "구현이 공개되어 있으므로 메커니즘 자체는 재구성할 수 있습니다. 다만 측정을 "
                f"재현하는 데 필요한 캡처 하드웨어와 튜닝 데이터는 {NEEDS_VERIFICATION} "
                "상태입니다."
            ),
            "evaluationSignalsFound": sorted(evaluation_terms),
            "additionalExperimentsNeeded": [
                ("Repository에 존재하지 않는, 정량화된 기준선 측정이 필요합니다."
                 if not measured else "기재된 수치에 대한 독립적인 재측정이 필요합니다."),
                "메커니즘과 플랫폼 고유 특성을 분리하기 위해 두 번째 하드웨어 플랫폼이 "
                "필요합니다.",
            ],
        }

    def _prior_art_keywords(self, cluster: Cluster, mechanism_terms: Counter,
                            problems: Counter) -> list[str]:
        """Prior-art search strings.

        Search keywords stay in English because patent and paper databases are
        indexed in English.
        """
        domain_words = cluster.topic.replace("-", " ")
        mechanism_words = [t for t, _ in mechanism_terms.most_common(3)]
        problem_words = [f.replace("-", " ") for f, _ in problems.most_common(2)]
        keywords: list[str] = []
        for mechanism in mechanism_words:
            keywords.append(f"{domain_words} {mechanism}")
            keywords.append(f"camera {mechanism}")
            for problem in problem_words:
                keywords.append(f"{mechanism} {problem} camera")
        keywords.append(f"image sensor {domain_words} control")
        return dedupe_keep_order(keywords)[:10]

    def _timeline(self, items: list[Evidence]) -> list[dict[str, Any]]:
        rows = [
            {
                "at": i.observed_at,
                "kind": i.kind,
                "kindLabel": evidence_kind_label(i.kind),
                "title": i.title,
                "evidenceId": i.id,
                "url": i.url,
            }
            for i in items if i.observed_at
        ]
        rows.sort(key=lambda r: r["at"] or "")
        return rows[:40]

    # ------------------------------------------------------------------
    # Scores
    # ------------------------------------------------------------------
    def _score_evidence(self, items: list[Evidence], repositories: list[str]) -> Score:
        reasons: list[ScoreReason] = []
        kinds = sorted({i.kind for i in items})
        value = 0.0

        delta = 14.0 * len(kinds)
        value += delta
        reasons.append(ScoreReason(
            text=f"증거가 서로 다른 {len(kinds)}종의 출처에서 확보되었습니다({_join_kinds(items)}).",
            delta=delta, evidence_ids=[i.id for i in items[:4]],
        ))

        delta = min(20.0, 2.0 * len(items))
        value += delta
        reasons.append(ScoreReason(text=f"증거 레코드가 총 {len(items)}건입니다.", delta=delta))

        located = [i for i in items if i.kind == "code" and i.symbol and i.line_start]
        if located:
            value += 10
            reasons.append(ScoreReason(
                text=f"{len(located)}건은 심볼명과 라인 범위를 함께 지목합니다.",
                delta=10.0, evidence_ids=[i.id for i in located[:4]],
            ))

        if len(repositories) > 1:
            value += 10
            reasons.append(ScoreReason(
                text=f"동일한 기술이 {len(repositories)}개 Repository에 나타납니다.",
                delta=10.0,
            ))

        return Score(value=clamp(value), label=score_label("evidence_strength"), reasons=reasons)

    def _score_patent(self, cluster: Cluster, items: list[Evidence], problems: Counter,
                      effects: Counter, measured: str | None, components: list[str],
                      repositories: list[str], triviality: Counter) -> Score:
        reasons: list[ScoreReason] = []
        value = 0.0

        mechanism_weight = max(
            (s.weight for i in cluster.core for s in i.signals if s.kind == "mechanism"),
            default=0.0,
        )
        delta = min(24.0, mechanism_weight * 6.0)
        value += delta
        reasons.append(ScoreReason(
            text=f"메커니즘 계열 `{cluster.mechanism_family}`의 사전 가중치가 "
                 f"{mechanism_weight:.1f}입니다.",
            delta=delta,
        ))

        if problems:
            value += 12
            reasons.append(ScoreReason(
                text="Repository가 해결하려는 문제를 직접 서술하고 있습니다.", delta=12.0,
            ))
        if effects:
            value += 12
            reasons.append(ScoreReason(text="기술적 효과가 주장되어 있습니다.", delta=12.0))
        if measured:
            value += 10
            reasons.append(ScoreReason(
                text=f"효과가 정량화되어 있습니다({measured}).", delta=10.0,
            ))
        if len(components) > 1 or len(repositories) > 1:
            value += 10
            reasons.append(ScoreReason(
                text=f"메커니즘이 {len(components)}개 Component, {len(repositories)}개 "
                     f"Repository에 걸쳐 나타나므로 일반화 가능성이 있습니다.",
                delta=10.0,
            ))
        code_count = sum(1 for i in items if i.kind == "code")
        if code_count >= 3:
            value += 8
            reasons.append(ScoreReason(
                text=f"서로 다른 {code_count}개의 코드 위치가 이를 구현합니다.", delta=8.0,
            ))
        if triviality:
            delta = -15.0
            value += delta
            reasons.append(ScoreReason(
                text=f"주변 텍스트에 사소한 변경 표지가 존재합니다({', '.join(sorted(triviality))}).",
                delta=delta,
            ))
        if not any(i.kind == "code" for i in items):
            value -= 20
            reasons.append(ScoreReason(text="코드 증거가 전혀 없습니다.", delta=-20.0))

        return Score(value=clamp(value), label=score_label("patent_potential"), reasons=reasons)

    def _score_paper(self, cluster: Cluster, items: list[Evidence], evaluation: Counter,
                     measured: str | None, problems: Counter) -> Score:
        reasons: list[ScoreReason] = []
        value = 0.0

        if evaluation:
            value += 18
            reasons.append(ScoreReason(
                text=f"평가 관련 어휘가 존재합니다({', '.join(sorted(evaluation))}).",
                delta=18.0,
            ))
        if measured:
            value += 12
            reasons.append(ScoreReason(
                text=f"측정값이 존재하므로({measured}) 기준선 비교가 부분적으로 가능합니다.",
                delta=12.0,
            ))
        if cluster.mechanism_family in ALGORITHMIC_MECHANISMS:
            value += 12
            reasons.append(ScoreReason(
                text=f"`{cluster.mechanism_family}`은 알고리즘 계열이므로 기준선과 비교할 수 "
                     f"있습니다.",
                delta=12.0,
            ))
        test_paths = [i for i in items
                      if i.path and re.search(r"(test|bench|eval)", i.path, re.IGNORECASE)]
        if test_paths:
            value += 10
            reasons.append(ScoreReason(
                text=f"증거 {len(test_paths)}건이 test 또는 benchmark 경로에 있습니다.",
                delta=10.0, evidence_ids=[i.id for i in test_paths[:4]],
            ))
        docs = [i for i in items if i.kind == "doc"]
        if docs:
            value += 10
            reasons.append(ScoreReason(
                text=f"설계 문서 {len(docs)}건이 해당 영역을 서술합니다.",
                delta=10.0, evidence_ids=[i.id for i in docs[:4]],
            ))
        if problems:
            value += 8
            reasons.append(ScoreReason(
                text="연구 질문에 대응하는 문제 서술이 존재합니다.", delta=8.0))
        if not measured and not evaluation:
            value -= 10
            reasons.append(ScoreReason(
                text="측정값도 평가 구성도 찾지 못했습니다.", delta=-10.0,
            ))

        return Score(value=clamp(value), label=score_label("paper_potential"), reasons=reasons)

    def _score_novelty(self, cluster: Cluster, corpus_text: str,
                       signature_counts: Counter, total_clusters: int,
                       triviality: Counter) -> Score:
        reasons: list[ScoreReason] = []
        value = 50.0
        reasons.append(ScoreReason(text="모든 후보는 50점에서 시작합니다.", delta=50.0))

        share = signature_counts[cluster.signature] / total_clusters
        delta = round(20.0 * (1.0 - share), 1)
        value += delta
        reasons.append(ScoreReason(
            text=f"이 topic과 메커니즘 조합은 본 코퍼스에서 찾은 클러스터의 "
                 f"{share * 100:.1f}%를 차지하므로, 상대적으로 특이한 조합입니다.",
            delta=delta,
        ))

        if cluster.mechanism_family in HIGH_SPECIFICITY_MECHANISMS:
            value += 10
            reasons.append(ScoreReason(
                text=f"`{cluster.mechanism_family}`은 일반적인 연결 코드가 아니라 설계된 기법을 "
                     f"시사합니다.",
                delta=10.0,
            ))

        patterns = LEXICON.known_pattern_hits(corpus_text)
        if patterns:
            value -= 15
            reasons.append(ScoreReason(
                text=f"주변에 일반적인 설계 패턴이 나타납니다({', '.join(patterns[:4])}). "
                     f"이는 추가 조사 우선순위를 낮춥니다.",
                delta=-15.0,
            ))
        platform = LEXICON.platform_standard_hits(corpus_text)
        if len(platform) >= 2:
            value -= 20
            reasons.append(ScoreReason(
                text=f"표준 플랫폼 연결 코드가 증거를 지배합니다({', '.join(platform[:4])}).",
                delta=-20.0,
            ))
        if triviality:
            value -= 10
            reasons.append(ScoreReason(text="사소한 변경 표지가 존재합니다.", delta=-10.0))

        return Score(
            value=clamp(value),
            label=score_label("novelty_confidence"),
            reasons=reasons,
            caveat=("이 수치는 추가 조사의 우선순위를 나타냅니다. 특허 신규성 판단이나 특허 "
                    "가능성에 대한 의견이 아닙니다. 선행기술 조사는 별도로 수행해야 합니다."),
        )

    def _candidate_type(self, scores: dict[str, Score]) -> str:
        patent = scores["patent_potential"].value
        paper = scores["paper_potential"].value
        if patent >= 45 and paper >= 45:
            return "patent+paper"
        return "paper" if paper > patent else "patent"


def link_related(candidates: list[Candidate]) -> None:
    """Connect candidates that share a topic or a mechanism family."""
    by_topic: dict[str, list[Candidate]] = defaultdict(list)
    by_mechanism: dict[str, list[Candidate]] = defaultdict(list)
    for candidate in candidates:
        by_topic[candidate.topic].append(candidate)
        by_mechanism[candidate.mechanism_family].append(candidate)

    for candidate in candidates:
        related: list[str] = []
        for sibling in by_topic[candidate.topic] + by_mechanism[candidate.mechanism_family]:
            if sibling.id != candidate.id and sibling.id not in related:
                related.append(sibling.id)
        candidate.related_candidate_ids = related[:8]
