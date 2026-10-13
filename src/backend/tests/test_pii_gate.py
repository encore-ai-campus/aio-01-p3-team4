import json
import pytest
from fastapi.testclient import TestClient
from app.clients import store, translate
from app.main import app
from app.services.pii import redact, redact_text
from test_answers import _setup, _sample, _needs_user


def test_input_pii_patterns_and_false_positives():
    values = ['900101-1234567', '010-2345-6789', '02-123-4567', '031-234-5678', '070-2345-6789',
              '0507-2345-6789', '2410-6475', '2410.6475', 'minwon.kim@minwon.example',
              '1234-5678-9012-3456', '101동 1203호', '101-1203호', '태경님', '저는 김민수고',
              '집 주소: 상도로 15', '자택은 상도로 15', '거주지 상도로 15',
              '저는 해남에 사는 한태경입니다', '상도동 사는 김민수예요', '제 이름은 한태경', '이름은 김민수']
    for text in values:
        assert redact_text(text)[1], text
    for text in ['상도로 15', '123-4번지 앞', '노량진역 1번 출구', '7호선', '2호선 3번 출구',
                 '동작구청 도로관리과', '기사님이 무단투기했어요', '주민센터 앞', '2410 6475',
                 '주민님 선생님 담당자님 관계자님 관리자님 경비님 어머님 아버님 사장님 과장님',
                 '상도동에 사는 주민입니다', '흑석동에 사는 한국인입니다', '사는 고등학생입니다',
                 '주무관님 미화원님 환경미화원님 경찰관님 경찰님 소방관님 의원님 기사님 대표님 누나님 언니님']:
        assert redact_text(text) == (text, []), text


@pytest.mark.parametrize('text', [
    '이마트 지하 1층 주차장 입구에 쓰레기 무단투기',
    '상가 102호 앞 보도블록이 깨졌어요',
    '담당 주무관님께 전달 부탁드려요 상도동 학교 앞 가로등 고장',
    '7호선 상도역 2번 출구 앞 가로등',
    '사당1동 4호선 사당역 10번 출구 앞',
])
def test_public_location_and_titles_do_not_trigger_pii_gate(text, monkeypatch):
    db, inputs, _ = _setup(monkeypatch, [_needs_user('temp', ['location_detail'])])
    response = TestClient(app).post('/jobs?wait=true', json={'text': text})
    assert response.status_code == 201
    job = response.json()
    assert redact_text(text) == (text, []) and db.jobs[job['id']]['text'] == text
    assert inputs[0]['text'] == text
    assert all(q['id'] != 'q_pii' for q in job['state']['last_result']['data']['questions'])


def test_pii_create_repeated_rewrite_then_clean_restart(monkeypatch):
    db, inputs, _ = _setup(monkeypatch, [_needs_user('temp', ['location_detail']), _sample('classifier_streetlight', 'temp')])
    original_query = db.query
    async def query(sql, params=(), fetch=False):
        if sql == 'UPDATE jobs SET text=%s WHERE id=%s':
            db.jobs[params[1]]['text'] = params[0]
            return
        return await original_query(sql, params, fetch)
    async def no_translation(*args):
        raise AssertionError('PII must not reach translation')
    monkeypatch.setattr(store, 'query', query)
    monkeypatch.setattr(translate, 'translate', no_translation)
    client = TestClient(app)
    text = '해남 태경님 8.10, 27, 2410-6475, minwon.kim@minwon.example 동작구 흑석동 1번 출구 도로 파손'
    first = client.post('/jobs', json={'text': text, 'lang': 'en', 'has_photo': True}).json()
    job_id = first['id']
    assert first['status'] == 'needs_user' and not inputs
    assert first['text'] == redact(text)[0] and '2410-6475' not in first['text']
    assert first['state']['last_result']['data']['questions'][0]['id'] == 'q_pii'
    assert not first['state'].get('confirm_needed') and len(db.timeline) == 1
    assert db.jobs[job_id]['text'] == redact(text)[0]
    for _ in range(3):
        again = client.post(f'/jobs/{job_id}/answers', json={'answers': {'q_pii': text}}).json()
        assert again['text'] == redact(text)[0]
        assert again['status'] == 'needs_user' and again['state']['question_round'] == 0
        assert again['state']['lang'] == 'en' and again['state']['has_photo']
    assert not inputs and all(row['answer'] == redact(text)[0] for row in db.inputs)
    assert 'minwon.kim@minwon.example' not in json.dumps([db.jobs, db.timeline, db.inputs])
    db.jobs[job_id]['state']['lang'] = 'ko'
    clean = '동작구 흑석동 가로등 고장'
    resumed = client.post(f'/jobs/{job_id}/answers?wait=true', json={'answers': {'q_pii': clean}}).json()
    assert db.jobs[job_id]['text'] == clean and resumed['state']['question_round'] == 1
    assert inputs[0]['text'] == clean and 'q_pii' not in inputs[0].get('answers', {})
    final = client.post(f'/jobs/{job_id}/answers?wait=true', json={'answers': {'location_detail': '흑석역 1번 출구 앞'}}).json()
    assert final['status'] == 'success' and 'q_pii' not in inputs[-1]['answers']


def test_location_answer_is_masked_and_explained(monkeypatch):
    db, inputs, _ = _setup(monkeypatch, [_needs_user('temp', ['location_detail']), _sample('classifier_streetlight', 'temp')])
    client = TestClient(app)
    job = client.post('/jobs?wait=true', json={'text': '가로등 고장'}).json()
    answer = '상도동 학교 앞 010-2345-6789'
    final = client.post(f"/jobs/{job['id']}/answers?wait=true", json={'answers': {'location_detail': answer}}).json()
    assert final['status'] == 'success'
    assert inputs[-1]['answers']['location_detail'] == db.inputs[-1]['answer'] == redact(answer)[0]
    assert final['state']['result']['confirm_needed'].count('답변에 있던 개인정보는 지우고 처리했어요') == 1
