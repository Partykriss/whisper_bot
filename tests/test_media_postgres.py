"""Real concurrent accounting, isolated from production tables in a temporary schema."""
import os
import uuid
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from threading import Barrier
from unittest.mock import patch
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from app.database import Base
from app.models import User
from app.services import media_store as store, task_queue
from app.media.sources import Source

@unittest.skipUnless(os.getenv('TEST_POSTGRES_URL'), 'Set TEST_POSTGRES_URL for PostgreSQL concurrency checks')
class PostgreSQLReservations(unittest.TestCase):
    def test_simultaneous_jobs_cannot_overspend(self):
        schema = 'media_test_' + uuid.uuid4().hex
        admin = create_engine(os.environ['TEST_POSTGRES_URL'])
        with admin.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA {schema}'))
        engine = create_engine(os.environ['TEST_POSTGRES_URL'],
                               connect_args={'options': f'-csearch_path={schema}'})
        sessions = sessionmaker(bind=engine)
        try:
            Base.metadata.create_all(engine)
            with sessions.begin() as db:
                db.add(User(telegram_id=7, plan='plus', seconds=100,
                            paid_until=datetime.utcnow() + timedelta(days=1)))
            with patch.object(store, 'SessionLocal', sessions), patch.object(task_queue, 'SessionLocal', sessions):
                jobs = [store.claim_job(7, name, Source(file_id=name))[0] for name in ('one', 'two')]
                claims = {item[2]: item[4] for item in (task_queue.claim(), task_queue.claim())}
                barrier = Barrier(2)
                def reserve(task_id):
                    barrier.wait(timeout=10)
                    try:
                        store.reserve(7, task_id, 60, claims[task_id])
                        return True
                    except store.InsufficientBalance:
                        return False
                with ThreadPoolExecutor(max_workers=2) as executor:
                    results = list(executor.map(reserve, jobs))
                self.assertEqual(sorted(results), [False, True])
                with sessions() as db:
                    user = db.query(User).one()
                    self.assertEqual((user.seconds, user.reserved_seconds), (100, 60))
        finally:
            engine.dispose()
            with admin.begin() as connection:
                connection.execute(text(f'DROP SCHEMA {schema} CASCADE'))
            admin.dispose()
