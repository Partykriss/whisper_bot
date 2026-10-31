import contextlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


spec = importlib.util.spec_from_file_location(
    'cloud_logout', Path(__file__).resolve().parents[2] / 'deploy/telegram-cloud-logout.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class CloudLogoutTests(unittest.TestCase):
    def exercise(self, *, missing_env=False, containers='bot-id', failure=None, mismatch=False):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'current.json').write_text(json.dumps({'runtime_dir': directory, 'image': 'release-image'}))
            if not missing_env:
                (root / 'app.env').write_text('BOT_TOKEN=secret-test-token\n')
            config = dict(user='whisper', state_dir=directory, command_timeout_seconds=10)
            calls = []

            def run(args, **kwargs):
                command = args[3]
                calls.append(command)
                output = {'info': '["name=rootless"]', 'ps': containers,
                          'image': 'image-id', 'inspect': 'different' if mismatch else 'image-id'}.get(command, '')
                return subprocess.CompletedProcess(args, 1 if command == failure else 0,
                                                   output, 'secret-test-token')

            stream = io.StringIO()
            with patch.object(module.pwd, 'getpwnam', return_value=SimpleNamespace(pw_uid=1003)), \
                    patch.object(module.os, 'geteuid', return_value=1003), \
                    patch.object(module.subprocess, 'run', side_effect=run), \
                    contextlib.redirect_stdout(stream):
                error = None
                try:
                    module.logout(config)
                except Exception as caught:
                    error = caught
            return calls, error, stream.getvalue()

    def test_success_stops_before_logout(self):
        calls, error, output = self.exercise()
        self.assertIsNone(error)
        self.assertEqual(calls[-2:], ['stop', 'run'])
        self.assertIn('Cloud logout confirmed', output)

    def test_preflight_failures_do_not_stop_bot(self):
        for options in ({'missing_env': True}, {'containers': ''},
                        {'containers': 'one two'}, {'mismatch': True}, {'failure': 'image'}):
            with self.subTest(options=options):
                calls, error, _ = self.exercise(**options)
                self.assertIsNotNone(error)
                self.assertNotIn('stop', calls)

    def test_logout_failure_reports_stopped_state_without_secrets(self):
        calls, error, output = self.exercise(failure='run')
        self.assertIn('Bot remains stopped', str(error))
        self.assertNotIn('secret-test-token', str(error) + output)
        self.assertNotIn('start', calls)

    def test_stop_failure_prevents_logout(self):
        calls, error, _ = self.exercise(failure='stop')
        self.assertNotIn('run', calls)
        self.assertIn('state is uncertain', str(error))

    def test_helper_rejects_false_telegram_response(self):
        async def false_logout():
            return False

        class Bot:
            def __init__(self, token):
                pass
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass
            log_out = staticmethod(false_logout)

        with patch.dict('sys.modules', aiogram=SimpleNamespace(Bot=Bot)), \
                patch.dict('os.environ', BOT_TOKEN='test'):
            with self.assertRaisesRegex(RuntimeError, 'not confirmed'):
                exec(module.LOGOUT_CODE, {})
