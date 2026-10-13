"""호출당 13초 예산 안에서 기본 모델·대체 모델·로컬 처리를 실행한다."""
import asyncio
import inspect
import logging
import time
from datetime import datetime, timezone

import httpx

from app.clients import llm
from app.core import config


async def recover(generate, fallback, parse, attempt=1, last_failure=None, on_failure=None, validate_domain=None, recovery=None):
    recovery = recovery if recovery is not None else {}
    recovery.update(attempts=[])
    failure_started = None

    def finish(data, recovered_by):
        recovery["recovered_by"] = recovered_by
        if failure_started is not None:
            recovery["elapsed_ms"] = round((time.monotonic() - failure_started) * 1000)
        return data
    # 총괄에서 재호출한 경우 이미 실패한 앞 단계를 반복하지 않는다.
    if attempt >= 3 or last_failure in ("PII_IN_DRAFT", "DRAFT_MISMATCH"):
        models = [None]
    elif not llm.enabled():
        models = [None, None]
    elif attempt >= 2:
        models = [config.OPENAI_FALLBACK_MODEL]
    else:
        # 기본 모델이 10초를 쓰더라도 남은 예산으로 대체 모델을 한 번 호출한다.
        models = [config.OPENAI_MODEL, config.OPENAI_FALLBACK_MODEL]
    deadline = time.monotonic() + config.LLM_BUDGET_SECONDS
    violations, repaired, repair_pending = [], False, False
    while models:
        model = models.pop(0)
        is_repair, repair_pending = repair_pending, False
        remaining = deadline - time.monotonic()
        if remaining <= 0 or (model is not None and remaining < 1.5):
            break
        started, started_at = time.monotonic(), datetime.now(timezone.utc).isoformat()
        try:
            raw = await asyncio.wait_for(
                generate(model, violations) if violations else generate(model), timeout=min(config.LLM_TIMEOUT_SECONDS, remaining)
            )
            data = parse(raw)
            if model is None or validate_domain is None:
                return finish(data, model or "rules")
            if time.monotonic() >= deadline:
                return finish(fallback(), "rules")
            errors = validate_domain(data)
            if inspect.isawaitable(errors):
                try:
                    errors = await asyncio.wait_for(errors, max(0, deadline - time.monotonic()))
                except TimeoutError:
                    return finish(fallback(), "rules")
            if not errors:
                return finish(data, "repair" if is_repair else model)
            violations = errors
            if not repaired and time.monotonic() < deadline:
                repaired = True
                repair_pending = True
                models.insert(0, model)
                logging.getLogger(__name__).warning(
                    "도메인 위반 incident 없음 repair=1 violations=%s", violations)
        except (httpx.HTTPError, ValueError, TypeError, KeyError, TimeoutError) as error:
            # 예외 원문에는 요청·개인정보가 포함될 수 있어 응답이나 로그에 싣지 않는다.
            if not violations or recovery["attempts"]:
                if failure_started is None:
                    failure_started = time.monotonic()
                    recovery["started_at"] = datetime.now(timezone.utc).isoformat()
                recovery["attempts"].append({"model": model or "rules", "error_type": type(error).__name__,
                    "elapsed_ms": round((time.monotonic() - started) * 1000), "started_at": started_at})
            if on_failure and not violations:
                on_failure(type(error).__name__)
            continue
    return finish(fallback(), "rules")
