import os
from pathlib import Path

from dotenv import load_dotenv

CONTRACTS = Path(os.getenv("CONTRACTS_DIR", str(Path(__file__).resolve().parents[2] / "../../contracts"))).resolve()
load_dotenv(CONTRACTS.parent / ".env")
SERVICE = "chaos"
REDIS_URL = os.getenv("REDIS_URL", "")
DATABASE_URL = os.getenv("DATABASE_URL", "")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-6-luna")
