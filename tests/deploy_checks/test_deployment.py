import json
import fcntl
from pathlib import Path
import sys
import tempfile
import subprocess
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'deploy'))
from deployment import Deployment, validate_release

COMMIT = 'a' * 40
DIGEST = 'sha256:' + 'b' * 64
SECRETS = b'BOT_TOKEN=test\nOPENAI_API_KEY=test\nDATABASE_URL=postgresql+psycopg2://whisper:pass@db/whisper_db\n'


class FakeDeployment(Deployment):
    def __init__(self, directory, failure=None, head=COMMIT):
        self.state = Path(directory)
        self.config = {'branch': 'master', 'repository': 'fixed-repo', 'image': 'fixed-image',
                       'encrypted_file': 'secrets/production.enc.env', 'compose_environment': {},
                       'compose_file': '/etc/whisper/compose.yaml', 'startup_timeout_seconds': 1,
                       'migration_timeout_seconds': 1, 'observe_seconds': 0}
        self.env = {}
        self.calls = []
        self.failure = failure
        self.head = head

    def run(self, *args, **kwargs):
        self.calls.append(args)
        if self.failure == 'migration' and 'migrate' in args and 'run' in args:
            raise RuntimeError('migration failed')
        if self.failure == 'decrypt' and args[0] == 'sops':
            raise RuntimeError('decrypt failed')
        if args[:2] == ('docker', 'info'):
            return b'["name=rootless"]'
        if 'rev-parse' in args:
            return self.head.encode()
        if args[:3] == ('docker', 'image', 'inspect'):
            return COMMIT.encode()
        if args[0] == 'sops':
            return SECRETS
        if 'pg_dump' in args:
            kwargs['output'].write(b'backup')
        if 'ps' in args:
            return b'container-id'
        if args[:2] == ('docker', 'inspect'):
            return json.dumps([{'State': {'Running': True}, 'RestartCount': 0}]).encode()
        return b''


