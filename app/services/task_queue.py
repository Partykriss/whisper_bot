"""Persistent task claims, leases and recovery; lock user before task."""
from datetime import datetime, timedelta
from uuid import uuid4
from sqlalchemy import or_
from app.database import SessionLocal
from app.models import Task, User
from app.media.sources import Source
from app.events.config import SETTINGS
from app.services.media_store import ACTIVE
from app.events.leases import LeaseLost


def claim(settings=SETTINGS):
    now = datetime.utcnow()
    with SessionLocal() as db, db.begin():
        candidates = db.query(Task.id, Task.user_id).filter(
            Task.status == 'queued', Task.available_at <= now).order_by(Task.id).all()
        for task_id, user_id in candidates:
            user = db.query(User).filter_by(telegram_id=user_id).with_for_update(skip_locked=True).first()
            if user is None:
                continue
            task = db.query(Task).filter_by(id=task_id, status='queued').with_for_update().first()
            if task is None:
                continue
            task.status = 'loading'
            task.attempts += 1
            task.lease_token = uuid4().hex
            task.lease_until = now + timedelta(seconds=settings.lease_seconds)
            return (user_id, task.chat_id, task.id,
                    Source(file_id=task.file_id, url=task.source_url,
                           duration=task.duration_seconds, size=task.source_size,
                           identity=task.source_identity), task.lease_token)
    return None


def renew(task_id, token, settings=SETTINGS):
    now = datetime.utcnow()
    with SessionLocal() as db, db.begin():
        count = db.query(Task).filter(Task.id == task_id, Task.lease_token == token,
                                     Task.status.in_(ACTIVE), Task.lease_until > now).update(
            {Task.lease_until: now + timedelta(seconds=settings.lease_seconds)})
        if not count:
            raise LeaseLost('Task lease expired or completed')


def recover(settings=SETTINGS):
    now = datetime.utcnow()
    with SessionLocal() as db:
        candidates = db.query(Task.id, Task.user_id).filter(
            Task.status.in_(ACTIVE), or_(Task.lease_until <= now, Task.lease_until.is_(None))).all()
    for task_id, user_id in candidates:
        with SessionLocal() as db, db.begin():
            user = db.query(User).filter_by(telegram_id=user_id).with_for_update().one()
            task = db.query(Task).filter_by(id=task_id).with_for_update().one()
            if task.status not in ACTIVE or (task.lease_until and task.lease_until > now):
                continue
            user.reserved_seconds -= task.reserved_seconds
            task.reserved_seconds = 0
            task.lease_token = None
            task.lease_until = None
            task.progress = 0
            task.available_at = now + timedelta(seconds=settings.retry_seconds)
            task.status = 'queued' if task.attempts < settings.task_attempts else 'failed'
            if task.status == 'failed':
                task.result_text = 'Processing repeatedly interrupted. Send the source again to retry.'
