import asyncio
import csv
import httpx
import pytest
from app.services import channel


@pytest.mark.parametrize('dong,category,expected', [('상도동', 'streetlight', True),
    ('상도1동', 'road_damage', True), ('사당동', 'streetlight', False),
    ('상도동', 'out_of_scope', False)])
def test_center_scope_keeps_official_and_link_checks(dong, category, expected):
    with (channel.CONTRACTS.parent / 'database/seeds/channels.csv').open() as stream:
        row = next(row for row in csv.DictReader(stream) if row['channel_id'] == 'dj-dong-상도1동-01')
    fields = ('channel_id', 'name', 'org', 'method', 'url', 'jurisdiction', 'verified_at', 'source_url')
    item = {key: row[key] for key in fields}
    seen = []
    def transport(request):
        seen.append(str(request.url))
        return httpx.Response(200)
    result = asyncio.run(channel.check({'category': category, 'region': {
        'sido': '서울특별시', 'sigungu': '동작구', 'dong': dong, 'place_text': None},
        'channels': [item]}, httpx.MockTransport(transport)))
    assert bool(result['passed_channel_ids']) == expected
    assert seen == [row['source_url']]
    assert all(c['ok'] for c in result['checks'] if c['name'] in ('official', 'link'))
