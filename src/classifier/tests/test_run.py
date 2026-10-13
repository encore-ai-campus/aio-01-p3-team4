import copy
import json

import pytest
from fastapi.testclient import TestClient

from app.clients import llm
from app.core import config
from app.main import app
from app.schemas.contract import StageResult
from app.services import places
from app.services.rules import classify_by_rules

CLIENT = TestClient(app)


def request(text, answers=None, **overrides):
    payload = json.loads((config.CONTRACTS / "samples/classifier_input.json").read_text())
    payload["inputs"] = {"text": text}
    if answers is not None:
        payload["inputs"]["answers"] = answers
    payload.update(overrides)
    return payload


def post(payload):
    response = CLIENT.post("/run", json=payload)
    assert response.status_code == 200, response.text
    StageResult.model_validate(response.json())
    return response.json()


@pytest.mark.parametrize("path", [
    p for p in sorted((config.CONTRACTS / "samples").glob("classifier_*_input.json"))
    if "llm_invalid" not in p.name
], ids=lambda p: p.stem)
def test_samples_have_valid_semantic_result(path):
    payload = json.loads(path.read_text())
    expected = json.loads(path.with_name(path.name.replace("_input", "_output")).read_text())
    result = post(payload)
    assert result["task_id"] == payload["task_id"]
    assert result["status"] == expected["status"]
    if expected["data"]["confidence"] >= 0.7:
        assert result["data"]["category"] == expected["data"]["category"]
    bad = copy.deepcopy(payload)
    bad["inputs"]["unexpected"] = True
    assert CLIENT.post("/run", json=bad).status_code == 422


@pytest.mark.parametrize("phrase,category", [
    ("가로등이 점등되지 않아요", "streetlight"),
    ("아스팔트에 포트홀이 생겼어요", "road_damage"),
    ("누군가 쓰레기를 버렸어요", "illegal_dumping"),
])
@pytest.mark.parametrize("located", [True, False])
def test_three_categories_with_and_without_location(phrase, category, located):
    prefix = "서울 동작구 흑석동 학교 앞 " if located else "집 앞 "
    result = post(request(prefix + phrase))
    assert result["data"]["category"] == category
    assert result["status"] == ("success" if located else "needs_user")
    if not located:
        assert result["data"]["region"]["sido"] is None
        assert result["data"]["questions"][0]["id"] == "q_location_detail"


def test_answer_location_and_optional_details():
    result = post(request("가로등이 꺼졌어요", {
        "q_location_detail": "서울 동작구 상도동 주민센터 앞",
        "q_occurred_at": "어제", "q_has_photo": "네",
    }))
    assert result["status"] == "success"
    assert result["data"]["details"]["has_photo"] is True
    assert result["data"]["details"]["occurred_at"] == "어제"
    assert result["data"]["region"]["sigungu"] == "동작구"


def test_questions_keep_distinct_ids_across_rounds():
    first = post(request("뭔가 이상해요"))
    ids = {q["id"] for q in first["data"]["questions"]}
    assert ids == {"q_category", "q_location_detail"}
    second = post(request("뭔가 이상해요", {"q_category": "가로등 고장"}))
    assert [q["id"] for q in second["data"]["questions"]] == ["q_location_detail"]
    third = post(request("뭔가 이상해요", {
        "q_category": "가로등 고장", "q_location_detail": "서울 동작구 흑석동 학교 앞",
    }))
    assert third["status"] == "success"
    assert third["data"]["category"] == "streetlight"


def test_legacy_q1_location_answer():
    assert post(request("집 앞 가로등이 꺼졌어요", {
        "q1": "서울 동작구 상도동 주민센터 앞",
    }))["status"] == "success"


@pytest.mark.parametrize("text", [
    "서울 강남구 학교 앞 가로등이 꺼졌어요",
    "부산 동작구 학교 앞 가로등 고장",
    "층간소음이 심해요",
])
def test_outside_scope(text):
    result = post(request(text))
    assert result["status"] == "failed"
    assert result["failure"] == "OUT_OF_SCOPE"


