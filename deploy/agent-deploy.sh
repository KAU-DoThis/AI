#!/usr/bin/env bash
# EC2에서 실행되는 AI 서버(agent) 배포 스크립트. GitHub Actions가 SSM Run Command로 실행한다.
# 서버에서 직접 실행할 때:  sudo bash agent-deploy.sh <이미지 태그(커밋 SHA)>
#
# 하는 일
#   1. ECR에서 새 agent 이미지 받기
#   2. /opt/fintris/.env 의 AGENT_IMAGE_TAG 를 새 태그로 바꾸고 agent 컨테이너만 다시 띄우기
#      (BE compose 안의 agent 서비스. main·mysql·caddy는 건드리지 않는다)
#   3. /health 가 응답할 때까지 기다리기 (최대 약 2분)
#   4. 응답이 없으면 이전 태그로 되돌리고 실패 처리
set -euo pipefail

NEW_TAG="${1:?사용법: agent-deploy.sh <이미지 태그>}"
APP_DIR=/opt/fintris
cd "$APP_DIR"

if [[ ! -f .env || ! -f docker-compose.yml ]]; then
  echo "[agent] $APP_DIR 에 .env 또는 docker-compose.yml 이 없습니다. BE 배포가 먼저 한 번 돼 있어야 합니다." >&2
  exit 1
fi

env_value() { grep -E "^$1=" .env | tail -n1 | cut -d= -f2- || true; }
AWS_REGION="$(env_value AWS_REGION)"; AWS_REGION="${AWS_REGION:-ap-northeast-2}"
ECR_REGISTRY="$(env_value ECR_REGISTRY)"
OLD_TAG="$(env_value AGENT_IMAGE_TAG)"
IMAGE="$ECR_REGISTRY/fintris-agent:$NEW_TAG"

# compose 파일 전체를 읽으므로 main 이미지 태그도 필요하다 (BE 배포가 남긴 값을 그대로 쓴다)
IMAGE_TAG="$(cat .current_tag 2>/dev/null || true)"
export IMAGE_TAG
if [[ -n "$(env_value SITE_ADDRESS)" ]]; then export COMPOSE_PROFILES=https; fi
unset AGENT_IMAGE_TAG || true   # 셸 값이 .env 보다 우선하지 않게

echo "[agent] 이전 태그: ${OLD_TAG:-없음} → 새 태그: $NEW_TAG"

if docker image inspect "$IMAGE" > /dev/null 2>&1; then
  echo "[agent] 서버에 이미 있는 이미지라 받지 않습니다"
else
  echo "[agent] ECR 로그인 후 이미지 받기"
  aws ecr get-login-password --region "$AWS_REGION" \
    | docker login --username AWS --password-stdin "$ECR_REGISTRY" > /dev/null
  docker pull -q "$IMAGE"
fi

set_tag() { sed -i "s/^AGENT_IMAGE_TAG=.*/AGENT_IMAGE_TAG=$1/" .env; }
start_agent() { docker compose up -d --no-deps agent; }
agent_healthy() {
  docker compose exec -T agent python -c \
    "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)" \
    > /dev/null 2>&1
}

set_tag "$NEW_TAG"
if ! start_agent; then
  echo "[agent] 컨테이너 교체 실패" >&2
  [[ -n "$OLD_TAG" ]] && { set_tag "$OLD_TAG"; start_agent || true; }
  exit 1
fi

echo "[agent] 서버 응답 확인 중..."
healthy=false
for _ in $(seq 1 24); do
  if agent_healthy; then healthy=true; break; fi
  sleep 5
done

if ! $healthy; then
  echo "[agent] 실패: /health 응답 없음. 최근 로그:" >&2
  docker compose logs --tail 80 agent >&2 || true
  if [[ -n "$OLD_TAG" && "$OLD_TAG" != "$NEW_TAG" ]]; then
    echo "[agent] 이전 버전($OLD_TAG)으로 되돌립니다." >&2
    set_tag "$OLD_TAG"
    start_agent || true
  fi
  exit 1
fi

# 지금 것과 바로 전 것만 남기고 옛 agent 이미지는 지운다 (디스크 아끼기)
docker images "$ECR_REGISTRY/fintris-agent" --format '{{.Tag}}' \
  | grep -vxF -e "$NEW_TAG" -e "${OLD_TAG:-__none__}" \
  | while read -r t; do docker rmi "$ECR_REGISTRY/fintris-agent:$t" > /dev/null 2>&1 || true; done

echo "[agent] 성공: $NEW_TAG"
docker compose ps agent --format '{{.Name}} {{.Status}}'
