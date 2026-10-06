"""Orchestration: acquisition -> duration -> reservation -> audio -> transcript."""
import asyncio
import hashlib
import logging
import tempfile
from pathlib import Path
from app.media.config import SETTINGS
from app.media.process import MediaError
from app.media.audio import probe_duration, prepare_chunks
from app.media.sources import (Source, youtube_url, youtube_metadata,
                              download_youtube, download_http, download_telegram)
from app.services import media_store as store
from app.services.transcription import transcribe_chunks
from app.payments import send_plan_invoices
from app.ui.messages import format_duration
from app.ui.results import deliver_result

class MediaJobs:
    def __init__(self, bot, client, settings=SETTINGS):
        self.bot, self.client, self.settings = bot, client, settings
        self.slots = asyncio.Semaphore(settings.concurrent_jobs)

    async def submit(self, user_id, chat_id, source, identity):
        if source.url:
            source = Source(url=youtube_url(source.url) or source.url)
            identity = source.url
        key = hashlib.sha256(f'{user_id}:{identity}'.encode()).hexdigest()
        task_id, status, cached = store.claim_job(user_id, key, source)
        if status == 'completed':
            await deliver_result(self.bot, chat_id, cached, self.settings)
            return
        if status != 'claimed':
            await self.bot.send_message(chat_id, 'This recording is already queued or processing.')
            return
        store.set_chat(user_id, task_id, chat_id)
        await self.process(user_id, chat_id, task_id, source)

    async def resume(self, user_id, task_id):
        claimed = store.claim_pending(user_id, task_id)
        if claimed:
            source, chat_id = claimed
            await self.process(user_id, chat_id, task_id, source)

    async def process(self, user_id, chat_id, task_id, source):
        status_message = None
        duration = source.duration
        try:
            status_message = await self.bot.send_message(chat_id, 'Media queued. Checking limits...')
            async with self.slots:
                settings = self.settings
                if source.size and source.size > settings.max_source_bytes:
                    raise MediaError('Source exceeds the configured file size limit.')
                if duration:
                    if duration > settings.max_duration_seconds:
                        raise MediaError('Recording exceeds the configured duration limit.')
                    store.check_balance(user_id, duration)
                Path(settings.downloads_dir).mkdir(parents=True, exist_ok=True)
                with tempfile.TemporaryDirectory(prefix='job-', dir=settings.downloads_dir) as directory:
                    if source.url:
                        yt_url = youtube_url(source.url)
                        if yt_url:
                            duration = await youtube_metadata(yt_url, settings)
                            store.check_balance(user_id, duration)
                            path = await download_youtube(yt_url, directory, settings)
                        else:
                            path = await download_http(source.url, directory, settings)
                    else:
                        path = await download_telegram(self.bot, source, directory, settings)
                    duration = await probe_duration(path, settings)
                    store.reserve(user_id, task_id, duration)
                    await status_message.edit_text('Preparing audio...')
                    chunks = await prepare_chunks(path, Path(directory) / 'chunks', settings)
                    await status_message.edit_text(f'Transcribing {len(chunks)} audio part(s)...')
                    async def progress(percent):
                        store.save_progress(user_id, task_id, percent)
                        try:
                            await status_message.edit_text(f'Transcribing audio: {percent}%')
                        except Exception:
                            # Progress notification failures must not abort paid work.
                            pass
                    text = await transcribe_chunks(self.client, chunks, progress, settings)
                    store.finish(user_id, task_id, text)
                # Delivery is outside the transcription transaction: a delivery
                # failure keeps the cached result and never releases spent time.
                await deliver_result(self.bot, chat_id, text, settings)
                await status_message.edit_text('Transcription complete.')
        except store.InsufficientBalance as error:
            store.wait_for_payment(user_id, task_id, duration)
            await self.bot.send_message(chat_id,
                f'Insufficient balance. Remaining: {format_duration(error.available)}, '
                f'required: {format_duration(error.required)}. No transcription was started.')
            await send_plan_invoices(self.bot, chat_id)
            if status_message:
                await status_message.edit_text('Waiting for payment.')
        except asyncio.CancelledError:
            store.fail(user_id, task_id, 'Processing interrupted. Send the source again to retry.')
            raise
        except Exception as error:
            # Keep credentials and provider URLs out of logs and user messages.
            logging.error('Media job %s failed (%s)', task_id, type(error).__name__)
            reason = str(error) if isinstance(error, MediaError) else 'Could not process or deliver this media. Send it again to retry.'
            store.fail(user_id, task_id, reason)
            if status_message:
                await status_message.edit_text(reason)
