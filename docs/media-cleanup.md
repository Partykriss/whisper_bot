# Durable asynchronous media cleanup

This supersedes the temporary-file retention description in [media.md](media.md).
See [ADR-0004](adr/0004-durable-media-cleanup.md) for the boundaries and rationale.

## Operation names and release points

| Operation | Implementation / contract |
| --- | --- |
| `lookup_transcript` | Existing `claim_job` transaction checks the user-scoped key before any download; completed results are delivered from the database. |
| `download_source` | Telegram, HTTP or YouTube adapter acquires the recording. |
| `copy_source_to_workspace` | Copies Telegram media into the private attempt directory, closes descriptors and verifies the copied size. |
| `delete_telegram_cached_source` | Releases the Telegram generation immediately after the copy; does not wait for unlink. |
| `prepare_audio_chunks` | FFmpeg produces and validates all audio segments. |
| `delete_workspace_source` | Releases the working source before the first OpenAI request. |
| `transcribe_audio_chunk` | Receives one chunk's transcription with its file descriptor scoped to the request. |
| `delete_audio_chunk` | Releases that chunk immediately after a successful response, before continuing to the next chunk. |
| `save_transcript` | Existing `finish` transaction stores text and consumption atomically. |
| `deliver_transcript` | Existing outbox delivery is independent of cleanup completion. |

All `delete_*` lifecycle operations persist a release/request; worker threads
perform the filesystem operation. Cleanup failures do not repeat OpenAI calls or
discard received text. Database registration is awaited; unlink is not. Idle
workers scan at `CLEANUP_SCAN_SECONDS`, so background deletion is not a zero-delay
or real-time guarantee. Unrelated Telegram downloads/deletions run concurrently.

HTTP/YouTube bypass the Telegram copy stage. YouTube downloads use `source.bin`
(FFmpeg detects the container from contents). Input audio follows the same rules
as input video. Temporary files now live in `MEDIA_DOWNLOADS_DIR/managed/<uuid>`;
older `job-*` directories are not adopted or deleted by this feature.

## Database contract

Alembic revision `d37e8f901abc` adds:

- `tasks.source_identity`: the original identity used for source coordination.
  Transcription cache keys remain unchanged and user-scoped. Older queued tasks
  fall back to their saved Telegram `file_id` or URL for coordination.
- `media_attempts`: task/lease ownership, source key, private directory and
  Telegram acquisition intent. An attempt directory is registered before mkdir
  and before any producer starts, covering partial and dynamically named outputs.
- `media_files`: scope, kind, path, generation/fingerprint, cleanup state, retry
  metadata, cleanup lease, error class and `deleted_at`.
- `media_file_uses`: references from attempts to file generations, with explicit
  release timestamps. A shared Telegram generation cannot be claimed while a
  reference is active.
- `media_cleanup_epoch`: installation cutoff for unregistered Telegram media.

File states are `creating`, `in_use`, `delete_pending`, `deleting`, `deleted`.
`workspace_directory` is an internal kind in addition to the three media kinds.
Directories are only removed when empty, never recursively erased by the worker.
The attempt's creation intent covers dynamic FFmpeg/yt-dlp outputs; individual
rows are inventoried after creation, on handled failure, or during recovery.

Completed files use a filesystem fingerprint (device, inode, size and mtime;
directories use device and inode), not a content hash. The worker confirms unlink,
absence, or replacement of the old generation before setting `deleted_at`.
A replacement at the same path is not deleted by an old cleanup operation.

Cleanup rows are claimed with `FOR UPDATE SKIP LOCKED`; a unique lease token
fences updates. The generation row remains locked during the short unlink and
completion transaction. Expired cleanup leases are recoverable. Errors retain
`delete_pending` with capped exponential retry and an error class, never raw
provider messages or token-bearing paths in logs. History is retained; automatic
database history pruning is outside this change.

## Concurrency, failure and recovery

A per-source PostgreSQL advisory lock serializes getFile/copy and deletion for the
same Telegram source across users. Other sources may proceed concurrently. A
shared flock on the mounted Telegram root excludes untracked-file reconciliation;
the reconciler/unattributed orphan deletion takes an exclusive, nonblocking guard.
These locks must be used by every application consuming this cache.

An OS directory lock protects a live workspace attempt even after its task lease
expires. Cancellation waits for non-cancellable file/OpenAI threads to close
their files before releasing that guard. FFmpeg cancellation terminates the
producer through the existing process adapter. Cleanup of explicitly released
individual files does not wait for the entire attempt to finish.

