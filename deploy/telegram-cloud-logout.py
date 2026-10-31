#!/usr/bin/env python3
"""Explicit, one-time cloud logout before deploying the local Telegram API."""
import fcntl
import json
import os
from pathlib import Path
import pwd
import subprocess
import sys


LOGOUT_CODE = '''
import asyncio, os
from aiogram import Bot

async def main():
    async with Bot(os.environ["BOT_TOKEN"]) as bot:
        if await bot.log_out() is not True:
            raise RuntimeError("Cloud logout was not confirmed")

asyncio.run(main())
'''


def logout(config):
    account = pwd.getpwnam(config['user'])
    if os.geteuid() != account.pw_uid or os.geteuid() == 0:
        raise RuntimeError('Run this command as the configured application user.')

    def docker(*args):
        result = subprocess.run(
            ['docker', '--context', 'rootless', *args],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            timeout=config['command_timeout_seconds'],
        )
        if result.returncode:
            raise RuntimeError('Docker command failed; diagnostics suppressed to protect secrets.')
        return result.stdout.strip()

    state_dir = Path(config['state_dir'])
    with (state_dir / 'deploy.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('Another deployment or logout is running.') from None
        state = json.loads((state_dir / 'current.json').read_text())
        env_file = Path(state['runtime_dir']) / 'app.env'
        entries = dict(line.split('=', 1) for line in env_file.read_text().splitlines()
                       if '=' in line and not line.startswith('#'))
        if not entries.get('BOT_TOKEN'):
            raise RuntimeError('Current runtime has no bot token.')
        if entries.get('MEDIA_TELEGRAM_API_BASE'):
            raise RuntimeError('Current runtime already selects a local API; refusing cloud migration.')
        security = json.loads(docker('info', '--format', '{{json .SecurityOptions}}'))
        if not any('rootless' in option for option in security):
            raise RuntimeError('Docker daemon must be rootless.')
        containers = docker('ps', '-q',
                            '--filter', 'label=com.docker.compose.project=whisper',
                            '--filter', 'label=com.docker.compose.service=bot').split()
        if len(containers) != 1:
            raise RuntimeError('Expected exactly one running Whisper bot; no bot was stopped.')
        image_id = docker('image', 'inspect', state['image'], '--format', '{{.Id}}')
        running_image = docker('inspect', containers[0], '--format', '{{.Image}}')
        if not image_id or image_id != running_image:
            raise RuntimeError('Running bot image differs from current release; no bot was stopped.')
        try:
            docker('stop', containers[0])
        except Exception:
            raise RuntimeError('Stop failed or timed out; bot state is uncertain. Inspect Docker before continuing.') from None
        print('Bot stopped. Logging out from the cloud API.', flush=True)
        try:
            docker('run', '--rm', '--network', 'bridge', '--env-file', str(env_file),
                   '--entrypoint', 'python', state['image'], '-c', LOGOUT_CODE)
        except Exception:
            raise RuntimeError('Cloud logout was not confirmed. Bot remains stopped; inspect Docker and Telegram before retrying. A timed-out helper may still be running.') from None
        print('Cloud logout confirmed. Bot remains stopped. Deploy the release with local API settings next.')


def main():
    if len(sys.argv) != 1:
        print('Usage: telegram-cloud-logout.py (no arguments)', file=sys.stderr)
        return 1
    try:
        config = json.loads(Path('/etc/whisper/config.json').read_text())
        logout(config)
    except RuntimeError as error:
        print(str(error), file=sys.stderr)
        return 1
    except Exception:
        print('Preflight failed; check installed config, current release, files and Docker access. Details suppressed to protect secrets.', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
