import asyncio
import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.clients import llm
from app.core import config
from app.main import app
from app.schemas.contract import StageResult
from app.services import recovery


@pytest.mark.parametrize('outcome', ['model', 'rules', 'repair'])
def test_recovery_measures_failure_and_remaining_work(monkeypatch, outcome):
    monkeypatch.setattr(config, 'OPENAI_API_KEY', 'test-key')
    now, calls, failures, trace = [0.0], [], [], {}
    monkeypatch.setattr(recovery, 'time', SimpleNamespace(monotonic=lambda: now[0]))
    async def generate(model, violations=None):
        calls.append(model)
        now[0] += 1.25
        if len(calls) == 1 or outcome == 'rules':
            raise ValueError('private request text must never escape')
        return json.dumps({'ok': outcome != 'repair' or len(calls) == 3})
    before = datetime.now(timezone.utc)
    value = asyncio.run(recovery.recover(generate, lambda: {'ok': True}, json.loads,
        on_failure=failures.append, validate_domain=lambda d: [] if d['ok'] else ['DONG'], recovery=trace))
    assert value == {'ok': True}
    assert trace['recovered_by'] == {'model': config.OPENAI_FALLBACK_MODEL, 'rules': 'rules', 'repair': 'repair'}[outcome]
    assert trace['elapsed_ms'] == (2500 if outcome == 'repair' else 1250)
    assert len(trace['attempts']) == (2 if outcome == 'rules' else 1)
    assert trace['attempts'][0]['model'] == config.OPENAI_MODEL
    assert all(a['elapsed_ms'] == 1250 and a['error_type'] == 'ValueError' for a in trace['attempts'])
    assert before <= datetime.fromisoformat(trace['attempts'][0]['started_at']) <= datetime.fromisoformat(trace['started_at'])
    assert 'private' not in json.dumps(trace)


def test_natural_failure_emits_valid_server_measured_recovery(monkeypatch, caplog):
    monkeypatch.setattr(config, 'OPENAI_API_KEY', 'test-key')
    calls = []
    sample = json.loads((config.CONTRACTS / f'samples/{config.SERVICE}_output.json').read_text())
    async def generate(*args, **kwargs):
        calls.append(args[2])
        if len(calls) == 1:
            return 'private response 010-1234-5678'
        # A model must not be able to supply fake recovery evidence.
        return json.dumps({**sample['data'], 'recovery': {'prompt': 'private response'},
                           'evidence': {'broken': 'fabricated', 'fixed': 'fabricated'}})
    monkeypatch.setattr(llm, 'generate', generate)
    request = json.loads((config.CONTRACTS / f'samples/{config.SERVICE}_input.json').read_text())
    result = TestClient(app).post('/run', json=request).json()
    StageResult.model_validate(result)
    assert result['incident_id'].startswith('incident-')
    assert calls == [config.OPENAI_MODEL, config.OPENAI_FALLBACK_MODEL]
    trace = result['data']['recovery']
    assert trace['recovered_by'] == config.OPENAI_FALLBACK_MODEL
    assert trace['attempts'][0]['error_type'] == 'JSONDecodeError'
    assert set(trace['attempts'][0]) == {'model', 'error_type', 'elapsed_ms', 'started_at', 'evidence'}
    assert trace['attempts'][0]['evidence'] == {'broken': 'private response [개인정보 삭제] · JSONDecodeError'}
    assert result['data']['evidence'] == {'fixed': f'{config.OPENAI_FALLBACK_MODEL} → 가로등 고장 · 상도동'}
    assert '010-1234-5678' not in json.dumps(result) + caplog.text
    assert 'fabricated' not in json.dumps(result)
    assert 'private response' not in caplog.text


@pytest.mark.parametrize('raw,masked', [
    ('설명 ' * 18 + '010-9876-5432\nJSON 대신 산문 응답',
     '설명 ' * 18 + '[개인정보 삭제] JSON 대신 산문 응답'),
    ('시민@example.com ' + r'\u0030\u0031\u0030-9876-5432 "\uc2dc\ubbfc\u0040example\u002ecom" '
     + '김민수 ' + json.dumps('김민수') + '\nJSON 대신 산문 응답',
     '[개인정보 삭제] [개인정보 삭제] "[개인정보 삭제]" [개인정보 삭제] "[개인정보 삭제]" JSON 대신 산문 응답'),
])
def test_failure_preview_masks_before_truncation_and_input_names(monkeypatch, raw, masked, caplog):
    monkeypatch.setattr(config, 'OPENAI_API_KEY', 'test-key')
    calls = []
    sample = json.loads((config.CONTRACTS / 'samples/classifier_output.json').read_text())
    async def generate(*args, **kwargs):
        calls.append(args[2])
        return raw if len(calls) == 1 else json.dumps(sample['data'])
    monkeypatch.setattr(llm, 'generate', generate)
    request = json.loads((config.CONTRACTS / 'samples/classifier_input.json').read_text())
    request['inputs']['text'] += ' 저는 김민수입니다. 연락처 010-9876-5432'
    result = TestClient(app).post('/run', json=request).json()
    StageResult.model_validate(result)
    assert result['data']['recovery']['attempts'][0]['evidence']['broken'] == ' '.join(masked.split())[:60] + ' · JSONDecodeError'
    evidence = json.dumps(result['data']['recovery'], ensure_ascii=False) + caplog.text
    assert all(private not in evidence for private in ('김민수', '010', '\\uae40', '\\u0030', '\\uc2dc', '시민', 'example.com'))


@pytest.mark.parametrize('field,value', [('prompt', 'private'), ('elapsed_ms', -1), ('started_at', 'bad-date')])
def test_recovery_contract_rejects_private_or_invalid_metadata(field, value):
    sample = json.loads((config.CONTRACTS / 'samples/classifier_recovered_output.json').read_text())
    trace = sample['data']['recovery']
    sample = json.loads((config.CONTRACTS / f'samples/{config.SERVICE}_output.json').read_text())
    sample['data']['recovery'] = trace
    trace['attempts'][0][field] = value
    with pytest.raises(ValueError):
        StageResult.model_validate(sample)
