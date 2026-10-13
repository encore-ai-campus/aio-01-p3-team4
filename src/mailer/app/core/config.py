import os
from pathlib import Path
from dotenv import load_dotenv

CONTRACTS = Path(os.getenv("CONTRACTS_DIR", str(Path(__file__).resolve().parents[2] / "../../contracts"))).resolve()
load_dotenv(CONTRACTS.parent / ".env")
SERVICE = "mailer"
REDIS_URL = os.getenv("REDIS_URL", "")
SMTP_HOST = os.getenv("SMTP_HOST", "mailpit")
SMTP_PORT = int(os.getenv("SMTP_PORT", "1025"))
MAIL_RELAY_HOST = os.getenv("MAIL_RELAY_HOST", "")
MAIL_RELAY_PORT = int(os.getenv("MAIL_RELAY_PORT") or "587")
MAIL_RELAY_USER = os.getenv("MAIL_RELAY_USER", "")
MAIL_RELAY_PASSWORD = os.getenv("MAIL_RELAY_PASSWORD", "")
MAIL_RELAY_TO = os.getenv("MAIL_RELAY_TO", "")
MAIL_RELAY_TIMEOUT_SEC = float(os.getenv("MAIL_RELAY_TIMEOUT_SEC", "8"))
MAIL_SENDER = os.getenv("MAIL_SENDER", "minwon-guide@minwon.example")
MAIL_CITIZEN = os.getenv("MAIL_CITIZEN", "citizen@minwon.example")
CONTACTS_CSV = Path(os.getenv("CONTACTS_CSV", str(CONTRACTS.parent / "database/seeds/mock_contacts.csv")))
OUTBOX_PATH = Path(os.getenv("OUTBOX_PATH", "/tmp/minwon_mailer_outbox.json"))
REPLY_DELAY_SEC = float(os.getenv("REPLY_DELAY_SEC", "3"))
OUTBOX_INTERVAL_SEC = float(os.getenv("OUTBOX_INTERVAL_SEC", "5"))

def relay_enabled():
    return all((MAIL_RELAY_HOST, MAIL_RELAY_USER, MAIL_RELAY_PASSWORD, MAIL_RELAY_TO))
