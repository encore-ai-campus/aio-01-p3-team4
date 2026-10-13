import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.clients import llm
from app.core import config
from app.main import app
from app.schemas.contract import StageResult
from app.services import places
from app.services.rules import (
    classify_by_rules, extract_location, extract_region, get_location_answer, has_confirmation,
)

TEXT = "서울 동작구 상도동 석남역 앞 가로등 고장 문제를 어제 봤어요. 사진은 없어요."
GENERAL = "발생 위치의 시·구와 동·도로명·근처 건물을 알려 주세요"
FIRE_TEXT = "서울시 동작구 상도동 동작소방서 앞에 가로등이 나갔어요"
CONFIRM = ("동작소방서는 동작구 신대방동에 있는 곳으로 확인돼요. "
           "말씀하신 상도동 대신 신대방동으로 접수할까요? "
           "맞으면 '네', 아니면 정확한 위치(동·도로명·근처 건물)를 알려 주세요")


@pytest.fixture
def kakao(monkeypatch):
    calls = []
    async def fetch(query):
        assert not query.endswith("동"), "[TEST] 동 이름은 키워드 검색을 쓰면 안 됨"
        calls.append(query)
        if query == "석남역":
            address = "인천 서해구 석남동 609"
        elif query == "동작소방서":
            address = "서울 동작구 신대방동 460-19"
        elif query in ("숭실대입구역", "흑석역", "노량진역"):
            dong = {"숭실대입구역": "상도동", "흑석역": "흑석동", "노량진역": "노량진동"}[query]
            address = f"서울 동작구 {dong} 1"
        else:
            return []
        return [{"place_name": query + (" 7호선" if query.endswith("역") else ""),
                 "address_name": address,
                 "category_group_name": "지하철역" if query.endswith("역") else "공공기관"}]
    async def fetch_address(query):
        assert query == "석남동"
        calls.append(f"address:{query}")
        return [{"address_type": "REGION", "address": {
            "region_1depth_name": sido, "region_2depth_name": sigungu,
        }} for sido, sigungu in [("인천", "서해구")] * 4 + [("충청남도", "서산시")]]
    monkeypatch.setattr(config, "KAKAO_REST_KEY", "test-key")
    monkeypatch.setattr(places, "_fetch_kakao", fetch)
    monkeypatch.setattr(places, "_fetch_kakao_address", fetch_address)
    return calls


def post(answer=None, key="location_detail", text=TEXT, answers=None):
    payload = json.loads((config.CONTRACTS / "samples/classifier_input.json").read_text())
    payload["inputs"] = {"text": text, "answers": answers if answers is not None else
                         ({} if answer is None else {key: answer})}
    response = TestClient(app).post("/run", json=payload)
    assert response.status_code == 200, response.text
    StageResult.model_validate(response.json())
    return response.json()


@pytest.mark.parametrize("key", ["location_detail", "q_location_detail", "q1"])
@pytest.mark.parametrize("answer,dong,detail,query", [
    pytest.param("서울 동작구 상도동 숭실대입구역 앞", "상도동", "상도동 숭실대입구역 앞", "숭실대입구역", id="A"),
    pytest.param("흑석동", "흑석동", "흑석동", None, id="B"),
    pytest.param("상도동", "상도동", "상도동", None, id="B2"),
    pytest.param("서울 동작구 흑석동", "흑석동", "흑석동", None, id="region-only"),
    pytest.param("서울 동작구 흑석동 흑석역 앞", "흑석동", "흑석동 흑석역 앞", "흑석역", id="corrected-station"),
])
def test_answer_replaces_original(kakao, key, answer, dong, detail, query):
    result = post(answer, key)
    assert result["status"] == "success" and result["failure"] is None
    assert result["data"]["region"] == dict(sido="서울특별시", sigungu="동작구", dong=dong, place_text=detail)
    assert result["data"]["details"] == dict(location_detail=detail, occurred_at="어제", has_photo=False)
    assert "석남" not in json.dumps(result, ensure_ascii=False)
    assert kakao == ([query] if query else [])


