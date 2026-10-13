"""지도 검색과 번들 역 데이터로 장소의 관할 지역을 확인한다."""
import asyncio
import json
import logging
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import httpx

from app.core import config
from app.services.rules import (
    DONGS, SIDOS, extract_region, get_location_answer, has_confirmation, strip_region_prefix,
)

CATEGORIES = {
    "지하철역", "공공기관", "학교", "문화시설", "관광명소", "대형마트",
    "병원", "은행", "주차장", "어린이집", "유치원",
}
VAGUE_LOCATIONS = {
    "어딘가", "어디", "어느", "여기", "거기", "저기", "그곳", "이곳", "근처", "주변",
    "부근", "인근", "쪽", "어딘지", "어디선가",
}
GENERIC_PLACES = set("""
인도 차도 보도 도로 골목 골목길 길 횡단보도 정류장 버스정류장 사거리 교차로 육교 지하도
공원 놀이터 주차장 아파트 빌라 상가 편의점 학교 초등학교 중학교 고등학교 집 동네
역 지하철역 병원 도서관 소방서 주민센터 어린이집 유치원 보도블록
""".split())
STATIONS = json.loads((Path(__file__).parents[1] / "data/stations.json").read_text(encoding="utf-8"))
LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class PlaceHit:
    sido: str
    sigungu: str
    place_name: str
    source: Literal["kakao", "offline"]
    dong: str | None = None


def extract_place_query(place_text, region) -> str | None:
    text = (place_text or region.get("place_text") or region.get("dong") or "").strip()
    for source in (place_text, region.get("place_text")):
        landmark = re.search(r"(?<![가-힣])([가-힣A-Za-z0-9·-]+(?:\s*역|소방서|주민센터|학교|병원|공원|도서관))", strip_region_prefix(source or ""))
        if landmark:
            query = re.sub(r"\s+역$", "역", landmark.group(1))
            if query not in GENERIC_PLACES:
                return query
    text = re.sub(r"(?:\s*(?:맞은편|횡단보도|교차로|사거리|입구|(?:\d+번\s*)?출구|근처|앞|옆))+$", "", text)
    query = strip_region_prefix(text) or next((part for part in reversed(text.split())
        if part not in DONGS and re.fullmatch(r"[가-힣]+\d*동", part)), "")
    if (len(query) < 2 or all(part in VAGUE_LOCATIONS | GENERIC_PLACES for part in query.split()) or
            re.fullmatch(r"(?:우리\s*)?(?:집|동네|길)", query)):
        return None
    return re.sub(r"([가-힣]+)\s+역", r"\1역", query)


async def _fetch_kakao(query):
    async with httpx.AsyncClient(timeout=config.PLACE_LOOKUP_TIMEOUT_SECONDS) as client:
        response = await client.get(
            "https://dapi.kakao.com/v2/local/search/keyword.json",
            params={"query": query, "x": 126.9393, "y": 37.5124, "size": 5},
            headers={"Authorization": f"KakaoAK {config.KAKAO_REST_KEY}"},
        )
        response.raise_for_status()
        return response.json()["documents"]


async def _fetch_kakao_address(query):
    async with httpx.AsyncClient(timeout=config.PLACE_LOOKUP_TIMEOUT_SECONDS) as client:
        response = await client.get(
            "https://dapi.kakao.com/v2/local/search/address.json",
            params={"query": query, "size": 5},
            headers={"Authorization": f"KakaoAK {config.KAKAO_REST_KEY}"},
        )
        response.raise_for_status()
        return response.json()["documents"]


async def lookup_dong(query) -> PlaceHit | None:
    if not config.KAKAO_REST_KEY:
        return None
    try:
        documents = await asyncio.wait_for(_fetch_kakao_address(query), config.PLACE_LOOKUP_TIMEOUT_SECONDS)
        regions = Counter()
        for document in documents:
            if document.get("address_type") != "REGION":
                continue
            address = document.get("address") or {}
            sido, sigungu = address.get("region_1depth_name"), address.get("region_2depth_name")
            if sido and sigungu:
                regions[(SIDOS.get(sido, sido), sigungu)] += 1
        if not regions:
            return None
        region = ("서울특별시", "동작구")
        if region not in regions:
            region, count = regions.most_common(1)[0]
            if count < 2 and sum(regions.values()) != 1:
                return None
        return PlaceHit(*region, query, "kakao")
    except (httpx.HTTPError, TimeoutError, ValueError, KeyError, TypeError) as error:
        LOGGER.warning("동 주소 검증 실패: %s, 확인 가능한 지역 없음", type(error).__name__)
        return None


