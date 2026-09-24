# Camera Tech Scout

지정한 GitHub Organization과 Repository를 주기적으로 탐색하여, Camera Software 분야의 특허 및
논문 후보를 증거 기반으로 발굴하고 정리하는 도구입니다. 결과는 GitHub Pages에서 서비스 가능한
정적 웹사이트로 제공됩니다.

## 가장 먼저 확인할 것

이 도구의 핵심은 순서입니다.

```
Evidence
  → Problem / Mechanism / Effect
      → Candidate
          → Critic
              → Verified Discovery
```

파일을 모델에 넣어 아이디어를 얻는 방식이 아닙니다. 먼저 실제 코드, 문서, Commit, Pull Request,
Issue에서 증거를 확보하고, 그 증거에서 문제와 메커니즘과 효과를 읽어 낸 다음에 후보를 만들고,
마지막으로 그 후보를 반박합니다.

근거가 부족한 항목은 그럴듯한 문장으로 채우지 않습니다. 대신 `UNKNOWN`, `LOW_CONFIDENCE`,
`NEEDS_VERIFICATION` 중 하나를 명시합니다.

## 빠르게 실행해 보기

네트워크와 GitHub API 없이 번들 fixture로 전체 파이프라인을 실행합니다.

```bash
pip install -r pipeline/requirements.txt
PYTHONPATH=pipeline/src python -m scout scan --fixture --dry-run
```

사이트까지 확인하려면 다음을 실행합니다.

```bash
PYTHONPATH=pipeline/src python -m scout scan --fixture
npm --prefix site install
npm --prefix site run dev
```

## 요구 사항

| 항목 | 버전 | 용도 |
|---|---|---|
| Python | 3.11 이상 | 파이프라인 |
| Node.js | 20 이상 | Astro 사이트 |
| git | 2.30 이상 | Repository clone 및 이력 분석 |
| GitHub 토큰 | 선택 | 없으면 시간당 60건으로 제한됩니다 |

토큰은 `GITHUB_TOKEN` 또는 `GH_TOKEN` 환경 변수에서 읽으며, 둘 다 없으면 `gh auth token`을
시도합니다.

## 분석 대상 변경

`config/sources.yaml` 파일 하나만 수정하면 됩니다. 코드를 고칠 필요가 없습니다.

```yaml
sources:
  organizations:
    - url: https://github.com/example-camera
      enabled: true
      match: [libcamera, isp]      # 이름 필터. 생략하면 전체
      max_repositories: 12

  repositories:
    - url: https://github.com/example/special-camera-stack
      enabled: true
    - url: https://github.com/other-org/research-camera
      enabled: false               # 일시적으로 제외
```

Organization에서 발견된 Repository와 직접 지정한 Repository는 하나의 분석 대상으로 합쳐지며,
중복은 자동으로 제거됩니다. 두 경로로 모두 발견된 Repository는 어떤 경로로 편입되었는지 함께
기록되어 Sources 페이지에 표시됩니다.

`defaults` 블록에서 수집 범위와 예산을 조정합니다. 모든 항목은 Organization이나 Repository
단위로 덮어쓸 수 있습니다.

```yaml
defaults:
  include: [src/**, docs/**, "*.md"]
  exclude: [third_party/**, test/data/**]
  analysis:
    code: true
    documents: true
    commits: true
    pull_requests: true
    issues: true
    releases: true
  limits:
    clone_depth: 400
    max_files: 4000
    max_commits: 400
```

## 명령어

```bash
python -m scout check-config                 # 설정 파일만 검증
python -m scout scan                         # 실제 스캔. 상태를 저장하고 data/ 를 갱신
python -m scout scan --dry-run               # DB와 data/ 를 건드리지 않고 전체 실행
python -m scout scan --fixture --dry-run     # 완전 오프라인. CI가 쓰는 조합
python -m scout scan --full                  # 저장된 상태를 무시하고 전체 재분석
python -m scout scan --max-repos 2           # 대상 개수 제한
python -m scout status                       # 저장된 스캔 상태 출력
python -m scout search "동기화"               # 저장된 후보 전문 검색
```

주요 옵션은 다음과 같습니다.

- `--dry-run`: 데이터베이스를 임시 파일로 복사해서 쓰고, JSON은 `data-dryrun/`에 씁니다. 직전
  실행과 비교한 상태 변화는 그대로 계산되므로 결과를 실제와 동일하게 확인할 수 있습니다. 모델
  계층은 기본적으로 호출하지 않습니다.
- `--fixture`: 번들된 fixture Repository를 실제 git 저장소로 만들어 분석합니다. 네트워크가 전혀
  필요 없고 결과가 항상 같으므로, CI에서 rate limit과 무관하게 파이프라인을 검증할 수 있습니다.
- `--fail-on-empty`: 후보가 하나도 나오지 않으면 실패로 처리합니다. CI 점검용입니다.

### Windows에서 실행할 때

