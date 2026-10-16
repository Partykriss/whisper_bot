import os
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.database import Base
from app.models import User, Task
from app.services import media_store as store, task_queue
from app.media.sources import Source

class ReservationTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite://')
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(bind=self.engine)
        self.mock = patch.object(store, 'SessionLocal', self.sessions)
        self.mock.start()
        self.queue_mock = patch.object(task_queue, "SessionLocal", self.sessions)
        self.queue_mock.start()
        with self.sessions.begin() as db:
            db.add(User(telegram_id=7, plan='plus', seconds=100,
                        paid_until=datetime.utcnow() + timedelta(days=1)))

    def tearDown(self):
        self.mock.stop()
        self.queue_mock.stop()
        self.engine.dispose()

    def job(self, key='one'):
        task_id = store.claim_job(7, key, Source(file_id='file'), 42)[0]
        task_queue.claim()
        return task_id

    def token(self, task_id):
        with self.sessions() as db:
            return db.get(Task, task_id).lease_token

    def snapshot(self):
        with self.sessions() as db:
            u = db.query(User).one()
            return u.seconds, u.reserved_seconds

    def test_insufficient_balance_cannot_start(self):
        job = self.job()
        with self.assertRaises(store.InsufficientBalance):
            store.reserve(7, job, 101, self.token(job))
        self.assertEqual(self.snapshot(), (100, 0))

    def test_reservation_blocks_another_job_and_consumes_once(self):
        job, other = self.job(), self.job('two')
        store.reserve(7, job, 60.1, self.token(job))
        self.assertEqual(self.snapshot(), (100, 61))
        with self.assertRaises(store.InsufficientBalance):
            store.reserve(7, other, 40, self.token(other))
        store.finish(7, job, 'text', self.token(job))
        store.finish(7, job, 'text', self.token(job))
        self.assertEqual(self.snapshot(), (39, 0))

    def test_failed_job_releases_reservation_and_can_retry(self):
        job = self.job()
        store.reserve(7, job, 60, self.token(job))
        store.fail(7, job, 'error', self.token(job))
        store.fail(7, job, 'error', self.token(job))
        self.assertEqual(self.snapshot(), (100, 0))
        self.assertEqual(self.job(), job)
        store.reserve(7, job, 60, self.token(job))
        store.finish(7, job, 'text', self.token(job))
        self.assertEqual(self.snapshot(), (40, 0))

    def test_delivery_failure_does_not_refund_completed_job(self):
        job = self.job()
        store.reserve(7, job, 60, self.token(job))
        store.finish(7, job, 'text', self.token(job))
        store.fail(7, job, 'delivery failed', self.token(job))
        self.assertEqual(self.snapshot(), (40, 0))
        self.assertEqual(store.claim_job(7, 'one', Source(file_id='file'))[1:], ('completed', 'text'))

    def test_admin_skips_balance_but_still_has_task_lifecycle(self):
        with self.sessions.begin() as db:
            db.query(User).one().plan = 'admin'
        job = self.job()
        store.reserve(7, job, 1000, self.token(job))
        store.finish(7, job, 'text', self.token(job))
        self.assertEqual(self.snapshot(), (100, 0))

    def test_restart_releases_reservations(self):
        job = self.job()
        store.reserve(7, job, 60, self.token(job))
        with self.sessions.begin() as db:
            db.get(Task, job).lease_until = datetime.utcnow() - timedelta(seconds=1)
        task_queue.recover()
        self.assertEqual(self.snapshot(), (100, 0))
        with self.sessions() as db:
            self.assertEqual(db.get(Task, job).status, 'queued')

    def test_active_duplicate_is_not_claimed(self):
        self.job()
        self.assertEqual(store.claim_job(7, 'one', Source(file_id='file'))[1], 'loading')

    def test_expired_period_refreshes_before_rejecting_large_task(self):
        with self.sessions.begin() as db:
            db.query(User).one().paid_until = datetime.utcnow() - timedelta(days=1)
        with self.assertRaises(store.InsufficientBalance):
            store.check_balance(7, 10000)
        with self.sessions() as db:
            user = db.query(User).one()
            self.assertEqual(user.plan.value, 'trial')
            self.assertGreater(user.paid_until, datetime.utcnow())

    def test_expired_period_cannot_admit_work_while_old_reservation_finishes(self):
        job, other = self.job(), self.job('two')
        store.reserve(7, job, 20, self.token(job))
        with self.sessions.begin() as db:
            db.query(User).one().paid_until = datetime.utcnow() - timedelta(days=1)
        with self.assertRaises(store.InsufficientBalance):
            store.reserve(7, other, 10, self.token(other))
        store.finish(7, job, 'text', self.token(job))
        self.assertEqual(self.snapshot(), (80, 0))
