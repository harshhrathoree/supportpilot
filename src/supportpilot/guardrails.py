"""Guardrails: deterministic rules that run AFTER the model and have the final say.

The model can suggest an action and an amount, but it can be wrong or be tricked by a ticket
that says "refund me Rs 5000". So the amount that is actually proposed is never taken from the
model. It is calculated here from the order data and the policy tables, and the model's
suggestion is only allowed to lower it. This code contains no AI, so it behaves the same every
time and can be tested exactly.

The numbers mirror the markdown files in policies/. If you change a policy, change it here too.
"""

from dataclasses import dataclass, field
from typing import Literal

Action = Literal["reply_only", "propose_compensation", "escalate"]
Role = Literal["support_agent", "team_lead", "operations_manager"]

# Higher number = more authority. A higher role may approve anything a lower role may.
ROLE_RANK: dict[str, int] = {"support_agent": 1, "team_lead": 2, "operations_manager": 3}

# policies/refund-approval-limits.md
SUPPORT_AGENT_LIMIT = 200
TEAM_LEAD_LIMIT = 500

# policies/repeat-refund-review.md
REPEAT_TICKET_LIMIT = 5

# Only these categories can ever get an automatic compensation proposal. For everything else the
# policy needs something the data cannot give us (a photo, a payment record, a human check),
# so the best the AI can do is reply or escalate.
COMPENSATION_CATEGORIES = {"late_delivery", "missing_item"}


@dataclass
class GuardrailResult:
    action: Action
    amount: int
    allowed_amount: int  # the ceiling computed from data and policy
    required_approver: Role
    flags: list[str] = field(default_factory=list)  # every way we changed the model's suggestion


def required_approver(amount: int) -> Role:
    """The lowest role that may approve this amount (policies/refund-approval-limits.md)."""
    if amount <= SUPPORT_AGENT_LIMIT:
        return "support_agent"
    if amount <= TEAM_LEAD_LIMIT:
        return "team_lead"
    return "operations_manager"


def can_approve(role: str, needed: str) -> bool:
    return ROLE_RANK.get(role, 0) >= ROLE_RANK[needed]


def late_delivery_allowed(order: dict) -> int:
    """policies/late-delivery.md. Uses minutes_late from our data, never the customer's claim."""
    if order.get("status") != "delivered" or order.get("minutes_late") is None:
        return 0
    late, total = order["minutes_late"], order["total_amount_rupees"]
    if late <= 15:
        return 0
    if late <= 30:
        return min(total * 10 // 100, 50)
    if late <= 60:
        return min(total * 20 // 100, 100)
    return min(total * 50 // 100, 300)


def missing_item_allowed(order: dict) -> int:
    """policies/missing-item.md. Refund only items the restaurant did not pack."""
    if order.get("status") != "delivered":
        return 0
    return sum(
        i["price_each_rupees"] * i["quantity"] for i in order.get("items", []) if not i["packed"]
    )


def compute_allowed(category: str, order: dict | None) -> int:
    if order is None or "error" in order:
        return 0
    if category == "late_delivery":
        return late_delivery_allowed(order)
    if category == "missing_item":
        return missing_item_allowed(order)
    return 0


def apply_guardrails(
    *,
    category: str,
    agent_status: str,
    verdict: str | None,
    order: dict | None,
    previous_tickets: int,
    requested_action: Action,
    requested_amount: int,
) -> GuardrailResult:
    """Turn the model's suggestion into what we are willing to put in front of a human."""
    flags: list[str] = []
    allowed = compute_allowed(category, order)

    action: Action = requested_action
    amount = max(requested_amount, 0)

    def escalate(reason: str) -> GuardrailResult:
        flags.append(reason)
        return GuardrailResult("escalate", 0, allowed, "team_lead", flags)

    # Rules that send the ticket to a human with no money proposed at all.
    if agent_status != "finished":
        return escalate(f"agent did not finish ({agent_status})")
    if previous_tickets >= REPEAT_TICKET_LIMIT:
        return escalate(f"customer has {previous_tickets} earlier tickets: manual review")
    if requested_action == "escalate":
        return escalate("agent asked to escalate")

    if action == "propose_compensation":
        if category not in COMPENSATION_CATEGORIES:
            return escalate(f"category {category} cannot get automatic compensation")
        if verdict != "supported":
            flags.append(f"verdict is {verdict}: compensation removed")
            action, amount = "reply_only", 0
        elif allowed == 0:
            flags.append("data shows nothing to compensate: compensation removed")
            action, amount = "reply_only", 0
        elif amount > allowed:
            flags.append(
                f"requested Rs {amount} is above the policy ceiling: lowered to Rs {allowed}"
            )
            amount = allowed
        elif amount == 0:
            flags.append("agent proposed compensation with no amount: set to the policy amount")
            amount = allowed
    else:
        if amount != 0:
            flags.append("amount ignored because the action is reply_only")
        amount = 0

    return GuardrailResult(action, amount, allowed, required_approver(amount), flags)
