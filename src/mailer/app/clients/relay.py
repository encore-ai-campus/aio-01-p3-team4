import asyncio
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr
from app.core import config

_pending = {}
COPY_NOTICE = "— 이 메일은 시연용 실제 수신함 복사본입니다. 실제 기관에는 전달되지 않았습니다."

def copy_message(item, reply=False) -> EmailMessage:
    from app.services.outbox import message

    msg = message(item, reply)
    name = f"{item['dept']} {item['officer']} (가상 담당자)" if reply else "생활 민원 길잡이 (시연)"
    msg.replace_header("From", formataddr((name, config.MAIL_RELAY_USER)))
    msg.replace_header("To", config.MAIL_RELAY_TO)
    report_id = f"<{item['task_id']}.relay@minwon.example>"
    if reply:
        msg.replace_header("In-Reply-To", report_id)
        msg["References"] = report_id
        msg["Message-ID"] = f"<{item['task_id']}.relay_reply@minwon.example>"
    else:
        msg.replace_header("Message-ID", report_id)
    msg.set_content(msg.get_content().rstrip("\n") + "\n\n" + COPY_NOTICE)
    return msg

async def send_copy(item, reply=False) -> bool:
    if not config.relay_enabled():
        return False
    try:
        msg = copy_message(item, reply)

        def deliver():
            with smtplib.SMTP(config.MAIL_RELAY_HOST, config.MAIL_RELAY_PORT,
                              timeout=config.MAIL_RELAY_TIMEOUT_SEC) as client:
                client.starttls(context=ssl.create_default_context())
                client.login(config.MAIL_RELAY_USER, config.MAIL_RELAY_PASSWORD)
                client.send_message(msg, from_addr=config.MAIL_RELAY_USER, to_addrs=[config.MAIL_RELAY_TO])

        await asyncio.wait_for(asyncio.to_thread(deliver), timeout=config.MAIL_RELAY_TIMEOUT_SEC)
        return True
    except Exception as exc:
        print(f"mailer task={item['task_id']} relay{'_reply' if reply else ''} error={type(exc).__name__}")
        return False

def schedule_copy(item, reply=False):
    if not config.relay_enabled():
        return
    prefix = "relay_reply" if reply else "relay"
    key = (item["task_id"], reply)
    if item.get(prefix + "_sent_at") or key in _pending or item.get(prefix + "_attempts", 0) >= 3:
        return
    item[prefix + "_attempts"] = item.get(prefix + "_attempts", 0) + 1
    _pending[key] = asyncio.create_task(_relay(item, reply))

async def _relay(item, reply=False):
    from app.services import outbox

    prefix = "relay_reply" if reply else "relay"
    try:
        sent = await send_copy(item, reply)
        store = outbox.get()
        async with store.lock:
            item[prefix + "_sent_at"] = outbox.now().isoformat() if sent else None
            item[prefix + "_error"] = None if sent else "MAIL_RELAY_FAILED"
            store.save(item)
    except Exception as exc:
        print(f"mailer task={item['task_id']} {prefix} error={type(exc).__name__}")
    finally:
        _pending.pop((item["task_id"], reply), None)
