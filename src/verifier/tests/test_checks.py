import asyncio
from copy import deepcopy
from datetime import timedelta
from urllib.parse import quote
import httpx
import pytest
from redis.exceptions import RedisError
from app.clients import links
from app.schemas.contract import Handoff, validate
from app.services import channel, verifier


def outcome(body, status, failure, passed):
    assert body["status"] == status
    assert body["failure"] == failure
    assert body["data"]["verdict"] == {"success": "PASS", "retry": "RETRY", "failed": "FAIL"}[status]
    assert body["data"]["passed_channel_ids"] == passed


def test_E1_link_dead_chaos(handoff, post, chaos):
    value = handoff()
    redis, flag = chaos("link_dead")
    body = post(value)
    outcome(body, "failed", "LINK_DEAD", [])
    assert any("ConnectError" in check["detail"] for check in body["data"]["checks"])
    assert body["incident_id"] == flag.incident_id
    assert all(c["detail"].endswith("연결 실패(ConnectError)") for c in body["data"]["checks"] if c["name"] == "link")
    assert redis.closed and not redis.values
    outcome(post(value), "success", None, [c["channel_id"] for c in value["inputs"]["channels"]])


def test_B3_not_official_chaos(handoff, post, chaos, monkeypatch):
    _, flag = chaos("not_official")
    monkeypatch.setattr(links, "TRANSPORT", httpx.MockTransport(lambda r: pytest.fail("비공식 요청")))
    body = post(handoff())
    outcome(body, "failed", "NOT_OFFICIAL", [])
    assert body["incident_id"] == flag.incident_id
    assert not any(c["name"] == "link" for c in body["data"]["checks"])


def test_E2_stale_info_chaos(handoff, chaos):
    _, flag = chaos("stale_info")
    model = Handoff.model_validate(handoff())
    original = deepcopy(model.inputs)
    body = asyncio.run(verifier.run(model)).model_dump()
    validate("stage_result", body)
    outcome(body, "success", "STALE_INFO", [c["channel_id"] for c in original["channels"]])
    assert body["incident_id"] == flag.incident_id
    assert model.inputs == original


def test_channel_evidence_uses_checked_address_and_actual_recheck(handoff, post, chaos, monkeypatch):
    requested = []

    def respond(request):
        requested.append(str(request.url))
        if request.url.host.endswith(".invalid") or request.url.host.startswith("closed."):
            raise httpx.ConnectError("offline fixture", request=request)
        return httpx.Response(200)

    monkeypatch.setattr(links, "TRANSPORT", httpx.MockTransport(respond))
    variants = (
        ("link_dead", "https://www.epeople.go.kr/", "https://www.epeople.go.kr.invalid/", "ConnectError"),
        ("not_official", "https://www.epeople.go.kr/", "https://epeople-go.kr.example/report", "비공식 주소"),
        (None, "https://closed.epeople.go.kr/", "https://closed.epeople.go.kr/", "ConnectError"),
        (None, "https://unofficial.example/report", "https://unofficial.example/report", "비공식 주소"),
    )
    for kind, supplied_url, checked_url, reason in variants:
        value = handoff()
        item = value["inputs"]["channels"][0]
        item["url"] = supplied_url
        value["inputs"]["channels"] = [item]
        if kind:
            chaos(kind)
        requested.clear()
        broken = post(value)["data"]["evidence"]
        assert set(broken) == {"broken"}
        assert all(part in broken["broken"] for part in (item["name"], checked_url, reason))
        assert (checked_url in requested) == (reason == "ConnectError")
        item["name"], item["url"] = "다시 찾은 실제 창구", "https://www.safetyreport.go.kr/"
        fixed = post(value)["data"]["evidence"]
        assert fixed == {"fixed": f"{item['name']} · {item['url']} · 검증 통과"}
    item["name"] = "연락 010-9876-5432 " + "긴 창구명 " * 100
    item["url"] = "https://closed.epeople.go.kr/?email=private%40example.com&other=" + quote("시민@example.com")
    evidence = post(value)["data"]["evidence"]
    assert len(evidence["broken"]) <= 500
    assert "010-9876-5432" not in evidence["broken"]
    item["name"] = "연락 010-9876-5432"
    evidence = post(value)["data"]["evidence"]
    assert "private@example.com" not in evidence["broken"] and "private%40example.com" not in evidence["broken"]
    assert "시민@example.com" not in evidence["broken"] and quote("시민@example.com") not in evidence["broken"]
    assert "010-9876-5432" not in evidence["broken"] and "[개인정보 삭제]" in evidence["broken"]


