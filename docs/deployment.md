# Production deployment on serv

The media-cleanup release requires coordinated host-file updates and migration
`d37e8f901abc`; see [cleanup deployment and verification](media-cleanup.md#deployment).
It adds a `telegram-cleanup` service using the same app image as UID 101 and a
dedicated `cleanup.env` without provider credentials. Update the installed
Compose, config, deployment module and secret-config module together before
deploying. A push alone does not install these root-owned host files.

This replaces `deploy/deploy.sh` (rsync/scp, system Docker, start-before-migrate).
The administrator runs all server commands manually. Application behavior and
database schema are unchanged. Local development uses `compose.dev.yaml`;
production uses the root-installed `compose.yaml`.

The development file retains the existing PostgreSQL volume and container names.
Its database port is now loopback-only; container downloads use a named volume
so the non-root runtime can write them. Existing host `downloads/` files are not
deleted or migrated (they are temporary processing files).

## Delivery contract

- PRs targeting master: GitHub-hosted tests, PostgreSQL integration, migrations,
  Compose validation with dummy secrets and a non-root Docker build.
- Master push or manual master workflow: publish the exact tested image to
  `ghcr.io/partykriss/whisper_bot:sha-<commit>`, then deploy by digest.
- Deployment is skipped until repository variable `DEPLOY_ENABLED` is `true`.
- Production deployment runs on labels `self-hosted, linux, x64, whisper` and
  environment `production`. It has no checkout and no GitHub token permissions.
- The host accepts only the current master commit and a matching image revision.
  A superseded queued run fails safely; deploy the latest successful build.
- Concurrency cancels no active deployment. A nonblocking host lock rejects
  overlapping manual calls. Pushes/PRs must not execute arbitrary jobs on the
  home runner: protect master and workflow changes and restrict the production
  environment to master. Labels do not enforce this policy.

## Host layout and prerequisites

| Path / account | Owner and purpose |
|---|---|
| `/home/whisper-runner/actions-runner` | whisper-runner; systemd runner service |
| `/usr/local/bin/whisper-deploy` | root; only allowed sudo entry point |
| `/usr/local/lib/whisper/` | root; deployment Python modules |
| `/etc/whisper/` | root; Compose and deployment config |
| `/home/whisper/deployment/` | whisper, 0700; Git mirror, releases, backups, lock |
| `/home/whisper/.config/sops/age/keys.txt` | whisper, 0600; age private key |
| `/run/user/1003/docker.sock` | whisper; rootless daemon (UID derived at runtime) |

Required: Ubuntu 24.04, Python 3 with `python3-dotenv`, Git, SOPS 3.13.3,
Docker rootless and Compose >=2.30 (raw env_file support), age key and pinned
GitHub SSH host key. Rootless Docker is already provisioned under whisper with
linger. Keep the system Docker/Symdex services running. Do not add either
Whisper account to the system `docker` group.

`deploy/config.json` is the single deployment tuning/configuration source.
The installed root-owned copy controls CPU/memory, log rotation, timeouts,
startup observation and trusted paths. Verify `docker info` reports cgroup v2,
systemd and rootless; CPU/memory limits need delegated controllers. Disk I/O
limits are not requested (the current rootless installation warns io.max is
unavailable). Configure host-level disk monitoring and backup retention.

## Prepare the source on the server

After the local implementation commit has been pushed, enter whisper's shell
from the administrator account vik:

```bash
sudo -iu whisper
```

If the checkout does not exist:

```bash
git -c core.sshCommand='ssh -i /home/whisper/.ssh/whisper_git -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes' \
  clone git@github.com:Partykriss/whisper_bot.git /home/whisper/whisper_bot
```

In that checkout, configure the key and update:

```bash
cd /home/whisper/whisper_bot &&
git config core.sshCommand 'ssh -i /home/whisper/.ssh/whisper_git -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes' &&
git pull --ff-only origin master
```

Verify the commit against the reviewed local commit. Deployment scripts are not
automatically installed from Git on every release.

## Install host files (vik)

Exit the whisper shell, then execute as vik. Absolute paths avoid running
installation commands from the wrong directory after a failed cd.

```bash
(
  set -e
  sudo apt-get update
  sudo apt-get install -y python3-dotenv
  sudo install -d -o root -g root -m 0755 /etc/whisper /usr/local/lib/whisper
  sudo install -o root -g root -m 0644 /home/whisper/whisper_bot/compose.yaml /etc/whisper/compose.yaml
  sudo install -o root -g root -m 0644 /home/whisper/whisper_bot/deploy/config.json /etc/whisper/config.json
  sudo install -o root -g root -m 0644 /home/whisper/whisper_bot/deploy/deployment.py /usr/local/lib/whisper/deployment.py
  sudo install -o root -g root -m 0644 /home/whisper/whisper_bot/deploy/secret_config.py /usr/local/lib/whisper/secret_config.py
  sudo install -o root -g root -m 0755 /home/whisper/whisper_bot/deploy/whisper-deploy /usr/local/bin/whisper-deploy
)
```

Edit the dedicated sudoers file using `sudo visudo -f /etc/sudoers.d/whisper-deploy`:

```sudoers
whisper-runner ALL=(whisper) NOPASSWD: /usr/local/bin/whisper-deploy *
```

Then run `sudo visudo -c`. The wildcard passes arguments to a fixed wrapper;
the Python entry point accepts exactly a 40-hex SHA and a sha256 digest.
The wrapper clears the environment and uses isolated Python. Do not grant a
shell, arbitrary docker commands, or write access to installed files/parents.
Even invalid args must fail before Docker or Git is touched:

```bash
sudo -u whisper-runner sudo -n -u whisper /usr/local/bin/whisper-deploy invalid invalid
```

Expected: exit 1, `Expected a full lowercase Git commit SHA`.

## GHCR read access (whisper)

Create a classic PAT with `read:packages` on an account authorized to read the
private package, without unnecessary additional scopes. Package access must be
linked to the repository. Git's SSH deploy key does not authenticate GHCR.
Under whisper, enter the token interactively:

```bash
read -r -p 'GitHub username: ' registry_user
read -r -s -p 'GHCR read token: ' registry_token
printf '\n'
printf '%s' "$registry_token" | docker --context rootless login ghcr.io -u "$registry_user" --password-stdin
unset registry_token registry_user
chmod 700 ~/.docker
chmod 600 ~/.docker/config.json
```

Docker's default config stores this credential, not encrypted; protect the
account and use a credential helper if available. Do not log in as the runner.

## Secrets and compatibility

Prepare the complete production configuration in the ignored `.env.server`, then
run `./deploy/encrypt-secrets.sh` from the checkout (or invoke its absolute path
from any directory). No arguments are accepted. SOPS uses the repository's
`.sops.yaml` public recipient; encryption does not require the private age key.
This replaces the entire `secrets/production.enc.env`, not individual entries.
The command leaves plaintext untouched, suppresses SOPS diagnostics, and replaces
the destination atomically only after successful encryption. It performs no Git
operations. Review and commit only the encrypted result; never force-add dotenv
files. A master push can deploy immediately, so complete the cloud logout step
before delivering secrets that enable the local Telegram API.

Validation: a temporary-fixture test covers invocation outside the checkout,
successful replacement, private output permissions, and preservation/cleanup
after a failed encryption without exposing diagnostic secrets. It substitutes
SOPS; it does not validate production credentials or decrypt the real file.

`secrets/production.enc.env` contains SOPS ciphertext only. The age public
recipient is in `.sops.yaml`; the private key never enters Git or the image.
Server-side decryption has already been manually verified. The deployment
entry point fetches the ciphertext from the exact release commit.

Required plaintext keys are `BOT_TOKEN`, `OPENAI_API_KEY`, and `DATABASE_URL`.
For production, DATABASE_URL must be of the form:

```text
postgresql+psycopg2://whisper:URL_ENCODED_PASSWORD@db:5432/whisper_db
```

Use a unique database password. Encode URL-reserved password characters.
Do not use localhost or the development port 5001. The script derives database
initialization credentials from this URL. If POSTGRES_* are present, they must
agree. Existing database credentials are not rotated by changing env files.
Use a separate reviewed rotation procedure instead. Initialization env vars do
not change credentials in an existing PostgreSQL volume.

Values must be single-line dotenv data. No shell evaluation or ${...}
interpolation is performed. Each service receives only its relevant env file;
runtime env files are 0600 in private per-release directories. Deploy logs
suppress external command output that could disclose secrets. Operators can
inspect container logs privately; do not paste tokens or full Docker inspect.

For optional local Telegram API, use `MEDIA_TELEGRAM_API_BASE=http://telegram-api:8081`,
`TELEGRAM_API_ID`, and `TELEGRAM_API_HASH`. The profile is selected from validated
secrets. The existing cloud logout prerequisite in [media.md](media.md) still
applies; deploy never performs it automatically.

The pinned local API image creates media under UID/GID 101:101, with directories
mode 750 and media files mode 640. The bot runs as UID 10001; production Compose
adds supplementary group 101 so it can traverse those directories and read
media. The Telegram volume remains read-only in the bot. Without this group,
Telegram can download a large file successfully while processing fails with
`PermissionError`. GID 101 is part of the pinned image's storage contract, not a
tuning parameter; recheck ownership when upgrading that image.

Existing hosts must install the updated Compose file before deploying. A Git
push alone does not replace the root-owned `/etc/whisper/compose.yaml`. After
updating the server checkout to the reviewed commit, run as administrator:

```bash
sudo install -o root -g root -m 0644 \
  /home/whisper/whisper_bot/compose.yaml /etc/whisper/compose.yaml
```

Then run the normal deployment to recreate the bot with the supplementary group;
restarting an existing container does not apply new group settings. No ownership
changes, broad chmod, or writable Telegram mount are required. The configuration
was validated with dummy secrets using `docker compose config --quiet`; actual
large-file processing must be verified after recreation on the server.

Keep an offline backup of the age key. Failed release directories can contain
plaintext too and must remain private. No automatic retention deletion is
performed; prune reviewed obsolete releases/backups manually and retain the
current runtime directory referenced by current.json.

### Switching from cloud to local Telegram API

Prepare `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`, and
`MEDIA_TELEGRAM_API_BASE=http://telegram-api:8081` in the complete `.env.server`,
then run `./deploy/encrypt-secrets.sh`. Do not deliver that change to master until
cloud logout is complete: master pushes can deploy automatically.

On the server, use a reviewed checkout containing the maintenance command. Wait
for active processing to finish, ensure no deployment is running, then run:

```bash
sudo -iu whisper
python3 /home/whisper/whisper_bot/deploy/telegram-cloud-logout.py
```

The command takes no arguments. It reads the installed `/etc/whisper/config.json`
for the application account, state directory and command timeout, and reads the
current release's `current.json` and `app.env`. It uses the rootless Docker
context and the deployment lock. Before stopping anything, it checks runtime
files, the cloud configuration, rootless mode, exactly one running Whisper bot,
and that its image matches the locally available current-release image.

After stopping the bot, a disposable container calls cloud `logOut` using the
existing token. Only a positive Telegram response is accepted. Diagnostics are
suppressed to avoid leaking credentials. Success leaves the bot stopped;
deliver the encrypted configuration through the normal master workflow next.
Deployment starts the local API and the bot. Verify both containers, send a short
recording, then a video larger than 20 MB to verify shared-storage access.

This is an explicit migration operation, never an automatic deploy step. Telegram
allows local login immediately after logout but blocks cloud login for ten
minutes. The bot is unavailable between stopping and successful deployment.
If stopping fails, inspect the bot's state before proceeding. If logout fails,
the bot remains stopped and the cloud logout outcome may be unknown; do not
blindly restart or retry. A timed-out Docker helper may still be running: inspect
it before deciding whether to remove it or proceed. The command never starts the
bot automatically and does not modify runtime files or the encrypted secrets.

Validation: mocked Docker tests cover successful ordering, missing runtime files,
absent/duplicate bots, unavailable/mismatched images, stop and logout failures,
and suppression of secret diagnostics. A helper test rejects a false Telegram
response. These checks do not contact production or prove live Telegram logout,
local API readiness, or shared-volume permissions.

## First release and verification

1. Push the reviewed commit. Tests and publication run; deployment remains off.
2. Create GitHub environment `production`, restrict it to master; configure
   required reviewers if available for your repository plan.
3. Install the host files, grant the limited sudo command, configure GHCR read
   access and verify rootless Docker. Check the production DATABASE_URL locally
   and re-encrypt/recommit if needed.
4. Stop the local bot using the same Telegram token (already done in this setup).
5. Set repository variable `DEPLOY_ENABLED=true`. Manually run `Whisper delivery`
   on master, or push a new reviewed master commit.
6. The script fetches/pulls/decrypts and validates before stopping the bot, starts
   the DB, stops the old bot, saves a custom-format pg_dump, migrates, starts one
   bot and performs a DB/Telegram getMe probe. getMe does not consume updates.
7. Send a short recording manually. OpenAI billing/transcription and large-media
   permissions need this live verification; the automated probe spends no credits.
8. Verify runner/Docker autostart and container recovery after a planned reboot.

Current status is `/home/whisper/deployment/current.json`: image digest, commit,
runtime env directory and pre-migration backup. Under whisper, inspect containers
with `docker --context rootless ps` and inspect logs locally.

The startup probe checks DB access/schema presence and Telegram credentials.
It does not prove continuous polling, OpenAI availability, or future task health.
Interrupted jobs are marked failed and reservations released on the next bot
startup; they are not automatically retried. Deploying while a job is active
can interrupt it. Draining jobs and zero-downtime releases are out of scope.

## Failure and recovery

Preflight failures leave the bot running. After the stop step, backup/migration
failures leave it stopped; a failing startup probe also stops the new bot.
Commands are bounded; a timed-out migration container is explicitly removed.
A host crash/SIGKILL can still leave `whisper-migration` behind: inspect and
resolve it before retrying rather than starting a second migration.

The previous successful current.json is retained on failure. Backups are private
custom-format pg_dump files; copy them off-host securely and rehearse restore.
Never use `compose down -v` as part of a release. There is no global Docker prune.

Rollback is manual: check database compatibility first, then run the previously
recorded digest with its original runtime files and installed Compose settings.
Do not assume reversing the image reverses migrations. A Git revert followed by
a successful master build is the normal forward recovery path. Automatic
deployment deliberately rejects old commits. Database restoration is a separate,
potentially destructive administrator operation.

## Validation

Local implementation checks completed on 2026-10-11:

- Docker runtime image built successfully; runtime UID/GID is 10001:10001.
- All 53 tests passed inside the runtime image, including deployment failure
  handling, FFmpeg integration and concurrent reservations on temporary PostgreSQL.
- Alembic upgraded an empty PostgreSQL 15 database through all revisions;
  `alembic check` reported no new upgrade operations.
- Production Compose, including the optional local API profile, validated with
  dummy env files and without displaying resolved credentials.
- The development Compose file and GitHub workflow passed validation; actionlint
  recognizes the custom whisper runner label. Shell/Python syntax and pip check
  passed. The image contains no .env, secrets directory or Git checkout.
- The existing ciphertext has SOPS metadata and no unencrypted dotenv values;
  the local server env passes the new deployment validator without logging values.

Actual GitHub workflow execution, rootless deployment on serv, reboot recovery,
live Telegram/OpenAI requests and backup restoration require the operator steps
above and cannot be claimed from a local build.
