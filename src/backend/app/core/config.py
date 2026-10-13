import os
from pathlib import Path
from dotenv import load_dotenv

CONTRACTS = Path(os.getenv("CONTRACTS_DIR", str(Path(__file__).resolve().parents[2] / "../../contracts"))).resolve()
load_dotenv(CONTRACTS.parent / ".env")
SERVICE = "backend"
METRICS_SINCE = os.getenv("METRICS_SINCE", "2026-10-12T04:26:00Z")
REDIS_URL = os.getenv("REDIS_URL", "")
DATABASE_URL = os.getenv("DATABASE_URL", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-6-luna")
OPENAI_FALLBACK_MODEL = os.getenv("OPENAI_FALLBACK_MODEL", "gpt-5.4-mini")
