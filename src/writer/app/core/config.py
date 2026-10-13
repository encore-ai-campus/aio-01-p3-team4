import os
from pathlib import Path
from dotenv import load_dotenv

CONTRACTS = Path(os.getenv("CONTRACTS_DIR", str(Path(__file__).resolve().parents[2] / "../../contracts"))).resolve()
load_dotenv(CONTRACTS.parent / ".env")
SERVICE = "writer"
REDIS_URL = os.getenv("REDIS_URL", "")
DATABASE_URL = os.getenv("DATABASE_URL", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-6-luna")
OPENAI_FALLBACK_MODEL = os.getenv("OPENAI_FALLBACK_MODEL", "gpt-5.4-mini")
# 총괄의 호출 제한 15초보다 짧게 유지한다.
LLM_TIMEOUT_SECONDS = min(10.0, max(0.1, float(os.getenv("LLM_TIMEOUT_SECONDS", "10"))))
LLM_BUDGET_SECONDS = min(13.0, max(0.1, float(os.getenv("LLM_BUDGET_SECONDS", "13"))))
