"""One result delivery path for new transcriptions and cached results."""
from aiogram.types import BufferedInputFile
from app.media.config import SETTINGS

async def deliver_result(bot, chat_id, text, settings=SETTINGS):
    text = text or 'No speech was recognized in this recording.'
    if len(text.encode('utf-16-le')) // 2 <= settings.message_max_units:
        return await bot.send_message(chat_id, text, parse_mode=None)
    else:
        return await bot.send_document(chat_id,
            BufferedInputFile(text.encode('utf-8'), filename='transcript.txt'),
            caption='Transcription complete.')
