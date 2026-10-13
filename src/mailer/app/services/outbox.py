import asyncio
import json
import smtplib
from datetime import datetime, timedelta
from email.message import EmailMessage
from pathlib import Path
from zoneinfo import ZoneInfo
from app.clients import relay, smtp
from app.core import config

KST = ZoneInfo("Asia/Seoul")
FOOTER = "※ 생활 민원 길잡이 시연용 가상 메일입니다. 실제 기관에는 전달되지 않습니다."
box = None

def now():
    return datetime.now(KST)

def message(item, reply=False):
    msg = EmailMessage()
    msg["From"] = item["to"] if reply else config.MAIL_SENDER
    msg["To"] = item["citizen"] if reply else item["to"]
    if reply:
        msg["Subject"] = f"[가상 회신] 접수되었습니다 · 접수번호 {item['receipt_no']}"
        msg["In-Reply-To"] = item["message_id"]
        title = item["subject"].removeprefix("[가상 민원] ")
        body = f"{item['dept']} {item['officer']}(가상 담당자)입니다. '{title}' 민원이 접수되었습니다. 접수번호: {item['receipt_no']}\n\n{FOOTER}"
    else:
        msg["Subject"], msg["Message-ID"] = item["subject"], item["message_id"]
        msg["Reply-To"], msg["X-Minwon-Task"] = item["citizen"], item["task_id"]
        body = item["body"]
    msg.set_content(body)
    return msg

class Outbox:
    def __init__(self, path=None):
        self.path = Path(path or config.OUTBOX_PATH)
        self.items = json.loads(self.path.read_text()) if self.path.exists() else {}
        self.lock = asyncio.Lock()

    def save(self, item):
        self.items[item["task_id"]] = item
        self.items = dict(sorted(self.items.items(), key=lambda pair: pair[1]["queued_at"])[-100:])
        self.path.write_text(json.dumps(self.items, ensure_ascii=False, indent=2) + "\n")

    def sent(self, item, time):
        prefix = time.astimezone(KST).strftime("%Y-%m%d-")
        count = sum((r["receipt_no"] or "").startswith(prefix) for r in self.items.values())
        item.update(status="sent", receipt_no=f"{prefix}{count + 1:04d}", last_error=None,
                    sent_at=time.isoformat(), reply_due_at=(time + timedelta(seconds=config.REPLY_DELAY_SEC)).isoformat())
        if item.get("evidence"):
            action = "편지함 대기 후 재발송" if item["task_id"] in self.items else "재발송"
            item["evidence"]["fixed"] = f"{action} 성공 {str(item['sent_at'])[11:19]} · 접수번호 {item['receipt_no']}"
        self.save(item)

def get():
    global box
    if box is None:
        box = Outbox()
    return box

async def flush(time):
    store = get()
    async with store.lock:
        for item in list(store.items.values()):
            if item["status"] == "queued":
                item["attempts"] += 1
                try:
                    await smtp.send(message(item))
                except (OSError, smtplib.SMTPException, smtp.Blocked) as exc:
                    item["last_error"] = str(exc)
                    store.save(item)
                    print(f"mailer task={item['task_id']} queued error={type(exc).__name__}")
                    continue
                store.sent(item, time)
                relay.schedule_copy(item)
                print(f"mailer task={item['task_id']} sent receipt={item['receipt_no']}")
            if not item["reply_sent_at"] and datetime.fromisoformat(item["reply_due_at"]) <= time:
                try:
                    await smtp.send(message(item, reply=True))
                except (OSError, smtplib.SMTPException, smtp.Blocked) as exc:
                    item["last_error"] = str(exc)
                    print(f"mailer task={item['task_id']} reply_retry error={type(exc).__name__}")
                else:
                    item.update(reply_sent_at=time.isoformat(), last_error=None)
                    relay.schedule_copy(item, reply=True)
                    print(f"mailer task={item['task_id']} replied receipt={item['receipt_no']}")
                store.save(item)
            for reply in (False, True):
                prefix = "relay_reply" if reply else "relay"
                if item["status"] == "sent" and item.get(prefix + "_error") and (not reply or item.get("reply_sent_at")):
                    relay.schedule_copy(item, reply)
