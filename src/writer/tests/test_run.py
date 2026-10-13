import copy
import json

import pytest
from fastapi.testclient import TestClient

from app.clients import llm
from app.core import config
from app.main import app
from app.schemas.contract import StageResult
from app.services.pii import redact_text
from app.services.templates import make_template

CLIENT = TestClient(app)


def request(category="streetlight", **overrides):
    payload = json.loads((config.CONTRACTS / "samples/writer_input.json").read_text())
    payload["inputs"]["category"] = category
    payload.update(overrides)
    return payload


def post(payload):
    response = CLIENT.post("/run", json=payload)
    assert response.status_code == 200, response.text
    StageResult.model_validate(response.json())
    return response.json()


@pytest.mark.parametrize("path", [
    p for p in sorted((config.CONTRACTS / "samples").glob("writer_*_input.json"))
    if "llm_invalid" not in p.name
], ids=lambda p: p.stem)
def test_samples_have_valid_result(path):
    payload = json.loads(path.read_text())
    result = post(payload)
    assert result["task_id"] == payload["task_id"]
    assert result["status"] == "success"
    assert result["data"]["title"] and result["data"]["body"]
    bad = copy.deepcopy(payload)
    bad["inputs"]["unexpected"] = True
    assert CLIENT.post("/run", json=bad).status_code == 422


@pytest.mark.parametrize("category,name", [
    ("streetlight", "가로등 고장"), ("road_damage", "도로 파손"),
    ("illegal_dumping", "쓰레기 무단투기"),
])
def test_category_templates(category, name):
    data = post(request(category))["data"]
    assert name in data["title"]
    assert "상도동 주민센터 앞" in data["body"]


def test_missing_information_is_not_invented():
    payload = request()
    payload["inputs"]["details"] = {"location_detail": None, "occurred_at": None, "has_photo": None}
    data = post(payload)["data"]
    assert {f["label"] for f in data["fill_in"]} == {"정확한 위치", "발생 시점"}
    assert "어제" not in data["body"]
    assert "사진" not in data["body"]


def test_redacts_input_and_does_not_return_pii_values():
    payload = request()
    payload["inputs"]["details"]["location_detail"] += (
        " 연락처 010-1234-5678 주민번호 900101-1234567 이메일 citizen@example.com 101동 1203호"
    )
    data = post(payload)["data"]
    result_text = json.dumps(data, ensure_ascii=False)
    for secret in ("010-1234-5678", "900101-1234567", "citizen@example.com", "1203호"):
        assert secret not in result_text
    assert set(data["removed_pii"]) == {"전화번호", "주민등록번호", "이메일", "상세 집주소"}
    assert "상도동 주민센터 앞" in data["body"]


def test_public_road_is_preserved_and_personal_address_is_removed():
    assert redact_text("상도로 123 횡단보도 앞")[0] == "상도로 123 횡단보도 앞"
    clean, labels = redact_text("집 주소: 서울 동작구 상도로 123, 연락 부탁드립니다.")
    assert "상도로 123" not in clean
    assert "상세 집주소" in labels


def test_residential_unit_followed_by_particle_is_removed():
    clean, labels = redact_text("101동 1203호에서 연락합니다. 국도 1호선 앞")
    assert "1203호" not in clean
    assert "국도 1호선" in clean
    assert labels == ["상세 집주소"]


@pytest.mark.parametrize('personal', [False, True])
def test_official_phone_is_preserved_without_a_pii_label(monkeypatch, personal):
    monkeypatch.setattr(config, 'OPENAI_API_KEY', 'test-key')
    payload = request()
    payload['inputs']['channel'].update(method='phone', url='tel:02-820-4064')
    if personal:
        payload['inputs']['details']['location_detail'] += ' 연락처 010.1234.5678'
    original = copy.deepcopy(payload)
    async def generate(prompt, inputs, model):
        assert inputs['channel'] == original['inputs']['channel']
        assert '010.1234.5678' not in str(inputs)
        return json.dumps(make_template(inputs))
    monkeypatch.setattr(llm, 'generate', generate)
    assert post(payload)['data']['removed_pii'] == (['전화번호'] if personal else [])
    assert payload == original


def test_model_never_receives_input_pii_and_output_is_cleaned(monkeypatch):
    monkeypatch.setattr(config, "OPENAI_API_KEY", "test-key")
    payload = request()
    payload["inputs"]["details"]["location_detail"] += " 010-1234-5678"
    async def generate(prompt, inputs, model):
        assert "010-1234-5678" not in json.dumps(inputs)
        data = make_template(inputs)
        data["body"] += " 02-123-4567"
        data["removed_pii"] = ["010-1234-5678"]
        return json.dumps(data)
    monkeypatch.setattr(llm, "generate", generate)
    data = post(payload)["data"]
    assert "02-123-4567" not in data["body"]
    assert data["removed_pii"] == ["전화번호"]


