"""Telegram, direct public HTTP files and public YouTube recordings."""
import asyncio
import ipaddress
import json
import re
import shutil
import socket
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit, parse_qs, urljoin
import aiohttp
from app.media.config import TELEGRAM_CLOUD_MAX_BYTES
from app.media.process import MediaError, run_process

@dataclass(frozen=True)
class Source:
    file_id: str | None = None
    url: str | None = None
    duration: float | None = None
    size: int | None = None


def validate_url(url):
    parsed = urlsplit(url)
    if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password:
        raise MediaError('Send a public HTTP(S) media link without embedded credentials.')
    if parsed.port not in (None, 80, 443):
        raise MediaError('Only standard HTTP(S) ports are supported.')
    host = parsed.hostname.lower().rstrip('.')
    if host == 'localhost':
        raise MediaError('Private network links are not supported.')
    try:
        if not ipaddress.ip_address(host).is_global:
            raise MediaError('Private network links are not supported.')
    except ValueError:
        pass
    return parsed


def youtube_url(url):
    parsed = validate_url(url)
    host = parsed.hostname.lower().rstrip('.')
    if host not in ('youtube.com', 'www.youtube.com', 'm.youtube.com', 'youtu.be'):
        return None
    if parsed.path == '/watch':
        video_id = parse_qs(parsed.query).get('v', [''])[0]
    elif host == 'youtu.be':
        video_id = parsed.path.strip('/')
    elif parsed.path.startswith(('/shorts/', '/embed/')):
        video_id = parsed.path.split('/')[2]
    else:
        raise MediaError('Send a single YouTube video, not a channel or playlist.')
    if not re.fullmatch(r'[A-Za-z0-9_-]{11}', video_id):
        raise MediaError('Invalid YouTube video link.')
    return f'https://www.youtube.com/watch?v={video_id}'

class PublicResolver(aiohttp.abc.AbstractResolver):
    async def resolve(self, host, port=0, family=socket.AF_INET):
        records = await asyncio.get_running_loop().getaddrinfo(
            host, port, family=family, type=socket.SOCK_STREAM)
        if any(not ipaddress.ip_address(r[4][0]).is_global for r in records):
            raise MediaError('Private network destinations are not supported.')
        return [dict(hostname=host, host=r[4][0], port=port, family=r[0],
                     proto=r[2], flags=socket.AI_NUMERICHOST) for r in records]
    async def close(self):
        pass

async def youtube_metadata(url, settings):
    result = await run_process(sys.executable, '-m', 'yt_dlp', '--ignore-config',
        '--no-playlist', '--no-cache-dir', '--dump-single-json', '--skip-download',
        '--', url, timeout=settings.download_timeout_seconds)
    info = json.loads(result)
    if info.get('is_live') or info.get('live_status') in ('is_live', 'is_upcoming', 'post_live'):
        raise MediaError('Live streams are not supported. Send a completed recording.')
    duration = info.get('duration')
    if duration is None or duration <= 0:
        raise MediaError('YouTube did not provide a recording duration.')
    if duration > settings.max_duration_seconds:
        raise MediaError('Recording exceeds the configured duration limit.')
    return float(duration)

async def download_youtube(url, directory, settings):
    await run_process(sys.executable, '-m', 'app.media.youtube',
        url, directory, settings.max_source_bytes, settings.download_timeout_seconds,
        timeout=settings.download_timeout_seconds)
    paths = [p for p in Path(directory).glob('source.*') if p.suffix != '.part']
    if len(paths) != 1:
        raise MediaError('YouTube audio could not be downloaded within the file limit.')
    if paths[0].stat().st_size > settings.max_source_bytes:
        raise MediaError('Source exceeds the configured file size limit.')
    return paths[0]

async def download_http(url, directory, settings):
    target = Path(directory) / 'source.bin'
    connector = aiohttp.TCPConnector(resolver=PublicResolver(), use_dns_cache=False)
    timeout = aiohttp.ClientTimeout(total=settings.download_timeout_seconds)
    async with aiohttp.ClientSession(connector=connector, timeout=timeout, trust_env=False,
                                     auto_decompress=False) as session:
        for _ in range(settings.max_redirects + 1):
            validate_url(url)
            async with session.get(url, allow_redirects=False) as response:
                if response.status in (301, 302, 303, 307, 308):
                    location = response.headers.get('Location')
                    if not location:
                        raise MediaError('Media link returned an invalid redirect.')
                    url = urljoin(url, location)
                    continue
                if response.status != 200:
                    raise MediaError('Media link is unavailable or requires authentication.')
                if response.content_length and response.content_length > settings.max_source_bytes:
                    raise MediaError('Source exceeds the configured file size limit.')
                size = 0
                with target.open('wb') as output:
                    async for block in response.content.iter_chunked(settings.download_block_bytes):
                        size += len(block)
                        if size > settings.max_source_bytes:
                            raise MediaError('Source exceeds the configured file size limit.')
                        output.write(block)
                if not size:
                    raise MediaError('The downloaded file is empty.')
                return target
    raise MediaError('Too many media link redirects.')

async def download_telegram(bot, source, directory, settings):
    limit = settings.max_source_bytes if settings.telegram_api_base else min(
        settings.max_source_bytes, TELEGRAM_CLOUD_MAX_BYTES)
    if source.size and source.size > limit:
        raise MediaError('File is too large. Large Telegram files require the local Bot API server.')
    file = await bot.get_file(source.file_id)
    if file.file_size and file.file_size > limit:
        raise MediaError('File exceeds the Telegram download or configured size limit.')
    target = Path(directory) / 'source.bin'
    if settings.telegram_api_base:
        root = Path(settings.telegram_local_root).resolve()
        local = Path(file.file_path).resolve()
        if not local.is_relative_to(root) or not local.is_file():
            raise MediaError('Local Telegram file storage is not mounted correctly.')
        if local.stat().st_size > limit:
            raise MediaError('Source exceeds the configured file size limit.')
        await asyncio.to_thread(shutil.copyfile, local, target)
    else:
        await bot.download_file(file.file_path, target, timeout=settings.download_timeout_seconds)
    if target.stat().st_size > limit:
        raise MediaError('Source exceeds the configured file size limit.')
    return target
