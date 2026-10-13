#!/usr/bin/env bash
# 프로젝트 컨테이너 중단 또는 데이터 서버 연결 차단 후 원래 상태로 복원한다.
set -euo pipefail
cd "$(dirname "$0")/.."
project_dir="$(pwd -P)"

fail() { printf 'chaos: %s\n' "$1" >&2; exit "${2:-1}"; }
case "${1:-}" in
  backend|classifier|locator|verifier|writer|chaos|frontend|postgres|redis|mailer|mailpit) target="$1" ;;
  *) fail 'Usage: scripts/chaos_infra.sh SERVICE [SECONDS]' 2 ;;
esac
[[ $# -le 2 ]] || fail 'Too many arguments' 2
duration="${2:-5}"
[[ "$duration" =~ ^[0-9]{1,2}$ ]] || fail 'Seconds must be an integer from 0 to 60' 2
duration=$((10#$duration))
(( duration <= 60 )) || fail 'Seconds must be an integer from 0 to 60' 2

env_file="${CHAOS_ENV_FILE:-.env}"
[[ -z "${CHAOS_ENV_FILE:-}" || -f "$env_file" ]] || fail 'CHAOS_ENV_FILE does not exist' 2
# dotenv를 실행하지 않고 필요한 데이터 서버 주소만 읽는다. 명시적 빈 DATA_HOST는 로컬 시험이다.
if [[ -z "${DATA_HOST+x}" && -f "$env_file" ]]; then
  DATA_HOST="$(sed -nE 's/^[[:space:]]*DATA_HOST[[:space:]]*=[[:space:]]*(.*)$/\1/p' "$env_file" | tail -1)"
  DATA_HOST="${DATA_HOST%%#*}"
  DATA_HOST="${DATA_HOST//$'\r'/}"
  DATA_HOST="${DATA_HOST//[[:space:]]/}"
  DATA_HOST="${DATA_HOST#\"}"; DATA_HOST="${DATA_HOST%\"}"
  DATA_HOST="${DATA_HOST#\'}"; DATA_HOST="${DATA_HOST%\'}"
fi
remote=0
if [[ "$target" =~ ^(postgres|redis)$ && -n "${DATA_HOST:-}" ]]; then
  [[ "$DATA_HOST" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]] || fail 'DATA_HOST must be an IPv4 address' 2
  IFS=. read -r -a octets <<< "$DATA_HOST"
  for octet in "${octets[@]}"; do
    (( 10#$octet <= 255 )) || fail 'DATA_HOST must be an IPv4 address' 2
    [[ "$octet" == 0 || "$octet" != 0* ]] || fail 'DATA_HOST must use canonical IPv4 notation' 2
  done
  remote=1
fi

compose=(docker compose -p "${COMPOSE_PROJECT_NAME:-minwon}" -f docker-compose.yml)
[[ ! -f "$env_file" ]] || compose+=(--env-file "$env_file")
lock_dir="${TMPDIR:-/tmp}/minwon-chaos-${target}.lock"
lock_owned=0
restore_needed=0
sleep_pid=''
container_id=''
tag="minwon-chaos:${target}:$$:${RANDOM}"
rule=()

wait_running() {
  local state attempt
  for (( attempt=0; attempt<30; attempt++ )); do
    state="$(docker inspect --format '{{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$container_id")" || return 1
    [[ "$state" == 'running healthy' || "$state" == 'running none' ]] && return 0
    [[ "$state" != *unhealthy* ]] || return 1
    sleep 1
  done
  return 1
}

cleanup() {
  local status=$? check_status=0 restored=1
  trap - EXIT INT TERM
  if [[ -n "$sleep_pid" ]]; then
    kill "$sleep_pid" 2>/dev/null || true
    wait "$sleep_pid" 2>/dev/null || true
  fi
  if (( restore_needed )); then
    if (( remote )); then
      sudo -n iptables -w 5 -C "${rule[@]}" || check_status=$?
      if (( check_status == 0 )); then
        sudo -n iptables -w 5 -D "${rule[@]}" || restored=0
      elif (( check_status != 1 )); then
        restored=0
      fi
    else
      "${compose[@]}" start "$target" && wait_running || restored=0
    fi
    if (( restored )); then
      printf 'chaos: restored %s\n' "$target"
    else
      printf 'chaos: RESTORE FAILED for %s; recovery instructions: %s/recovery.txt\n' "$target" "$lock_dir" >&2
      status=1
    fi
  fi
  if (( lock_owned && restored )); then
    rm -f "$lock_dir/recovery.txt"
    rmdir "$lock_dir" || status=1
  fi
  exit "$status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
mkdir "$lock_dir" 2>/dev/null || fail "Another test or unresolved recovery holds $lock_dir"
lock_owned=1

if (( remote )); then
  [[ "$target" == postgres ]] && port=5432 || port=6379
  if [[ "$target" == postgres ]]; then
    database_url="${DATABASE_URL:-}"
    if [[ -z "$database_url" && -f "$env_file" ]]; then
      database_url="$(sed -nE 's/^[[:space:]]*DATABASE_URL[[:space:]]*=[[:space:]]*(.*)$/\1/p' "$env_file" | tail -1)"
    fi
    database_url="${database_url%%[[:space:]]*}"
    database_url="${database_url#\"}"; database_url="${database_url%\"}"
    database_url="${database_url#\'}"; database_url="${database_url%\'}"
    authority="${database_url#*://}"
    authority="${authority%%[/?#]*}"
    authority="${authority##*@}"
    [[ "$authority" != *:* ]] || port="${authority##*:}"
    [[ "$port" =~ ^[0-9]{1,5}$ ]] || fail 'DATABASE_URL port must be from 1 to 65535' 2
    port=$((10#$port))
    (( port >= 1 && port <= 65535 )) || fail 'DATABASE_URL port must be from 1 to 65535' 2
  fi
  rule=(DOCKER-USER -d "$DATA_HOST" -p tcp --dport "$port" -m comment --comment "$tag" -j REJECT)
  sudo -n iptables -w 5 -n -L DOCKER-USER >/dev/null
  printf 'sudo -n iptables -w 5 -D ' > "$lock_dir/recovery.txt"
  printf '%q ' "${rule[@]}" >> "$lock_dir/recovery.txt"
  printf '\n' >> "$lock_dir/recovery.txt"
  restore_needed=1
  sudo -n iptables -w 5 -I "${rule[@]}"
else
  "${compose[@]}" config --quiet
  container_id="$("${compose[@]}" ps --all --quiet "$target")"
  [[ -n "$container_id" && "$container_id" != *$'\n'* ]] || fail 'Exactly one existing target container is required; no container will be created'
  owner_dir="$(docker inspect --format '{{index .Config.Labels "com.docker.compose.project.working_dir"}}' "$container_id")"
  if command -v cygpath >/dev/null; then owner_dir="$(cygpath -u "$owner_dir")"; fi
  [[ -d "$owner_dir" && "$(cd "$owner_dir" && pwd -P)" == "$project_dir" ]] || fail 'Target container belongs to another workspace'
  state="$(docker inspect --format '{{.State.Status}}' "$container_id")"
  case "$state" in
    exited|created) printf 'chaos: %s was stopped; unchanged\n' "$target"; exit 0 ;;
    running) ;;
    *) fail "Target state $state is not supported" ;;
  esac
  printf '%q ' "${compose[@]}" start "$target" > "$lock_dir/recovery.txt"
  printf '\n' >> "$lock_dir/recovery.txt"
  restore_needed=1
  "${compose[@]}" stop --timeout 5 "$target"
fi
printf 'chaos: injected %s for %s seconds\n' "$target" "$duration"
sleep "$duration" &
sleep_pid=$!
wait "$sleep_pid"
sleep_pid=''
