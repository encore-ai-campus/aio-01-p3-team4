import os
from pathlib import Path
from dotenv import load_dotenv

PROJECT_ROOT = (Path(__file__).resolve().parents[2] / "../..").resolve()
CONTRACTS = Path(os.getenv("CONTRACTS_DIR", str(PROJECT_ROOT / "contracts"))).resolve()
load_dotenv(CONTRACTS.parent / ".env")
SERVICE = "locator"
REDIS_URL = os.getenv("REDIS_URL", "")
DATABASE_URL = os.getenv("DATABASE_URL", "")
SEEDS_DIR = Path(os.getenv("SEEDS_DIR", "/seeds" if Path("/seeds").is_dir() else str(PROJECT_ROOT / "database" / "seeds")))
# verifier checks telephone syntax and its official source page.
INCLUDE_PHONE = os.getenv("LOCATOR_INCLUDE_PHONE", "true").lower() == "true"
