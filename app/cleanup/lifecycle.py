"""Per-attempt ownership; release files without waiting for deletion."""
import asyncio
from contextlib import asynccontextmanager
import logging
from pathlib import Path
import os
import stat
from uuid import uuid4
from app.cleanup import store
from app.cleanup.async_io import file_thread
from app.cleanup.config import SETTINGS
from app.cleanup.filesystem import directory_guard, fingerprint, inspect_file


class MediaFiles:
    def __init__(self, task_id, token, source, media_settings, settings=SETTINGS):
        self.id = uuid4().hex
        self.task_id, self.token = task_id, token
        self.media, self.settings = media_settings, settings
        self.key = store.source_key(source.identity or source.file_id or source.url)
        self.root = Path(media_settings.downloads_dir).absolute()
        self.directory = self.root / 'managed' / self.id
        self.files = {}
        self.guard = None

    async def __aenter__(self):
        await file_thread(store.register_attempt, self.id, self.task_id, self.token, self.directory, self.key)
        try:
            # Coordinate the registered-intent -> directory/OS-lock handoff with
            # recovery, including a process paused before mkdir beyond its lease.
            with store.source_lock(store.source_key('attempt:' + self.id)):
                store.check_attempt_owner(self.id)
                await self.plan(self.directory, 'workspace_directory')
                self.directory.mkdir(parents=True, exist_ok=False)
                self.guard = directory_guard(self.directory)
                self.guard.__enter__()
                await self.ready(self.directory)
                await self.plan(self.directory / 'source.bin', 'workspace_source')
            return self
        except BaseException:
            try:
                await file_thread(self._finish)
            except Exception as error:
                logging.error('Media initialization cleanup deferred (%s)', type(error).__name__)
            finally:
                if self.guard:
                    self.guard.__exit__(None, None, None)
            raise

    async def plan(self, path, kind):
        identity = await file_thread(store.register_file, self.id, str(path), kind,
                                     'workspace', self.id)
        self.files[str(path)] = identity
        return identity

    async def ready(self, path):
        info = await file_thread(inspect_file, self.root, path, 'workspace')
        await file_thread(store.ready, self.files[str(path)], fingerprint(info))

    async def register_audio_chunks(self, chunks):
        for chunk in chunks:
            await self.plan(chunk, 'audio_chunk')
            await self.ready(chunk)

    async def delete_workspace_source(self, path):
        await self.ready(path)
        await self.schedule_media_cleanup(path)

    async def delete_audio_chunk(self, path):
        await self.schedule_media_cleanup(path)

    async def schedule_media_cleanup(self, path):
        await self._release(self.files[str(path)])

    async def _release(self, identity):
        try:
            await file_thread(store.schedule_media_cleanup, self.id, identity)
        except Exception as error:
            # The durable use/attempt remains registered for reconciliation.
            # A cleanup outage must not discard an already received transcript.
            logging.error('Media release deferred (%s)', type(error).__name__)

    async def register_telegram_source(self, path):
        info = await file_thread(inspect_file, self.media.telegram_local_root, path, 'telegram')
        signature = fingerprint(info)
        return await file_thread(store.register_file, self.id, str(path), 'telegram_cached_source',
                                 'telegram', signature, signature)

    async def delete_telegram_cached_source(self, identity):
        await self._release(identity)

    @asynccontextmanager
    async def telegram_acquisition(self):
        # Directory flock works across both container UIDs and read-only mounts;
        # unlike a DB lease it survives a DB outage until the reader actually exits.
        await file_thread(store.acquisition_intent, self.id, True)
        while True:
            cache_guard = directory_guard(self.media.telegram_local_root)
            source_guard = store.source_lock(self.key)
            cache_acquired = False
            try:
                cache_guard.__enter__()
                cache_acquired = True
                # Keep acquisition on the event-loop thread: try-lock is nonblocking
                # and no context-manager ownership is lost on cancellation.
                source_guard.__enter__()
                break
            except BlockingIOError:
                if cache_acquired:
                    cache_guard.__exit__(None, None, None)
                await asyncio.sleep(self.settings.scan_seconds)
            except BaseException:
                if cache_acquired:
                    cache_guard.__exit__(None, None, None)
                raise
        try:
            yield
        finally:
            try:
                source_guard.__exit__(None, None, None)
            finally:
                cache_guard.__exit__(None, None, None)
            await file_thread(store.acquisition_intent, self.id, False)

    def _finish(self):
        # Producer scope was persisted before creation; inventory dynamic FFmpeg
        # outputs and partial downloads even when their producer failed.
        known = {path: (identity, signature, state) for identity, path, signature, state
                 in store.workspace_files(self.id)}
        for base, directories, files in os.walk(self.directory, followlinks=False):
            for name in directories + files:
                path = Path(base) / name
                if str(path) in known and known[str(path)][1] is not None:
                    continue
                try:
                    info = inspect_file(self.root, path, 'workspace')
                except (OSError, ValueError):
                    continue
                kind = ('workspace_directory' if stat.S_ISDIR(info.st_mode) else
                        'audio_chunk' if path.parent.name == 'chunks' else 'workspace_source')
                store.register_file(self.id, str(path), kind, 'workspace', self.id, fingerprint(info))
        store.schedule_media_cleanup(self.id)

    async def __aexit__(self, *args):
        try:
            await file_thread(self._finish)
        except Exception as error:
            logging.error('Media cleanup registration failed (%s); reconciliation will retry', type(error).__name__)
        finally:
            if self.guard:
                self.guard.__exit__(None, None, None)
