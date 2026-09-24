"""Data model shared by every pipeline stage and by the generated JSON.

The dataclasses below are the contract between the Python pipeline and the
Astro site. ``to_dict`` output is written verbatim into ``data/*.json``.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

# Explicit markers used whenever the evidence does not support a statement.
UNKNOWN = "UNKNOWN"
LOW_CONFIDENCE = "LOW_CONFIDENCE"
NEEDS_VERIFICATION = "NEEDS_VERIFICATION"

EvidenceKind = Literal["code", "doc", "commit", "pull_request", "issue", "release", "config"]
SignalKind = Literal["domain", "problem", "mechanism", "effect", "triviality", "evaluation"]
CandidateType = Literal["patent", "paper", "patent+paper"]
CandidateStatus = Literal[
    "NEW", "STRENGTHENED", "WEAKENED", "UPDATED", "REJECTED", "NEEDS_VERIFICATION", "UNCHANGED"
]
Verdict = Literal["accepted", "accepted_with_reservations", "rejected"]


def _clean(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _clean(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_clean(v) for v in value]
    return value


@dataclass
class Signal:
    """One lexicon term observed inside a concrete piece of evidence."""

    kind: str
    term: str
    family: str
    weight: float
    context: str = ""

    def to_dict(self) -> dict[str, Any]:
        return _clean(asdict(self))


@dataclass
class Evidence:
    """A verifiable observation inside a repository.

    Every field the site displays as a fact must originate here.
    """

    id: str
    org: str
    repo: str
    kind: str
    path: str | None = None
    symbol: str | None = None
    symbol_kind: str | None = None
    line_start: int | None = None
    line_end: int | None = None
    commit_sha: str | None = None
    ref: str | None = None
    title: str = ""
    snippet: str = ""
    url: str = ""
    observed_at: str | None = None
    first_seen_commit: str | None = None
    first_seen_at: str | None = None
    signals: list[Signal] = field(default_factory=list)

    @property
    def repo_full_name(self) -> str:
        return self.org + "/" + self.repo

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["repository"] = self.repo_full_name
        return _clean(data)


@dataclass
class ScoreReason:
    """A single auditable contribution to a score."""

    text: str
    delta: float
    evidence_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return _clean(asdict(self))


@dataclass
class Score:
    value: float
    label: str
    reasons: list[ScoreReason] = field(default_factory=list)
    caveat: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "value": round(self.value, 1),
            "label": self.label,
            "caveat": self.caveat,
            "reasons": [r.to_dict() for r in self.reasons],
        }


@dataclass
class CriticFinding:
    rule: str
    title: str
    passed: bool
    detail: str
    evidence_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return _clean(asdict(self))


@dataclass
class Narrative:
    """One prose section of a candidate page.

    Each section carries the evidence ids it was derived from, so the site can
    render a citation next to every claim.
    """

    text: str
    evidence_ids: list[str] = field(default_factory=list)
    confidence: str = ""

    def to_dict(self) -> dict[str, Any]:
        return _clean(asdict(self))


@dataclass
class Candidate:
    id: str
    slug: str
    title: str
    type: str
    signature: str
    topic: str
    mechanism_family: str
    summary: str
    problem: Narrative
    existing_approach: Narrative
    proposed_technique: Narrative
    difference: Narrative
    technical_effect: Narrative
    patent_angle: dict[str, Any]
    paper_angle: dict[str, Any]
    prior_art_keywords: list[str]
    evidence_ids: list[str]
    repositories: list[str]
    related_candidate_ids: list[str] = field(default_factory=list)
    technologies: list[str] = field(default_factory=list)
    scores: dict[str, Score] = field(default_factory=dict)
    critic_findings: list[CriticFinding] = field(default_factory=list)
    verdict: str = "accepted"
    status: str = "NEW"
    status_reason: str = ""
    timeline: list[dict[str, Any]] = field(default_factory=list)
    first_seen_at: str | None = None
    last_updated_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "slug": self.slug,
            "title": self.title,
            "type": self.type,
            "signature": self.signature,
            "topic": self.topic,
            "mechanismFamily": self.mechanism_family,
            "summary": self.summary,
            "problem": self.problem.to_dict(),
            "existingApproach": self.existing_approach.to_dict(),
            "proposedTechnique": self.proposed_technique.to_dict(),
            "difference": self.difference.to_dict(),
            "technicalEffect": self.technical_effect.to_dict(),
            "patentAngle": _clean(self.patent_angle),
            "paperAngle": _clean(self.paper_angle),
            "priorArtKeywords": self.prior_art_keywords,
            "evidenceIds": self.evidence_ids,
            "repositories": self.repositories,
            "relatedCandidateIds": self.related_candidate_ids,
            "technologies": self.technologies,
            "scores": {k: v.to_dict() for k, v in self.scores.items()},
            "criticFindings": [f.to_dict() for f in self.critic_findings],
            "verdict": self.verdict,
            "status": self.status,
            "statusReason": self.status_reason,
            "timeline": _clean(self.timeline),
            "firstSeenAt": self.first_seen_at,
            "lastUpdatedAt": self.last_updated_at,
        }


@dataclass
class Rejection:
    """A candidate the critic rejected.

    Rejections are kept forever so that the same idea is not rediscovered and
    re-reviewed on every scan.
    """

    id: str
    slug: str
    title: str
    signature: str
    reason: str
    failed_rules: list[str]
    evidence_ids: list[str]
    repositories: list[str]
    recheck_conditions: list[str]
    rejected_at: str
    scores: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return _clean(asdict(self))


@dataclass
class RepoState:
    """Incremental scan bookkeeping for one repository."""

    full_name: str
    org: str
    name: str
    url: str
    default_branch: str = "HEAD"
    head_sha: str | None = None
    last_scanned_sha: str | None = None
    last_scan_time: str | None = None
    scan_mode: str = "full"
    status: str = "pending"
    status_detail: str = ""
    files_scanned: int = 0
    evidence_count: int = 0
    candidate_count: int = 0
    topics: list[str] = field(default_factory=list)
    description: str = ""
    stars: int = 0
    language: str = ""
    pushed_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return _clean(asdict(self))
