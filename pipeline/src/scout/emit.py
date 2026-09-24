"""JSON emission.

Everything the site renders comes from this directory. The pipeline writes flat
JSON so that the Astro build has no database dependency and so that a reviewer
can read the raw output without running anything.

One file per page, plus ``evidence.json`` which every page dereferences by id.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .labels import domain_label, evidence_kind_label, mechanism_label, problem_label
from .models import Candidate, Evidence, Rejection, RepoState
from .resolver import ResolveReport
from .technology import TechnologyGraph, technology_summary
from .util import log, write_json

STATUS_LABEL: dict[str, str] = {
    "NEW": "신규",
    "STRENGTHENED": "근거 강화",
    "WEAKENED": "근거 약화",
    "UPDATED": "내용 변경",
    "REJECTED": "반박됨",
    "NEEDS_VERIFICATION": "확인 필요",
    "UNCHANGED": "변화 없음",
}

VERDICT_LABEL: dict[str, str] = {
    "accepted": "통과",
    "accepted_with_reservations": "유보 조건부 통과",
    "rejected": "반박됨",
}

TYPE_LABEL: dict[str, str] = {
    "patent": "Patent 후보",
    "paper": "Paper 후보",
    "patent+paper": "Patent + Paper 후보",
}


@dataclass
class EmitInput:
    run_id: str
    mode: str
    started_at: str
    config_path: Path
    resolve_report: ResolveReport
    repo_states: list[RepoState]
    evidence: list[Evidence]
    candidates: list[Candidate]
    rejections: list[dict[str, Any]]
    graph: TechnologyGraph
    runs: list[dict[str, Any]]
    dropped_clusters: list[dict[str, Any]]
    api_problems: list[dict[str, Any]]
    engine_report: dict[str, Any]
    dry_run: bool = False


def emit_all(target: Path, data: EmitInput) -> list[Path]:
    target.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    evidence_by_id = {e.id: e for e in data.evidence}
    generated_at = datetime.now(timezone.utc).isoformat()

    written.append(write_json(target / "meta.json", _meta(data, generated_at)))
    written.append(write_json(target / "overview.json", _overview(data, generated_at)))
    written.append(write_json(target / "sources.json", _sources(data)))
    written.append(write_json(target / "repositories.json", _repositories(data)))
    written.append(write_json(target / "candidates.json",
                              _candidates(data, evidence_by_id)))
    written.append(write_json(target / "technology-map.json", _technology_map(data)))
    written.append(write_json(target / "recent.json", _recent(data)))
    written.append(write_json(target / "rejected.json", _rejected(data)))
    written.append(write_json(target / "evidence.json", _evidence(data)))
    written.append(write_json(target / "history.json", _history(data)))
    written.append(write_json(target / "search-index.json",
                             _search_index(data, evidence_by_id)))

    log.info("  wrote %d JSON files to %s", len(written), target)
    return written


# ----------------------------------------------------------------------
def _meta(data: EmitInput, generated_at: str) -> dict[str, Any]:
    return {
        "runId": data.run_id,
        "mode": data.mode,
        "startedAt": data.started_at,
        "generatedAt": generated_at,
        "dryRun": data.dry_run,
        "configPath": str(data.config_path.name),
        "engine": data.engine_report,
        "labels": {
            "status": STATUS_LABEL,
            "verdict": VERDICT_LABEL,
            "type": TYPE_LABEL,
            "evidenceKind": {k: evidence_kind_label(k) for k in
                             ("code", "doc", "commit", "pull_request", "issue",
                              "release", "config")},
        },
        "disclaimer": (
            "이 사이트의 모든 후보는 공개 Repository에서 자동으로 수집한 증거에 근거합니다. "
            "Novelty Confidence는 추가 조사의 우선순위를 나타내는 값이며, 특허 신규성 판단이 "
            "아닙니다. UNKNOWN, LOW_CONFIDENCE, NEEDS_VERIFICATION 표시는 해당 항목을 "
            "뒷받침하는 증거가 부족하다는 뜻입니다."
        ),
    }


def _overview(data: EmitInput, generated_at: str) -> dict[str, Any]:
    candidates = data.candidates
    by_status = Counter(c.status for c in candidates)
    by_type = Counter(c.type for c in candidates)
    high_evidence = [c for c in candidates
                     if c.scores["evidence_strength"].value >= 60]
    organizations = sorted({s.org for s in data.repo_states})

    last_scan = max((s.last_scan_time for s in data.repo_states if s.last_scan_time),
                    default=None)

    return {
        "generatedAt": generated_at,
        "dryRun": data.dry_run,
        "organizationCount": len(organizations),
        "organizations": organizations,
        "repositoryCount": len(data.repo_states),
        "lastScanTime": last_scan,
        "evidenceCount": len(data.evidence),
        "candidateCount": len(candidates),
        "counts": {
            "new": by_status.get("NEW", 0),
            "strengthened": by_status.get("STRENGTHENED", 0),
            "weakened": by_status.get("WEAKENED", 0),
            "updated": by_status.get("UPDATED", 0),
            "needsVerification": by_status.get("NEEDS_VERIFICATION", 0),
            "unchanged": by_status.get("UNCHANGED", 0),
            "patent": by_type.get("patent", 0),
            "paper": by_type.get("paper", 0),
            "patentAndPaper": by_type.get("patent+paper", 0),
            "highEvidence": len(high_evidence),
            "rejected": len(data.rejections),
        },
        "evidenceByKind": [
            {"kind": kind, "label": evidence_kind_label(kind), "count": count}
            for kind, count in Counter(e.kind for e in data.evidence).most_common()
        ],
        "topCandidates": [_candidate_card(c) for c in candidates[:8]],
        "recentTechnologyChanges": _technology_changes(data),
        "scanHealth": {
            "repositoriesOk": sum(1 for s in data.repo_states if s.status == "ok"),
            "repositoriesPartial": sum(1 for s in data.repo_states if s.status == "partial"),
            "repositoriesError": sum(1 for s in data.repo_states if s.status == "error"),
            "apiProblemCount": len(data.api_problems),
        },
    }


def _candidate_card(candidate: Candidate) -> dict[str, Any]:
    return {
        "id": candidate.id,
        "slug": candidate.slug,
        "title": candidate.title,
        "type": candidate.type,
        "typeLabel": TYPE_LABEL.get(candidate.type, candidate.type),
        "status": candidate.status,
        "statusLabel": STATUS_LABEL.get(candidate.status, candidate.status),
        "verdict": candidate.verdict,
        "verdictLabel": VERDICT_LABEL.get(candidate.verdict, candidate.verdict),
        "topic": candidate.topic,
        "topicLabel": domain_label(candidate.topic),
        "mechanismFamily": candidate.mechanism_family,
        "mechanismLabel": mechanism_label(candidate.mechanism_family),
        "summary": candidate.summary,
        "repositories": candidate.repositories,
        "evidenceCount": len(candidate.evidence_ids),
        "scores": {k: {"value": round(v.value, 1), "label": v.label}
                   for k, v in candidate.scores.items()},
        "lastUpdatedAt": candidate.last_updated_at,
        "firstSeenAt": candidate.first_seen_at,
    }


def _sources(data: EmitInput) -> dict[str, Any]:
    report = data.resolve_report
    state_by_name = {s.full_name: s for s in data.repo_states}
    return {
        "organizations": [
            {
                **org,
                "selectedDetails": [
                    _source_repo_row(state_by_name.get(name), name)
                    for name in org.get("repositoriesSelected", [])
                ],
            }
            for org in report.organizations
        ],
        "repositories": [
            {
                **repo.to_dict(),
                **_source_repo_row(state_by_name.get(repo.full_name), repo.full_name),
            }
            for repo in report.repositories
        ],
        "skipped": report.skipped,
        "apiProblems": data.api_problems,
    }


def _source_repo_row(state: RepoState | None, full_name: str) -> dict[str, Any]:
    if state is None:
        return {
            "fullName": full_name,
            "status": "pending",
            "statusDetail": "이번 실행에서 분석되지 않았습니다.",
            "lastScannedSha": None,
            "lastScanTime": None,
            "candidateCount": 0,
            "evidenceCount": 0,
            "topics": [],
        }
    return {
        "fullName": state.full_name,
        "status": state.status,
        "statusDetail": state.status_detail,
        "scanMode": state.scan_mode,
        "headSha": state.head_sha,
        "lastScannedSha": state.last_scanned_sha,
        "lastScanTime": state.last_scan_time,
        "filesScanned": state.files_scanned,
        "evidenceCount": state.evidence_count,
        "candidateCount": state.candidate_count,
        "topics": state.topics,
        "topicLabels": [domain_label(t) for t in state.topics],
    }


def _repositories(data: EmitInput) -> dict[str, Any]:
    by_repo_evidence: Counter = Counter(e.repo_full_name for e in data.evidence)
    by_repo_candidates: Counter = Counter()
    for candidate in data.candidates:
        for name in candidate.repositories:
            by_repo_candidates[name] += 1

    rows = []
    for state in sorted(data.repo_states, key=lambda s: s.full_name):
        rows.append({
            **state.to_dict(),
            "topicLabels": [domain_label(t) for t in state.topics],
            "evidenceCount": by_repo_evidence.get(state.full_name, state.evidence_count),
            "candidateCount": by_repo_candidates.get(state.full_name, 0),
            "url": state.url,
        })
    return {"repositories": rows}


def _candidates(data: EmitInput, evidence_by_id: dict[str, Evidence]) -> dict[str, Any]:
    detailed = []
    title_by_id = {c.id: c.title for c in data.candidates}
    for candidate in data.candidates:
        payload = candidate.to_dict()
        payload["typeLabel"] = TYPE_LABEL.get(candidate.type, candidate.type)
        payload["statusLabel"] = STATUS_LABEL.get(candidate.status, candidate.status)
        payload["verdictLabel"] = VERDICT_LABEL.get(candidate.verdict, candidate.verdict)
        payload["topicLabel"] = domain_label(candidate.topic)
        payload["mechanismLabel"] = mechanism_label(candidate.mechanism_family)
        payload["technologyLabels"] = [domain_label(t) for t in candidate.technologies]
        payload["relatedCandidates"] = [
            {"id": rid, "title": title_by_id.get(rid, rid)}
            for rid in candidate.related_candidate_ids if rid in title_by_id
        ]
        payload["evidence"] = [
            _evidence_row(evidence_by_id[eid])
            for eid in candidate.evidence_ids if eid in evidence_by_id
        ]
        payload["counterEvidence"] = [
            f for f in payload["criticFindings"] if not f["passed"]
        ]
        payload["evidenceByKind"] = [
            {"kind": kind, "label": evidence_kind_label(kind), "count": count}
            for kind, count in Counter(
                evidence_by_id[e].kind for e in candidate.evidence_ids
                if e in evidence_by_id
            ).most_common()
        ]
        detailed.append(payload)

    return {
        "candidates": detailed,
        "cards": [_candidate_card(c) for c in data.candidates],
        "droppedClusters": data.dropped_clusters,
    }


def _evidence_row(item: Evidence) -> dict[str, Any]:
    row = item.to_dict()
    row["kindLabel"] = evidence_kind_label(item.kind)
    row["location"] = _location_text(item)
    row["signalSummary"] = _signal_summary(item)
    return row


def _location_text(item: Evidence) -> str:
    parts = [item.repo_full_name]
    if item.path:
        parts.append(item.path)
    if item.symbol:
        parts.append(item.symbol)
    if item.line_start:
        parts.append(f"L{item.line_start}" + (f"-L{item.line_end}"
                                              if item.line_end and
                                              item.line_end != item.line_start else ""))
    if item.commit_sha:
        parts.append(item.commit_sha[:10])
    if item.ref:
        parts.append(item.ref)
    return " / ".join(parts)


def _signal_summary(item: Evidence) -> list[dict[str, Any]]:
    grouped: dict[str, list[str]] = {}
    for signal in item.signals:
        grouped.setdefault(signal.kind, [])
        if signal.term not in grouped[signal.kind]:
            grouped[signal.kind].append(signal.term)
    label = {"domain": "기술 영역", "problem": "문제", "mechanism": "메커니즘",
             "effect": "효과", "evaluation": "평가", "triviality": "사소한 변경"}
    return [
        {"kind": kind, "label": label.get(kind, kind), "terms": terms[:8]}
        for kind, terms in grouped.items()
    ]


def _technology_map(data: EmitInput) -> dict[str, Any]:
    summary = technology_summary(data.evidence)
    candidates_by_topic: dict[str, list[dict[str, Any]]] = {}
    for candidate in data.candidates:
        candidates_by_topic.setdefault(candidate.topic, []).append({
            "id": candidate.id, "slug": candidate.slug, "title": candidate.title,
            "type": candidate.type,
        })
    for row in summary:
        row["technologyLabel"] = domain_label(row["technology"])
        row["problemLabels"] = [problem_label(p) for p in row["problems"]]
        row["mechanismLabels"] = [mechanism_label(m) for m in row["mechanisms"]]
        row["candidates"] = candidates_by_topic.get(row["technology"], [])
    return {
        "technologies": summary,
        "graph": data.graph.to_dict(),
        "crossRepositoryTechnologies": [r for r in summary if r["crossRepository"]],
    }


def _recent(data: EmitInput) -> dict[str, Any]:
    changed = [c for c in data.candidates
               if c.status in ("NEW", "STRENGTHENED", "WEAKENED", "UPDATED")]
    changed.sort(key=lambda c: (c.last_updated_at or "", c.title), reverse=True)

    recent_evidence = sorted(
        (e for e in data.evidence if e.observed_at),
        key=lambda e: e.observed_at or "", reverse=True,
    )[:60]

    return {
        "changedCandidates": [
            {**_candidate_card(c), "statusReason": c.status_reason} for c in changed
        ],
        "recentEvidence": [_evidence_row(e) for e in recent_evidence],
        "technologyChanges": _technology_changes(data),
    }


def _technology_changes(data: EmitInput) -> list[dict[str, Any]]:
    """Which technologies the current run touched, and how strongly."""
    counter: Counter = Counter()
    for item in data.evidence:
        for signal in item.signals:
            if signal.kind == "domain":
                counter[signal.family] += 1
    rows = []
    for family, count in counter.most_common(10):
        related = [c for c in data.candidates if c.topic == family]
        rows.append({
            "technology": family,
            "technologyLabel": domain_label(family),
            "evidenceCount": count,
            "candidateCount": len(related),
            "newCandidates": sum(1 for c in related if c.status == "NEW"),
        })
    return rows


def _rejected(data: EmitInput) -> dict[str, Any]:
    rows = []
    for record in data.rejections:
        rows.append({
            **record,
            "recheckConditions": record.get("recheck_conditions")
            or record.get("recheckConditions") or [],
            "failedRules": record.get("failed_rules") or record.get("failedRules") or [],
        })
    return {
        "rejected": rows,
        "note": ("반박된 후보도 삭제하지 않습니다. 같은 아이디어를 반복해서 발굴하고 다시 "
                 "검토하는 일을 막기 위해, 반박 사유와 재검토 조건을 함께 보존합니다."),
        "droppedClusters": data.dropped_clusters,
        "droppedNote": ("아래 항목은 후보가 되기 위한 최소 조건에 미치지 못해 Critic 이전 "
                        "단계에서 제외된 클러스터입니다."),
    }


def _evidence(data: EmitInput) -> dict[str, Any]:
    return {
        "evidence": [_evidence_row(e) for e in data.evidence],
        "counts": {
            "total": len(data.evidence),
            "byKind": dict(Counter(e.kind for e in data.evidence)),
            "byRepository": dict(Counter(e.repo_full_name for e in data.evidence)),
        },
    }


def _history(data: EmitInput) -> dict[str, Any]:
    return {
        "runs": data.runs,
        "currentRun": {
            "id": data.run_id,
            "mode": data.mode,
            "startedAt": data.started_at,
            "dryRun": data.dry_run,
            "repositories": len(data.repo_states),
            "evidence": len(data.evidence),
            "candidates": len(data.candidates),
            "rejected": len(data.rejections),
            "engine": data.engine_report,
        },
        "repositoryStates": [s.to_dict() for s in data.repo_states],
    }


def _search_index(data: EmitInput, evidence_by_id: dict[str, Evidence]) -> list[dict[str, Any]]:
    """A small client-side search index. Deliberately not a full-text engine."""
    rows: list[dict[str, Any]] = []
    for candidate in data.candidates:
        rows.append({
            "type": "candidate",
            "id": candidate.id,
            "slug": candidate.slug,
            "title": candidate.title,
            "body": " ".join([
                candidate.summary,
                candidate.problem.text,
                candidate.proposed_technique.text,
                candidate.technical_effect.text,
                " ".join(candidate.prior_art_keywords),
                " ".join(candidate.repositories),
                domain_label(candidate.topic),
                mechanism_label(candidate.mechanism_family),
            ]),
            "url": f"/discoveries/{candidate.slug}/",
        })
    for state in data.repo_states:
        rows.append({
            "type": "repository",
            "id": state.full_name,
            "title": state.full_name,
            "body": " ".join([state.description or "",
                              " ".join(domain_label(t) for t in state.topics)]),
            "url": f"/repositories/#{state.full_name.replace('/', '--')}",
        })
    return rows
