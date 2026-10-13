import asyncio
import copy
import json
from datetime import datetime, timezone

import pytest

from app.clients import agents, store
from app.core.config import CONTRACTS
from app.schemas.contract import validate
from app.services import incidents, supervisor


@pytest.mark.parametrize('agent,status,natural,pii', [
    ('classifier', 'success', False, False), ('writer', 'success', False, False),
    ('classifier', 'needs_user', False, False), ('classifier', 'success', True, False),
    ('writer', 'success', False, True),
])
def test_internal_retries_precede_result_without_recalling_agent(monkeypatch, agent, status, natural, pii):
    name = 'classifier_streetlight_needs_user' if status == 'needs_user' else agent
    result = json.loads((CONTRACTS / f'samples/{name}_output.json').read_text())
    trace = json.loads((CONTRACTS / 'samples/classifier_recovered_output.json').read_text())['data']['recovery']
    result['data']['recovery'] = trace
    if natural:
        trace['attempts'][0].pop('evidence', None)
    else:
        trace['attempts'][0]['evidence'] = {'broken': '분류 결과입니다 · JSONDecodeError'}
    result['incident_id'] = 'incident-natural' if natural else 'inc-chaos'
    if pii:
        trace['attempts'][0]['error_type'] = 'PII_IN_DRAFT'
        trace['recovered_by'] = 'redact'
    else:
        trace['attempts'].append({**trace['attempts'][0], 'model': 'gpt-5.4-mini',
                                  'started_at': trace['started_at']})
        trace['recovered_by'] = 'rules'
    calls, statements = [], []
    async def call(handoff):
        calls.append(handoff)
        return {**copy.deepcopy(result), 'task_id': handoff.task_id}
    async def query(sql, params=(), fetch=False):
        statements.append((sql, params))
    monkeypatch.setattr(agents, 'call', call)
    monkeypatch.setattr(store, 'query', query)
    async def no_retry(*args):
        pytest.fail('internal recovery must not enter the supervisor retry loop')
    monkeypatch.setattr(agents, 'wait_before_retry', no_retry)
    inputs = json.loads((CONTRACTS / f'samples/{agent}_input.json').read_text())['inputs']
    output = asyncio.run(supervisor.execute_stage('job-visible', agent, inputs, 'test', []))
    validate('stage_result', output)
    assert len(calls) == 1 and output['status'] == status
    events = [p for sql, p in statements if sql.startswith('INSERT INTO timeline_events')]
    assert [p[2] for p in events] == ['retry'] * len(trace['attempts']) + [status]
    for number, (params, attempt) in enumerate(zip(events, trace['attempts']), 1):
        assert params[3] == number and params[5] == attempt['started_at']
        assert params[4].obj['failure'] == ('PII_IN_DRAFT' if pii else 'LLM_OUTPUT_INVALID')
        assert params[4].obj['incident_id'] == result['incident_id']
        assert params[4].obj['data'] == {
            **{k: attempt[k] for k in ('model', 'error_type', 'elapsed_ms')},
            'evidence': attempt.get('evidence') or {'broken': f"{attempt['model']} · {attempt['error_type']} · {attempt['elapsed_ms']}ms"}}
    detected = next(p for sql, p in statements if sql == incidents.SQL_DETECTED)
    assert detected[4] == trace['started_at']
    assert detected[3] == ('PII_IN_DRAFT' if pii else 'LLM_OUTPUT_INVALID')
    assert statements[-1] == (incidents.SQL_RECOVERED, (result['incident_id'],))


@pytest.mark.integration
def test_detected_timestamp_is_preserved_in_postgres():
    # Reuse the suite's explicit isolated DB, never the application database.
    from test_integration_db import check_db_connection, get_target_db_url
    import psycopg
    from uuid import uuid4
    url = get_target_db_url()
    if not check_db_connection(url)[0]:
        pytest.skip('isolated PostgreSQL unavailable')
    incident_id = f'inc-test-visible-{uuid4().hex}'
    first = datetime(2026, 10, 12, 1, 2, 3, tzinfo=timezone.utc)
    with psycopg.connect(url) as conn:
        with conn.cursor() as cursor:
            cursor.execute(incidents.SQL_DETECTED, (incident_id, None, 'classifier', 'LLM_OUTPUT_INVALID', first.isoformat(), 'detected'))
            cursor.execute(incidents.SQL_DETECTED, (incident_id, None, 'classifier', 'LLM_OUTPUT_INVALID', None, 'detected'))
            cursor.execute(incidents.SQL_RECOVERED, (incident_id,))
            cursor.execute('SELECT detected_at, recovered_at, result FROM incidents WHERE id=%s', (incident_id,))
            detected, recovered, result = cursor.fetchone()
            assert detected == first and recovered > detected and result == 'recovered'
        conn.rollback()
