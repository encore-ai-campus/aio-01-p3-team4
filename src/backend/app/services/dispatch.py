import os
import httpx
from uuid import uuid4
from psycopg.types.json import Jsonb
from app.clients import agents, store
from app.schemas.contract import Handoff, StageResult
from app.services import incidents

MAX_ATTEMPTS = 3  # 같은 task_id로 다시 불러도 mailer가 중복 발송을 막는다
TRACKED_FAILURES = {"MAIL_SEND_FAILED", "MAIL_BLOCKED", "SERVICE_UNAVAILABLE"}


class MailerContractError(Exception):
    """mailer 응답이 StageResult 계약을 충족하지 않을 때 발생한다."""


async def send(job: dict, title: str, body: str, index: int = 0, citizen: dict | None = None) -> dict:
    """결과가 나온 작업의 신고 문구를 가상 담당자에게 보낸다(mailer). 진행 기록은 agent=mailer."""
    state = job.get("state") or {}
    result = (state.get("results") or [state.get("result") or {}])[index]
    channels = result.get("channels") or []
    inputs = {
        "category": result.get("category"),
        "region": result.get("region"),
        "channel": channels[0],
        "draft": {"title": title, "body": body},
    }
    if citizen is not None:
        inputs["citizen"] = citizen
    last_failure = None
    active_incidents = set()
    generated_incidents = {}

    for attempt in range(1, MAX_ATTEMPTS + 1):
        handoff = Handoff.model_validate({
            "task_id": f"{job['id']}:{index}" if index else job["id"],
            "from": "supervisor",
            "to": "mailer",
            "objective": "확인된 신고 문구를 가상 담당자에게 발송",
            "inputs": inputs,
            "constraints": ["가상 주소(.example)로만 발송"],
            "attempt": attempt,
            "last_failure": last_failure,
            "memory_refs": [],
        })
        try:
            mail = StageResult.model_validate(await agents.call(handoff)).model_dump()
        except (TypeError, ValueError) as exc:
            raise MailerContractError("mailer 응답이 계약과 맞지 않습니다") from exc

        failure = mail.get("failure")
        inc_id = mail.get("incident_id")

        if not inc_id and failure in TRACKED_FAILURES:
            if failure not in generated_incidents:
                generated_incidents[failure] = await incidents.claim_pending(job["id"], "mailer") or f"incident-{uuid4().hex}"
            inc_id = generated_incidents[failure]
            mail["incident_id"] = inc_id

        if inc_id and failure:
            if inc_id not in active_incidents:
                await incidents.record_detected(inc_id, job["id"], "mailer", failure)
                active_incidents.add(inc_id)

        await store.query(
            "INSERT INTO timeline_events(job_id,agent,event_type,attempt,payload) VALUES (%s,%s,%s,%s,%s)",
            (job["id"], "mailer", mail["status"], attempt, Jsonb(mail)),
        )

        if mail["status"] == "success":
            if failure is None and active_incidents:
                for active_id in list(active_incidents):
                    await incidents.record_recovered(active_id)
                active_incidents.clear()
            break

        if mail["status"] != "retry":
            break

        last_failure = failure
        if attempt < MAX_ATTEMPTS:
            await agents.wait_before_retry(failure, attempt)

    if active_incidents and mail.get("data", {}).get("status") != "queued":
        for active_id in active_incidents:
            await incidents.record_failed(active_id)
        active_incidents.clear()

    update = {"mail": mail} if index == 0 else {}
    if len(state.get("results", [])) > 1:
        state.setdefault("mails", {})[str(index)] = mail
        await store.query("UPDATE jobs SET state = state || %s || jsonb_build_object('mails', "
                          "COALESCE(state->'mails', '{}'::jsonb) || %s) WHERE id=%s",
                          (Jsonb(update), Jsonb({str(index): mail}), job["id"]))
    else:
        await store.query("UPDATE jobs SET state = state || %s WHERE id=%s", (Jsonb(update), job["id"]))
    state.update(update)
    return {"id": job["id"], "status": mail["status"], "mail": mail.get("data", {}), "failure": mail.get("failure")}


async def reconcile_outbox():
    """보낼 편지함에서 뒤늦게 실제 발송된 메일을 작업 기록(timeline·state)과 장애 복구에 반영한다."""
    async with httpx.AsyncClient(timeout=3) as client:
        response = await client.get(os.getenv("MAILER_URL", "http://mailer:8000") + "/outbox")
        response.raise_for_status()
    for item in response.json()["items"]:
        if item.get("status") != "sent" or not item.get("sent_at"):
            continue
        job_id, _, index = item["task_id"].partition(":")
        rows = await store.query("SELECT * FROM jobs WHERE id=%s", (job_id,), fetch=True)
        if not rows:
            continue
        state = rows[0].get("state") or {}
        current = (state.get("mails") or {}).get(index) if index else state.get("mail")
        # 발송 요청 당시 queued로 끝난 작업만 대상. 이미 sent로 기록된 작업은 건너뛴다.
        if not current or (current.get("data") or {}).get("status") != "queued":
            continue
        mail = {"task_id": item["task_id"], "author": "mailer", "status": "success",
                "data": {key: item.get(key) for key in ("message_id", "to", "dept", "officer", "status", "receipt_no", "attempts")},
                "failure": None, "incident_id": item.get("incident_id")}
        mail["data"]["evidence"] = {**item.get("evidence", {}),
            "fixed": f"편지함 대기 후 재발송 성공 {str(item['sent_at'])[11:19]} · 접수번호 {item['receipt_no']}"}
        await store.query(
            "INSERT INTO timeline_events(job_id,agent,event_type,attempt,payload) VALUES (%s,%s,%s,%s,%s)",
            (job_id, "mailer", "success", int(item.get("attempts") or 1), Jsonb(mail)),
        )
        if index:
            await store.query("UPDATE jobs SET state = state || jsonb_build_object('mails', "
                              "COALESCE(state->'mails', '{}'::jsonb) || %s) WHERE id=%s",
                              (Jsonb({index: mail}), job_id))
        else:
            await store.query("UPDATE jobs SET state = state || %s WHERE id=%s", (Jsonb({"mail": mail}), job_id))
        if item.get("incident_id"):
            await store.query(
                "UPDATE incidents SET recovered_at=%s, result='recovered', human_intervention=false "
                "WHERE id=%s AND job_id=%s AND target='mailer' AND detected_at IS NOT NULL "
                "AND recovered_at IS NULL",
                (item["sent_at"], item["incident_id"], job_id),
            )
