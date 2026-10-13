import asyncio
import json

import httpx
import pytest

from app.clients import llm


@pytest.mark.parametrize("break_output", [False, True])
def test_break_output_request_keeps_normal_reasoning_default(monkeypatch, break_output):
    original = httpx.AsyncClient
    def respond(request):
        body = json.loads(request.content)
        assert ("reasoning" in body) is break_output
        assert body.get("reasoning") == ({"effort": "none"} if break_output else None)
        assert ("text" not in body) is break_output
        assert body["instructions"] == ("이번 응답은 JSON·중괄호·코드블록을 절대 쓰지 말고 한국어 산문 두 문장으로만 답한다."
                                         if break_output else "JSON 객체로 답한다.")
        return httpx.Response(200, json={"status": "completed", "output": [{"type": "message",
            "content": [{"type": "output_text", "text": "{}"}]}]})
    monkeypatch.setattr(llm.httpx, "AsyncClient", lambda **kwargs: original(
        transport=httpx.MockTransport(respond), **kwargs))
    assert asyncio.run(llm.generate("JSON 객체로 답한다.", {}, "test-model", break_output=break_output)) == "{}"


def test_timeout_and_budget_defaults_caps_and_overrides(monkeypatch):
    import runpy
    import dotenv
    from app.core import config
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *args: None)
    keys = ("LLM_TIMEOUT_SECONDS", "LLM_BUDGET_SECONDS")
    for values, expected in [(None, (10, 13)), ((99, 99), (10, 13)), ((6, 9), (6, 9)), ((0, 0), (0.1, 0.1))]:
        for key, value in zip(keys, values or (None, None)):
            monkeypatch.delenv(key, raising=False) if value is None else monkeypatch.setenv(key, str(value))
        loaded = runpy.run_path(config.__file__)
        assert tuple(loaded[key] for key in keys) == expected
