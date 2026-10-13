import json
import pytest
from app.clients import llm
from app.core import config
from app.services.rules import classify_by_rules
from test_places import post, mock_kakao, document


@pytest.mark.parametrize('model_enabled', [False, True])
def test_typo_is_confirmed_then_corrected(monkeypatch, model_enabled):
    text = '서울시 동작구 흑성동 가로등이 나갓어요'
    if model_enabled:
        monkeypatch.setattr(config, 'OPENAI_API_KEY', 'test-key')
        async def generate(*args, **kwargs):
            data = classify_by_rules({'text': text})
            data['details']['location_detail'] = '흑성동'
            return json.dumps(data)
        monkeypatch.setattr(llm, 'generate', generate)
    first = post(text)
    assert first['status'] == 'needs_user' and first['data']['region']['dong'] is None
    assert '흑석동 말씀이신가요?' in first['data']['questions'][0]['text']
    second = post(text, {'location_detail': '네'})
    assert second['status'] == 'success' and second['data']['region']['dong'] == '흑석동'
    assert '흑성동' not in json.dumps(second, ensure_ascii=False)


@pytest.mark.parametrize('dong', ['오류동 ', '신림동 ', '역삼동 '])
def test_invalid_station_dong_requires_confirmation(monkeypatch, dong):
    calls = mock_kakao(monkeypatch, [document('노량진역 9호선', '서울 동작구 노량진동 60-11')])
    text = f'서울시 동작구 {dong}노량진 역 1번 출구 앞에 가로등이 나갓어요'
    first = post(text)
    assert calls == ['노량진역']
    assert first['status'] == 'needs_user' and first['data']['region']['dong'] is None
    assert '노량진동으로 안내할까요?' in first['data']['questions'][0]['text']
    assert post(text, {'location_detail': '네'})['data']['region']['dong'] == '노량진동'


@pytest.mark.parametrize('dong', ['오류동', '역삼동', '신림동'])
def test_unknown_dong_without_landmark_never_proceeds(dong):
    result = post(f'서울 동작구 {dong} 가로등 고장')
    assert result['status'] == 'needs_user' and result['data']['region']['dong'] is None
    assert result['data']['questions'][0]['text'] == '동작구의 어느 동인가요?'


def test_known_dong_passes():
    assert post('서울 동작구 흑석동 학교 앞 가로등 고장')['status'] == 'success'


def test_address_without_complaint_asks_category_and_dong():
    result = post('서울 동작구 상도로 15')
    assert [q['id'] for q in result['data']['questions']] == ['q_category', 'q_location_detail']
