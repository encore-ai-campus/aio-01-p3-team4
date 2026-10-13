"""사람이 편집한 초안의 규칙 기반 첨삭. 서비스 간 계약과 독립적인 백엔드 응답."""
import asyncio
import re
from difflib import SequenceMatcher
from psycopg.types.json import Jsonb
from app.clients import agents, store
from app.schemas.contract import Handoff, StageResult
from app.services.supervisor import CATEGORY_LABELS

# verifier.services.draft의 검사와 동일하게 유지한다(서비스 간 import 금지).
PII = {
    "휴대전화": r"(?<!\d)01[016789][-.\s]?\d{3,4}[-.\s]?\d{4}(?!\d)",
    "유선전화": r"(?<!\d)0(?:2|[3-6][1-5]|70)[-.\s]?\d{3,4}[-.\s]?\d{4}(?!\d)",
    "주민등록번호": r"(?<!\d)\d{6}[-\s]?[1-8]\d{6}(?!\d)",
    "이메일": r"[a-z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-z0-9-]+(?:\.[a-z0-9-]+)+",
    "상세 주소": r"(?<![\d가-힣])\d+동\s*\d+호(?![\d선])",
}
KEYWORDS = {
    "streetlight": ("가로등", "보안등", "조명", "전등"),
    "road_damage": ("도로", "포트홀", "파손", "패임", "파임", "보도블록", "균열", "싱크홀"),
    "illegal_dumping": ("쓰레기", "무단투기", "불법투기", "폐기물"),
}
DOMAIN = r"(?<![@a-z0-9._-])(?:https?://)?((?:[a-z0-9-]+\.)+[a-z]{2,})(?:/\S*)?"
OFFICIAL_SUFFIXES = ("go.kr", "gov.kr", "120dasan.or.kr")


def issue(kind, field, message, start=None, end=None, replacement=None):
    return dict(id="", kind=kind, field=field, start=start, end=end,
                severity="block" if kind in ("pii", "link") else "warn", message=message,
                fix=None if replacement is None else {"replacement": replacement})


def apply_fixes(draft, issues):
    fixed = dict(draft)
    for field in ("title", "body"):
        spans, boundary = [], -1
        candidates = [i for i in issues if i["field"] == field and i["fix"] and i["start"] is not None]
        # 겹치면 바깥 문장의 원문 복원을 우선하며 동일 위치를 두 번 고치지 않는다.
        for item in sorted(candidates, key=lambda i: (i["start"], -i["end"])):
            if item["start"] >= boundary:
                spans.append(item)
                boundary = item["end"]
        for item in reversed(spans):
            fixed[field] = fixed[field][:item["start"]] + item["fix"]["replacement"] + fixed[field][item["end"]:]
        if spans:
            fixed[field] = re.sub(r"[ \t]{2,}", " ", fixed[field]).strip()
    for item in issues:
        if item["kind"] == "region_missing" and item["fix"]:
            region = item["fix"]["replacement"].strip()
            if region not in fixed["title"] + fixed["body"]:
                fixed["body"] = region + " " + fixed["body"]
    return fixed


