#!/usr/bin/env bash
#
# Weekly run for an on-premise cronjob.
#
# This is the same work the GitHub Actions weekly workflow performs, so moving
# from Actions to a cronjob changes the scheduler and nothing else.
#
# Install (runs every Monday at 03:00 local time):
#
#   0 3 * * 1 /opt/camera-tech-scout/scripts/run-weekly.sh >> /var/log/camera-tech-scout.log 2>&1
#
# Environment:
#   GITHUB_TOKEN        required for the GitHub API. Read-only scope is enough.
#   SCOUT_LLM_API_KEY   optional, only when the Hermes endpoint requires a key.
#   SCOUT_DRY_RUN       set to 1 to run without persisting anything.
#   SCOUT_NO_LLM        set to 1 to skip the model layer.
#   SCOUT_PYTHON        python interpreter to use, default `python3`.
#   SCOUT_CACHE_DIR     clone cache, default `<repo>/.cache`.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

PYTHON="${SCOUT_PYTHON:-python3}"
CACHE_DIR="${SCOUT_CACHE_DIR:-$REPO_ROOT/.cache}"
VENV_DIR="$REPO_ROOT/.venv"
LOG_PREFIX="[camera-tech-scout $(date -u +%Y-%m-%dT%H:%M:%SZ)]"

log() { echo "$LOG_PREFIX $*"; }

# --- 1. interpreter -----------------------------------------------------------
if [ ! -d "$VENV_DIR" ]; then
  log "가상 환경을 생성합니다: $VENV_DIR"
  "$PYTHON" -m venv "$VENV_DIR"
fi
# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"

log "의존성을 확인합니다."
pip install --quiet --upgrade pip
pip install --quiet -r pipeline/requirements.txt

# --- 2. preconditions ---------------------------------------------------------
if [ -z "${GITHUB_TOKEN:-}" ] && ! command -v gh >/dev/null 2>&1; then
  log "경고: GITHUB_TOKEN이 없고 gh CLI도 없습니다. 시간당 60건으로 제한됩니다."
fi

export PYTHONPATH="$REPO_ROOT/pipeline/src"
export PYTHONIOENCODING=utf-8

ARGS=(scan --cache "$CACHE_DIR")
[ "${SCOUT_DRY_RUN:-0}" = "1" ] && ARGS+=(--dry-run)
[ "${SCOUT_NO_LLM:-0}" = "1" ] && ARGS+=(--no-llm)

# --- 3. configuration check ---------------------------------------------------
log "설정 파일을 검증합니다."
"$VENV_DIR/bin/python" -m scout check-config --config config/sources.yaml

# --- 4. scan ------------------------------------------------------------------
log "스캔을 시작합니다: scout ${ARGS[*]}"
"$VENV_DIR/bin/python" -m scout "${ARGS[@]}"

# --- 5. site ------------------------------------------------------------------
if [ "${SCOUT_DRY_RUN:-0}" = "1" ]; then
  log "dry run이므로 사이트 빌드를 건너뜁니다."
  exit 0
fi

if command -v npm >/dev/null 2>&1; then
  log "사이트를 빌드합니다."
  npm --prefix site ci --silent
  npm --prefix site run build --silent
  log "빌드 결과: $REPO_ROOT/site/dist"
else
  log "npm을 찾지 못해 사이트 빌드를 건너뜁니다. JSON은 $REPO_ROOT/data 에 있습니다."
fi

# --- 6. publish ---------------------------------------------------------------
# An internal deployment usually serves `site/dist` from a web server directly.
# Set SCOUT_PUBLISH_DIR to copy the build there.
if [ -n "${SCOUT_PUBLISH_DIR:-}" ] && [ -d site/dist ]; then
  log "빌드 결과를 배포 위치로 복사합니다: $SCOUT_PUBLISH_DIR"
  mkdir -p "$SCOUT_PUBLISH_DIR"
  rm -rf "${SCOUT_PUBLISH_DIR:?}"/*
  cp -r site/dist/* "$SCOUT_PUBLISH_DIR/"
fi

log "완료했습니다."
