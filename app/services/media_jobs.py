"""Orchestration: acquisition -> duration -> reservation -> audio -> transcript."""
import asyncio
import hashlib
import logging
from dataclasses import replace
from pathlib import Path
from app.media.config import SETTINGS
from app.media.process import MediaError
from app.media.audio import probe_duration, prepare_audio_chunks
from app.media.sources import (Source, youtube_url, youtube_metadata,
                              download_youtube, download_http, download_telegram)
from app.services import media_store as store
from app.services.transcription import transcribe_chunks
from app.cleanup.lifecycle import MediaFiles

class MediaJobs:
    def __init__(self, bot, client, settings=SETTINGS):
        self.bot, self.client, self.settings = bot, client, settings

    async def submit(self, user_id, chat_id, source, identity):
        if source.url:
            source = Source(url=youtube_url(source.url) or source.url)
            identity = source.url
        key = hashlib.sha256(f'{user_id}:{identity}'.encode()).hexdigest()
        store.claim_job(user_id, key, replace(source, identity=identity), chat_id)

    async def process(self, user_id, chat_id, task_id, source, lease_token=None):
        duration = source.duration
        try:
            settings = self.settings
            if source.size and source.size > settings.max_source_bytes:
                raise MediaError('Source exceeds the configured file size limit.')
            if duration:
                if duration > settings.max_duration_seconds:
                    raise MediaError('Recording exceeds the configured duration limit.')
                store.check_balance(user_id, duration)
            async with MediaFiles(task_id, lease_token, source, settings) as files:
                directory = files.directory
                if source.url:
                    yt_url = youtube_url(source.url)
                    if yt_url:
                        duration = await youtube_metadata(yt_url, settings)
                        store.check_balance(user_id, duration)
                        path = await download_youtube(yt_url, directory, settings)
                    else:
                        path = await download_http(source.url, directory, settings)
                else:
                    path = await download_telegram(self.bot, source, directory, settings, files)
                await files.ready(path)
                duration = await probe_duration(path, settings)
                store.reserve(user_id, task_id, duration, lease_token)
                # The durable attempt-directory intent covers dynamic FFmpeg output
                # names before any file is created, including failed extraction.
                chunks = await prepare_audio_chunks(path, Path(directory) / 'chunks', settings)
                await files.register_audio_chunks(chunks)
                await files.delete_workspace_source(path)
                async def progress(percent):
                    store.save_progress(user_id, task_id, percent, lease_token)
                text = await transcribe_chunks(self.client, chunks, progress, settings,
                                               release_chunk=files.delete_audio_chunk)
                store.finish(user_id, task_id, text, lease_token)
        except store.InsufficientBalance:
            store.wait_for_payment(user_id, task_id, duration, lease_token)
        except store.LeaseLost:
            logging.warning('Media job %s lost its lease', task_id)
        except asyncio.CancelledError:
            # Leave the persisted lease for recovery; cancellation is not failure.
            raise
        except Exception as error:
            logging.error('Media job %s failed (%s)', task_id, type(error).__name__)
            reason = str(error) if isinstance(error, MediaError) else 'Could not process this media. Send it again to retry.'
            store.fail(user_id, task_id, reason, lease_token)
