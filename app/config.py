"""Runtime settings, read from environment variables."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv("DESKVOICE_DATA_DIR", ROOT / "data"))
DB_PATH = Path(os.getenv("DESKVOICE_DB", DATA_DIR / "deskvoice.db"))

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
USE_VERTEX = os.getenv("USE_VERTEX", "").lower() == "true"
GOOGLE_CLOUD_PROJECT = os.getenv("GOOGLE_CLOUD_PROJECT")
GOOGLE_CLOUD_LOCATION = os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

# "demo" forces the offline rule-based brain even when a key is present.
FORCE_DEMO = os.getenv("DESKVOICE_MODE", "").lower() == "demo"


def gemini_enabled() -> bool:
    return not FORCE_DEMO and bool(GEMINI_API_KEY or USE_VERTEX)