def test_invalid_model_response_switches_to_fallback(monkeypatch):
    monkeypatch.setattr(config, "OPENAI_API_KEY", "test-key")
    calls = []
    payload = request("서울 동작구 상도동 주민센터 앞 가로등 고장")
    async def generate(prompt, inputs, model):
        calls.append(model)
        return "{invalid" if len(calls) < 2 else json.dumps(classify_by_rules(inputs))
    monkeypatch.setattr(llm, "generate", generate)
    assert post(payload)["status"] == "success"
    assert calls == [config.OPENAI_MODEL, config.OPENAI_FALLBACK_MODEL]


def test_invalid_schema_uses_rules(monkeypatch):
    monkeypatch.setattr(config, "OPENAI_API_KEY", "test-key")
    async def generate(*args):
        return '{"category":"unknown"}'
    monkeypatch.setattr(llm, "generate", generate)
    result = post(request("서울 동작구 상도동 학교 앞 도로 파손"))
    assert result["status"] == "success"
    assert result["data"]["category"] == "road_damage"


@pytest.mark.parametrize("located", [False, True])
def test_observed_model_location_responses_are_grounded(monkeypatch, located):
    monkeypatch.setattr(config, "OPENAI_API_KEY", "test-key")
    sample = "classifier_streetlight" + ("" if located else "_needs_user")
    payload = json.loads((config.CONTRACTS / f"samples/{sample}_input.json").read_text())
    data = {
        "category": "streetlight", "confidence": 0.93,
        "region": {
            "sido": "서울특별시", "sigungu": "동작구",
            "dong": "상도동" if located else None,
            "place_text": "상도동 주민센터 앞" if located else "집 앞",
        },
        "details": {"location_detail": None if located else "집 앞",
                    "occurred_at": None, "has_photo": None},
        "missing": [], "questions": [],
    }
    calls = []
    async def generate(prompt, inputs, model):
        calls.append(model)
        return json.dumps(data)
    monkeypatch.setattr(llm, "generate", generate)
    result = post(payload)
    assert calls == [config.OPENAI_MODEL]
    assert result["incident_id"] is None
    assert result["data"]["confidence"] == 0.93
    if located:
        assert result["status"] == "success"
        assert result["data"]["details"]["location_detail"] == "주민센터 앞"
    else:
        assert result["status"] == "needs_user"
        assert result["data"]["details"]["location_detail"] is None
        assert result["data"]["region"]["sido"] is None
        assert result["data"]["region"]["sigungu"] is None
        assert [q["id"] for q in result["data"]["questions"]] == ["q_location_detail"]


@pytest.mark.parametrize("text,answers,model_dong", [
    ("서울 동작 상도 주민센터 앞 가로등 고장", None, "상도동"),
    ("가로등 고장", {"q_location_detail": "서울 동작 상도 주민센터 앞"}, "상도동"),
    ("서울 동작구 상도동 주민센터 앞 가로등 고장", None, "사당동"),
])
def test_model_region_uses_input_aliases_and_answers(monkeypatch, text, answers, model_dong):
    monkeypatch.setattr(config, "OPENAI_API_KEY", "test-key")
    data = json.loads((config.CONTRACTS / "samples/classifier_output.json").read_text())["data"]
    data["region"]["dong"] = model_dong
    data["details"]["location_detail"] = "주민센터 앞"
    async def generate(*args):
        return json.dumps(data)
    monkeypatch.setattr(llm, "generate", generate)
    result = post(request(text, answers))
    assert result["status"] == ("needs_user" if answers else "success")
    assert result["incident_id"] is None
    assert result["data"]["region"]["sido"] == "서울특별시"
    assert result["data"]["region"]["sigungu"] == (None if answers else "동작구")
    assert result["data"]["region"]["dong"] == (None if answers else "상도동")
    assert result["data"]["details"]["location_detail"] == (
        "동작 상도 주민센터 앞" if answers else "주민센터 앞")
    if answers:
        assert [q["id"] for q in result["data"]["questions"]] == ["q_location_detail"]


