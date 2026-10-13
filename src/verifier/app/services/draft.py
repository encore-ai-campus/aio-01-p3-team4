import re
from app.services.channel import is_official

PII = {
    "휴대전화": r"(?<!\d)01[016789][-.\s]?\d{3,4}[-.\s]?\d{4}(?!\d)",
    "유선전화": r"(?<!\d)0(?:2|[3-6][1-5]|70)[-.\s]?\d{3,4}[-.\s]?\d{4}(?!\d)",
    "주민등록번호": r"(?<!\d)\d{6}[-\s]?[1-8]\d{6}(?!\d)",
    "이메일": r"[a-z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-z0-9-]+(?:\.[a-z0-9-]+)+",
    # 집 호수만 개인정보로 본다. 번지는 고장 위치(예: 123-4번지 앞 가로등)라서 문구에 남아도 된다.
    "상세 주소": r"(?<![\d가-힣])\d+동\s*\d+호(?![\d선])",
}
KEYWORDS = {
    "streetlight": ("가로등", "보안등", "조명", "전등"),
    "road_damage": ("도로", "포트홀", "파손", "패임", "파임", "보도블록", "균열", "싱크홀"),
    "illegal_dumping": ("쓰레기", "무단투기", "불법투기", "폐기물"),
}
DOMAIN = r"(?<![@a-z0-9._-])(?:https?://)?((?:[a-z0-9-]+\.)+[a-z]{2,})(?:/\S*)?"


def check(inputs):
    text = inputs["draft"]["title"] + "\n" + inputs["draft"]["body"]
    kinds = [kind for kind, pattern in PII.items() if re.search(pattern, text, re.IGNORECASE)]
    checks = [{"name": "pii", "ok": not kinds,
               "detail": "개인정보 감지: " + ", ".join(kinds) if kinds else "개인정보 없음"}]
    category = inputs["category"]
    if category != "out_of_scope":
        ok = any(word in text for word in KEYWORDS[category])
        checks.append({"name": "category", "ok": ok,
                       "detail": "유형 키워드 일치" if ok else "유형 키워드 불일치"})
    regions = [inputs["region"][key] for key in ("sigungu", "dong") if inputs["region"][key]]
    if regions:
        ok = any(region in text for region in regions)
        checks.append({"name": "region", "ok": ok,
                       "detail": "지역 일치" if ok else "지역 불일치"})
    without_emails = re.sub(PII["이메일"], "", text, flags=re.IGNORECASE)
    ok = all(is_official("https://" + host) for host in re.findall(DOMAIN, without_emails, re.IGNORECASE))
    checks.append({"name": "links", "ok": ok,
                   "detail": "문구 링크 공식 주소" if ok else "문구에 비공식 링크 포함"})
    reasons = (["PII_IN_DRAFT"] if kinds else [])
    if any(not c["ok"] for c in checks if c["name"] != "pii"):
        reasons.append("DRAFT_MISMATCH")
    return {"failure": reasons[0] if reasons else None, "checks": checks,
            "reasons": reasons, "passed_channel_ids": []}
