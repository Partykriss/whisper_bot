"""Independent, bounded cleanup and orphan reconciliation executors."""
import asyncio
from contextlib import ExitStack
from datetime import datetime, timedelta
import logging
import os
from pathlib import Path
from app.cleanup import store
from app.cleanup.async_io import file_thread
from app.cleanup.config import SETTINGS
from app.cleanup.filesystem import (delete_media_file, directory_guard, fingerprint,
                                    inspect_file, inventory)
from app.media.config import SETTINGS as MEDIA_SETTINGS


class CleanupWorker:
    def __init__(self, scope, media=MEDIA_SETTINGS, settings=SETTINGS):
        self.scope, self.media, self.settings = scope, media, settings
        self.root = Path(media.telegram_local_root if scope == 'telegram' else media.downloads_dir).absolute()
        self.tasks = []

    def clean_one(self):
        item = store.claim_media_cleanup(self.scope, self.settings)
        if item is None:
            return False
        try:
            with ExitStack() as guards:
                if self.scope == 'telegram':
                    keys = store.cleanup_source_keys(item['id'])
                    guards.enter_context(directory_guard(self.root, exclusive=not keys))
                    for key in keys:
                        guards.enter_context(store.source_lock(key))
                with store.deletion_claim(item):
                    delete_media_file(self.root, item)
        except Exception as error:
            store.retry_media_cleanup(item, error, self.settings)
        return True

    def reconcile_workspace(self):
        for attempt_id, directory in store.orphan_attempts(self.settings):
            try:
                with store.source_lock(store.source_key('attempt:' + attempt_id)):
                    self.reconcile_attempt(attempt_id, Path(directory))
            except BlockingIOError:
                continue
            except (OSError, ValueError) as error:
                logging.error('Media attempt inventory deferred (%s)', type(error).__name__)

    def reconcile_attempt(self, attempt_id, directory):
        try:
            # Validate even the root of a registered attempt before walking it.
            inspect_file(self.root, directory, 'workspace')
        except FileNotFoundError:
            store.schedule_media_cleanup(attempt_id)
            store.mark_attempt_cleaned(attempt_id)
            return
        with directory_guard(directory, exclusive=True):
            known = {path: (identity, signature, state) for identity, path, signature, state
                     in store.workspace_files(attempt_id)}
            for path, info in inventory(self.root, 'workspace', directory):
                if str(path) in known and known[str(path)][1] is not None:
                    continue
                kind = 'audio_chunk' if path.parent.name == 'chunks' else 'workspace_source'
                store.register_file(attempt_id, str(path), kind, 'workspace', attempt_id, fingerprint(info))
            # Include empty directories, without following any symlinks.
            for base, dirs, files in os.walk(directory, followlinks=False):
                path = Path(base)
                if str(path) in known and known[str(path)][1] is not None:
                    continue
                try:
                    info = inspect_file(self.root, path, 'workspace')
                except FileNotFoundError:
                    continue
                store.register_file(attempt_id, str(path), 'workspace_directory',
                                    'workspace', attempt_id, fingerprint(info))
            # Missing planned files can be completed without a fingerprint.
            store.schedule_media_cleanup(attempt_id)

    def reconcile_telegram(self):
        try:
            with directory_guard(self.root, exclusive=True):
                epoch = store.cleanup_epoch()
                cutoff = datetime.utcnow() - timedelta(seconds=self.settings.orphan_guard_seconds)
                for path, info in inventory(self.root, 'telegram'):
                    # ctime covers rename into the completed directory; old cache
                    # remains untouched unless explicitly acquired by a new job.
                    created = datetime.utcfromtimestamp(info.st_ctime)
                    if epoch <= created <= cutoff:
                        store.observe_orphan(path, fingerprint(info), self.settings)
                store.queue_unreferenced_generations(self.settings)
        except (BlockingIOError, FileNotFoundError):
            return

    async def cleanup_loop(self):
        while True:
            try:
                if await file_thread(self.clean_one):
                    await asyncio.sleep(0)
                    continue
            except Exception as error:
                logging.error('Media cleanup worker failed (%s)', type(error).__name__)
            await asyncio.sleep(self.settings.scan_seconds)

    async def reconcile_loop(self):
        while True:
            try:
                await file_thread(self.reconcile_telegram if self.scope == 'telegram' else self.reconcile_workspace)
            except Exception as error:
                logging.error('Media reconciliation failed (%s)', type(error).__name__)
            await asyncio.sleep(self.settings.reconcile_seconds)

    async def __aenter__(self):
        self.tasks = [asyncio.create_task(self.cleanup_loop()) for _ in range(self.settings.workers)]
        self.tasks.append(asyncio.create_task(self.reconcile_loop()))
        return self

    async def __aexit__(self, *args):
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