def check(result, title, body, details=None):
    draft, original, issues = {"title": title, "body": body}, result.get("draft", {}), []
    if all(draft[k] == original.get(k) for k in draft):
        return dict(verdict="ok", issues=[], fixed=draft, verifier_checked=False)
    text = title + "\n" + body
    places = list(dict.fromkeys(value for value in [*(result.get("region", {}).get(k) for k in
        ("sigungu", "dong", "place_text")), (details or {}).get("location_detail")] if value))
    for field, value in draft.items():
        for label, pattern in PII.items():
            for match in re.finditer(pattern, value, re.I):
                issues.append(issue("pii", field, f"{label}가 들어 있어요. 신고 문구에 개인정보는 넣지 않아요",
                                    match.start(), match.end(), ""))
        # 이메일 길이를 보존해 나머지 링크 span이 원문 인덱스와 같게 한다.
        without_emails = re.sub(PII["이메일"], lambda m: " " * len(m[0]), value, flags=re.I)
        for match in re.finditer(DOMAIN, without_emails, re.I):
            host = match[1].lower().rstrip(".")
            if not any(host == suffix or host.endswith("." + suffix) for suffix in OFFICIAL_SUFFIXES):
                issues.append(issue("link", field, "공식 주소가 아닌 링크가 있어요. 이 링크는 지워 주세요",
                                    match.start(), match.end(), ""))
        before = list(re.finditer(r"[^.!?\n]+[.!?]?", original.get(field, "")))
        after = list(re.finditer(r"[^.!?\n]+[.!?]?", value))
        matcher = SequenceMatcher(None, [m[0] for m in before], [m[0] for m in after], autojunk=False)
        for tag, a, b, c, d in matcher.get_opcodes():
            if tag == "equal":
                continue
            for offset in range(max(b - a, d - c)):
                old = before[a + offset][0] if a + offset < b else ""
                new = after[c + offset] if c + offset < d else None
                sentence = new[0] if new else ""
                start, end = (new.start(), new.end()) if new else ((after[c].start(),) * 2 if c < len(after) else (len(value),) * 2)
                changed_place = any((place in old) != (place in sentence) for place in places)
                changed_number = re.findall(r"\d+(?:[.,]\d+)*", old) != re.findall(r"\d+(?:[.,]\d+)*", sentence)
                for kind, changed, label in (("place_changed", changed_place, "장소"), ("number_changed", changed_number, "숫자")):
                    if changed and old.strip():
                        issues.append(issue(kind, field, f"원래 민원에선 '{old.strip()}'이었어요. {label}가 달라졌어요",
                                            start, end, old))
                    elif changed:
                        # 사람이 새로 넣은 문장은 되돌릴 원문이 없으니 알려만 주고 고치기 버튼은 주지 않는다.
                        issues.append(issue(kind, field, f"원래 민원에 없던 {label}가 들어갔어요: '{sentence.strip()}'",
                                            start, end))
    missing = [p for p in places if p in original.get("title", "") + original.get("body", "") and p not in text]
    if missing:
        issues.append(issue("region_missing", "body", "원래 민원의 지역이 빠졌어요", replacement=" ".join(missing) + " "))
    category = result.get("category")
    if category in KEYWORDS and not any(word in text for word in KEYWORDS[category]):
        issues.append(issue("category_missing", "body", f"{CATEGORY_LABELS[category]} 민원이라는 말이 빠졌어요"))
    for number, item in enumerate(issues, 1):
        item["id"] = f"i{number}"
    return dict(verdict="block" if any(i["severity"] == "block" for i in issues) else "warn" if issues else "ok",
                issues=issues, fixed=apply_fixes(draft, issues), verifier_checked=False)


async def review(job, result, title, body, index=0):
    classifications = job["state"].get("classifications") or [job["state"].get("classification", {})]
    details = classifications[index].get("details") if index < len(classifications) else None
    response = check(result, title, body, details)
    handoff = Handoff.model_validate({"task_id": job["id"], "from": "supervisor", "to": "verifier",
        "objective": "사람이 고친 문구 재검증", "inputs": {"mode": "draft", "category": result["category"],
        "region": result["region"], "draft": {**result["draft"], "title": title, "body": body}},
        "constraints": ["개인정보와 비공식 링크 검사"], "attempt": 1, "last_failure": None, "memory_refs": []})
    try:
        verified = StageResult.model_validate(await asyncio.wait_for(agents.call(handoff), timeout=2)).model_dump()
        response["verifier_checked"] = verified["author"] == "verifier" and verified["failure"] != "SERVICE_UNAVAILABLE"
        if verified["failure"] == "PII_IN_DRAFT" and not any(i["kind"] == "pii" for i in response["issues"]):
            item = issue("pii", "body", "검증기가 개인정보를 발견했어요. 개인 연락처나 주소를 지워 주세요")
            item["id"] = f'i{len(response["issues"]) + 1}'
            response["issues"].append(item)
            response["verdict"] = "block"
    except Exception:
        verified = dict(task_id=job["id"], author="backend", status="retry", data={}, failure="SERVICE_UNAVAILABLE", incident_id=None)
    await store.query("INSERT INTO timeline_events(job_id,agent,event_type,attempt,payload) VALUES (%s,%s,%s,%s,%s)",
                      (job["id"], "verifier", verified["status"], 1, Jsonb({**verified, "review": True, "index": index})))
    return response
