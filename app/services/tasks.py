from app.database import SessionLocal
from app.models import Task, Payment


def get_cached_result(file_unique_id: str) -> str | None:
    """Check if this file was already processed successfully."""
    with SessionLocal() as db:
        task = db.query(Task).filter(
            Task.file_unique_id == file_unique_id,
            Task.status == "completed"
        ).first()
        return task.result_text if task else None


def save_task_to_db(
    user_id: int,
    file_unique_id: str,
    status: str = "processing",
    file_id: str | None = None,
    chat_id: int | None = None,
    duration_seconds: float | None = None,
) -> int:
    """Create a new task record."""
    with SessionLocal() as db:
        new_task = Task(
            user_id=user_id,
            file_unique_id=file_unique_id,
            status=status,
            file_id=file_id,
            chat_id=chat_id,
            duration_seconds=duration_seconds,
        )
        db.add(new_task)
        db.commit()
        db.refresh(new_task)
        return new_task.id


def update_task_in_db(task_id: int, text: str, status: str, duration_seconds: float | None = None):
    """Update existing task with result or error status."""
    with SessionLocal() as db:
        task = db.query(Task).filter(Task.id == task_id).first()
        if task:
            task.result_text = text
            task.status = status
            if duration_seconds is not None:
                task.duration_seconds = float(duration_seconds)
            db.commit()


def set_task_status(task_id: int, status: str):
    with SessionLocal() as db:
        task = db.query(Task).filter(Task.id == task_id).first()
        if task:
            task.status = status
            db.commit()


def get_pending_task_for_user(telegram_id: int):
    """Get the oldest pending_payment task for a user."""
    with SessionLocal() as db:
        return (
            db.query(Task)
            .filter(Task.user_id == telegram_id, Task.status == "pending_payment")
            .order_by(Task.created_at.asc())
            .first()
        )


def get_pending_task_by_file_unique_id(file_unique_id: str):
    with SessionLocal() as db:
        return (
            db.query(Task)
            .filter(Task.file_unique_id == file_unique_id, Task.status == "pending_payment")
            .first()
        )


def record_payment(telegram_id: int, plan_name: str, payment):
    """Persist a successful payment."""
    with SessionLocal() as db:
        new_payment = Payment(
            user_id=telegram_id,
            plan=plan_name,
            amount=payment.total_amount,
            currency=payment.currency,
            payload=payment.invoice_payload,
            telegram_charge_id=payment.telegram_payment_charge_id,
            provider_charge_id=payment.provider_payment_charge_id,
        )
        db.add(new_payment)
        db.commit()
