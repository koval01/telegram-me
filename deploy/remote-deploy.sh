#!/usr/bin/env bash
# Deploy one image on this server with automatic rollback.
#
#   remote-deploy.sh <image-ref>            deploy, smoke-test, roll back on failure
#   remote-deploy.sh --verify <image-ref>   check that <image-ref> still runs well
#   remote-deploy.sh --rollback             start the image deployed before the last one
#
# Expects compose.prod.yaml and .env in $APP_DIR (default ~/telegram-me), and
# smoke_matrix.py next to them for the smoke test.
#
# A deployment succeeds when the new image becomes healthy within
# $WAIT_TIMEOUT seconds and then passes the smoke test (scripts/smoke_matrix.py,
# run inside the container against live t.me, one retry). Otherwise the
# previously deployed image (or the 1.x `docker run` container this stack
# replaces) is started again and the script exits non-zero.
#
# --verify (the canary check after a soak) fails when the API container runs
# another image, is not healthy, was restarted or killed for memory, or fails
# the smoke test. --rollback undoes the last successful deployment; the deploy
# workflow uses it to put servers that already took a release back when a
# later check fails.
#
# SMOKE=0 skips the smoke test, SMOKE_RETRY_DELAY sets the pause before its
# retry (default 5 s); SKIP_PULL=1 uses a local image (handy for
# testing the script, see tests/deploy/).
set -Eeuo pipefail

mode=deploy
case "${1:-}" in
  --verify) mode=verify; shift ;;
  --rollback) mode=rollback ;;
esac
if [[ "$mode" == rollback ]]; then
  image=""
else
  image="${1:?usage: remote-deploy.sh [--verify] <image-ref> | --rollback}"
fi
app_dir="${APP_DIR:-$HOME/telegram-me}"
wait_timeout="${WAIT_TIMEOUT:-90}"
legacy_name="telegram-me"  # container name used by 1.x deployments
state_file=".deployed-image"
previous_file=".previous-image"

cd "$app_dir"
# APP_IMAGE defaults to the image being deployed; callers override it per call.
compose() { APP_IMAGE="${APP_IMAGE:-$image}" docker compose -f compose.prod.yaml "$@"; }
log() { printf '[deploy %s] %s\n' "$(hostname)" "$*"; }

# The peer layer (ENABLE_PEERS) advertises this server's private address (the
# source address of its default route) and publishes one port per worker plus
# one, for every mode: compose must see the same ports each time.
sed -i.bak '/^PEER_BIND=/d; /^PEER_PORTS=/d' .env && rm -f .env.bak
if grep -qx 'ENABLE_PEERS=true' .env; then
  peer_addr="$(sed -n 's/^PEER_ADVERTISE_ADDR=//p' .env)"
  if [[ -z "$peer_addr" ]]; then
    peer_addr="$(ip -4 route get 192.0.2.1 | awk '{for (i = 1; i < NF; i++) if ($i == "src") print $(i + 1)}')"
    echo "PEER_ADVERTISE_ADDR=$peer_addr" >> .env
  fi
  peer_port="$(sed -n 's/^PEER_PORT=//p' .env)"
  peer_port="${peer_port:-7946}"
  workers="$(sed -n 's/^WEB_CONCURRENCY=//p' .env)"
  workers="${workers:-$(( $(nproc) < 4 ? $(nproc) : 4 ))}"
  printf 'PEER_BIND=%s\nPEER_PORTS=%s-%s\n' "$peer_addr" "$peer_port" "$((peer_port + workers))" >> .env
  log "peer layer on $peer_addr, ports $peer_port-$((peer_port + workers))"
fi

