# ADR-0004: Durable asynchronous media cleanup

Status: Accepted for implementation

## Context

Working sources/chunks previously lived until the entire job ended; Telegram
cached sources were never explicitly deleted. Task leases alone cannot fence a
still-running FFmpeg process or OpenAI thread from filesystem deletion.

## Decision

Use the existing PostgreSQL database for file generations, attempt ownership and
cleanup leases. Register an attempt directory before any producer starts; this
directory is also the durable creation intent for dynamically named outputs.
Register each completed output before releasing it. Record Telegram acquisition
intent before getFile and the returned file generation immediately afterwards.

Release Telegram cache usage after a verified, closed workspace copy; release
the workspace source after all audio chunks pass validation; release each chunk
after its transcription response. Persist cleanup requests but never wait for
filesystem deletion before progressing or delivering the transcript.

Workspace cleanup runs in the bot as UID 10001. A separate worker using the same
application image runs as UID 101 and mounts only Telegram storage read/write.
The bot's Telegram mount stays read-only. Both workers use one cleanup repository
and bounded executors, without a Docker socket or new public network interface.

PostgreSQL per-source advisory locks coordinate Telegram acquisition and deletion
across users; unrelated downloads and deletions remain concurrent. A shared OS
guard on the Telegram root permits those operations concurrently but excludes
orphan reconciliation, which needs a complete view with no acquisition in flight.
Unattributed orphan deletion takes the exclusive guard too. Locks and file
generations are independent of user-scoped transcription cache keys.

An OS lock on each attempt directory protects still-running producers/readers
even if their task lease expires. Cancellation waits for non-cancellable file
and OpenAI threads to release files. Cleanup of released individual files is
independent of that directory lock. Orphan reconciliation takes it exclusively.
Unlink uses validated directory-relative paths, refuses symlinks and compares
file generations. Missing/replaced old generations are idempotent completion.

Reconciliation inventories registered attempt directories and only completed
Telegram media in explicitly permitted directories. A migration-time cutoff,
stability observation and age guard exclude pre-existing unregistered Telegram
cache and in-flight files. Never traverse Telegram temp, databases or sessions.

## Consequences

Cleanup errors are retried independently and remain observable in PostgreSQL;
they cannot erase transcripts, charge users or repeat OpenAI calls. Recovery
requires a working database and filesystem, not an instantaneous deletion SLA.
Existing at-least-once transcription semantics remain unchanged. Updating
root-owned Compose/deployment files and migrating before startup is required.

## Validation

Exercise real PostgreSQL claims/fencing/recovery, nonblocking pipeline release,
generation replacement, symlink protection, cancellation, and pinned-image UID
permissions. Live Telegram redownload and end-to-end transcription require a
separate production smoke check after explicit deployment authorization.
