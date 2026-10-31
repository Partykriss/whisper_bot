"""One-shot deployment probe; never starts Telegram polling or spends OpenAI credits."""
import asyncio
import os
import sys

from aiogram import Bot
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from sqlalchemy import create_engine, text


async def probe():
    engine = create_engine(os.environ['DATABASE_URL'], connect_args={'connect_timeout': 10})
    try:
        with engine.connect() as connection:
            connection.execute(text('SELECT version_num FROM alembic_version')).one()
            connection.execute(text('SELECT id FROM users LIMIT 0'))
            connection.execute(text('SELECT id FROM media_files LIMIT 0'))
            connection.execute(text('SELECT started_at FROM media_cleanup_epoch WHERE id = 1')).one()
    finally:
        engine.dispose()
    base = os.environ.get('MEDIA_TELEGRAM_API_BASE')
    session = AiohttpSession(api=TelegramAPIServer.from_base(base, is_local=True)) if base else AiohttpSession()
    async with Bot(os.environ['BOT_TOKEN'], session=session) as bot:
        await bot.get_me(request_timeout=15)
    print('Database and Telegram deployment probe passed')


if __name__ == '__main__':
    try:
        asyncio.run(probe())
    except Exception:
        print('Deployment probe failed; details suppressed to protect credentials', file=sys.stderr)
        sys.exit(1)
