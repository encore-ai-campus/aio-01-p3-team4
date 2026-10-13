import ast
import re
from app.core.config import CONTRACTS
from app.services.domain import KEYWORDS
from app.services.pii import redact


def test_writer_covers_verifier_and_review_pii_without_erasing_public_locations():
    copies = []
    for service, filename in [('verifier', 'draft.py'), ('backend', 'review.py')]:
        source = CONTRACTS.parent / 'src' / service / 'app/services' / filename
        values = {node.targets[0].id: ast.literal_eval(node.value) for node in ast.parse(source.read_text()).body
                  if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)
                  and node.targets[0].id in ('PII', 'KEYWORDS')}
        assert values['KEYWORDS'] == KEYWORDS
        copies.append(values['PII'])
    assert copies[0] == copies[1]
    samples = [
        ('사당1동 4호선 사당역 10번 출구 앞', False, False), ('101동 1203호', True, True),
        ('1203호로', False, False), ('101동 1203호로', True, True), ('101동 1203호이고', True, True),
        ('101동 1203호(', True, True), ('010.1234.5678', True, True), ('031.123.4567', True, True),
        ('지하 1층 주차장 입구', False, False), ('상가 3층', False, False), ('102호 앞', False, False),
        ('보안등 5호', False, False), ('상도로 15', False, False), ('123-4번지 앞', False, False), ('맛집 3층', False, False),
        ('우리 집 3층 1203호로', True, False), ('자택 1203호이고', True, False), ('제가 사는 102호(', True, False),
        ('010 1234 5678', True, True), ('0212345678', True, True), ('900101-1234567', True, True),
        ('USER!#$%&\'*+/=?^_`{|}~@EXAMPLE.C', True, True), ('A.user@example.com', True, True),
    ]
    prefixes = ['01' + n for n in '016789'] + ['02', '070'] + ['0' + a + b for a in '3456' for b in '12345']
    samples += [(prefix + sep + middle + sep + '5678', True, True)
                for prefix in prefixes for sep in ('', '-', '.', ' ') for middle in ('123', '1234')]
    for text, removed, detected in samples:
        clean, labels = redact(text)
        assert (clean != text) == bool(labels) == removed, text
        for patterns in copies:
            assert any(re.search(p, text, re.I) for p in patterns.values()) == detected, text
            assert not any(re.search(p, clean, re.I) for p in patterns.values()), text
