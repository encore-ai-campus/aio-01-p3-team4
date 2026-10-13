import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.clients import llm
from app.core import config
from app.main import app
from app.schemas.contract import StageResult
from app.services import places
from app.services.rules import classify_by_rules


def document(name, address, category="지하철역"):
    return {"place_name": name, "address_name": address, "category_group_name": category}


def mock_kakao(monkeypatch, documents):
    calls = []
    async def fetch(query):
        calls.append(query)
        return documents
    monkeypatch.setattr(config, "KAKAO_REST_KEY", "test-key")
    monkeypatch.setattr(places, "_fetch_kakao", fetch)
    return calls


def post(text, answers=None):
    payload = json.loads((config.CONTRACTS / "samples/classifier_input.json").read_text())
    payload["inputs"] = {"text": text, "answers": answers or {}}
    response = TestClient(app).post("/run", json=payload)
    assert response.status_code == 200, response.text
    StageResult.model_validate(response.json())
    return response.json()


@pytest.mark.parametrize("dong,station,name,verified_dong,exit_text", [
    ("상도동", "노량진역", "노량진역 9호선", "노량진동", " 2번출구"),
    ("흑석동", "신대방삼거리역", "신대방삼거리역 7호선", "대방동", ""),
])
def test_verified_place_asks_before_correcting_dong(monkeypatch, dong, station, name, verified_dong, exit_text):
    calls = mock_kakao(monkeypatch, [document(name, f"서울 동작구 {verified_dong} 60-11")])
    result = post(f"서울 동작구 {dong} {station}{exit_text} 앞 가로등 고장")
    assert calls == [station]
    assert (result["status"], result["failure"]) == ("needs_user", "NEEDS_MORE_INFO")
    assert result["data"]["region"]["dong"] == dong
    assert len(result["data"]["questions"]) == 1
    assert verified_dong in result["data"]["questions"][0]["text"]
    assert station in result["data"]["region"]["place_text"]


@pytest.mark.parametrize("dong", ["상도동", "상도1동"])
def test_equivalent_dong_is_preserved(monkeypatch, dong):
    mock_kakao(monkeypatch, [document("상도역 7호선", "서울 동작구 상도1동 702-1")])
    result = post(f"서울 동작구 {dong} 상도역 앞 가로등 고장")
    assert result["status"] == "success"
    assert result["data"]["region"]["dong"] == dong


@pytest.mark.parametrize("station,name,address,dong", [
    ("노량진역", "노량진역 9호선", "서울 동작구 노량진동 60-11", "노량진동"),
    ("상도역", "상도역 7호선", "서울 동작구 상도1동 702-1", "상도동"),
    ("흑석역 2번 출구", "흑석역 9호선", "서울 동작구 흑석동 1", "흑석동"),
])
def test_verified_place_fills_missing_dong_without_question(monkeypatch, station, name, address, dong):
    mock_kakao(monkeypatch, [document(name, address)])
    result = post(f"{station} 앞 가로등 고장")
    assert result["status"] == "success"
    assert result["data"]["region"]["dong"] == dong
    assert result["data"]["questions"] == []


@pytest.mark.parametrize("address", ["서울 동작구", "서울 동작구 노량진로 1", "서울 동작구 노량진동60-11"])
def test_keyword_address_without_dong_preserves_stated_dong(monkeypatch, address):
    mock_kakao(monkeypatch, [document("노량진역 9호선", address)])
    assert asyncio.run(places.lookup("노량진역")).dong is None
    result = post("서울 동작구 상도동 노량진역 앞 가로등 고장")
    assert result["status"] == "success"
    assert result["data"]["region"]["dong"] == "상도동"


@pytest.mark.parametrize("key", ["q_location_detail", "location_detail", "q1"])
@pytest.mark.parametrize("answer", ["상도동 노량진역 앞", "서울 동작구 상도동 노량진역 2번출구 앞"])
def test_location_answer_asks_before_correcting_verified_dong(monkeypatch, key, answer):
    calls = mock_kakao(monkeypatch, [document("노량진역 9호선", "서울 동작구 노량진동 60-11")])
    result = post("서울 동작구 상도동 석남역 앞 가로등 고장", {key: answer})
    assert calls == ["노량진역"]
    assert (result["status"], result["failure"]) == ("needs_user", "NEEDS_MORE_INFO")
    assert result["data"]["region"]["dong"] == "상도동"
    assert len(result["data"]["questions"]) == 1
    assert "노량진동" in result["data"]["questions"][0]["text"]
    assert "노량진역" in result["data"]["region"]["place_text"]


