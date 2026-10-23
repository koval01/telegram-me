#!/usr/bin/env bash
# Deploy one image on this server with automatic rollback.
#
#   remote-deploy.sh <image-ref>
#
# Expects compose.prod.yaml and .env in $APP_DIR (default ~/telegram-me).
# The new image must become healthy within $WAIT_TIMEOUT seconds, otherwise
# the previously deployed image (or the 1.x `docker run` container this stack
# replaces) is started again and the script exits non-zero.
# SKIP_PULL=1 uses a local image (handy for testing the script).
set -Eeuo pipefail

image="${1:?usage: remote-deploy.sh <image-ref>}"
app_dir="${APP_DIR:-$HOME/telegram-me}"
wait_timeout="${WAIT_TIMEOUT:-90}"
legacy_name="telegram-me"  # container name used by 1.x deployments
state_file=".deployed-image"

cd "$app_dir"
# APP_IMAGE defaults to the image being deployed; callers override it per call.
compose() { APP_IMAGE="${APP_IMAGE:-$image}" docker compose -f compose.prod.yaml "$@"; }
log() { printf '[deploy %s] %s\n' "$(hostname)" "$*"; }

previous="$(cat "$state_file" 2>/dev/null || true)"
legacy_image=""

if [[ "${SKIP_PULL:-0}" != 1 ]]; then
  log "pulling $image"
  docker pull --quiet "$image"
fi

if docker container inspect "$legacy_name" >/dev/null 2>&1; then
  legacy_image="$(docker container inspect -f '{{.Config.Image}}' "$legacy_name")"
  log "removing 1.x container $legacy_name ($legacy_image)"
  docker rm -f "$legacy_name" >/dev/null
fi

rollback() {
  if [[ -n "$previous" ]]; then
    log "rolling back to $previous"
    APP_IMAGE="$previous" compose up -d --wait --wait-timeout "$wait_timeout"
  elif [[ -n "$legacy_image" ]]; then
    log "restoring 1.x container from $legacy_image"
    compose down --remove-orphans
    docker run -d --name "$legacy_name" --restart unless-stopped \
      -p "${APP_PORT:-8000}:3000" --env-file "${LEGACY_ENV_FILE:-$HOME/env.app}" \
      "$legacy_image" >/dev/null
  else
    log "nothing to roll back to"
  fi
}

log "starting $image"
if ! APP_IMAGE="$image" compose up -d --wait --wait-timeout "$wait_timeout" --remove-orphans; then
  log "new image did not become healthy"
  compose logs --tail 50 api || true
  rollback
  exit 1
fi

echo "$image" > "$state_file"
log "healthy: $(curl -fsS "http://127.0.0.1:${APP_PORT:-8000}/healthz")"
docker image prune --force --filter "until=168h" >/dev/null || true
