"""Sole persistence boundary for media generations, uses and cleanup leases."""
from contextlib import contextmanager
from datetime import datetime, timedelta
import hashlib
from uuid import uuid4
from sqlalchemy import exists, or_, text
from app.database import SessionLocal
from app.models import MediaAttempt, MediaFile, MediaFileUse, MediaCleanupEpoch, Task
from app.events.leases import LeaseLost
from app.cleanup.config import SETTINGS


def source_key(identity):
    return hashlib.sha256(identity.encode()).hexdigest()


@contextmanager
def source_lock(key):
    """Try a transaction-scoped source lock without blocking the asyncio loop."""
    number = int.from_bytes(bytes.fromhex(key)[:8], 'big', signed=True)
    with SessionLocal() as db, db.begin():
        acquired = db.execute(text('SELECT pg_try_advisory_xact_lock(:key)'), {'key': number}).scalar()
        if not acquired:
            raise BlockingIOError('Source is being acquired')
        yield


def register_attempt(attempt_id, task_id, token, directory, key):
    with SessionLocal() as db, db.begin():
        task = db.get(Task, task_id)
        if not task or task.lease_token != token or not task.lease_until or task.lease_until <= datetime.utcnow():
            raise LeaseLost('Cannot create files for a stale task')
        db.add(MediaAttempt(id=attempt_id, task_id=task_id, task_token=token,
                            directory=str(directory), source_key=key))


def acquisition_intent(attempt_id, acquiring):
    with SessionLocal() as db, db.begin():
        db.query(MediaAttempt).filter_by(id=attempt_id).update({'telegram_acquiring': int(acquiring)})


def check_attempt_owner(attempt_id):
    with SessionLocal() as db:
        attempt = db.get(MediaAttempt, attempt_id)
        task = db.get(Task, attempt.task_id)
        if (attempt.state != 'active' or task.status not in ('loading', 'processing')
                or task.lease_token != attempt.task_token or not task.lease_until
                or task.lease_until <= datetime.utcnow()):
            raise LeaseLost('Cannot create a recovered or expired attempt directory')


def register_file(attempt_id, path, kind, scope, generation, signature=None):
    """Telegram callers hold the cache guard and source lock; workspaces are private."""
    with SessionLocal() as db, db.begin():
        item = db.query(MediaFile).filter_by(path=str(path), generation=generation).with_for_update().first()
        if item is None:
            item = MediaFile(path=str(path), generation=generation, kind=kind, scope=scope,
                             fingerprint=signature, state='in_use' if signature else 'creating')
            db.add(item)
            db.flush()
        elif signature:
            item.fingerprint = signature
        if attempt_id:
            use = db.get(MediaFileUse, (item.id, attempt_id))
            if use is None:
                db.add(MediaFileUse(file_id=item.id, attempt_id=attempt_id))
            else:
                use.released_at = None
            item.state = 'in_use' if signature else 'creating'
            item.deleted_at = None
        return item.id


def ready(file_id, signature):
    with SessionLocal() as db, db.begin():
        item = db.get(MediaFile, file_id)
        item.fingerprint = signature
        item.state = 'in_use'


def _schedule(db, item, now):
    active = db.query(MediaFileUse).filter_by(file_id=item.id, released_at=None).first()
    if active is None and item.state not in ('deleted', 'deleting'):
        item.state = 'delete_pending'
        item.available_at = now


def schedule_media_cleanup(attempt_id, file_id=None):
    now = datetime.utcnow()
    with SessionLocal() as db, db.begin():
        uses = db.query(MediaFileUse).filter_by(attempt_id=attempt_id, released_at=None)
        if file_id is not None:
            uses = uses.filter_by(file_id=file_id)
        # Lock files before their uses: same order as register_file and claim.
        ids = [use.file_id for use in uses.all()]
        for identity in sorted(ids):
            item = db.query(MediaFile).filter_by(id=identity).with_for_update().one()
            db.query(MediaFileUse).filter_by(file_id=identity, attempt_id=attempt_id).update({'released_at': now})
            _schedule(db, item, now)
        if file_id is None:
            db.query(MediaAttempt).filter_by(id=attempt_id).update({'state': 'released', 'telegram_acquiring': 0})


