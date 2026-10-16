# Whisper Telegram Bot

See [production deployment](docs/deployment.md), [installation](docs/setup.md), [media processing and deployment](docs/media.md),
and [architecture decisions](docs/adr/README.md).

Telegram bot that transcribes voice/audio/video messages, media documents, direct media links and public YouTube videos with OpenAI Whisper. Results and balance reservations are stored in Postgres.

## Features
- Voice, Audio, Video, Video Note and media document support
- Direct HTTP(S) media links and public YouTube audio downloads
- FFmpeg audio extraction and chunking for large recordings
- Optional local Telegram Bot API for files larger than 20 MB
- Atomic duration reservations before transcription; long results as text files
- OpenAI Whisper transcription (`whisper-1`)
- Postgres cache + Alembic migrations
- Telegram Stars payments (Plus/Pro)
- Balance in seconds with monthly period (`paid_until`)
- Auto-resume pending transcription after successful payment

## Tech Stack
- Python, aiogram
- OpenAI Python SDK
- SQLAlchemy + Alembic
- PostgreSQL (docker-compose)

## Quick Start

### 1) Create and activate venv
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2) Start database
```bash
docker compose -f compose.dev.yaml up -d db
```

### 3) Create `.env`
```
BOT_TOKEN=your_bot_token
OPENAI_API_KEY=your_openai_api_key
DATABASE_URL=postgresql+psycopg2://whisper:whisperpass@localhost:5001/whisper_db

# Limits (minutes)
LIMIT_TRIAL_MIN=3
LIMIT_PLUS_MIN=100
LIMIT_PRO_MIN=2000

# Telegram Stars prices (XTR)
PRICE_PLUS_STARS=99
PRICE_PRO_STARS=1499
```

### 4) Apply migrations
```bash
alembic upgrade head
```

### 5) Run the bot
```bash
python main.py
```

## How it works
1. Bot receives a Telegram file or media link and checks its user-scoped cache.
2. Known source limits are checked before downloading; actual duration is probed afterward.
3. Balance is atomically reserved before OpenAI calls. Insufficient balance creates a `pending_payment` task.
4. FFmpeg extracts and splits audio; OpenAI transcribes ordered chunks.
5. Completion stores the result and consumes reserved time once; failure releases the reservation.
6. Successful payment revalidates and resumes the oldest pending transcription.

## Project Structure
- `main.py` — handlers and orchestration
- `app/media/` — source adapters, FFmpeg preparation, processing configuration
- `app/services/`
  - `billing.py` — periods, limits, warnings
  - `tasks.py` — task persistence
  - `transcription.py` — OpenAI adapter
  - `media_jobs.py` — media processing orchestration
  - `media_store.py` — job lifecycle and atomic reservations
  - `users.py` — user/balance logic
- `app/ui/messages.py` — formatting and warnings
- `app/models.py` — SQLAlchemy models
- `migrations/` — Alembic migrations
- `compose.dev.yaml` — PostgreSQL

## Tests
```bash
python -m unittest discover -s tests
```

## Notes
- Files in `downloads/` are temporary and removed after processing.
- Internet access is required for Telegram and OpenAI APIs.

## License
Not specified.

Database-driven processing and notification delivery: [queue architecture and operations](docs/database-queues.md).
