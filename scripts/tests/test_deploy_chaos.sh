#!/usr/bin/env bash
set -euo pipefail
service="${1:-chaos}"
case "$service" in backend|classifier|locator|verifier|writer|mailer|chaos|frontend) ;; *) exit 2 ;; esac
root="$(cd "$(dirname "$0")/../.." && pwd -P)"
sandbox="$(mktemp -d "${TMPDIR:-/tmp}/minwon-deploy-test.XXXXXX")"
mkdir "$sandbox/bin" "$sandbox/keys"
export PATH="$sandbox/bin:$PATH" RUNNER_TEMP="$sandbox/keys"
export QA_LOG="$sandbox/calls" QA_COUNT="$sandbox/ssh-count" QA_MODE=ok
export AWS_HOST=192.0.2.10 AWS_USER=ubuntu AWS_SSH_PRIVATE_KEY=synthetic-key AWS_SSH_KNOWN_HOSTS=synthetic-host-key
export GITHUB_SHA=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa GITHUB_RUN_ID=123 GITHUB_RUN_ATTEMPT=1
export MINWON_DEPLOY_ROOT="$sandbox/server"
release="$GITHUB_SHA-$GITHUB_RUN_ID-$GITHUB_RUN_ATTEMPT"
cleanup() {
  local status=$?
  if (( status != 0 )); then printf 'Failed fixture retained: %s\n' "$sandbox" >&2; return; fi
  local resolved base
  resolved="$(realpath "$sandbox")"; base="$(realpath "${TMPDIR:-/tmp}")"
  [[ "$resolved" == "$base/minwon-deploy-test."* ]] || return 1
  find "$resolved" -type f -exec rm -f -- {} +
  find "$resolved" -depth -type d -exec rmdir -- {} +
}
trap cleanup EXIT
cat > "$sandbox/bin/ssh" <<'FAKE'
#!/usr/bin/env bash
printf 'ssh %s\n' "$*" >> "$QA_LOG"
key=''; strict=0
while (( $# )); do
  case "$1" in -i) key="$2"; shift ;; StrictHostKeyChecking=yes) strict=1 ;; esac
  shift
done
[[ -f "$key" && "$strict" == 1 ]]
[[ "$(cat "$key")" == synthetic-key ]]
count=$(cat "$QA_COUNT"); count=$((count+1)); printf '%s' "$count" > "$QA_COUNT"
if (( count == 1 )); then cat >/dev/null; [[ "$QA_MODE" != ssh_fail ]] || exit 255; fi
if (( count == 2 )); then [[ "$QA_MODE" != remote_fail ]] || exit 42; fi
FAKE
cat > "$sandbox/bin/rsync" <<'FAKE'
#!/usr/bin/env bash
printf 'rsync %s\n' "$*" >> "$QA_LOG"
[[ "$*" == *--exclude=.env* && "$*" == *--exclude=.env.\** ]]
[[ "$QA_MODE" != upload_fail ]] || exit 23
last="${!#}"
if [[ "$last" == *@* ]]; then exit 0; fi
previous=$(($#-1)); source_dir="${!previous}"
mkdir -p "$last"
for file in "$source_dir"* "$source_dir".[!.]*; do
  [[ -e "$file" ]] || continue
  case "${file##*/}" in .env|.env.*) continue ;; esac
  cp -R "$file" "$last/"
done
FAKE
cat > "$sandbox/bin/flock" <<'FAKE'
#!/usr/bin/env bash
printf 'flock %s\n' "$*" >> "$QA_LOG"
[[ "$QA_MODE" != lock_fail ]]
FAKE
cat > "$sandbox/bin/timeout" <<'FAKE'
#!/usr/bin/env bash
printf 'timeout %s\n' "$*" >> "$QA_LOG"
shift
exec "$@"
FAKE
cat > "$sandbox/bin/docker" <<'FAKE'
#!/usr/bin/env bash
printf 'docker %s\n' "$*" >> "$QA_LOG"
case "$*" in
  *' config --quiet') [[ "$QA_MODE" != config_fail ]] ;;
  *' up -d mailpit') [[ "$QA_MODE" != mailpit_fail ]] || exit 18 ;;
  *' up -d --build --no-deps '*) [[ "$QA_MODE" != build_fail ]] || exit 17 ;;
  *' ps --all --quiet '*) [[ "$QA_MODE" == missing ]] || echo container-id ;;
  inspect*'.Image'*) echo sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb ;;
  inspect*) [[ "$QA_MODE" == unhealthy ]] && echo 'running unhealthy' || echo 'running healthy' ;;
  exec*)
    [[ "$QA_MODE" != identity_fail ]] || exit 1
    if [[ "$*" == *wget* ]]; then printf '%s' '{"status":"ok","service":"frontend"}'; fi ;;
  *) exit 99 ;;
esac
FAKE
chmod +x "$sandbox/bin/"*
passed=0
run() {
  local expected="$1" status=0; shift
  : > "$QA_LOG"; printf 0 > "$QA_COUNT"
  bash "$1" "$service" "${@:2}" > "$sandbox/output" 2>&1 || status=$?
  [[ "$status" == "$expected" ]] || { cat "$sandbox/output"; printf 'Expected %s, got %s\n' "$expected" "$status" >&2; exit 1; }
  passed=$((passed+1))
}
runner="$root/scripts/deploy_service.sh"
run 0 "$runner"
grep -q 'StrictHostKeyChecking=yes' "$QA_LOG"
grep -q 'deploy_service_remote.sh' "$QA_LOG"
grep -q "src/$service/" "$QA_LOG"
grep -q 'IdentitiesOnly=yes' "$QA_LOG"
grep -q -- '--relative --delete --exclude=.env' "$QA_LOG"
grep -q "minwon/.deploy/incoming/$release/" "$QA_LOG"
[[ "$(cat "$QA_COUNT")" == 2 ]]
[[ -z "$(ls -A "$RUNNER_TEMP")" ]]
for mode in ssh_fail upload_fail remote_fail; do
  case "$mode" in ssh_fail) expected=255 ;; upload_fail) expected=23 ;; *) expected=42 ;; esac
  QA_MODE="$mode" run "$expected" "$runner"
  [[ -z "$(ls -A "$RUNNER_TEMP")" ]]
