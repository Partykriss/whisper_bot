# ADR-0002: Immutable images and a restricted rootless deployment account

## Status

Accepted, 2026-10-11.

Queue execution, result delivery and restart recovery are updated by
[ADR-0003](0003-database-work-queues.md).

## Context

Whisper shares a home Ubuntu server with Symdex. A system-wide Docker socket
would grant the deployment account control of the other project's containers
and root-equivalent host access. The Telegram polling bot requires one instance
and currently marks interrupted processing as failed on restart.

## Decision

GitHub-hosted CI tests PRs and master commits, builds a non-root Python image,
and passes the tested image as an artifact to a separate GHCR publication job.
Only successful master releases can invoke the home runner, after an explicit
repository deployment enable switch and production environment policy.

`whisper-runner` owns the runner only. Its sudo permission invokes one root-owned
entry point as `whisper`. That account owns a separate rootless Docker daemon,
Git read key, GHCR read credentials and the age decryption key. No account is
added to the system Docker group. The entry point uses a clean environment,
validates SHA/digest arguments, rejects stale master releases, checks the image
revision label, and reads only the encrypted env from Git. Root-owned Compose
and deployment code are installed manually outside the checkout.

SOPS ciphertext travels in Git; plaintext is parsed as data, never sourced.
Runtime env files live in private per-release directories and use Compose raw
env-file parsing. The existing application environment contract is preserved.
Database initialization credentials are derived from DATABASE_URL. Credential
rotation requires a separate operation on an existing database.

The deploy sequence is preflight/pull/decrypt, ready DB, stop bot, backup,
migrate using the release image, start one bot, observe and probe DB/Telegram.
Deployment is serialized in Actions and with a host lock. Migration failure
leaves the bot stopped; no automatic database or image rollback is performed.

## Consequences

Rootless Docker isolates daemon access from Symdex, but CPU, disk and kernel are
still shared. Whoever can publish deployed code can use the application's
secrets. Production environment/branch controls and trusted workflow changes
remain necessary; runner labels alone are routing, not an authorization boundary.
Host configuration updates are deliberate administrator steps. Backup retention,
off-host backups and recovery remain operator responsibilities. There is a
short deployment outage and active media processing can be interrupted.

Tests cover argument validation, stale releases, literal secret handling,
failure ordering, credential rotation, lock contention and runtime file modes.
See [deployment](../deployment.md) for provisioning and validation evidence.
