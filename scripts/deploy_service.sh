#!/usr/bin/env bash
# GitHub runner: 검증된 checkout의 선택 서비스 소스만 업로드하고 원격 배포를 호출한다.
set -euo pipefail
service="${1:-}"
[[ $# == 1 ]] || exit 2
case "$service" in backend|classifier|locator|verifier|writer|mailer|chaos|frontend) ;; *) exit 2 ;; esac
cd "$(dirname "$0")/.."
for name in AWS_HOST AWS_USER AWS_SSH_PRIVATE_KEY AWS_SSH_KNOWN_HOSTS GITHUB_SHA GITHUB_RUN_ID GITHUB_RUN_ATTEMPT; do
  [[ -n "${!name:-}" ]] || { printf 'deploy: missing %s\n' "$name" >&2; exit 2; }
done
[[ "$AWS_HOST" =~ ^[a-zA-Z0-9][a-zA-Z0-9.-]*$ && "$AWS_USER" =~ ^[a-z_][a-z0-9_-]*$ ]] || exit 2
[[ "$GITHUB_SHA" =~ ^[a-f0-9]{40}$ && "$GITHUB_RUN_ID" =~ ^[0-9]+$ && "$GITHUB_RUN_ATTEMPT" =~ ^[0-9]+$ ]] || exit 2
port="${DEPLOY_SSH_PORT:-22}"
[[ "$port" =~ ^[0-9]{1,5}$ ]] && (( 10#$port >= 1 && 10#$port <= 65535 )) || exit 2
port=$((10#$port))
release="$GITHUB_SHA-$GITHUB_RUN_ID-$GITHUB_RUN_ATTEMPT"
umask 077
task_temp="$(mktemp -d "${RUNNER_TEMP:-${TMPDIR:-/tmp}}/minwon-ssh.XXXXXX")"
cleanup() {
  rm -f "$task_temp/key" "$task_temp/known_hosts"
  rmdir "$task_temp"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
printf '%s\n' "$AWS_SSH_PRIVATE_KEY" > "$task_temp/key"
printf '%s\n' "$AWS_SSH_KNOWN_HOSTS" > "$task_temp/known_hosts"
unset AWS_SSH_PRIVATE_KEY AWS_SSH_KNOWN_HOSTS
ssh_options=(-i "$task_temp/key" -p "$port" -o StrictHostKeyChecking=yes -o "UserKnownHostsFile=$task_temp/known_hosts" -o IdentitiesOnly=yes -o BatchMode=yes -o ConnectTimeout=10 -o ServerAliveInterval=15 -o ServerAliveCountMax=3)
remote="$AWS_USER@$AWS_HOST"
ssh "${ssh_options[@]}" "$remote" bash -s -- "$release" <<'REMOTE'
set -euo pipefail
umask 077
[[ -f "$HOME/minwon/.env" ]] || { echo 'deploy: existing server .env is required' >&2; exit 1; }
mkdir -p "$HOME/minwon/.deploy/incoming/$1"
REMOTE
printf -v remote_shell '%q ' ssh "${ssh_options[@]}"
rsync --archive --relative --delete --exclude=.env --exclude='.env.*' --exclude=.venv/ --exclude=__pycache__/ --exclude=.pytest_cache/ \
  -e "$remote_shell" "src/$service/" contracts/ database/ docker-compose.yml .dockerignore scripts/deploy_service_remote.sh \
  "$remote:minwon/.deploy/incoming/$release/"
# Validated release and SHA expand locally; HOME is resolved remotely.
# shellcheck disable=SC2029
ssh "${ssh_options[@]}" "$remote" "bash \"\$HOME/minwon/.deploy/incoming/$release/scripts/deploy_service_remote.sh\" '$service' '$release' '$GITHUB_SHA'"
