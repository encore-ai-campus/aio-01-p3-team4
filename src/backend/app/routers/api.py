import asyncio
import json
import logging
import re
from uuid import uuid4
from typing import Literal
import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator, model_validator
from psycopg.types.json import Jsonb
from app.clients import store
from app.clients.chaos import ChaosFlag
from app.services.supervisor import run_job, request_pii
from app.services.pii import redact
from app.services import dispatch, review
from app.services import incidents as incident_service
from app.services import ops

router = APIRouter()
background_jobs: set[asyncio.Task] = set()
logger = logging.getLogger(__name__)


async def start_job(job_id, text, answers=None, question_round=0, wait=False, lang="ko", has_photo=False, answers_redacted=False):
    if wait:
        return await run_job(job_id, text, answers, question_round=question_round, lang=lang, has_photo=has_photo, answers_redacted=answers_redacted)
    state = {"question_round": question_round, "confirm_needed": []}
    if lang != "ko":
        state.update(lang=lang, original_text=text)
    if has_photo:
        state["has_photo"] = True

    async def execute():
        try:
            await run_job(job_id, text, answers, question_round=question_round, lang=lang, has_photo=has_photo, answers_redacted=answers_redacted)
        except Exception:
            logger.exception("Background job failed: %s", job_id)
            failed_state = {**state, "confirm_needed": ["처리 중 오류가 발생했어요. 잠시 후 다시 시도해 주세요."]}
            try:
                await store.query("UPDATE jobs SET status='failed',state=%s,completed_at=now() WHERE id=%s",
                                  (Jsonb(failed_state), job_id))
            except Exception:
                logger.exception("Could not persist failed job: %s", job_id)

    task = asyncio.create_task(execute())
    background_jobs.add(task)
    task.add_done_callback(background_jobs.discard)
    return {"id": job_id, "status": "running", "state": state}


