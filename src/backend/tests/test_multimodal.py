import asyncio
import json
import runpy
from unittest.mock import AsyncMock
import pytest
from fastapi.testclient import TestClient
from app.clients import agents, store, translate
from app.core.config import CONTRACTS
from app.main import app
from app.services.supervisor import run_job
from test_answers import AnswerDB, _sample

@pytest.fixture
def setup(monkeypatch):
    db, calls = AnswerDB(), []
    template = runpy.run_path(str(CONTRACTS.parent / "src/writer/app/services/templates.py"))["make_template"]
    async def call(handoff):
        calls.append(handoff)
        name = handoff.to + "_streetlight" + ("_draft" if handoff.inputs.get("mode") == "draft" else "")
        result = _sample(name, handoff.task_id)
        if handoff.to == "classifier":
            result["data"]["details"]["has_photo"] = handoff.inputs.get("answers", {}).get("q_has_photo") == "있음"
        if handoff.to == "writer":
            result["data"] = template(handoff.inputs)
        return result
    translator = AsyncMock(side_effect=lambda p, s, t: {"text": "상도동 주민센터 앞 가로등 고장"} if "text" in p else p)
    monkeypatch.setattr(store, "query", db.query)
    monkeypatch.setattr(agents, "call", call)
    monkeypatch.setattr(translate, "translate", translator)
    return TestClient(app), calls, translator

def test_ko_no_translation(setup):
    client, _, translator = setup
    state = client.post("/jobs?wait=true", json={"text": "상도동 가로등"}).json()["state"]
    translator.assert_not_called()
    assert not {"lang", "original_text", "has_photo", "i18n"} & state.keys()

def test_english_input(setup):
    client, calls, _ = setup
    original = "The streetlight in front of Sangdo-dong community center is out"
    state = client.post("/jobs?wait=true", json={"text": original, "lang": "en"}).json()["state"]
    assert calls[0].inputs["text"] == state["text_ko"] == "상도동 주민센터 앞 가로등 고장"
    assert (state["lang"], state["original_text"]) == ("en", original)

def test_translation_failure(setup):
    client, calls, translator = setup
    translator.side_effect = None; translator.return_value = None
    state = client.post("/jobs?wait=true", json={"text": "broken lamp", "lang": "en"}).json()["state"]
    assert calls[0].inputs["text"] == "broken lamp" and "i18n" not in state
    assert "번역에 실패해 원문으로 처리했습니다" in state["confirm_needed"]

def test_photo_reaches_classifier_and_writer(setup):
    client, calls, _ = setup
    state = client.post("/jobs?wait=true", json={"text": "상도동 가로등", "has_photo": True}).json()["state"]
    assert calls[0].inputs["answers"]["q_has_photo"] == "있음" and state["has_photo"] is True
    assert "현장 사진을 보유하고 있습니다." in state["result"]["draft"]["body"]
    asyncio.run(run_job(calls[0].task_id, "상도동 가로등", {"q_has_photo": "없음"}, has_photo=True))
    assert calls[5].inputs["answers"]["q_has_photo"] == "없음"

def test_output_translation_structure(setup):
    client, _, translator = setup
    state = client.post("/jobs?wait=true", json={"text": "broken lamp", "lang": "en"}).json()["state"]
    assert set(state["i18n"]) == {"lang", "questions", "confirm_needed", "results"}
    assert set(state["i18n"]["results"][0]["channels"][0]) == {"name", "org", "jurisdiction"}
    assert state["result"]["draft"]["body"].startswith("상도동") and translator.await_count == 2

def test_strict_job_input():
    for options in ({"lang": "fr"}, {"lang": 1}, {"has_photo": "true"}, {"has_photo": 1}):
        assert TestClient(app).post("/jobs", json={"text": "민원", **options}).status_code == 422


def test_translate_request_mentions_json(monkeypatch):
    """OpenAI json_object 형식은 input에 'json' 단어가 없으면 400을 돌려준다."""
    import httpx
    from app.core import config
    sent = []
    async def handler(request):
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={"status": "completed", "output": [{"type": "message", "content": [
            {"type": "output_text", "text": json.dumps({"text": "상도동 가로등"})}]}]})
    monkeypatch.setattr(config, "OPENAI_API_KEY", "sk-test")
    real_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw))
    assert asyncio.run(translate.translate({"text": "lamp"}, "en", "ko")) == {"text": "상도동 가로등"}
    assert "json" in sent[0]["input"].lower() and "lamp" in sent[0]["input"]
