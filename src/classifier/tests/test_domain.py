import copy
import json
import pytest
from app.clients import llm
from app.core import config
from app.services.domain import validate_domain
from app.services.rules import classify_by_rules
from test_run import post, request


@pytest.mark.parametrize('field,value,code', [('category', 'invented', 'CATEGORY'),
    ('confidence', float('nan'), 'CONFIDENCE'), ('confidence', 1.01, 'CONFIDENCE'),
    ('questions', [{'id': 'invented', 'field': 'category', 'text': 'x'}], 'QUESTION_ID')])
def test_domain_rejects_value_codes(field, value, code):
    data = classify_by_rules({'text': '서울 동작구 흑석동 학교 앞 가로등 고장'})
    data[field] = value
    assert code in validate_domain(data)


@pytest.mark.parametrize('bad,good', [('흑성동', '흑석동'), ('오류동', '노량진동')])
def test_bad_dong_repairs_once_but_still_asks_confirmation(monkeypatch, bad, good, caplog):
    monkeypatch.setattr(config, 'OPENAI_API_KEY', 'test-key')
    calls = []
    text = f'서울 동작구 {bad} 학교 앞 가로등 고장'
    async def generate(prompt, inputs, model):
        calls.append((model, inputs))
        data = classify_by_rules({'text': text})
        if len(calls) == 2:
            assert inputs['violations'] == ['DONG']
            assert good in inputs['allowed']['dongs'] and 'streetlight' in inputs['allowed']['categories']
            data['region']['dong'] = good
        return json.dumps(data)
    monkeypatch.setattr(llm, 'generate', generate)
    result = post(request(text))
    assert [c[0] for c in calls] == [config.OPENAI_MODEL] * 2
    assert result['status'] == 'needs_user' and result['incident_id'] is None
    assert good in result['data']['questions'][0]['text']
    assert result['data']['region']['dong'] is None
    assert bad not in caplog.text


def test_region_and_grounded_place_domain():
    data = classify_by_rules({'text': '서울 동작구 상도동 노량진역 앞 가로등 고장'})
    assert validate_domain(data, '노량진동') == ['PLACE_DONG']
    data['region']['sigungu'] = '강남구'
    assert 'REGION_CATEGORY' in validate_domain(data)
