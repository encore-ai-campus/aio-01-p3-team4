import asyncio
import re
from time import perf_counter
from uuid import uuid4
from psycopg.types.json import Jsonb
from app.clients import agents, store, translate
from app.schemas.contract import Handoff, StageResult, validate
from app.services import incidents
from app.services.security import injection_fragment, is_injection

MAX_STAGE_ATTEMPTS = 3  # 같은 단계 최대 3번 시도(처음 포함) — 설계서 §5-3-1
MAX_REPLANS = 2         # 재계획 최대 횟수 (최초 시도 이후 추가 재계획 최대 2회)
MAX_QUESTION_ROUNDS = 2
SAME_STAGE_RETRY_FAILURES = {"LLM_OUTPUT_INVALID", "SERVICE_UNAVAILABLE"}
REPLAN_FAILURES = {"NO_CHANNEL", "LINK_DEAD", "NOT_OFFICIAL", "PII_IN_DRAFT", "DRAFT_MISMATCH"}
TRACKED_FAILURES = SAME_STAGE_RETRY_FAILURES | REPLAN_FAILURES | {"STALE_INFO", "INJECTION_BLOCKED"}

# classifier.rules.CATEGORIES의 서비스 경계 내 사본. 분류기와 함께 키워드를 맞춘다.
CATEGORIES = {
    "streetlight": ("가로등", "보안등", "거리 조명"),
    "road_damage": ("도로 파손", "도로파손", "포트홀", "싱크홀", "도로에 구멍", "도로가 깨", "보도블록", "아스팔트"),
    "illegal_dumping": ("무단투기", "무단 투기", "쓰레기", "폐기물"),
}
CATEGORY_LABELS = {"streetlight": "가로등 고장", "road_damage": "도로 파손", "illegal_dumping": "쓰레기 무단투기"}

CONSTRAINTS = ["개인정보를 묻지 말 것", "공식 출처만 사용할 것"]
OBJECTIVES = {"classifier": "민원 유형과 지역 파악", "locator": "공식 신고 창구 탐색",
              "verifier": "창구 또는 문구 검증", "writer": "신고 문구 초안 작성"}


