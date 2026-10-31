"""Fixed server deployment entry point; never run scripts from the release checkout."""
import fcntl
import json
import os
from pathlib import Path
import pwd
import re
import subprocess
import sys
import tempfile
import time

# This directory and all installed files must be owned by root.
sys.path.insert(0, '/usr/local/lib/whisper')
from secret_config import prepare_secrets, raw_env, cleanup_secrets


def validate_release(commit, digest):
    if not re.fullmatch(r'[0-9a-f]{40}', commit):
        raise ValueError('Expected a full lowercase Git commit SHA')
    if not re.fullmatch(r'sha256:[0-9a-f]{64}', digest):
        raise ValueError('Expected an image SHA256 digest')


def private_write(path, content):
    with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)


class Deployment:
    def __init__(self, config):
        self.config = config
        account = pwd.getpwnam(config['user'])
        if os.geteuid() != account.pw_uid or os.geteuid() == 0:
            raise ValueError('Deploy must run as the configured unprivileged application user')
        self.state = Path(config['state_dir'])
        self.state.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.env = {
            'PATH': '/usr/local/bin:/usr/bin:/bin', 'HOME': account.pw_dir,
            'USER': account.pw_name, 'LANG': 'C.UTF-8',
            'DOCKER_HOST': f'unix:///run/user/{account.pw_uid}/docker.sock',
            'XDG_RUNTIME_DIR': f'/run/user/{account.pw_uid}',
            'DBUS_SESSION_BUS_ADDRESS': f'unix:path=/run/user/{account.pw_uid}/bus',
            'SOPS_AGE_KEY_FILE': config['age_key'],
            'GIT_SSH_COMMAND': f'ssh -i {config["ssh_key"]} -o IdentitiesOnly=yes -o BatchMode=yes -o StrictHostKeyChecking=yes',
            'GIT_TERMINAL_PROMPT': '0', 'GIT_CONFIG_NOSYSTEM': '1',
            'GIT_CONFIG_GLOBAL': '/dev/null',
        }
        self.compose = None

    def run(self, *args, input=None, timeout=None, output=None):
        # Capture diagnostics: third-party tools can print database URLs or tokens.
        result = subprocess.run(args, input=input, stdout=output or subprocess.PIPE,
                                stderr=subprocess.PIPE, env=self.env,
                                timeout=timeout or self.config['command_timeout_seconds'])
        if result.returncode:
            raise RuntimeError(f'{Path(args[0]).name} failed (exit {result.returncode}); output suppressed to protect secrets')
        return result.stdout

    def compose_run(self, *args, **kwargs):
        return self.run(*self.compose, *args, **kwargs)

    def execute(self, commit, digest):
        validate_release(commit, digest)
        with (self.state / 'deploy.lock').open('a') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise RuntimeError('Another deployment is already running') from None
            self.release(commit, digest)

    def release(self, commit, digest):
        print('Checking rootless Docker and trusted release', flush=True)
        security = json.loads(self.run('docker', 'info', '--format', '{{json .SecurityOptions}}'))
        if not any('rootless' in option for option in security):
            raise RuntimeError('Refusing to deploy to a non-rootless Docker daemon')
        repository = self.state / 'repository.git'
        if not repository.exists():
            self.run('git', 'init', '--bare', str(repository))
        git = ('git', '-c', 'core.hooksPath=/dev/null', '--git-dir', str(repository))
        branch = self.config['branch']
        self.run(*git, 'fetch', '--no-tags', self.config['repository'],
                 f'+refs/heads/{branch}:refs/remotes/origin/{branch}')
        head = self.run(*git, 'rev-parse', f'refs/remotes/origin/{branch}').decode().strip()
        if head != commit:
            raise ValueError('Release is no longer the current master commit; deploy the latest successful build')
        image = f'{self.config["image"]}@{digest}'
        self.run('docker', 'pull', image)
        revision = self.run('docker', 'image', 'inspect', image, '--format',
                            '{{index .Config.Labels "org.opencontainers.image.revision"}}').decode().strip()
        if revision != commit:
            raise ValueError('Image revision does not match the release commit')
        encrypted = self.run(*git, 'show', f'{commit}:{self.config["encrypted_file"]}')
        # Use a real protected file rather than /dev/stdin across sudo boundaries.
        with tempfile.TemporaryDirectory(dir=self.state) as temporary:
            source = Path(temporary) / 'encrypted.env'
            source.write_bytes(encrypted)
            decrypted = self.run('sops', '--decrypt', '--input-type', 'dotenv',
                                 '--output-type', 'dotenv', str(source)).decode()
        try:
            app, database, telegram, local_api = prepare_secrets(decrypted)
        except Exception:
            raise ValueError('Invalid runtime secrets; check required keys and the internal DATABASE_URL (db:5432)') from None
        releases = self.state / 'releases'
        releases.mkdir(exist_ok=True, mode=0o700)
        release = Path(tempfile.mkdtemp(prefix=f'{commit[:12]}-', dir=releases))
        for name, values in [('app', app), ('db', database), ('telegram', telegram),
                             ('cleanup', cleanup_secrets(app))]:
            private_write(release / f'{name}.env', raw_env(values))
        # Do not source decrypted data or let it control Compose interpolation.
        self.env.update(self.config['compose_environment'])
        self.env.update(BOT_IMAGE=image, RUNTIME_DIR=str(release))
        self.compose = ('docker', 'compose', '--project-name', 'whisper', '--env-file', '/dev/null',
                        '-f', self.config['compose_file'])
        if local_api:
            self.compose += ('--profile', 'large-media')
        self.compose_run('config', '--quiet')
        # Do not silently change initialization credentials on an existing database.
        current_path = self.state / 'current.json'
        if current_path.exists():
            current = json.loads(current_path.read_text())
            if (Path(current['runtime_dir']) / 'db.env').read_text() != raw_env(database):
                raise ValueError('Database credential changes require a separate rotation procedure')
        self.compose_run('pull', 'db', *(['telegram-api'] if local_api else []))
        self.compose_run('up', '-d', '--wait', '--wait-timeout',
                         str(self.config['startup_timeout_seconds']), 'db')
        print('Stopping the bot; backing up database before migration', flush=True)
        self.compose_run('stop', 'bot', 'telegram-cleanup')
        backup_dir = self.state / 'backups'
        backup_dir.mkdir(exist_ok=True, mode=0o700)
        backup = backup_dir / f'{release.name}.dump'
        partial = backup.with_suffix('.partial')
        try:
            with partial.open('xb') as stream:
                self.compose_run('exec', '-T', 'db', 'pg_dump', '-Fc',
                                 '-U', database['POSTGRES_USER'], '-d', database['POSTGRES_DB'], output=stream)
            partial.replace(backup)
        except Exception:
            partial.unlink(missing_ok=True)
            raise
        print('Applying migrations', flush=True)
        try:
            self.compose_run('run', '--rm', '--no-deps', '--name', 'whisper-migration', 'migrate',
                             timeout=self.config['migration_timeout_seconds'])
        except Exception:
            # Killing the CLI on timeout does not stop the container it started.
            try:
                self.run('docker', 'rm', '-f', 'whisper-migration')
            except Exception:
                pass
            raise
        if local_api:
            self.compose_run('up', '-d', '--no-deps', 'telegram-api', 'telegram-cleanup')
        else:
            self.compose_run('stop', 'telegram-api', 'telegram-cleanup')
        print('Starting bot and observing startup', flush=True)
        self.compose_run('up', '-d', '--no-deps', 'bot')
        container = self.compose_run('ps', '-q', 'bot').decode().strip()
        try:
            time.sleep(self.config['observe_seconds'])
            status = json.loads(self.run('docker', 'inspect', container))[0]
            if not status['State']['Running'] or status['RestartCount'] != 0:
                raise RuntimeError('Bot exited or restarted during startup observation')
            # Readiness: credentials, network and DB schema; no second polling instance.
            self.compose_run('exec', '-T', 'bot', 'python', '/code/app/deployment_probe.py')
            if local_api:
                cleanup_container = self.compose_run('ps', '-q', 'telegram-cleanup').decode().strip()
                cleanup_status = json.loads(self.run('docker', 'inspect', cleanup_container))[0]
                if not cleanup_status['State']['Running'] or cleanup_status['RestartCount'] != 0:
                    raise RuntimeError('Telegram cleanup service exited or restarted during startup')
        except Exception:
            self.compose_run('stop', 'bot', 'telegram-cleanup')
            raise
        private_write(current_path, json.dumps({'commit': commit, 'image': image,
                                               'runtime_dir': str(release), 'backup': str(backup)}, indent=2) + '\n')
        print(f'Deployed {commit} ({digest})', flush=True)


def main():
    os.umask(0o077)
    try:
        if len(sys.argv) != 3:
            raise ValueError('Usage: whisper-deploy COMMIT_SHA IMAGE_DIGEST')
        validate_release(*sys.argv[1:])
        config = json.loads(Path('/etc/whisper/config.json').read_text())
        Deployment(config).execute(*sys.argv[1:])
    except (ValueError, RuntimeError) as error:
        print(f'Deployment failed: {error}', file=sys.stderr)
        return 1
    except Exception:
        print('Deployment failed; command timed out or a system operation failed. No automatic rollback was attempted.', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
