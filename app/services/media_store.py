"""Atomic media-job lifecycle and balance accounting.

All lifecycle transactions lock the user before the task. Caller must not run
multiple bot instances: startup recovery assumes one pipeline process.
"""
import math
from datetime import datetime
from app.database import SessionLocal
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


def _locked(db, user_id, task_id):
    user = db.query(User).filter_by(telegram_id=user_id).with_for_update().one()
    task = db.query(Task).filter_by(id=task_id, user_id=user_id).with_for_update().one()
    return user, task


def claim_job(user_id, key, source):
    with SessionLocal() as db, db.begin():
        db.query(User).filter_by(telegram_id=user_id).with_for_update().one()
        task = db.query(Task).filter_by(user_id=user_id, file_unique_id=key).with_for_update().first()
        if task and task.status in (*ACTIVE, 'completed'):
            return task.id, task.status, task.result_text
        if task is None:
            task = Task(user_id=user_id, file_unique_id=key)
            db.add(task)
        task.file_id = source.file_id
        task.source_url = source.url
        task.duration_seconds = source.duration
        task.status = 'loading'
        task.result_text = None
        task.progress = 0
        db.flush()
        return task.id, 'claimed', None


def claim_pending(user_id, task_id):
    with SessionLocal() as db, db.begin():
        user, task = _locked(db, user_id, task_id)
        if task.status != 'pending_payment':
            return None
        task.status = 'loading'
        task.progress = 0
        from app.media.sources import Source
        return Source(file_id=task.file_id, url=task.source_url,
                      duration=task.duration_seconds), task.chat_id


def set_chat(user_id, task_id, chat_id):
    with SessionLocal() as db, db.begin():
        _, task = _locked(db, user_id, task_id)
        task.chat_id = chat_id


def check_balance(user_id, duration):
    required = math.ceil(duration)
    with SessionLocal() as db, db.begin():
        user = db.query(User).filter_by(telegram_id=user_id).with_for_update().one()
        plan, available = _allowance(user)
        insufficient = plan != 'admin' and available < required
    if insufficient:
        raise InsufficientBalance(available, required)


def reserve(user_id, task_id, duration):
    required = math.ceil(duration)
    if required <= 0 or not math.isfinite(duration):
        raise ValueError('A positive finite duration is required')
    with SessionLocal() as db, db.begin():
        user, task = _locked(db, user_id, task_id)
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


def wait_for_payment(user_id, task_id, duration):
    with SessionLocal() as db, db.begin():
        _, task = _locked(db, user_id, task_id)
        if task.status == 'loading':
            task.status = 'pending_payment'
            task.duration_seconds = duration


def save_progress(user_id, task_id, percent):
    with SessionLocal() as db, db.begin():
        _, task = _locked(db, user_id, task_id)
        if task.status == 'processing':
            task.progress = percent


def finish(user_id, task_id, text):
    with SessionLocal() as db, db.begin():
        user, task = _locked(db, user_id, task_id)
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


def fail(user_id, task_id, reason):
    with SessionLocal() as db, db.begin():
        user, task = _locked(db, user_id, task_id)
        if task.status not in ACTIVE:
            return
        user.reserved_seconds -= task.reserved_seconds
        task.reserved_seconds = 0
        task.status = 'failed'
        task.result_text = reason


def recover_interrupted():
    with SessionLocal() as db:
        jobs = [(t.user_id, t.id) for t in db.query(Task).filter(Task.status.in_(ACTIVE)).all()]
    for user_id, task_id in jobs:
        fail(user_id, task_id, 'Processing interrupted by restart; send the source again to retry.')