@pytest.mark.parametrize("answer,sido,sigungu", [
    pytest.param("인천 서구 석남동 석남역 앞", "인천광역시", "서구", id="C"),
    pytest.param("부산 해운대구 해운대역 앞", "부산광역시", "해운대구", id="G"),
    pytest.param("석남동", "인천광역시", "서해구", id="D"),
])
def test_outside_answer_uses_its_region(kakao, answer, sido, sigungu):
    result = post(answer)
    assert (result["status"], result["failure"]) == ("failed", "OUT_OF_SCOPE")
    assert result["data"]["region"]["sido"] == sido
    assert result["data"]["region"]["sigungu"] == sigungu
    if answer == "석남동":
        assert kakao == ["address:석남동"]


@pytest.mark.parametrize("failure", [False, True], ids=["unknown", "timeout"])
def test_D_unverified_dong_requires_location(monkeypatch, kakao, failure):
    async def fetch(query):
        assert query == "석남동"
        if failure:
            raise httpx.ReadTimeout("[TEST] timeout")
        return []
    monkeypatch.setattr(places, "_fetch_kakao_address", fetch)
    result = post("석남동")
    assert result["status"] == "needs_user"
    assert result["data"]["region"] == dict(sido="서울특별시", sigungu="동작구", dong=None, place_text=None)
    assert result["data"]["questions"][0]["text"] == "동작구의 어느 동인가요?"


@pytest.mark.parametrize("key", ["location_detail", "q_location_detail", "q1"])
def test_E_explicit_conflicting_answer_asks_again(kakao, key):
    for _ in range(2):
        result = post("서울 동작구 상도동 석남역 앞", key)
        assert (result["status"], result["failure"]) == ("needs_user", "NEEDS_MORE_INFO")
        assert result["data"]["missing"] == ["location_detail"]
        assert result["data"]["questions"] == [{"id": "q_location_detail", "field": "location_detail",
            "text": "석남역은 인천광역시 서해구에 있는 곳으로 확인돼요. 서울 동작구 민원이 맞다면 동작구 안의 위치(동·도로명·근처 건물)만 다시 알려 주세요"}]
    assert kakao == ["석남역", "석남역"]


@pytest.mark.parametrize("key", ["location_detail", "q_location_detail", "q1"])
@pytest.mark.parametrize("answer", ["몰라요", "모름", "모르겠어요", "없어요", "없음", "아니요", "아뇨", "  ", "", "앞", "집 앞", "서울 동작구"])
def test_F_unusable_answer_does_not_restore_original(kakao, key, answer):
    result = post(answer, key)
    assert (result["status"], result["failure"]) == ("needs_user", "NEEDS_MORE_INFO")
    assert result["data"]["region"] == dict(sido="서울특별시", sigungu="동작구", dong=None, place_text=None)
    assert result["data"]["details"]["location_detail"] is None
    assert result["data"]["questions"] == [{"id": "q_location_detail", "field": "location_detail", "text": GENERAL}]
    assert kakao == []


def test_H_lookup_timeout_trusts_answer_with_or_without_offline_hit(monkeypatch, kakao):
    async def fetch(query):
        raise httpx.ReadTimeout("[TEST] timeout")
    monkeypatch.setattr(places, "_fetch_kakao", fetch)
    for place in ("숭실대입구역", "미등록건물"):
        result = post(f"서울 동작구 상도동 {place} 앞")
        assert result["status"] == "success"
        assert result["data"]["region"] == dict(sido="서울특별시", sigungu="동작구", dong="상도동", place_text=f"상도동 {place} 앞")
        assert result["data"]["details"]["location_detail"] == f"상도동 {place} 앞"


@pytest.mark.parametrize("answer", ["흑석동", "몰라요", "인천 서구 석남동 석남역 앞"])
def test_I_answer_overrides_model_location_and_region(monkeypatch, kakao, answer):
    expected = post(answer)
    monkeypatch.setattr(config, "OPENAI_API_KEY", "test-key")
    calls = []
    async def generate(prompt, inputs, model):
        calls.append(model)
        data = classify_by_rules({"text": TEXT})
        data["details"]["location_detail"] = data["region"]["place_text"] = "석남역 앞"
        return json.dumps(data)
    monkeypatch.setattr(llm, "generate", generate)
    result = post(answer)
    assert calls == [config.OPENAI_MODEL]
    assert result["incident_id"] is None
    assert result == expected


