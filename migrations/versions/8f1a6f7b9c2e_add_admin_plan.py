"""Add admin plan to enum

Revision ID: 8f1a6f7b9c2e
Revises: 4b39ff17637e
Create Date: 2026-01-23 12:10:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "8f1a6f7b9c2e"
down_revision: Union[str, Sequence[str], None] = "4b39ff17637e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_type WHERE typname = 'plantype') THEN
                IF NOT EXISTS (
                    SELECT 1
                    FROM pg_enum e
                    JOIN pg_type t ON t.oid = e.enumtypid
                    WHERE t.typname = 'plantype' AND e.enumlabel = 'admin'
                ) THEN
                    ALTER TYPE plantype ADD VALUE 'admin';
                END IF;
            END IF;

            IF EXISTS (SELECT 1 FROM pg_type WHERE typname = 'plan_type') THEN
                IF NOT EXISTS (
                    SELECT 1
                    FROM pg_enum e
                    JOIN pg_type t ON t.oid = e.enumtypid
                    WHERE t.typname = 'plan_type' AND e.enumlabel = 'admin'
                ) THEN
                    ALTER TYPE plan_type ADD VALUE 'admin';
                END IF;
            END IF;
        END$$;
        """
    )


def downgrade() -> None:
    """Downgrade schema."""
    # Removing enum values in Postgres is non-trivial; keep as a no-op.
    pass
