import asyncio

from app.services import places
from app.services.address import validate_dong
from app.services.josa import with_josa
from app.services.questions import prepare_questions
from app.services.rules import classify_by_rules
from test_places import document, mock_kakao, post


def test_generic_locations_never_search(monkeypatch):
    calls = mock_kakao(monkeypatch, [document("인도대사관", "서울 용산구 한남동 1", "공공기관")])
    for word in ("인도 차도 보도 도로 골목 골목길 길 횡단보도 정류장 버스정류장 사거리 교차로 육교 지하도 "
                 "공원 놀이터 주차장 아파트 빌라 상가 편의점 학교 초등학교 집 역 지하철역 병원 도서관").split() + ["공원 놀이터"]:
        inputs = {"text": f"흑석동 {word} 앞 가로등 고장"}
        data = classify_by_rules(inputs)
        data["details"]["location_detail"] = f"흑석동 {word} 앞"
        assert places.extract_place_query(word, {}) is None
        grounded, conflict = asyncio.run(places.ground(data, inputs))
        assert conflict is None and grounded["region"]["dong"] == "흑석동"
    assert calls == []


def test_named_landmarks_still_search(monkeypatch):
    for query, category in [("보라매공원", "관광명소"), ("상도초등학교", "학교"), ("노량진역", "지하철역")]:
        calls = mock_kakao(monkeypatch, [document(query, "서울 동작구 노량진동 1", category)])
        result = post(f"노량진동 {query} 앞 가로등 고장")
        assert calls == [query] and result["status"] == "success"


def test_explicit_local_region_ignores_partial_nonstation_match(monkeypatch):
    calls = mock_kakao(monkeypatch, [document("편의시설 관리사무소", "서울 용산구 한남동 1", "공공기관")])
    for text, answers in [("흑석동 편의시설 앞 가로등 고장", {}),
                          ("동작구 편의시설 앞 가로등 고장", {}),
                          ("가로등 고장", {"q_location_detail": "상도동 편의시설 앞"})]:
        result = post(text, answers)
        assert result["status"] in ("success", "needs_user")
        assert result["data"]["region"]["sigungu"] == "동작구"
        assert all("용산구" not in q["text"] for q in result["data"]["questions"])
    inputs = {"text": "안녕하세요", "answers": {"q_what": "흑석동 편의시설 앞 가로등 고장"}}
    data = classify_by_rules(inputs)
    data["details"]["location_detail"] = "편의시설 앞"
    assert asyncio.run(places.ground(data, inputs))[1] is None
    assert calls == ["편의시설"] * 4


def test_exact_named_foreign_landmark_still_asks(monkeypatch):
    for query, name, category in [("국립중앙박물관", "국립중앙박물관", "문화시설"),
                                  ("용산초등학교", "용산초등학교 병설유치원", "유치원")]:
        calls = mock_kakao(monkeypatch, [document(name, "서울 용산구 용산동 1", category)])
        result = post(f"흑석동 {query} 앞 가로등 고장")
        assert calls == [query] and result["status"] == "needs_user"
        assert "서울특별시 용산구" in result["data"]["questions"][0]["text"]


def test_station_conflict_keeps_location_question_and_correct_particle(monkeypatch):
    calls = mock_kakao(monkeypatch, [document("석남역 7호선", "인천 서해구 석남동 1")])
    result = post("서울 동작구 상도동 석남역 앞 가로등 고장")
    assert calls == ["석남역"] and result["status"] == "needs_user"
    question = result["data"]["questions"][0]
    assert question["id"] == "q_location_detail" and "석남역은 인천광역시" in question["text"]
    assert "은(는)" not in question["text"]


def test_topic_and_subject_particles():
    for word, topic, subject in [("인도", "인도는", "인도가"), ("석남역", "석남역은", "석남역이"),
                                  ("서울", "서울은", "서울이")]:
        assert with_josa(word, "은/는") == topic
        assert with_josa(word, "이/가") == subject


def test_direction_particle_and_nonhangul_fallback():
    for word, expected in [("노량진동", "노량진동으로"), ("인도", "인도로"), ("서울", "서울로")]:
        assert with_josa(word, "으로/로") == expected
    for word in ("ABC", "101"):
        for pair, suffix in [("은/는", "은(는)"), ("이/가", "이(가)"), ("으로/로", "으로(로)")]:
            assert with_josa(word, pair) == word + suffix


def test_question_and_address_messages_use_particles():
    inputs = {"text": "서울 동작구 오류동 노량진역 앞 가로등 고장"}
    data = classify_by_rules(inputs)
    data["region"]["dong"] = "노량진동"
    data, conflict = validate_dong(data, inputs)
    assert "오류동은 동작구에 없어요" in conflict["text"]
    assert "노량진동으로 안내할까요?" in conflict["text"]
    data, _, _ = prepare_questions(data, {"kind": "dong", "query": "인도", "stated": "상도동", "dong": "노량진동"})
    question = data["questions"][0]
    assert question["id"] == "q_confirm_location"
    assert question["text"].startswith("인도는 동작구 노량진동")
    assert "노량진동으로 접수할까요?" in question["text"]
