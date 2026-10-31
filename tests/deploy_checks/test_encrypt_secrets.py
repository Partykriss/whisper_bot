import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


class EncryptSecretsTests(unittest.TestCase):
    def test_success_and_failure_from_unrelated_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'project with spaces'
            for name in ('deploy', 'secrets', 'bin'):
                (root / name).mkdir(parents=True)
            script = root / 'deploy/encrypt-secrets.sh'
            shutil.copyfile(Path(__file__).resolve().parents[2] / 'deploy/encrypt-secrets.sh', script)
            source = root / '.env.server'
            source.write_text('BOT_TOKEN=private-test-value\n')
            (root / '.sops.yaml').write_text('test configuration')
            target = root / 'secrets/production.enc.env'
            target.write_text('previous ciphertext')
            sops = root / 'bin/sops'
            sops.write_text('''#!/bin/sh
test "$1" = --config || exit 2
test "$2" = "$PWD/.sops.yaml" || exit 2
test -r .env.server || exit 2
echo ciphertext
if [ "${FAIL:-}" = 1 ]; then
    echo private-test-value >&2
    exit 1
fi
''')
            sops.chmod(0o755)
            env = dict(os.environ, PATH=f'{root / "bin"}:{os.environ["PATH"]}')
            result = subprocess.run(['sh', str(script)], cwd=directory, env=env, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(target.read_text(), 'ciphertext\n')
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)
            env['FAIL'] = '1'
            result = subprocess.run(['sh', str(script)], cwd=directory, env=env, capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(target.read_text(), 'ciphertext\n')
            self.assertNotIn(b'private-test-value', result.stdout + result.stderr)
            self.assertEqual(source.read_text(), 'BOT_TOKEN=private-test-value\n')
            self.assertEqual(list((root / 'secrets').iterdir()), [target])
