"""Command line interface.

    scout scan                      live scan, persists state and writes data/
    scout scan --dry-run            same pipeline, throwaway database and output
    scout scan --fixture --dry-run  fully offline, deterministic, used by CI
    scout status                    what the store currently knows
    scout search "<query>"          FTS5 lookup over stored candidates
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import ConfigError, load_config
from .labels import domain_label
from .pipeline import RunOptions, run
from .store import Store
from .util import log, setup_logging

DEFAULT_CONFIG = Path("config/sources.yaml")
DEFAULT_DATA = Path("data")
DEFAULT_CACHE = Path(".cache")
DEFAULT_DB = Path(".cache/candidates.sqlite3")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="scout",
        description="Camera Software 분야의 특허 및 논문 후보를 증거 기반으로 발굴합니다.",
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="디버그 로그를 출력합니다.")
    sub = parser.add_subparsers(dest="command", required=True)

    scan = sub.add_parser("scan", help="분석 대상을 스캔하고 후보를 생성합니다.")
    scan.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    scan.add_argument("--data", type=Path, default=DEFAULT_DATA,
                      help="JSON 산출 디렉터리입니다.")
    scan.add_argument("--cache", type=Path, default=DEFAULT_CACHE,
                      help="clone과 fixture를 보관하는 디렉터리입니다.")
    scan.add_argument("--db", type=Path, default=DEFAULT_DB)
    scan.add_argument("--dry-run", action="store_true",
                      help="DB와 data/를 건드리지 않고 파이프라인 전체를 실행합니다.")
    scan.add_argument("--fixture", action="store_true",
                      help="GitHub API와 clone 없이 번들 fixture로 실행합니다.")
    scan.add_argument("--rebuild-fixtures", action="store_true",
                      help="fixture 저장소를 다시 생성합니다.")
    scan.add_argument("--max-repos", type=int, default=None,
                      help="분석할 Repository 개수를 제한합니다.")
    scan.add_argument("--full", action="store_true",
                      help="저장된 상태를 무시하고 전체를 다시 분석합니다.")
    llm_group = scan.add_mutually_exclusive_group()
    llm_group.add_argument("--llm", dest="llm", action="store_true", default=None,
                           help="설정과 무관하게 LLM 계층을 실행합니다.")
    llm_group.add_argument("--no-llm", dest="llm", action="store_false",
                           help="LLM 계층을 건너뜁니다. dry-run의 기본값입니다.")
    scan.add_argument("--fail-on-empty", action="store_true",
                      help="후보가 하나도 생성되지 않으면 실패로 처리합니다. CI 점검용입니다.")

    status = sub.add_parser("status", help="저장된 스캔 상태를 출력합니다.")
    status.add_argument("--db", type=Path, default=DEFAULT_DB)

    search = sub.add_parser("search", help="저장된 후보를 전문 검색합니다.")
    search.add_argument("query")
    search.add_argument("--db", type=Path, default=DEFAULT_DB)
    search.add_argument("--limit", type=int, default=20)

    check = sub.add_parser("check-config", help="설정 파일만 검증합니다.")
    check.add_argument("--config", type=Path, default=DEFAULT_CONFIG)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    setup_logging(args.verbose)

    try:
        if args.command == "scan":
            return _cmd_scan(args)
        if args.command == "status":
            return _cmd_status(args)
        if args.command == "search":
            return _cmd_search(args)
        if args.command == "check-config":
            return _cmd_check_config(args)
    except ConfigError as exc:
        log.error("설정 오류: %s", exc)
        return 2
    except KeyboardInterrupt:
        log.error("사용자가 중단했습니다.")
        return 130
    return 1


# ----------------------------------------------------------------------
def _cmd_scan(args: argparse.Namespace) -> int:
    options = RunOptions(
        config_path=args.config,
        data_dir=args.data,
        cache_dir=args.cache,
        db_path=args.db,
        dry_run=args.dry_run,
        fixture=args.fixture,
        use_llm=args.llm,
        max_repos=args.max_repos,
        full=args.full,
        rebuild_fixtures=args.rebuild_fixtures,
    )
    result = run(options)
    _print_summary(result)

    if args.fail_on_empty and result.candidate_count == 0:
        log.error("후보가 생성되지 않았습니다. --fail-on-empty 조건에 따라 실패로 처리합니다.")
        return 1
    return 0


def _print_summary(result) -> None:
    write = sys.stdout.write
    write("\n")
    write("=" * 72 + "\n")
    title = "DRY RUN 요약" if result.dry_run else "실행 요약"
    write(f"{title}  ({result.mode})\n")
    write("=" * 72 + "\n")
    write(f"  Run ID            : {result.run_id}\n")
    write(f"  분석 Repository   : {len(result.repositories)}개\n")
    write(f"  수집 Evidence     : {result.evidence_count}건\n")
    write(f"  생성 후보         : {result.candidate_count}건\n")
    write(f"  반박된 후보       : {result.rejected_count}건\n")
    write(f"  JSON 산출 위치    : {result.data_dir}\n")

    engine = result.engine_report or {}
    if engine.get("skipped"):
        write(f"  LLM 계층          : 건너뜀 ({engine.get('skipReason', '')})\n")
    elif engine.get("enabled"):
        write(f"  LLM 계층          : {engine.get('runner', '')} / "
              f"{engine.get('scoutModel', '')} "
              f"(서술 보강 {engine.get('candidatesEnriched', 0)}건, "
              f"반박 추가 {engine.get('objectionsAdded', 0)}건, "
              f"근거 없어 폐기 {engine.get('claimsDiscarded', 0)}건)\n")

    if result.warnings:
        write("\n  경고\n")
        for warning in result.warnings:
            write(f"    - {warning}\n")

    if result.repositories:
        write("\n  Repository 별 상태\n")
        for state in result.repositories:
            write(f"    {state.status:8s} {state.scan_mode:12s} "
                  f"{state.full_name:42s} "
                  f"files={state.files_scanned:<5d} evidence={state.evidence_count:<5d} "
                  f"topics={','.join(state.topics[:3]) or '-'}\n")
            if state.status_detail:
                write(f"             └ {state.status_detail}\n")

    if result.candidates:
        write("\n  상위 후보\n")
        for candidate in result.candidates[:10]:
            scores = candidate.scores
            write(f"    [{candidate.status:18s}] {candidate.verdict:26s} "
                  f"patent={scores['patent_potential'].value:5.1f} "
                  f"paper={scores['paper_potential'].value:5.1f} "
                  f"evidence={scores['evidence_strength'].value:5.1f} "
                  f"novelty={scores['novelty_confidence'].value:5.1f}\n")
            write(f"        {candidate.title}\n")
            write(f"        증거 {len(candidate.evidence_ids)}건 / "
                  f"{', '.join(candidate.repositories)}\n")
    write("=" * 72 + "\n")


def _cmd_status(args: argparse.Namespace) -> int:
    if not args.db.exists():
        log.error("데이터베이스가 없습니다: %s", args.db)
        return 2
    store = Store(args.db)
    try:
        states = store.all_repo_states()
        runs = store.runs(limit=10)
        candidates = store.all_candidates()
        rejections = store.all_rejections()

        print(f"데이터베이스      : {args.db}")
        print(f"분석 Repository   : {len(states)}개")
        print(f"저장된 후보       : {len(candidates)}건")
        print(f"반박 기록         : {len(rejections)}건")
        print()
        print("Repository 상태")
        for state in sorted(states, key=lambda s: s.full_name):
            topics = ", ".join(domain_label(t) for t in state.topics[:3]) or "-"
            print(f"  {state.full_name:44s} {state.status:8s} "
                  f"sha={(state.last_scanned_sha or '-')[:10]:10s} "
                  f"scanned={state.last_scan_time or '-'}")
            print(f"      기술 영역: {topics}")
        print()
        print("최근 실행")
        for record in runs:
            print(f"  {record['id']}  mode={record.get('mode', '-'):8s} "
                  f"repos={record.get('repositories', 0):<4d} "
                  f"evidence={record.get('evidence', 0):<6d} "
                  f"candidates={record.get('candidates', 0):<4d} "
                  f"rejected={record.get('rejected', 0)}")
        return 0
    finally:
        store.close()


def _cmd_search(args: argparse.Namespace) -> int:
    if not args.db.exists():
        log.error("데이터베이스가 없습니다: %s", args.db)
        return 2
    store = Store(args.db)
    try:
        ids = store.search_candidates(args.query, limit=args.limit)
        if not ids:
            print("일치하는 후보가 없습니다.")
            return 0
        by_id = {c["id"]: c for c in store.all_candidates()}
        for candidate_id in ids:
            candidate = by_id.get(candidate_id)
            if candidate is None:
                continue
            print(f"- {candidate['title']}")
            print(f"  {candidate['summary']}")
            print(f"  patent={candidate['scores']['patent_potential']['value']} "
                  f"paper={candidate['scores']['paper_potential']['value']} "
                  f"evidence={len(candidate['evidenceIds'])}건")
        return 0
    finally:
        store.close()


def _cmd_check_config(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    print(f"설정 파일         : {config.path}")
    print(f"Organization      : {len(config.enabled_organizations())}개 활성")
    for org in config.enabled_organizations():
        print(f"  - {org.org} (match={org.match or '전체'}, "
              f"max={org.max_repositories})")
    print(f"Repository        : {len(config.enabled_repositories())}개 활성")
    for repo in config.enabled_repositories():
        print(f"  - {repo.full_name}")
    disabled = [r.full_name for r in config.repositories if not r.enabled]
    if disabled:
        print(f"비활성 Repository : {', '.join(disabled)}")
    print(f"Engine            : scout={config.engine.scout}, critic={config.engine.critic}, "
          f"llm={'on' if config.engine.llm_enabled else 'off'}")
    print(f"Threshold         : min_evidence_score={config.thresholds.min_evidence_score}, "
          f"min_signal_kinds={config.thresholds.min_signal_kinds}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