async def execute_stage(job_id: str, agent: str, inputs: dict, objective: str, constraints: list[str],
                        memory_refs: list[str] | None = None,
                        initial_last_failure: str | None = None,
                        replan_incidents: set[str] | None = None,
                        incident_ids: dict[tuple[str, str], str] | None = None) -> dict:
    """한 단계를 실행한다: Handoff 생성 → 호출 → 호출마다 타임라인 1행.
    LLM_OUTPUT_INVALID / SERVICE_UNAVAILABLE인 경우 같은 단계 최대 3회 재시도.
    그 외 retry 또는 failed는 루프를 종료하고 run_job에 반환하여 재계획하도록 함.
    """
    last_failure = initial_last_failure
    active_incidents = {}
    generated_incidents = incident_ids if incident_ids is not None else {}
    result = None

    for attempt in range(1, MAX_STAGE_ATTEMPTS + 1):
        handoff = Handoff.model_validate({"task_id": job_id, "from": "supervisor", "to": agent,
            "objective": objective, "inputs": inputs, "constraints": list(constraints),
            "attempt": attempt, "last_failure": last_failure, "memory_refs": list(memory_refs or [])})
        started = perf_counter()
        result = StageResult.model_validate(await agents.call(handoff)).model_dump()
        status = result["status"]
        failure = result["failure"]
        inc_id = result.get("incident_id")
        if "SERVICE_UNAVAILABLE" in active_incidents.values() and failure != "SERVICE_UNAVAILABLE":
            evidence = result["data"].setdefault("evidence", {})
            outcome = "성공" if status == "success" else f"{status} · {failure}"
            evidence["fixed"] = f"재호출 {perf_counter() - started:.1f}초 · {outcome} · {evidence.get('fixed', '')}".rstrip(" ·")[:500]

        incident_key = (agent, failure) if failure else None
        if inc_id and incident_key:
            generated_incidents.setdefault(incident_key, inc_id)

        # Chaos가 주입한 장애는 incident_id를 포함하지만, 실제 운영 장애는 ID 없이
        # 발생할 수 있다. 업무 분기(NEEDS_MORE_INFO/OUT_OF_SCOPE)는 제외하고
        # 복구 지표 대상 장애에만 Supervisor ID를 부여한다.
        if not inc_id and failure in TRACKED_FAILURES:
            target = agent if failure == "SERVICE_UNAVAILABLE" else result["author"]
            if incident_key not in generated_incidents:
                generated_incidents[incident_key] = await incidents.claim_pending(job_id, target) or f"incident-{uuid4().hex}"
            inc_id = generated_incidents[incident_key]
            result["incident_id"] = inc_id

        timeline_agent = agent if failure == "SERVICE_UNAVAILABLE" else result["author"]
        recovery = result["data"].get("recovery", {})
        internal_recovered = inc_id and agent in {"classifier", "writer"} and (
            (status == "success" and failure is None) or
            (status in {"needs_user", "failed"} and failure in {"NEEDS_MORE_INFO", "OUT_OF_SCOPE"}))
        if internal_recovered:
            for number, failed_attempt in enumerate(recovery.get("attempts", []), 1):
                code = "PII_IN_DRAFT" if failed_attempt["error_type"] == "PII_IN_DRAFT" else "LLM_OUTPUT_INVALID"
                payload = {"task_id": job_id, "author": timeline_agent, "status": "retry", "failure": code,
                           "incident_id": inc_id, "data": {k: failed_attempt[k] for k in ("model", "error_type", "elapsed_ms")}}
                payload["data"]["evidence"] = failed_attempt.get("evidence") or {"broken":
                    f"{failed_attempt['model']} · {failed_attempt['error_type']} · {failed_attempt['elapsed_ms']}ms"[:500]}
                await store.query("INSERT INTO timeline_events(job_id,agent,event_type,attempt,payload,created_at) VALUES (%s,%s,%s,%s,%s,%s)",
                                  (job_id, timeline_agent, "retry", number, Jsonb(payload), failed_attempt["started_at"]))
        await store.query("INSERT INTO timeline_events(job_id,agent,event_type,attempt,payload) VALUES (%s,%s,%s,%s,%s)",
                          (job_id, timeline_agent, status, attempt, Jsonb(result)))

        if inc_id:
            if internal_recovered:
                first_error = next(iter(recovery.get("attempts", [])), {}).get("error_type")
                code = "PII_IN_DRAFT" if first_error == "PII_IN_DRAFT" else "LLM_OUTPUT_INVALID"
                await incidents.record_detected(inc_id, job_id, agent, code, detected_at=recovery.get("started_at"))
                await incidents.record_recovered(inc_id)
            elif failure == "STALE_INFO":
                await incidents.record_degraded(inc_id, job_id, result["author"], failure)
            elif failure in SAME_STAGE_RETRY_FAILURES:
                # SERVICE_UNAVAILABLE 응답의 author는 backend 래퍼이지만 실제 장애 대상은
                # 호출하려던 에이전트다.
                target = agent if failure == "SERVICE_UNAVAILABLE" else result["author"]
                await incidents.record_detected(inc_id, job_id, target, failure)
                active_incidents[inc_id] = failure
            elif failure in REPLAN_FAILURES:
                # 재계획 대상 실패는 execute_stage에서 detected만 기록하고 최종 실패/복구는 run_job에서 처리
                await incidents.record_detected(inc_id, job_id, result["author"], failure)
                if replan_incidents is not None:
                    replan_incidents.add(inc_id)
                else:
                    await incidents.record_failed(inc_id)
            elif status == "failed":
                await incidents.record_detected(inc_id, job_id, result["author"], failure)
                if failure == "INJECTION_BLOCKED":
                    await incidents.record_handled(inc_id, "blocked")
                else:
                    await incidents.record_failed(inc_id)
            elif status == "needs_user":
                await incidents.record_detected(inc_id, job_id, result["author"], failure)
        for active_id, active_failure in list(active_incidents.items()):
            if (status == "success" and failure is None) or (
                    active_failure == "SERVICE_UNAVAILABLE" and failure != "SERVICE_UNAVAILABLE"):
                await incidents.record_recovered(active_id)
                del active_incidents[active_id]
        if status == "success":
            break

        # 같은 단계 재시도는 status=retry이고 failure가 LLM_OUTPUT_INVALID 또는 SERVICE_UNAVAILABLE인 경우만
        if status == "retry" and failure in SAME_STAGE_RETRY_FAILURES:
            last_failure = failure
            if attempt < MAX_STAGE_ATTEMPTS:
                await agents.wait_before_retry(failure, attempt)
            continue
        else:
            # PII_IN_DRAFT, DRAFT_MISMATCH 등이나 failed, needs_user는 루프 종료하여 run_job으로 반환
            break

    if active_incidents:
        for active_id in active_incidents:
            await incidents.record_failed(active_id)
        active_incidents.clear()

    return result


