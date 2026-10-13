"""동작구 주소의 결정적 스키마 검증. 교정 후보는 사용자 확인 전 적용하지 않는다."""
import csv
import re
from pathlib import Path
from app.core.config import CONTRACTS
from app.services.josa import with_josa

SEEDS = Path('/seeds') if Path('/seeds').is_dir() else CONTRACTS.parent / 'database/seeds'
REFERENCE = SEEDS / 'reference/complaint_routing'
with (REFERENCE / 'dongjak_administrative_dongs.csv').open(encoding='utf-8-sig') as stream:
    DONGS = {row['dong_name'] for row in csv.DictReader(stream)}
with (REFERENCE / 'dongjak_dong_mapping.csv').open(encoding='utf-8-sig') as stream:
    DONGS.update(row['legal_dong'] for row in csv.DictReader(stream))


def distance_one(left, right):
    if len(left) == len(right):
        return sum(a != b for a, b in zip(left, right)) == 1
    shorter, longer = sorted((left, right), key=len)
    return len(longer) == len(shorter) + 1 and any(
        longer[:i] + longer[i + 1:] == shorter for i in range(len(longer)))


def validate_dong(data, inputs, conflict=None):
    from app.services.rules import SIDOS, extract_region, get_location_answer, has_confirmation
    region = data['region']
    if region['sigungu'] != '동작구' or SIDOS.get(region['sido'], region['sido']) not in (None, '서울특별시'):
        return data, conflict
    answer = get_location_answer(inputs)
    source = answer if answer is not None else '\n'.join(
        [inputs['text'], inputs.get('answers', {}).get('q_what', '')])
    stated = extract_region(source)['dong']
    if not stated and region['dong'] in DONGS and re.search(r'(?<![가-힣])' + re.escape(region['dong'][:-1]) + r'(?![가-힣]|\s*역)', source):
        stated = region['dong']
    if (not stated or stated in DONGS) and region['dong'] in DONGS:
        return data, conflict
    candidates = [dong for dong in DONGS if stated and distance_one(stated, dong)]
    target = candidates[0] if len(candidates) == 1 else (
        (conflict or {}).get('dong') or region['dong'])
    target = target if target in DONGS else None
    if target and has_confirmation(inputs):
        region['dong'] = target
        location = data['details']['location_detail'] or target
        location = location.replace(stated, target) if stated else location
        data['details']['location_detail'] = region['place_text'] = location
        return data, None
    # 유효하지 않은 동은 질문 소진 뒤의 구 단위 안내에도 남기지 않는다.
    region['dong'] = None
    if stated:
        for container, field in ((region, 'place_text'), (data['details'], 'location_detail')):
            container[field] = (container[field] or '').replace(stated, '').strip() or None
    if target:
        query = (conflict or {}).get('query') or region['place_text'] or '확인된 위치'
        message = f'{target} 말씀이신가요?' if len(candidates) == 1 else (
            f'말씀하신 {with_josa(stated, "은/는")} 동작구에 없어요. {query} 기준 {with_josa(target, "으로/로")} 안내할까요?'
            if stated else f'{query} 기준 {with_josa(target, "으로/로")} 안내할까요?')
        return data, {'kind': 'dong', 'text': message + " 맞으면 '네', 아니면 정확한 동을 알려 주세요"}
    if conflict:
        return data, conflict
    if stated or data['details']['location_detail']:
        return data, {'text': '동작구의 어느 동인가요?'}
    return data, None
