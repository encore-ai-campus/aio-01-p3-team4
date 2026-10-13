import asyncio
import json
from copy import deepcopy
from fastapi.testclient import TestClient
from app.clients import agents, store
from app.core.config import CONTRACTS
from app.main import app
from app.schemas.contract import validate
from app.services import supervisor


def sample(name):
    return json.loads((CONTRACTS / 'samples' / f'{name}_output.json').read_text())


def test_multiple_categories_parallel_and_independent_delivery(monkeypatch):
    calls, statements, entered, failed_categories = [], [], set(), set()
    async def query(sql, params=(), fetch=False):
        statements.append((sql, params))
    async def run():
        both = asyncio.Event()
        async def call(handoff):
            validate('handoff', handoff.model_dump(by_alias=True))
            inputs = handoff.inputs
            category = inputs.get('answers', {}).get('q_category', inputs.get('category', 'streetlight'))
            calls.append(handoff)
            if handoff.to == 'writer' and category in failed_categories:
                return {**sample('writer_llm_invalid'), 'task_id': handoff.task_id}
            if handoff.to == 'locator':
                entered.add(category)
                if len(entered) == 2:
                    both.set()
                await asyncio.wait_for(both.wait(), 1)
            name = 'mailer' if handoff.to == 'mailer' else f'{handoff.to}_{category}'
            if inputs.get('mode') == 'draft':
                name += '_draft'
            return {**sample(name), 'task_id': handoff.task_id}
        monkeypatch.setattr(agents, 'call', call)
        job = await supervisor.run_job('job-multi', '상도동 가로등 고장이고 쓰레기도 쌓여 있어요', {'location_detail': '주민센터 앞'})
        results = job['state']['results']
        assert job['status'] == 'success' and len(results) == 2
        assert job['state']['result'] == results[0]
        assert [r['category'] for r in results] == ['streetlight', 'illegal_dumping']
        for result in results:
            validate('final', result)
        assert calls[1].inputs['answers']['q_category'] == 'illegal_dumping'
        from app.services.dispatch import send
        await asyncio.gather(*(send(deepcopy(job), r['draft']['title'], r['draft']['body'], i) for i, r in enumerate(results)))
        mail = [h for h in calls if h.to == 'mailer']
        assert [h.task_id for h in mail] == ['job-multi', 'job-multi:1']
        assert [h.inputs['channel'] for h in mail] == [r['channels'][0] for r in results]
        assert all("COALESCE(state->'mails'" in sql for sql, _ in statements if 'jsonb_build_object' in sql)
        for failures in ({'streetlight'}, {'illegal_dumping'}, {'streetlight', 'illegal_dumping'}):
            failed_categories.clear()
            failed_categories.update(failures)
            partial = await supervisor.run_job('job-partial', '상도동 가로등 쓰레기')
            assert partial['status'] == ('failed' if len(failures) == 2 else 'success')
            assert partial['state']['last_result']['failure'] == 'LLM_OUTPUT_INVALID'
            assert partial['state']['result'] == partial['state']['results'][0]
            for item in partial['state']['results']:
                validate('final', item)
                assert bool(item['draft']['body']) == (item['category'] not in failures)
    monkeypatch.setattr(store, 'query', query)
    asyncio.run(run())


def test_extra_category_needs_user_is_skipped(monkeypatch):
    async def query(*args, **kwargs):
        pass
    async def call(handoff):
        extra = handoff.inputs.get('answers', {}).get('q_category') == 'illegal_dumping'
        name = f'{handoff.to}_streetlight'
        if extra:
            name = 'classifier_illegal_dumping_needs_user'
        elif handoff.inputs.get('mode') == 'draft':
            name += '_draft'
        return {**sample(name), 'task_id': handoff.task_id}
    monkeypatch.setattr(store, 'query', query)
    monkeypatch.setattr(agents, 'call', call)
    job = asyncio.run(supervisor.run_job('job-skip', '가로등 쓰레기', question_round=2))
    assert job['status'] == 'success'
    assert len(job['state']['results']) == 1
    assert '쓰레기 무단투기 건은 정보가 부족해 안내하지 못했습니다' in job['state']['result']['confirm_needed']


