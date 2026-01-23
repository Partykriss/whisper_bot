import os
import enum
from dotenv import load_dotenv

load_dotenv()

# --- Enums (Moving here to avoid circular imports) ---
class PlanType(str, enum.Enum):
    trial = "trial"
    plus = "plus"
    pro = "pro"
    admin = "admin"

# --- Database ---
DATABASE_URL = os.getenv("DATABASE_URL")

# --- API Keys ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# --- Limits Logic ---
TRIAL_LIMIT_SEC = int(os.getenv("LIMIT_TRIAL_MIN", 3)) * 60
PLUS_LIMIT_SEC = int(os.getenv("LIMIT_PLUS_MIN", 100)) * 60
PRO_LIMIT_SEC = int(os.getenv("LIMIT_PRO_MIN", 2000)) * 60

PLAN_LIMITS = {
    PlanType.trial: TRIAL_LIMIT_SEC,
    PlanType.plus: PLUS_LIMIT_SEC,
    PlanType.pro: PRO_LIMIT_SEC
}

DOWNLOADS_DIR = "downloads"

# --- Payments (Telegram Stars) ---
PRICE_PLUS_STARS = int(os.getenv("PRICE_PLUS_STARS", 0))
PRICE_PRO_STARS = int(os.getenv("PRICE_PRO_STARS", 0))
