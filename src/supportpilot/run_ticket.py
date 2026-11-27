"""Run the full flow on one real ticket from the database and save the trace.

Run with:  uv run python -m supportpilot.run_ticket 2

Reads the database only. It never changes data. Needs `supportpilot.seed` to have run.
"""

import sys
from pathlib import Path

from supportpilot.agent import run_agent, save_trace
from supportpilot.classifier import classify
from supportpilot.db import get_session
from supportpilot.llm import GeminiClient
from supportpilot.models import Ticket
from supportpilot.tools import ToolContext

TRACES_DIR = Path(__file__).resolve().parents[2] / "traces"


def main(ticket_id: int) -> None:
    llm = GeminiClient()
    with get_session() as session:
        ticket = session.get(Ticket, ticket_id)
        if ticket is None:
            sys.exit(f"Ticket {ticket_id} not found. Did you run supportpilot.seed?")

        print(f"Ticket #{ticket.id} (order {ticket.order_id}): {ticket.text}\n")
        classification, class_usage = classify(llm, ticket.text)
        print(f"Classified: {classification.category}, priority {classification.priority}")

        ctx = ToolContext(session, ticket.customer_id, ticket_id=ticket.id)
        result = run_agent(llm, ctx, ticket.text, classification, ticket.order_id)

    for row in result.trace:
        if row["kind"] == "model":
            turn = row["turn"]
            what = turn["tool"] if turn["action"] == "call_tool" else "finish"
            print(f"  step {row['step']}: model -> {what}")
        elif row["kind"] == "tool":
            print(f"  step {row['step']}: tool result {row['result']}")

    print(f"\nStatus: {result.status} in {result.steps} steps")
    if result.report:
        print(f"Verdict: {result.report.verdict}")
        for fact in result.report.evidence:
            print(f"  - {fact}")
        print(f"Draft reply: {result.report.draft_reply}")
    if result.error:
        print(f"Error: {result.error}")
    total = class_usage.cost_usd + result.cost_usd
    print(f"Cost: ${total:.6f}")
    print(f"Trace: {save_trace(result, ticket.id, TRACES_DIR)}")


if __name__ == "__main__":
    if len(sys.argv) != 2 or not sys.argv[1].isdigit():
        sys.exit("Usage: uv run python -m supportpilot.run_ticket <ticket_id>")
    main(int(sys.argv[1]))
