"""Candidate store.

SQLite holds the state that makes a scan incremental and makes change visible:
where each repository was last scanned, which evidence already existed, and what
each candidate looked like on the previous run. The status of a candidate
(``NEW``, ``STRENGTHENED``, ``WEAKENED`` and so on) is derived here by comparing
the new record with the stored one, never guessed.

FTS5 indexes candidate prose so that the pipeline can answer "have we seen this
idea before" without a full scan.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .models import Candidate, Evidence, Rejection, RepoState, Signal
from .util import log

SCHEMA = """
CREATE TABLE IF NOT EXISTS run (
    id            TEXT PRIMARY KEY,
    started_at    TEXT NOT NULL,
    finished_at   TEXT,
    mode          TEXT,
    repositories  INTEGER DEFAULT 0,
    evidence      INTEGER DEFAULT 0,
    candidates    INTEGER DEFAULT 0,
    rejected      INTEGER DEFAULT 0,
    notes         TEXT
);

CREATE TABLE IF NOT EXISTS repo_state (
    full_name        TEXT PRIMARY KEY,
    org              TEXT,
    name             TEXT,
    url              TEXT,
    default_branch   TEXT,
    head_sha         TEXT,
    last_scanned_sha TEXT,
    last_scan_time   TEXT,
    scan_mode        TEXT,
    status           TEXT,
    status_detail    TEXT,
    files_scanned    INTEGER DEFAULT 0,
    evidence_count   INTEGER DEFAULT 0,
    candidate_count  INTEGER DEFAULT 0,
    description      TEXT,
    stars            INTEGER DEFAULT 0,
    language         TEXT,
    pushed_at        TEXT,
    topics           TEXT
);

CREATE TABLE IF NOT EXISTS evidence (
    id                TEXT PRIMARY KEY,
    repository        TEXT NOT NULL,
    org               TEXT,
    kind              TEXT,
    path              TEXT,
    symbol            TEXT,
    symbol_kind       TEXT,
    line_start        INTEGER,
    line_end          INTEGER,
    commit_sha        TEXT,
    ref               TEXT,
    title             TEXT,
    snippet           TEXT,
    url               TEXT,
    observed_at       TEXT,
    first_seen_commit TEXT,
    first_seen_at     TEXT,
    signals           TEXT,
    first_run_id      TEXT,
    last_run_id       TEXT
);
CREATE INDEX IF NOT EXISTS evidence_repo ON evidence(repository);
CREATE INDEX IF NOT EXISTS evidence_kind ON evidence(kind);

CREATE TABLE IF NOT EXISTS candidate (
    id                TEXT PRIMARY KEY,
    slug              TEXT,
    title             TEXT,
    type              TEXT,
    signature         TEXT,
    topic             TEXT,
    mechanism_family  TEXT,
    verdict           TEXT,
    status            TEXT,
    patent            REAL,
    paper             REAL,
    evidence_strength REAL,
    novelty           REAL,
    evidence_count    INTEGER,
    repositories      TEXT,
    payload           TEXT,
    first_seen_at     TEXT,
    last_updated_at   TEXT,
    first_run_id      TEXT,
    last_run_id       TEXT
);
CREATE INDEX IF NOT EXISTS candidate_signature ON candidate(signature);

CREATE TABLE IF NOT EXISTS candidate_history (
    candidate_id      TEXT NOT NULL,
    run_id            TEXT NOT NULL,
    at                TEXT NOT NULL,
    status            TEXT,
    verdict           TEXT,
    patent            REAL,
    paper             REAL,
    evidence_strength REAL,
    evidence_count    INTEGER,
    PRIMARY KEY (candidate_id, run_id)
);

CREATE TABLE IF NOT EXISTS rejection (
    id           TEXT PRIMARY KEY,
    signature    TEXT,
    title        TEXT,
    rejected_at  TEXT,
    times_seen   INTEGER DEFAULT 1,
    payload      TEXT,
    last_run_id  TEXT
);

