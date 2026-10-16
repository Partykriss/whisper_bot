"""Telegram adapter for persisted notifications; no media execution here."""
import logging
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from app.events import store
from app.payments import send_plan_invoices
from app.ui.results import deliver_result
from app.events.leases import LeaseLost


async def send(bot, item, settings):
    delivery_id, token, chat_id, kind, payload = item
    try:
        message = None
        if kind == 'user_registered':
            message = await bot.send_message(chat_id, f"New subscriber: user {payload['telegram_id']}.")
        elif kind == 'payment_received':
            message = await bot.send_message(chat_id,
                f"New payment: user {payload['user_id']}, plan {payload['plan']}, "
                f"amount {payload['amount']} {payload['currency']}.")
        elif kind == 'message':
            message = await bot.send_message(chat_id, payload['text'])
        elif kind in ('task_changed', 'cached_result'):
            task = store.task_snapshot(payload['task_id'])
            if task is None:
                store.complete(delivery_id, token, status='skipped')
                return
            if kind == 'cached_result' or payload['status'] == 'completed':
                if task['status'] != 'completed':
                    store.complete(delivery_id, token, status='skipped')
                    return
                message = await deliver_result(bot, chat_id, task['result_text'], settings)
                if kind == 'task_changed' and task['status_message_id']:
                    try:
                        await bot.edit_message_text('Transcription complete.', chat_id=chat_id,
                                                    message_id=task['status_message_id'])
                    except TelegramAPIError:
                        # The transcript was delivered; cosmetic edits must not resend it.
                        logging.info('Could not update completed task status message')
            else:
                # Do not replay obsolete progress or old attempts after downtime.
                if (task['generation'] != payload['generation'] or task['status'] != payload['status']
                        or task['progress'] != payload['progress']):
                    store.complete(delivery_id, token, status='skipped')
                    return
                status = task['status']
                texts = {'queued': 'Media queued.', 'loading': 'Checking and downloading media...',
                         'processing': f"Transcribing audio: {task['progress']}%",
                         'pending_payment': 'Insufficient balance. Waiting for payment. No transcription was started.',
                         'failed': task['result_text']}
                if status not in texts:
                    raise ValueError('Unknown task status')
                if task['status_message_id']:
                    try:
                        await bot.edit_message_text(texts[status], chat_id=chat_id,
                                                    message_id=task['status_message_id'])
                    except TelegramBadRequest as error:
                        if 'message is not modified' not in error.message.lower():
                            message = await bot.send_message(chat_id, texts[status])
                else:
                    message = await bot.send_message(chat_id, texts[status])
                if message:
                    store.save_message(payload['task_id'], payload['generation'], message.message_id)
                if status == 'pending_payment':
                    await send_plan_invoices(bot, chat_id)
        else:
            raise ValueError('Unknown event kind')
        store.complete(delivery_id, token, getattr(message, 'message_id', None))
    except LeaseLost:
        raise
    except TelegramRetryAfter as error:
        store.retry(delivery_id, token, type(error).__name__, delay=error.retry_after)
    except (TelegramForbiddenError, TelegramBadRequest) as error:
        store.retry(delivery_id, token, type(error).__name__, permanent=True)
    except Exception as error:
        store.retry(delivery_id, token, type(error).__name__)
