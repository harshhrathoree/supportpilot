# SupportPilot

An AI support copilot for a food-delivery company. When a customer complains ("my biryani arrived cold and an item was missing"), the system:

1. Classifies the complaint.
2. Looks up the real order data with tools.
3. Checks the company refund policy.
4. Drafts a reply and a proposed refund.
5. Waits for a human support agent to approve, edit or reject it.

> **Status:** Step 5 done (agent loop with tools). See [Progress](#progress).

## Docs (read these in order)

| File | What it explains |
|---|---|
| [`README.md`](README.md) | What the project is, how to run it, and progress (this file) |
| [`docs/flow.md`](docs/flow.md) | How data moves through the app, one flow at a time |
| [`docs/code-walkthrough.md`](docs/code-walkthrough.md) | Every file we write, explained line by line for a beginner |

## Tech stack

| Layer | Tool | Why |
|---|---|---|
| Language | Python 3.12 | Standard for AI work |
| Web framework | FastAPI | Fast, typed, auto-generated API docs |
| Database | Neon (Postgres + pgvector) | One free serverless database for orders, tickets and policy embeddings |
| LLM | Google Gemini (`gemini-3.5-flash-lite`, fallback `gemini-3.1-flash-lite`) | Free tier, structured output, very cheap. Model names are set in `config.py` and can be overridden in `.env` with `LLM_MODEL` |
| Package manager | uv | Fast, creates the virtual environment for us |
| Tests / lint | pytest, ruff | Standard tools |

## Project layout

```
supportpilot/
  src/supportpilot/    application code
    config.py          reads settings and secrets from .env
    db.py              database connection (SQLAlchemy engine and sessions)
    models.py          table definitions: customers, orders, order_items, tickets
    init_db.py         creates the tables in Neon
    mock_data.py      generates fake customers, orders and labeled tickets
    seed.py            saves the mock data to Neon and writes data/labels.jsonl
    llm.py             the only file that talks to Gemini: structured output, rate limit, retries, fallback, cost
    classifier.py      decides the complaint category, confidence and priority of a ticket
    classify_labels.py script: runs the classifier on all labeled tickets and prints accuracy
    tools.py           read-only tools the agent can call (order details, customer history)
    agent.py           the agent loop: call tools, collect evidence, stop on limits, save a trace
    run_ticket.py      script: classify and investigate one real ticket end to end
    main.py            the FastAPI app and its routes
  data/labels.jsonl    ground-truth category for each of the 100 tickets
  docs/examples/       a sample agent trace you can read without running anything
  tests/               automated tests
  docs/                flow.md and code-walkthrough.md
  .env.example         template for secrets (copy to .env)
  pyproject.toml       dependencies and tool settings
```

## Run it locally

```bash
# 1. Install dependencies (creates .venv automatically)
uv sync

# 2. Create your secrets file and fill it in later steps
cp .env.example .env

# 3. Create the database tables in Neon (needs DATABASE_URL in .env)
uv run python -m supportpilot.init_db

# 4. Fill the database with mock data (WARNING: deletes existing rows first)
uv run python -m supportpilot.seed

# 5. (Optional) Classify all 100 tickets and print accuracy. Takes about 8 min on the free tier
uv run python -m supportpilot.classify_labels

# 6. Investigate one ticket end to end and save its trace to traces/
uv run python -m supportpilot.run_ticket 2

# 7. Start the server
uv run uvicorn supportpilot.main:app --reload

# 8. Open http://127.0.0.1:8000/health  -> {"status":"ok"}
#    Interactive API docs: http://127.0.0.1:8000/docs
```

Run the tests and the linter:

```bash
uv run pytest
uv run ruff check . && uv run ruff format --check .
```

## Progress

- [x] Step 0: project skeleton, health endpoint, tests
- [x] Step 1: Neon database and schema
- [x] Step 2: mock data generator
- [x] Step 3: Gemini provider layer
- [x] Step 4: ticket classifier (100/100 on the first template set; a harder set comes in Step 8)
- [x] Step 5: agent loop with order tools (evidence and draft reply; refund decisions come in Steps 6 and 7)
- [ ] Step 6: policy retrieval (RAG)
- [ ] Step 7: human approval and guardrails
- [ ] Step 8: evaluation harness
- [ ] Step 9: UI and deployment

## Git workflow

Small commits, one logical change each, using [Conventional Commits](https://www.conventionalcommits.org/) (`feat:`, `fix:`, `docs:`, `test:`, `chore:`). Secrets live only in `.env`, which is git-ignored.