async def classify(job_id: str, text: str, answers: dict | None, question_round: int,
                   state: dict, open_incidents: set[str]) -> tuple[dict | None, str | None]:
    """classifier 단계를 실행하고 분류 결과를 반환한다."""
    inputs = {"text": text, **({"answers": answers} if answers else {})}
    try:
        result = await execute_stage(job_id, "classifier", inputs, OBJECTIVES["classifier"], CONSTRAINTS)
    except ValueError:
        state["confirm_needed"].append("classifier 단계 응답이 계약과 맞지 않아 처리를 중단했습니다")
        return None, "failed"

    state["last_result"] = result
    failure = result.get("failure")
    status = result.get("status")

    if status == "needs_user":
        # 샘플처럼 매 회차 q1이 재사용돼도 이전 답변을 덮어쓰지 않도록
        # 계약에 이미 고정된 field를 안정적인 질문 ID로 사용한다.
        for question in result.get("data", {}).get("questions", []):
            if question["id"] not in {"q_what", "q_confirm_location"}:
                question["id"] = question["field"]

    if failure == "OUT_OF_SCOPE":
        data = result.get("data")
        if data and isinstance(data, dict):
            state["classification"] = data
            state["confirm_needed"].append("지원 범위 밖의 민원입니다")
            return data, None
        return None, "failed"

    if status == "needs_user":
        if question_round >= MAX_QUESTION_ROUNDS:
            state["confirm_needed"].append("추가 질문 2회 후에도 정보가 부족하여 상위 창구를 안내합니다")
            state["question_limit_reached"] = True
            data = result["data"]
            if data.get("category") is None or data.get("confidence", 0) < 0.7 or "category" in data.get("missing", []):
                data["category"] = "out_of_scope"
            state["classification"] = result["data"]
            return result["data"], None
        state["question_round"] = question_round + 1
        return None, "needs_user"

    if status == "success":
        state["classification"] = result["data"]
        return result["data"], None

    return None, "failed"