def test_conflicting_place_asks_one_location_question(monkeypatch):
    calls = mock_kakao(monkeypatch, [document("석남역 7호선", "인천 서해구 석남동 609")])
    result = post("서울 동작구 상도동 석남역 앞 가로등 고장")
    assert calls == ["석남역"]
    assert (result["status"], result["failure"]) == ("needs_user", "NEEDS_MORE_INFO")
    data = result["data"]
    assert data["region"]["sigungu"] == "동작구"
    assert data["region"]["dong"] == "상도동"
    assert data["missing"] == ["location_detail"]
    assert len(data["questions"]) == 1
    question = data["questions"][0]
    assert (question["id"], question["field"]) == ("q_location_detail", "location_detail")
    assert "석남역" in question["text"] and "인천" in question["text"]


def test_location_answers_follow_verified_region(monkeypatch):
    text = "서울 동작구 상도동 석남역 앞 가로등 고장"
    calls = mock_kakao(monkeypatch, [document("숭실대입구역", "서울 동작구 상도동 514")])
    for key in ("q_location_detail", "location_detail", "q1"):
        result = post(text, {key: "상도동 숭실대입구역 앞"})
        assert result["status"] == "success" and result["data"]["questions"] == []
    assert calls == ["숭실대입구역"] * 3
    mock_kakao(monkeypatch, [document("석남역", "인천 서해구 석남동 609")])
    for key in ("q_location_detail", "location_detail", "q1"):
        result = post(text, {key: "석남역 앞"})
        assert (result["status"], result["failure"]) == ("failed", "OUT_OF_SCOPE")
        assert result["data"]["questions"] == []
        assert result["data"]["region"]["sigungu"] == "서해구"


def test_place_outside_dongjak_fills_missing_region(monkeypatch):
    mock_kakao(monkeypatch, [document("석남역 7호선", "인천 서해구 석남동 609")])
    result = post("석남역 앞 가로등이 꺼졌어요")
    assert (result["status"], result["failure"]) == ("failed", "OUT_OF_SCOPE")
    assert result["data"]["region"]["sido"] == "인천광역시"
    assert result["data"]["region"]["sigungu"] == "서해구"
    assert result["data"]["region"]["dong"] is None
    assert post("부산 해운대구 학교 앞 가로등 고장")["failure"] == "OUT_OF_SCOPE"
    mock_kakao(monkeypatch, [document("통합청사", "전남광주통합특별시 동구 청사로 1", "공공기관")])
    result = post("통합청사 앞 가로등 고장")
    assert result["failure"] == "OUT_OF_SCOPE"
    assert result["data"]["region"]["sido"] == "전남광주통합특별시"


def test_dongjak_candidate_is_preferred_after_model_parsing(monkeypatch):
    calls = mock_kakao(monkeypatch, [
        document("노량진역 별관", "경기 광명시 철산동 1"),
        document("다른역", "서울 동작구 상도동 1"),
        document("노량진역 9호선", "서울 동작구 노량진동 60-11"),
    ])
    monkeypatch.setattr(config, "OPENAI_API_KEY", "test-key")
    async def generate(prompt, inputs, model):
        return json.dumps(classify_by_rules(inputs))
    monkeypatch.setattr(llm, "generate", generate)
    result = post("노량진역 앞 가로등 고장")
    assert calls == ["노량진역"]
    assert result["status"] == "success"
    assert result["data"]["region"]["dong"] == "노량진동"
    assert result["data"]["questions"] == []
    assert result["data"]["region"]["sido"] == "서울특별시"
    assert result["data"]["region"]["sigungu"] == "동작구"