경로가 깊으면 clone 단계에서 `WinError 206`이 발생합니다. Windows의 `MAX_PATH` 제한 때문입니다.
캐시 위치를 짧은 경로로 지정하십시오.

```bash
python -m scout scan --cache C:/scout-cache --db C:/scout-cache/candidates.sqlite3
```

## 실행 주기

주 1회 실행합니다.

- **현재 (구현 단계)**: GitHub Actions
  - `.github/workflows/dry-run.yml` — push와 Pull Request에서 fixture dry-run을 수행합니다.
    수동 실행 시 `live` 모드를 선택하면 실제 Repository를 대상으로 dry-run 할 수 있습니다.
  - `.github/workflows/weekly-scan.yml` — 매주 월요일 18:00 UTC에 실제 스캔을 수행하고 결과를
    커밋한 뒤 GitHub Pages에 배포합니다.
- **사내 운영 전환 시**: cronjob
  - `scripts/run-weekly.sh`가 Actions 워크플로와 동일한 작업을 수행합니다. 스케줄러만 바뀌고
    파이프라인과 산출물은 같습니다.

```cron
0 3 * * 1 /opt/camera-tech-scout/scripts/run-weekly.sh >> /var/log/camera-tech-scout.log 2>&1
```

## Scout와 Critic

후보 생성과 검증을 분리합니다.

**Scout**는 가능성이 있는 기술을 적극적으로 발굴합니다. 단순히 특이한 코드를 찾는 것이 아니라,
문제가 있고 그것을 해결하는 비자명한 메커니즘이 있으며 기술적 효과가 있는 형태를 찾습니다.
메커니즘을 구현한 실제 코드가 없으면 후보를 만들지 않습니다.

**Critic**은 그 후보를 반박합니다. 균형을 맞추려 하지 않고, 각 항목이 그 후보가 발명이 아니라
평범한 엔지니어링임을 보이려 시도합니다.

| 규칙 | 검토 내용 | 성격 |
|---|---|---|
| R1 | 사소한 변경이나 유지보수 작업이 아닌가 | 치명적 |
| R2 | 설정 변경만으로 이루어진 것이 아닌가 | 치명적 |
| R3 | 흔한 software design pattern이 아닌가 | 유보 |
| R4 | parameter tuning 수준이 아닌가 | 치명적 |
| R5 | 기존 Android, Linux, V4L2에서 일반적인 방법이 아닌가 | 유보 |
| R6 | Evidence가 충분한가 | 치명적 |
| R7 | 주장과 실제 코드가 일치하는가 | 치명적 |
| R8 | 효과가 주장에 그치지 않고 측정되었는가 | 유보 |

치명적 항목을 하나라도 위반하면 후보는 반박됩니다. **반박된 후보도 삭제하지 않습니다.** 반박
사유와 재검토 조건을 함께 보존하여, 같은 아이디어를 매주 다시 발굴하고 다시 논의하는 일을
막습니다.

## LLM 계층

주간 실행은 Hermes가 수행하며 그 안에서 Qwen 모델을 사용합니다. 모델은 하나이므로 Scout과
Critic은 모델을 나누는 대신 system prompt와 sampling으로 역할을 분리합니다. Critic은
`temperature: 0`으로 고정하여 같은 후보가 매번 같은 방식으로 반박되도록 합니다.

```yaml
engine:
  scout: llm
  critic: llm
  llm:
    enabled: true
    runner: hermes
    base_url: http://localhost:8000/v1   # OpenAI 호환 엔드포인트
    api_key_env: SCOUT_LLM_API_KEY
    model: qwen
    scout_temperature: 0.3
    critic_temperature: 0.0
    max_candidates: 40
```

모델에 대한 제약은 단 하나로 요약됩니다. **모델은 증거를 만들어 낼 수 없습니다.** 모델이
반환한 문장이 실제로 수집된 evidence id를 인용하지 않으면 그 문장은 버립니다. 엔드포인트에
접속할 수 없거나 응답이 JSON이 아니면 규칙 기반 결과를 그대로 유지하고 실패를 기록합니다.

따라서 모델이 없어도 이 도구는 완전하게 동작합니다. 규칙 기반 엔진이 언제나 주 엔진이고, 모델은
이미 증거에 연결된 서술을 다듬고 반박을 추가하는 보조 계층입니다.

## 디렉터리 구조

