from aiogram import Bot
from app.services.billing import expiry_warning_text
from app.services.users import get_or_create_user


def format_duration(seconds: int, force_hours: bool = False) -> str:
    total = max(0, int(seconds))
    if force_hours:
        hours = total // 3600
        minutes = (total % 3600) // 60
        secs = total % 60
        return f"{hours}:{minutes:02d}:{secs:02d}"
    minutes = total // 60
    secs = total % 60
    return f"{minutes}:{secs:02d}"


async def send_expiry_warning_if_needed(bot: Bot, user_id: int, chat_id: int) -> None:
    """Send expiry warning as a separate message if applicable."""
    user = get_or_create_user(user_id)
    plan_name = user.plan.value if hasattr(user.plan, "value") else str(user.plan)
    warning = expiry_warning_text(plan_name, user.paid_until)
    if warning:
        await bot.send_message(chat_id, warning.lstrip("\n"))
