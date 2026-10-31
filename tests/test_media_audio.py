import shutil
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from app.media.config import MediaSettings
from app.media.process import MediaError, run_process
from app.media.audio import probe_duration, prepare_audio_chunks

@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'FFmpeg integration requires Docker or local FFmpeg')
class AudioTests(unittest.IsolatedAsyncioTestCase):
    async def test_video_audio_extraction_split_and_size_guard(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'video.mp4'
            settings = MediaSettings(chunk_seconds=1, max_duration_seconds=10)
            await run_process('ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'color=size=16x16:rate=10',
                              '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=16000',
                              '-t', '3', '-c:v', 'mpeg4', '-c:a', 'aac', source, timeout=30)
            duration = await probe_duration(source, settings)
            self.assertAlmostEqual(duration, 3, delta=0.2)
            chunks = await prepare_audio_chunks(source, Path(directory) / 'chunks', settings)
            self.assertGreaterEqual(len(chunks), 3)
            for chunk in chunks:
                self.assertLessEqual(chunk.stat().st_size, settings.upload_max_bytes)
            with self.assertRaises(MediaError):
                await prepare_audio_chunks(source, Path(directory) / 'tiny', replace(settings, upload_max_bytes=1))
            with self.assertRaises(MediaError):
                await probe_duration(source, replace(settings, max_duration_seconds=1))

    async def test_silent_video_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'silent.mp4'
            await run_process('ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'color=size=16x16',
                              '-t', '1', '-c:v', 'mpeg4', source, timeout=30)
            with self.assertRaisesRegex(MediaError, 'no audio'):
                await probe_duration(source, MediaSettings())

    async def test_source_larger_than_api_limit_is_split_into_small_audio_parts(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'large.wav'
            settings = MediaSettings(chunk_seconds=10, max_duration_seconds=60)
            await run_process('ffmpeg', '-v', 'error', '-f', 'lavfi',
                              '-i', 'sine=frequency=440:sample_rate=192000',
                              '-ac', '2', '-t', '40', source, timeout=30)
            self.assertGreater(source.stat().st_size, settings.upload_max_bytes)
            await probe_duration(source, settings)
            chunks = await prepare_audio_chunks(source, Path(directory) / 'chunks', settings)
            self.assertGreaterEqual(len(chunks), 4)
            self.assertTrue(all(p.stat().st_size <= settings.upload_max_bytes for p in chunks))
