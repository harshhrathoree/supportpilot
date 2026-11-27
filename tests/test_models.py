"""Model tests run on in-memory SQLite, so they are fast and never touch the Neon database."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from supportpilot import models
from supportpilot.db import Base, to_driver_url


@pytest.fixture
def session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def test_to_driver_url_adds_psycopg_driver():
    assert to_driver_url("postgresql://u:p@h/db") == "postgresql+psycopg://u:p@h/db"
    assert to_driver_url("postgres://u:p@h/db") == "postgresql+psycopg://u:p@h/db"
    assert to_driver_url("sqlite://") == "sqlite://"


def test_ticket_links_to_order_and_items(session):
    now = datetime.now(UTC)
    customer = models.Customer(name="Asha", phone="9999900000")
    order = models.Order(
        customer=customer,
        restaurant_name="Biryani House",
        status="delivered",
        total_amount=450,
        payment_method="upi",
        placed_at=now - timedelta(minutes=90),
        promised_at=now - timedelta(minutes=50),
        delivered_at=now - timedelta(minutes=30),
    )
    order.items = [
        models.OrderItem(name="Chicken Biryani", quantity=1, price=300),
        models.OrderItem(name="Raita", quantity=1, price=60, packed=False),
    ]
    session.add(order)
    session.flush()
    ticket = models.Ticket(customer_id=customer.id, order_id=order.id, text="Raita was missing")
    session.add(ticket)
    session.commit()

    saved = session.get(models.Ticket, ticket.id)
    assert saved.status == "open"
    assert saved.category is None
    unpacked = [i.name for i in session.get(models.Order, saved.order_id).items if not i.packed]
    assert unpacked == ["Raita"]