# Remember $1 as deployed (and $2 as what it replaced) for --rollback and
# plain `docker compose -f compose.prod.yaml ps|logs|...` on the server.
record() {
  echo "$1" > "$state_file"
  if [[ -n "${2:-}" && "$2" != "$1" ]]; then echo "$2" > "$previous_file"; else rm -f "$previous_file"; fi
  sed -i.bak '/^APP_IMAGE=/d; /^COMPOSE_PROFILES=/d' .env && rm -f .env.bak
  printf 'APP_IMAGE=%s\nCOMPOSE_PROFILES=%s\n' "$1" "${COMPOSE_PROFILES:-}" >> .env
}

# The smoke matrix inside the API container: the request path through this
# server's own configuration, Redis and network to t.me, without the proxy.
smoke() {
  if [[ "${SMOKE:-1}" == 0 ]]; then
    log "smoke test skipped (SMOKE=0)"
    return 0
  fi
  if [[ ! -f smoke_matrix.py ]]; then
    log "smoke_matrix.py is missing in $app_dir"
    return 1
  fi
  local attempt
  for attempt in 1 2; do
    # shellcheck disable=SC2016 # $PORT expands inside the container
    if APP_IMAGE="$1" compose exec -T api sh -c \
      'exec python - --base-url "http://127.0.0.1:${PORT:-3000}" --preset auto --timeout 20 --exploratory-channels ""' \
      < smoke_matrix.py > smoke.log 2>&1; then
      log "smoke test passed"
      return 0
    fi
    log "smoke test failed (attempt $attempt of 2):"
    sed -n '/^SUMMARY/,$p' smoke.log | sed 's/^/    /'
    [[ "$attempt" == 2 ]] || sleep "${SMOKE_RETRY_DELAY:-5}"
  done
  return 1
}

# Problems with the running API container, if any.
check_container() {
  local id running health restarts oom
  id="$(APP_IMAGE="$1" compose ps -q api)"
  if [[ -z "$id" ]]; then
    echo "the api container is not running"
    return
  fi
  IFS='|' read -r running health restarts oom < <(docker inspect -f \
    '{{.Config.Image}}|{{if .State.Health}}{{.State.Health.Status}}{{end}}|{{.RestartCount}}|{{.State.OOMKilled}}' "$id")
  [[ "$running" == "$1" ]] || echo "runs $running instead of $1"
  [[ "$health" == healthy ]] || echo "is ${health:-without a health check}, not healthy"
  [[ "$restarts" == 0 ]] || echo "was restarted $restarts time(s)"
  [[ "$oom" == false ]] || echo "was killed for using too much memory"
}

if [[ "$mode" == verify ]]; then
  problems="$(check_container "$image")"
  if [[ -n "$problems" ]]; then
    log "verification of $image failed: the api container"
    printf '    %s\n' "$problems"
    compose logs --tail 50 api || true
    exit 1
  fi
  smoke "$image" || exit 1
  log "verified $image"
  exit 0
fi

if [[ "$mode" == rollback ]]; then
  current="$(cat "$state_file" 2>/dev/null || true)"
  image="$(cat "$previous_file" 2>/dev/null || true)"
  if [[ -z "$image" ]]; then
    log "nothing to roll back to (no $previous_file)"
    exit 1
  fi
  log "rolling back from ${current:-unknown} to $image"
  APP_IMAGE="$image" compose up -d --wait --wait-timeout "$wait_timeout" --remove-orphans
  record "$image"
  log "rolled back:$(curl -fsS "http://127.0.0.1:${APP_PORT:-8000}/healthz")"
  exit 0
fi

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
  if [[ -n "$previous" && "$previous" != "$image" ]]; then
    log "rolling back to $previous"
    APP_IMAGE="$previous" compose up -d --wait --wait-timeout "$wait_timeout" --remove-orphans
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

if ! smoke "$image"; then
  log "new image is healthy but failed the smoke test"
  compose logs --tail 50 api || true
  rollback
  exit 1
fi

record "$image" "$previous"
log "healthy:$(curl -fsS "http://127.0.0.1:${APP_PORT:-8000}/healthz")"
docker image prune --force --filter "until=168h" >/dev/null || true
