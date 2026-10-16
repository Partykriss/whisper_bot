"""Transactional event fanout and leased per-recipient delivery."""
from datetime import datetime, timedelta
from uuid import uuid4
from sqlalchemy import or_, and_, exists
from sqlalchemy.orm import aliased
from app.database import SessionLocal
from app.models import Event, Delivery, Task, User
from app.events.config import SETTINGS
from app.events.leases import LeaseLost


def enqueue_message(db, chat_id, text):
    db.add(Event(kind='message', payload={'chat_id': chat_id, 'text': text}))


def fanout_one():
    with SessionLocal() as db, db.begin():
        event = db.query(Event).filter(Event.processed_at.is_(None)).order_by(Event.id).with_for_update(skip_locked=True).first()
        if event is None:
            return False
        if event.kind in ('user_registered', 'payment_received'):
            recipients = [row[0] for row in db.query(User.telegram_id).filter(User.plan == 'admin')]
        else:
            recipients = [event.payload['chat_id']] if event.payload.get('chat_id') is not None else []
        for recipient in recipients:
            db.add(Delivery(event_id=event.id, chat_id=recipient))
        event.processed_at = datetime.utcnow()
        return True


def claim(settings=SETTINGS):
    now = datetime.utcnow()
    older = aliased(Delivery)
    with SessionLocal() as db, db.begin():
        # Serialize notifications per chat, including a failed attempt awaiting retry.
        row = db.query(Delivery).filter(
            or_(and_(Delivery.status == 'pending', Delivery.available_at <= now),
                and_(Delivery.status == 'sending', Delivery.lease_until <= now)),
            ~exists().where(and_(older.chat_id == Delivery.chat_id, older.id < Delivery.id,
                                older.status.in_(('pending', 'sending'))))
        ).order_by(Delivery.id).with_for_update(skip_locked=True).first()
        if row is None:
            return None
        if row.attempts >= settings.delivery_attempts:
            row.status = 'failed'
            row.last_error = 'Delivery attempts exhausted after interrupted send'
            return None
        row.attempts += 1
        row.status = 'sending'
        row.lease_token = uuid4().hex
        row.lease_until = now + timedelta(seconds=settings.lease_seconds)
        event = db.get(Event, row.event_id)
        return row.id, row.lease_token, row.chat_id, event.kind, event.payload


def renew(delivery_id, token, settings=SETTINGS):
    now = datetime.utcnow()
    with SessionLocal() as db, db.begin():
        count = db.query(Delivery).filter_by(id=delivery_id, lease_token=token, status='sending').filter(
            Delivery.lease_until > now).update({Delivery.lease_until: now + timedelta(seconds=settings.lease_seconds)})
        if not count:
            raise LeaseLost('Delivery lease lost')


def complete(delivery_id, token, message_id=None, status='sent'):
    with SessionLocal() as db, db.begin():
        count = db.query(Delivery).filter_by(id=delivery_id, lease_token=token, status='sending').filter(
            Delivery.lease_until > datetime.utcnow()).update(
                {Delivery.status: status, Delivery.message_id: message_id, Delivery.lease_until: None})
        if not count:
            raise LeaseLost('Delivery lease lost')


def retry(delivery_id, token, error_name, delay=None, permanent=False, settings=SETTINGS):
    with SessionLocal() as db, db.begin():
        row = db.query(Delivery).filter_by(id=delivery_id, lease_token=token, status='sending').with_for_update().first()
        if row is None or row.lease_until <= datetime.utcnow():
            raise LeaseLost('Delivery lease lost')
        row.status = 'failed' if permanent or row.attempts >= settings.delivery_attempts else 'pending'
        row.last_error = error_name
        row.available_at = datetime.utcnow() + timedelta(seconds=max(settings.retry_seconds, delay or 0))
        row.lease_until = None


def task_snapshot(task_id):
    with SessionLocal() as db:
        row = db.get(Task, task_id)
        if row is None:
            return None
        return {name: getattr(row, name) for name in
                ('status', 'generation', 'progress', 'result_text', 'status_message_id')}


def save_message(task_id, generation, message_id):
    with SessionLocal() as db, db.begin():
        db.query(Task).filter_by(id=task_id, generation=generation).update({Task.status_message_id: message_id})
