"""Table definitions. Each class is one table; each attribute is one column."""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

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
    # One of: open, awaiting_approval, resolved
    status: Mapped[str] = mapped_column(String(20), default="open")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
