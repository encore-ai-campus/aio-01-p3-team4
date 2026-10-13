import asyncio
import re
import smtplib
from email.message import EmailMessage
from email.utils import getaddresses
from app.core import config

class Blocked(Exception):
    pass

def is_virtual(address: str) -> bool:
    return re.fullmatch(r"[A-Za-z0-9._%+-]+@([A-Za-z0-9-]+\.)+example", address, re.IGNORECASE) is not None

async def send(msg: EmailMessage):
    recipients = [address for _, address in getaddresses(
        [str(value) for name in ("To", "Cc", "Bcc") for value in msg.get_all(name, [])])]
    sender = str(msg.get("From", ""))
    for address in [sender, *recipients]:
        if not is_virtual(address):
            raise Blocked(address)
    if not recipients:
        raise Blocked("")

    def deliver():
        with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=2) as client:
            client.send_message(msg, to_addrs=recipients)
    await asyncio.wait_for(asyncio.to_thread(deliver), timeout=2)
