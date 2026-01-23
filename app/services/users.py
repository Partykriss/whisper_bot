from datetime import datetime
from app.database import SessionLocal
from app.models import User
from app.services.billing import add_one_month, get_plan_limit_seconds


def is_paid_plan(plan_name: str) -> bool:
    return plan_name in {"plus", "pro"}


def get_or_create_user(telegram_id: int) -> User:
    """Fetch user by telegram_id or create a new one."""
    with SessionLocal() as db:
        user = db.query(User).filter(User.telegram_id == telegram_id).first()
        if user:
            if user.seconds is None:
                plan_name = user.plan.value if hasattr(user.plan, "value") else str(user.plan)
                if plan_name == "trial":
                    user.seconds = get_plan_limit_seconds(user.plan)
            plan_name = user.plan.value if hasattr(user.plan, "value") else str(user.plan)
            if plan_name == "trial":
                if user.paid_until is None:
                    user.seconds = get_plan_limit_seconds("trial")
                    user.paid_until = add_one_month(datetime.utcnow())
            db.commit()
            db.refresh(user)
            return user
        user = User(
            telegram_id=telegram_id,
            seconds=get_plan_limit_seconds("trial"),
            paid_until=add_one_month(datetime.utcnow()),
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        return user


def set_user_plan(telegram_id: int, plan_name: str):
    """Set user plan and reset usage/period."""
    with SessionLocal() as db:
        user = db.query(User).filter(User.telegram_id == telegram_id).first()
        if not user:
            return
        user.plan = plan_name
        user.seconds = 0
        user.paid_until = None
        db.commit()


def subtract_user_seconds(telegram_id: int, duration_seconds: float):
    """Subtract seconds from user's balance."""
    with SessionLocal() as db:
        user = db.query(User).filter(User.telegram_id == telegram_id).first()
        if not user:
            return
        current = user.seconds or 0
        user.seconds = int(current) - int(round(duration_seconds))
        db.commit()


def apply_payment_to_user(telegram_id: int, plan_name: str):
    """Apply payment: add plan seconds to balance and update period."""
    with SessionLocal() as db:
        user = db.query(User).filter(User.telegram_id == telegram_id).first()
        if not user:
            return
        limit_sec = get_plan_limit_seconds(plan_name)
        current_balance = int(user.seconds or 0)
        if limit_sec > 0:
            user.seconds = current_balance + int(limit_sec)
        user.plan = plan_name
        user.paid_until = add_one_month(datetime.utcnow())
        db.commit()


def get_admin_user_ids() -> list[int]:
    """Return telegram_ids of admin users."""
    with SessionLocal() as db:
        rows = db.query(User.telegram_id).filter(User.plan == "admin").all()
        return [row[0] for row in rows]
