# Media transcription

## Inputs and results

Send voice messages, audio, video, video notes, or audio/video documents.
Send one public HTTP(S) media URL or an individual YouTube video URL in a text
message. YouTube watch, short links and Shorts are normalized to the full video;
start-time URL parameters do not trim the recording. Only the first URL in a
message is processed. YouTube audio-only streams are downloaded through yt-dlp.
Public websites other than YouTube must expose a direct media file URL.
Playlists, live streams, private resources, DRM, pictures, archives, and OCR are
outside this feature. Documents without an audio track produce an explanation.

The bot shows processing status and chunk progress. A short result is sent as
plain text; a long result is sent as UTF-8 `transcript.txt`. Empty recognition
returns a no-speech message. Failed delivery does not erase a completed result:
sending the same source again returns its cached transcript without charging
again. New cache keys are user-scoped; old cached rows are retained but are not
used for new requests. Existing pending Telegram tasks can still resume.

## Limits and balance

1. Validate known source size/duration. For YouTube obtain metadata and check
   available balance before downloading audio.
2. Download within the configured byte limit and probe actual duration.
3. Atomically reserve `ceil(duration_seconds)` under a PostgreSQL user-row lock.
   Available balance is total seconds minus active reservations. Expired plans
   refresh to Trial before admission; existing reserved work can finish, but
   expired periods cannot admit new work until refreshed or renewed.
4. Extract the first audio track as mono MP3 and split into ordered chunks.
5. Validate every chunk against the upload limit before any OpenAI request.
6. Transcribe chunks in order and store the complete result and consumption in
   one transaction. On failure release the reservation without charging the user.

Admin users bypass balance and period restrictions, but still have source size,
recording duration and resource limits. Cache hits do not consume time.
Pending-payment jobs retain the source reference, not the downloaded file. After
payment the source is downloaded again and actual duration is revalidated.
Parallel reservations cannot spend the same available seconds. Payment balance
updates use the same user-row lock. The existing payment receipt/application
flow is otherwise unchanged, including its separate transactions.

## Configuration

Media defaults are defined only in `app/media/config.py`. Override settings with
`MEDIA_` plus the uppercase field name in `.env`:

| Setting | Default | Meaning |
| --- | --- | --- |
| `MEDIA_MAX_SOURCE_BYTES` | 2147483648 | Maximum source size (2 GiB) |
| `MEDIA_MAX_DURATION_SECONDS` | 21600 | Maximum recording duration (6 hours) |
| `MEDIA_CHUNK_SECONDS` | 600 | Target audio chunk duration |
| `MEDIA_AUDIO_BITRATE_KBPS` | 64 | Mono MP3 bitrate |
| `MEDIA_AUDIO_SAMPLE_RATE` | 16000 | Audio sample rate in Hz |
| `MEDIA_UPLOAD_MAX_BYTES` | 25000000 | Maximum bytes per transcription request |
| `MEDIA_CONCURRENT_JOBS` | 2 | Concurrent download/processing jobs |
| `MEDIA_PROCESS_TIMEOUT_SECONDS` | 3600 | Timeout per FFmpeg/ffprobe process |
| `MEDIA_DOWNLOAD_TIMEOUT_SECONDS` | 3600 | Timeout per download/metadata operation |
| `MEDIA_MESSAGE_MAX_UNITS` | 4000 | Plain-message threshold in UTF-16 units |
| `MEDIA_DOWNLOAD_BLOCK_BYTES` | 262144 | HTTP streaming buffer |
| `MEDIA_MAX_REDIRECTS` | 5 | Maximum direct-link redirects |
| `MEDIA_DOWNLOADS_DIR` | downloads | Temporary processing directory |
| `MEDIA_TRANSCRIPTION_MODEL` | whisper-1 | Transcription model |
| `MEDIA_TELEGRAM_API_BASE` | empty | Cloud API, or local Bot API base URL |
| `MEDIA_TELEGRAM_LOCAL_ROOT` | /var/lib/telegram-bot-api | Shared local API storage |

