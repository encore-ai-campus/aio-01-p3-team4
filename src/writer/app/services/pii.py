"""개인정보 값을 응답에 다시 노출하지 않고 종류만 기록한다."""
import re

PATTERNS = [
    ("주민등록번호", re.compile(r"(?<!\d)\d{6}[-\s]?[1-8]\d{6}(?!\d)")),
    ("전화번호", re.compile(r"(?<!\d)(?:01[016789]|02|0[3-6]\d|070|050\d)[-.\s]?\d{3,4}[-.\s]?\d{4}(?!\d)")),
    ("이메일", re.compile(r"[a-z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-z0-9-]+(?:\.[a-z0-9-]+)+", re.I)),
    ("상세 집주소", re.compile(r"(?<![\d가-힣])\d+\s*동\s*\d+\s*호(?![\d선])")),
    ("상세 집주소", re.compile(r"(?<![가-힣])(?:우리\s*집|집|자택|거주지|사는\s*곳|사는)[^,.\n]*?\d+\s*(?:층|호)(?![\d선])(?:\s*\d+\s*호(?![\d선]))?")),
    ("상세 집주소", re.compile(
        r"(?:집\s*주소|거주지|자택)(?:는|은|:)?\s*[^,.\n]*?(?:[가-힣]+(?:로|길|동)\s*\d+(?:-\d+)?)[^,.\n]*"
    )),
]


def redact_text(text):
    removed = []
    for label, pattern in PATTERNS:
        if pattern.search(text):
            text = pattern.sub("[개인정보 삭제]", text)
            if label not in removed:
                removed.append(label)
    return text, removed


def redact(value):
    """중첩 입력·출력의 문자열을 정리한다. 원본 객체는 변경하지 않는다."""
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, (dict, list)):
        cleaned = {} if isinstance(value, dict) else []
        removed = []
        entries = value.items() if isinstance(value, dict) else enumerate(value)
        for key, item in entries:
            new_item, labels = redact(item)
            if isinstance(cleaned, dict):
                cleaned[key] = new_item
            else:
                cleaned.append(new_item)
            for label in labels:
                if label not in removed:
                    removed.append(label)
        return cleaned, removed
    return value, []
