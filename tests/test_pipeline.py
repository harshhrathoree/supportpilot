"""Pipeline and API tests. Fake LLM and embedder, in-memory database: free and deterministic."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from supportpilot import main, models, pipeline
from supportpilot.agent import AgentReport, AgentTurn
from supportpilot.classifier import Classification
from supportpilot.db import Base
from supportpilot.llm import LLMError, Usage
from supportpilot.policies import PolicyDoc, load_policies

NOW = datetime(2026, 11, 27, 12, 0, tzinfo=UTC)


class FakeEmbedder:
    def embed_documents(self, texts):
        return [[1.0] + [0.0] * 767 for _ in texts]

    def embed_query(self, text):
        return [1.0] + [0.0] * 767


class FakeLLM:
    """Answers the classifier and the agent from prepared values, and records every call."""

    def __init__(self, category="late_delivery", report=None, fail_agent=False):
        self.category = category
        self.report = report
        self.fail_agent = fail_agent
        self.prompts: list[str] = []
        self.tool_called = False

    def generate_structured(self, *, system, prompt, schema):
        self.prompts.append(prompt)
        usage = Usage("fake", 10, 5)
        if schema is Classification:
            return Classification(
                category=self.category, confidence=0.9, priority="medium", reason="x"
            ), usage
        if self.fail_agent:
            raise LLMError("model down")
        if not self.tool_called:
            self.tool_called = True
            return AgentTurn(action="call_tool", tool="get_order_details", order_id=1), usage
        return AgentTurn(action="finish", report=self.report), usage


def report(**kw):
    base = {
        "verdict": "supported",
        "evidence": ["Order was 40 minutes late"],
        "draft_reply": "Sorry for the delay. The team is reviewing it.",
        "policy_slug": "late-delivery",
        "action": "propose_compensation",
        "amount_rupees": 100,
    }
    return AgentReport(**{**base, **kw})


@pytest.fixture
def engine():
    # StaticPool keeps one shared in-memory database, so the API test client sees the same data.
    e = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(e)
    return e


@pytest.fixture
def db(engine):
    with Session(engine) as s:
        alice = models.Customer(name="Alice", phone="9000000001")
        s.add(alice)
        s.flush()
        placed = NOW - timedelta(hours=2)
        s.add(
            models.Order(
                customer=alice,
                restaurant_name="Biryani House",
                status="delivered",
                total_amount=500,
                payment_method="upi",
                placed_at=placed,
                promised_at=placed + timedelta(minutes=40),
                delivered_at=placed + timedelta(minutes=80),  # 40 minutes late
                items=[models.OrderItem(name="Biryani", quantity=1, price=500)],
            )
        )
        s.flush()
        s.add(models.Ticket(customer_id=alice.id, order_id=1, text="My food was very late"))
        load_policies(
            s,
            FakeEmbedder(),
            [PolicyDoc("late-delivery", "Late delivery", "# Late delivery\nrules")],
        )
        s.commit()
        yield s


def process(db, llm=None, ticket_id=1):
    return pipeline.process_ticket(db, llm or FakeLLM(report=report()), FakeEmbedder(), ticket_id)


# --- process_ticket ---


def test_process_creates_a_pending_proposal_and_queues_the_ticket(db):
    proposal = process(db)
    ticket = db.get(models.Ticket, 1)
    assert (proposal.status, proposal.action, proposal.amount) == (
        "pending",
        "propose_compensation",
        100,
    )
    assert proposal.required_approver == "support_agent"
    assert proposal.policy_slug == "late-delivery"
    assert (ticket.status, ticket.category) == ("awaiting_approval", "late_delivery")


def test_process_writes_an_audit_event_from_the_ai(db):
    process(db)
    event = db.scalar(select(models.AuditEvent))
    assert (event.event, event.actor) == ("proposal_created", "ai")
    assert event.detail["policies"][0]["slug"] == "late-delivery"
    assert event.detail["amount"] == 100


def test_retrieved_policy_text_reaches_the_agent_prompt(db):
    llm = FakeLLM(report=report())
    process(db, llm)
    agent_prompts = llm.prompts[1:]  # the first call is the classifier
    assert '<policy slug="late-delivery">' in agent_prompts[0]


def test_a_model_that_asks_for_5000_gets_the_policy_amount_instead(db):
    proposal = process(db, FakeLLM(report=report(amount_rupees=5000)))
    assert (proposal.requested_amount, proposal.amount, proposal.allowed_amount) == (5000, 100, 100)
    assert any("above the policy ceiling" in f for f in proposal.flags)


def test_a_made_up_policy_slug_is_dropped(db):
    proposal = process(db, FakeLLM(report=report(policy_slug="free-money")))
    assert proposal.policy_slug is None
    assert any("not retrieved" in f for f in proposal.flags)


def test_when_the_model_is_down_the_ticket_goes_to_a_human_with_a_safe_reply(db):
    proposal = process(db, FakeLLM(fail_agent=True))
    assert (proposal.agent_status, proposal.action, proposal.amount) == ("llm_error", "escalate", 0)
    assert proposal.draft_reply == pipeline.FALLBACK_REPLY
    assert db.get(models.Ticket, 1).status == "awaiting_approval"


def test_a_ticket_cannot_be_processed_twice(db):
    process(db)
    with pytest.raises(pipeline.AlreadyProcessed):
        process(db)


def test_unknown_ticket_is_reported(db):
    with pytest.raises(pipeline.TicketNotFound):
        process(db, ticket_id=999)


# --- decide ---


def decide(db, proposal, **kw):
    args = {"decision": "approve", "reviewer": "Priya", "role": "support_agent"}
    return pipeline.decide(db, proposal.id, **{**args, **kw})


def test_approve_resolves_the_ticket_and_records_who_and_when(db):
    proposal = process(db)
    decide(db, proposal)
    assert (proposal.status, proposal.decided_by, proposal.final_amount) == (
        "approved",
        "Priya",
        100,
    )
    assert proposal.decided_at is not None
    assert db.get(models.Ticket, 1).status == "resolved"
    events = [e.event for e in db.scalars(select(models.AuditEvent).order_by(models.AuditEvent.id))]
    assert events == ["proposal_created", "approved"]


def test_edit_changes_the_reply_and_amount(db):
    proposal = process(db)
    decide(db, proposal, decision="edit", reply="Hello, apologies.", amount=60)
    assert (proposal.status, proposal.final_reply, proposal.final_amount) == (
        "edited",
        "Hello, apologies.",
        60,
    )


def test_an_edit_that_changes_nothing_is_rejected(db):
    proposal = process(db)
    with pytest.raises(pipeline.InvalidDecision):
        decide(db, proposal, decision="edit")


def test_reject_needs_a_note_and_sends_the_ticket_to_manual_handling(db):
    proposal = process(db)
    with pytest.raises(pipeline.InvalidDecision):
        decide(db, proposal, decision="reject")
    decide(db, proposal, decision="reject", note="Customer should be called instead")
    assert (proposal.status, proposal.final_amount) == ("rejected", 0)
    assert db.get(models.Ticket, 1).status == "manual_handling"


def test_a_proposal_can_only_be_decided_once(db):
    proposal = process(db)
    decide(db, proposal)
    with pytest.raises(pipeline.AlreadyDecided):
        decide(db, proposal)


def test_a_junior_cannot_approve_an_amount_above_their_limit_by_editing_it_up(db):
    proposal = process(db)
    with pytest.raises(pipeline.NotAuthorized):
        decide(db, proposal, decision="edit", amount=300, role="support_agent")
    assert proposal.status == "pending"  # the failed attempt changed nothing
    decide(db, proposal, decision="edit", amount=300, role="team_lead")
    assert proposal.final_amount == 300


def test_a_proposal_that_needs_a_team_lead_cannot_be_approved_by_a_support_agent(db):
    proposal = process(db, FakeLLM(fail_agent=True))  # escalations need a team lead
    with pytest.raises(pipeline.NotAuthorized):
        decide(db, proposal)
    decide(db, proposal, role="team_lead")
    assert proposal.status == "approved"


def test_an_edit_above_the_policy_ceiling_is_allowed_for_a_senior_but_flagged_in_the_audit(db):
    proposal = process(db)
    decide(db, proposal, decision="edit", amount=250, role="team_lead")
    event = db.scalars(select(models.AuditEvent).order_by(models.AuditEvent.id.desc())).first()
    assert event.detail["above_policy_ceiling"] is True


def test_unknown_role_and_blank_reviewer_are_refused(db):
    proposal = process(db)
    with pytest.raises(pipeline.NotAuthorized):
        decide(db, proposal, role="intern")
    with pytest.raises(pipeline.InvalidDecision):
        decide(db, proposal, reviewer="  ")


# --- the HTTP API ---


@pytest.fixture
def client(engine, db):
    def session_for_request():
        s = Session(engine)
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    main.app.dependency_overrides[main.db_session] = session_for_request
    main.app.dependency_overrides[main.get_llm] = lambda: FakeLLM(report=report())
    main.app.dependency_overrides[main.get_embedder] = FakeEmbedder
    yield TestClient(main.app)
    main.app.dependency_overrides.clear()


def test_api_full_flow_process_then_view_then_approve(client):
    created = client.post("/tickets/1/process")
    assert created.status_code == 201
    assert created.json()["amount"] == 100

    detail = client.get("/tickets/1").json()
    assert detail["ticket"]["status"] == "awaiting_approval"
    assert detail["proposal"]["status"] == "pending"
    assert [a["event"] for a in detail["audit"]] == ["proposal_created"]

    queue = client.get("/tickets", params={"status": "awaiting_approval"}).json()
    assert [t["id"] for t in queue] == [1]

    body = {"decision": "approve", "reviewer": "Priya", "role": "support_agent"}
    decided = client.post(f"/proposals/{created.json()['id']}/decision", json=body)
    assert decided.status_code == 200
    assert decided.json()["status"] == "approved"
    assert client.get("/tickets/1").json()["ticket"]["status"] == "resolved"


def test_api_error_codes(client):
    assert client.get("/tickets/999").status_code == 404
    assert client.post("/tickets/999/process").status_code == 404
    assert client.post("/tickets/1/process").status_code == 201
    assert client.post("/tickets/1/process").status_code == 409  # already processed

    body = {"decision": "approve", "reviewer": "P", "role": "support_agent"}
    assert client.post("/proposals/999/decision", json=body).status_code == 404
    assert (
        client.post("/proposals/1/decision", json={**body, "decision": "reject"}).status_code == 422
    )
    assert client.post("/proposals/1/decision", json={**body, "role": "intern"}).status_code == 422
    assert client.post("/proposals/1/decision", json=body).status_code == 200
    assert client.post("/proposals/1/decision", json=body).status_code == 409  # already decided


def test_api_returns_403_when_the_role_is_too_junior(client):
    client.post("/tickets/1/process")
    body = {"decision": "edit", "reviewer": "P", "role": "support_agent", "amount": 400}
    assert client.post("/proposals/1/decision", json=body).status_code == 403
