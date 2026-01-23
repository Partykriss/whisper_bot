import calendar
from datetime import datetime
from app.config import PLAN_LIMITS


def add_one_month(dt: datetime) -> datetime:
    """Return dt + 1 month, clamped to the last day of the target month."""
    year = dt.year + (1 if dt.month == 12 else 0)
    month = 1 if dt.month == 12 else dt.month + 1
    last_day = calendar.monthrange(year, month)[1]
    day = min(dt.day, last_day)
    return dt.replace(year=year, month=month, day=day)


def days_left(paid_until: datetime | None) -> int | None:
    if paid_until is None:
        return None
    delta = paid_until - datetime.utcnow()
    return delta.days


def is_period_expired(paid_until: datetime | None) -> bool:
    if paid_until is None:
        return False
    return datetime.utcnow() > paid_until


def get_plan_limit_seconds(plan) -> int:
    """Resolve plan limit even if enum classes differ."""
    plan_key = plan.value if hasattr(plan, "value") else str(plan)
    limits_by_value = {
        (k.value if hasattr(k, "value") else str(k)): v
        for k, v in PLAN_LIMITS.items()
    }
    return limits_by_value.get(plan_key, 0)


def expiry_warning_text(plan_name: str, paid_until: datetime | None) -> str:
    left = days_left(paid_until)
    if plan_name in {"plus", "pro"} and left is not None and 0 <= left <= 3:
        return "\n{days} day(s) left. We recommend topping up before expiry to carry over your balance.".format(
            days=left
        )
    return ""
