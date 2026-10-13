#!/usr/bin/env bash
# EC2: 서버 잠금 아래 선택된 소스 반영, 선택 서비스만 빌드·기동하고 health/서비스 식별을 확인한다.
set -euo pipefail
service="${1:-}"
case "$service" in backend|classifier|locator|verifier|writer|mailer|chaos|frontend) ;; *) exit 2 ;; esac
release="${2:-}"
sha="${3:-}"
[[ $# == 3 && "$sha" =~ ^[a-f0-9]{40}$ && "$release" =~ ^[a-f0-9]{40}-[0-9]+-[0-9]+$ && "$release" == "$sha-"* ]] || exit 2
root="${MINWON_DEPLOY_ROOT:-$HOME/minwon}"
root="$(cd "$root" && pwd -P)"
incoming="$root/.deploy/incoming/$release"
[[ -f "$root/.env" && -d "$incoming/src/$service" && -d "$incoming/contracts" ]] || { echo 'deploy: server .env or staged source missing' >&2; exit 1; }
exec 9>"$root/.deploy/server.lock"
flock -w 300 9
mkdir -p "$root/src/$service" "$root/contracts" "$root/database"
rsync --archive --delete --exclude=.env --exclude='.env.*' --exclude=.venv/ --exclude=__pycache__/ --exclude=.pytest_cache/ "$incoming/src/$service/" "$root/src/$service/"
rsync --archive --delete --exclude=.env --exclude='.env.*' "$incoming/contracts/" "$root/contracts/"
cp "$incoming/docker-compose.yml" "$root/docker-compose.yml"
cp "$incoming/.dockerignore" "$root/.dockerignore"
cp "$incoming/database/schema.sql" "$root/database/schema.sql"
if [[ "$service" == locator ]]; then
  [[ -d "$incoming/database/seeds" ]] || { echo 'deploy: locator seeds missing' >&2; exit 1; }
  mkdir -p "$root/database/seeds"
  rsync --archive --delete --exclude=.env --exclude='.env.*' "$incoming/database/seeds/" "$root/database/seeds/"
fi
cd "$root"
compose=(docker compose -p minwon -f "$root/docker-compose.yml" --env-file "$root/.env")
"${compose[@]}" config --quiet
if [[ "$service" == mailer ]]; then
  timeout 600 "${compose[@]}" up -d mailpit
fi
timeout 600 "${compose[@]}" up -d --build --no-deps "$service"
container_id="$("${compose[@]}" ps --all --quiet "$service")"
[[ -n "$container_id" && "$container_id" != *$'\n'* ]] || { echo 'deploy: exactly one selected container required' >&2; exit 1; }
deadline=$((SECONDS + 90))
healthy=0
while (( SECONDS < deadline )); do
  state="$(docker inspect --format '{{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{else}}missing{{end}}' "$container_id")"
  case "$state" in
    'running healthy')
      if [[ "$service" == frontend ]]; then
        identity="$(docker exec "$container_id" wget -q -T 3 -O - http://127.0.0.1/health)"
        [[ "$identity" == '{"status":"ok","service":"frontend"}' ]] || { echo 'deploy: frontend service identity check failed' >&2; exit 1; }
      else
        docker exec "$container_id" python -c 'import json,sys,urllib.request; r=json.load(urllib.request.urlopen("http://127.0.0.1:8000/health",timeout=3)); assert r.get("status")=="ok" and r.get("service")==sys.argv[1]' "$service" >/dev/null 2>&1 || { echo 'deploy: service identity check failed' >&2; exit 1; }
      fi
      healthy=1; break ;;
    *unhealthy*|exited*|dead*) echo 'deploy: service container failed health check' >&2; exit 1 ;;
  esac
  sleep 1
done
(( healthy )) || { echo 'deploy: service readiness deadline exceeded' >&2; exit 1; }
image_id="$(docker inspect --format '{{.Image}}' "$container_id")"
printf '{"service":"%s","sha":"%s","release":"%s","image":"%s","deployed_at":"%s"}\n' "$service" "$sha" "$release" "$image_id" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$root/.deploy/$service.json.$release"
mv "$root/.deploy/$service.json.$release" "$root/.deploy/$service.json"
printf 'deploy: %s ready sha=%s\n' "$service" "$sha"
