"""Probe recordings and produce chronologically ordered audio-only chunks."""
import json
import math
from pathlib import Path
from app.media.process import MediaError, run_process

async def probe_duration(path, settings):
    output = await run_process('ffprobe', '-v', 'error', '-protocol_whitelist', 'file,pipe', '-show_streams',
                               '-show_format', '-of', 'json', path,
                               timeout=settings.process_timeout_seconds)
    data = json.loads(output)
    streams = [s for s in data.get('streams', []) if s.get('codec_type') == 'audio']
    if not streams:
        raise MediaError('This file has no audio track.')
    durations = [s.get('duration') for s in streams]
    durations.append(data.get('format', {}).get('duration'))
    durations = [float(d) for d in durations if d not in (None, 'N/A')]
    duration = max(durations, default=0)
    if not math.isfinite(duration) or duration <= 0:
        raise MediaError('Could not determine recording duration.')
    if duration > settings.max_duration_seconds:
        raise MediaError('Recording exceeds the configured duration limit.')
    return duration

async def prepare_audio_chunks(path, directory, settings):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    await run_process('ffmpeg', '-nostdin', '-v', 'error', '-y',
                      '-protocol_whitelist', 'file,pipe', '-i', path,
                      '-map', '0:a:0', '-vn', '-ac', '1',
                      '-ar', settings.audio_sample_rate, '-c:a', 'libmp3lame',
                      '-b:a', f'{settings.audio_bitrate_kbps}k',
                      '-f', 'segment', '-segment_time', settings.chunk_seconds,
                      '-reset_timestamps', '1', directory / '%06d.mp3',
                      timeout=settings.process_timeout_seconds)
    chunks = sorted(directory.glob('*.mp3'))
    if not chunks:
        raise MediaError('No audio could be extracted.')
    if any(p.stat().st_size > settings.upload_max_bytes for p in chunks):
        raise MediaError('Audio chunk exceeds upload limit; reduce MEDIA_CHUNK_SECONDS.')
    return chunks