def test_review_pii_spans_fixes_and_links():
    from app.services.review import check
    result = sample('final_streetlight')
    original = result['draft']
    values = ['010-1234-5678', '02-123-4567', '900101-1234567', 'A.user@example.com', '101동 202호']
    for value in values:
        title = original['title'] + ' ' + value
        reviewed = check(result, title, original['body'])
        pii = next(i for i in reviewed['issues'] if i['kind'] == 'pii')
        assert reviewed['verdict'] == 'block'
        assert title[pii['start']:pii['end']] == value
        assert pii['field'] == 'title' and pii['fix'] == {'replacement': ''}
        assert reviewed['fixed']['title'] == original['title']
    for host, blocked in [('https://evil.test/신고', True), ('https://go.kr.evil.test', True),
                          ('https://www.gov.kr', False), ('https://www.epeople.go.kr', False), ('https://120dasan.or.kr', False)]:
        body = original['body'] + '\n' + host
        reviewed = check(result, original['title'], body)
        assert any(i['kind'] == 'link' for i in reviewed['issues']) == blocked
        if blocked:
            link = next(i for i in reviewed['issues'] if i['kind'] == 'link')
            assert body[link['start']:link['end']] == host
            assert reviewed['fixed']['body'] == original['body']


def test_review_does_not_confuse_subway_line_with_home_unit():
    from app.services.review import check
    result = dict(category='streetlight', region={}, draft={'title': '가로등', 'body': '위치 확인'})
    for location, pii in [('사당1동 4호선 사당역 10번 출구 앞', False), ('101동 1203호로', True)]:
        reviewed = check(result, '가로등', location)
        assert any(i['kind'] == 'pii' for i in reviewed['issues']) == pii


def test_review_place_and_number_change_restores_original():
    from app.services.review import check
    result = sample('final_streetlight')
    result['draft'].update(title='가로등 고장', body='상도동 주민센터 앞 가로등 2개가 꺼졌어요. 수리 부탁해요.')
    result['region']['place_text'] = '상도동 주민센터 앞'
    reviewed = check(result, '가로등 고장', '사당동 주민센터 앞 가로등 3개가 꺼졌어요. 수리 부탁해요.')
    assert reviewed['verdict'] == 'warn'
    for kind in ('place_changed', 'number_changed', 'region_missing'):
        assert any(i['kind'] == kind for i in reviewed['issues'])
    place = next(i for i in reviewed['issues'] if i['kind'] == 'place_changed')
    assert place['fix']['replacement'] == '상도동 주민센터 앞 가로등 2개가 꺼졌어요.'
    assert place['start'] == 0 and place['end'] == len('사당동 주민센터 앞 가로등 3개가 꺼졌어요.')
    fixed = reviewed['fixed']
    assert check(result, fixed['title'], fixed['body'])['verdict'] == 'ok'
    assert any(i['kind'] == 'category_missing' for i in check(result, '고장', '수리 부탁해요')['issues'])


def test_original_ok_and_verifier_review_fallback(monkeypatch):
    from app.services import review
    result = sample('final_streetlight')
    draft = {k: result['draft'][k] for k in ('title', 'body')}
    assert review.check(result, **draft)['issues'] == []
    job = dict(id='job-review', status='success', state={'result': result})
    events, calls = [], []
    async def query(sql, params=(), fetch=False):
        if fetch:
            return [job]
        events.append(params)
    async def call(handoff):
        calls.append(handoff)
        return {**sample('verifier_streetlight_draft'), 'task_id': handoff.task_id}
    monkeypatch.setattr(store, 'query', query)
    monkeypatch.setattr(agents, 'call', call)
    client = TestClient(app)
    response = client.post('/jobs/job-review/review', json=draft).json()
    assert response['verdict'] == 'ok' and response['verifier_checked'] is True
    assert len(calls) == 1 and calls[0].inputs['mode'] == 'draft'
    assert events[0][1:4] == ('verifier', 'success', 1) and events[0][4].obj['review'] is True
    async def leak(handoff):
        return {**sample('verifier_pii_in_draft'), 'task_id': handoff.task_id}
    monkeypatch.setattr(agents, 'call', leak)
    blocked = client.post('/jobs/job-review/review', json=draft).json()
    assert blocked['verdict'] == 'block' and blocked['issues'][0]['start'] is None
    async def timeout(handoff):
        await asyncio.sleep(3)
    monkeypatch.setattr(agents, 'call', timeout)
    response = client.post('/jobs/job-review/review', json=draft).json()
    assert response['verifier_checked'] is False and response['verdict'] == 'ok'
    assert events[-1][4].obj['failure'] == 'SERVICE_UNAVAILABLE'


