#!/usr/bin/env bash
set -euo pipefail
root="$(cd "$(dirname "$0")/../.." && pwd -P)"
sandbox="$(mktemp -d "${TMPDIR:-/tmp}/minwon-chaos-test.XXXXXX")"
mkdir "$sandbox/bin" "$sandbox/locks"
export QA_ROOT="$root" QA_LOG="$sandbox/calls" QA_RULE="$sandbox/rule" QA_FOREIGN="$sandbox/foreign-rule"
export PATH="$sandbox/bin:$PATH" TMPDIR="$sandbox/locks" DATA_HOST=''
unset DATABASE_URL COMPOSE_PROJECT_NAME
export CHAOS_ENV_FILE="$sandbox/env" QA_STATE=running QA_MODE=ok
: > "$CHAOS_ENV_FILE"
printf preserved > "$QA_FOREIGN"
cat > "$sandbox/bin/docker" <<'FAKE'
#!/usr/bin/env bash
printf 'docker %s\n' "$*" >> "$QA_LOG"
case "$*" in
  *' config --quiet') [[ "$QA_MODE" != config_fail ]] ;;
  *' ps --all --quiet '*) [[ "$QA_MODE" == missing ]] || echo container-id ;;
  inspect\ *working_dir*) [[ "$QA_MODE" == wrong_owner ]] && echo /no-such-workspace || echo "$QA_ROOT" ;;
  inspect\ *Health*) [[ "$QA_MODE" == unhealthy ]] && echo 'running unhealthy' || echo 'running healthy' ;;
  inspect\ *) echo "$QA_STATE" ;;
  *' stop '*) [[ "$QA_MODE" != stop_fail ]] ;;
  *' start '*) [[ "$QA_MODE" != start_fail ]] ;;
  *) exit 99 ;;
esac
FAKE
cat > "$sandbox/bin/sudo" <<'FAKE'
#!/usr/bin/env bash
[[ "$1" != -n ]] || shift
exec "$@"
FAKE
cat > "$sandbox/bin/iptables" <<'FAKE'
#!/usr/bin/env bash
printf 'iptables %s\n' "$*" >> "$QA_LOG"
case "$*" in
  *' -L '*) [[ "$QA_MODE" != preflight_fail ]] ;;
  *' -I '*) [[ "$QA_MODE" != insert_fail ]] || exit 4; printf '%s' "${*:4}" > "$QA_RULE"; [[ "$QA_MODE" != partial_insert_fail ]] || exit 4 ;;
  *' -C '*) [[ -f "$QA_RULE" && "$(cat "$QA_RULE")" == "${*:4}" ]] ;;
  *' -D '*) [[ "$QA_MODE" != delete_fail ]] || exit 4; [[ "$(cat "$QA_RULE")" == "${*:4}" ]] || exit 4; rm -f "$QA_RULE" ;;
  *) exit 99 ;;
esac
FAKE
cat > "$sandbox/bin/sleep" <<'FAKE'
#!/usr/bin/env bash
printf 'sleep %s\n' "$*" >> "$QA_LOG"
case "$QA_MODE" in
  int) kill -INT "$PPID" ;;
  term) kill -TERM "$PPID" ;;
  sleep_fail) exit 19 ;;
esac
FAKE
chmod +x "$sandbox/bin/"*
passed=0
run() {
  : > "$QA_LOG"
  local expected="$1" status=0; shift
  bash "${QA_SCRIPT:-$root/scripts/chaos_infra.sh}" "$@" > "$sandbox/output" 2>&1 || status=$?
  [[ "$status" == "$expected" ]] || { cat "$sandbox/output"; echo "Expected $expected, got $status: $*" >&2; exit 1; }
  passed=$((passed+1))
}
no_mutation() { ! grep -Eq ' stop | start | -I | -D ' "$QA_LOG"; }
for args in 'unknown 1' 'classifier -1' 'classifier 61' 'classifier 1.5' 'classifier 999999999999999' 'classifier 1 extra'; do
  read -r -a items <<< "$args"; run 2 "${items[@]}"; no_mutation
done
for target in backend classifier locator verifier writer chaos frontend postgres redis mailer mailpit; do
  run 0 "$target" 0
  grep -q " stop --timeout 5 $target" "$QA_LOG"
  grep -q " start $target" "$QA_LOG"
  [[ ! -d "$TMPDIR/minwon-chaos-$target.lock" ]]
done
run 0 classifier 08
run 0 classifier 60
COMPOSE_PROJECT_NAME=minwon-integ run 0 classifier 0
grep -q 'docker compose -p minwon-integ .* stop --timeout 5 classifier' "$QA_LOG"
grep -q 'docker compose -p minwon-integ .* start classifier' "$QA_LOG"
if command -v cygpath >/dev/null; then
  PWD="$(cygpath -m "$root")" QA_SCRIPT=scripts/chaos_infra.sh run 0 classifier 0