async def locate_and_verify(job_id: str, classified: dict, state: dict,
                            open_incidents: set[str],
                            force_national: bool = False) -> tuple[list[dict] | None, str | None]:
    """locator와 verifier(mode=channel)를 실행하며 창구 제외 및 지역 폴백 재계획을 수행한다."""
    category = classified["category"]
    raw_region = classified["region"]

    # 지역 폴백 순서: district -> city -> national
    if force_national or category == "out_of_scope":
        level = "national"
        region = {"sido": None, "sigungu": None, "dong": None, "place_text": raw_region.get("place_text")}
    elif raw_region.get("sido") and raw_region.get("sigungu"):
        level = "district"
        region = dict(raw_region)
    elif raw_region.get("sido"):
        level = "city"
        region = {"sido": raw_region.get("sido"), "sigungu": None, "dong": None, "place_text": raw_region.get("place_text")}
    else:
        level = "national"
        region = {"sido": None, "sigungu": None, "dong": None, "place_text": raw_region.get("place_text")}

    exclude_channel_ids = []
    replan_count = 0
    stage_incidents = set()
    stage_incident_ids: dict[tuple[str, str], str] = {}

    while True:
        locator_inputs = {"category": category, "region": region, "exclude_channel_ids": list(exclude_channel_ids)}
        try:
            loc_res = await execute_stage(job_id, "locator", locator_inputs, OBJECTIVES["locator"], CONSTRAINTS,
                                          replan_incidents=stage_incidents, incident_ids=stage_incident_ids)
        except ValueError:
            state["confirm_needed"].append("locator 단계 응답이 계약과 맞지 않아 처리를 중단했습니다")
            return None, "failed"

        state["last_result"] = loc_res
        open_incidents.update(stage_incidents)

        # 1. NO_CHANNEL 또는 locator 실패 시 지역 폴백
        if loc_res.get("failure") == "NO_CHANNEL" or loc_res.get("status") != "success":
            reported_level = loc_res.get("data", {}).get("fallback_level")
            if reported_level in {"district", "city", "national"}:
                level = reported_level
            if loc_res.get("failure") == "NO_CHANNEL" and replan_count < MAX_REPLANS:
                if level == "district":
                    level = "city"
                    region = {"sido": raw_region.get("sido"), "sigungu": None, "dong": None, "place_text": raw_region.get("place_text")}
                    replan_count += 1
                    continue
                elif level == "city":
                    level = "national"
                    region = {"sido": None, "sigungu": None, "dong": None, "place_text": raw_region.get("place_text")}
                    replan_count += 1
                    continue
            state["confirm_needed"].append("전국 단계까지 확인했지만 신고 창구를 찾지 못했습니다")
            return None, "failed"

        channels = loc_res.get("data", {}).get("channels", [])
        # locator가 다시 반환한 후보 중 이미 제외된 ID 방어
        candidate_channels = [c for c in channels if c["channel_id"] not in exclude_channel_ids]

        if not candidate_channels:
            if replan_count < MAX_REPLANS:
                if level == "district":
                    level = "city"
                    region = {"sido": raw_region.get("sido"), "sigungu": None, "dong": None, "place_text": raw_region.get("place_text")}
                    replan_count += 1
                    continue
                elif level == "city":
                    level = "national"
                    region = {"sido": None, "sigungu": None, "dong": None, "place_text": raw_region.get("place_text")}
                    replan_count += 1
                    continue
            state["confirm_needed"].append("전국 단계까지 확인했지만 신고 창구를 찾지 못했습니다")
            return None, "failed"

        # 2. verifier 창구 검증
        ver_inputs = {"category": category, "region": region, "mode": "channel", "channels": candidate_channels}
        try:
            ver_res = await execute_stage(job_id, "verifier", ver_inputs, OBJECTIVES["verifier"], CONSTRAINTS,
                                          replan_incidents=stage_incidents, incident_ids=stage_incident_ids)
        except ValueError:
            state["confirm_needed"].append("verifier 단계 응답이 계약과 맞지 않아 처리를 중단했습니다")
            return None, "failed"

        state["last_result"] = ver_res
        ver_failure = ver_res.get("failure")
        ver_status = ver_res.get("status")
        open_incidents.update(stage_incidents)

        if ver_failure in ("LINK_DEAD", "NOT_OFFICIAL"):
            passed_ids = set(ver_res.get("data", {}).get("passed_channel_ids", []))
            failed_ids = [c["channel_id"] for c in candidate_channels if c["channel_id"] not in passed_ids]
            for cid in failed_ids:
                if cid not in exclude_channel_ids:
                    exclude_channel_ids.append(cid)
            if replan_count < MAX_REPLANS:
                replan_count += 1
                continue
            state["confirm_needed"].append("확인 가능한 공식 창구를 찾지 못했습니다")
            return None, "failed"

        if ver_failure == "STALE_INFO":
            stale_msg = "신고 창구 확인일이 오래되었습니다"
            if stale_msg not in state["confirm_needed"]:
                state["confirm_needed"].append(stale_msg)

        if ver_status == "success":
            passed = [c for c in candidate_channels if c["channel_id"] in ver_res.get("data", {}).get("passed_channel_ids", [])]
            if not passed:
                for c in candidate_channels:
                    if c["channel_id"] not in exclude_channel_ids:
                        exclude_channel_ids.append(c["channel_id"])
                if replan_count < MAX_REPLANS:
                    replan_count += 1
                    continue
                state["confirm_needed"].append("확인 가능한 공식 창구를 찾지 못했습니다")
                return None, "failed"

            state["channels"] = passed
            for inc in list(stage_incidents):
                if inc in open_incidents:
                    await incidents.record_recovered(inc)
                    open_incidents.discard(inc)
            return passed, None

        return None, "failed"