def test_J_first_input_checks_unknown_dong_only(kakao):
    result = post(text="서울 동작구 석남동 가로등 고장")
    assert result["status"] == "needs_user" and result["failure"] == "NEEDS_MORE_INFO"
    assert result["data"]["questions"][0]["text"].startswith("석남동은 인천광역시 서해구에 있는 곳으로 확인돼요.")
    assert kakao == ["address:석남동"]
    result = post(text="서울 동작구 상도동 가로등 고장")
    assert result["status"] == "needs_user"
    assert result["data"]["questions"][0]["text"] == GENERAL
    assert kakao == ["address:석남동"]


def test_dong_lookup_does_not_use_keyword_results(monkeypatch, kakao):
    async def fetch(query):
        pytest.fail("[TEST] 주소 검색 실패를 키워드 검색으로 대체하면 안 됨")
    async def fetch_address(query):
        assert query == "석남동"
        return []
    monkeypatch.setattr(places, "_fetch_kakao", fetch)
    monkeypatch.setattr(places, "_fetch_kakao_address", fetch_address)
    result = post("석남동")
    assert result["status"] == "needs_user"
    assert result["data"]["region"]["sigungu"] == "동작구"


def test_legacy_category_answer_stays_separate_from_location(kakao):
    result = post(text="뭔가 이상해요", answers={"q1": "가로등 고장"})
    assert result["data"]["category"] == "streetlight"
    assert result["data"]["details"]["location_detail"] is None
    assert result["data"]["questions"] == [{"id": "q_location_detail", "field": "location_detail", "text": GENERAL}]
    result = post(text="뭔가 이상해요", answers={"q_category": "가로등 고장", "location_detail": "흑석동"})
    assert result["status"] == "success" and kakao == []


def test_dong_conflict_keeps_stated_dong_and_strips_original_prefix(kakao):
    result = post(text=FIRE_TEXT)
    assert (result["status"], result["failure"]) == ("needs_user", "NEEDS_MORE_INFO")
    assert result["data"]["region"] == dict(
        sido="서울특별시", sigungu="동작구", dong="상도동", place_text="동작소방서 앞")
    assert result["data"]["details"]["location_detail"] == "동작소방서 앞"
    assert result["data"]["missing"] == ["location_detail"]
    assert result["data"]["questions"] == [
        {"id": "q_confirm_location", "field": "location_detail", "text": CONFIRM}]
    assert kakao == ["동작소방서"]


@pytest.mark.parametrize("key", ["location_detail", "q_location_detail", "q1"])
@pytest.mark.parametrize("answer", ["네", "예", "응", "그래", "맞아", "맞아요", "맞습니다", "맞음",
                                    "ㅇㅇ", "넵", "네네", "예요", "그렇습니다", "좋아요", "확인", " 네 ! "])
@pytest.mark.parametrize("model_enabled", [False, True])
def test_affirmative_confirms_dong_using_original(monkeypatch, kakao, key, answer, model_enabled):
    model_calls = []
    if model_enabled:
        monkeypatch.setattr(config, "OPENAI_API_KEY", "test-key")
        async def generate(prompt, inputs, model):
            model_calls.append(model)
            data = classify_by_rules({"text": inputs["text"]})
            data["region"]["dong"] = "흑석동"  # 입력에 없는 AI 지명은 원문으로 검증한다.
            return json.dumps(data)
        monkeypatch.setattr(llm, "generate", generate)
    result = post(answer, key, text=FIRE_TEXT)
    assert result["status"] == "success" and result["failure"] is None
    assert result["data"]["region"] == dict(
        sido="서울특별시", sigungu="동작구", dong="신대방동", place_text="동작소방서 앞")
    assert result["data"]["details"]["location_detail"] == "동작소방서 앞"
    assert result["data"]["questions"] == [] and result["data"]["missing"] == []
    assert model_calls == ([config.OPENAI_FALLBACK_MODEL] if model_enabled else [])
    assert result["incident_id"] is None and kakao == ["동작소방서"]


