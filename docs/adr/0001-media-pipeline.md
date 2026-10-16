# ADR-0001: A bounded media pipeline with balance reservations

## Status

Accepted, 2026-10-06.

Queue execution, result delivery and restart recovery are updated by
[ADR-0003](0003-database-work-queues.md).

## Context

Telegram handlers currently pass original files directly to Whisper. Documents,
URLs, large inputs, long results, and concurrent balance consumption need one
consistent processing path. Limits must be checked before transcription.

## Decision

Keep Python, aiogram, SQLAlchemy and PostgreSQL. Telegram handlers call a media
job service; that service coordinates source adapters, FFmpeg preparation,
OpenAI transcription and Telegram result delivery. Source and audio modules
do not import handlers or persistence. Database transactions own job lifecycle
and balance reservations, locking the user before the task. Payment balance
updates use the same user lock. No new queue service is introduced.

Obtain source metadata for early rejection when possible, then probe the
downloaded recording. Reserve rounded-up actual seconds atomically before
any OpenAI call; commit consumption only with a completed transcript. Release
reservations on failure. Expired periods are refreshed only when there are no
active reservations. Recover interrupted jobs at startup by releasing their
reservations and marking them failed; do not replay paid API calls silently.

Support Telegram media/documents, public direct HTTP(S) files and individual
public YouTube recordings. No playlists, live streams, authenticated resources,
DRM, image OCR or arbitrary website scraping. Direct downloads reject private
network destinations, including redirect targets and resolved IP addresses.

Use FFmpeg to extract and segment compressed audio. Use yt-dlp for YouTube
metadata and audio downloads. Configure a local Telegram Bot API container via
an optional Docker profile for large Telegram files; retain cloud API operation
with an explicit 20 MB download limit. Deployment switching is a separate
operator step requiring Telegram API credentials and cloud logout.

All media tuning lives in `app/media/config.py`. Initial source limits are
2 GiB and six hours. API chunks are validated against the upload byte limit.
Short transcripts are messages; long transcripts are UTF-8 text documents.

## Consequences and validation

The bot remains one process with bounded concurrent processing. In-flight jobs
are recoverable but not automatically resumed. Temporary downloads are removed
after processing; pending payments re-download and revalidate their source.
Integration tests cover duration probing, extraction, splitting and ordering.
Lifecycle tests cover insufficient balance, reservations, retry, failure refund
and single consumption; PostgreSQL tests verify concurrent reservations.
Live YouTube and Telegram remain dependent on source access and credentials.
