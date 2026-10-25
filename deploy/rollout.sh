#!/usr/bin/env bash
# Roll $IMAGE out to every server in $SERVERS, one at a time (run by
# .github/workflows/deploy.yml on the runner).
#
# 1. Server 1 is the canary: deploy (remote-deploy.sh: health check, smoke
#    test, rollback of that server on failure), wait $CANARY_SOAK seconds
#    under real traffic, then verify it again (same image, healthy, no
#    restart, smoke test).
# 2. Every other server: deploy the same way.
# 3. With $PUBLIC_URL: the smoke test through the public entry point
#    (Cloudflare, Caddy), with the x-bypass-key header when $BYPASS_KEY is set.
#    Invalid requests may get Cloudflare's firewall block instead of the API's
#    422: schema validation rejects them at the edge.
#
# When any step fails after a server took the release, every server that
# took it is rolled back (remote-deploy.sh --rollback), so the fleet never
# stays split between a good and a bad release. Servers are named 1, 2, ...;
# their addresses are masked by the workflow.
#
# Environment: IMAGE, SERVERS (space separated), SSH_USER, COMPOSE_PROFILES,
# SMOKE (1 or 0), CANARY_SOAK (seconds, default 60), PUBLIC_URL, BYPASS_KEY,
# SMOKE_RETRY_DELAY (seconds before the public smoke test is retried, default 10).
set -Eeuo pipefail

: "${IMAGE:?}" "${SERVERS:?}" "${SSH_USER:?}"
smoke="${SMOKE:-1}"
soak="${CANARY_SOAK:-60}"
profiles="${COMPOSE_PROFILES:-}"
# Values below are interpolated into the remote command on purpose.
[[ "$IMAGE" =~ ^[A-Za-z0-9./:_@-]+$ ]] || { echo "::error::invalid image: $IMAGE"; exit 1; }
[[ "$profiles" =~ ^[a-z,]*$ ]] || { echo "::error::invalid profiles: $profiles"; exit 1; }
[[ "$smoke" =~ ^[01]$ && "$soak" =~ ^[0-9]+$ ]] || { echo "::error::invalid SMOKE or CANARY_SOAK"; exit 1; }

read -r -a servers <<< "$SERVERS"
ssh_opts=(-i ~/.ssh/id_deploy -o BatchMode=yes -o ConnectTimeout=15 -o LogLevel=ERROR)
deployed=()  # indexes of servers that run $IMAGE now

remote() {  # remote <index> <smoke: 1 or 0> <arguments of remote-deploy.sh>...
  local target="$SSH_USER@${servers[$1]}" with_smoke="$2"
  shift 2
  # shellcheck disable=SC2029 # expanded here on purpose, values are validated above
  ssh "${ssh_opts[@]}" "$target" \
    "COMPOSE_PROFILES='$profiles' SMOKE='$with_smoke' bash ~/telegram-me/remote-deploy.sh $*"
}

roll_back_fleet() {
  local index
  if [[ ${#deployed[@]} == 0 ]]; then
    echo "No server kept the release; nothing else to roll back."
    return
  fi
  for ((index = ${#deployed[@]} - 1; index >= 0; index--)); do
    local number=$((deployed[index] + 1))
    echo "::group::roll back server $number"
    if ! remote "${deployed[index]}" 0 --rollback; then
      echo "::error::server $number: rollback failed, fix it by hand (see docs/deployment/production.md#rolling-back)"
    fi
    echo "::endgroup::"
  done
}

fail() {
  echo "::error::$1"
  roll_back_fleet
  exit 1
}

for index in "${!servers[@]}"; do
  number=$((index + 1))
  target="$SSH_USER@${servers[$index]}"
  echo "::group::server $number"
  ssh "${ssh_opts[@]}" "$target" 'mkdir -p ~/telegram-me && chmod 700 ~/telegram-me'
  scp -q -r "${ssh_opts[@]}" deploy/compose.prod.yaml deploy/remote-deploy.sh deploy/.env \
    deploy/Caddyfile deploy/certs scripts/smoke_matrix.py "$target:telegram-me/"
  ssh "${ssh_opts[@]}" "$target" 'chmod 600 ~/telegram-me/.env'
  if ! remote "$index" "$smoke" "'$IMAGE'"; then
    echo "::endgroup::"
    fail "server $number: $IMAGE failed its health check or smoke test and was rolled back there"
  fi
  deployed+=("$index")
  echo "::endgroup::"

  if [[ "$index" == 0 && ${#servers[@]} -gt 1 && "$soak" -gt 0 ]]; then
    echo "::group::canary: server 1 under traffic for ${soak}s"
    sleep "$soak"
    if ! remote 0 "$smoke" --verify "'$IMAGE'"; then
      echo "::endgroup::"
      fail "canary: $IMAGE did not hold up on server 1"
    fi
    echo "::endgroup::"
  fi
done

if [[ -n "${PUBLIC_URL:-}" && "$smoke" == 1 ]]; then
  echo "::group::smoke test through $PUBLIC_URL"
  headers=()
  if [[ -n "${BYPASS_KEY:-}" ]]; then headers=(--header "x-bypass-key:$BYPASS_KEY"); fi
  passed=false
  for attempt in 1 2; do
    if python3 scripts/smoke_matrix.py --base-url "$PUBLIC_URL" --preset auto --timeout 30 \
      --exploratory-channels "" --edge-blocks-invalid "${headers[@]}" > public-smoke.log 2>&1; then
      passed=true
      break
    fi
    echo "public smoke test failed (attempt $attempt of 2):"
    sed -n '/^SUMMARY/,$p' public-smoke.log
    [[ "$attempt" == 2 ]] || sleep "${SMOKE_RETRY_DELAY:-10}"
  done
  rm -f public-smoke.log
  echo "::endgroup::"
  [[ "$passed" == true ]] || fail "the public smoke test failed after the rollout"
fi

echo "Deployed $IMAGE to ${#servers[@]} server(s)."
