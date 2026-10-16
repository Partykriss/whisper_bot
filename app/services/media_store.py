"""Atomic media-job lifecycle and balance accounting.

All lifecycle transactions lock the user before the task. Workers must present
their current lease token for every lifecycle mutation.
"""
import math
from datetime import datetime
from app.database import SessionLocal
from app.events.leases import LeaseLost
from app.models import Task, User
from app.services.billing import add_one_month, get_plan_limit_seconds, is_period_expired

ACTIVE = ('loading', 'processing')


class InsufficientBalance(Exception):
    def __init__(self, available, required):
        self.available, self.required = available, required
        super().__init__('Insufficient transcription balance')


def _refresh(user):
    plan = user.plan.value if hasattr(user.plan, 'value') else str(user.plan)
    if plan != 'admin' and not user.reserved_seconds and is_period_expired(user.paid_until):
        user.plan = 'trial'
        user.seconds = get_plan_limit_seconds('trial')
        user.paid_until = add_one_month(datetime.utcnow())
    return user.plan.value if hasattr(user.plan, 'value') else str(user.plan)


def _allowance(user):
    plan = _refresh(user)
    available = max(0, int(user.seconds or 0) - user.reserved_seconds)
    # Let already-reserved work finish, but do not admit new work on an
    # expired period until renewal or the normal trial refresh is possible.
    if plan != 'admin' and is_period_expired(user.paid_until):
        available = 0
    return plan, available


def _locked(db, user_id, task_id, lease_token=None):
    user = db.query(User).filter_by(telegram_id=user_id).with_for_update().one()
    task = db.query(Task).filter_by(id=task_id, user_id=user_id).with_for_update().one()
    if not lease_token or task.lease_token != lease_token or task.lease_until <= datetime.utcnow():
        raise LeaseLost('Task lease is no longer owned')
    return user, task


def claim_job(user_id, key, source, chat_id=None):
    with SessionLocal() as db, db.begin():
        db.query(User).filter_by(telegram_id=user_id).with_for_update().one()
        task = db.query(Task).filter_by(user_id=user_id, file_unique_id=key).with_for_update().first()
        if task and task.status in (*ACTIVE, 'queued', 'completed'):
            if task.status == 'completed' and chat_id is not None:
                from app.models import Event
                db.add(Event(kind='cached_result', payload={'task_id': task.id, 'chat_id': chat_id}))
            elif chat_id is not None:
                from app.events.store import enqueue_message
                enqueue_message(db, chat_id, 'This recording is already queued or processing.')
            return task.id, task.status, task.result_text
        if task is None:
            task = Task(user_id=user_id, file_unique_id=key)
            db.add(task)
        task.file_id = source.file_id
        task.source_url = source.url
        task.duration_seconds = source.duration
        task.chat_id = chat_id
        task.source_size = source.size
        task.generation = (task.generation or 0) + 1
        task.attempts = 0
        task.lease_token = None
        task.lease_until = None
        task.available_at = datetime.utcnow()
        task.status_message_id = None
        task.status = 'queued'
        task.result_text = None
        task.progress = 0
        db.flush()
        return task.id, 'claimed', None


def check_balance(user_id, duration):
    required = math.ceil(duration)
    with SessionLocal() as db, db.begin():
        user = db.query(User).filter_by(telegram_id=user_id).with_for_update().one()
        plan, available = _allowance(user)
        insufficient = plan != 'admin' and available < required
    if insufficient:
        raise InsufficientBalance(available, required)


def reserve(user_id, task_id, duration, lease_token=None):
    required = math.ceil(duration)
    if required <= 0 or not math.isfinite(duration):
        raise ValueError('A positive finite duration is required')
    with SessionLocal() as db, db.begin():
        user, task = _locked(db, user_id, task_id, lease_token)
        if task.status != 'loading':
            raise ValueError('Task must be claimed before reserving')
        plan, available = _allowance(user)
        insufficient = plan != 'admin' and available < required
        if not insufficient:
            task.duration_seconds = duration
            task.reserved_seconds = required if plan != 'admin' else 0
            user.reserved_seconds += task.reserved_seconds
            task.status = 'processing'
    if insufficient:
        raise InsufficientBalance(available, required)


def wait_for_payment(user_id, task_id, duration, lease_token=None):
    with SessionLocal() as db, db.begin():
        user, task = _locked(db, user_id, task_id, lease_token)
        if task.status == 'loading':
            # Payment may commit between the failed balance check and this lock.
            plan, available = _allowance(user)
            funded = duration is not None and (plan == 'admin' or available >= math.ceil(duration))
            task.status = 'queued' if funded else 'pending_payment'
            task.duration_seconds = duration
            task.lease_token = None
            task.lease_until = None
            task.available_at = datetime.utcnow()


def save_progress(user_id, task_id, percent, lease_token=None):
    with SessionLocal() as db, db.begin():
        _, task = _locked(db, user_id, task_id, lease_token)
        if task.status == 'processing':
            task.progress = percent


def finish(user_id, task_id, text, lease_token=None):
    with SessionLocal() as db, db.begin():
        user, task = _locked(db, user_id, task_id, lease_token)
        if task.status == 'completed':
            return
        if task.status != 'processing':
            raise ValueError('Only processing tasks may complete')
        user.seconds = int(user.seconds or 0) - task.reserved_seconds
        user.reserved_seconds -= task.reserved_seconds
        task.reserved_seconds = 0
        task.status = 'completed'
        task.result_text = text
        task.progress = 100


def fail(user_id, task_id, reason, lease_token=None):
    with SessionLocal() as db, db.begin():
        user, task = _locked(db, user_id, task_id, lease_token)
        if task.status not in ACTIVE:
            return
        user.reserved_seconds -= task.reserved_seconds
        task.reserved_seconds = 0
        task.status = 'failed'
        task.result_text = reason
