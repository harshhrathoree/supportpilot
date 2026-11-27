"""Save generated mock data into the database and write the ground-truth labels file.

Run with:  uv run python -m supportpilot.seed

WARNING: this first DELETES all rows in every table, then inserts fresh mock data.
"""

import json
from pathlib import Path

from sqlalchemy.orm import Session

from supportpilot.db import Base, get_session
from supportpilot.mock_data import MockData, generate
from supportpilot.models import Ticket

# <project root>/data/labels.jsonl
LABELS_PATH = Path(__file__).resolve().parents[2] / "data" / "labels.jsonl"


def reset_tables(session: Session) -> None:
    """Delete all rows. Children are deleted before parents because of the foreign keys."""
    for table in reversed(Base.metadata.sorted_tables):
        session.execute(table.delete())
    # The session still remembers the objects it loaded earlier. Forget them, or new rows that
    # reuse the same ids would collide with those stale objects.
    session.expunge_all()


def load(session: Session, data: MockData) -> list[dict]:
    """Insert the data and return the labels (one dict per ticket). Does not commit."""
    reset_tables(session)
    session.add_all(data.customers)  # also saves their orders and items
    session.flush()  # the database now assigns ids

    saved = []
    for spec in data.tickets:
        order_id = spec.order.id if spec.order else None
        ticket = Ticket(customer_id=spec.customer.id, order_id=order_id, text=spec.render(order_id))
        session.add(ticket)
        saved.append((ticket, spec.category))
    session.flush()

    return [
        {"ticket_id": t.id, "order_id": t.order_id, "category": category, "text": t.text}
        for t, category in saved
    ]


def write_labels(labels: list[dict], path: Path = LABELS_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in labels))


def main() -> None:
    data = generate()
    with get_session() as session:
        labels = load(session, data)
        session.commit()
    write_labels(labels)  # only after the commit succeeded
    print(f"Seeded {len(data.customers)} customers, {len(labels)} tickets.")
    print(f"Labels written to {LABELS_PATH}")


if __name__ == "__main__":
    main()
