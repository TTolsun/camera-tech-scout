"""Technology graph construction.

The graph is what lets the site answer questions that a flat candidate list
cannot: which repositories work on the same technology, which problems keep
recurring, and which mechanisms are used to attack them.

    Organization -> Repository -> Component -> Technology -> Problem -> Mechanism -> Candidate

Every edge is backed by at least one evidence record, so the graph never asserts
a relationship the repositories do not actually contain.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from .models import Candidate, Evidence
from .util import stable_id

NODE_ORDER = ["organization", "repository", "component", "technology", "problem",
              "mechanism", "candidate"]


@dataclass
class GraphNode:
    id: str
    type: str
    label: str
    weight: float = 0.0
    evidence_ids: list[str] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "label": self.label,
            "weight": round(self.weight, 2),
            "evidenceCount": len(self.evidence_ids),
            "evidenceIds": self.evidence_ids[:40],
            "meta": self.meta,
        }


@dataclass
class GraphEdge:
    source: str
    target: str
    type: str
    weight: float = 0.0
    evidence_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "target": self.target,
            "type": self.type,
            "weight": round(self.weight, 2),
            "evidenceCount": len(self.evidence_ids),
        }


class TechnologyGraph:
    def __init__(self) -> None:
        self.nodes: dict[str, GraphNode] = {}
        self.edges: dict[tuple[str, str, str], GraphEdge] = {}

    def node(self, type_: str, key: str, label: str, **meta: Any) -> GraphNode:
        node_id = stable_id(type_[:3], type_, key)
        existing = self.nodes.get(node_id)
        if existing is None:
            existing = GraphNode(id=node_id, type=type_, label=label, meta=dict(meta))
            self.nodes[node_id] = existing
        elif meta:
            existing.meta.update(meta)
        return existing

    def link(self, source: GraphNode, target: GraphNode, type_: str,
             weight: float = 1.0, evidence_id: str | None = None) -> None:
        key = (source.id, target.id, type_)
        edge = self.edges.get(key)
        if edge is None:
            edge = GraphEdge(source=source.id, target=target.id, type=type_)
            self.edges[key] = edge
        edge.weight += weight
        if evidence_id and evidence_id not in edge.evidence_ids:
            edge.evidence_ids.append(evidence_id)

    def touch(self, node: GraphNode, weight: float, evidence_id: str | None) -> None:
        node.weight += weight
        if evidence_id and evidence_id not in node.evidence_ids:
            node.evidence_ids.append(evidence_id)

    def to_dict(self) -> dict[str, Any]:
        nodes = sorted(
            self.nodes.values(),
            key=lambda n: (NODE_ORDER.index(n.type) if n.type in NODE_ORDER else 99, -n.weight),
        )
        return {
            "nodes": [n.to_dict() for n in nodes],
            "edges": [e.to_dict() for e in self.edges.values()],
            "counts": {t: sum(1 for n in self.nodes.values() if n.type == t) for t in NODE_ORDER},
        }


def component_of(path: str | None) -> str:
    """Derive a component name from a file path.

    ``src/libcamera/pipeline/rpi/vc4/vc4.cpp`` becomes ``libcamera/pipeline``,
    which is specific enough to be useful and stable enough to group on.
    """
    if not path:
        return "(repository level)"
    parts = [p for p in Path(path).parts if p not in (".", "")]
    if len(parts) <= 1:
        return "(root)"
    meaningful = [p for p in parts[:-1] if p.lower() not in ("src", "lib", "include", "source")]
    if not meaningful:
        meaningful = parts[:-1]
    return "/".join(meaningful[:2])


def build_graph(evidence: Iterable[Evidence], candidates: Iterable[Candidate]) -> TechnologyGraph:
    graph = TechnologyGraph()
    by_id = {e.id: e for e in evidence}

    for item in by_id.values():
        org_node = graph.node("organization", item.org, item.org)
        repo_node = graph.node("repository", item.repo_full_name, item.repo_full_name,
                               org=item.org)
        graph.link(org_node, repo_node, "owns", 1.0, item.id)
        graph.touch(org_node, 0.2, item.id)
        graph.touch(repo_node, 0.5, item.id)

        component_node = graph.node(
            "component", f"{item.repo_full_name}:{component_of(item.path)}",
            component_of(item.path), repository=item.repo_full_name,
        )
        graph.link(repo_node, component_node, "contains", 1.0, item.id)
        graph.touch(component_node, 1.0, item.id)

        for signal in item.signals:
            if signal.kind == "domain":
                tech = graph.node("technology", signal.family, signal.family)
                graph.link(component_node, tech, "implements", signal.weight, item.id)
                graph.touch(tech, signal.weight, item.id)
            elif signal.kind == "problem":
                problem = graph.node("problem", signal.family, signal.family)
                graph.link(component_node, problem, "reports", signal.weight, item.id)
                graph.touch(problem, signal.weight, item.id)
            elif signal.kind == "mechanism":
                mechanism = graph.node("mechanism", signal.family, signal.family)
                graph.link(component_node, mechanism, "uses", signal.weight, item.id)
                graph.touch(mechanism, signal.weight, item.id)

    for candidate in candidates:
        cand_node = graph.node("candidate", candidate.id, candidate.title,
                               slug=candidate.slug, type=candidate.type,
                               verdict=candidate.verdict)
        cand_node.weight = float(candidate.scores["patent_potential"].value
                                 if "patent_potential" in candidate.scores else 1.0)
        tech = graph.node("technology", candidate.topic, candidate.topic)
        mechanism = graph.node("mechanism", candidate.mechanism_family,
                               candidate.mechanism_family)
        graph.link(tech, cand_node, "yields", 1.0)
        graph.link(mechanism, cand_node, "realises", 1.0)
        for full_name in candidate.repositories:
            repo_node = graph.node("repository", full_name, full_name)
            graph.link(repo_node, cand_node, "evidences", 1.0)

    return graph


def technology_summary(evidence: Iterable[Evidence]) -> list[dict[str, Any]]:
    """Per-technology roll-up used by the Technology Map page."""
    buckets: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"repositories": set(), "problems": set(), "mechanisms": set(),
                 "evidence": 0, "weight": 0.0}
    )
    for item in evidence:
        families = {s.family for s in item.signals if s.kind == "domain"}
        problems = {s.family for s in item.signals if s.kind == "problem"}
        mechanisms = {s.family for s in item.signals if s.kind == "mechanism"}
        for family in families:
            bucket = buckets[family]
            bucket["repositories"].add(item.repo_full_name)
            bucket["problems"].update(problems)
            bucket["mechanisms"].update(mechanisms)
            bucket["evidence"] += 1
            bucket["weight"] += sum(s.weight for s in item.signals if s.family == family)

    out = []
    for family, bucket in buckets.items():
        out.append({
            "technology": family,
            "repositories": sorted(bucket["repositories"]),
            "problems": sorted(bucket["problems"]),
            "mechanisms": sorted(bucket["mechanisms"]),
            "evidenceCount": bucket["evidence"],
            "weight": round(bucket["weight"], 1),
            "crossRepository": len(bucket["repositories"]) > 1,
        })
    out.sort(key=lambda row: (-row["evidenceCount"], row["technology"]))
    return out
