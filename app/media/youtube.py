"""Isolated yt-dlp downloader with a streaming byte limit.

Executed in a subprocess so timeout/cancellation stops network downloads.
"""
import sys
from pathlib import Path
from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError

class QuietLogger:
    def debug(self, message):
        pass
    info = warning = error = debug


def download(url, directory, max_bytes, timeout):
    def enforce_limit(progress):
        if progress.get('downloaded_bytes', 0) > max_bytes:
            raise DownloadError('Configured source size limit exceeded')
    options = {
        'format': 'bestaudio', 'noplaylist': True, 'cachedir': False,
        'outtmpl': str(Path(directory) / 'source.bin'),
        'max_filesize': max_bytes, 'socket_timeout': timeout,
        'progress_hooks': [enforce_limit], 'logger': QuietLogger(),
        'quiet': True, 'no_warnings': True, 'noprogress': True,
    }
    with YoutubeDL(options) as downloader:
        downloader.download([url])

if __name__ == '__main__':
    download(sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4]))