```
camera-tech-scout/
├── config/sources.yaml          분석 대상과 수집 예산
├── data/                        사이트가 읽는 JSON. 파이프라인이 생성합니다
├── pipeline/
│   ├── requirements.txt
│   └── src/scout/
│       ├── cli.py               명령행 인터페이스
│       ├── pipeline.py          단계 오케스트레이션
│       ├── config.py            설정 로딩과 검증
│       ├── resolver.py          Source Resolver. 중복 제거
│       ├── github.py            GitHub REST 수집기
│       ├── collect.py           clone, fetch, commit 이력 파싱
│       ├── symbols.py           tree-sitter 심볼 추출
│       ├── lexicon/             기술 영역, 문제, 메커니즘, 효과 어휘 사전
│       ├── evidence.py          Evidence 생성. 사실을 만드는 유일한 단계
│       ├── technology.py        Technology Graph
│       ├── scouting.py          Idea Scout
│       ├── critic.py            Critic
│       ├── store.py             SQLite + FTS5. 증분 스캔 상태와 상태 변화
│       ├── emit.py              JSON 산출
│       ├── engine/llm.py        선택적 모델 계층
│       └── fixtures.py          오프라인 fixture
├── site/                        Astro 정적 사이트
├── scripts/run-weekly.sh        cronjob 진입점
├── DESIGN.md                    시각 디자인 기준
└── .github/workflows/
    ├── dry-run.yml
    └── weekly-scan.yml
```

## 증분 스캔

매번 전체를 다시 분석하지 않습니다. Repository별로 다음 상태를 저장합니다.

```json
{
  "repository": "owner/repo",
  "last_scanned_sha": "abc123",
  "last_scan_time": "2026-09-24T18:00:00Z"
}
```

다음 실행에서는 `last_scanned_sha`와 현재 HEAD 사이의 변경을 중심으로 분석합니다. 변경된 파일의
기존 Evidence는 먼저 제거한 뒤 다시 생성하므로 중복이 쌓이지 않습니다. 이전 Commit이 얕은 clone
범위를 벗어나 있으면 자동으로 전체 재분석으로 전환하고, 그 사실을 Repository 상태에 기록합니다.

후보의 상태 변화는 추측하지 않고 저장된 직전 기록과 비교해서 결정합니다.

`NEW` · `STRENGTHENED` · `WEAKENED` · `UPDATED` · `REJECTED` · `NEEDS_VERIFICATION` · `UNCHANGED`

## 어휘 사전 확장

관심 기술 영역을 넓히려면 `pipeline/src/scout/lexicon/terms.py`의 표를 수정합니다. 표는
`계열 -> (가중치, [용어])` 형태이며, `*`로 끝나는 용어는 어간으로 취급합니다.

메커니즘 표는 의도적으로 좁게 유지합니다. Critic이 "메커니즘 어휘가 하나뿐이면 평범한 구조"라는
판단을 이 표의 좁음에 의존해서 내리기 때문입니다. 일반적인 소프트웨어 용어를 메커니즘 표에
추가하면 반박 규칙이 함께 무뎌집니다.

한국어 표시 이름은 `labels.py`에서 관리합니다. 심볼명, 파일 경로, Repository 이름, 정착된 기술
용어는 원어를 유지합니다.

## 사이트

Astro 정적 사이트이며 `data/*.json`만 읽습니다. 데이터베이스에 의존하지 않으므로 JSON만 있으면
어디서든 빌드됩니다.

```bash
npm --prefix site run dev      # 개발 서버
npm --prefix site run build    # site/dist 생성
```

GitHub Pages의 프로젝트 경로에 배포할 때는 `SITE_URL`과 `BASE_PATH` 환경 변수를 사용합니다.
워크플로가 `actions/configure-pages`의 출력으로 자동 설정합니다.

다른 데이터 디렉터리를 보려면 `SCOUT_DATA_DIR`를 지정합니다.

```bash
SCOUT_DATA_DIR=../data-dryrun npm --prefix site run build
```

## 이 도구가 하지 못하는 일

- 선행기술 조사를 대신하지 않습니다. 제공하는 것은 검색어이며 조사 결과가 아닙니다.
- 특허 가능성이나 신규성에 대한 판단을 제공하지 않습니다. `Novelty Confidence`는 추가 조사의
  우선순위를 뜻하는 값이며, 신규성 판단이 아닙니다.
- 어휘 사전에 없는 기술 영역은 찾지 못합니다.
- 얕은 clone 범위를 벗어난 과거 이력은 읽지 못합니다.
- Repository가 문제를 서술하지 않으면 문제 항목은 `UNKNOWN`으로 남습니다. 코드만으로 의도를
  추정하지 않습니다.

## 디자인

시각 디자인은 `DESIGN.md`를 기준으로 합니다. 흰 캔버스, 하나의 잉크 색, 하나의 강조색, 구역을
나누는 회색 밴드, 8px 간격 체계와 80px 섹션 간격, 16px 카드와 4px 버튼이 기본 규칙입니다.

두 가지는 이 프로젝트에 맞게 조정했습니다.

1. 원본 서체가 상용이므로 `DESIGN.md`가 권장하는 Manrope를 사용하고, 본문이 한국어이므로
   `Noto Sans KR`을 함께 사용합니다.
2. 강조색은 링크와 활성 메뉴 표시에만 사용합니다. 평가 막대는 무채색을 쓰므로, 정보 밀도가 높은
   페이지에서도 "강조색은 한 화면에 두 번까지"라는 규칙이 유지됩니다.

디자인 언어만 참고했으며, 특정 기업의 워드마크나 브랜딩은 사용하지 않습니다.
