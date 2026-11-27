"""Generate fake but internally consistent customers, orders and support tickets.

Nothing here touches the database. `generate()` returns plain Python objects, so it is easy
to test. `seed.py` is what saves them.

Key idea: each ticket is built FROM an order whose data matches the complaint. A "late
delivery" ticket always has an order that really arrived late, a "missing item" ticket has
an item with packed=False, and so on. That makes the category label trustworthy ground truth,
and lets the agent later verify claims against real data.
"""

import random
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from supportpilot.models import Customer, Order, OrderItem

# A fixed "now" keeps the data reproducible, no matter when the generator runs.
ANCHOR = datetime(2026, 11, 27, 12, 0, tzinfo=UTC)

N_CUSTOMERS = 50
N_ORDERS = 500

# How many tickets of each category to create. The total is 100.
TICKET_MIX = {
    "late_delivery": 18,
    "missing_item": 18,
    "wrong_item": 12,
    "food_quality": 14,
    "payment_issue": 12,
    "refund_status": 10,
    "rider_behavior": 8,
    "other": 8,
}

# Templates are written to be replaced with the order id after the order is saved,
# because the database picks the id. Lowercase on purpose: lowercasing a text must not break it.
ORDER_TOKEN = "<order>"

MENUS = {
    "Biryani House": {
        "Chicken Biryani": 300,
        "Mutton Biryani": 380,
        "Veg Biryani": 240,
        "Raita": 60,
        "Gulab Jamun": 80,
    },
    "Pizza Corner": {
        "Margherita Pizza": 280,
        "Farmhouse Pizza": 360,
        "Garlic Bread": 130,
        "Coke": 60,
        "Choco Lava Cake": 110,
    },
    "Dosa Darbar": {
        "Masala Dosa": 120,
        "Idli Sambar": 90,
        "Filter Coffee": 50,
        "Vada": 70,
        "Rava Dosa": 130,
    },
    "Wok Express": {
        "Veg Hakka Noodles": 200,
        "Chilli Chicken": 260,
        "Fried Rice": 190,
        "Spring Rolls": 150,
        "Manchow Soup": 120,
    },
    "Burger Barn": {
        "Aloo Tikki Burger": 120,
        "Chicken Burger": 190,
        "French Fries": 100,
        "Peri Peri Fries": 130,
        "Chocolate Shake": 150,
    },
    "Tandoor Tales": {
        "Butter Chicken": 340,
        "Paneer Tikka": 280,
        "Garlic Naan": 60,
        "Dal Makhani": 220,
        "Jeera Rice": 140,
    },
}

FIRST_NAMES = [
    "Asha", "Rahul", "Priya", "Vikram", "Neha", "Arjun", "Sneha", "Karan", "Meera", "Rohan",
    "Divya", "Aditya", "Pooja", "Sanjay", "Anita",
]  # fmt: skip
LAST_NAMES = ["Sharma", "Verma", "Iyer", "Reddy", "Patel", "Singh", "Nair", "Gupta", "Das", "Rao"]

TEMPLATES = {
    "late_delivery": [
        "My order #<order> from {restaurant} came {mins} minutes later than promised. Unacceptable.",
        "The food from {restaurant} arrived way later than the time you showed me. Please look into it.",
        "Why was my delivery so delayed? Order <order> took {mins} minutes longer than the ETA.",
        "Rider took forever. I was starving and it arrived {mins} min after the promised time.",
        "Late delivery again!! order number <order>. I want compensation.",
    ],
    "missing_item": [
        "{item} was missing from my order #<order>. I paid for it!",
        "Opened the bag from {restaurant} and there is no {item}. Please refund that item.",
        "one item missing in order <order> - {item}",
        "Hi, my {item} wasn't in the package. Order #<order>. Please fix.",
        "you charged me for {item} but it never came",
    ],
    "wrong_item": [
        "I ordered {item} but got {other} instead. Order #<order>.",
        "Wrong food delivered! Asked for {item}, received {other}.",
        "This is not what I ordered. You sent {other} from {restaurant} instead of {item}.",
        "order <order> has the wrong dish, I got {other} rather than {item}",
        "received the wrong order? I got {other}, not {item}.",
    ],
    "food_quality": [
        "The {item} from {restaurant} was completely cold when it arrived. Order #<order>.",
        "food was stale and tasted bad, I couldn't eat the {item}.",
        "My {item} was soggy and undercooked. Very disappointed with order <order>.",
        "Found a hair in my {item}!! This is disgusting. order #<order>",
        "the {item} was way too oily and smelled off",
    ],
    "payment_issue": [
        "I was charged twice for order #<order>. Please reverse the extra payment.",
        "Money got deducted from my account twice for the same order.",
        "Payment of Rs {amount} went through but I did not get any confirmation. Order <order>.",
        "applied a coupon but the full amount was charged for order #<order>",
        "my card was debited two times. fix this asap",
    ],
    "refund_status": [
        "Where is my refund for order #<order>? It's been 5 days.",
        "I cancelled order <order> but the money has not come back yet.",
        "refund status please. order #<order> was cancelled",
        "Still waiting for my refund of Rs {amount} from {restaurant}. When will it arrive?",
        "how long will the refund take for my cancelled order",
    ],
    "rider_behavior": [
        "The delivery guy was rude and shouted at me when I asked about my order #<order>.",
        "Rider asked me to come downstairs to collect and was very impolite.",
        "delivery partner misbehaved with my mother. Order <order>. Take action.",
        "The rider marked the order delivered and then called me abusive words.",
        "rider refused to bring the food to my door and argued with me",
    ],
    "other": [
        "How do I change my registered phone number?",
        "Your app keeps crashing when I open the cart.",
        "Can I add a new delivery address for my office?",
        "I want to delete my account, how do I do that?",
        "Do you deliver to Whitefield after 11 pm?",
    ],
}


