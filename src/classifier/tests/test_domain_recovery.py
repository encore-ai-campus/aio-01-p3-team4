import asyncio
import json
from types import SimpleNamespace
import pytest
from app.core import config
from app.services import recovery


@pytest.mark.parametrize('valid_at,expected_calls', [(2, 2), (3, 3), (99, 3)])
def test_domain_repair_only_creates_incident_on_rules_fallback(monkeypatch, caplog, valid_at, expected_calls):
    monkeypatch.setattr(config, 'OPENAI_API_KEY', 'test-key')
    calls, failures = [], []
    async def generate(model, violations=None):
        calls.append((model, violations))
        return json.dumps({'ok': len(calls) >= valid_at})
    value = asyncio.run(recovery.recover(generate, lambda: {'ok': 'local'}, json.loads,
        on_failure=failures.append, validate_domain=lambda d: [] if d['ok'] else ['DONG']))
    assert len(calls) == expected_calls
    assert calls[:2] == [(config.OPENAI_MODEL, None), (config.OPENAI_MODEL, ['DONG'])]
    if expected_calls == 3:
        assert calls[2] == (config.OPENAI_FALLBACK_MODEL, ['DONG'])
    assert value == {'ok': True if valid_at < 99 else 'local'}
    assert failures == (["DomainValidationError"] if valid_at == 99 else [])
    assert caplog.text.count('도메인 위반 incident 없음 repair=1') == 1


def test_budget_exhausted_skips_repair_and_records_incident(monkeypatch):
    monkeypatch.setattr(config, 'OPENAI_API_KEY', 'test-key')
    monkeypatch.setattr(config, 'LLM_BUDGET_SECONDS', 1)
    now, calls, failures = [0.0], [], []
    monkeypatch.setattr(recovery, 'time', SimpleNamespace(monotonic=lambda: now[0]))
    async def generate(model, violations=None):
        calls.append(model)
        now[0] = 1.0
        return '{"ok":false}'
    value = asyncio.run(recovery.recover(generate, lambda: {'ok': 'local'}, json.loads,
        on_failure=failures.append, validate_domain=lambda d: ['DONG']))
    assert value == {'ok': 'local'} and calls == [config.OPENAI_MODEL] and failures == ["BudgetExhausted"]


def test_domain_check_cannot_extend_budget_and_records_incident(monkeypatch):
    monkeypatch.setattr(config, 'OPENAI_API_KEY', 'test-key')
    monkeypatch.setattr(config, 'LLM_BUDGET_SECONDS', 0.01)
    calls, failures = [], []
    async def generate(model, violations=None):
        calls.append(model)
        return '{"ok":false}'
    async def slow_domain(data):
        await asyncio.sleep(1)
        return ['DONG']
    value = asyncio.run(recovery.recover(generate, lambda: {'ok': 'local'}, json.loads,
        on_failure=failures.append, validate_domain=slow_domain))
    assert value == {'ok': 'local'} and len(calls) == 1 and failures == ["BudgetExhausted"]
