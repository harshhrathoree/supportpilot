"""Guardrail tests. This code has no AI in it, so every rule can be tested exactly."""

import pytest

from supportpilot.guardrails import (
    apply_guardrails,
    can_approve,
    compute_allowed,
    late_delivery_allowed,
    missing_item_allowed,
    required_approver,
)


def order(**kw):
    base = {"status": "delivered", "minutes_late": 0, "total_amount_rupees": 500, "items": []}
    return {**base, **kw}


def item(name, price, qty=1, packed=True):
    return {"name": name, "price_each_rupees": price, "quantity": qty, "packed": packed}


def run(**kw):
    defaults = {
        "category": "late_delivery",
        "agent_status": "finished",
        "verdict": "supported",
        "order": order(minutes_late=40),
        "previous_tickets": 0,
        "requested_action": "propose_compensation",
        "requested_amount": 100,
    }
    return apply_guardrails(**{**defaults, **kw})


# --- the policy tables ---


@pytest.mark.parametrize(
    ("minutes_late", "total", "expected"),
    [
        (0, 500, 0),
        (-8, 500, 0),
        (15, 500, 0),  # 15 or fewer counts as on time
        (16, 500, 50),  # 10% of 500 = 50, cap 50
        (16, 300, 30),  # 10% of 300 = 30, under the cap
        (30, 900, 50),  # cap applies
        (31, 400, 80),  # 20% of 400 = 80, under the cap of 100
        (60, 900, 100),  # cap applies
        (61, 400, 200),  # 50% of 400 = 200, under the cap of 300
        (120, 2000, 300),  # cap applies
    ],
)
def test_late_delivery_ceiling_follows_the_policy_table(minutes_late, total, expected):
    assert (
        late_delivery_allowed(order(minutes_late=minutes_late, total_amount_rupees=total))
        == expected
    )


def test_late_delivery_ceiling_is_zero_when_not_delivered():
    assert late_delivery_allowed(order(status="cancelled", minutes_late=None)) == 0


def test_missing_item_ceiling_is_price_times_quantity_of_unpacked_items_only():
    items = [
        item("Biryani", 300),
        item("Raita", 60, qty=2, packed=False),
        item("Naan", 40, packed=False),
    ]
    assert missing_item_allowed(order(items=items)) == 160
    assert missing_item_allowed(order(items=[item("Biryani", 300)])) == 0


def test_no_ceiling_for_categories_that_need_a_human_or_a_photo():
    assert compute_allowed("food_quality", order(items=[item("x", 100, packed=False)])) == 0
    assert compute_allowed("late_delivery", None) == 0
    assert compute_allowed("late_delivery", {"error": "not found"}) == 0


# --- approver tiers ---


@pytest.mark.parametrize(
    ("amount", "role"),
    [(0, "support_agent"), (200, "support_agent"), (201, "team_lead"), (500, "team_lead"),
     (501, "operations_manager")],
)  # fmt: skip
def test_required_approver_tiers(amount, role):
    assert required_approver(amount) == role


def test_seniority_ordering():
    assert can_approve("operations_manager", "support_agent")
    assert can_approve("team_lead", "team_lead")
    assert not can_approve("support_agent", "team_lead")
    assert not can_approve("someone_made_up", "support_agent")


# --- the model's suggestion is checked, never trusted ---


def test_a_correct_suggestion_passes_through_unchanged():
    result = run(order=order(minutes_late=40), requested_amount=100)
    assert (result.action, result.amount, result.flags) == ("propose_compensation", 100, [])


def test_a_request_above_the_ceiling_is_lowered_and_flagged():
    result = run(requested_amount=5000)  # the "refund me Rs 5000" attack
    assert result.amount == 100  # 40 minutes late on Rs 500 = 20% capped at 100
    assert result.allowed_amount == 100
    assert result.required_approver == "support_agent"
    assert any("above the policy ceiling" in f for f in result.flags)


def test_a_lower_request_is_kept_because_the_model_may_only_lower_the_amount():
    assert run(requested_amount=40).amount == 40


def test_compensation_with_no_amount_is_set_to_the_policy_amount():
    result = run(requested_amount=0)
    assert result.amount == 100
    assert any("no amount" in f for f in result.flags)


def test_compensation_is_removed_when_the_data_does_not_support_the_claim():
    result = run(verdict="contradicted")
    assert (result.action, result.amount) == ("reply_only", 0)
    assert any("compensation removed" in f for f in result.flags)


def test_compensation_is_removed_when_the_order_was_actually_on_time():
    result = run(order=order(minutes_late=3), requested_amount=200)
    assert (result.action, result.amount) == ("reply_only", 0)


def test_a_made_up_amount_for_a_missing_item_is_capped_at_the_unpacked_price():
    items = [item("Biryani", 300), item("Raita", 60, packed=False)]
    result = run(category="missing_item", order=order(items=items), requested_amount=360)
    assert result.amount == 60


def test_categories_that_need_a_photo_or_payment_record_can_never_get_money():
    for category in ["food_quality", "wrong_item", "payment_issue", "refund_status", "other"]:
        result = run(category=category, requested_amount=300)
        assert (result.action, result.amount) == ("escalate", 0), category


def test_reply_only_never_carries_an_amount():
    result = run(requested_action="reply_only", requested_amount=300)
    assert (result.action, result.amount) == ("reply_only", 0)
    assert any("amount ignored" in f for f in result.flags)


def test_negative_amounts_are_clamped_to_zero():
    assert run(requested_action="reply_only", requested_amount=-50).amount == 0


# --- escalation rules that need no judgement ---


def test_an_unfinished_agent_run_goes_to_a_human_with_no_money():
    for status in ["step_limit", "budget_exceeded", "llm_error"]:
        result = run(agent_status=status, verdict=None)
        assert (result.action, result.amount, result.required_approver) == (
            "escalate",
            0,
            "team_lead",
        )


def test_customers_with_many_earlier_tickets_are_escalated_not_compensated():
    assert run(previous_tickets=4).action == "propose_compensation"
    result = run(previous_tickets=5)
    assert (result.action, result.amount) == ("escalate", 0)
    assert any("manual review" in f for f in result.flags)


def test_agent_asking_to_escalate_is_respected():
    result = run(requested_action="escalate", requested_amount=0)
    assert result.action == "escalate"