def test_generic_places_and_unmatched_categories_are_ignored(monkeypatch):
    mock_kakao(monkeypatch, [
        document("경인철도횡단보도육교", "서울 구로구 구로동 1", ""),
        document("횡단보도", "제주 제주시 일도동 1", "음식점"),
        document("횡단보도", "서울 동작구 상도동 1", "카페"),
        document("학교", "서울 구로구 구로동 1", "학교"),
    ])
    assert asyncio.run(places.lookup("횡단보도")) is None
    result = post("횡단보도 앞 가로등")
    assert result["status"] == "needs_user"
    assert result["data"]["questions"][0]["text"] == "발생 위치의 시·구와 동·도로명·근처 건물을 알려 주세요"
    mock_kakao(monkeypatch, [])
    assert asyncio.run(places.lookup("노량진역")) is None


def test_failed_lookup_falls_back_without_guessing_ambiguous_stations(monkeypatch):
    monkeypatch.setattr(config, "KAKAO_REST_KEY", "test-key")
    monkeypatch.setattr(config, "PLACE_LOOKUP_TIMEOUT_SECONDS", 0.01)
    async def slow_fetch(query):
        await asyncio.sleep(1)
    monkeypatch.setattr(places, "_fetch_kakao", slow_fetch)
    assert post("노량진역 앞 가로등 고장")["status"] == "needs_user"
    assert asyncio.run(places.lookup("시청역")) is None
    result = post("시청역 앞 가로등 고장")
    assert result["status"] == "needs_user"
    assert result["data"]["region"]["sido"] is None
    monkeypatch.setattr(config, "KAKAO_REST_KEY", "")
    hit = asyncio.run(places.lookup("노량진역"))
    assert hit.source == "offline" and hit.sigungu == "동작구"
    assert hit.dong is None
    assert post("노량진역 앞 가로등 고장")["data"]["region"]["dong"] is None
    assert post("서울 동작구 상도동 노량진역 앞 가로등 고장")["data"]["region"]["dong"] == "상도동"
    assert asyncio.run(places.lookup("석남역")) is None
    monkeypatch.setitem(places.STATIONS, "중복", [["부산광역시", "동구"], ["서울특별시", "동작구"]])
    assert asyncio.run(places.lookup("중복역")).sigungu == "동작구"
    assert asyncio.run(places.lookup("강남역")).sigungu != "동작구"


def test_place_query_strips_regions_and_suffixes_without_changing_home():
    for suffix in ("앞", "옆", "근처", "맞은편", "입구", "출구", "횡단보도", "교차로", "사거리"):
        assert places.extract_place_query(f"서울 동작구 상도동 석남역 {suffix}", {}) == "석남역"
    assert places.extract_place_query("서울특별시 동작구 본동 숭실대입구역 앞", {}) == "숭실대입구역"
    assert places.extract_place_query("서울 미지1동 노량진역 횡단보도 앞", {}) == "노량진역"
    assert places.extract_place_query(None, {"place_text": "노량진역 앞"}) == "노량진역"
    for text in ("집 앞", "우리 집 앞", "동네 근처", "길 옆", "횡단보도 앞", "역 앞", ""):
        assert places.extract_place_query(text, {}) is None
    result = post("집 앞 가로등 고장")
    assert result["status"] == "needs_user" and result["data"]["region"]["sido"] is None


def test_kakao_http_contract_and_error_fallback(monkeypatch):
    monkeypatch.setattr(config, "KAKAO_REST_KEY", "test-key")
    responses = [
        httpx.Response(200, json={"documents": [document("노량진역", "서울 동작구 노량진동 1")]}),
        httpx.Response(503), httpx.Response(200, text="{invalid"),
    ]
    def handle(request):
        assert str(request.url).startswith("https://dapi.kakao.com/v2/local/search/keyword.json?")
        assert dict(request.url.params) == {"query": "노량진역", "x": "126.9393", "y": "37.5124", "size": "5"}
        assert request.headers["Authorization"] == "KakaoAK test-key"
        assert set(request.extensions["timeout"].values()) == {1.5}
        return responses.pop(0)
    client = httpx.AsyncClient
    monkeypatch.setattr(places.httpx, "AsyncClient", lambda **kwargs: client(
        transport=httpx.MockTransport(handle), **kwargs))
    for source in ("kakao", "offline", "offline"):
        hit = asyncio.run(places.lookup("노량진역"))
        assert hit.source == source and hit.sigungu == "동작구"
    assert responses == []


