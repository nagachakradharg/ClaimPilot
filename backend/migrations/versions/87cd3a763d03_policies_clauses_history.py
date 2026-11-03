"""policies clauses history

Revision ID: 87cd3a763d03
Revises: 57690f3a1bbb
Create Date: 2026-11-24 01:33:55.924381

"""

from collections.abc import Sequence

import pgvector.sqlalchemy  # noqa: F401  (Vector columns)
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "87cd3a763d03"
down_revision: str | Sequence[str] | None = "57690f3a1bbb"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "policies",
        sa.Column("policy_number", sa.String(length=32), nullable=False),
        sa.Column("form_id", sa.String(length=32), nullable=False),
        sa.Column("holder_name", sa.String(length=120), nullable=False),
        sa.Column("holder_email", sa.String(length=200), nullable=False),
        sa.Column("holder_phone", sa.String(length=40), nullable=False),
        sa.Column("holder_address", sa.String(length=300), nullable=False),
        sa.Column("effective_date", sa.Date(), nullable=False),
        sa.Column("expiry_date", sa.Date(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("vehicle", sa.JSON(), nullable=False),
        sa.Column("vehicle_acv", sa.Float(), nullable=False),
        sa.Column("collision_deductible", sa.Float(), nullable=True),
        sa.Column("comprehensive_deductible", sa.Float(), nullable=True),
        sa.PrimaryKeyConstraint("policy_number"),
    )
    op.create_index(op.f("ix_policies_form_id"), "policies", ["form_id"], unique=False)
    op.create_table(
        "policy_clauses",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("form_id", sa.String(length=32), nullable=False),
        sa.Column("clause_id", sa.String(length=32), nullable=False),
        sa.Column("section", sa.String(length=120), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("embedding", pgvector.sqlalchemy.vector.VECTOR(dim=384), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("form_id", "clause_id"),
    )
    op.create_index(op.f("ix_policy_clauses_form_id"), "policy_clauses", ["form_id"], unique=False)
    op.create_table(
        "claim_history_profiles",
        sa.Column("policy_number", sa.String(length=32), nullable=False),
        sa.Column("past_claims", sa.Integer(), nullable=False),
        sa.Column("days_policy_to_accident", sa.Integer(), nullable=False),
        sa.Column("address_change_recent", sa.Boolean(), nullable=False),
        sa.Column("police_report_filed", sa.Boolean(), nullable=False),
        sa.Column("witness_present", sa.Boolean(), nullable=False),
        sa.Column("fraud_label", sa.Boolean(), nullable=False),
        sa.Column("source_row", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["policy_number"], ["policies.policy_number"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("policy_number"),
    )
    op.execute(
        "CREATE INDEX ix_policy_clauses_embedding ON policy_clauses "
        "USING hnsw (embedding vector_cosine_ops)"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("claim_history_profiles")
    op.drop_index(op.f("ix_policy_clauses_form_id"), table_name="policy_clauses")
    op.drop_table("policy_clauses")
    op.drop_index(op.f("ix_policies_form_id"), table_name="policies")
    op.drop_table("policies")
