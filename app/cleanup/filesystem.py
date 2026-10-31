"""Narrow, symlink-resistant access to managed media; never reads file contents."""
import fcntl
import hashlib
import os
from pathlib import Path
import re
import stat
from contextlib import contextmanager

# Completed TDLib media directories. Never include temp, databases or bot roots.
TELEGRAM_MEDIA = frozenset(('videos', 'video_notes', 'audios', 'voice', 'documents', 'animations'))


def fingerprint(info):
    values = (info.st_dev, info.st_ino) if stat.S_ISDIR(info.st_mode) else (
        info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)
    return hashlib.sha256(':'.join(map(str, values)).encode()).hexdigest()


def relative_path(root, path, scope):
    root, path = Path(root).absolute(), Path(path).absolute()
    parts = path.relative_to(root).parts
    if not parts or any(part in ('.', '..') for part in parts):
        raise ValueError('Unmanaged media path')
    if scope == 'telegram':
        if (len(parts) != 3 or not re.fullmatch(r'\d+:[A-Za-z0-9_-]+', parts[0])
                or parts[1] not in TELEGRAM_MEDIA or parts[2].startswith('.')):
            raise ValueError('Not a completed Telegram media path')
    elif scope == 'workspace':
        if len(parts) < 2 or parts[0] != 'managed' or not re.fullmatch(r'[0-9a-f]{32}', parts[1]):
            raise ValueError('Not a managed attempt path')
    else:
        raise ValueError('Unknown media scope')
    return parts


@contextmanager
def parent_fd(root, path, scope):
    parts = relative_path(root, path, scope)
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        yield fd, parts[-1]
    finally:
        os.close(fd)


def inspect_file(root, path, scope):
    with parent_fd(root, path, scope) as (fd, name):
        info = os.stat(name, dir_fd=fd, follow_symlinks=False)
        if not stat.S_ISREG(info.st_mode) and not (scope == 'workspace' and stat.S_ISDIR(info.st_mode)):
            raise ValueError('Media must be a regular file or managed directory')
        return info


def delete_media_file(root, item):
    """Return absent/replaced/deleted; never delete a replacement generation."""
    try:
        with parent_fd(root, item['path'], item['scope']) as (fd, name):
            info = os.stat(name, dir_fd=fd, follow_symlinks=False)
            if stat.S_ISLNK(info.st_mode):
                raise ValueError('Refusing media symlink')
            if item['fingerprint'] is None:
                raise ValueError('Present media must be inventoried before deletion')
            if fingerprint(info) != item['fingerprint']:
                return 'replaced'
            if item['kind'] == 'workspace_directory':
                # A busy directory is retained and inventoried, never recursively erased.
                guard = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                try:
                    fcntl.flock(guard, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    os.rmdir(name, dir_fd=fd)
                finally:
                    os.close(guard)
            elif stat.S_ISREG(info.st_mode):
                os.unlink(name, dir_fd=fd)
            else:
                raise ValueError('Not a media file')
        return 'deleted'
    except FileNotFoundError:
        return 'absent'


@contextmanager
def directory_guard(path, exclusive=False):
    """Nonblocking OS guard shared across UIDs and read-only mounts."""
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        fcntl.flock(fd, (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)


def inventory(root, scope, subtree=None):
    """Yield only approved paths; refuse symlink traversal at every boundary."""
    root = Path(root)
    for base, directories, files in os.walk(subtree or root, followlinks=False):
        relative = Path(base).relative_to(root).parts
        if scope == 'telegram':
            if not relative:
                directories[:] = [d for d in directories if re.fullmatch(r'\d+:[A-Za-z0-9_-]+', d)]
            elif len(relative) == 1:
                directories[:] = [d for d in directories if d in TELEGRAM_MEDIA]
            else:
                directories[:] = []
        else:
            if not relative:
                directories[:] = [d for d in directories if d == 'managed']
            elif relative == ('managed',):
                directories[:] = [d for d in directories if re.fullmatch(r'[0-9a-f]{32}', d)]
        for name in files:
            path = Path(base) / name
            try:
                info = inspect_file(root, path, scope)
            except (ValueError, OSError):
                continue
            yield path, info
