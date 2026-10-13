#!/usr/bin/env bash
# 기존 chaos 서버 진입점 유지
set -euo pipefail
exec bash "$(dirname "$0")/deploy_service_remote.sh" chaos "$@"
