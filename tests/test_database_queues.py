"""PostgreSQL integration: triggers, leases, retries and result delivery."""
import asyncio
import importlib.util
import os
import select
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from app.database import Base
from app.models import User, Task, Event, Delivery, Payment
from app.events import store as events, delivery, listener
from app.events.config import QueueSettings
from app.media.config import MediaSettings
from app.media.sources import Source
from app.services import media_store, task_queue, tasks


@unittest.skipUnless(os.getenv('TEST_POSTGRES_URL'), 'Set TEST_POSTGRES_URL for database queue integration')
class DatabaseQueues(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.admin = create_engine(os.environ['TEST_POSTGRES_URL'])

    @classmethod
    def tearDownClass(cls):
        cls.admin.dispose()

    def setUp(self):
        self.schema = 'queue_test_' + uuid.uuid4().hex
        with self.admin.begin() as conn:
            conn.execute(text(f'CREATE SCHEMA {self.schema}'))
        self.engine = create_engine(os.environ['TEST_POSTGRES_URL'],
                                   connect_args={'options': f'-csearch_path={self.schema}'})
        self.sessions = sessionmaker(bind=self.engine)
        # Start from model tables, then install the exact production trigger DDL.
        Base.metadata.create_all(self.engine)
        import app
        path = Path(app.__file__).resolve().parents[1] / 'migrations/versions/b24e7a90c123_database_work_queues.py'
        spec = importlib.util.spec_from_file_location('queue_migration', path)
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        statements = []
        fake = SimpleNamespace(add_column=lambda *a, **k: None, create_index=lambda *a, **k: None,
                               create_table=lambda *a, **k: None, execute=statements.append)
        with patch.object(migration, 'op', fake):
            migration.upgrade()
        with self.engine.begin() as conn:
            for statement in statements:
                conn.execute(text(statement))
        self.stack = ExitStack()
        for module in (events, media_store, task_queue, tasks):
            self.stack.enter_context(patch.object(module, 'SessionLocal', self.sessions))
        with self.sessions.begin() as db:
            db.add_all([User(telegram_id=7, plan='plus', seconds=100,
                             paid_until=datetime.utcnow() + timedelta(days=1)),
                        User(telegram_id=99, plan='admin', seconds=0)])
        with self.sessions.begin() as db:
            db.query(Event).delete()
        self.bot = SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(message_id=123)),
                                   send_document=AsyncMock(return_value=SimpleNamespace(message_id=124)),
                                   edit_message_text=AsyncMock(), send_invoice=AsyncMock())

    def tearDown(self):
        self.stack.close()
        self.engine.dispose()
        with self.admin.begin() as conn:
            conn.execute(text(f'DROP SCHEMA {self.schema} CASCADE'))

    def enqueue(self, key='one'):
        return media_store.claim_job(7, key, Source(file_id='file'), 7)[0]

    def finish(self, text_result='transcript'):
        task_id = self.enqueue()
        item = task_queue.claim()
        media_store.reserve(7, task_id, 30, item[4])
        media_store.finish(7, task_id, text_result, item[4])
        return task_id, item[4]

    def drain(self):
        while events.fanout_one():
            pass
        while item := events.claim():
            asyncio.run(delivery.send(self.bot, item, MediaSettings()))

    def test_rollback_has_neither_event_nor_notify_and_commit_has_both(self):
        raw = self.engine.raw_connection()
        conn = raw.driver_connection
        conn.autocommit = True
        try:
            with conn.cursor() as cursor:
                cursor.execute('LISTEN whisper_events')
            with self.sessions() as db:
                db.add(User(telegram_id=8, plan='trial'))
                db.flush()
                db.rollback()
            self.assertFalse(select.select([conn], [], [], 0.1)[0])
            with self.sessions() as db:
                self.assertEqual(db.query(Event).count(), 0)
            with self.sessions.begin() as db:
                db.add(User(telegram_id=8, plan='trial'))
            self.assertTrue(select.select([conn], [], [], 2)[0])
            conn.poll()
            self.assertEqual(len(conn.notifies), 1)
            self.drain()
            self.bot.send_message.assert_awaited_once_with(99, 'New subscriber: user 8.')
        finally:
            raw.close()

    def test_claim_is_exclusive_and_stale_owner_cannot_finish(self):
        task_id = self.enqueue()
        with ThreadPoolExecutor(2) as executor:
            claims = list(executor.map(lambda _: task_queue.claim(), range(2)))
        item = next(value for value in claims if value)
        self.assertEqual(sum(value is not None for value in claims), 1)
        with self.assertRaises(media_store.LeaseLost):
            media_store.reserve(7, task_id, 30)
        media_store.reserve(7, task_id, 30, item[4])
        with self.sessions.begin() as db:
            db.get(Task, task_id).lease_until = datetime.utcnow() - timedelta(seconds=1)
        task_queue.recover(QueueSettings(retry_seconds=1))
        with self.sessions.begin() as db:
            db.get(Task, task_id).available_at = datetime.utcnow() - timedelta(seconds=1)
        next_item = task_queue.claim()
        with self.assertRaises(media_store.LeaseLost):
            media_store.finish(7, task_id, 'stale', item[4])
        media_store.reserve(7, task_id, 30, next_item[4])
        media_store.finish(7, task_id, 'fresh', next_item[4])
        media_store.finish(7, task_id, 'fresh', next_item[4])
        with self.sessions() as db:
            user = db.query(User).filter_by(telegram_id=7).one()
            self.assertEqual((user.seconds, user.reserved_seconds), (70, 0))
            self.assertEqual(db.get(Task, task_id).result_text, 'fresh')

    def test_result_survives_delivery_failure_and_cached_replay_uses_queue(self):
        task_id, _ = self.finish()
        self.bot.send_message.side_effect = RuntimeError('network unavailable')
        self.drain()
        with self.sessions.begin() as db:
            row = db.query(Delivery).filter_by(status='pending').one()
            row.available_at = datetime.utcnow() - timedelta(seconds=1)
            self.assertEqual(db.get(Task, task_id).result_text, 'transcript')
        self.bot.send_message.side_effect = None
        self.drain()
        self.bot.send_message.assert_awaited_with(7, 'transcript', parse_mode=None)
        self.bot.send_message.reset_mock()
        media_store.claim_job(7, 'one', Source(file_id='file'), 77)
        self.bot.send_message.assert_not_awaited()
        self.drain()
        self.bot.send_message.assert_awaited_once_with(77, 'transcript', parse_mode=None)
        with self.sessions() as db:
            self.assertEqual(db.query(User).filter_by(telegram_id=7).one().seconds, 70)

    def test_long_transcript_is_delivered_as_document_from_event(self):
        self.finish('word ' * 2000)
        self.drain()
        self.bot.send_document.assert_awaited_once()
        self.assertEqual(self.bot.send_document.await_args.args[1].data, ('word ' * 2000).encode())

    def test_payment_is_atomic_idempotent_and_requeues_pending_task(self):
        task_id = self.enqueue()
        item = task_queue.claim()
        media_store.wait_for_payment(7, task_id, 300, item[4])
        payment = SimpleNamespace(total_amount=100, currency='XTR', invoice_payload='plan_plus',
                                  telegram_payment_charge_id='charge', provider_payment_charge_id='provider')
        with patch.object(tasks, 'get_plan_limit_seconds', return_value=400):
            self.assertTrue(tasks.record_payment(7, payment, 7))
            self.assertFalse(tasks.record_payment(7, payment, 7))
        with self.sessions() as db:
            self.assertEqual(db.query(Payment).count(), 1)
            self.assertEqual(db.query(User).filter_by(telegram_id=7).one().seconds, 500)
            self.assertEqual(db.get(Task, task_id).status, 'queued')
            self.assertEqual(db.query(Event).filter_by(kind='payment_received').count(), 1)
        self.drain()
        self.bot.send_message.assert_any_await(99, 'New payment: user 7, plan plus, amount 100 XTR.')

    def test_payment_failure_rolls_back_credit_and_event(self):
        payment = SimpleNamespace(total_amount=100, currency='XTR', invoice_payload='plan_plus',
                                  telegram_payment_charge_id='charge', provider_payment_charge_id='provider')
        with patch.object(tasks, 'enqueue_message', side_effect=RuntimeError('fail')):
            with self.assertRaises(RuntimeError):
                tasks.record_payment(7, payment, 7)
        with self.sessions() as db:
            self.assertEqual(db.query(Payment).count(), 0)
            self.assertEqual(db.query(Event).count(), 0)
            self.assertEqual(db.query(User).filter_by(telegram_id=7).one().seconds, 100)

    def test_delivery_claim_is_exclusive_and_expired_send_can_retry(self):
        with self.sessions.begin() as db:
            events.enqueue_message(db, 7, 'hello')
        events.fanout_one()
        with ThreadPoolExecutor(2) as executor:
            claims = list(executor.map(lambda _: events.claim(), range(2)))
        self.assertEqual(sum(value is not None for value in claims), 1)
        first = next(value for value in claims if value)
        with self.sessions.begin() as db:
            db.get(Delivery, first[0]).lease_until = datetime.utcnow() - timedelta(seconds=1)
        second = events.claim()
        with self.assertRaises(media_store.LeaseLost):
            events.complete(first[0], first[1])
        asyncio.run(delivery.send(self.bot, second, MediaSettings()))
        with self.sessions() as db:
            self.assertEqual(db.get(Delivery, first[0]).status, 'sent')

    def test_listener_reconnects_and_wakes_after_commit(self):
        async def scenario():
            wake = asyncio.Event()
            # create_connect_args does not include engine connect_args.
            url = self.engine.url.update_query_dict({'options': f'-csearch_path={self.schema}',
                                                     'application_name': self.schema})
            listen_engine = create_engine(url)
            with patch.object(listener, 'engine', listen_engine):
                worker = asyncio.create_task(listener.listen(wake, QueueSettings(scan_seconds=1, reconnect_seconds=1)))
                try:
                    await asyncio.wait_for(wake.wait(), 5)
                    wake.clear()
                    with self.engine.begin() as conn:
                        conn.execute(text("SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                                          "WHERE pid <> pg_backend_pid() AND application_name = :name"),
                                     {'name': self.schema})
                    await asyncio.wait_for(wake.wait(), 5)
                    wake.clear()
                    with self.sessions.begin() as db:
                        events.enqueue_message(db, 7, 'after reconnect')
                    await asyncio.wait_for(wake.wait(), 5)
                finally:
                    worker.cancel()
                    await asyncio.gather(worker, return_exceptions=True)
                    listen_engine.dispose()
        asyncio.run(scenario())

    def test_payment_racing_with_insufficient_balance_does_not_strand_task(self):
        task_id = self.enqueue()
        item = task_queue.claim()
        with self.assertRaises(media_store.InsufficientBalance):
            media_store.check_balance(7, 300)
        payment = SimpleNamespace(total_amount=100, currency='XTR', invoice_payload='plan_plus',
                                  telegram_payment_charge_id='race', provider_payment_charge_id='provider')
        with patch.object(tasks, 'get_plan_limit_seconds', return_value=400):
            tasks.record_payment(7, payment, 7)
        media_store.wait_for_payment(7, task_id, 300, item[4])
        self.assertIsNotNone(task_queue.claim())

    def test_old_progress_is_coalesced_and_failed_recipient_does_not_block_others(self):
        with self.sessions.begin() as db:
            db.add(User(telegram_id=98, plan='admin'))
        self.enqueue()
        item = task_queue.claim()
        media_store.reserve(7, item[2], 20, item[4])
        media_store.save_progress(7, item[2], 10, item[4])
        media_store.save_progress(7, item[2], 80, item[4])
        async def send(chat_id, text, **kwargs):
            if chat_id == 98:
                raise RuntimeError('unavailable')
            return SimpleNamespace(message_id=123)
        self.bot.send_message.side_effect = send
        self.drain()
        user_messages = [call.args[1] for call in self.bot.send_message.await_args_list if call.args[0] == 7]
        self.assertEqual(user_messages, ['Transcribing audio: 80%'])
        self.bot.send_message.assert_any_await(99, 'New subscriber: user 98.')
        with self.sessions() as db:
            self.assertEqual(db.query(Delivery).filter_by(chat_id=98, status='pending').count(), 1)

    def test_exhausted_crash_retries_release_balance_and_keep_failed_delivery(self):
        task_id = self.enqueue()
        item = task_queue.claim()
        media_store.reserve(7, task_id, 30, item[4])
        with self.sessions.begin() as db:
            db.get(Task, task_id).lease_until = datetime.utcnow() - timedelta(seconds=1)
        task_queue.recover(QueueSettings(task_attempts=1))
        with self.sessions() as db:
            self.assertEqual(db.get(Task, task_id).status, 'failed')
            self.assertEqual(db.query(User).filter_by(telegram_id=7).one().reserved_seconds, 0)
        while events.fanout_one():
            pass
        # Skip old task states, then simulate a crash during terminal delivery.
        while item := events.claim(QueueSettings(delivery_attempts=1)):
            if item[4].get('status') == 'failed':
                with self.sessions.begin() as db:
                    db.get(Delivery, item[0]).lease_until = datetime.utcnow() - timedelta(seconds=1)
                self.assertIsNone(events.claim(QueueSettings(delivery_attempts=1)))
                with self.sessions() as db:
                    self.assertEqual(db.get(Delivery, item[0]).status, 'failed')
                break
            asyncio.run(delivery.send(self.bot, item, MediaSettings()))
        else:
            self.fail('Missing failure notification')

    def test_runtime_processes_persisted_job_and_delivers_through_outbox(self):
        from app.events.runtime import Runtime
        from app.services.media_jobs import MediaJobs
        import tempfile
        async def scenario(directory):
            delivered = asyncio.Event()
            async def send(chat_id, text, **kwargs):
                if text == 'runtime transcript':
                    delivered.set()
                return SimpleNamespace(message_id=123)
            self.bot.send_message.side_effect = send
            jobs = MediaJobs(self.bot, object(), MediaSettings(downloads_dir=directory, concurrent_jobs=1))
            with patch('app.services.media_jobs.download_telegram', AsyncMock(return_value=Path('media'))), \
                 patch('app.services.media_jobs.probe_duration', AsyncMock(return_value=30)), \
                 patch('app.services.media_jobs.prepare_chunks', AsyncMock(return_value=[Path('chunk')])), \
                 patch('app.services.media_jobs.transcribe_chunks', AsyncMock(return_value='runtime transcript')), \
                 patch.object(listener, 'engine', self.engine):
                await jobs.submit(7, 7, Source(file_id='file'), 'runtime')
                self.bot.send_message.assert_not_awaited()
                async with Runtime(self.bot, jobs, QueueSettings(scan_seconds=1, delivery_workers=1)):
                    await asyncio.wait_for(delivered.wait(), 5)
            with self.sessions() as db:
                self.assertEqual(db.query(Task).one().status, 'completed')
                self.assertEqual(db.query(User).filter_by(telegram_id=7).one().seconds, 70)
                self.assertGreater(db.query(Delivery).filter_by(status='sent').count(), 0)
        with tempfile.TemporaryDirectory() as directory:
            asyncio.run(scenario(directory))
