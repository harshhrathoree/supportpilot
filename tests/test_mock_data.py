from datetime import timedelta

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from supportpilot import models
from supportpilot.db import Base
from supportpilot.mock_data import N_CUSTOMERS, N_ORDERS, ORDER_TOKEN, TICKET_MIX, generate
from supportpilot.seed import load


@pytest.fixture
def session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def all_orders(data):
    return [o for c in data.customers for o in c.orders]


def test_counts_match_the_plan():
    data = generate()
    assert len(data.customers) == N_CUSTOMERS
    assert len(all_orders(data)) == N_ORDERS
    for category, count in TICKET_MIX.items():
        assert sum(t.category == category for t in data.tickets) == count


def test_same_seed_gives_same_data_and_different_seed_differs():

    def texts(seed):
        return [t.template for t in generate(seed).tickets]

    assert texts(1) == texts(1)
    assert texts(1) != texts(2)


def test_late_tickets_have_late_orders_and_everything_else_is_on_time():
    data = generate()
    late_orders = {id(t.order) for t in data.tickets if t.category == "late_delivery"}
    for order in all_orders(data):
        if order.status != "delivered":
            continue
        delay = order.delivered_at - order.promised_at
        if id(order) in late_orders:
            assert delay >= timedelta(minutes=25)
        else:
            assert delay <= timedelta(minutes=5)


def test_missing_item_tickets_have_exactly_one_unpacked_item_named_in_the_text():
    data = generate()
    specs = [t for t in data.tickets if t.category == "missing_item"]
    for spec in specs:
        unpacked = [i.name for i in spec.order.items if not i.packed]
        assert len(unpacked) == 1
        assert unpacked[0].lower() in spec.template.lower()


def test_refund_status_orders_are_cancelled_and_other_tickets_have_no_order():
    data = generate()
    for spec in data.tickets:
        if spec.category == "refund_status":
            assert spec.order.status == "cancelled"
        if spec.category == "other":
            assert spec.order is None


def test_load_saves_everything_and_labels_point_at_real_tickets(session):
    data = generate()
    labels = load(session, data)
    session.commit()

    assert session.scalar(select(func.count()).select_from(models.Ticket)) == 100
    assert session.scalar(select(func.count()).select_from(models.Order)) == N_ORDERS
    assert len(labels) == 100
    for row in labels:
        ticket = session.get(models.Ticket, row["ticket_id"])
        assert ticket.text == row["text"]
        assert ORDER_TOKEN not in ticket.text
        assert ticket.order_id == row["order_id"]


def test_load_twice_replaces_instead_of_duplicating(session):
    load(session, generate())
    load(session, generate())
    session.commit()
    assert session.scalar(select(func.count()).select_from(models.Ticket)) == 100
    assert session.scalar(select(func.count()).select_from(models.Customer)) == N_CUSTOMERS


def test_reseeding_keeps_the_policy_embeddings(session):
    session.add(
        models.PolicyChunk(
            slug="p", title="P", content="# P", content_hash="h", embedding=[0.0] * 768
        )
    )
    session.flush()
    load(session, generate())
    session.commit()
    assert session.scalar(select(func.count()).select_from(models.PolicyChunk)) == 1
