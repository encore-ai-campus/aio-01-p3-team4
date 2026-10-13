"""개인정보 값을 응답에 다시 노출하지 않고 종류만 기록한다."""
import re

# writer.pii 사본 + 입력 단계 패턴. classifier의 사본도 함께 맞춘다.
TITLES = "기사|주민|선생|고객|직원|공무원|담당자|관계자|관리자|경비|이웃|회원|손님|부모|어머|아버|형|누나|언니|사모|주무관|미화원|환경미화원|경찰관|경찰|소방관|의원|대표"
SURNAMES = "김이박최정강조윤장임한오서신권황안송전홍유고문양손배백허남심노하곽성차주우구민진나지엄채원천방공현함변염여추도소석선설마길연위표명기반왕금옥육인맹제모탁국어은편용"
PATTERNS = [
    ("이름", re.compile(r"(?<![가-힣])(?!(?:" + TITLES + r")님)(?![가-힣]*장님)[가-힣]{2,3}님")),
    ("이름", re.compile(r"저는\s*[" + SURNAMES + r"][가-힣]{2}(?:이고|고|입니다|이에요|예요|인데|이며)")),
    ("카드번호", re.compile(r"(?<!\d)\d{4}(?:[- ]?\d{4}){3}(?!\d)")),
    ("상세 집주소", re.compile(r"(?<!\d)\d+[-]\d+\s*호")),
    ("주민등록번호", re.compile(r"(?<!\d)\d{6}[-\s]?[1-8]\d{6}(?!\d)")),
    ("전화번호", re.compile(r"(?<!\d)(?:01[016789]|02|0[3-6]\d|070|050\d)[-.\s]?\d{3,4}[-.\s]?\d{4}(?!\d)")),
    ("전화번호", re.compile(r"(?<!\d)\d{3,4}[-.]\d{4}(?!\d)")),
    ("이메일", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
    ("상세 집주소", re.compile(r"\d+\s*동\s*\d+\s*호(?=$|[\s,.]|에서|에|입니다|를)")),
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
