"""OpenAI adapter: audio-only chunks, called after balance reservation."""
import asyncio
from pathlib import Path
from app.media.config import SETTINGS
from app.media.process import MediaError


def transcribe_media(client, file_path, settings=SETTINGS, prompt=None):
    file_path = Path(file_path)
    if file_path.stat().st_size > settings.upload_max_bytes:
        raise MediaError('Audio chunk exceeds the transcription upload limit.')
    with file_path.open('rb') as audio:
        options = dict(model=settings.transcription_model, file=audio)
        if prompt:
            options['prompt'] = prompt
        return client.audio.transcriptions.create(**options).text


async def transcribe_chunks(client, chunks, progress, settings=SETTINGS):
    results = []
    for index, chunk in enumerate(chunks):
        text = await asyncio.to_thread(transcribe_media, client, chunk, settings)
        results.append(text.strip())
        await progress(int((index + 1) * 100 / len(chunks)))
    return '\n\n'.join(results)
