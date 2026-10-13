import asyncio
import json
from types import SimpleNamespace

import pytest

from app.clients import llm
from app.core import config
from app.schemas.contract import Handoff
from app.services import classification, places, recovery
from app.services.address import DONGS
from app.services.rules import classify_by_rules, extract_region, get_location_answer, has_confirmation, is_affirmative
from test_run import post, request
from test_places import document, mock_kakao


@pytest.mark.parametrize('word', ['작동', '공동', '진동', '행동', '이동', '자동', '가동', '운동', '활동'])
def test_predicates_and_non_location_nouns_are_not_dongs(word):
    for text in (f'가로등 {word} 안 해요', f'가로등 {word}이 잘 안 돼요', f'가로등 {word}하지 않아요',
                 f'동작구 {word}이 안돼요', f'동작구 아파트 {word} 현관 앞 쓰레기 무단투기'):
        assert extract_region(text)['dong'] is None


@pytest.mark.parametrize('text,dong', [('서울 동작구 석남동', '석남동'), ('인천 서구 석남동 석남역 앞', '석남동'),
                                     ('오류동 노량진 역 앞', '오류동'), ('서울 동작구 흑성동 가로등 고장', '흑성동')])
def test_unknown_dong_in_place_context_is_preserved(text, dong):
    assert extract_region(text)['dong'] == dong


def test_every_administrative_and_legal_dong_infers_dongjak():
    for dong in DONGS:
        assert extract_region(f'{dong} 아파트 정문 앞')['sigungu'] == '동작구'
    for place in ('하수구', '중앙대학교', '안전센터'):
        assert extract_region(f'상도동 {place} 앞')['dong'] == '상도동'


@pytest.mark.parametrize('answer', ['네 맞아요', '예 거기요', '응 그쪽이요', '넵 맞습니다'])
def test_short_confirmation_is_not_a_location(answer):
    inputs = {'text': '가로등 고장', 'answers': {'location_detail': answer}}
    assert is_affirmative(answer) and has_confirmation(inputs)
    assert get_location_answer(inputs) is None
    assert not is_affirmative('네 흑석동이요') and not is_affirmative('네 아니요')


@pytest.mark.parametrize('answer,expected', [('아니요', '아니요'), ('네 흑석동이요', '네 흑석동이요'),
                                          ('네 맞아요', '상도동 노량진역 앞')])
def test_confirmation_slot_preserves_or_corrects_previous_location(answer, expected):
    inputs = {'text': '가로등 고장', 'answers': {'q_location_detail': '상도동 노량진역 앞', 'q_confirm_location': answer}}
    assert get_location_answer(inputs) == expected


def test_answer_only_location_then_confirmation_runs_once(monkeypatch):
    calls = mock_kakao(monkeypatch, [document('노량진역 9호선', '서울 동작구 노량진동 60')])
    first = asyncio.run(classification.run(Handoff.model_validate(request(
        '가로등이 고장났어요', {'q_location_detail': '상도동 노량진역 앞'}))))
    assert first.data['questions'][0]['id'] == 'q_confirm_location'
    monkeypatch.setattr(config, 'OPENAI_API_KEY', 'test-key')
    models = []
    async def generate(prompt, inputs, model):
        models.append(model)
        return json.dumps(classify_by_rules(inputs))
    monkeypatch.setattr(llm, 'generate', generate)
    second = asyncio.run(classification.run(Handoff.model_validate(request('가로등이 고장났어요', {
        'q_location_detail': '상도동 노량진역 앞', 'q_confirm_location': '네 맞아요'}))))
    assert second.status == 'success' and second.data['region']['dong'] == '노량진동'
    assert second.incident_id is None and models == [config.OPENAI_FALLBACK_MODEL]
    assert calls == ['노량진역', '노량진역']


@pytest.mark.parametrize('text', ['가로등이 작동 안 해요', '동작구 아파트 공동 현관 앞 쓰레기 무단투기',
                                '서울 동작구 석남동 가로등 고장'])