@dataclass
class TicketSpec:
    """A ticket that is not saved yet. Its text still contains the order id placeholder."""

    customer: Customer
    order: Order | None
    category: str  # the ground-truth label
    template: str

    def render(self, order_id: int | None) -> str:
        return self.template.replace(ORDER_TOKEN, str(order_id))


@dataclass
class MockData:
    customers: list[Customer]  # each customer holds its orders, each order holds its items
    tickets: list[TicketSpec]


def make_customer(rng: random.Random) -> Customer:
    name = f"{rng.choice(FIRST_NAMES)} {rng.choice(LAST_NAMES)}"
    phone = "9" + "".join(str(rng.randint(0, 9)) for _ in range(9))
    return Customer(name=name, phone=phone)


def make_order(
    rng: random.Random,
    customer: Customer,
    *,
    late_by: int | None = None,
    status: str = "delivered",
    payment_method: str | None = None,
    drop_item: bool = False,
) -> Order:
    """Build one order.

    late_by: minutes after the promised time that it arrived. None means on time (or a bit early).
    drop_item: mark one item as not packed, which is the evidence for a missing-item complaint.
    """
    restaurant = rng.choice(list(MENUS))
    menu = MENUS[restaurant]
    items = [
        OrderItem(name=name, quantity=rng.randint(1, 2), price=menu[name], packed=True)
        for name in rng.sample(list(menu), rng.randint(2, 4))
    ]
    if drop_item:
        rng.choice(items).packed = False

    placed_at = ANCHOR - timedelta(
        days=rng.randint(0, 29), hours=rng.randint(0, 5), minutes=rng.randint(0, 59)
    )
    promised_at = placed_at + timedelta(minutes=rng.randint(30, 45))
    delivered_at = None
    if status == "delivered":
        delay = late_by if late_by is not None else rng.randint(-10, 5)
        delivered_at = promised_at + timedelta(minutes=delay)

    return Order(
        customer=customer,
        restaurant_name=restaurant,
        status=status,
        total_amount=sum(i.price * i.quantity for i in items),
        payment_method=payment_method or rng.choice(["upi", "card", "cod"]),
        placed_at=placed_at,
        promised_at=promised_at,
        delivered_at=delivered_at,
        items=items,
    )


def make_ticket(rng: random.Random, category: str, customers: list[Customer]) -> TicketSpec:
    customer = rng.choice(customers)
    template = rng.choice(TEMPLATES[category])

    order = None
    fields: dict[str, object] = {}
    if category != "other":  # "other" tickets (app questions) have no order
        late_by = rng.randint(25, 70) if category == "late_delivery" else None
        order = make_order(
            rng,
            customer,
            late_by=late_by,
            status="cancelled" if category == "refund_status" else "delivered",
            payment_method=(
                rng.choice(["upi", "card"])
                if category in ("payment_issue", "refund_status")
                else None
            ),
            drop_item=category == "missing_item",
        )
        item = (
            next(i for i in order.items if not i.packed)
            if category == "missing_item"
            else rng.choice(order.items)
        )
        not_ordered = [
            n for n in MENUS[order.restaurant_name] if n not in {i.name for i in order.items}
        ]
        fields = {
            "restaurant": order.restaurant_name,
            "amount": order.total_amount,
            "mins": late_by,
            "item": item.name,
            "other": rng.choice(not_ordered),
        }

    text = template.format(**fields)
    if rng.random() < 0.2:  # real customers often type in lowercase
        text = text.lower()
    return TicketSpec(customer=customer, order=order, category=category, template=text)


def generate(seed: int = 42) -> MockData:
    """Build the full dataset. The same seed always gives the same data."""
    rng = random.Random(seed)
    customers = [make_customer(rng) for _ in range(N_CUSTOMERS)]

    tickets = [
        make_ticket(rng, category, customers)
        for category, count in TICKET_MIX.items()
        for _ in range(count)
    ]
    rng.shuffle(tickets)

    # Fill up to N_ORDERS with normal orders that have no complaint.
    n_background = N_ORDERS - sum(1 for t in tickets if t.order is not None)
    for _ in range(n_background):
        make_order(
            rng,
            rng.choice(customers),
            status="delivered" if rng.random() < 0.9 else "cancelled",
        )

    return MockData(customers=customers, tickets=tickets)
