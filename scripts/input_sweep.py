#!/usr/bin/env python3
"""[TEST] Real classifier run() sweep; same frozen expectations before and after."""
import argparse
import asyncio
import hashlib
import importlib.util
import json
import logging
import os
from pathlib import Path
import sys
import time
from contextvars import ContextVar

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src/classifier'))
# No shared DB/cache or injected faults; .env still supplies the actual model key.
os.environ['REDIS_URL'] = ''
os.environ['DATABASE_URL'] = ''
from app.clients import llm
from app.core import config
from app.schemas.contract import Handoff
from app.services.classification import run

spec = importlib.util.spec_from_file_location('sweep_security', ROOT / 'src/backend/app/services/security.py')
security = importlib.util.module_from_spec(spec)
spec.loader.exec_module(security)
trace = ContextVar('trace')
original_generate = llm.generate


async def tracked_generate(*args, **kwargs):
    entry = {'model': args[2], 'completed': False}
    trace.get().append(entry)
    try:
        raw = await original_generate(*args, **kwargs)
        entry.update(completed=True, output=json.loads(raw))
        return raw
    except Exception as exc:
        entry['error'] = type(exc).__name__
        raise


def outcome(result):
    if result.get('failure') == 'INJECTION_BLOCKED':
        return 'blocked'
    if result.get('failure') == 'OUT_OF_SCOPE':
        return 'out_of_scope'
    if result.get('status') == 'success':
        return 'proceed'
    ids = {q['id'] for q in result.get('data', {}).get('questions', [])}
    for question, label in [('q_what', 'ask_what'), ('q_category', 'ask_category'),
                            ('q_location_detail', 'ask_location'), ('q_confirm_location', 'ask_location')]:
        if question in ids:
            return label
    return 'error'


async def execute(case, number, repeat):
    calls = []
    started = time.monotonic()
    token = trace.set(calls)
    try:
        if security.is_injection(case['text'])[0]:
            result = {'status': 'failed', 'failure': 'INJECTION_BLOCKED', 'data': {}}
        else:
            handoff = Handoff.model_validate({
                'task_id': f'input-sweep-{number}-{repeat}', 'from': 'supervisor',
                'to': 'classifier', 'objective': '생활 민원 분류',
                'inputs': {'text': case['text'], 'answers': case.get('answers', {})},
                'constraints': [], 'attempt': 1, 'last_failure': None, 'memory_refs': [],
            })
            result = (await run(handoff)).model_dump()
        actual = outcome(result)
        completed = actual == 'blocked' or any(call['completed'] for call in calls)
        category_ok = 'category' not in case or result['data'].get('category') == case['category']
        expected = case['expect'] if isinstance(case['expect'], list) else [case['expect']]
        return {'actual': actual, 'pass': completed and actual in expected and category_ok,
                'llm_completed': completed, 'elapsed_ms': round((time.monotonic() - started) * 1000),
                'calls': calls, 'result': result}
    except Exception as exc:
        return {'actual': 'error', 'pass': False, 'llm_completed': False,
                'error': type(exc).__name__, 'calls': calls}
    finally:
        trace.reset(token)


async def main(args):
    if not llm.enabled():
        raise SystemExit('[TEST] OPENAI_API_KEY required: rules-only sweep is not accepted')
    llm.generate = tracked_generate
    cases = [json.loads(line) for line in args.cases.read_text().splitlines() if line.strip()]
    allowed = {'proceed', 'ask_category', 'ask_location', 'ask_what', 'out_of_scope', 'blocked'}
    strict = args.cases.name in {'input_sweep_extra.jsonl', 'classifier_regions_sweep.jsonl'}
    assert cases
    expected_count = {'input_sweep.jsonl': 48, 'address_sweep.jsonl': 6,
                      'input_sweep_extra.jsonl': 24, 'classifier_regions_sweep.jsonl': 20}
    assert len(cases) == expected_count[args.cases.name]
    assert all(set(c['expect'] if isinstance(c['expect'], list) else [c['expect']]) <= allowed for c in cases)
    semaphore = asyncio.Semaphore(args.concurrency)
    async def case_run(number, case):
        async with semaphore:
            attempts = [await execute(case, number, repeat) for repeat in (1, 2)]
        passed = (all if strict else any)(attempt['pass'] for attempt in attempts)
        actual = ', '.join(a['actual'] for a in attempts)
        reason = '' if passed else '; '.join(
            f"{a['actual']} category={a.get('result', {}).get('data', {}).get('category')} llm={a['llm_completed']}"
            for a in attempts)
        row = {'id': number, **case, 'pass': passed, 'reason': reason, 'attempts': attempts}
        print(f"[TEST] {number:02} | {'PASS' if passed else 'FAIL'} | {case['expect']} | {actual} | {reason}".rstrip(), flush=True)
        return row
    rows = await asyncio.gather(*(case_run(i, case) for i, case in enumerate(cases, 1)))
    count = sum(row['pass'] for row in rows)
    report = {'cases_sha256': hashlib.sha256(args.cases.read_bytes()).hexdigest(),
              'model': config.OPENAI_MODEL, 'fallback_model': config.OPENAI_FALLBACK_MODEL,
              'passed': count, 'total': len(rows), 'rows': rows}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    lines = ['| # | 입력 | 기대 | 실행 1 / 2 | 결과 | 실패 사유 |', '|---|---|---|---|---|---|']
    for row in rows:
        actual = ' / '.join(a['actual'] for a in row['attempts'])
        excerpt = row['text'].replace('|', '\\|').replace('\n', ' ')[:100]
        lines.append(f"| {row['id']} | {excerpt} | {row['expect']} | {actual} | {'PASS' if row['pass'] else 'FAIL'} | {row['reason']} |")
    args.output.with_suffix('.md').write_text('\n'.join(lines) + '\n')
    print(f'[TEST] TOTAL {count}/{len(rows)} ({count/len(rows):.1%}); {args.output}', flush=True)
    return 0 if count == len(rows) else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cases', type=Path, default=ROOT / 'scripts/tests/input_sweep.jsonl')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--concurrency', type=int, default=3)
    logging.basicConfig(level=logging.WARNING, format='[TEST] %(levelname)s %(message)s')
    raise SystemExit(asyncio.run(main(parser.parse_args())))
