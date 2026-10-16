# Database work queues

PostgreSQL is the source of truth for media work, domain events and Telegram
notification delivery. Apply Alembic revision `b24e7a90c123` before starting the
new bot. Stop the previous bot before migrating. No production migration is
performed by the development workflow.

## Flow and module boundaries

- `main.py` accepts Telegram input and persists tasks or payments.
- `app/services/media_store.py` owns media lifecycle transactions and accounting.
- `app/services/task_queue.py` claims tasks, renews leases and recovers expired work.
- `app/services/media_jobs.py` downloads/prepares audio and calls the OpenAI adapter.
  It saves progress/results and never sends a transcript or status notification.
- Database triggers capture new users, new payments and task status/progress changes
  into `events`. Each event insert calls `pg_notify('whisper_events', event_id)`.
- `app/events/listener.py` holds a dedicated asynchronous psycopg2 connection.
  LISTEN commits before the initial scan. It reconnects after connection loss.
- `app/events/store.py` atomically expands events into `notification_deliveries`.
  Administrators are users with `plan = admin` at fanout time. The recipient set
  is fixed once fanout commits. No administrators means no recipient deliveries.
- `app/events/delivery.py` sends messages/documents/invoices from committed data.
- `app/events/runtime.py` starts and stops listener, fanout, media and delivery
  workers. Workers also scan periodically, so missed NOTIFY signals lose no work.

Dependencies run from handlers/runtime to services and adapters to persistence.
Triggers record facts; billing, routing and delivery policy remain in Python.
There is one Telegram polling process. Multiple database workers can safely claim
work, but running multiple pollers for one bot token is unsupported.

## State and delivery contracts

Tasks move from `queued` to `loading`, then `processing`, then `completed`.
Insufficient balance moves a task to `pending_payment`; payment requeues it.
Failures move a task to `failed`. Source, target chat and size are committed
before a worker can claim the task. Retrieving a completed cache entry creates a
`cached_result` event addressed to the requesting chat, without charging again.

Completion stores transcript, consumes the reservation and changes task status
in one transaction; the trigger inserts the completion event in that same
transaction. The notification worker reads the transcript from `tasks` and sends
text or a UTF-8 document using the existing media message-size setting. Transcript
contents are never placed in a NOTIFY payload. Delivery failures do not affect
completed tasks or balances.

A task lease has a unique owner token and expiration. Heartbeats renew it while
work runs. Stale workers cannot modify lifecycle/accounting. Expired work releases
its reservation and becomes queued again after the retry delay. Exhausting the
attempt limit produces a durable failure notification. Ordinary media errors
fail immediately; automatic retries target interrupted leased work. Transient
Telegram failures retry independently; permanent Telegram errors or exhausted
attempts retain a `failed` delivery with an error class for inspection.

Deliveries are serialized per chat; one recipient's error does not block other
recipients. Old progress is skipped against current task status, generation and
percentage. A stored Telegram message ID allows current status updates to edit
one message. Completion delivery also attempts a cosmetic status-message update;
a failed edit never causes an already-delivered transcript to be resent.

Payment charge IDs are unique. Credit, payment insertion, confirmation event and
requeue of pending tasks commit together, under the user lock. Duplicate payment
updates do nothing. A balance recheck when entering `pending_payment` closes the
race with a payment that arrived during the preceding failed balance check.

Direct command responses (`/start`, `/payment`) and Telegram protocol callbacks
remain immediate. Registration/payment notices, media status, insufficient-balance
invoices, errors, new transcripts and cached transcript delivery use the database.

## Configuration

All queue tuning lives in `app/events/config.py`, overridable with `QUEUE_` plus
the uppercase field name: `SCAN_SECONDS`, `RECONNECT_SECONDS`, `LEASE_SECONDS`,
`HEARTBEAT_SECONDS`, `RETRY_SECONDS`, `TASK_ATTEMPTS`, `DELIVERY_ATTEMPTS` and
`DELIVERY_WORKERS`. Heartbeat must be shorter than lease. Media concurrency stays
in the existing `MEDIA_CONCURRENT_JOBS` setting; no second media concurrency
limit is introduced.

## Operations and limitations

Inspect `events.processed_at` for fanout backlog, `tasks.status/lease_until/attempts`
for work backlog and `notification_deliveries.status/last_error/attempts` for
failed deliveries. Events and delivery history are retained; no automatic pruning
is introduced. A failed delivery can be deliberately retried by resetting its
status to `pending`, attempts to zero and `available_at` to the current UTC time.
The periodic scan will pick it up. Avoid retrying a delivery known to have reached
Telegram unless duplicate delivery is acceptable.

External effects are at least once. A process crash after Telegram accepts a
message but before the sent marker commits can duplicate the message. A partially
sent pair of invoices may be repeated. A crash after OpenAI succeeds but before
transcript commit can repeat the paid OpenAI call. Cancellation cannot forcibly
stop an already-running synchronous OpenAI call in its thread. Ownership fencing
prevents that old attempt from committing or double-charging the user's balance.

Migration captures only future events. Existing completed transcripts/payments
are not broadcast. Existing loading/processing tasks receive expired leases and
are recovered by the new runtime. Deployments must allow migration before startup.
Downgrading removes queue history; drain deliveries and stop workers first.

## Validation

PostgreSQL integration tests cover rollback versus commit notification, listener
reconnect, exclusive claims, stale ownership, reservation recovery, payment
idempotency and rollback, the payment/waiting race, progress coalescing, recipient
isolation, retry exhaustion, text/document result delivery and cached replay.
Runtime tests cover heartbeat and cancellation on lease loss. Existing accounting
and media tests remain in place with the updated queue contracts.

Run with an isolated database URL in both `DATABASE_URL` and `TEST_POSTGRES_URL`:

```sh
python -m unittest discover -s tests -v
alembic upgrade head
alembic check
```

Live Telegram/OpenAI calls are mocked in tests. FFmpeg integration tests require
FFmpeg/ffprobe locally or the runtime image.

Local verification on 2026-10-16 used a disposable PostgreSQL 18 container:
65 regression tests ran successfully (62 passed, three FFmpeg tests skipped because
local FFmpeg/ffprobe were unavailable). The final 12-test PostgreSQL queue suite,
including the additional full runtime flow, and both heartbeat lifecycle tests
passed. Alembic upgrade, downgrade/re-upgrade and model comparison succeeded.
No production database or external Telegram/OpenAI service was modified.
