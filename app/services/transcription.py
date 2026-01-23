import os
import asyncio
import logging
from aiogram import Bot
from app.services.users import subtract_user_seconds
from app.services.tasks import update_task_in_db, set_task_status
from app.ui.messages import send_expiry_warning_if_needed


def transcribe_media(client, file_path: str) -> str:
    """Send media file to OpenAI and return text results."""
    with open(file_path, "rb") as file:
        transcript = client.audio.transcriptions.create(
            model="whisper-1",
            file=file
        )
    return transcript.text


async def process_task_by_file_id(
    bot: Bot,
    client,
    task,
    user_id: int,
):
    """Download, transcribe, and finalize a task by Telegram file_id."""
    if not task.file_id or not task.chat_id:
        return

    set_task_status(task.id, "processing")
    status_msg = await bot.send_message(task.chat_id, "Processing your media...")
    local_path = None
    try:
        file = await bot.get_file(task.file_id)
        ext = file.file_path.split(".")[-1]
        local_path = f"downloads/{task.file_id}.{ext}"
        os.makedirs("downloads", exist_ok=True)

        await bot.download_file(file.file_path, local_path)
        text = await asyncio.to_thread(transcribe_media, client, local_path)

        update_task_in_db(task.id, text, "completed", duration_seconds=task.duration_seconds)
        if task.duration_seconds:
            subtract_user_seconds(user_id, task.duration_seconds)

        await status_msg.edit_text(text)
        await send_expiry_warning_if_needed(bot, user_id, task.chat_id)
    except Exception as e:
        logging.error(f"Error: {e}")
        update_task_in_db(task.id, str(e), "failed")
        await status_msg.edit_text("Failed to process media.")
    finally:
        if local_path and os.path.exists(local_path):
            os.remove(local_path)
