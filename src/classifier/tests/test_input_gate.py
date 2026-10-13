import json
import pytest
from app.clients import llm
from app.core import config
from app.services.rules import classify_by_rules
from test_run import post, request


@pytest.mark.parametrize('text', ['안녕', 'ㅋㅋ', '😀', 'https://example.com',
                                 '가로등이 꺼지면 어떻게 하죠?', '서울 동작구 상도동 학교 앞 가로등은 정상입니다'])
def test_non_complaint_even_when_model_overconfident(text, monkeypatch):
    monkeypatch.setattr(config, 'OPENAI_API_KEY', 'test-key')
    async def generate(*args, **kwargs):
        data = classify_by_rules({'text': text})
        data.update(category='streetlight', confidence=0.99)
        return json.dumps(data)
    monkeypatch.setattr(llm, 'generate', generate)
    result = post(request(text))
    assert result['status'] == 'needs_user'
    assert result['data']['category'] is None
    assert [q['id'] for q in result['data']['questions']] == ['q_what']


def test_what_answer_resumes_and_negation_selects_actual_category():
    result = post(request('안녕', {'q_what': '서울 동작구 상도동 학교 앞 가로등 고장'}))
    assert result['status'] == 'success' and result['data']['category'] == 'streetlight'
    result = post(request('서울 동작구 상도동 학교 앞 가로등은 멀쩡한데 보도블록이 깨졌어요'))
    assert result['status'] == 'success' and result['data']['category'] == 'road_damage'


def test_routing_question_proceeds_without_inventing_region():
    result = post(request('가로등 고장 어디에 신고해요?'))
    assert result['status'] == 'success' and result['data']['category'] == 'streetlight'
    assert result['data']['region']['sido'] is None
    assert result['data']['region']['sigungu'] is None


@pytest.mark.parametrize('answers', [None, {'q_location_detail': '서울 동작구 상도동 학교 앞'}])
def test_disagreeing_high_confidence_requires_category(answers, monkeypatch):
    monkeypatch.setattr(config, 'OPENAI_API_KEY', 'test-key')
    async def generate(*args, **kwargs):
        data = classify_by_rules({'text': '서울 동작구 상도동 학교 앞 가로등 고장'})
        data.update(category='road_damage', confidence=0.99)
        return json.dumps(data)
    monkeypatch.setattr(llm, 'generate', generate)
    result = post(request('서울 동작구 상도동 학교 앞 가로등 고장', answers))
    assert result['status'] == 'needs_user'
    assert 'category' in result['data']['missing']


@pytest.mark.parametrize('category,confidence,question', [
    ('out_of_scope', 0, 'q_what'), ('streetlight', 0.6, 'q_category'),
    ('out_of_scope', 0.9, None),
])
def test_successful_model_is_not_overruled(category, confidence, question, monkeypatch):
    monkeypatch.setattr(config, 'OPENAI_API_KEY', 'test-key')
    async def generate(*args, **kwargs):
        data = classify_by_rules({'text': '상도동 학교 앞 가로등 고장'})
        data.update(category=category, confidence=confidence)
        return json.dumps(data)
    monkeypatch.setattr(llm, 'generate', generate)
    result = post(request('상도동 학교 앞 가로등 고장'))
    assert result['data']['confidence'] == confidence
    if question:
        assert result['data']['questions'][0]['id'] == question
    else:
        assert result['failure'] == 'OUT_OF_SCOPE'


@pytest.mark.parametrize('answer_key', ['q_category', 'category'])
def test_explicit_category_survives_model_non_complaint(answer_key, monkeypatch):
    monkeypatch.setattr(config, 'OPENAI_API_KEY', 'test-key')
    async def generate(*args, **kwargs):
        data = classify_by_rules({'text': '상도동 학교 앞 가로등 고장'})
        data.update(category='out_of_scope', confidence=0)
        return json.dumps(data)
    monkeypatch.setattr(llm, 'generate', generate)
    result = post(request('상도동 학교 앞 뭔가 이상해요', {answer_key: '가로등 고장'}))
    assert result['status'] == 'success' and result['data']['category'] == 'streetlight'


def test_model_failure_uses_rules_and_keeps_recovery(monkeypatch):
    monkeypatch.setattr(config, 'OPENAI_API_KEY', 'test-key')
    async def generate(*args, **kwargs):
        raise ValueError('invalid output')
    monkeypatch.setattr(llm, 'generate', generate)
    result = post(request('상도동 학교 앞 가로등 고장'))
    assert result['status'] == 'success' and result['data']['confidence'] == 0.9
    assert result['incident_id'] and result['data']['recovery']['recovered_by'] == 'rules'
    assert len(result['data']['recovery']['attempts']) == 2


def test_model_receives_only_redacted_text_and_answers(monkeypatch):
    monkeypatch.setattr(config, 'OPENAI_API_KEY', 'test-key')
    async def generate(prompt, inputs, model):
        serialized = json.dumps(inputs, ensure_ascii=False)
        assert '010-2345-6789' not in serialized and 'minwon.kim@minwon.example' not in serialized
        assert '[개인정보 삭제]' in inputs['text'] and '[개인정보 삭제]' in inputs['answers']['q_location_detail']
        return json.dumps(classify_by_rules({'text': '상도동 학교 앞 가로등 고장'}))
    monkeypatch.setattr(llm, 'generate', generate)
    post(request('010-2345-6789 상도동 가로등 고장', {'q_location_detail': '상도동 학교 앞 minwon.kim@minwon.example'}))
