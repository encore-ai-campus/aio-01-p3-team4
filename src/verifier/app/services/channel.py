import asyncio
import csv
import re
from pathlib import Path
from datetime import date, datetime, timedelta, timezone
from urllib.parse import unquote, urlsplit
import httpx
from app.clients import links
from app.core.config import CONTRACTS

SEEDS = Path("/seeds") if Path("/seeds").is_dir() else CONTRACTS.parent / "database/seeds"
with (SEEDS / "reference/complaint_routing/dongjak_dong_mapping.csv").open(encoding="utf-8-sig") as stream:
    DONG_MAPPING = {row["legal_dong"]: row["administrative_dongs"].split("|") for row in csv.DictReader(stream)}
ADMIN_DONGS = {dong for dongs in DONG_MAPPING.values() for dong in dongs}

KST = timezone(timedelta(hours=9))
OFFICIAL_SUFFIXES = (
    "go.kr",  # 정부기관 공식 2단계 도메인
    "gov.kr",  # 정부24: 행정안전부 운영
    "120dasan.or.kr",  # 서울특별시 120다산콜재단
)


def today() -> date:
    return datetime.now(KST).date()


def is_official(url) -> bool:
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower().rstrip(".")
    return parsed.scheme in ("http", "https") and any(
        host == suffix or host.endswith("." + suffix) for suffix in OFFICIAL_SUFFIXES
    )


def check_url(item) -> str:
    return item["source_url"] if item["method"] == "phone" else item["url"]


async def check(inputs, transport=None, *, break_links=False):
    from app.services.draft import PII

    async with httpx.AsyncClient(
        transport=transport if transport is not None else links.TRANSPORT,
        timeout=5.0, follow_redirects=True, headers={"User-Agent": "minwon-verifier/1.0"},
    ) as client:
        async def inspect(item):
            checks, codes = [], []

            def record(name, ok, detail, code):
                checks.append({"name": name, "ok": ok, "detail": f"{item['channel_id']} · {detail}"})
                if not ok:
                    codes.append(code)

            url = check_url(item)
            telephone = item["method"] == "phone"
            valid_phone = not telephone or bool(re.fullmatch(r"tel:\+?[0-9][0-9-]{2,19}", item["url"]))
            official = valid_phone and is_official(url)
            record("official", official, "공식 주소" if official else "비공식 주소", "NOT_OFFICIAL")
            alive = False
            if official:
                if break_links:
                    parsed = urlsplit(url)
                    url = parsed._replace(netloc=f"{parsed.hostname}.invalid").geturl()
                alive, detail = await links.check_link(client, url)
                if telephone:
                    detail = "전화번호 공식 출처 " + detail
                record("link", alive, detail, "LINK_DEAD")
            age_days = (today() - date.fromisoformat(item["verified_at"])).days
            fresh = age_days <= 90
            record("fresh", fresh, "확인일 90일 이내" if fresh else
                   f"확인일 {item['verified_at']} ({age_days}일 전, 기준 90일)", "STALE_INFO")
            region = inputs["region"]
            # '동작구'·'서울특별시 동작구' 둘 다 받는다. 시 단위 창구는 sido와 같아야 한다.
            jurisdiction = item["jurisdiction"].strip()
            region_ok = (jurisdiction in ("전국", region["sido"])
                         or bool(region["sigungu"]) and jurisdiction.endswith(region["sigungu"]))
            parts = item["channel_id"].rsplit("-", 2)
            category_ok = (len(parts) != 3 or not all(parts) or not parts[-1].isdigit()
                           or parts[-2] in ("all", inputs["category"]))
            if item["channel_id"].startswith("dj-dong-"):
                dong = region.get("dong")
                dongs = [dong] if dong in ADMIN_DONGS else DONG_MAPPING.get(dong, [])
                region_ok = (region["sido"] == "서울특별시" and region["sigungu"] == "동작구"
                             and jurisdiction in dongs)
                category_ok = (inputs["category"] != "out_of_scope"
                               and item["channel_id"] == f"dj-dong-{jurisdiction}-01")
            scope = region_ok and category_ok
            record("scope", scope, "지역·유형 일치" if scope else "관할 불일치: 지역 또는 유형", "NOT_OFFICIAL")
            blocker = "NOT_OFFICIAL" if not official else "LINK_DEAD" if not alive else None
            description = f"{item['name']} · {url}"
            evidence = {}
            failed_checks = [c["detail"] for c in checks if not c["ok"]]
            if failed_checks:
                evidence["broken"] = description + " · " + "; ".join(failed_checks)
            if official and alive and scope:
                evidence["fixed"] = description + (" · 검증 통과" if fresh else " · 확인 필요 표시를 붙여 안내")
            return checks, codes, official and alive and scope, fresh, blocker, evidence

        results = await asyncio.gather(*(inspect(item) for item in inputs["channels"]))
    passed = [item["channel_id"] for item, result in zip(inputs["channels"], results) if result[2]]
    reasons = list(dict.fromkeys(code for result in results for code in result[1]))
    if passed:
        failure = "STALE_INFO" if any(result[2] and not result[3] for result in results) else None
    else:
        failure = next((result[4] for result in results if result[4]), "NOT_OFFICIAL")
        if failure not in reasons:
            reasons.append(failure)
    def summary(key):
        # 화면 카드에는 대표 창구 하나만(확인 필요 표시가 붙은 창구 우선), 나머지는 개수로
        items = sorted((r[5][key] for r in results if key in r[5]), key=lambda text: "확인 필요" not in text)
        return unquote(items[0]) + (f" 외 {len(items) - 1}곳" if len(items) > 1 else "")
    evidence = {key: summary(key) for key in ("broken", "fixed") if any(key in r[5] for r in results)}
    for key, text in evidence.items():
        text = re.sub(r"[\w.+-]+@[\w.-]+\.\w+", "[개인정보 삭제]", text)
        for pattern in PII.values():
            text = re.sub(pattern, "[개인정보 삭제]", text, flags=re.IGNORECASE)
        evidence[key] = " ".join(text.split())[:500]
    return {"failure": failure, "checks": [c for r in results for c in r[0]],
            "reasons": reasons, "passed_channel_ids": passed,
            **({"evidence": evidence} if evidence else {})}
