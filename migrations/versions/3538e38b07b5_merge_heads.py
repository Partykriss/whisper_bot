"""merge heads

Revision ID: 3538e38b07b5
Revises: 8f1a6f7b9c2e, 1c201c5cf62c
Create Date: 2026-01-23 12:23:17.431249

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '3538e38b07b5'
down_revision: Union[str, Sequence[str], None] = ('8f1a6f7b9c2e', '1c201c5cf62c')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