def test_negative_confirmation_asks_general_location(kakao):
    result = post("아니요", text=FIRE_TEXT)
    assert (result["status"], result["failure"]) == ("needs_user", "NEEDS_MORE_INFO")
    assert result["data"]["details"]["location_detail"] is None
    assert result["data"]["region"]["dong"] is None
    assert result["data"]["questions"] == [
        {"id": "q_location_detail", "field": "location_detail", "text": GENERAL}]
    assert kakao == []


def test_new_location_answer_replaces_fire_station_and_keeps_dong_prefix(kakao):
    result = post("상도동 숭실대입구역 앞", text=FIRE_TEXT)
    assert result["status"] == "success" and result["data"]["questions"] == []
    assert result["data"]["region"] == dict(
        sido="서울특별시", sigungu="동작구", dong="상도동", place_text="상도동 숭실대입구역 앞")
    assert result["data"]["details"]["location_detail"] == "상도동 숭실대입구역 앞"
    assert kakao == ["숭실대입구역"]


@pytest.mark.parametrize("answer", [None, "네"])
def test_equivalent_administrative_dong_is_kept_even_after_confirmation(kakao, answer):
    result = post(answer, text="서울시 동작구 신대방2동 동작소방서 앞 가로등 고장")
    assert result["status"] == "success" and result["data"]["questions"] == []
    assert result["data"]["region"]["dong"] == "신대방2동"
    assert result["data"]["details"]["location_detail"] == "동작소방서 앞"
    assert kakao == ["동작소방서"]


@pytest.mark.parametrize("alias,official", [
    ("서울시", "서울특별시"), ("인천시", "인천광역시"), ("부산시", "부산광역시"),
    ("대구시", "대구광역시"), ("대전시", "대전광역시"), ("광주시", "광주광역시"),
    ("울산시", "울산광역시"),
])
def test_city_aliases_and_shared_region_prefix_removal(alias, official):
    text = f"{alias} 동작구 상도2동 동작소방서 앞 가로등 고장"
    assert extract_region(text)["sido"] == official
    assert extract_location(text) == "동작소방서 앞"
    assert places.extract_place_query(text.removesuffix(" 가로등 고장"), {}) == "동작소방서"
    assert extract_location("상도동 주민센터 앞") == "주민센터 앞"
    assert extract_location("상도동 앞") is None
    assert extract_location(f"{alias} 동작구 상도동 상도로 12") == "상도로 12"


@pytest.mark.parametrize("key", ["location_detail", "q_location_detail", "q1"])
def test_confirmation_requires_whole_location_answer(key):
    assert has_confirmation({"text": FIRE_TEXT, "answers": {key: "맞 아 요?!"}})
    assert get_location_answer({"text": FIRE_TEXT, "answers": {key: "맞 아 요?!"}}) is None
    for answer in ("아니요", "네 아니요", "네 신대방동", "확인 부탁해요", ""):
        inputs = {"text": FIRE_TEXT, "answers": {key: answer}}
        assert not has_confirmation(inputs) and get_location_answer(inputs) == answer
    assert not has_confirmation({"text": FIRE_TEXT, "answers": {"q_has_photo": "네"}})
    assert not has_confirmation({"text": "뭔가 이상해요", "answers": {"q1": "네"}})


@pytest.mark.parametrize("key", ["location_detail", "q_location_detail", "q1"])
@pytest.mark.parametrize("answer", ["네", "맞아요", "ㅇㅇ"])
def test_affirmative_does_not_resolve_outside_region_conflict(kakao, key, answer):
    result = post(answer, key)
    assert (result["status"], result["failure"]) == ("needs_user", "NEEDS_MORE_INFO")
    assert result["data"]["region"]["dong"] == "상도동"
    assert result["data"]["missing"] == ["location_detail"]
    assert result["data"]["questions"] == [{"id": "q_location_detail", "field": "location_detail",
        "text": "석남역은 인천광역시 서해구에 있는 곳으로 확인돼요. 서울 동작구 민원이 맞다면 동작구 안의 위치(동·도로명·근처 건물)만 다시 알려 주세요"}]
    assert kakao == ["석남역"]
