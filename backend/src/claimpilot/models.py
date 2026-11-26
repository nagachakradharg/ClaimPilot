"""ORM models. All data is synthetic (policies, holders) or derived from CC0 data (history)."""

from __future__ import annotations

from datetime import date, datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from claimpilot.config import EMBEDDING_DIM


class Base(DeclarativeBase):
    pass


class Policy(Base):
    __tablename__ = "policies"

    policy_number: Mapped[str] = mapped_column(String(32), primary_key=True)
    form_id: Mapped[str] = mapped_column(String(32), index=True)
    holder_name: Mapped[str] = mapped_column(String(120))
    holder_email: Mapped[str] = mapped_column(String(200))
    holder_phone: Mapped[str] = mapped_column(String(40))
    holder_address: Mapped[str] = mapped_column(String(300))
    effective_date: Mapped[date] = mapped_column(Date)
    expiry_date: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(16), default="active")  # active | cancelled
    vehicle: Mapped[dict] = mapped_column(JSON)  # year, make, model, vin, category
    vehicle_acv: Mapped[float] = mapped_column(Float)  # actual cash value, USD
    collision_deductible: Mapped[float | None] = mapped_column(Float, nullable=True)
    comprehensive_deductible: Mapped[float | None] = mapped_column(Float, nullable=True)


class PolicyClause(Base):
    __tablename__ = "policy_clauses"
    __table_args__ = (UniqueConstraint("form_id", "clause_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    form_id: Mapped[str] = mapped_column(String(32), index=True)
    clause_id: Mapped[str] = mapped_column(String(32))
    section: Mapped[str] = mapped_column(String(120))
    title: Mapped[str] = mapped_column(String(200))
    text: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIM))


class ClaimHistoryProfile(Base):
    """Per-policy claim-history signals, mapped from the CC0 Kaggle vehicle fraud dataset."""

    __tablename__ = "claim_history_profiles"

    policy_number: Mapped[str] = mapped_column(
        ForeignKey("policies.policy_number", ondelete="CASCADE"), primary_key=True
    )
    past_claims: Mapped[int] = mapped_column(Integer)
    days_policy_to_accident: Mapped[int] = mapped_column(Integer)
    address_change_recent: Mapped[bool] = mapped_column(Boolean)
    police_report_filed: Mapped[bool] = mapped_column(Boolean)
    witness_present: Mapped[bool] = mapped_column(Boolean)
    fraud_label: Mapped[bool] = mapped_column(Boolean)
    source_row: Mapped[int] = mapped_column(Integer)


class User(Base):
    """Seeded demo users (no signup in v1). Roles: claimant | adjuster | auditor."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(200), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    role: Mapped[str] = mapped_column(String(16), index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    policy_number: Mapped[str | None] = mapped_column(String(32), nullable=True)  # claimants


class ClaimRecord(Base):
    """List/index row per claim. The full run state lives in the LangGraph checkpointer."""

    __tablename__ = "claims"

    claim_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    policy_number: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(20), index=True)
    decision: Mapped[str | None] = mapped_column(String(20), nullable=True)
    final_payout: Mapped[float | None] = mapped_column(Float, nullable=True)
    review_reasons: Mapped[list] = mapped_column(JSON, default=list)
    core_claim_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Adjuster (email) who owns the claim's review; None = in the shared queue.
    assigned_to: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    processing_ms: Mapped[float | None] = mapped_column(Float, nullable=True)  # excl. human wait


class AuditEvent(Base):
    """Append-only audit trail: nodes, model calls, tool calls, human actions.
    `data` is PII-redacted before it is written."""

    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    claim_id: Mapped[str] = mapped_column(String(32), index=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    kind: Mapped[str] = mapped_column(String(16))  # node_start|node_end|llm|tool|human|status
    name: Mapped[str] = mapped_column(String(64))
    actor: Mapped[str] = mapped_column(String(64), default="system")
    latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    data: Mapped[dict] = mapped_column(JSON, default=dict)