def test_stale_evidence_reports_actual_verification_date_and_warning(handoff, post, chaos):
    for injected in (False, True):
        value = handoff()
        item = value["inputs"]["channels"][0]
        value["inputs"]["channels"] = [item]
        item["verified_at"] = (channel.today() - timedelta(days=91)).isoformat()
        verified_at = item["verified_at"]
        if injected:
            chaos("stale_info")
            verified_at = "2020-01-01"
        body = post(value)
        evidence = body["data"]["evidence"]
        age = (channel.today() - channel.date.fromisoformat(verified_at)).days
        assert body["failure"] == "STALE_INFO"
        assert all(part in evidence["broken"] for part in (item["name"], verified_at, f"{age}일 전", "기준 90일"))
        assert evidence["fixed"] == f"{item['name']} · {item['url']} · 확인 필요 표시를 붙여 안내"
        assert all(0 < len(text) <= 500 for text in evidence.values())


@pytest.mark.parametrize("mode", ["channel", "draft"])
def test_old_pii_flag_does_not_inject_into_verifier_copy(handoff, post, chaos, mode):
    redis, _ = chaos("pii_leak")
    body = post(handoff(mode))
    assert body["status"] == "success" and body["incident_id"] is None
    assert not redis.values


def telephone_channel(value):
    item = value["inputs"]["channels"][0]
    item.update(channel_id="dj-streetlight-01", method="phone", url="tel:02-820-4064",
                jurisdiction="동작구", source_url="https://www.dongjak.go.kr/source")
    return item


def test_phone_uses_official_source_without_requesting_tel(handoff, post, monkeypatch):
    requested = []

    def handler(request):
        requested.append(str(request.url))
        return httpx.Response(200)

    monkeypatch.setattr(links, "TRANSPORT", httpx.MockTransport(handler))
    value = handoff()
    item = telephone_channel(value)
    value["inputs"]["channels"] = [item]
    outcome(post(value), "success", None, [item["channel_id"]])
    item["verified_at"] = (channel.today() - timedelta(days=91)).isoformat()
    outcome(post(value), "success", "STALE_INFO", [item["channel_id"]])
    assert requested == [item["source_url"]] * 2


def test_phone_requires_valid_number_and_official_source(handoff, post, monkeypatch):
    monkeypatch.setattr(links, "TRANSPORT", httpx.MockTransport(lambda r: pytest.fail("잘못된 전화 창구 요청")))
    for change in ({"url": "tel:not-a-number"}, {"source_url": "https://dongjak.go.kr.example/source"}):
        value = handoff()
        item = telephone_channel(value)
        item.update(change)
        value["inputs"]["channels"] = [item]
        outcome(post(value), "failed", "NOT_OFFICIAL", [])


@pytest.mark.parametrize("kind,failure", [("link_dead", "LINK_DEAD"), ("not_official", "NOT_OFFICIAL")])
def test_channel_chaos_covers_phone_and_web_then_recovers(handoff, post, chaos, monkeypatch, kind, failure):
    requests = []
    def respond(request):
        requests.append(request.url.host)
        if request.url.host.endswith(".invalid"):
            raise httpx.ConnectError("DNS failed", request=request)
        return httpx.Response(200)
    monkeypatch.setattr(links, "TRANSPORT", httpx.MockTransport(respond))
    value = handoff()
    telephone_channel(value)
    redis, flag = chaos(kind)
    body = post(value)
    outcome(body, "failed", failure, [])
    assert body["incident_id"] == flag.incident_id and not redis.values
    outcome(post(value), "success", None, [item["channel_id"] for item in value["inputs"]["channels"]])


@pytest.mark.parametrize("host", ["evil-go.kr", "www.epeople.go.kr.evil.com", "www.epeople.go.kr@evil.com"])
def test_C1_lookalike_hosts_not_official(host):
    assert not channel.is_official("https://" + host)
    assert channel.is_official("https://www.gov.kr")
    assert channel.is_official("https://WWW.EPEOPLE.GO.KR./")
    assert channel.is_official("https://120dasan.or.kr/")
    assert not channel.is_official("ftp://www.epeople.go.kr/")
    assert not channel.is_official("/no-host")


def test_head_405_falls_back_to_get(handoff, post, monkeypatch):
    requests = []

    def handler(request):
        requests.append(request.method)
        assert request.headers["User-Agent"] == "minwon-verifier/1.0"
        assert request.extensions["timeout"]["read"] == 5.0
        return httpx.Response(405 if request.method == "HEAD" else 200)

    monkeypatch.setattr(links, "TRANSPORT", httpx.MockTransport(handler))
    value = handoff()
    value["inputs"]["channels"] = value["inputs"]["channels"][1:]
    outcome(post(value), "success", None, ["national-all-02"])
    assert requests == ["HEAD", "GET"]


