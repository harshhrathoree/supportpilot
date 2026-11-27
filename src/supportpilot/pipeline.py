"""The pipeline: process a ticket end to end, and record what a human decides.

process_ticket:  classify -> find policies -> agent investigates -> guardrails -> save a Proposal
decide:          a human approves, edits or rejects the Proposal

Nothing here pays anyone. A decision is only RECORDED. Paying out would be a separate, audited
service, because this project has no payment system.
"""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from supportpilot.agent import run_agent
from supportpilot.classifier import Classification, classify
from supportpilot.embeddings import Embedder
from supportpilot.guardrails import ROLE_RANK, apply_guardrails, can_approve, required_approver
from supportpilot.llm import LLMClient, LLMError, Usage
from supportpilot.models import AuditEvent, Proposal, Ticket
from supportpilot.policies import search_policies
from supportpilot.tools import ToolContext, get_customer_history, get_order_details

FALLBACK_REPLY = (
    "Thank you for contacting us. A member of our support team will review your ticket "
    "and get back to you shortly."
)


class PipelineError(Exception):
    """Base class. The API turns each subclass into an HTTP status code."""


class TicketNotFound(PipelineError): ...


class ProposalNotFound(PipelineError): ...


class AlreadyProcessed(PipelineError): ...


class AlreadyDecided(PipelineError): ...


class NotAuthorized(PipelineError): ...


class InvalidDecision(PipelineError): ...


def process_ticket(
    session: Session, llm: LLMClient, embedder: Embedder, ticket_id: int
) -> Proposal:
    """Run the AI side. Saves one Proposal (status pending) and moves the ticket to the queue."""
    ticket = session.get(Ticket, ticket_id)
    if ticket is None:
        raise TicketNotFound(f"Ticket {ticket_id} not found")
    already = session.scalar(select(Proposal.id).where(Proposal.ticket_id == ticket_id))
    if ticket.status != "open" or already is not None:
        raise AlreadyProcessed(f"Ticket {ticket_id} was already processed")

    flags: list[str] = []
    try:
        classification, class_usage = classify(llm, ticket.text)
        ticket.category = classification.category
    except LLMError as e:
        # Never lose a ticket because the model is down. "other" cannot receive compensation,
        # so the guardrails keep this safe, and the ticket's own category stays empty.
        classification = Classification(
            category="other", confidence=0.0, priority="medium", reason="classifier unavailable"
        )
        class_usage = Usage("none", 0, 0)
        flags.append(f"classifier failed, so no category and no compensation: {e}")

    hits = search_policies(session, embedder, f"{classification.category}: {ticket.text}", top_k=3)
    ctx = ToolContext(session, ticket.customer_id, ticket_id=ticket.id)
    result = run_agent(llm, ctx, ticket.text, classification, ticket.order_id, policies=hits)

    # The guardrails get their own copy of the facts, read straight from the database. They
    # never rely on what the model says it saw.
    order = get_order_details(ctx, ticket.order_id) if ticket.order_id is not None else None
    previous = get_customer_history(ctx)["previous_tickets"]
    report = result.report
    guard = apply_guardrails(
        category=classification.category,
        agent_status=result.status,
        verdict=report.verdict if report else None,
        order=order,
        previous_tickets=previous,
        requested_action=report.action if report else "escalate",
        requested_amount=report.amount_rupees if report else 0,
    )

    flags.extend(guard.flags)
    policy_slug = report.policy_slug if report else None
    if policy_slug is not None and policy_slug not in {h.slug for h in hits}:
        flags.append(f"agent cited policy {policy_slug!r}, which was not retrieved: ignored")
        policy_slug = None

    proposal = Proposal(
        ticket_id=ticket.id,
        agent_status=result.status,
        verdict=report.verdict if report else None,
        evidence=report.evidence if report else [],
        draft_reply=report.draft_reply if report else FALLBACK_REPLY,
        policy_slug=policy_slug,
        action=guard.action,
        requested_amount=report.amount_rupees if report else 0,
        allowed_amount=guard.allowed_amount,
        amount=guard.amount,
        required_approver=guard.required_approver,
        flags=flags,
        cost_usd=class_usage.cost_usd + result.cost_usd,
    )
    session.add(proposal)
    try:
        session.flush()
    except IntegrityError as e:  # two requests processed the same ticket at once
        session.rollback()
        raise AlreadyProcessed(f"Ticket {ticket_id} was already processed") from e

    ticket.status = "awaiting_approval"
    session.add(
        AuditEvent(
            ticket_id=ticket.id,
            event="proposal_created",
            actor="ai",
            detail={
                "category": classification.model_dump(),
                "policies": [{"slug": h.slug, "score": h.score} for h in hits],
                "agent_status": result.status,
                "agent_steps": result.steps,
                "action": guard.action,
                "amount": guard.amount,
                "flags": flags,
            },
        )
    )
    session.flush()
    return proposal


def decide(
    session: Session,
    proposal_id: int,
    *,
    decision: str,
    reviewer: str,
    role: str,
    reply: str | None = None,
    amount: int | None = None,
    note: str | None = None,
) -> Proposal:
    """Record a human decision. `decision` is approve, edit or reject.

    `role` is supplied by the caller. This demo has no login, so it is NOT verified. A real
    deployment must take the role from an authenticated session.
    """
    proposal = session.get(Proposal, proposal_id)
    if proposal is None:
        raise ProposalNotFound(f"Proposal {proposal_id} not found")
    if proposal.status != "pending":
        raise AlreadyDecided(f"Proposal {proposal_id} was already {proposal.status}")
    if role not in ROLE_RANK:
        raise NotAuthorized(f"Unknown role {role!r}")
    if not reviewer.strip():
        raise InvalidDecision("reviewer name is required")
    ticket = session.get(Ticket, proposal.ticket_id)

    if decision == "reject":
        if not (note and note.strip()):
            raise InvalidDecision("a rejection needs a note saying why")
        status, final_reply, final_amount = "rejected", None, 0
        ticket.status = "manual_handling"
    elif decision in ("approve", "edit"):
        final_reply = proposal.draft_reply
        final_amount = proposal.amount
        if decision == "edit":
            if reply is None and amount is None:
                raise InvalidDecision("an edit must change the reply or the amount")
            if reply is not None:
                if not reply.strip():
                    raise InvalidDecision("the reply cannot be empty")
                final_reply = reply
            if amount is not None:
                if amount < 0:
                    raise InvalidDecision("the amount cannot be negative")
                final_amount = amount
        # Whoever signs must be senior enough for the proposal AND for the final amount.
        needed = max(
            proposal.required_approver,
            required_approver(final_amount),
            key=lambda r: ROLE_RANK[r],
        )
        if not can_approve(role, needed):
            raise NotAuthorized(f"Role {role} cannot approve this: {needed} is needed")
        status = "approved" if decision == "approve" else "edited"
        ticket.status = "resolved"
    else:
        raise InvalidDecision(f"Unknown decision {decision!r}. Use approve, edit or reject")

    proposal.status = status
    proposal.decided_by = reviewer.strip()
    proposal.decided_role = role
    proposal.final_reply = final_reply
    proposal.final_amount = final_amount
    proposal.decision_note = note
    proposal.decided_at = datetime.now(UTC)
    session.add(
        AuditEvent(
            ticket_id=ticket.id,
            event=status,
            actor=proposal.decided_by,
            detail={
                "role": role,
                "final_amount": final_amount,
                "proposed_amount": proposal.amount,
                "above_policy_ceiling": final_amount > (proposal.allowed_amount or 0),
                "note": note,
            },
        )
    )
    session.flush()
    return proposal
