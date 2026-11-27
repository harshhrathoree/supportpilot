"""Table definitions. Each class is one table; each attribute is one column."""

from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from supportpilot.config import settings
from supportpilot.db import Base


class Customer(Base):
    __tablename__ = "customers"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    phone: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    orders: Mapped[list["Order"]] = relationship(back_populates="customer")


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), index=True)
    restaurant_name: Mapped[str] = mapped_column(String(100))
    # One of: placed, delivered, cancelled
    status: Mapped[str] = mapped_column(String(20))
    total_amount: Mapped[int] = mapped_column(Integer)  # rupees
    payment_method: Mapped[str] = mapped_column(String(20))  # upi, card, cod
    placed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    promised_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))  # the ETA we told them
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    customer: Mapped[Customer] = relationship(back_populates="orders")
    items: Mapped[list["OrderItem"]] = relationship(back_populates="order")


class OrderItem(Base):
    __tablename__ = "order_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    price: Mapped[int] = mapped_column(Integer)  # rupees, per unit
    # False means the restaurant did not pack it: evidence for a "missing item" complaint.
    packed: Mapped[bool] = mapped_column(default=True)

    order: Mapped[Order] = relationship(back_populates="items")


class Ticket(Base):
    __tablename__ = "tickets"

    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), index=True)
    order_id: Mapped[int | None] = mapped_column(ForeignKey("orders.id"))
    text: Mapped[str] = mapped_column(Text)  # the customer's message (untrusted input!)
    # Filled in by the classifier in Step 4. Null until then.
    category: Mapped[str | None] = mapped_column(String(30))
    # One of: open, awaiting_approval, resolved, manual_handling (a human rejected the AI's draft)
    status: Mapped[str] = mapped_column(String(20), default="open")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PolicyChunk(Base):
    """One policy document and its embedding. Each policy is short, so one document = one chunk."""

    __tablename__ = "policy_chunks"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(100), unique=True)  # file name without .md
    title: Mapped[str] = mapped_column(String(200))
    content: Mapped[str] = mapped_column(Text)
    # Fingerprint of the content. If it hasn't changed we skip re-embedding (saves API calls).
    content_hash: Mapped[str] = mapped_column(String(64))
    # pgvector column on Postgres. Tests run on SQLite, which has no vector type, so there it
    # is stored as a JSON list of numbers instead.
    embedding: Mapped[list[float]] = mapped_column(
        Vector(settings.embedding_dims).with_variant(JSON(), "sqlite")
    )


class Proposal(Base):
    """What the AI suggests for a ticket, and what the human decided. One per ticket."""

    __tablename__ = "proposals"

    id: Mapped[int] = mapped_column(primary_key=True)
    # unique: a ticket is processed once. A second attempt hits this constraint.
    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id"), unique=True)
    # finished, or why the AI did not complete: step_limit, budget_exceeded, llm_error
    agent_status: Mapped[str] = mapped_column(String(20))
    verdict: Mapped[str | None] = mapped_column(String(20))  # supported, contradicted, unverifiable
    evidence: Mapped[list[str]] = mapped_column(JSON, default=list)
    draft_reply: Mapped[str] = mapped_column(Text)
    policy_slug: Mapped[str | None] = mapped_column(String(100))
    # reply_only, propose_compensation or escalate. Already corrected by the guardrails.
    action: Mapped[str] = mapped_column(String(30))
    requested_amount: Mapped[int] = mapped_column(Integer, default=0)  # what the model asked for
    allowed_amount: Mapped[int | None] = mapped_column(Integer)  # ceiling computed by our code
    amount: Mapped[int] = mapped_column(Integer, default=0)  # what is proposed after guardrails
    required_approver: Mapped[str] = mapped_column(String(20))
    flags: Mapped[list[str]] = mapped_column(JSON, default=list)  # why the guardrails changed it
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # Filled in when a human decides. status is one of: pending, approved, edited, rejected
    status: Mapped[str] = mapped_column(String(20), default="pending")
    decided_by: Mapped[str | None] = mapped_column(String(100))
    decided_role: Mapped[str | None] = mapped_column(String(20))
    final_reply: Mapped[str | None] = mapped_column(Text)
    final_amount: Mapped[int | None] = mapped_column(Integer)
    decision_note: Mapped[str | None] = mapped_column(Text)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuditEvent(Base):
    """Append-only history of what happened to a ticket. Rows are added, never edited."""

    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id"), index=True)
    event: Mapped[str] = mapped_column(String(40))  # proposal_created, approved, edited, rejected
    actor: Mapped[str] = mapped_column(String(100))  # "ai" or the human's name
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
