"""Tool tests run on in-memory SQLite. They prove the tools return correct facts and stay scoped."""

import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from supportpilot import models
from supportpilot.db import Base
from supportpilot.tools import TOOLS, ToolContext, get_customer_history, get_order_details, run_tool

NOW = datetime(2026, 11, 27, 12, 0, tzinfo=UTC)


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def add_order(db, customer, *, late_by=0, status="delivered"):
    placed = NOW - timedelta(hours=2)
    promised = placed + timedelta(minutes=40)
    order = models.Order(
        customer=customer,
        restaurant_name="Biryani House",
        status=status,
        total_amount=360,
        payment_method="upi",
        placed_at=placed,
        promised_at=promised,
        delivered_at=promised + timedelta(minutes=late_by) if status == "delivered" else None,
        items=[
            models.OrderItem(name="Chicken Biryani", quantity=1, price=300),
            models.OrderItem(name="Raita", quantity=1, price=60, packed=False),
        ],
    )
    db.add(order)
    db.flush()
    return order


@pytest.fixture
def world(db):
    alice = models.Customer(name="Alice", phone="9000000001")
    bob = models.Customer(name="Bob", phone="9000000002")
    db.add_all([alice, bob])
    db.flush()
    return {
        "alice": alice,
        "bob": bob,
        "alice_order": add_order(db, alice, late_by=40),
        "bob_order": add_order(db, bob),
    }


def test_order_details_shows_lateness_and_unpacked_item(db, world):
    ctx = ToolContext(db, world["alice"].id)
    result = get_order_details(ctx, world["alice_order"].id)
    assert result["minutes_late"] == 40
    assert result["delivery_minutes"] == 80
    assert result["total_amount_rupees"] == 360
    assert {i["name"]: i["packed"] for i in result["items"]} == {
        "Chicken Biryani": True,
        "Raita": False,
    }


def test_early_delivery_is_negative_minutes_late(db, world):
    order = add_order(db, world["alice"], late_by=-8)
    result = get_order_details(ToolContext(db, world["alice"].id), order.id)
    assert result["minutes_late"] == -8


def test_undelivered_order_has_no_lateness(db, world):
    order = add_order(db, world["alice"], status="cancelled")
    result = get_order_details(ToolContext(db, world["alice"].id), order.id)
    assert result["status"] == "cancelled"
    assert result["minutes_late"] is None
    assert result["delivered_at"] is None


def test_another_customers_order_looks_exactly_like_a_missing_order(db, world):
    ctx = ToolContext(db, world["alice"].id)
    someone_elses = get_order_details(ctx, world["bob_order"].id)
    nonexistent = get_order_details(ctx, 99999)
    assert "error" in someone_elses
    assert "items" not in someone_elses
    # Same shape of answer, so a probing model cannot tell the two cases apart.
    assert someone_elses["error"].replace(str(world["bob_order"].id), "N") == nonexistent[
        "error"
    ].replace("99999", "N")


def test_order_id_is_required(db, world):
    assert "error" in get_order_details(ToolContext(db, world["alice"].id), None)


def test_history_counts_orders_and_earlier_tickets_but_not_the_current_one(db, world):
    alice = world["alice"]
    for _ in range(2):
        db.add(models.Ticket(customer_id=alice.id, text="old"))
    current = models.Ticket(customer_id=alice.id, text="now")
    db.add(current)
    db.add(models.Ticket(customer_id=world["bob"].id, text="not alice"))
    db.flush()
    result = get_customer_history(ToolContext(db, alice.id, ticket_id=current.id))
    assert result == {"total_orders": 1, "previous_tickets": 2}


def test_run_tool_returns_json_text(db, world):
    out = run_tool(ToolContext(db, world["alice"].id), "get_order_details", world["alice_order"].id)
    assert json.loads(out)["restaurant"] == "Biryani House"


def test_unknown_tool_is_reported_not_raised(db, world):
    out = json.loads(run_tool(ToolContext(db, world["alice"].id), "delete_everything", None))
    assert "Unknown tool" in out["error"]
    assert "get_order_details" in out["error"]


def test_every_tool_is_read_only_by_name():
    for name in TOOLS:
        assert name.startswith("get_")