def _in_dongjak(hit):
    return hit.sido == "서울특별시" and hit.sigungu == "동작구"


def _offline(query):
    regions = {tuple(region) for region in STATIONS.get(query.removesuffix("역"), [])}
    if ("서울특별시", "동작구") in regions:
        region = ("서울특별시", "동작구")
    elif len(regions) == 1:
        region = next(iter(regions))
    else:
        return None
    return PlaceHit(SIDOS.get(region[0], region[0]), region[1], query, "offline")


async def lookup(query) -> PlaceHit | None:
    if re.fullmatch(r"[가-힣]+\d*동", query):
        return await lookup_dong(query)
    if not config.KAKAO_REST_KEY:
        LOGGER.warning("장소 검증: 카카오 키 없음, 오프라인 역 데이터 사용")
        return _offline(query)
    try:
        # 연결·읽기 각각의 제한과 별개로 조회 전체 시간을 제한한다.
        documents = await asyncio.wait_for(_fetch_kakao(query), config.PLACE_LOOKUP_TIMEOUT_SECONDS)
        hits = []
        compact = re.sub(r"\s+", "", query)
        for document in documents:
            name = document["place_name"]
            candidate = re.sub(r"\s+", "", name)
            category = document.get("category_group_name")
            if (not candidate or category not in CATEGORIES or
                    not (candidate.startswith(compact) or candidate in compact)):
                continue
            address = document["address_name"].split()
            if len(address) >= 2:
                dong = (re.sub(r"\d+", "", address[2])
                        if len(address) > 2 and re.fullmatch(r"[가-힣]+\d*동", address[2]) else None)
                hits.append(PlaceHit(SIDOS.get(address[0], address[0]), address[1], name, "kakao", dong))
        local = next((hit for hit in hits if _in_dongjak(hit)), None)
        return local or (hits[0] if len({(hit.sido, hit.sigungu) for hit in hits}) == 1 else None)
    except (httpx.HTTPError, TimeoutError, ValueError, KeyError, TypeError) as error:
        LOGGER.warning("장소 검증 실패: %s, 오프라인 역 데이터 사용", type(error).__name__)
        return _offline(query)


async def ground(data, inputs):
    region = data["region"]
    location_answer = get_location_answer(inputs)
    query = extract_place_query(data["details"]["location_detail"], region)
    if not query:
        location = data["details"]["location_detail"] or region["place_text"] or ""
        if any(part in VAGUE_LOCATIONS for part in location.split()):
            data["details"]["location_detail"] = region["place_text"] = None
        return data, None
    hit = await lookup(query)
    if not hit:
        return data, None
    stated = extract_region(location_answer) if location_answer is not None else region
    if not _in_dongjak(hit) and stated["sigungu"] == "동작구":
        source = location_answer if location_answer is not None else "\n".join(
            [inputs["text"], inputs.get("answers", {}).get("q_what", "")])
        # 이름 붙은 시설이나 검색 결과 이름과 정확히 일치하는 장소만 지역과 충돌시킨다.
        if (extract_region(source)["sigungu"] == "동작구" and
                not re.search(r".+(?:역|소방서|주민센터|학교|병원|공원|도서관)$", query) and
                re.sub(r"\s+", "", query) != re.sub(r"\s+", "", hit.place_name)):
            return data, None
        return data, {"kind": "region", "query": query, "sido": hit.sido, "sigungu": hit.sigungu}
    # 답에 시·구가 없으면 원문에서 승계한 보조 지역보다 확인된 장소를 우선한다.
    if location_answer is not None and not stated["sido"] and not stated["sigungu"]:
        region.update(sido=hit.sido, sigungu=hit.sigungu)
    for field in ("sido", "sigungu"):
        if not region[field]:
            region[field] = getattr(hit, field)
    if _in_dongjak(hit) and hit.dong:
        if re.sub(r"\d+", "", region["dong"] or "") != hit.dong:
            if not region["dong"] or has_confirmation(inputs):
                region["dong"] = hit.dong
            else:
                return data, {"kind": "dong", "query": query, "stated": region["dong"], "dong": hit.dong}
    return data, None
