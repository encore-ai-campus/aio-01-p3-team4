import asyncio
import smtplib
from fastapi import HTTPException
from redis.asyncio import Redis
from app.clients import relay, smtp
from app.clients.chaos import ChaosClient
from app.core.config import REDIS_URL, SERVICE
from app.core import config
from app.schemas.contract import Handoff, StageResult
from app.services import contacts, outbox

RETRY_DELAYS = (0.3, 0.6)
CATEGORY = {"streetlight": "가로등 고장", "road_damage": "도로 파손", "illegal_dumping": "쓰레기 무단투기", "out_of_scope": "기타"}

def result(task_id, item, failure=None):
    return StageResult.model_validate({"task_id": task_id, "author": SERVICE,
        "status": "failed" if failure == "MAIL_BLOCKED" else "retry" if failure else "success",
        "data": {**{key: item[key] for key in ("message_id", "to", "dept", "officer", "status", "receipt_no", "attempts")},
                 **({"evidence": item["evidence"]} if item.get("evidence") else {})},
        "failure": failure, "incident_id": item.get("incident_id")})

async def run(handoff: Handoff) -> StageResult:
    if handoff.to != SERVICE:
        raise HTTPException(422, "수신 서비스가 다릅니다")
    store = outbox.get()
    async with store.lock:
        item = store.items.get(handoff.task_id)
        if item and item["status"] == "sent":
            return result(handoff.task_id, item)
        if item is None:
            inputs = handoff.inputs
            contact = contacts.find(inputs["channel"]["channel_id"])
            citizen = inputs.get("citizen", {}).get("email", config.MAIL_CITIZEN)
            item = {"task_id": handoff.task_id, "message_id": None, "to": contact["email"],
                    "dept": contact["dept"], "officer": contact["officer"], "status": "blocked",
                    "receipt_no": None, "attempts": 0}
            for address in (contact["email"], citizen, config.MAIL_SENDER):
                if not smtp.is_virtual(address):
                    item["to"] = address
                    return result(handoff.task_id, item, "MAIL_BLOCKED")
            region = " ".join(v for v in inputs["region"].values() if v)
            sender = inputs.get("citizen", {}).get("name")
            signature = f"보낸 사람: {sender} <{citizen}>\n\n" if sender else ""
            item.update(message_id=f"<{handoff.task_id}.report@minwon.example>", citizen=citizen,
                        subject="[가상 민원] " + inputs["draft"]["title"],
                        body=f"{signature}{inputs['draft']['body']}\n\n유형: {CATEGORY[inputs['category']]} / 지역: {region}\n{outbox.FOOTER}",
                        status="queued", last_error=None, incident_id=None, queued_at=outbox.now().isoformat(),
                        sent_at=None, reply_due_at=None, reply_sent_at=None)

        async def deliver():
            for attempt in range(3):
                item["attempts"] += 1
                try:
                    await smtp.send(outbox.message(item))
                except smtp.Blocked as exc:
                    return result(handoff.task_id, {**item, "message_id": None, "to": str(exc),
                                                   "status": "blocked", "attempts": 0}, "MAIL_BLOCKED")
                except (OSError, smtplib.SMTPException) as exc:
                    item["last_error"] = str(exc)
                    item.setdefault("evidence", {"broken": f"메일 발송 실패 · {item['attempts']}차 시도 {type(exc).__name__}"})
                    if attempt < 2:
                        await asyncio.sleep(RETRY_DELAYS[attempt])
                else:
                    store.sent(item, outbox.now())
                    relay.schedule_copy(item)
                    return result(handoff.task_id, item)
            store.save(item)
            return result(handoff.task_id, item, "MAIL_SEND_FAILED")

        async def inject(flag):
            if flag.kind != "mail_down":
                return await deliver()
            item["incident_id"] = flag.incident_id
            return await deliver()

        redis = Redis.from_url(REDIS_URL, socket_connect_timeout=0.5, socket_timeout=0.5) if REDIS_URL else None
        try:
            return await ChaosClient(redis).run(SERVICE, deliver, inject)
        finally:
            if redis:
                await redis.aclose()
