"""개인정보를 지우기 전에 문구의 값 영역을 순수 규칙으로 검사한다."""
import csv
import re
from pathlib import Path
from app.core.config import CONTRACTS
from app.services.pii import PATTERNS

# verifier의 키워드와 동일하게 유지한다(서비스 간 import 금지).
KEYWORDS = {
    "streetlight": ("가로등", "보안등", "조명", "전등"),
    "road_damage": ("도로", "포트홀", "파손", "패임", "파임", "보도블록", "균열", "싱크홀"),
    "illegal_dumping": ("쓰레기", "무단투기", "불법투기", "폐기물"),
}
SEEDS = Path('/seeds') if Path('/seeds').is_dir() else CONTRACTS.parent / 'database/seeds'
REFERENCE = SEEDS / 'reference/complaint_routing'
with (REFERENCE / 'dongjak_administrative_dongs.csv').open(encoding='utf-8-sig') as stream:
    DONGS = {row['dong_name'] for row in csv.DictReader(stream)}
with (REFERENCE / 'dongjak_dong_mapping.csv').open(encoding='utf-8-sig') as stream:
    DONGS.update(row['legal_dong'] for row in csv.DictReader(stream))


def validate_domain(data, inputs):
    title, body = data['title'], data['body']
    text = title + '\n' + body
    dong = inputs['region']['dong']
    names = re.findall(r'(?<![가-힣])([가-힣]{1,8}\d*동)(?=\s|[,.]|[은는이가에의을를]|$)', text)
    names = set(names) & DONGS
    checks = {
        'CATEGORY_WORD': inputs['category'] not in KEYWORDS or any(word in text for word in KEYWORDS[inputs['category']]),
        'DONG': all(re.sub(r'\d+', '', name) == re.sub(r'\d+', '', dong or '') for name in names),
        'PII': not any(pattern.search(str(data)) for _, pattern in PATTERNS),
        'LENGTH': bool(title.strip() and body.strip()) and len(title) <= 200 and len(body) <= 5000,
    }
    return [code for code, valid in checks.items() if not valid]
