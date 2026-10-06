# Base installation

This describes the imported baseline. See [media processing](media.md) for the
current functionality, new migration and deployment instructions.

The project was imported from https://github.com/Partykriss/whisper_bot,
branch `master`, commit `b26554eb5f857dd6c7aaecdd4464a9ce6210a083`.
The upstream Git history and `origin` remote are preserved. Application behavior
and database contracts have not been changed.

## Local setup

Python 3.12 is used. Dependencies are installed in `.venv` from the upstream
`requirements.txt`, which does not pin package versions.
The local `.env` is ignored by Git. Fill in `BOT_TOKEN` and `OPENAI_API_KEY`
before running the bot. Never commit credentials.

Start only PostgreSQL first, then apply migrations and start the local bot:

```bash
docker compose up -d db
# Wait until PostgreSQL is ready before applying migrations.
.venv/bin/alembic upgrade head
.venv/bin/python main.py
```

The local database URL uses `localhost:5001`. For a containerized bot, change
the URL in `.env` to
`postgresql+psycopg2://whisper:whisperpass@db:5432/whisper_db`, then run:

```bash
docker compose up -d db
# Wait until PostgreSQL is ready.
docker compose run --rm bot alembic upgrade head
docker compose up -d bot
```

Run one bot instance per token. Docker installs FFmpeg in the bot image;
the current transcription code sends files directly to OpenAI and does not
perform FFmpeg conversion. The Compose database credentials are development
defaults and must be changed before production deployment.

## Imported baseline behavior and limits

- Telegram voice, audio, video, and video-note messages are accepted.
- Transcription uses OpenAI `whisper-1`; results are cached in PostgreSQL.
- Trial, Plus, and Pro use a balance in seconds and an expiration date.
- Plus and Pro are purchased using one-time Telegram Stars invoices.
  Recurring subscription renewal is not implemented.
- Successful payment can resume the oldest task waiting for payment.
- URL ingestion, media-document handlers, large-file conversion/chunking,
  and long-result delivery are not implemented.
- Payment recording and balance updates use separate transactions; reliable
  retry handling requires further work.
- Application startup does not apply migrations automatically.

## Verification

Installation checks passed:

- All 8 existing unit tests passed with `DATABASE_URL=sqlite://`.
- `pip check` found no broken requirements.
- Python compilation passed for `main.py`, `app`, `migrations`, and `tests`.
- `docker compose config --quiet` passed.
- `alembic heads` reported one head: `6a8f0c3d9b12`.
- The diff passed whitespace checks; `.env` and `.venv` are Git-ignored.

The existing tests emit deprecation warnings for `datetime.utcnow()`.
Installed direct dependency versions: OpenAI 3.24.0, python-dotenv 1.2.4,
SQLAlchemy 2.1.3, psycopg2-binary 2.9.13, Alembic 1.20.0, aiogram 3.31.0.

Tests can be run without a live database using a temporary SQLite engine URL:

```bash
DATABASE_URL=sqlite:// .venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m pip check
```

SQLite here only allows imports for the existing unit tests; it is not a
replacement for PostgreSQL or a validation of PostgreSQL migrations.
Live Telegram, OpenAI calls, Stars payments, and database migrations need a
separate integration check after credentials and PostgreSQL are available.
