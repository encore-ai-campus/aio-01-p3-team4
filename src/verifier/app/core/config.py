import os
from pathlib import Path
from dotenv import load_dotenv

CONTRACTS = Path(os.getenv("CONTRACTS_DIR", str(Path(__file__).resolve().parents[2] / "../../contracts"))).resolve()
load_dotenv(CONTRACTS.parent / ".env")
SERVICE = "verifier"
REDIS_URL = os.getenv("REDIS_URL", "")
DATABASE_URL = os.getenv("DATABASE_URL", "")