Direct downloads reject private IP destinations and validate DNS results and
redirect destinations. FFmpeg/ffprobe cannot load network streams from input
files. Temporary job directories are deleted after success or handled failure.
Only completed transcripts are cached; partial API output is not persisted.
A failure after some API calls can cost the operator money even when the user's
reservation is released. Chunk boundaries can fall inside speech; recognition
quality near boundaries requires listening-based verification on real recordings.

## Deployment

Rebuild the bot, apply the additive migration, then restart one bot instance:

```bash
docker compose build bot
docker compose stop bot
docker compose run --rm bot alembic upgrade head
docker compose up -d bot
```

The new revision is `a10e6b2c4d90`. It adds a source URL, progress, and reservation
columns with zero defaults, preserving existing users, balances and tasks.
Run one bot instance. On startup interrupted loading/processing jobs are marked
failed and reservations are released. Resubmit a source to retry; processing is
not silently replayed after a restart. An abrupt kill may leave temporary files
in `downloads`; remove abandoned job directories only while the bot is stopped.

### Large Telegram files

The cloud Bot API can download only up to 20 MB. Large files require the optional
`telegram-api` service and a shared volume. The Docker image includes FFmpeg,
Deno and yt-dlp's JavaScript support for YouTube extraction.

Obtain Telegram application credentials at https://my.telegram.org/apps and add
these private settings to `.env` (do not commit them):

```dotenv
TELEGRAM_API_ID=your_application_id
TELEGRAM_API_HASH=your_application_hash
MEDIA_TELEGRAM_API_BASE=http://telegram-api:8081
```

Before switching a running cloud bot, stop it and log out of the cloud API.
The following command reads the token from the environment without displaying it:

```bash
docker compose stop bot
docker compose run --rm -e MEDIA_TELEGRAM_API_BASE= bot python -c '
import asyncio, os
from aiogram import Bot
async def logout():
    async with Bot(os.environ["BOT_TOKEN"]) as bot:
        await bot.log_out()
asyncio.run(logout())
'
docker compose --profile large-media up -d telegram-api
docker compose up -d bot
```

The Bot API port is internal to Docker, and storage is mounted read-only in the
bot. The local API's own cache persists in `telegram_api_data`; monitor disk
usage. Switching back to the cloud immediately after logout is restricted by
Telegram; consult https://core.telegram.org/bots/api#logout.

## Verification

Run the suite inside the rebuilt image so FFmpeg integration tests execute:

```bash
docker compose run --rm -e DATABASE_URL=sqlite:// bot python -m unittest discover -s tests -v
```

The PostgreSQL concurrency test needs `TEST_POSTGRES_URL` pointing to a test-capable
PostgreSQL server. It creates and removes an isolated random schema; application
users/tables are untouched. Tests verify rejection before OpenAI, failure refund,
retry, cache delivery, ordered chunks, long UTF-8 output, private-network rejection,
video extraction, no-audio rejection, and inputs larger than the API upload limit.

Live Telegram downloads, local Bot API operation, YouTube availability and speech
recognition quality require separate real-source checks. YouTube can restrict
server IPs or require authentication; this version does not load user cookies.

### Installation verification (2026-10-06)

- 35 tests passed in Docker, including FFmpeg integration and real PostgreSQL
  concurrent reservations. Two local-API adapter scenarios verify cloud rejection
  before download and copying a file larger than 20 MB from shared storage.
- A public YouTube recording from yt-dlp's documentation was downloaded as
  audio only: metadata duration 597 seconds, probed duration 596.501 seconds,
  audio size 9,730,538 bytes. No OpenAI call was made by this smoke check.
- Migration `a10e6b2c4d90` was applied to the existing PostgreSQL database.
  The existing administrator remained on `admin` with no active reservation.
- The updated Docker bot started polling successfully.
- Local Bot API deployment is pending `TELEGRAM_API_ID`/`TELEGRAM_API_HASH`.
  Large Telegram downloads cannot operate through the cloud API meanwhile.
- Live speech recognition, payment events and end-to-end local Bot API downloads
  have not been exercised during implementation.
