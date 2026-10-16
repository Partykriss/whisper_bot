"""Atomic payment acceptance, balance credit and pending-task requeue."""
from datetime import datetime
from app.database import SessionLocal
from app.models import Task, Payment, User
from app.services.billing import add_one_month, get_plan_limit_seconds
from app.events.store import enqueue_message


def record_payment(telegram_id, payment, chat_id):
    with SessionLocal() as db, db.begin():
        user = db.query(User).filter_by(telegram_id=telegram_id).with_for_update().one()
        if db.query(Payment.id).filter_by(telegram_charge_id=payment.telegram_payment_charge_id).first():
            return False
        plan = {'plan_plus': 'plus', 'plan_pro': 'pro'}.get(payment.invoice_payload, 'trial')
        db.add(Payment(user_id=telegram_id, plan=plan, amount=payment.total_amount,
                       currency=payment.currency, payload=payment.invoice_payload,
                       telegram_charge_id=payment.telegram_payment_charge_id,
                       provider_charge_id=payment.provider_payment_charge_id))
        if plan in ('plus', 'pro'):
            user.seconds = int(user.seconds or 0) + get_plan_limit_seconds(plan)
            user.plan = plan
            user.paid_until = add_one_month(datetime.utcnow())
            for task in db.query(Task).filter_by(user_id=telegram_id, status='pending_payment').order_by(Task.id).with_for_update():
                task.status = 'queued'
                task.progress = 0
                task.attempts = 0
                task.lease_token = None
                task.lease_until = None
                task.available_at = datetime.utcnow()
        enqueue_message(db, chat_id, 'Payment successful. Your balance and plan have been updated.'
                        if plan != 'trial' else 'Payment successful.')
        return True
