# Whisper Telegram Bot

See [local installation and verification](docs/setup.md) for this workspace setup.

Telegram bot that accepts voice/audio/video messages, transcribes them with OpenAI Whisper, and stores results in Postgres. Cached results are returned by `file_unique_id`.

## Features
- Voice, Audio, Video, and Video Note support
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
docker compose up -d
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
1. Bot receives media and checks cache by `file_unique_id`.
2. If cache hit, returns transcription.
3. Otherwise checks balance (seconds). If insufficient, stores task as `pending_payment` and offers payment.
4. After payment, pending transcription is processed automatically.
5. Balance is reduced by media duration.

## Project Structure
- `main.py` — handlers and orchestration
- `app/services/`
  - `billing.py` — periods, limits, warnings
  - `tasks.py` — task persistence
  - `transcription.py` — transcription pipeline
  - `users.py` — user/balance logic
- `app/ui/messages.py` — formatting and warnings
- `app/models.py` — SQLAlchemy models
- `migrations/` — Alembic migrations
- `docker-compose.yml` — PostgreSQL

## Tests
```bash
python -m unittest discover -s tests
```

## Notes
- Files in `downloads/` are temporary and removed after processing.
- Internet access is required for Telegram and OpenAI APIs.

## License
Not specified.
