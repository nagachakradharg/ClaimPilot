"""users claims audit

Revision ID: 9b221c5e2551
Revises: 87cd3a763d03
Create Date: 2026-11-26 15:55:13.267754

"""

from collections.abc import Sequence

import pgvector.sqlalchemy  # noqa: F401  (Vector columns)
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "9b221c5e2551"
down_revision: str | Sequence[str] | None = "87cd3a763d03"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "audit_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("claim_id", sa.String(length=32), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("actor", sa.String(length=64), nullable=False),
        sa.Column("latency_ms", sa.Float(), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("cost_usd", sa.Float(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_audit_events_claim_id"), "audit_events", ["claim_id"], unique=False)
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("email", sa.String(length=200), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("policy_number", sa.String(length=32), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email"),
    )
    op.create_index(op.f("ix_users_role"), "users", ["role"], unique=False)
    op.create_table(
        "claims",
        sa.Column("claim_id", sa.String(length=32), nullable=False),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("policy_number", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("decision", sa.String(length=20), nullable=True),
        sa.Column("final_payout", sa.Float(), nullable=True),
        sa.Column("review_reasons", sa.JSON(), nullable=False),
        sa.Column("core_claim_id", sa.String(length=32), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("processing_ms", sa.Float(), nullable=True),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["users.id"],
        ),
        sa.PrimaryKeyConstraint("claim_id"),
    )
    op.create_index(op.f("ix_claims_owner_id"), "claims", ["owner_id"], unique=False)
    op.create_index(op.f("ix_claims_status"), "claims", ["status"], unique=False)
    op.drop_index(
        op.f("ix_policy_clauses_embedding"),
        table_name="policy_clauses",
        postgresql_ops={"embedding": "vector_cosine_ops"},
        postgresql_using="hnsw",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.create_index(
        op.f("ix_policy_clauses_embedding"),
        "policy_clauses",
        ["embedding"],
        unique=False,
        postgresql_ops={"embedding": "vector_cosine_ops"},
        postgresql_using="hnsw",
    )
    op.drop_index(op.f("ix_claims_status"), table_name="claims")
    op.drop_index(op.f("ix_claims_owner_id"), table_name="claims")
    op.drop_table("claims")
    op.drop_index(op.f("ix_users_role"), table_name="users")
    op.drop_table("users")
    op.drop_index(op.f("ix_audit_events_claim_id"), table_name="audit_events")
    op.drop_table("audit_events")
