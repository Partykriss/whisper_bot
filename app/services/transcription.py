"""OpenAI adapter: audio-only chunks, called after balance reservation."""
from pathlib import Path
from app.media.config import SETTINGS
from app.media.process import MediaError
from app.cleanup.async_io import file_thread


def transcribe_audio_chunk(client, file_path, settings=SETTINGS, prompt=None):
    file_path = Path(file_path)
    if file_path.stat().st_size > settings.upload_max_bytes:
        raise MediaError('Audio chunk exceeds the transcription upload limit.')
    with file_path.open('rb') as audio:
        options = dict(model=settings.transcription_model, file=audio)
        if prompt:
            options['prompt'] = prompt
        return client.audio.transcriptions.create(**options).text


async def transcribe_chunks(client, chunks, progress, settings=SETTINGS, release_chunk=None):
    results = []
    for index, chunk in enumerate(chunks):
        text = await file_thread(transcribe_audio_chunk, client, chunk, settings)
        results.append(text.strip())
        if release_chunk:
            await release_chunk(chunk)
        await progress(int((index + 1) * 100 / len(chunks)))
    return '\n\n'.join(results)
