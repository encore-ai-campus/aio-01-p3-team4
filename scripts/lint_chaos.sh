#!/usr/bin/env bash
# 루트/서비스 폴더 어느 위치에서 호출해도 CI와 같은 import 분류를 사용한다.
set -euo pipefail
cd "$(dirname "$0")/../src/chaos"
ruff check --isolated app/core/config.py app/core/errors.py app/main.py app/routers/api.py app/schemas/redteam.py app/clients/redteam.py app/services/flags.py app/services/redteam.py tests/conftest.py tests/test_flags.py tests/test_redteam.py