done
for name in AWS_HOST AWS_USER AWS_SSH_PRIVATE_KEY AWS_SSH_KNOWN_HOSTS GITHUB_SHA GITHUB_RUN_ID GITHUB_RUN_ATTEMPT; do
  saved="${!name}"; export "$name="
  run 2 "$runner"; [[ ! -s "$QA_LOG" ]]
  export "$name=$saved"
done
AWS_HOST='host;bad' run 2 "$runner"
AWS_USER='-bad' run 2 "$runner"
GITHUB_SHA='../../bad' run 2 "$runner"
DEPLOY_SSH_PORT=0 run 2 "$runner"
DEPLOY_SSH_PORT=65536 run 2 "$runner"
DEPLOY_SSH_PORT=2222 run 0 "$runner"
grep -q -- '-p 2222' "$QA_LOG"

staged="$MINWON_DEPLOY_ROOT/.deploy/incoming/$release"
mkdir -p "$staged/src/$service" "$staged/contracts" "$staged/database" "$MINWON_DEPLOY_ROOT/src/$service"
mkdir -p "$staged/database/seeds"
printf seed > "$staged/database/seeds/test.json"
printf server-secret > "$MINWON_DEPLOY_ROOT/.env"
printf service-secret > "$MINWON_DEPLOY_ROOT/src/$service/.env"
printf new-source > "$staged/src/$service/source.txt"
printf malicious-replacement > "$staged/src/$service/.env"
printf '{}' > "$staged/contracts/sample.json"
printf services: > "$staged/docker-compose.yml"
printf .env > "$staged/.dockerignore"
printf schema > "$staged/database/schema.sql"
remote_script="$root/scripts/deploy_service_remote.sh"
run 0 "$remote_script" "$release" "$GITHUB_SHA"
[[ "$(cat "$MINWON_DEPLOY_ROOT/.env")" == server-secret ]]
[[ "$(cat "$MINWON_DEPLOY_ROOT/src/$service/.env")" == service-secret ]]
[[ "$(cat "$MINWON_DEPLOY_ROOT/src/$service/source.txt")" == new-source ]]
grep -Fq "up -d --build --no-deps $service" "$QA_LOG"
if [[ "$service" == mailer ]]; then
  grep -q 'docker .* up -d mailpit$' "$QA_LOG"
  [[ "$(grep 'docker .* up -d ' "$QA_LOG" | head -1)" == *' up -d mailpit' ]]
else
  if grep -q ' up -d mailpit$' "$QA_LOG"; then exit 1; fi
fi
grep -q 'flock -w 300 9' "$QA_LOG"
grep -q -- 'timeout 600' "$QA_LOG"
[[ -f "$MINWON_DEPLOY_ROOT/.deploy/$service.json" ]]
last_success="$(cat "$MINWON_DEPLOY_ROOT/.deploy/$service.json")"
if [[ "$service" == mailer ]]; then
  QA_MODE=mailpit_fail run 18 "$remote_script" "$release" "$GITHUB_SHA"
  if grep -q 'up -d --build' "$QA_LOG"; then exit 1; fi
  [[ "$(cat "$MINWON_DEPLOY_ROOT/.deploy/$service.json")" == "$last_success" ]]
fi
for mode in lock_fail config_fail build_fail missing unhealthy identity_fail; do
  [[ "$mode" == build_fail ]] && expected=17 || expected=1
  QA_MODE="$mode" run "$expected" "$remote_script" "$release" "$GITHUB_SHA"
  [[ "$(cat "$MINWON_DEPLOY_ROOT/.deploy/$service.json")" == "$last_success" ]]
  if [[ "$mode" == lock_fail ]] && grep -q "^rsync " "$QA_LOG"; then exit 1; fi
done
run 2 "$remote_script" '../invalid' "$GITHUB_SHA"
run 2 "$remote_script" "$release" invalid
if [[ "$service" == locator ]]; then [[ "$(cat "$MINWON_DEPLOY_ROOT/database/seeds/test.json")" == seed ]]; fi
status=0
: > "$QA_LOG"
bash "$runner" mailpit > "$sandbox/output" 2>&1 || status=$?
[[ "$status" == 2 && ! -s "$QA_LOG" ]]; passed=$((passed+1))
mv "$MINWON_DEPLOY_ROOT/.env" "$MINWON_DEPLOY_ROOT/.env.saved"
run 1 "$remote_script" "$release" "$GITHUB_SHA"
[[ ! -s "$QA_LOG" ]]
if [[ "$service" == chaos ]]; then
  mv "$MINWON_DEPLOY_ROOT/.env.saved" "$MINWON_DEPLOY_ROOT/.env"
  : > "$QA_LOG"; printf 0 > "$QA_COUNT"
  bash "$root/scripts/deploy_chaos.sh" > "$sandbox/output" 2>&1
  passed=$((passed+1))
  bash "$root/scripts/deploy_chaos_remote.sh" "$release" "$GITHUB_SHA" > "$sandbox/output" 2>&1
  passed=$((passed+1))
fi
printf 'deploy %s tests: %s passed\n' "$service" "$passed"