def test_later_attempt_uses_local_rules(monkeypatch):
    monkeypatch.setattr(config, "OPENAI_API_KEY", "test-key")
    async def unexpected(*args):
        pytest.fail("최종 대안에서 AI를 호출하면 안 됨")
    monkeypatch.setattr(llm, "generate", unexpected)
    assert post(request("서울 동작구 상도동 학교 앞 가로등 고장",
                        attempt=3, last_failure="LLM_OUTPUT_INVALID"))["status"] == "success"


def test_nonfinite_model_confidence_uses_rules(monkeypatch):
    monkeypatch.setattr(config, "OPENAI_API_KEY", "test-key")
    async def generate(prompt, inputs, model):
        data = classify_by_rules(inputs)
        data["confidence"] = float("nan")
        return json.dumps(data)
    monkeypatch.setattr(llm, "generate", generate)
    result = post(request("서울 동작구 상도동 학교 앞 가로등 고장"))
    assert result["data"]["confidence"] == 0.9
    assert result["incident_id"] and result["data"]["recovery"]["recovered_by"] == "rules"


def test_wrong_destination():
    payload = request("가로등 고장")
    payload["to"] = "writer"
    payload["inputs"] = json.loads((config.CONTRACTS / "samples/writer_input.json").read_text())["inputs"]
    assert CLIENT.post("/run", json=payload).status_code == 422


def test_model_trimmed_location_falls_back_to_rule_value(monkeypatch):
    monkeypatch.setattr(config, "OPENAI_API_KEY", "test-key")
    async def generate(prompt, inputs, model):
        data = classify_by_rules(inputs)
        data["details"]["location_detail"] = "역 앞"
        return json.dumps(data, ensure_ascii=False)
    monkeypatch.setattr(llm, "generate", generate)
    result = post(request("노량진역 앞 가로등 고장"))
    assert result["data"]["details"]["location_detail"] == "노량진역 앞"
    assert result["data"]["region"]["place_text"] == "노량진역 앞"


@pytest.mark.parametrize("exit_text", ["1번 출구", "12번출구"])
def test_station_exit_keeps_location_without_inventing_a_district(exit_text):
    text = f"상도역 {exit_text} 앞 가로등이 밤마다 꺼져 있어요"
    local = classify_by_rules({"text": text})
    assert local["region"]["sigungu"] is None
    assert local["region"]["dong"] is None
    assert local["details"]["location_detail"] == f"상도역 {exit_text} 앞"
    result = post(request(text))
    assert result["status"] == "needs_user"
    assert result["data"]["category"] == "streetlight"
    assert result["data"]["region"]["sigungu"] == "동작구"
    assert result["data"]["details"]["location_detail"] == f"상도역 {exit_text} 앞"


@pytest.mark.parametrize("text,expected_district", [
    ("상도역 1번 출구 앞 가로등이 밤마다 꺼져 있어요", None),
    ("흑석동 상가 출구 앞 쓰레기 무단투기", "동작구"),
])
def test_model_exit_district_is_replaced_before_place_grounding(monkeypatch, text, expected_district):
    monkeypatch.setattr(config, "OPENAI_API_KEY", "test-key")
    calls, grounded = [], []
    async def generate(prompt, inputs, model):
        calls.append(model)
        data = classify_by_rules(inputs)
        data["confidence"] = 0.93
        data["region"]["sigungu"] = "출구"
        return json.dumps(data, ensure_ascii=False)
    async def ground(data, inputs):
        grounded.append(data["region"]["sigungu"])
        return data, None
    monkeypatch.setattr(llm, "generate", generate)
    monkeypatch.setattr(places, "ground", ground)
    result = post(request(text))
    assert calls == [config.OPENAI_MODEL, config.OPENAI_MODEL, config.OPENAI_FALLBACK_MODEL]
    assert grounded == [expected_district]
    assert result["incident_id"] and result["data"]["recovery"]["recovered_by"] == "rules"
    assert result["data"]["confidence"] == 0.9
    assert result["data"]["region"]["sigungu"] == expected_district
    assert result["status"] == ("needs_user" if expected_district is None else "success")
