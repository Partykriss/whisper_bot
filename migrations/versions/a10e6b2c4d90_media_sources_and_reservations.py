"""Media sources, progress and balance reservations.

Revision ID: a10e6b2c4d90
Revises: 6a8f0c3d9b12
"""
from alembic import op
import sqlalchemy as sa

revision = 'a10e6b2c4d90'
down_revision = '6a8f0c3d9b12'
branch_labels = None
depends_on = None

def upgrade():
    op.add_column('users', sa.Column('reserved_seconds', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('tasks', sa.Column('source_url', sa.String(), nullable=True))
    op.add_column('tasks', sa.Column('reserved_seconds', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('tasks', sa.Column('progress', sa.Integer(), nullable=False, server_default='0'))

def downgrade():
    op.drop_column('tasks', 'progress')
    op.drop_column('tasks', 'reserved_seconds')
    op.drop_column('tasks', 'source_url')
    op.drop_column('users', 'reserved_seconds')
