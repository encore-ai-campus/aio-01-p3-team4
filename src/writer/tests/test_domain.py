import json
from app.clients import llm
from app.core import config
from app.services.domain import validate_domain
from app.services.templates import make_template
from test_run import post, request


def test_writer_domain_dong_pii_category_and_length():
    inputs = request()['inputs']
    data = make_template(inputs)
    assert validate_domain(data, inputs) == []
    data['body'] += ' 사당동에서 연락처 010-1234-5678'
    assert set(validate_domain(data, inputs)) == {'DONG', 'PII'}
    data['title'] = 'x' * 201
    data['body'] = '사당동에서 연락처 010-1234-5678'
    assert {'DONG', 'PII', 'CATEGORY_WORD', 'LENGTH'} <= set(validate_domain(data, inputs))


def test_writer_repairs_before_redaction_without_incident(monkeypatch, caplog):
    monkeypatch.setattr(config, 'OPENAI_API_KEY', 'test-key')
    calls = []
    async def generate(prompt, inputs, model):
        calls.append((model, inputs))
        data = make_template(inputs)
        if len(calls) == 1:
            data['body'] += ' 사당동 연락처 010-1234-5678'
        else:
            assert set(inputs['violations']) == {'DONG', 'PII'}
            assert inputs['allowed']['dongs'] == ['상도동']
            assert inputs['allowed']['categories'] == ['가로등 고장', '도로 파손', '쓰레기 무단투기', '생활 불편']
        return json.dumps(data)
    monkeypatch.setattr(llm, 'generate', generate)
    result = post(request())
    assert len(calls) == 2 and calls[0][0] == calls[1][0]
    assert result['incident_id'] is None and result['data']['removed_pii'] == []
    assert '사당동' not in result['data']['body'] and '010-' not in caplog.text


def test_keywords_in_either_title_or_body_are_enough():
    from app.services.domain import KEYWORDS
    for category, words in KEYWORDS.items():
        for word in words:
            for field in ('title', 'body'):
                data = {'title': '현장 확인 요청', 'body': '작동이 멈춰 이동이 불편하고 자동 점등이 안 돼요'}
                data[field] += word
                assert validate_domain(data, request(category)['inputs']) == []


def test_only_real_dongs_are_compared_without_administrative_numbers():
    inputs = request()['inputs']
    for dong, mentioned in [('상도동', '상도1동'), ('상도1동', '상도동'), ('상도2동', '상도4동')]:
        inputs['region']['dong'] = dong
        data = {'title': '가로등 확인 요청', 'body': mentioned + '에서 작동을 확인하고 이동해 주세요'}
        assert validate_domain(data, inputs) == []
        data['body'] += ' 사당1동에서 확인'
        assert validate_domain(data, inputs) == ['DONG']