@pytest.mark.parametrize("text,location", [
    ("동작구 어딘가 가로등이 고장났어요", "어딘가"),
    ("서울 동작구 상도동 어딘가 가로등이 꺼진 것 같아요", "상도동 어딘가"),
])
@pytest.mark.parametrize("model_enabled", [False, True])
def test_vague_input_asks_general_location_without_fetch(monkeypatch, text, location, model_enabled):
    calls = mock_kakao(monkeypatch, [document("어딘가", "강원 강릉시 해안로 1", "숙박")])
    model_calls = []
    if model_enabled:
        monkeypatch.setattr(config, "OPENAI_API_KEY", "test-key")
        async def generate(prompt, inputs, model):
            model_calls.append(model)
            data = classify_by_rules(inputs)
            data["details"]["location_detail"] = data["region"]["place_text"] = location
            return json.dumps(data)
        monkeypatch.setattr(llm, "generate", generate)
    result = post(text)
    assert model_calls == ([config.OPENAI_MODEL] if model_enabled else [])
    assert calls == []
    assert (result["status"], result["failure"]) == ("needs_user", "NEEDS_MORE_INFO")
    assert result["data"]["region"]["sigungu"] == "동작구"
    assert result["data"]["details"]["location_detail"] is None
    assert result["data"]["region"]["place_text"] is None
    assert result["data"]["questions"] == [{
        "id": "q_location_detail", "field": "location_detail",
        "text": "발생 위치의 시·구와 동·도로명·근처 건물을 알려 주세요",
    }]


def test_place_query_ignores_whole_vague_tokens():
    for phrase in ("어딘가", "어디", "어느", "여기", "거기", "저기", "그곳", "이곳", "근처", "주변",
                   "부근", "인근", "쪽", "어딘지", "어디선가", "여기 주변", "그곳 부근"):
        assert places.extract_place_query(phrase, {}) is None
        assert places.extract_place_query(f"서울 동작구 상도동 {phrase}", {}) is None
    assert places.extract_place_query("서울 동작구 여기초등학교 앞", {}) == "여기초등학교"
    assert places.extract_place_query("어딘가학교 근처", {}) == "어딘가학교"


@pytest.mark.parametrize("query,name,category,accepted", [
    ("횡단보도", "경인철도횡단보도육교", "공공기관", False),
    ("노량진역", "노량진역 9호선", "지하철역", True),
    ("노량진역 9호선", "노량진역", "지하철역", True),
    ("해변호텔", "해변호텔", "숙박", False),
])
def test_candidate_requires_allowed_category_and_name_match(monkeypatch, query, name, category, accepted):
    calls = mock_kakao(monkeypatch, [document(name, "서울 동작구 상도동 1", category)])
    hit = asyncio.run(places.lookup(query))
    assert (hit is not None) == accepted
    assert calls == [query]


def address_document(sido, sigungu, address_type="REGION"):
    return {"address_type": address_type, "address": {
        "region_1depth_name": sido, "region_2depth_name": sigungu,
    }}


def mock_address(monkeypatch, documents):
    calls = []
    async def fetch(query):
        calls.append(query)
        return documents
    async def keyword(query):
        pytest.fail("[TEST] 동 이름 query는 주소 API만 호출해야 함")
    monkeypatch.setattr(config, "KAKAO_REST_KEY", "test-key")
    monkeypatch.setattr(places, "_fetch_kakao_address", fetch)
    monkeypatch.setattr(places, "_fetch_kakao", keyword)
    return calls


