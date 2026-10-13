import asyncio
import json
import pytest
from app.clients import agents, store
from app.core.config import CONTRACTS
from app.services.supervisor import run_job


def sample(name, task):
    value = json.loads((CONTRACTS / 'samples' / f'{name}_output.json').read_text())
    value['task_id'] = task
    return value


@pytest.mark.parametrize('category', [None, 'streetlight'])
def test_unknown_category_stops_after_two_rounds_without_writer(category, monkeypatch):
    calls = []
    async def query(*args, **kwargs):
        pass
    async def call(h):
        calls.append(h)
        if h.to == 'classifier':
            value = sample('classifier_ask_what', h.task_id)
            value['data']['category'] = category
            return value
        assert h.to != 'writer'
        return sample(f'{h.to}_out_of_scope', h.task_id)
    monkeypatch.setattr(store, 'query', query)
    monkeypatch.setattr(agents, 'call', call)
    first = asyncio.run(run_job('gate-first', '안녕'))
    assert first['state']['last_result']['data']['questions'][0]['id'] == 'q_what'
    job = asyncio.run(run_job('gate-limit', '안녕', question_round=2))
    assert job['status'] == 'success'
    result = job['state']['result']
    assert result['category'] == 'out_of_scope' and not result['draft']['body']
    assert result['confirm_needed'] and all(c['jurisdiction'] == '전국' for c in result['channels'])


def test_mixed_input_does_not_invent_category_answer(monkeypatch):
    async def query(*args, **kwargs):
        pass
    async def call(h):
        assert h.to == 'classifier'
        assert 'q_category' not in h.inputs.get('answers', {})
        return sample('classifier_low_confidence', h.task_id)
    monkeypatch.setattr(store, 'query', query)
    monkeypatch.setattr(agents, 'call', call)
    job = asyncio.run(run_job('gate-mixed', '서울 동작구 상도동 가로등 고장이고 쓰레기도 쌓였어요'))
    assert job['status'] == 'needs_user'