async def write_and_verify(job_id: str, classified: dict, channels: list[dict],
                           state: dict, open_incidents: set[str]) -> tuple[dict | None, str | None]:
    """writer와 verifier(mode=draft)를 실행하며 문구 재작성 재계획을 수행한다."""
    channel = channels[0]
    context = {"category": classified["category"], "region": classified["region"]}
    replan_count = 0
    last_verifier_failure = None
    stage_incidents = set()
    stage_incident_ids: dict[tuple[str, str], str] = {}

    while True:
        writer_inputs = {**context, "details": classified["details"], "channel": channel}
        try:
            writer_res = await execute_stage(job_id, "writer", writer_inputs, OBJECTIVES["writer"], CONSTRAINTS,
                                             initial_last_failure=last_verifier_failure)
        except ValueError:
            state["confirm_needed"].append("writer 단계 응답이 계약과 맞지 않아 처리를 중단했습니다")
            return None, "failed"

        state["last_result"] = writer_res
        if writer_res.get("status") != "success":
            return None, "failed"

        draft = writer_res.get("data")

        ver_inputs = {**context, "mode": "draft", "draft": draft}
        try:
            ver_res = await execute_stage(job_id, "verifier", ver_inputs, OBJECTIVES["verifier"], CONSTRAINTS,
                                          replan_incidents=stage_incidents, incident_ids=stage_incident_ids)
        except ValueError:
            state["confirm_needed"].append("verifier 단계 응답이 계약과 맞지 않아 처리를 중단했습니다")
            return None, "failed"

        state["last_result"] = ver_res
        open_incidents.update(stage_incidents)

        ver_failure = ver_res.get("failure")
        ver_status = ver_res.get("status")

        if ver_failure in ("PII_IN_DRAFT", "DRAFT_MISMATCH"):
            if replan_count < MAX_REPLANS:
                replan_count += 1
                last_verifier_failure = ver_failure
                continue
            state["confirm_needed"].append("안전한 신고 문구를 만들지 못했습니다")
            return None, "failed"

        if ver_status == "success":
            for inc in list(stage_incidents):
                if inc in open_incidents:
                    await incidents.record_recovered(inc)
                    open_incidents.discard(inc)
            return draft, None

        return None, "failed"



def result_data(state: dict, draft: dict | None = None) -> dict:
    classified = state.get("classification", {})
    channels = state.get("channels", [])
    return {
        **{key: classified[key] for key in ("category", "region") if key in classified},
        "channels": channels,
        "draft": draft or {"title": "", "body": "", "fill_in": [], "removed_pii": []},
        "confirm_needed": list(dict.fromkeys(state["confirm_needed"])),
        "sources": [{"url": c["source_url"], "checked_at": c["verified_at"]} for c in channels],
    }


async def request_pii(job_id, lang="ko", has_photo=False, text=None):
    payload = {"task_id": job_id, "author": "backend", "status": "needs_user", "failure": "NEEDS_MORE_INFO",
               "incident_id": None, "data": {"questions": [{"id": "q_pii", "field": "pii", "text":
                   "개인정보(이름·전화·이메일·주소 상세)는 빼고 다시 적어 주세요. 신고 문구에는 개인정보가 자동으로 들어가지 않아요"}]}}
    state = {"question_round": 0, "confirm_needed": [], "lang": lang, "has_photo": has_photo, "last_result": payload}
    await store.query("INSERT INTO timeline_events(job_id,agent,event_type,attempt,payload) VALUES (%s,%s,%s,%s,%s)",
                      (job_id, "backend", "needs_user", 1, Jsonb(payload)))
    await store.query("UPDATE jobs SET status=%s,state=%s,completed_at=CASE WHEN %s IN ('success','failed') THEN now() ELSE NULL END WHERE id=%s",
                      ("needs_user", Jsonb(state), "needs_user", job_id))
    # text = 개인정보를 가린 원문. 화면 제목이 입력 원문 대신 이걸 보여 준다
    return {"id": job_id, "status": "needs_user", "state": state, "text": text}