def test_model_null_dong_does_not_restore_common_noun(monkeypatch, text):
    monkeypatch.setattr(config, 'OPENAI_API_KEY', 'test-key')
    async def generate(*args):
        data = classify_by_rules({'text': text})
        data['region']['dong'] = None
        return json.dumps(data)
    monkeypatch.setattr(llm, 'generate', generate)
    result = post(request(text))
    assert result['failure'] != 'OUT_OF_SCOPE' and result['data']['region']['dong'] is None
    assert all('본동' not in q['text'] for q in result['data']['questions'])


@pytest.mark.parametrize('text,category', [('흑석역 앞 인도가 움푹 패였어요', 'road_damage'),
                                        ('누가 소파를 몰래 버리고 갔어요', 'illegal_dumping'),
                                        ('길바닥이 울퉁불퉁해요', 'road_damage')])
def test_valid_model_category_survives_missing_rule_keywords(monkeypatch, text, category):
    monkeypatch.setattr(config, 'OPENAI_API_KEY', 'test-key')
    async def generate(*args):
        data = classify_by_rules({'text': text})
        data.update(category=category, confidence=0.83)
        return json.dumps(data)
    monkeypatch.setattr(llm, 'generate', generate)
    result = post(request(text))
    assert result['data']['category'] == category and result['data']['confidence'] == 0.83
    assert all(q['id'] not in ('q_what', 'q_category') for q in result['data']['questions'])


@pytest.mark.parametrize('addresses,expected', [(['서울 영등포구 여의도동 1', '서울 마포구 공덕동 1'], None),
    (['인천 서구 석남동 1', '인천 서구 석남동 2'], '서구'),
    (['서울 마포구 공덕동 1', '서울 동작구 상도동 1'], '동작구')])
def test_chain_store_requires_one_region_or_dongjak(monkeypatch, addresses, expected):
    mock_kakao(monkeypatch, [document('이마트 지점', address, '대형마트') for address in addresses])
    hit = asyncio.run(places.lookup('이마트'))
    assert (hit.sigungu if hit else None) == expected


@pytest.mark.parametrize('answer,expected', [('있음', True), ('있습니다', True), ('첨부', True),
                                          ('true', True), ('없음', False), ('없습니다', False)])
def test_photo_attachment_signals(answer, expected):
    assert classify_by_rules({'text': '가로등 고장', 'answers': {'q_has_photo': answer}})['details']['has_photo'] is expected


def test_problem_description_in_location_uses_original_landmark(monkeypatch):
    calls = mock_kakao(monkeypatch, [document('노량진역 9호선', '서울 동작구 노량진동 60')])
    monkeypatch.setattr(config, 'OPENAI_API_KEY', 'test-key')
    async def generate(prompt, inputs, model):
        data = classify_by_rules(inputs)
        data['details']['location_detail'] = '보도블록이 들떠 있음'
        return json.dumps(data)
    monkeypatch.setattr(llm, 'generate', generate)
    result = post(request('노량진역 1번 출구 앞 보도블록이 들떠서 넘어질 뻔했어요'))
    assert result['status'] == 'success' and result['data']['details']['location_detail'] == '노량진역 1번 출구 앞'
    assert set(calls) == {'노량진역'}


def test_domain_budget_fallback_has_measured_recovery(monkeypatch):
    monkeypatch.setattr(config, 'OPENAI_API_KEY', 'test-key')
    monkeypatch.setattr(config, 'LLM_BUDGET_SECONDS', 1)
    now, failures, trace = [0.0], [], {}
    monkeypatch.setattr(recovery, 'time', SimpleNamespace(monotonic=lambda: now[0]))
    async def generate(model, violations=None):
        now[0] += 0.6
        return '{}'
    result = asyncio.run(recovery.recover(generate, lambda: {'local': True}, json.loads,
        on_failure=failures.append, validate_domain=lambda data: ['PLACE_DONG'], recovery=trace))
    assert result == {'local': True} and failures == ['BudgetExhausted']
    assert trace['recovered_by'] == 'rules' and trace['attempts'][0]['elapsed_ms'] == 600
    assert trace['attempts'][0]['error_type'] == 'BudgetExhausted' and trace['started_at']