def test_invalid_response_and_wrong_category_use_template(monkeypatch):
    monkeypatch.setattr(config, "OPENAI_API_KEY", "test-key")
    async def generate(prompt, inputs, model):
        return json.dumps({"title": "여권 발급", "body": "여권을 발급해 주세요", "fill_in": [], "removed_pii": []})
    monkeypatch.setattr(llm, "generate", generate)
    data = post(request())["data"]
    assert "가로등 고장" in data["title"]


def test_verifier_retry_uses_safe_template(monkeypatch):
    monkeypatch.setattr(config, "OPENAI_API_KEY", "test-key")
    async def unexpected(*args):
        pytest.fail("문구 검증 실패 후에는 안전한 템플릿을 사용해야 함")
    monkeypatch.setattr(llm, "generate", unexpected)
    assert post(request(attempt=2, last_failure="PII_IN_DRAFT"))["status"] == "success"


@pytest.mark.parametrize("includes_contact", [False, True])
@pytest.mark.parametrize("primary_fails", [False, True])
def test_pii_chaos_cleans_generated_draft(monkeypatch, includes_contact, primary_fails):
    from app.services import drafting
    from app.clients.chaos import ChaosFlag
    flag = ChaosFlag.model_validate({
        "incident_id": "pii-test", "kind": "pii_leak",
        "injected_at": "2026-10-08T00:00:00Z", "params": {},
    })
    async def inject_once(self, target, operation, inject):
        return await inject(flag)
    monkeypatch.setattr(drafting.ChaosClient, "run", inject_once)
    import httpx
    monkeypatch.setattr(config, "OPENAI_API_KEY", "test-key")
    original_client = httpx.AsyncClient
    calls = []
    def respond(request):
        inputs = json.loads(json.loads(request.content)["input"].partition("\n")[2])
        calls.append(inputs)
        assert "additional_description" not in inputs
        if primary_fails and len(calls) == 1:
            raise TimeoutError("[TEST] primary model timeout")
        data = make_template(inputs)
        if includes_contact:
            data["body"] += " 연락처 010-1234-5678"
        return httpx.Response(200, json={"status": "completed", "output": [{
            "type": "message", "content": [{"type": "output_text", "text": json.dumps(data)}]}]})
    monkeypatch.setattr(llm.httpx, "AsyncClient", lambda **kwargs: original_client(
        transport=httpx.MockTransport(respond), **kwargs))
    result = post(request())
    assert len(calls) == (2 if primary_fails else 1)
    assert result["incident_id"] == "pii-test"
    assert "010-1234-5678" not in result["data"]["body"]
    assert result["data"]["removed_pii"] == ["전화번호"]
    assert result['data']['body'].count('[개인정보 삭제]') == 1
    trace = result['data']['recovery']
    assert trace['recovered_by'] == 'redact'
    assert trace['attempts'][-1]['error_type'] == 'PII_IN_DRAFT'
    assert trace['attempts'][-1]['model'] == (config.OPENAI_FALLBACK_MODEL if primary_fails else config.OPENAI_MODEL)
    evidence = trace['attempts'][-1]['evidence']
    if includes_contact:
        assert '010-1234-5678' not in json.dumps(result)
        assert '[개인정보 삭제]' in evidence['broken']
        assert '실험용 가짜 번호' not in evidence['broken']
    else:
        assert evidence['broken'] == '연락처 010-1234-5678 (실험용 가짜 번호)'
        assert result['data']['evidence']['fixed'] == '연락처 [개인정보 삭제] · 지운 항목: 전화번호'


def test_user_number_matching_chaos_never_becomes_public_evidence(monkeypatch):
    monkeypatch.setattr(config, 'OPENAI_API_KEY', 'test-key')
    payload = request()
    payload['inputs']['details']['location_detail'] += ' 연락처 010-1234-5678'
    async def generate(prompt, inputs, model):
        assert '010-1234-5678' not in json.dumps(inputs)
        data = make_template(inputs)
        data['body'] += '\n연락처 010-1234-5678 이메일 private@example.com'
        data['evidence'] = {'broken': '010-1234-5678', 'fixed': 'spoofed'}
        return json.dumps(data)
    monkeypatch.setattr(llm, 'generate', generate)
    data = post(payload)['data']
    assert all(secret not in json.dumps(data) for secret in ('010-1234-5678', 'private@example.com', 'spoofed'))
    # Existing domain recovery fixes natural PII before final redaction and emits no incident.
    assert 'evidence' not in data and 'recovery' not in data
    assert data['removed_pii'] == ['전화번호']