fi
QA_STATE=exited run 0 classifier; no_mutation
QA_STATE=created run 0 classifier; no_mutation
QA_STATE=paused run 1 classifier; no_mutation
QA_MODE=missing run 1 classifier; no_mutation
QA_MODE=wrong_owner run 1 classifier; no_mutation
QA_MODE=config_fail run 1 classifier; no_mutation
QA_MODE=stop_fail run 1 classifier; grep -q ' start classifier' "$QA_LOG"
QA_MODE=sleep_fail run 19 classifier; grep -q ' start classifier' "$QA_LOG"
QA_MODE=int run 130 classifier; grep -q ' start classifier' "$QA_LOG"
QA_MODE=term run 143 classifier; grep -q ' start classifier' "$QA_LOG"
for mode in start_fail unhealthy; do
  QA_MODE="$mode" run 1 classifier
  grep -q 'RESTORE FAILED' "$sandbox/output"
  [[ -f "$TMPDIR/minwon-chaos-classifier.lock/recovery.txt" ]]
  rm "$TMPDIR/minwon-chaos-classifier.lock/recovery.txt"; rmdir "$TMPDIR/minwon-chaos-classifier.lock"
done
mkdir "$TMPDIR/minwon-chaos-classifier.lock"
run 1 classifier; no_mutation
rmdir "$TMPDIR/minwon-chaos-classifier.lock"
for host in '999.1.1.1' '127.1' 'name.example' '-x' '1.2.3.04' '1.2.3.4;echo'; do
  DATA_HOST="$host" run 2 postgres; no_mutation
done
for target in postgres redis; do
  DATA_HOST=192.0.2.10 run 0 "$target"
  [[ "$target" == postgres ]] && expected_port=5432 || expected_port=6379
  grep -q -- "--dport $expected_port " "$QA_LOG"
  grep -q ' -I DOCKER-USER ' "$QA_LOG"
  grep -q ' -D DOCKER-USER ' "$QA_LOG"
  grep -q -- '--comment minwon-chaos:' "$QA_LOG"
  [[ ! -f "$QA_RULE" ]]
done
printf 'DATABASE_URL="postgresql://demo:synthetic@192.0.2.10:5433/minwon" # test\n' > "$CHAOS_ENV_FILE"
DATA_HOST=192.0.2.10 run 0 postgres
grep -q -- '--dport 5433 ' "$QA_LOG"
DATA_HOST=192.0.2.10 DATABASE_URL=postgresql://demo:synthetic@192.0.2.10:15432/minwon run 0 postgres
grep -q -- '--dport 15432 ' "$QA_LOG"
DATA_HOST=192.0.2.10 DATABASE_URL=postgresql://192.0.2.10:65536/minwon run 2 postgres
no_mutation
: > "$CHAOS_ENV_FILE"
for mode in preflight_fail insert_fail; do
  [[ "$mode" == insert_fail ]] && expected=4 || expected=1
  DATA_HOST=192.0.2.10 QA_MODE="$mode" run "$expected" postgres
  if grep -q ' -D ' "$QA_LOG"; then exit 1; fi
done
DATA_HOST=192.0.2.10 QA_MODE=partial_insert_fail run 4 postgres
grep -q ' -D DOCKER-USER ' "$QA_LOG"
[[ ! -f "$QA_RULE" ]]
for mode in int term sleep_fail; do
  case "$mode" in int) expected=130 ;; term) expected=143 ;; *) expected=19 ;; esac
  DATA_HOST=192.0.2.10 QA_MODE="$mode" run "$expected" postgres
  grep -q ' -D DOCKER-USER ' "$QA_LOG"
  [[ ! -f "$QA_RULE" ]]
done
DATA_HOST=192.0.2.10 QA_MODE=delete_fail run 1 postgres
[[ -f "$TMPDIR/minwon-chaos-postgres.lock/recovery.txt" ]]
rm "$TMPDIR/minwon-chaos-postgres.lock/recovery.txt" "$QA_RULE"; rmdir "$TMPDIR/minwon-chaos-postgres.lock"
printf 'DATA_HOST="192.0.2.10" # test\n' > "$CHAOS_ENV_FILE"
unset DATA_HOST
run 0 redis; grep -q ' -I ' "$QA_LOG"
export DATA_HOST=''
run 0 redis; grep -q ' stop ' "$QA_LOG"
# 명시한 env 누락은 변경 전에 거부한다.
CHAOS_ENV_FILE="$sandbox/absent" run 2 classifier; no_mutation
[[ "$(cat "$QA_FOREIGN")" == preserved ]]
printf 'infra tests: %s passed\n' "$passed"
rm -f "$sandbox/bin/docker" "$sandbox/bin/sudo" "$sandbox/bin/iptables" "$sandbox/bin/sleep" "$sandbox/calls" "$sandbox/output" "$sandbox/env" "$QA_FOREIGN"
rmdir "$sandbox/bin" "$sandbox/locks" "$sandbox"
