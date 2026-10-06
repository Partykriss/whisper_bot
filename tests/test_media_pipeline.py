import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from app.media.config import MediaSettings
from app.media.sources import Source
from app.media.process import MediaError
from app.services.media_jobs import MediaJobs
from app.services.media_store import InsufficientBalance
from app.ui.results import deliver_result
from app.services.transcription import transcribe_chunks

class PipelineTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.settings = MediaSettings(downloads_dir=self.temp.name)
        self.bot = SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(edit_text=AsyncMock())),
                                   send_document=AsyncMock())
        self.jobs = MediaJobs(self.bot, Mock(), self.settings)

    async def asyncTearDown(self):
        self.temp.cleanup()

    async def test_actual_duration_rejects_before_preparation_or_openai(self):
        with patch('app.services.media_jobs.download_telegram', AsyncMock(return_value=Path('media'))), \
             patch('app.services.media_jobs.probe_duration', AsyncMock(return_value=120)), \
             patch('app.services.media_jobs.store') as store, \
             patch('app.services.media_jobs.prepare_chunks', AsyncMock()) as prepare, \
             patch('app.services.media_jobs.transcribe_chunks', AsyncMock()) as transcribe, \
             patch('app.services.media_jobs.send_plan_invoices', AsyncMock()):
            store.InsufficientBalance = InsufficientBalance
            store.reserve.side_effect = InsufficientBalance(60, 120)
            await self.jobs.process(7, 42, 1, Source(file_id='file'))
            prepare.assert_not_awaited()
            transcribe.assert_not_awaited()
            store.wait_for_payment.assert_called_once_with(7, 1, 120)

    async def test_youtube_metadata_rejects_before_download(self):
        with patch('app.services.media_jobs.youtube_metadata', AsyncMock(return_value=120)), \
             patch('app.services.media_jobs.download_youtube', AsyncMock()) as download, \
             patch('app.services.media_jobs.store') as store, \
             patch('app.services.media_jobs.send_plan_invoices', AsyncMock()):
            store.InsufficientBalance = InsufficientBalance
            store.check_balance.side_effect = InsufficientBalance(60, 120)
            await self.jobs.process(7, 42, 1, Source(url='https://youtu.be/abcdefghijk'))
            download.assert_not_awaited()
            store.wait_for_payment.assert_called_once_with(7, 1, 120)

    async def test_failure_releases_and_never_finishes(self):
        with patch('app.services.media_jobs.download_telegram', AsyncMock(return_value=Path('media'))), \
             patch('app.services.media_jobs.probe_duration', AsyncMock(return_value=30)), \
             patch('app.services.media_jobs.prepare_chunks', AsyncMock(side_effect=MediaError('bad audio'))), \
             patch('app.services.media_jobs.store') as store:
            store.InsufficientBalance = InsufficientBalance
            await self.jobs.process(7, 42, 1, Source(file_id='file'))
            store.reserve.assert_called_once_with(7, 1, 30)
            store.finish.assert_not_called()
            store.fail.assert_called_once_with(7, 1, 'bad audio')

    async def test_success_reserves_before_openai_and_delivers(self):
        events = []
        with patch('app.services.media_jobs.download_http', AsyncMock(return_value=Path('media'))), \
             patch('app.services.media_jobs.probe_duration', AsyncMock(return_value=30)), \
             patch('app.services.media_jobs.prepare_chunks', AsyncMock(return_value=[Path('chunk')])), \
             patch('app.services.media_jobs.store') as store, \
             patch('app.services.media_jobs.transcribe_chunks', AsyncMock()) as transcribe:
            store.InsufficientBalance = InsufficientBalance
            store.reserve.side_effect = lambda *a: events.append('reserve')
            async def recognize(*args):
                events.append('openai')
                return 'hello'
            transcribe.side_effect = recognize
            await self.jobs.process(7, 42, 1, Source(url='https://example.com/audio.mp3'))
            self.assertEqual(events, ['reserve', 'openai'])
            store.finish.assert_called_once_with(7, 1, 'hello')
            self.bot.send_message.assert_any_await(42, 'hello', parse_mode=None)

    async def test_long_result_delivered_as_utf8_document(self):
        await deliver_result(self.bot, 42, 'Привет' * 1000, self.settings)
        document = self.bot.send_document.await_args.args[1]
        self.assertEqual(document.data.decode(), 'Привет' * 1000)
        self.bot.send_message.assert_not_awaited()

    async def test_chunks_stay_in_source_order(self):
        paths = [Path(self.temp.name) / f'{n}.mp3' for n in range(3)]
        for path in paths:
            path.write_bytes(b'audio')
        client = Mock()
        client.audio.transcriptions.create.side_effect = [SimpleNamespace(text=t) for t in ('one', 'two', 'three')]
        progress = AsyncMock()
        text = await transcribe_chunks(client, paths, progress, self.settings)
        self.assertEqual(text, 'one\n\ntwo\n\nthree')
        self.assertEqual([c.args[0] for c in progress.await_args_list], [33, 66, 100])