class DeploymentTests(unittest.TestCase):
    def test_arbitrary_arguments_rejected(self):
        for commit, digest in [('master', DIGEST), (COMMIT, '--privileged'), (COMMIT + ';id', DIGEST)]:
            with self.assertRaises(ValueError):
                validate_release(commit, digest)

    def test_success_orders_stop_backup_migrate_start_and_records_release(self):
        with tempfile.TemporaryDirectory() as directory:
            deploy = FakeDeployment(directory)
            deploy.execute(COMMIT, DIGEST)
            calls = deploy.calls
            stop = next(i for i, c in enumerate(calls) if c[-2:] == ('stop', 'bot'))
            backup = next(i for i, c in enumerate(calls) if 'pg_dump' in c)
            migrate = next(i for i, c in enumerate(calls) if c[-1] == 'migrate' and 'run' in c)
            start = next(i for i, c in enumerate(calls) if c[-4:] == ('up', '-d', '--no-deps', 'bot'))
            self.assertLess(stop, backup)
            self.assertLess(backup, migrate)
            self.assertLess(migrate, start)
            current = json.loads((Path(directory) / 'current.json').read_text())
            self.assertEqual(current['commit'], COMMIT)
            self.assertEqual(Path(current['backup']).read_bytes(), b'backup')
            self.assertEqual((Path(current['runtime_dir']) / 'app.env').stat().st_mode & 0o777, 0o600)

    def test_migration_failure_never_starts_new_bot_or_records_success(self):
        with tempfile.TemporaryDirectory() as directory:
            deploy = FakeDeployment(directory, failure='migration')
            with self.assertRaises(RuntimeError):
                deploy.execute(COMMIT, DIGEST)
            self.assertFalse(any(c[-4:] == ('up', '-d', '--no-deps', 'bot') for c in deploy.calls))
            self.assertFalse((Path(directory) / 'current.json').exists())

    def test_decryption_failure_keeps_running_bot_untouched(self):
        with tempfile.TemporaryDirectory() as directory:
            deploy = FakeDeployment(directory, failure='decrypt')
            with self.assertRaises(RuntimeError):
                deploy.execute(COMMIT, DIGEST)
            self.assertFalse(any('stop' in c or 'up' in c for c in deploy.calls))

    def test_superseded_release_is_rejected_before_pull_or_secrets(self):
        with tempfile.TemporaryDirectory() as directory:
            deploy = FakeDeployment(directory, head='c' * 40)
            with self.assertRaises(ValueError):
                deploy.execute(COMMIT, DIGEST)
            self.assertFalse(any('pull' in c or c[0] == 'sops' for c in deploy.calls))

    def test_probe_failure_stops_bot_and_does_not_record_success(self):
        with tempfile.TemporaryDirectory() as directory:
            deploy = FakeDeployment(directory)
            original = deploy.compose_run
            def compose(*args, **kwargs):
                if '/code/app/deployment_probe.py' in args:
                    raise RuntimeError('probe failed')
                return original(*args, **kwargs)
            with patch.object(deploy, 'compose_run', side_effect=compose):
                with self.assertRaises(RuntimeError):
                    deploy.execute(COMMIT, DIGEST)
            self.assertEqual(deploy.calls[-1][-2:], ('stop', 'bot'))
            self.assertFalse((Path(directory) / 'current.json').exists())

    def test_database_rotation_rejected_before_stopping_bot(self):
        with tempfile.TemporaryDirectory() as directory:
            previous = Path(directory) / 'previous'
            previous.mkdir()
            (previous / 'db.env').write_text('POSTGRES_PASSWORD=old\n')
            (Path(directory) / 'current.json').write_text(json.dumps({'runtime_dir': str(previous)}))
            deploy = FakeDeployment(directory)
            with self.assertRaisesRegex(ValueError, 'rotation'):
                deploy.execute(COMMIT, DIGEST)
            self.assertFalse(any('stop' in c or 'up' in c for c in deploy.calls))

    def test_concurrent_deployment_rejected_before_external_commands(self):
        with tempfile.TemporaryDirectory() as directory:
            deploy = FakeDeployment(directory)
            with (Path(directory) / 'deploy.lock').open('a') as held:
                fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
                with self.assertRaisesRegex(RuntimeError, 'already running'):
                    deploy.execute(COMMIT, DIGEST)
            self.assertEqual(deploy.calls, [])

    def test_rootful_docker_is_rejected_before_fetch(self):
        with tempfile.TemporaryDirectory() as directory:
            deploy = FakeDeployment(directory)
            with patch.object(deploy, 'run', return_value=b'["name=seccomp"]') as run:
                with self.assertRaisesRegex(RuntimeError, 'non-rootless'):
                    deploy.execute(COMMIT, DIGEST)
                self.assertEqual(run.call_count, 1)

    def test_image_from_other_revision_rejected_before_decryption(self):
        with tempfile.TemporaryDirectory() as directory:
            deploy = FakeDeployment(directory)
            original = deploy.run
            def run(*args, **kwargs):
                if args[:3] == ('docker', 'image', 'inspect'):
                    return b'c' * 40
                return original(*args, **kwargs)
            with patch.object(deploy, 'run', side_effect=run):
                with self.assertRaisesRegex(ValueError, 'revision'):
                    deploy.execute(COMMIT, DIGEST)
            self.assertFalse(any(c[0] == 'sops' or 'stop' in c for c in deploy.calls))

    def test_migration_timeout_removes_only_its_named_container(self):
        with tempfile.TemporaryDirectory() as directory:
            deploy = FakeDeployment(directory)
            original = deploy.compose_run
            def compose(*args, **kwargs):
                if 'run' in args and 'migrate' in args:
                    raise subprocess.TimeoutExpired('docker', 1)
                return original(*args, **kwargs)
            with patch.object(deploy, 'compose_run', side_effect=compose):
                with self.assertRaises(subprocess.TimeoutExpired):
                    deploy.execute(COMMIT, DIGEST)
            self.assertIn(('docker', 'rm', '-f', 'whisper-migration'), deploy.calls)
            self.assertFalse(any(c[-4:] == ('up', '-d', '--no-deps', 'bot') for c in deploy.calls))

    def test_runner_environment_cannot_redirect_docker_or_git(self):
        with tempfile.TemporaryDirectory() as directory:
            config = {'user': 'whisper', 'state_dir': directory, 'age_key': '/fixed/key',
                      'ssh_key': '/fixed/ssh', 'command_timeout_seconds': 1}
            account = SimpleNamespace(pw_uid=1003, pw_dir='/home/whisper', pw_name='whisper')
            with patch('deployment.pwd.getpwnam', return_value=account), \
                 patch('deployment.os.geteuid', return_value=1003), \
                 patch.dict('os.environ', {'DOCKER_HOST': 'unix:///var/run/docker.sock',
                                           'GIT_SSH_COMMAND': 'evil', 'LD_PRELOAD': '/evil.so'}):
                deploy = Deployment(config)
                with patch('deployment.subprocess.run', return_value=SimpleNamespace(returncode=0, stdout=b'')) as run:
                    deploy.run('docker', 'info')
                env = run.call_args.kwargs['env']
                self.assertEqual(env['DOCKER_HOST'], 'unix:///run/user/1003/docker.sock')
                self.assertNotIn('LD_PRELOAD', env)
                self.assertNotEqual(env['GIT_SSH_COMMAND'], 'evil')
