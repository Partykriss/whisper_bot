"""All media processing tuning; durations in seconds, sizes in bytes."""
import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()

@dataclass(frozen=True)
class MediaSettings:
    max_source_bytes: int = 2 * 1024**3
    max_duration_seconds: int = 6 * 3600
    chunk_seconds: int = 600
    audio_bitrate_kbps: int = 64
    audio_sample_rate: int = 16000
    upload_max_bytes: int = 25_000_000
    process_timeout_seconds: int = 3600
    download_timeout_seconds: int = 3600
    concurrent_jobs: int = 2
    message_max_units: int = 4000
    download_block_bytes: int = 256 * 1024
    max_redirects: int = 5
    downloads_dir: str = 'downloads'
    telegram_api_base: str = ''
    telegram_local_root: str = '/var/lib/telegram-bot-api'
    transcription_model: str = 'whisper-1'

    def __post_init__(self):
        for name, value in vars(self).items():
            if isinstance(value, int) and value <= 0:
                raise ValueError(f'{name} must be positive')
        if self.message_max_units > 4096:
            raise ValueError('message_max_units exceeds Telegram limit')
        if self.upload_max_bytes > 25_000_000:
            raise ValueError('upload_max_bytes exceeds transcription API limit')

    @classmethod
    def from_env(cls):
        values = {}
        for name, field in cls.__dataclass_fields__.items():
            value = os.getenv('MEDIA_' + name.upper())
            if value is not None:
                values[name] = int(value) if field.type is int else value
        return cls(**values)

SETTINGS = MediaSettings.from_env()
# Cloud Telegram download contract, not a tuning value.
TELEGRAM_CLOUD_MAX_BYTES = 20_000_000
