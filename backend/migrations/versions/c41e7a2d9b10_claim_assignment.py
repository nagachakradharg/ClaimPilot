"""claim assignment (per-adjuster queue)

Revision ID: c41e7a2d9b10
Revises: 9b221c5e2551
Create Date: 2026-11-27
"""

import sqlalchemy as sa
from alembic import op

revision = "c41e7a2d9b10"
down_revision = "9b221c5e2551"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("claims", sa.Column("assigned_to", sa.String(length=255), nullable=True))
    op.create_index("ix_claims_assigned_to", "claims", ["assigned_to"])


def downgrade() -> None:
    op.drop_index("ix_claims_assigned_to", table_name="claims")
    op.drop_column("claims", "assigned_to")