def test_redirect_followed(handoff, post, monkeypatch):
    paths = []

    def handler(request):
        paths.append(request.url.path)
        return httpx.Response(301, headers={"Location": "/report"}) if request.url.path == "/" else httpx.Response(200)

    monkeypatch.setattr(links, "TRANSPORT", httpx.MockTransport(handler))
    value = handoff()
    value["inputs"]["channels"] = value["inputs"]["channels"][:1]
    outcome(post(value), "success", None, ["national-all-01"])
    assert paths == ["/", "/report"]


def test_partial_pass_link_dead(handoff, post, monkeypatch):
    requests = []

    def handler(request):
        requests.append((request.url.host, request.method))
        if request.url.host.startswith("closed."):
            raise httpx.ConnectError("모의 실패", request=request)
        return httpx.Response(200)

    monkeypatch.setattr(links, "TRANSPORT", httpx.MockTransport(handler))
    value = handoff()
    value["inputs"]["channels"][0]["url"] = "https://closed.epeople.go.kr/"
    body = post(value)
    outcome(body, "success", None, ["national-all-02"])
    assert body["data"]["reasons"] == ["LINK_DEAD"]
    assert ("closed.epeople.go.kr", "GET") not in requests


def test_non_official_never_requested(handoff, post, monkeypatch):
    monkeypatch.setattr(links, "TRANSPORT", httpx.MockTransport(lambda r: pytest.fail("요청 0회여야 함")))
    value = handoff()
    value["inputs"]["channels"] = value["inputs"]["channels"][:1]
    value["inputs"]["channels"][0]["url"] = "https://example.com/"
    body = post(value)
    outcome(body, "failed", "NOT_OFFICIAL", [])
    assert [c["name"] for c in body["data"]["checks"]] == ["official", "fresh", "scope"]


def test_partial_pass_scope(handoff, post):
    value = handoff()
    first, second = value["inputs"]["channels"]
    first.update(channel_id="dongjak-streetlight-01", jurisdiction="동작구")
    second.update(channel_id="gangnam-streetlight-01", jurisdiction="강남구")
    third = {**first, "channel_id": "dongjak-road_damage-01"}
    value["inputs"]["channels"].append(third)
    outcome(post(value), "success", None, ["dongjak-streetlight-01"])


def test_all_scope_mismatch_fails(handoff, post):
    value = handoff()
    for item in value["inputs"]["channels"]:
        item["jurisdiction"] = "강남구"
    body = post(value)
    outcome(body, "failed", "NOT_OFFICIAL", [])
    assert all("관할 불일치" in c["detail"] for c in body["data"]["checks"] if c["name"] == "scope")


@pytest.mark.parametrize("kind,value", [("휴대전화", "010-1234-5678"), ("유선전화", "02-123-4567"),
    ("주민등록번호", "900101-1234567"), ("이메일", "test@example.com"),
    ("상세 주소", "101동 1203호")], ids=["mobile", "landline", "rrn", "email", "unit"])
def test_C7_pii_kinds(kind, value, handoff, post):
    request = handoff("draft")
    request["inputs"]["draft"]["body"] += "\n" + value
    body = post(request)
    outcome(body, "retry", "PII_IN_DRAFT", [])
    pii = next(c for c in body["data"]["checks"] if c["name"] == "pii")
    assert pii["detail"] == "개인정보 감지: " + kind
    assert value not in str(body)
    if kind == "이메일":
        assert next(c for c in body["data"]["checks"] if c["name"] == "links")["ok"]


def test_lot_number_location_is_not_pii(handoff, post):
    request = handoff("draft")
    request["inputs"]["draft"]["body"] += "\n위치: 상도동 123-4번지 앞"
    outcome(post(request), "success", None, [])


@pytest.mark.parametrize('location,pii', [('사당1동 4호선 사당역 10번 출구 앞', False), ('101동 1203호로', True)])
def test_administrative_dong_and_subway_line_are_not_a_home_unit(location, pii, handoff, post):
    request = handoff('draft')
    request['inputs']['draft']['body'] += '\n' + location
    assert post(request)['failure'] == ('PII_IN_DRAFT' if pii else None)


@pytest.mark.parametrize("jurisdiction,passed", [("서울특별시 동작구", True), ("동작구", True),
    ("서울특별시", True), ("서울특별시 관악구", False), ("관악구", False)])
def test_jurisdiction_forms(jurisdiction, passed, handoff, post):
    value = handoff()
    first = value["inputs"]["channels"][0]
    first.update(channel_id="dongjak-streetlight-01", jurisdiction=jurisdiction)
    value["inputs"]["channels"] = [first]
    body = post(value)
    assert (body["data"]["passed_channel_ids"] == ["dongjak-streetlight-01"]) is passed


