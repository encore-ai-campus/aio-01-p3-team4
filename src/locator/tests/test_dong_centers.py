import pytest
from app.core.config import SEEDS_DIR
from app.services.catalog import load_catalog, seed_paths, select_channels


def select(dong='상도동', **overrides):
    inputs = {'category': 'streetlight', 'region': {'sido': '서울특별시', 'sigungu': '동작구',
              'dong': dong, 'place_text': None}, 'exclude_channel_ids': []}
    inputs.update(overrides)
    return select_channels(load_catalog(seed_paths(SEEDS_DIR)), inputs, include_phone=True)


@pytest.mark.parametrize('dong,expected', [
    ('상도동', ['상도1동', '상도2동']), ('사당동', ['사당1동', '사당2동']),
    ('노량진동', ['노량진1동', '노량진2동']), ('신대방동', ['신대방1동', '신대방2동']),
    ('흑석동', ['흑석동']), ('대방동', ['대방동']), ('상도4동', ['상도4동']),
])
def test_district_order_then_centers(dong, expected):
    result = select(dong)
    assert result['fallback_level'] == 'district'
    before = select(None)['channels']
    assert result['channels'][:len(before)] == before
    assert before[0]['channel_id'] == 'dj-streetlight-01'
    assert [row['jurisdiction'] for row in result['channels'][len(before):]] == expected
    assert all('가까운 접수 창구' in row['name'] for row in result['channels'][len(before):])


def test_numeric_dong_in_address_and_exclusion():
    result = select(region={'sido': '서울특별시', 'sigungu': '동작구', 'dong': '상도동',
                            'place_text': '상도3동 주민센터 앞'})
    assert [r['jurisdiction'] for r in result['channels'] if r['channel_id'].startswith('dj-dong-')] == ['상도3동']
    result = select(exclude_channel_ids=['dj-dong-상도1동-01'])
    assert [r['jurisdiction'] for r in result['channels'][3:]] == ['상도2동', '상도3동']


def test_no_centers_on_outside_or_fallback():
    assert all(not r['channel_id'].startswith('dj-dong-') for r in select(category='out_of_scope')['channels'])
    rows = load_catalog(seed_paths(SEEDS_DIR))
    result = select(exclude_channel_ids=[r['channel_id'] for r in rows if r['jurisdiction'] == '동작구'])
    assert result['fallback_level'] == 'city'
    assert all(not r['channel_id'].startswith('dj-dong-') for r in result['channels'])
