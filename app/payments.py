from aiogram.types import LabeledPrice

from app.config import PLAN_LIMITS, PRICE_PLUS_STARS, PRICE_PRO_STARS
from app.models import PlanType


def _format_seconds_as_minutes(seconds: int) -> str:
    minutes = max(1, int(round(seconds / 60)))
    return f"{minutes} min"


def _plan_description(plan: PlanType) -> str:
    limit_sec = PLAN_LIMITS.get(plan, 0)
    return f"Limit: {limit_sec} sec ({_format_seconds_as_minutes(limit_sec)})"


async def send_plan_invoices(bot, chat_id: int) -> None:
    """Send two invoices: Plus and Pro plans (Telegram Stars)."""
    plus_price = LabeledPrice(label="Whisper Plus", amount=PRICE_PLUS_STARS)
    pro_price = LabeledPrice(label="Whisper Pro", amount=PRICE_PRO_STARS)

    await bot.send_invoice(
        chat_id=chat_id,
        title="Whisper Plus",
        description=_plan_description(PlanType.plus),
        payload="plan_plus",
        currency="XTR",
        prices=[plus_price],
        provider_token="",
    )
    await bot.send_invoice(
        chat_id=chat_id,
        title="Whisper Pro",
        description=_plan_description(PlanType.pro),
        payload="plan_pro",
        currency="XTR",
        prices=[pro_price],
        provider_token="",
    )