Handled failure, cancellation or insufficient balance releases remaining files.
After payment, processing downloads again. If cleanup registration fails, the
durable attempt and uses are recovered after the producer exits. Recovery waits
for the age guard, verifies the task no longer owns the attempt and obtains the
exclusive directory lock before inventorying partial files. This prevents an old
attempt from deleting a new attempt's files.
An attempt-specific advisory lock also fences the initial directory creation
against recovery; a paused creator rechecks ownership before creating files.

Telegram reconciliation only scans regular completed files in `videos`,
`video_notes`, `audios`, `voice`, `documents` and `animations` beneath bot-specific
directories. It excludes `temp`, bot-root databases/sessions and symlinks.
Unregistered files must have ctime after the migration cutoff, exceed the age
guard, and appear unchanged in two observations separated by that guard. Files
with pre-cutoff ctime remain untouched unless a new task explicitly acquires
them. Inventory of legacy cache and any manual deletion remain separate work.
If Telegram removes or replaces an observed orphan itself, the worker confirms
the old generation is absent or replaced and completes its registry record.

No cleanup can complete while the server, filesystem or database is unavailable.
Records persist and retry after recovery. A crash after an OpenAI response but
before the final transcript transaction can still repeat transcription on retry;
that existing at-least-once processing contract is unchanged.

## Processes, permissions and configuration

The main bot runs workspace cleanup threads as UID 10001 and retains its
read-only Telegram mount with supplementary group 101. `telegram-cleanup` uses
the same runtime image, runs `python -m app.cleanup` as UID/GID 101:101 and mounts
only the Telegram volume read/write. Its `cleanup.env` contains the database URL,
Telegram root and cleanup settings, not bot/OpenAI/application credentials.
No Docker socket or public cleanup endpoint is added.

All algorithm tuning is in `app/cleanup/config.py`, with `CLEANUP_` overrides:

| Setting suffix | Default | Unit / meaning |
| --- | --- | --- |
| `WORKERS` | 2 | Concurrent deletion threads per scope/process |
| `SCAN_SECONDS` | 1 | Idle queue scan and busy-source retry interval |
| `RECONCILE_SECONDS` | 60 | Recovery/inventory interval |
| `LEASE_SECONDS` | 120 | Cleanup claim duration |
| `RETRY_SECONDS` | 10 | Initial error retry delay |
| `RETRY_MAX_SECONDS` | 3600 | Maximum error retry delay |
| `ORPHAN_GUARD_SECONDS` | 300 | Minimum age/stability window for recovery |

Container resource settings remain in `deploy/config.json`. Both application and
cleanup service receive `CLEANUP_` overrides from validated production secrets.

## Deployment

Do not start this application version against the old schema. Update the
root-installed Compose, deployment config, `deployment.py` and `secret_config.py`
from the reviewed commit using the existing [host installation steps](deployment.md#install-host-files-vik).
The installed deployment code must create `cleanup.env` and stop both bot and
cleanup worker before backup/migration. It starts the worker after migration,
checks that it stays running and stops it with the bot if startup verification
fails. Existing runtime directories lack `cleanup.env`; deploy a new release
rather than manually starting the new service against an old directory.

Keep the previous host files for coordinated rollback. Stop both processes before
any schema downgrade. Downgrade drops cleanup history, so prefer a forward fix
and drain pending cleanup first. A master push does not update root-owned host
files automatically. The normal bot startup probe now also checks cleanup schema.

For development, use `compose.dev.yaml --profile large-media` with a database URL
reachable from both containers; apply migrations before starting bot/cleanup.
The local Telegram image is pinned to the production digest to preserve UID 101.

## Operations and verification

Safe summary queries (do not publish stored paths, which can include bot tokens):

```sql
SELECT scope, kind, state, count(*) FROM media_files GROUP BY scope, kind, state;
SELECT id, kind, attempts, last_error, available_at
FROM media_files WHERE state = 'delete_pending' AND last_error IS NOT NULL;
```

Validation covers PostgreSQL claims, shared source references, expired owners,
slow unlink without blocking transcript completion, partial-output recovery,
generation replacement, cancellation, symlinks and protected Telegram paths.
All 92 tests passed, including PostgreSQL integration and FFmpeg checks. Migration
upgrade and `alembic check` also passed in an isolated local PostgreSQL/container
environment. The UID smoke check
uses disposable media with the observed 750/640 permissions, reads as UID 10001
with group 101 and deletes as UID 101, checking persisted completion and preservation
of a service file. No real provider credentials or production data are used.

After deployment, a live test must still confirm local Telegram redownload after
deletion and end-to-end large-video transcription. Confirm both source copies
and all chunks disappear and their registry rows reach `deleted`. Send the same
file again to verify cached transcript delivery without downloading or charging.