def test_B4_category_mismatch(handoff, post):
    value = handoff("draft")
    value["inputs"]["draft"].update(title="동작구 쓰레기 신고", body="상도동 쓰레기 무단투기")
    outcome(post(value), "retry", "DRAFT_MISMATCH", [])


def test_B4_region_mismatch(handoff, post):
    value = handoff("draft")
    value["inputs"]["draft"].update(title="강남구 가로등", body="강남구 가로등 고장")
    outcome(post(value), "retry", "DRAFT_MISMATCH", [])


def test_C1_short_link_in_draft(handoff, post):
    value = handoff("draft")
    value["inputs"]["draft"]["body"] += " bit.ly/x"
    outcome(post(value), "retry", "DRAFT_MISMATCH", [])
    value["inputs"]["draft"]["body"] = "동작구 가로등 고장, 신고는bit.ly/x로"
    outcome(post(value), "retry", "DRAFT_MISMATCH", [])
    value["inputs"]["draft"]["body"] = "동작구 가로등 https://www.epeople.go.kr"
    outcome(post(value), "success", None, [])


def test_rearm_other_modes_empty_channels_and_ignore_foreign_kinds(handoff, post, chaos):
    for kind in ("link_dead", "not_official", "stale_info"):
        redis, flag = chaos(kind)
        body = post(handoff("draft"))
        assert body["incident_id"] is None and body["status"] == "success"
        assert redis.values["chaos:verifier"] == flag.model_dump_json()
        value = handoff()
        value["inputs"]["channels"] = []
        outcome(post(value), "failed", "NOT_OFFICIAL", [])
        assert redis.values["chaos:verifier"] == flag.model_dump_json()
    for kind in ("llm_invalid", "injection"):
        redis, _ = chaos(kind)
        body = post(handoff())
        assert body["incident_id"] is None and body["status"] == "success" and not redis.values


def test_fresh_boundary_and_excluded_stale_channel(handoff, post):
    value = handoff()
    first, second = value["inputs"]["channels"]
    first["verified_at"] = (channel.today() - timedelta(days=90)).isoformat()
    assert post(value)["failure"] is None
    first["verified_at"] = (channel.today() - timedelta(days=91)).isoformat()
    assert post(value)["failure"] == "STALE_INFO"
    first["url"] = "https://closed.epeople.go.kr/"
    body = post(value)
    outcome(body, "success", None, [second["channel_id"]])
    assert body["data"]["reasons"] == ["LINK_DEAD", "STALE_INFO"]


def test_redis_errors_do_not_break_normal_verification(handoff, post, chaos, monkeypatch):
    redis, _ = chaos("link_dead")

    async def unavailable(*args):
        raise RedisError("모의 Redis 장애")

    monkeypatch.setattr(redis, "set", unavailable)
    assert post(handoff("draft"))["incident_id"] is None
    monkeypatch.setattr(redis, "getdel", unavailable)
    assert post(handoff())["status"] == "success" and redis.closed


def test_links_run_concurrently_and_get_body_is_not_read(handoff, post, monkeypatch):
    started, closed = [], []
    ready = asyncio.Event()

    class NoReadBody(httpx.AsyncByteStream):
        async def __aiter__(self):
            pytest.fail("GET 본문을 읽으면 안 됨")
            yield b""

        async def aclose(self):
            closed.append(True)

    async def handler(request):
        if request.method == "HEAD":
            started.append(request.url.host)
            if len(started) == 2:
                ready.set()
            await asyncio.wait_for(ready.wait(), timeout=1)
            return httpx.Response(405)
        return httpx.Response(200, stream=NoReadBody())

    monkeypatch.setattr(links, "TRANSPORT", httpx.MockTransport(handler))
    outcome(post(handoff()), "success", None, ["national-all-01", "national-all-02"])
    assert len(started) == len(closed) == 2


def test_channel_evidence_shows_one_channel_and_count(handoff, post, chaos, monkeypatch):
    monkeypatch.setattr(links, "TRANSPORT", httpx.MockTransport(lambda request: httpx.Response(200)))
    value = handoff()
    first = value["inputs"]["channels"][0]
    value["inputs"]["channels"] = [{**first, "channel_id": f"{first['channel_id']}-{n}", "name": f"창구{n}"} for n in range(3)]
    chaos("not_official")
    broken = post(value)["data"]["evidence"]["broken"]
    assert broken.startswith("창구0 · ") and broken.endswith(" 외 2곳") and "창구1" not in broken
