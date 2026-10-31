import asyncio
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from app.cleanup.async_io import file_thread
from app.cleanup.filesystem import delete_media_file, fingerprint, inspect_file, inventory
from app.cleanup.lifecycle import MediaFiles
from app.media.config import MediaSettings
from app.media.sources import Source


class FilesystemCleanupTests(unittest.TestCase):
    def test_replacement_generation_is_not_deleted(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / 'managed' / ('a' * 32) / 'source.bin'
            path.parent.mkdir(parents=True)
            path.write_bytes(b'old')
            item = dict(path=str(path), scope='workspace', kind='workspace_source',
                        fingerprint=fingerprint(path.stat()))
            replacement = path.with_name('new')
            replacement.write_bytes(b'new-generation')
            replacement.replace(path)
            self.assertEqual(delete_media_file(root, item), 'replaced')
            self.assertEqual(path.read_bytes(), b'new-generation')

    def test_symlinks_and_service_paths_are_never_deleted(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            other = root / 'protected'
            other.write_bytes(b'keep')
            managed = root / 'managed' / ('a' * 32)
            managed.mkdir(parents=True)
            symlink = managed / 'source.bin'
            symlink.symlink_to(other)
            item = dict(path=str(symlink), scope='workspace', kind='workspace_source',
                        fingerprint=fingerprint(other.stat()))
            with self.assertRaises(ValueError):
                delete_media_file(root, item)
            symlink.unlink()
            managed.rmdir()
            managed.symlink_to(root, target_is_directory=True)
            with self.assertRaises(OSError):
                inspect_file(root, managed / 'protected', 'workspace')
            for relative in ('123:token/db.bin', '123:token/temp/file.bin', '../protected'):
                with self.assertRaises(ValueError):
                    inspect_file(root, root / relative, 'telegram')
            self.assertEqual(other.read_bytes(), b'keep')

    def test_telegram_inventory_excludes_temp_and_service_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for relative in ('123:token/videos/file.mp4', '123:token/temp/partial',
                             '123:token/db.bin', 'session.bin', '123:token/videos/.partial'):
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b'test')
            self.assertEqual([p.relative_to(root).as_posix() for p, _ in inventory(root, 'telegram')],
                             ['123:token/videos/file.mp4'])


class AsyncCleanupTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancellation_waits_for_file_reader_to_close(self):
        entered, release, closed = threading.Event(), threading.Event(), threading.Event()
        def read():
            entered.set()
            release.wait(5)
            closed.set()
        work = asyncio.create_task(file_thread(read))
        while not entered.is_set():
            await asyncio.sleep(0.01)
        work.cancel()
        await asyncio.sleep(0)
        self.assertFalse(work.done())
        self.assertFalse(closed.is_set())
        release.set()
        with self.assertRaises(asyncio.CancelledError):
            await work
        self.assertTrue(closed.is_set())

    async def test_cleanup_registration_failure_does_not_repeat_recognition(self):
        files = MediaFiles(1, 'token', Source(file_id='file'), MediaSettings())
        files.files['chunk'] = 1
        with patch('app.cleanup.lifecycle.store.schedule_media_cleanup', side_effect=OSError('private')):
            # The existing durable use is recovered later, without failing the job.
            await files.delete_audio_chunk('chunk')