async def run_job(job_id: str, text: str, answers: dict | None = None, question_round: int = 0, lang: str = "ko", has_photo: bool = False, answers_redacted: bool = False):
    """전체 신고 파이프라인(분류 → 창구 탐색/검증 → 문구 작성/검증 → 최종 검증)을 총괄 실행한다."""
    state = {"question_round": question_round, "confirm_needed": []}
    if answers_redacted:
        state["answers_redacted"] = True
        state["confirm_needed"].append("답변에 있던 개인정보는 지우고 처리했어요")
    originals = [text, *(answers or {}).values()] if lang != "ko" else []
    if lang != "ko":
        state.update(lang=lang, original_text=text)
        translated = await translate.translate({"text": text}, lang, "ko")
        translated_answers = await translate.translate({"a": answers}, lang, "ko") if answers else {"a": answers}
        if translated is None or translated_answers is None:
            state["confirm_needed"].append("번역에 실패해 원문으로 처리했습니다")
        text = translated["text"] if translated else text
        answers = translated_answers["a"] if translated_answers else answers
        state["text_ko"] = text
    if has_photo:
        answers = {"q_has_photo": "있음", **(answers or {})}
        state["has_photo"] = True
    open_incidents = set()

    async def finish(status="success"):
        if status == "failed":
            if open_incidents:
                for inc in open_incidents:
                    await incidents.record_failed(inc)
                open_incidents.clear()
            failure = state.get("last_result", {}).get("failure")
            if failure == "SERVICE_UNAVAILABLE":
                state["confirm_needed"].append("응답이 없는 단계가 있어 확인된 데까지만 안내합니다")
            elif failure == "LLM_OUTPUT_INVALID":
                state["confirm_needed"].append("응답 내용을 확인하지 못해 확인된 데까지만 안내합니다")
            elif failure in REPLAN_FAILURES:
                state["confirm_needed"].append("검증을 통과하지 못한 부분은 공식 페이지에서 확인해 주세요")
            elif failure == "INJECTION_BLOCKED":
                state["confirm_needed"].append("공격 의심 문구가 감지되어 처리를 중단했습니다")
            elif not state["confirm_needed"]:
                state["confirm_needed"].append("처리를 완료하지 못해 확인된 정보까지만 안내합니다")
            state.setdefault("result", result_data(state))
            state["result"]["confirm_needed"] = list(dict.fromkeys(state["confirm_needed"]))
        if lang != "ko":
            visible = {"questions": {q["id"]: q["text"] for q in state.get("last_result", {}).get("data", {}).get("questions", [])},
                       "confirm_needed": list(dict.fromkeys(state["confirm_needed"])), "results": [
                           {"channels": [{k: c[k] for k in ("name", "org", "jurisdiction")} for c in r["channels"]],
                            "draft": {k: r["draft"][k] for k in ("title", "body")}}
                           for r in state.get("results", [state["result"]] if "result" in state else [])]}
            localized = await translate.translate(visible, "ko", lang)
            if localized is not None:
                state["i18n"] = {"lang": lang, **localized}
        await store.query(
            "UPDATE jobs SET status=%s,state=%s,completed_at=CASE WHEN %s IN ('success','failed') THEN now() ELSE NULL END WHERE id=%s",
            (status, Jsonb(state), status, job_id)
        )
        return {"id": job_id, "status": status, "state": state}

    # 사용자 설명 및 추가 답변 내 공격 문구(Prompt Injection) 사전 차단
    # 차단 여부는 탐지기 판단 그대로, 잡힌 조각은 증거 표시용으로만 쓴다
    attack = next((injection_fragment(value) or "공격 문구 감지" for value in [*originals, text, *(answers or {}).values()]
                   if is_injection(value)[0]), None)
    if attack:
        inc_id = f"incident-{uuid4().hex}"
        payload = {
            "task_id": job_id,
            "author": "backend",
            "status": "failed",
            "data": {"evidence": {"broken": attack, "fixed": "차단 · 이번 입력의 에이전트 호출 0"}},
            "failure": "INJECTION_BLOCKED",
            "incident_id": inc_id,
        }
        await store.query(
            "INSERT INTO timeline_events(job_id,agent,event_type,attempt,payload) VALUES (%s,%s,%s,%s,%s)",
            (job_id, "backend", "failed", 1, Jsonb(payload)),
        )
        await incidents.record_detected(inc_id, job_id, "backend", "INJECTION_BLOCKED")
        await incidents.record_handled(inc_id, "blocked")

        state["last_result"] = payload
        return await finish("failed")

    # 실제 불편만 후보로 삼고, 복수 유형의 불확실성은 classifier의 되묻기를 따른다.
    keywords = "|".join(re.escape(re.sub(r"\s+", "", w)) for words in CATEGORIES.values() for w in words)
    category_answers = [v for k, v in (answers or {}).items() if k not in {
        "location_detail", "q_location_detail", "q_confirm_location", "region", "q_region"}]
    combined = re.sub(r"\s+", "", "\n".join([text, *category_answers])).lower()
    combined = re.sub(r"(?:" + keywords + r")(?:의)?(?:옆|앞|밑|위|아래|근처|쪽|건너편|맞은편)", "", combined)
    combined = re.sub(r"(?:" + keywords + r")(?:은|는|이|가)?"
                      r"(?:멀쩡(?!하지)|정상(?:입니다|이에요|인데|이고)|없(?:고|어요|습니다))", "", combined)
    detected = [c for c, words in CATEGORIES.items() if any(re.sub(r"\s+", "", w) in combined for w in words)]

    classified, stop = await classify(job_id, text, answers, question_round, state, open_incidents)
    if stop:
        return await finish(stop)

    classified_items = [classified]
    if classified["category"] != "out_of_scope" and state["last_result"]["status"] != "needs_user":
        for category, words in CATEGORIES.items():
            if category == classified["category"] or category not in detected:
                continue
            extra_state = {**state, "confirm_needed": []}
            extra, stop = await classify(job_id, text, {**(answers or {}), "q_category": category},
                                         0, extra_state, open_incidents)
            if stop or not extra or extra["category"] != category:
                state["confirm_needed"].append(f"{CATEGORY_LABELS[category]} 건은 정보가 부족해 안내하지 못했습니다")
            else:
                classified_items.append(extra)

    async def pipeline(item):
        sub = {**state, "classification": item, "confirm_needed": list(state["confirm_needed"])}
        pending = set()
        draft = None
        channels, stop = await locate_and_verify(job_id, item, sub, pending)
        if not stop and item["category"] != "out_of_scope":
            draft, stop = await write_and_verify(job_id, item, channels, sub, pending)
        if stop:
            # 이 유형만 실패했으면 그 건의 미복구 incident는 여기서 마감한다(전체 결과와 무관).
            for inc in pending:
                await incidents.record_failed(inc)
        else:
            open_incidents.update(pending)
        if stop and len(classified_items) > 1:
            sub["confirm_needed"].append(f"{CATEGORY_LABELS.get(item['category'], item['category'])} 건은 안내를 끝내지 못했습니다")
        try:
            final = validate("final", result_data(sub, draft))
        except ValueError:
            sub["confirm_needed"].append("최종 결과가 계약과 맞지 않아 처리를 중단했습니다")
            final, stop = result_data(sub), "failed"
        return sub, final, stop

    completed = await asyncio.gather(*(pipeline(item) for item in classified_items))
    state["classifications"] = classified_items
    state["results"] = [final for _, final, _ in completed]
    state["result"] = state["results"][0]
    state["channels"] = state["result"]["channels"]
    state["confirm_needed"] = list(dict.fromkeys(line for sub, _, _ in completed for line in sub["confirm_needed"]))
    failed = [sub for sub, _, stop in completed if stop]
    state["last_result"] = (failed[0] if failed else completed[0][0]).get("last_result")
    return await finish("success" if any(not stop for _, _, stop in completed) else "failed")
