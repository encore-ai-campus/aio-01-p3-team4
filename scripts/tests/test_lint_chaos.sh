#!/usr/bin/env bash
set -euo pipefail
root="$(cd "$(dirname "$0")/../.." && pwd -P)"
sandbox="$(mktemp -d "${TMPDIR:-/tmp}/minwon-lint-test.XXXXXX")"
cleanup() { rm -f "$sandbox/ruff" "$sandbox/output"; rmdir "$sandbox"; }
trap cleanup EXIT
export QA_LINT_LOG="$sandbox/output" QA_LINT_STATUS=0
cat > "$sandbox/ruff" <<'FAKE'
#!/usr/bin/env bash
printf '%s\n' "$PWD" "$@" > "$QA_LINT_LOG"
exit "$QA_LINT_STATUS"
FAKE
chmod +x "$sandbox/ruff"
export PATH="$sandbox:$PATH"
for caller in "$root" "$root/src/chaos"; do
  (cd "$caller" && bash "$root/scripts/lint_chaos.sh")
  [[ "$(head -1 "$QA_LINT_LOG")" == "$root/src/chaos" ]]
  [[ "$(sed -n '2,3p' "$QA_LINT_LOG")" == $'check\n--isolated' ]]
  [[ "$(wc -l < "$QA_LINT_LOG")" == 14 ]]
done
status=0
QA_LINT_STATUS=7 bash "$root/scripts/lint_chaos.sh" || status=$?
[[ "$status" == 7 ]]
printf 'lint entrypoint tests: 3 passed\n'