class JobInput(BaseModel):
    text: str = Field(min_length=1, max_length=500)
    lang: Literal["ko", "en", "ja", "zh", "vi"] = "ko"
    has_photo: bool = Field(default=False, strict=True)

    @field_validator("text")
    @classmethod
    def validate_text(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("민원 설명은 빈 값일 수 없습니다")
        return v
class Answers(BaseModel):
    answers: dict[str, str]

    @model_validator(mode="after")
    def validate_answers(self):
        if not self.answers:
            raise ValueError("답변은 하나 이상이어야 합니다")
        for ans in self.answers.values():
            if not ans or not ans.strip():
                raise ValueError("답변은 빈 값일 수 없습니다")
            if len(ans) > 500:
                raise ValueError("답변은 500자 이하여야 합니다")
        return self
class Draft(BaseModel):
    index: int = Field(default=0, ge=0, strict=True)
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=5000)

    @field_validator("title", "body")
    @classmethod
    def validate_not_blank(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("빈 값일 수 없습니다")
        return v

class Citizen(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    email: str = Field(max_length=254)

    @field_validator("email")
    @classmethod
    def virtual_email(cls, value: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9._%+-]+@([A-Za-z0-9-]+\.)+example", value, re.IGNORECASE):
            raise ValueError("시연 계정의 .example 메일 주소만 사용할 수 있어요")
        return value


class SendDraft(Draft):
    citizen: Citizen | None = None

@router.get("/health")
def health():
    return {"status": "ok", "service": "backend"}

@router.post("/jobs", status_code=201)
async def create_job(body: JobInput, wait: bool = False):
    job_id = "job-" + uuid4().hex
    text, removed = redact(body.text)
    await store.query("INSERT INTO jobs(id,text,status) VALUES (%s,%s,%s)", (job_id, text, "running"))
    if removed:
        return await request_pii(job_id, body.lang, body.has_photo, text)
    return await start_job(job_id, text, wait=wait, lang=body.lang, has_photo=body.has_photo)

@router.get("/jobs/{id}")
async def get_job(id: str):
    rows = await store.query("SELECT * FROM jobs WHERE id=%s", (id,), fetch=True)
    if not rows:
        raise HTTPException(404, "작업 없음")
    job = dict(rows[0])
    if isinstance(job.get("state"), str):
        try:
            job["state"] = json.loads(job["state"])
        except Exception:
            pass
    return job

@router.get("/jobs/{id}/timeline")
async def timeline(id: str):
    await get_job(id)
    rows = await store.query("SELECT * FROM timeline_events WHERE job_id=%s ORDER BY id", (id,), fetch=True)
    results = []
    for r in (rows or []):
        event = dict(r)
        if isinstance(event.get("payload"), str):
            try:
                event["payload"] = json.loads(event["payload"])
            except Exception:
                pass
        results.append(event)
    return results

@router.post("/jobs/{id}/answers")
async def answer_job(id: str, body: Answers, wait: bool = False):
    job = await get_job(id)
    if job["status"] != "needs_user":
        raise HTTPException(409, "추가 질문 대기 중인 작업이 아닙니다")
    last_data = job["state"].get("last_result", {}).get("data", {})
    questions = {q["id"] for q in last_data.get("questions", [])}
    # 현재 질문 ID 전체와 답변 정확히 일치해야 함
    if set(body.answers) != questions:
        raise HTTPException(422, "현재 질문에 모두 답변해야 합니다")

    # needs_user → running 선점과 답변 INSERT를 한 트랜잭션으로 처리한다.
    answers, removed = redact(body.answers)
    rows = await store.claim_and_store_answers(id, answers)
    if rows is None:
        raise HTTPException(409, "이미 처리 중이거나 완료된 작업입니다")
    if "q_pii" in questions:
        text = answers["q_pii"]
        await store.query("UPDATE jobs SET text=%s WHERE id=%s", (text, id))
        if removed:
            return await request_pii(id, job["state"].get("lang", "ko"), job["state"].get("has_photo", False), text)
        return await start_job(id, text, wait=wait, lang=job["state"].get("lang", "ko"), has_photo=job["state"].get("has_photo", False))
    return await start_job(id, job["text"], {r["question_id"]: r["answer"] for r in rows if r["question_id"] != "q_pii"},
                           question_round=job["state"].get("question_round", 0), wait=wait,
                           lang=job["state"].get("lang", "ko"), has_photo=job["state"].get("has_photo", False),
                           answers_redacted=bool(removed) or job["state"].get("answers_redacted", False))

def draft_result(job, index):
    state = job.get("state") or {}
    results = state.get("results") or [state.get("result")]
    if index >= len(results):
        raise HTTPException(422, "민원 번호가 올바르지 않아요")
    result = results[index]
    channels = result.get("channels") if isinstance(result, dict) else None
    if job.get("status") != "success" or not channels or (state.get("results") and not result["draft"]["body"]):
        raise HTTPException(409, "결과가 나온 작업만 보낼 수 있습니다")
    return result


@router.post("/jobs/{id}/review")
async def review_job(id: str, body: Draft):
    job = await get_job(id)
    return await review.review(job, draft_result(job, body.index), body.title, body.body, body.index)


@router.post("/jobs/{id}/send")
async def send_job(id: str, body: SendDraft):
    job = await get_job(id)
    result = draft_result(job, body.index)
    checked = review.check(result, body.title, body.body)
    if checked["verdict"] == "block":
        return JSONResponse(status_code=409, content={"detail": "개인정보나 비공식 링크를 고친 뒤 보내 주세요", "issues": checked["issues"]})
    try:
        return await dispatch.send(job, body.title, body.body, body.index, body.citizen.model_dump() if body.citizen else None)
    except dispatch.MailerContractError as exc:
        raise HTTPException(502, "가상 발송 서비스 응답이 계약과 맞지 않습니다") from exc

@router.get("/metrics")
async def metrics():
    return await incident_service.get_metrics()


@router.get("/ops/overview")
async def ops_overview(hours: int = 0):
    if hours not in (0, 24, 168):
        raise HTTPException(422, "기간은 24시간, 7일, 전체 중에서 선택해 주세요")
    return await ops.overview(hours)

@router.get("/incidents")
async def incidents():
    rows = await store.query(
        "SELECT * FROM incidents "
        "ORDER BY COALESCE(detected_at, injected_at) DESC NULLS LAST, id DESC",
        fetch=True,
    )
    return [incident_service.normalize_incident_for_api(r) for r in (rows or [])]

@router.api_route("/chaos/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def proxy_chaos(path: str, request: Request):
    import os
    url = os.getenv("CHAOS_URL", "http://chaos:8000")
    body = await request.body()
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.request(request.method, f"{url}/{path}", content=body, params=request.query_params,
                                        headers={"content-type": request.headers.get("content-type", "application/json")})
    if request.method == "PUT" and 200 <= response.status_code < 300:
        clean_path = path.strip("/")
        if clean_path.startswith("flags/"):
            target = clean_path.split("flags/", 1)[1].strip("/")
            flag = ChaosFlag.model_validate_json(body)
            await incident_service.record_injected(flag.incident_id, target, flag.kind, flag.injected_at.isoformat())
    if request.method == "POST" and path.strip("/") == "run" and 200 <= response.status_code < 300:
        selected = response.json()
        await incident_service.record_injected(selected["incident_id"], selected["target"], selected["kind"], selected["injected_at"])
    from fastapi.responses import Response
    return Response(response.content, response.status_code, media_type="application/json")