def test_send_blocks_unsafe_draft_and_validates_index(monkeypatch):
    result = sample('final_streetlight')
    async def query(*args, **kwargs):
        return [dict(id='job-safe', status='success', state={'results': [result], 'result': result})]
    async def forbidden(*args, **kwargs):
        raise AssertionError('unsafe drafts must never reach mailer')
    monkeypatch.setattr(store, 'query', query)
    monkeypatch.setattr(agents, 'call', forbidden)
    client = TestClient(app)
    for body in ('연락처 010-1234-5678', 'https://unofficial.test'):
        response = client.post('/jobs/job-safe/send', json={'title': '가로등 신고', 'body': body})
        assert response.status_code == 409
        assert any(i['severity'] == 'block' for i in response.json()['issues'])
    for index in (-1, 1, 0.5, True):
        response = client.post('/jobs/job-safe/send', json={'title': '가로등 신고', 'body': '상도동 가로등', 'index': index})
        assert response.status_code == 422


def test_review_new_sentence_with_number_warns_without_fix():
    from app.services.review import check
    result = sample('final_streetlight')
    result['draft'].update(title='가로등 고장', body='상도동 주민센터 앞 가로등이 꺼졌어요.')
    reviewed = check(result, '가로등 고장', '상도동 주민센터 앞 가로등이 꺼졌어요. 3일 전부터 그래요.')
    added = [i for i in reviewed['issues'] if i['kind'] == 'number_changed']
    assert reviewed['verdict'] == 'warn' and len(added) == 1
    assert added[0]['fix'] is None and "''" not in added[0]['message'] and '3일 전부터' in added[0]['message']
    assert reviewed['fixed']['body'] == '상도동 주민센터 앞 가로등이 꺼졌어요. 3일 전부터 그래요.'


def test_location_landmarks_never_create_extra_complaints(monkeypatch):
    from unittest.mock import AsyncMock
    monkeypatch.setattr(store, 'query', AsyncMock())
    calls = []
    async def call(handoff):
        calls.append(handoff)
        category = handoff.inputs.get('answers', {}).get('q_category', 'illegal_dumping')
        name = f'{handoff.to}_{category}' + ('_draft' if handoff.inputs.get('mode') == 'draft' else '')
        return {**sample(name), 'task_id': handoff.task_id}
    monkeypatch.setattr(agents, 'call', call)
    for position in ('옆', '앞', '밑', '위', '아래', '근처', '쪽', '건너편', '맞은편'):
        job = asyncio.run(supervisor.run_job('job-landmark', f'상도동 가로등 {position}에 쓰레기가 버려져 있어요',
                                              {'q_location_detail': '가로등 옆이요', 'location_detail': '보도블록 근처'}))
        assert job['status'] == 'success'
        assert [r['category'] for r in job['state']['results']] == ['illegal_dumping']
    assert len([h for h in calls if h.to == 'classifier']) == 9


def test_location_confirmation_id_preserves_previous_answer(monkeypatch):
    from unittest.mock import AsyncMock
    response = sample('classifier_streetlight_needs_user')
    response['data']['questions'] = [{'id': 'q_confirm_location', 'field': 'location_detail', 'text': '상도동이 맞나요?'}]
    execute = AsyncMock(return_value=response)
    monkeypatch.setattr(supervisor, 'execute_stage', execute)
    state = {'confirm_needed': []}
    asyncio.run(supervisor.classify('job-confirm', '가로등 고장', {'location_detail': '상도동', 'q_confirm_location': '네'}, 0, state, set()))
    assert state['last_result']['data']['questions'][0]['id'] == 'q_confirm_location'
    assert execute.call_args.args[2]['answers'] == {'location_detail': '상도동', 'q_confirm_location': '네'}
