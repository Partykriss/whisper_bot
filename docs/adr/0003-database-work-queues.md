# ADR-0003: PostgreSQL work queues and durable notifications

## Status

Accepted, 2026-10-16. Supersedes the direct execution, direct result delivery and
startup failure recovery portions of ADR-0001 and ADR-0002.

## Context

Handlers currently execute media work directly. Payments and balance updates
use separate transactions. Restarted jobs fail, and Telegram delivery has no
durable retry state.

## Decision

PostgreSQL owns queued tasks, an event outbox and per-recipient deliveries.
Triggers capture user registration, payment insertion and task state changes.
An outbox insert triggers NOTIFY on `whisper_events`; its payload is an event ID.
LISTEN wakes workers, while startup and periodic scans recover missed signals.
Handlers persist work; media workers never deliver results. Notification workers
read committed transcripts and send messages/documents, including cached results.

Python retains business logic. Repositories own transactions and locks; runtime
orchestrates independent media and notification workers. Existing SQLAlchemy and
psycopg2 are retained. Workers initially run in the polling process. Only one
Telegram poller is supported, but database claims are safe across workers.

Tasks use expiring leases and ownership tokens, with heartbeat renewal. Expired
work releases reservations and is retried within configured attempt limits.
All task mutations lock user before task and reject stale ownership. Payment
insertion, credit and requeue are one idempotent transaction. Event fanout and
its processed marker commit together. Deliveries have their own leases/retries.

## Consequences

Database transitions and accounting are atomic. External OpenAI/Telegram calls
are at least once: a crash after external success but before database commit can
repeat a request or message. No exactly-once claim is made for external effects.
A synchronous OpenAI call already running in a thread cannot be forcibly stopped
on lease loss; stale workers cannot commit accounting or results.
Progress is coalesced against current state; terminal outcomes remain durable.
Failed deliveries remain inspectable after exhausting retries. Existing history
is not broadcast by migration. Protocol callbacks remain immediate.

See [database queues](../database-queues.md) for operation and validation.