@pytest.mark.parametrize("query,regions,expected", [
    ("석남동", [("인천", "서해구")] * 4 + [("충청남도", "서산시")], ("인천광역시", "서해구")),
    ("흑석동", [("대전", "서구")] * 4 + [("서울", "동작구")], ("서울특별시", "동작구")),
    ("신림동", [("서울", "관악구")], ("서울특별시", "관악구")),
    ("이동", [("서울", "중구"), ("부산", "동구"), ("인천", "서구"),
              ("대전", "유성구"), ("경기", "포천시")], None),
    ("운동", [], None),
    ("상도2동", [("서울", "동작구")], ("서울특별시", "동작구")),
])
def test_dong_address_observed_regions(monkeypatch, query, regions, expected):
    calls = mock_address(monkeypatch, [address_document(*region) for region in regions])
    hit = asyncio.run(places.lookup(query))
    assert ((hit.sido, hit.sigungu) if hit else None) == expected
    if hit:
        assert hit.place_name == query and hit.source == "kakao"
        assert hit.dong is None
    assert calls == [query]
    if query not in ("흑석동", "상도2동"):
        assert places.extract_place_query(f"서울 동작구 {query}", {}) == query
    else:
        assert places.extract_place_query(f"서울 동작구 {query}", {}) is None


def test_dong_address_ignores_non_regions_and_preserves_unknown_sido(monkeypatch):
    calls = mock_address(monkeypatch, [
        address_document("서울", "동작구", "REGION_ADDR"),
        address_document("서울", "동작구", "ROAD_ADDR"),
        address_document("서울", "동작구", "ROAD"),
        {"address_type": "REGION", "address": None},
        address_document("", "동작구"),
        address_document("전남광주통합특별시", "동구"),
    ])
    hit = asyncio.run(places.lookup("미지동"))
    assert (hit.sido, hit.sigungu) == ("전남광주통합특별시", "동구")
    assert calls == ["미지동"]


@pytest.mark.parametrize("error", [httpx.ReadTimeout, TimeoutError, ValueError])
def test_dong_address_errors_do_not_use_station_fallback(monkeypatch, error):
    calls = mock_address(monkeypatch, [])
    monkeypatch.setitem(places.STATIONS, "석남동", [["서울특별시", "동작구"]])
    async def fetch(query):
        calls.append(query)
        raise error("[TEST] address lookup failed")
    monkeypatch.setattr(places, "_fetch_kakao_address", fetch)
    assert asyncio.run(places.lookup("석남동")) is None
    assert calls == ["석남동"]


def test_dong_address_without_key_and_total_timeout(monkeypatch):
    calls = mock_address(monkeypatch, [])
    monkeypatch.setattr(config, "KAKAO_REST_KEY", "")
    assert asyncio.run(places.lookup("석남동")) is None and calls == []
    monkeypatch.setattr(config, "KAKAO_REST_KEY", "test-key")
    monkeypatch.setattr(config, "PLACE_LOOKUP_TIMEOUT_SECONDS", 0.01)
    cancelled = []
    async def slow(query):
        try:
            await asyncio.sleep(1)
        finally:
            cancelled.append(query)
    monkeypatch.setattr(places, "_fetch_kakao_address", slow)
    assert asyncio.run(places.lookup("석남동")) is None
    assert cancelled == ["석남동"]


def test_kakao_address_http_contract_and_error_fallback(monkeypatch):
    monkeypatch.setattr(config, "KAKAO_REST_KEY", "test-key")
    responses = [
        httpx.Response(200, json={"documents": [address_document("서울", "관악구")]}),
        httpx.Response(503), httpx.Response(200, text="{invalid"),
        httpx.Response(200, json={"documents": None}), httpx.Response(200, json={}),
    ]
    def handle(request):
        assert request.method == "GET"
        assert str(request.url).startswith("https://dapi.kakao.com/v2/local/search/address.json?")
        assert dict(request.url.params) == {"query": "신림동", "size": "5"}
        assert request.headers["Authorization"] == "KakaoAK test-key"
        assert set(request.extensions["timeout"].values()) == {1.5}
        return responses.pop(0)
    client = httpx.AsyncClient
    monkeypatch.setattr(places.httpx, "AsyncClient", lambda **kwargs: client(
        transport=httpx.MockTransport(handle), **kwargs))
    hit = asyncio.run(places.lookup("신림동"))
    assert (hit.sido, hit.sigungu, hit.source) == ("서울특별시", "관악구", "kakao")
    for _ in range(4):
        assert asyncio.run(places.lookup("신림동")) is None
    assert responses == []
