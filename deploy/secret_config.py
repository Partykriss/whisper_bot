"""Validate decrypted dotenv input and prepare literal Compose runtime files."""
import io
import re
from urllib.parse import unquote, urlsplit

from dotenv import dotenv_values


def prepare_secrets(plaintext):
    values = dict(dotenv_values(stream=io.StringIO(plaintext), interpolate=False))
    if any(not re.fullmatch(r'[A-Z][A-Z0-9_]*', key) or value is None
           or any(char in value for char in '\r\n\x00') for key, value in values.items()):
        raise ValueError('Secret configuration must contain single-line KEY=value entries')
    if any(not values.get(key) for key in ('BOT_TOKEN', 'OPENAI_API_KEY', 'DATABASE_URL')):
        raise ValueError('BOT_TOKEN, OPENAI_API_KEY and DATABASE_URL are required')
    url = urlsplit(values['DATABASE_URL'])
    if (url.scheme != 'postgresql+psycopg2' or url.hostname != 'db'
            or url.port not in (None, 5432) or not url.username or not url.password
            or not re.fullmatch(r'/[A-Za-z_][A-Za-z0-9_]*', url.path)
            or url.query or url.fragment):
        raise ValueError('DATABASE_URL must target the Compose db on port 5432, without query options')
    database = {'POSTGRES_USER': unquote(url.username), 'POSTGRES_PASSWORD': unquote(url.password),
                'POSTGRES_DB': url.path[1:]}
    if any(any(char in value for char in '\r\n\x00') for value in database.values()):
        raise ValueError('Invalid database credentials')
    if any(key in values and values[key] != value for key, value in database.items()):
        raise ValueError('POSTGRES settings must agree with DATABASE_URL')
    base = values.get('MEDIA_TELEGRAM_API_BASE', '')
    if base and base != 'http://telegram-api:8081':
        raise ValueError('Local Telegram API must use http://telegram-api:8081')
    telegram = {'TELEGRAM_LOCAL': '1'}
    if base:
        if not values.get('TELEGRAM_API_ID', '').isdigit() or not values.get('TELEGRAM_API_HASH'):
            raise ValueError('Local Telegram API requires TELEGRAM_API_ID and TELEGRAM_API_HASH')
        telegram.update({key: values[key] for key in ('TELEGRAM_API_ID', 'TELEGRAM_API_HASH')})
    values['MEDIA_DOWNLOADS_DIR'] = '/code/downloads'
    values['MEDIA_TELEGRAM_LOCAL_ROOT'] = '/var/lib/telegram-bot-api'
    return values, database, telegram, bool(base)


def raw_env(values):
    # Compose format: raw preserves dollar signs, quotes and shell metacharacters.
    return ''.join(f'{key}={value}\n' for key, value in sorted(values.items()))
