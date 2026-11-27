"""FastAPI application: the HTTP interface to the pipeline.

Endpoints:
  GET  /                                the review screen (a single HTML page)
  GET  /health                          liveness check
  GET  /tickets                         the queue (filter with ?status=)
  GET  /tickets/{id}                    one ticket with its proposal and audit history
  POST /tickets/{id}/process            run the AI on a ticket (creates a proposal)
  POST /proposals/{id}/decision         a human approves, edits or rejects
"""

from collections.abc import Iterator
from pathlib import Path
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from supportpilot import pipeline
from supportpilot.db import get_session
from supportpilot.embeddings import Embedder, GeminiEmbedder
from supportpilot.llm import GeminiClient, LLMClient
from supportpilot.models import AuditEvent, Proposal, Ticket

app = FastAPI(title="SupportPilot", version="0.1.0")


# --- Dependencies. FastAPI calls these for each request. Tests replace them with fakes. ---


def db_session() -> Iterator[Session]:
    """One database session per request. Commit if the request worked, roll back if not."""
    session = get_session()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_llm() -> LLMClient:
    return GeminiClient()


def get_embedder() -> Embedder:
    return GeminiEmbedder()


# Annotated aliases: "this parameter is filled by calling that dependency".
DB = Annotated[Session, Depends(db_session)]
LLM = Annotated[LLMClient, Depends(get_llm)]
EMBEDDER = Annotated[Embedder, Depends(get_embedder)]

# --- What requests and responses look like ---


class DecisionRequest(BaseModel):
    decision: Literal["approve", "edit", "reject"]
    reviewer: str
    # Not verified: this demo has no login. See pipeline.decide.
    role: Literal["support_agent", "team_lead", "operations_manager"]
    reply: str | None = None
    amount: int | None = None
    note: str | None = None


def proposal_dict(p: Proposal) -> dict:
    return {
        "id": p.id,
        "ticket_id": p.ticket_id,
        "status": p.status,
        "agent_status": p.agent_status,
        "verdict": p.verdict,
        "evidence": p.evidence,
        "draft_reply": p.draft_reply,
        "policy_slug": p.policy_slug,
        "action": p.action,
        "requested_amount": p.requested_amount,
        "allowed_amount": p.allowed_amount,
        "amount": p.amount,
        "required_approver": p.required_approver,
        "flags": p.flags,
        "cost_usd": p.cost_usd,
        "decided_by": p.decided_by,
        "decided_role": p.decided_role,
        "final_reply": p.final_reply,
        "final_amount": p.final_amount,
        "decision_note": p.decision_note,
    }


def ticket_dict(t: Ticket) -> dict:
    return {
        "id": t.id,
        "customer_id": t.customer_id,
        "order_id": t.order_id,
        "text": t.text,
        "category": t.category,
        "status": t.status,
    }


# --- Routes ---

STATIC_DIR = Path(__file__).parent / "static"


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    """The review screen. It is one HTML file that calls the JSON endpoints below."""
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/health")
def health() -> dict[str, str]:
    """Liveness check: returns ok if the server is running."""
    return {"status": "ok"}


@app.get("/tickets")
def list_tickets(
    db: DB,
    status: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[dict]:
    query = select(Ticket).order_by(Ticket.id).limit(limit)
    if status:
        query = query.where(Ticket.status == status)
    return [ticket_dict(t) for t in db.scalars(query)]


@app.get("/tickets/{ticket_id}")
def get_ticket(ticket_id: int, db: DB) -> dict:
    ticket = db.get(Ticket, ticket_id)
    if ticket is None:
        raise HTTPException(404, f"Ticket {ticket_id} not found")
    proposal = db.scalar(select(Proposal).where(Proposal.ticket_id == ticket_id))
    audit = db.scalars(
        select(AuditEvent).where(AuditEvent.ticket_id == ticket_id).order_by(AuditEvent.id)
    )
    return {
        "ticket": ticket_dict(ticket),
        "proposal": proposal_dict(proposal) if proposal else None,
        "audit": [{"event": a.event, "actor": a.actor, "detail": a.detail} for a in audit],
    }


@app.post("/tickets/{ticket_id}/process", status_code=201)
def process(
    ticket_id: int,
    db: DB,
    llm: LLM,
    embedder: EMBEDDER,
) -> dict:
    try:
        return proposal_dict(pipeline.process_ticket(db, llm, embedder, ticket_id))
    except pipeline.TicketNotFound as e:
        raise HTTPException(404, str(e)) from e
    except pipeline.AlreadyProcessed as e:
        raise HTTPException(409, str(e)) from e


@app.post("/proposals/{proposal_id}/decision")
def decide(proposal_id: int, body: DecisionRequest, db: DB) -> dict:
    try:
        proposal = pipeline.decide(
            db,
            proposal_id,
            decision=body.decision,
            reviewer=body.reviewer,
            role=body.role,
            reply=body.reply,
            amount=body.amount,
            note=body.note,
        )
    except pipeline.ProposalNotFound as e:
        raise HTTPException(404, str(e)) from e
    except pipeline.AlreadyDecided as e:
        raise HTTPException(409, str(e)) from e
    except pipeline.NotAuthorized as e:
        raise HTTPException(403, str(e)) from e
    except pipeline.InvalidDecision as e:
        raise HTTPException(422, str(e)) from e
    return proposal_dict(proposal)
