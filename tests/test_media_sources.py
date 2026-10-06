import socket
import unittest
from unittest.mock import AsyncMock, patch
from app.media.sources import validate_url, youtube_url, PublicResolver
from app.media.process import MediaError

class URLTests(unittest.TestCase):
    def test_private_literal_and_credentials_are_rejected(self):
        for url in ('http://127.0.0.1/a', 'http://169.254.169.254/a', 'http://[::1]/a',
                    'http://localhost/a', 'http://user:pass@example.com/a', 'file:///tmp/a',
                    'https://example.com:5432/a'):
            with self.subTest(url=url), self.assertRaises(MediaError):
                validate_url(url)

    def test_youtube_single_video_canonicalization(self):
        expected = 'https://www.youtube.com/watch?v=abcdefghijk'
        self.assertEqual(youtube_url('https://youtu.be/abcdefghijk?t=30'), expected)
        self.assertEqual(youtube_url('https://www.youtube.com/shorts/abcdefghijk'), expected)
        self.assertIsNone(youtube_url('https://example.com/audio.mp3'))
        with self.assertRaises(MediaError):
            youtube_url('https://www.youtube.com/playlist?list=abc')

class ResolverTests(unittest.IsolatedAsyncioTestCase):
    async def test_private_dns_answer_is_rejected(self):
        with patch('asyncio.get_running_loop') as loop:
            loop.return_value.getaddrinfo = AsyncMock(return_value=[
                (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('10.0.0.1', 443))])
            with self.assertRaises(MediaError):
                await PublicResolver().resolve('public.example', 443)

class HTTPDownloadTests(unittest.IsolatedAsyncioTestCase):
    async def test_private_redirect_is_rejected_before_second_request(self):
        from types import SimpleNamespace
        from app.media.sources import download_http
        from app.media.config import MediaSettings
        response = SimpleNamespace(status=302, headers={'Location': 'http://127.0.0.1/private'})
        context = AsyncMock()
        context.__aenter__.return_value = response
        session = AsyncMock()
        session.get = unittest.mock.Mock(return_value=context)
        session_context = AsyncMock()
        session_context.__aenter__.return_value = session
        with patch('app.media.sources.aiohttp.ClientSession', return_value=session_context), \
             patch('app.media.sources.aiohttp.TCPConnector'):
            with self.assertRaises(MediaError):
                await download_http('https://example.com/audio', '/tmp', MediaSettings())
        session.get.assert_called_once()

    async def test_streamed_size_limit_works_without_content_length(self):
        import tempfile
        from types import SimpleNamespace
        from app.media.sources import download_http
        from app.media.config import MediaSettings
        async def blocks(size):
            yield b'1234'
            yield b'5678'
        response = SimpleNamespace(status=200, content_length=None,
                                   content=SimpleNamespace(iter_chunked=blocks))
        context = AsyncMock()
        context.__aenter__.return_value = response
        session = AsyncMock()
        session.get = unittest.mock.Mock(return_value=context)
        session_context = AsyncMock()
        session_context.__aenter__.return_value = session
        with tempfile.TemporaryDirectory() as directory, \
             patch('app.media.sources.aiohttp.ClientSession', return_value=session_context), \
             patch('app.media.sources.aiohttp.TCPConnector'):
            with self.assertRaises(MediaError):
                await download_http('https://example.com/audio', directory,
                                    MediaSettings(max_source_bytes=5))

class TelegramDownloadTests(unittest.IsolatedAsyncioTestCase):
    async def test_cloud_rejects_large_file_without_requesting_it(self):
        import tempfile
        from types import SimpleNamespace
        from app.media.sources import download_telegram, Source
        from app.media.config import MediaSettings
        bot = SimpleNamespace(get_file=AsyncMock(), download_file=AsyncMock())
        with tempfile.TemporaryDirectory() as directory, self.assertRaises(MediaError):
            await download_telegram(bot, Source(file_id='file', size=21_000_000),
                                    directory, MediaSettings())
        bot.get_file.assert_not_awaited()

    async def test_local_api_copies_large_file_and_preserves_api_cache(self):
        import tempfile
        from pathlib import Path
        from types import SimpleNamespace
        from app.media.sources import download_telegram, Source
        from app.media.config import MediaSettings
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'api'
            root.mkdir()
            cached = root / 'media.mp4'
            with cached.open('wb') as output:
                output.truncate(21_000_000)
            downloads = Path(directory) / 'downloads'
            downloads.mkdir()
            bot = SimpleNamespace(get_file=AsyncMock(return_value=SimpleNamespace(
                file_size=21_000_000, file_path=str(cached))), download_file=AsyncMock())
            settings = MediaSettings(telegram_api_base='http://telegram-api:8081',
                                     telegram_local_root=str(root))
            target = await download_telegram(bot, Source(file_id='file', size=21_000_000),
                                             downloads, settings)
            self.assertEqual(target.stat().st_size, 21_000_000)
            self.assertTrue(cached.is_file())
            bot.download_file.assert_not_awaited()
