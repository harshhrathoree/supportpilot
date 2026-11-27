"""Tools the agent can call. All of them are READ-ONLY and limited to one customer.

Why read-only: the agent can look things up, but it cannot change anything. Refunds are only
ever proposed by the agent and executed after a human approves (Step 7).

Why scoped: the customer id comes from the ticket, set by OUR code in `ToolContext`. The model
only chooses an order id. If that order belongs to someone else, the tool answers exactly as if
the order did not exist, so a tricked model cannot read other customers' data.
"""

import json
from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from supportpilot.models import Order, Ticket


@dataclass
class ToolContext:
    session: Session
    customer_id: int  # taken from the ticket, never from the model
    ticket_id: int | None = None  # the ticket being handled, left out of the history counts


def get_order_details(ctx: ToolContext, order_id: int | None) -> dict:
    if order_id is None:
        return {"error": "order_id is required for this tool"}
    order = ctx.session.get(Order, order_id)
    # Same message for "missing" and "belongs to someone else", so existence is not leaked.
    if order is None or order.customer_id != ctx.customer_id:
        return {"error": f"Order {order_id} not found for this customer"}

    minutes_late = None
    delivery_minutes = None
    if order.delivered_at is not None:
        minutes_late = round((order.delivered_at - order.promised_at).total_seconds() / 60)
        delivery_minutes = round((order.delivered_at - order.placed_at).total_seconds() / 60)

    return {
        "order_id": order.id,
        "restaurant": order.restaurant_name,
        "status": order.status,
        "payment_method": order.payment_method,
        "total_amount_rupees": order.total_amount,
        "placed_at": order.placed_at.isoformat(),
        "promised_at": order.promised_at.isoformat(),
        "delivered_at": order.delivered_at.isoformat() if order.delivered_at else None,
        "minutes_late": minutes_late,  # positive = late, negative = early, null = not delivered
        "delivery_minutes": delivery_minutes,
        "items": [
            {
                "name": i.name,
                "quantity": i.quantity,
                "price_each_rupees": i.price,
                "packed": i.packed,  # false = the restaurant did not pack it
            }
            for i in order.items
        ],
    }


def get_customer_history(ctx: ToolContext, order_id: int | None = None) -> dict:
    orders = ctx.session.scalar(
        select(func.count()).select_from(Order).where(Order.customer_id == ctx.customer_id)
    )
    tickets = select(func.count()).select_from(Ticket).where(Ticket.customer_id == ctx.customer_id)
    if ctx.ticket_id is not None:
        tickets = tickets.where(Ticket.id != ctx.ticket_id)
    return {"total_orders": orders, "previous_tickets": ctx.session.scalar(tickets)}


@dataclass(frozen=True)
class ToolSpec:
    description: str
    fn: Callable[[ToolContext, int | None], dict]


# The single list of tools. The prompt and the dispatcher both read from here, so they can't
# disagree. To add a tool, add one entry.
TOOLS: dict[str, ToolSpec] = {
    "get_order_details": ToolSpec(
        "Facts about one of this customer's orders: status, payment, promised and actual "
        "delivery time, minutes late, and every item with whether it was packed. "
        "Needs order_id.",
        get_order_details,
    ),
    "get_customer_history": ToolSpec(
        "How many orders this customer has placed and how many earlier tickets they filed. "
        "Takes no arguments.",
        get_customer_history,
    ),
}


def run_tool(ctx: ToolContext, name: str, order_id: int | None) -> str:
    """Run a tool by name and return the result as JSON text for the model to read."""
    spec = TOOLS.get(name)
    if spec is None:
        result: dict = {"error": f"Unknown tool {name!r}. Available: {', '.join(TOOLS)}"}
    else:
        result = spec.fn(ctx, order_id)
    return json.dumps(result)
