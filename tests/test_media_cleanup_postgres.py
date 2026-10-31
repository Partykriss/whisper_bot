"""Real PostgreSQL fencing, file lifecycle and concurrent cleanup contracts."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from datetime import datetime, timedelta
import os
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from app.database import Base
from app.models import MediaFile, MediaAttempt, MediaCleanupEpoch, Task, User
from app.cleanup import store
from app.cleanup.config import CleanupSettings
from app.cleanup.filesystem import directory_guard, fingerprint
from app.cleanup.filesystem import delete_media_file
from app.cleanup.lifecycle import MediaFiles
from app.cleanup.worker import CleanupWorker
from app.events.leases import LeaseLost
from app.media.config import MediaSettings
from app.media.sources import Source
from app.services import media_store
from app.services.media_jobs import MediaJobs


@unittest.skipUnless(os.getenv('TEST_POSTGRES_URL'), 'Set TEST_POSTGRES_URL for cleanup integration')
class MediaCleanupPostgres(unittest.TestCase):
    def setUp(self):
        self.schema = 'cleanup_test_' + uuid4().hex
        self.admin = create_engine(os.environ['TEST_POSTGRES_URL'])
        with self.admin.begin() as conn:
            conn.execute(text(f'CREATE SCHEMA {self.schema}'))
        self.engine = create_engine(os.environ['TEST_POSTGRES_URL'],
                                   connect_args={'options': f'-csearch_path={self.schema}'})
        self.sessions = sessionmaker(bind=self.engine)
        Base.metadata.create_all(self.engine)
        self.stack = ExitStack()
        self.stack.enter_context(patch.object(store, 'SessionLocal', self.sessions))
        self.stack.enter_context(patch.object(media_store, 'SessionLocal', self.sessions))
        self.directory = self.stack.enter_context(tempfile.TemporaryDirectory())
        self.root = Path(self.directory) / 'work'
        self.telegram = Path(self.directory) / 'telegram'
        self.root.mkdir()
        self.telegram.mkdir()
        self.settings = CleanupSettings(orphan_guard_seconds=1, retry_seconds=1, retry_max_seconds=2)
        self.media = MediaSettings(downloads_dir=str(self.root), telegram_local_root=str(self.telegram),
                                   telegram_api_base='http://telegram-api:8081')
        self.workspace = CleanupWorker('workspace', self.media, self.settings)
        self.cache = CleanupWorker('telegram', self.media, self.settings)
        with self.sessions.begin() as db:
            db.add(User(telegram_id=7, plan='admin', seconds=0))
            db.add(MediaCleanupEpoch(id=1, started_at=datetime.utcnow() - timedelta(hours=1)))
        self.task_id, self.token = self.task()

    def tearDown(self):
        self.stack.close()
        self.engine.dispose()
        with self.admin.begin() as conn:
            conn.execute(text(f'DROP SCHEMA {self.schema} CASCADE'))
        self.admin.dispose()

    def task(self):
        token = uuid4().hex
        with self.sessions.begin() as db:
            task = Task(user_id=7, status='loading', lease_token=token,
                        lease_until=datetime.utcnow() + timedelta(hours=1))
            db.add(task)
            db.flush()
            return task.id, token

    def attempt(self, key='source'):
        identity = uuid4().hex
        path = self.root / 'managed' / identity
        store.register_attempt(identity, self.task_id, self.token, path, store.source_key(key))
        path.mkdir(parents=True)
        return identity, path

    def file(self, attempt, path, scope='workspace'):
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_bytes(b'media')
        signature = fingerprint(path.stat())
        return store.register_file(attempt, path, 'telegram_cached_source' if scope == 'telegram'
                                   else 'workspace_source', scope,
                                   signature if scope == 'telegram' else attempt, signature)

    def row(self, identity):
        with self.sessions() as db:
            item = db.get(MediaFile, identity)
            return item.state, item.deleted_at, item.last_error

    def due(self):
        with self.sessions.begin() as db:
            db.query(MediaFile).filter(MediaFile.state == 'delete_pending').update(
                {'available_at': datetime.utcnow() - timedelta(seconds=1)})

    def drain(self, worker):
        for _ in range(50):
            if not worker.clean_one():
                with self.sessions() as db:
                    pending = db.query(MediaFile).filter_by(scope=worker.scope, state='delete_pending').count()
                if not pending:
                    return
                self.due()
        self.fail('Cleanup did not drain')

    def test_cleanup_retries_failure_and_confirms_missing_file(self):
        attempt, path = self.attempt()
        source = path / 'source.bin'
        identity = self.file(attempt, source)
        store.schedule_media_cleanup(attempt)
        with patch('app.cleanup.worker.delete_media_file', side_effect=PermissionError('secret path')):
            self.assertTrue(self.workspace.clean_one())
        self.assertEqual(self.row(identity), ('delete_pending', None, 'PermissionError'))
        source.unlink()  # Crash after unlink and before committing completion.
        self.due()
        self.assertTrue(self.workspace.clean_one())
        self.assertEqual(self.row(identity)[0], 'deleted')
        self.assertIsNotNone(self.row(identity)[1])

    def test_expired_cleanup_owner_cannot_delete_or_complete_new_claim(self):
        attempt, path = self.attempt()
        source = path / 'source.bin'
        identity = self.file(attempt, source)
        store.schedule_media_cleanup(attempt)
        old = store.claim_media_cleanup('workspace', self.settings)
        with self.sessions.begin() as db:
            db.get(MediaFile, identity).lease_until = datetime.utcnow() - timedelta(seconds=1)
        new = store.claim_media_cleanup('workspace', self.settings)
        with self.assertRaises(LeaseLost), store.deletion_claim(old):
            source.unlink()
        store.retry_media_cleanup(old, OSError(), self.settings)
        self.assertEqual(self.row(identity)[0], 'deleting')
        self.assertTrue(source.exists())
        with store.deletion_claim(new):
            source.unlink()
        self.assertEqual(self.row(identity)[0], 'deleted')

    def test_two_workers_never_claim_same_generation(self):
        attempt, path = self.attempt()
        identity = self.file(attempt, path / 'source.bin')
        store.schedule_media_cleanup(attempt)
        barrier = threading.Barrier(2)
        def claim():
            barrier.wait(5)
            return store.claim_media_cleanup('workspace', self.settings)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: claim(), range(2)))
        self.assertEqual([r['id'] for r in results if r], [identity])

    def test_processing_and_attempt_exit_do_not_wait_for_slow_deletion(self):
        entered, release = threading.Event(), threading.Event()
        def slow_delete(root, item):
            entered.set()
            if not release.wait(5):
                raise TimeoutError()
            return delete_media_file(root, item)
        async def scenario(pool):
            async with MediaFiles(self.task_id, self.token, Source(file_id='file'), self.media) as files:
                path = files.directory / 'source.bin'
                path.write_bytes(b'video')
                await files.ready(path)
                await files.delete_workspace_source(path)
                future = pool.submit(self.workspace.clean_one)
                while not entered.is_set():
                    await asyncio.sleep(0.01)
                # Save the result and close the attempt while unlink is blocked.
                media_store.reserve(7, self.task_id, 1, self.token)
                media_store.finish(7, self.task_id, 'ready', self.token)
            self.assertFalse(release.is_set())
            self.assertTrue(path.exists())
            return future
        with ThreadPoolExecutor(max_workers=1) as pool, \
                patch('app.cleanup.worker.delete_media_file', side_effect=slow_delete):
            try:
                async def bounded():
                    return await asyncio.wait_for(scenario(pool), 2)
                future = asyncio.run(bounded())
            finally:
                release.set()
            self.assertTrue(future.result(timeout=5))
        self.drain(self.workspace)

    def test_unrelated_source_can_be_deleted_while_another_downloads(self):
        attempt, _ = self.attempt('one')
        path = self.telegram / '123:token/videos/one.mp4'
        self.file(attempt, path, 'telegram')
        store.schedule_media_cleanup(attempt)
        with directory_guard(self.telegram), store.source_lock(store.source_key('another')):
            self.assertTrue(self.cache.clean_one())
        self.assertFalse(path.exists())

    def test_failed_producer_cleans_partial_source_and_dynamic_audio(self):
        async def scenario():
            with self.assertRaises(ValueError):
                async with MediaFiles(self.task_id, self.token, Source(file_id='file'), self.media) as files:
                    (files.directory / 'source.bin').write_bytes(b'partial')
                    chunks = files.directory / 'chunks'
                    chunks.mkdir()
                    (chunks / '000000.mp3').write_bytes(b'unregistered partial output')
                    raise ValueError('producer failed')
        asyncio.run(scenario())
        self.drain(self.workspace)
        self.assertEqual(list((self.root / 'managed').iterdir()), [])
        with self.sessions() as db:
            self.assertTrue(all(f.state == 'deleted' for f in db.query(MediaFile)))

    def test_paused_creator_cannot_recreate_recovered_attempt(self):
        register = store.register_attempt
        def pause_after_intent(*args):
            register(*args)
            with self.sessions.begin() as db:
                db.get(MediaAttempt, args[0]).created_at -= timedelta(hours=1)
                db.get(Task, self.task_id).lease_until = datetime.utcnow() - timedelta(seconds=1)
            self.workspace.reconcile_workspace()
        async def scenario():
            files = MediaFiles(self.task_id, self.token, Source(file_id='file'), self.media)
            with self.assertRaises(LeaseLost):
                await files.__aenter__()
            self.assertFalse(files.directory.exists())
        with patch.object(store, 'register_attempt', side_effect=pause_after_intent):
            asyncio.run(scenario())

    def test_shared_telegram_file_waits_for_last_use_and_source_lock(self):
        a, _ = self.attempt()
        b, _ = self.attempt()
        path = self.telegram / '123:token/videos/file.mp4'
        identity = self.file(a, path, 'telegram')
        self.assertEqual(self.file(b, path, 'telegram'), identity)
        store.schedule_media_cleanup(a)
        self.assertFalse(self.cache.clean_one())
        store.schedule_media_cleanup(b)
        with store.source_lock(store.source_key('source')):
            self.assertTrue(self.cache.clean_one())
            self.assertTrue(path.exists())
        self.due()
        self.assertTrue(self.cache.clean_one())
        self.assertFalse(path.exists())

    def test_replacement_is_preserved_and_can_be_registered_again(self):
        attempt, _ = self.attempt()
        path = self.telegram / '123:token/videos/file.mp4'
        old = self.file(attempt, path, 'telegram')
        store.schedule_media_cleanup(attempt)
        replacement = path.with_suffix('.new')
        replacement.write_bytes(b'new download')
        replacement.replace(path)
        self.cache.clean_one()
        self.assertEqual(path.read_bytes(), b'new download')
        self.assertEqual(self.row(old)[0], 'deleted')
        other, _ = self.attempt()
        new = self.file(other, path, 'telegram')
        self.assertNotEqual(old, new)
        store.schedule_media_cleanup(other)
        self.cache.clean_one()
        self.assertFalse(path.exists())

    def test_orphan_recovery_respects_live_process_even_after_lease_loss(self):
        attempt, path = self.attempt()
        partial = path / 'chunks/000000.mp3'
        partial.parent.mkdir()
        partial.write_bytes(b'partial')
        with self.sessions.begin() as db:
            db.get(MediaAttempt, attempt).created_at -= timedelta(hours=1)
            db.get(Task, self.task_id).lease_until = datetime.utcnow() - timedelta(seconds=1)
        with directory_guard(path):
            self.workspace.reconcile_workspace()
            self.assertFalse(self.workspace.clean_one())
            self.assertTrue(partial.exists())
        self.workspace.reconcile_workspace()
        self.drain(self.workspace)
        self.assertFalse(path.exists())
        with self.sessions() as db:
            self.assertTrue(all(f.state == 'deleted' for f in db.query(MediaFile)))

    def test_untracked_telegram_media_requires_epoch_age_stability_and_no_readers(self):
        path = self.telegram / '123:token/videos/file.mp4'
        path.parent.mkdir(parents=True)
        path.write_bytes(b'new orphan')
        future = datetime.utcnow() + timedelta(seconds=10)
        with patch('app.cleanup.worker.datetime') as clock:
            clock.utcnow.return_value = future
            clock.utcfromtimestamp.side_effect = datetime.utcfromtimestamp
            with directory_guard(self.telegram):
                self.cache.reconcile_telegram()
            with self.sessions() as db:
                self.assertEqual(db.query(MediaFile).count(), 0)
            # Pretend the file pre-dates installation: no adoption/deletion.
            with self.sessions.begin() as db:
                db.get(MediaCleanupEpoch, 1).started_at = future
            self.cache.reconcile_telegram()
            with self.sessions() as db:
                self.assertEqual(db.query(MediaFile).count(), 0)
            with self.sessions.begin() as db:
                db.get(MediaCleanupEpoch, 1).started_at = future - timedelta(hours=1)
            self.cache.reconcile_telegram()
            self.assertFalse(self.cache.clean_one())
            with self.sessions.begin() as db:
                db.query(MediaFile).one().created_at -= timedelta(seconds=10)
            self.cache.reconcile_telegram()
        self.cache.clean_one()
        self.assertFalse(path.exists())

    def test_orphan_observation_is_completed_when_telegram_already_removed_file(self):
        path = self.telegram / '123:token/videos/file.mp4'
        path.parent.mkdir(parents=True)
        path.write_bytes(b'orphan')
        store.observe_orphan(path, fingerprint(path.stat()), self.settings)
        with self.sessions.begin() as db:
            db.query(MediaFile).one().created_at -= timedelta(seconds=10)
        path.unlink()
        self.cache.reconcile_telegram()
        self.cache.clean_one()
        with self.sessions() as db:
            self.assertEqual(db.query(MediaFile).one().state, 'deleted')

    def test_pipeline_releases_each_input_at_earliest_safe_stage(self):
        cached = self.telegram / '123:token/videos/file.mp4'
        cached.parent.mkdir(parents=True)
        cached.write_bytes(b'video')
        bot = SimpleNamespace(get_file=AsyncMock(return_value=SimpleNamespace(
            file_size=5, file_path=str(cached))))
        chunks = []
        async def prepare(source, directory, settings):
            self.cache.clean_one()
            self.assertFalse(cached.exists())
            self.assertTrue(source.exists())
            directory.mkdir()
            for name in ('000000.mp3', '000001.mp3'):
                chunk = directory / name
                chunk.write_bytes(b'audio')
                chunks.append(chunk)
            return chunks
        calls = []
        def transcribe(**kwargs):
            self.drain(self.workspace)
            self.assertFalse(chunks[0].parent.parent.joinpath('source.bin').exists())
            if calls:
                self.assertFalse(chunks[0].exists())
            calls.append(1)
            return SimpleNamespace(text='hello')
        client = Mock()
        client.audio.transcriptions.create.side_effect = transcribe
        jobs = MediaJobs(bot, client, self.media)
        with patch('app.services.media_jobs.probe_duration', AsyncMock(return_value=1)), \
                patch('app.services.media_jobs.prepare_audio_chunks', side_effect=prepare):
            asyncio.run(jobs.process(7, 7, self.task_id, Source(file_id='file', identity='same-file'), self.token))
        self.assertEqual(len(calls), 2)
        self.drain(self.workspace)
        with self.sessions() as db:
            task = db.get(Task, self.task_id)
            self.assertEqual((task.status, task.result_text), ('completed', 'hello\n\nhello'))
            self.assertTrue(all(f.state == 'deleted' for f in db.query(MediaFile)))
        self.assertEqual(list((self.root / 'managed').iterdir()), [])
