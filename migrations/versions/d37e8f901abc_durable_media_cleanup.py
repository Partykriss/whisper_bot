"""Durable media generations, uses and cleanup leases.

Revision ID: d37e8f901abc
Revises: b24e7a90c123
"""
from alembic import op
import sqlalchemy as sa

revision = 'd37e8f901abc'
down_revision = 'b24e7a90c123'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('tasks', sa.Column('source_identity', sa.String()))
    op.create_table('media_attempts',
        sa.Column('id', sa.String(), primary_key=True),
        sa.Column('task_id', sa.Integer(), sa.ForeignKey('tasks.id'), nullable=False),
        sa.Column('task_token', sa.String(), nullable=False),
        sa.Column('directory', sa.String(), nullable=False, unique=True),
        sa.Column('source_key', sa.String(), nullable=False),
        sa.Column('state', sa.String(), nullable=False),
        sa.Column('telegram_acquiring', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False))
    op.create_index('ix_media_attempts_task_id', 'media_attempts', ['task_id'])
    op.create_table('media_files',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('scope', sa.String(), nullable=False),
        sa.Column('kind', sa.String(), nullable=False),
        sa.Column('path', sa.String(), nullable=False),
        sa.Column('generation', sa.String(), nullable=False),
        sa.Column('fingerprint', sa.String()),
        sa.Column('state', sa.String(), nullable=False),
        sa.Column('attempts', sa.Integer(), nullable=False),
        sa.Column('available_at', sa.DateTime(), nullable=False),
        sa.Column('lease_token', sa.String()),
        sa.Column('lease_until', sa.DateTime()),
        sa.Column('last_error', sa.String()),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('deleted_at', sa.DateTime()),
        sa.UniqueConstraint('path', 'generation', name='uq_media_file_generation'))
    op.create_index('ix_media_files_cleanup', 'media_files', ['scope', 'state', 'available_at'])
    op.create_table('media_file_uses',
        sa.Column('file_id', sa.Integer(), sa.ForeignKey('media_files.id'), primary_key=True),
        sa.Column('attempt_id', sa.String(), sa.ForeignKey('media_attempts.id'), primary_key=True),
        sa.Column('released_at', sa.DateTime()))
    op.create_table('media_cleanup_epoch',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('started_at', sa.DateTime(), nullable=False))
    op.execute("INSERT INTO media_cleanup_epoch VALUES (1, timezone('utc', now()))")


def downgrade():
    op.drop_table('media_file_uses')
    op.drop_table('media_files')
    op.drop_table('media_attempts')
    op.drop_table('media_cleanup_epoch')
    op.drop_column('tasks', 'source_identity')
