"""Drop users.seconds_used column

Revision ID: c6d7b3e8a5f0
Revises: 9b0f8b2e4d1a
Create Date: 2026-01-23 13:25:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "c6d7b3e8a5f0"
down_revision: Union[str, Sequence[str], None] = "9b0f8b2e4d1a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_column("users", "seconds_used")


def downgrade() -> None:
    """Downgrade schema."""
    op.add_column("users", sa.Column("seconds_used", sa.Float(), nullable=True))