CREATE VIRTUAL TABLE IF NOT EXISTS candidate_fts USING fts5(
    candidate_id UNINDEXED,
    title,
    summary,
    body
);
"""


@dataclass
class PreviousCandidate:
    id: str
    status: str
    verdict: str
    patent: float
    paper: float
    evidence_strength: float
    evidence_count: int
    payload: dict[str, Any]
    first_seen_at: str | None


class Store:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path))
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self.db.commit()

    def close(self) -> None:
        self.db.commit()
        self.db.close()

    # ------------------------------------------------------------------
    # Runs
    # ------------------------------------------------------------------
    def start_run(self, run_id: str, mode: str) -> str:
        self.db.execute(
            "INSERT OR REPLACE INTO run (id, started_at, mode) VALUES (?, ?, ?)",
            (run_id, datetime.now(timezone.utc).isoformat(), mode),
        )
        self.db.commit()
        return run_id

    def finish_run(self, run_id: str, *, repositories: int, evidence: int,
                   candidates: int, rejected: int, notes: Any) -> None:
        self.db.execute(
            "UPDATE run SET finished_at = ?, repositories = ?, evidence = ?, candidates = ?, "
            "rejected = ?, notes = ? WHERE id = ?",
            (datetime.now(timezone.utc).isoformat(), repositories, evidence, candidates,
             rejected, json.dumps(notes, ensure_ascii=False), run_id),
        )
        self.db.commit()

    def runs(self, limit: int = 40) -> list[dict[str, Any]]:
        rows = self.db.execute(
            "SELECT * FROM run ORDER BY started_at DESC LIMIT ?", (limit,)
        ).fetchall()
        out = []
        for row in rows:
            record = dict(row)
            try:
                record["notes"] = json.loads(record.get("notes") or "null")
            except json.JSONDecodeError:
                record["notes"] = None
            out.append(record)
        return out

    # ------------------------------------------------------------------
    # Repository state
    # ------------------------------------------------------------------
    def get_repo_state(self, full_name: str) -> RepoState | None:
        row = self.db.execute(
            "SELECT * FROM repo_state WHERE full_name = ?", (full_name,)
        ).fetchone()
        if row is None:
            return None
        data = dict(row)
        topics = data.pop("topics", None)
        state = RepoState(
            full_name=data["full_name"], org=data["org"] or "", name=data["name"] or "",
            url=data["url"] or "", default_branch=data["default_branch"] or "HEAD",
            head_sha=data["head_sha"], last_scanned_sha=data["last_scanned_sha"],
            last_scan_time=data["last_scan_time"], scan_mode=data["scan_mode"] or "full",
            status=data["status"] or "pending", status_detail=data["status_detail"] or "",
            files_scanned=data["files_scanned"] or 0,
            evidence_count=data["evidence_count"] or 0,
            candidate_count=data["candidate_count"] or 0,
            description=data["description"] or "", stars=data["stars"] or 0,
            language=data["language"] or "", pushed_at=data["pushed_at"],
        )
        try:
            state.topics = json.loads(topics) if topics else []
        except json.JSONDecodeError:
            state.topics = []
        return state

    def save_repo_state(self, state: RepoState) -> None:
        self.db.execute(
            """
            INSERT INTO repo_state (
                full_name, org, name, url, default_branch, head_sha, last_scanned_sha,
                last_scan_time, scan_mode, status, status_detail, files_scanned,
                evidence_count, candidate_count, description, stars, language, pushed_at, topics
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(full_name) DO UPDATE SET
                org=excluded.org, name=excluded.name, url=excluded.url,
                default_branch=excluded.default_branch, head_sha=excluded.head_sha,
                last_scanned_sha=excluded.last_scanned_sha,
                last_scan_time=excluded.last_scan_time, scan_mode=excluded.scan_mode,
                status=excluded.status, status_detail=excluded.status_detail,
                files_scanned=excluded.files_scanned, evidence_count=excluded.evidence_count,
                candidate_count=excluded.candidate_count, description=excluded.description,
                stars=excluded.stars, language=excluded.language,
                pushed_at=excluded.pushed_at, topics=excluded.topics
            """,
            (
                state.full_name, state.org, state.name, state.url, state.default_branch,
                state.head_sha, state.last_scanned_sha, state.last_scan_time, state.scan_mode,
                state.status, state.status_detail, state.files_scanned, state.evidence_count,
                state.candidate_count, state.description, state.stars, state.language,
                state.pushed_at, json.dumps(state.topics),
            ),
        )
        self.db.commit()

    def all_repo_states(self) -> list[RepoState]:
        names = [r["full_name"] for r in
                 self.db.execute("SELECT full_name FROM repo_state").fetchall()]
        return [s for s in (self.get_repo_state(n) for n in names) if s is not None]

    # ------------------------------------------------------------------
    # Evidence
    # ------------------------------------------------------------------
    def upsert_evidence(self, items: Iterable[Evidence], run_id: str) -> int:
        count = 0
        for item in items:
            self.db.execute(
                """
                INSERT INTO evidence (
                    id, repository, org, kind, path, symbol, symbol_kind, line_start, line_end,
                    commit_sha, ref, title, snippet, url, observed_at, first_seen_commit,
                    first_seen_at, signals, first_run_id, last_run_id
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET
                    title=excluded.title, snippet=excluded.snippet, url=excluded.url,
                    line_start=excluded.line_start, line_end=excluded.line_end,
                    observed_at=COALESCE(excluded.observed_at, evidence.observed_at),
                    first_seen_commit=COALESCE(evidence.first_seen_commit,
                                               excluded.first_seen_commit),
                    first_seen_at=COALESCE(evidence.first_seen_at, excluded.first_seen_at),
                    signals=excluded.signals, last_run_id=excluded.last_run_id
                """,
                (
                    item.id, item.repo_full_name, item.org, item.kind, item.path, item.symbol,
                    item.symbol_kind, item.line_start, item.line_end, item.commit_sha, item.ref,
                    item.title, item.snippet, item.url, item.observed_at, item.first_seen_commit,
                    item.first_seen_at,
                    json.dumps([s.__dict__ for s in item.signals], ensure_ascii=False),
                    run_id, run_id,
                ),
            )
            count += 1
        self.db.commit()
        return count

    def load_evidence_for_repos(self, repositories: Iterable[str]) -> list[Evidence]:
        names = list(repositories)
        if not names:
            return []
        placeholders = ",".join("?" for _ in names)
        rows = self.db.execute(
            f"SELECT * FROM evidence WHERE repository IN ({placeholders})", names
        ).fetchall()
        return [_row_to_evidence(row) for row in rows]

    def delete_evidence_for_paths(self, repository: str, paths: Iterable[str]) -> int:
        paths = [p for p in paths if p]
        if not paths:
            return 0
        removed = 0
        for path in paths:
            cursor = self.db.execute(
                "DELETE FROM evidence WHERE repository = ? AND path = ? "
                "AND kind IN ('code','doc','config')",
                (repository, path),
            )
            removed += cursor.rowcount if cursor.rowcount and cursor.rowcount > 0 else 0
        self.db.commit()
        return removed

    # ------------------------------------------------------------------
    # Candidates
    # ------------------------------------------------------------------
    def get_previous_candidate(self, candidate_id: str) -> PreviousCandidate | None:
        row = self.db.execute(
            "SELECT * FROM candidate WHERE id = ?", (candidate_id,)
        ).fetchone()
        if row is None:
            return None
        try:
            payload = json.loads(row["payload"] or "{}")
        except json.JSONDecodeError:
            payload = {}
        return PreviousCandidate(
            id=row["id"], status=row["status"] or "", verdict=row["verdict"] or "",
            patent=row["patent"] or 0.0, paper=row["paper"] or 0.0,
            evidence_strength=row["evidence_strength"] or 0.0,
            evidence_count=row["evidence_count"] or 0, payload=payload,
            first_seen_at=row["first_seen_at"],
        )

    def assign_status(self, candidate: Candidate, run_id: str) -> None:
        """Derive the candidate status by comparing with the stored version."""
        previous = self.get_previous_candidate(candidate.id)
        new_count = len(candidate.evidence_ids)
        strength = candidate.scores["evidence_strength"].value

        if previous is None:
            candidate.status = "NEW"
            candidate.status_reason = "이 기술 영역과 메커니즘 조합을 처음 발견했습니다."
        elif candidate.verdict == "rejected":
            candidate.status = "REJECTED"
            candidate.status_reason = "이번 실행에서 Critic이 반박했습니다."
        elif new_count > previous.evidence_count or strength > previous.evidence_strength + 1:
            candidate.status = "STRENGTHENED"
            candidate.status_reason = (
                f"증거가 {previous.evidence_count}건에서 {new_count}건으로 늘었습니다."
            )
        elif new_count < previous.evidence_count or strength < previous.evidence_strength - 1:
            candidate.status = "WEAKENED"
            candidate.status_reason = (
                f"증거가 {previous.evidence_count}건에서 {new_count}건으로 줄었습니다. "
                f"관련 코드가 제거되었을 수 있습니다."
            )
        elif previous.payload.get("summary") != candidate.summary:
            candidate.status = "UPDATED"
            candidate.status_reason = "증거 수는 같지만 서술 내용이 바뀌었습니다."
        else:
            candidate.status = "UNCHANGED"
            candidate.status_reason = "직전 스캔 이후 변화가 없습니다."
            candidate.first_seen_at = previous.first_seen_at or candidate.first_seen_at

        if previous is not None and previous.first_seen_at:
            candidate.first_seen_at = previous.first_seen_at

        # A candidate whose problem and effect are both ungrounded needs a human
        # to look at it before it is treated as a finding.
        if candidate.status in ("NEW", "UNCHANGED") and candidate.problem.confidence \
                and candidate.technical_effect.confidence:
            candidate.status = "NEEDS_VERIFICATION"
            candidate.status_reason = (
                "문제와 효과 어느 쪽도 수집된 자료에 근거를 두고 있지 않습니다. "
                "발견 사항으로 다루기 전에 사람이 확인해야 합니다."
            )

    def upsert_candidate(self, candidate: Candidate, run_id: str) -> None:
        payload = candidate.to_dict()
        scores = candidate.scores
        self.db.execute(
            """
            INSERT INTO candidate (
                id, slug, title, type, signature, topic, mechanism_family, verdict, status,
                patent, paper, evidence_strength, novelty, evidence_count, repositories,
                payload, first_seen_at, last_updated_at, first_run_id, last_run_id
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET
                slug=excluded.slug, title=excluded.title, type=excluded.type,
                verdict=excluded.verdict, status=excluded.status, patent=excluded.patent,
                paper=excluded.paper, evidence_strength=excluded.evidence_strength,
                novelty=excluded.novelty, evidence_count=excluded.evidence_count,
                repositories=excluded.repositories, payload=excluded.payload,
                first_seen_at=COALESCE(candidate.first_seen_at, excluded.first_seen_at),
                last_updated_at=excluded.last_updated_at, last_run_id=excluded.last_run_id
            """,
            (
                candidate.id, candidate.slug, candidate.title, candidate.type,
                candidate.signature, candidate.topic, candidate.mechanism_family,
                candidate.verdict, candidate.status,
                scores["patent_potential"].value, scores["paper_potential"].value,
                scores["evidence_strength"].value, scores["novelty_confidence"].value,
                len(candidate.evidence_ids), json.dumps(candidate.repositories),
                json.dumps(payload, ensure_ascii=False),
                candidate.first_seen_at, candidate.last_updated_at, run_id, run_id,
            ),
        )
        self.db.execute(
            """
            INSERT OR REPLACE INTO candidate_history
                (candidate_id, run_id, at, status, verdict, patent, paper,
                 evidence_strength, evidence_count)
            VALUES (?,?,?,?,?,?,?,?,?)
            """,
            (
                candidate.id, run_id, datetime.now(timezone.utc).isoformat(),
                candidate.status, candidate.verdict,
                scores["patent_potential"].value, scores["paper_potential"].value,
                scores["evidence_strength"].value, len(candidate.evidence_ids),
            ),
        )
        self.db.execute("DELETE FROM candidate_fts WHERE candidate_id = ?", (candidate.id,))
        self.db.execute(
            "INSERT INTO candidate_fts (candidate_id, title, summary, body) VALUES (?,?,?,?)",
            (
                candidate.id, candidate.title, candidate.summary,
                "\n".join([
                    candidate.problem.text, candidate.proposed_technique.text,
                    candidate.difference.text, candidate.technical_effect.text,
                    " ".join(candidate.prior_art_keywords),
                ]),
            ),
        )
        self.db.commit()

    def candidate_history(self, candidate_id: str) -> list[dict[str, Any]]:
        rows = self.db.execute(
            "SELECT * FROM candidate_history WHERE candidate_id = ? ORDER BY at",
            (candidate_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def search_candidates(self, query: str, limit: int = 20) -> list[str]:
        try:
            rows = self.db.execute(
                "SELECT candidate_id FROM candidate_fts WHERE candidate_fts MATCH ? LIMIT ?",
                (query, limit),
            ).fetchall()
        except sqlite3.OperationalError as exc:
            log.debug("FTS query rejected: %s", exc)
            return []
        return [row["candidate_id"] for row in rows]

    # ------------------------------------------------------------------
    # Rejections
    # ------------------------------------------------------------------
    def upsert_rejection(self, rejection: Rejection, run_id: str) -> None:
        self.db.execute(
            """
            INSERT INTO rejection (id, signature, title, rejected_at, times_seen, payload,
                                   last_run_id)
            VALUES (?,?,?,?,1,?,?)
            ON CONFLICT(id) DO UPDATE SET
                times_seen = rejection.times_seen + 1,
                payload = excluded.payload,
                last_run_id = excluded.last_run_id
            """,
            (rejection.id, rejection.signature, rejection.title, rejection.rejected_at,
             json.dumps(rejection.to_dict(), ensure_ascii=False), run_id),
        )
        self.db.commit()

    def all_rejections(self) -> list[dict[str, Any]]:
        rows = self.db.execute(
            "SELECT * FROM rejection ORDER BY rejected_at DESC"
        ).fetchall()
        out = []
        for row in rows:
            try:
                payload = json.loads(row["payload"] or "{}")
            except json.JSONDecodeError:
                payload = {}
            payload["timesSeen"] = row["times_seen"]
            out.append(payload)
        return out

    def known_rejection_signatures(self) -> dict[str, int]:
        rows = self.db.execute("SELECT signature, times_seen FROM rejection").fetchall()
        return {row["signature"]: row["times_seen"] for row in rows}

    # ------------------------------------------------------------------
    def all_candidates(self) -> list[dict[str, Any]]:
        rows = self.db.execute(
            "SELECT payload FROM candidate WHERE verdict != 'rejected' "
            "ORDER BY patent + paper DESC"
        ).fetchall()
        out = []
        for row in rows:
            try:
                out.append(json.loads(row["payload"]))
            except (json.JSONDecodeError, TypeError):
                continue
        return out


def _row_to_evidence(row: sqlite3.Row) -> Evidence:
    try:
        raw_signals = json.loads(row["signals"] or "[]")
    except json.JSONDecodeError:
        raw_signals = []
    signals = [
        Signal(kind=s.get("kind", ""), term=s.get("term", ""), family=s.get("family", ""),
               weight=float(s.get("weight", 0.0)), context=s.get("context", ""))
        for s in raw_signals if isinstance(s, dict)
    ]
    repository = row["repository"] or "/"
    org, _, name = repository.partition("/")
    return Evidence(
        id=row["id"], org=row["org"] or org, repo=name, kind=row["kind"] or "code",
        path=row["path"], symbol=row["symbol"], symbol_kind=row["symbol_kind"],
        line_start=row["line_start"], line_end=row["line_end"],
        commit_sha=row["commit_sha"], ref=row["ref"], title=row["title"] or "",
        snippet=row["snippet"] or "", url=row["url"] or "", observed_at=row["observed_at"],
        first_seen_commit=row["first_seen_commit"], first_seen_at=row["first_seen_at"],
        signals=signals,
    )
