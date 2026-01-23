"""Add payments table

Revision ID: 7d2e6d0c4f31
Revises: 3538e38b07b5
Create Date: 2026-01-23 12:25:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "7d2e6d0c4f31"
down_revision: Union[str, Sequence[str], None] = "3538e38b07b5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "payments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "plan",
            postgresql.ENUM(
                "trial", "plus", "pro", "admin", name="plantype", create_type=False
            ),
            nullable=False,
        ),
        sa.Column("amount", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(), nullable=False),
        sa.Column("payload", sa.String(), nullable=False),
        sa.Column("telegram_charge_id", sa.String(), nullable=False),
        sa.Column("provider_charge_id", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["user_id"], ["users.telegram_id"]),
        sa.UniqueConstraint("telegram_charge_id"),
    )
    op.create_index(op.f("ix_payments_user_id"), "payments", ["user_id"], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_payments_user_id"), table_name="payments")
    op.drop_table("payments")