def claim_media_cleanup(scope, settings=SETTINGS):
    now = datetime.utcnow()
    with SessionLocal() as db, db.begin():
        # Recovery is idempotent; the row lease fences completion by old workers.
        db.query(MediaFile).filter(MediaFile.scope == scope, MediaFile.state == 'deleting',
                                   MediaFile.lease_until <= now).update(
            {'state': 'delete_pending', 'lease_token': None, 'lease_until': None})
        used = exists().where(MediaFileUse.file_id == MediaFile.id, MediaFileUse.released_at.is_(None))
        item = db.query(MediaFile).filter(MediaFile.scope == scope, MediaFile.state == 'delete_pending',
                                         MediaFile.available_at <= now, ~used).order_by(
            MediaFile.id).with_for_update(skip_locked=True).first()
        if item is None:
            return None
        item.state, item.lease_token = 'deleting', uuid4().hex
        item.lease_until = now + timedelta(seconds=settings.lease_seconds)
        item.attempts += 1
        return {name: getattr(item, name) for name in (
            'id', 'path', 'generation', 'fingerprint', 'kind', 'scope', 'lease_token', 'attempts')}


@contextmanager
def deletion_claim(item):
    """Keep the generation row locked during unlink; no new use can race it."""
    with SessionLocal() as db, db.begin():
        row = db.query(MediaFile).filter_by(id=item['id']).with_for_update().one()
        if (row.state != 'deleting' or row.lease_token != item['lease_token']
                or row.lease_until <= datetime.utcnow()):
            raise LeaseLost('Cleanup ownership expired')
        if db.query(MediaFileUse).filter_by(file_id=row.id, released_at=None).first():
            raise LeaseLost('File is in use')
        yield
        row.state, row.deleted_at = 'deleted', datetime.utcnow()
        row.lease_token, row.lease_until, row.last_error = None, None, None


def retry_media_cleanup(item, error, settings=SETTINGS):
    exponent_cap = (settings.retry_max_seconds // settings.retry_seconds).bit_length()
    delay = min(settings.retry_max_seconds, settings.retry_seconds * 2 ** min(item['attempts'] - 1, exponent_cap))
    if isinstance(error, BlockingIOError):
        delay = settings.scan_seconds
    with SessionLocal() as db, db.begin():
        db.query(MediaFile).filter_by(id=item['id'], lease_token=item['lease_token'], state='deleting').update({
            'state': 'delete_pending', 'lease_token': None, 'lease_until': None,
            'last_error': type(error).__name__,
            'available_at': datetime.utcnow() + timedelta(seconds=delay)})


def cleanup_source_keys(file_id):
    with SessionLocal() as db:
        return sorted({row[0] for row in db.query(MediaAttempt.source_key).join(MediaFileUse).filter(
            MediaFileUse.file_id == file_id).all()})


def orphan_attempts(settings=SETTINGS):
    now = datetime.utcnow()
    with SessionLocal() as db:
        rows = db.query(MediaAttempt).join(Task).filter(
            MediaAttempt.state != 'cleaned',
            MediaAttempt.created_at <= now - timedelta(seconds=settings.orphan_guard_seconds),
            or_(MediaAttempt.state == 'released', Task.lease_token.is_(None),
                Task.lease_token != MediaAttempt.task_token, Task.lease_until <= now,
                Task.status.notin_(('loading', 'processing')))).order_by(MediaAttempt.created_at).all()
        return [(r.id, r.directory) for r in rows]


def mark_attempt_cleaned(attempt_id):
    with SessionLocal() as db, db.begin():
        db.query(MediaAttempt).filter_by(id=attempt_id).update({'state': 'cleaned'})


def workspace_files(attempt_id):
    with SessionLocal() as db:
        return [(f.id, f.path, f.fingerprint, f.state) for f in db.query(MediaFile).join(MediaFileUse).filter(
            MediaFileUse.attempt_id == attempt_id, MediaFile.scope == 'workspace').all()]


def observe_orphan(path, signature, settings=SETTINGS):
    """Two stable observations separated by the guard precede orphan deletion."""
    now = datetime.utcnow()
    with SessionLocal() as db, db.begin():
        item = db.query(MediaFile).filter_by(path=str(path), generation=signature).with_for_update().first()
        if item is None:
            db.add(MediaFile(path=str(path), generation=signature, fingerprint=signature,
                             kind='telegram_cached_source', scope='telegram', state='creating'))
        elif item.state == 'creating' and item.created_at <= now - timedelta(seconds=settings.orphan_guard_seconds):
            _schedule(db, item, now)


def cleanup_epoch():
    with SessionLocal() as db:
        return db.query(MediaCleanupEpoch).filter_by(id=1).one().started_at


def queue_unreferenced_generations(settings=SETTINGS):
    """Also finish orphan observations whose files vanished/replaced meanwhile."""
    now = datetime.utcnow()
    with SessionLocal() as db, db.begin():
        referenced = exists().where(MediaFileUse.file_id == MediaFile.id)
        db.query(MediaFile).filter(MediaFile.scope == 'telegram', MediaFile.state == 'creating',
                                   MediaFile.created_at <= now - timedelta(seconds=settings.orphan_guard_seconds),
                                   ~referenced).update({'state': 'delete_pending', 'available_at': now},
                                                      synchronize_session=False)
