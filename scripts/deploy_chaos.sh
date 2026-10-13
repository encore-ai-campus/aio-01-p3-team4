#!/usr/bin/env bash
# 기존 chaos runner 진입점 유지
set -euo pipefail
exec bash "$(dirname "$0")/deploy_service.sh" chaos "$@"
