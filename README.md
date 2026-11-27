# SupportPilot

An AI support copilot for a food-delivery company. When a customer complains ("my biryani arrived cold and an item was missing"), the system:

1. Classifies the complaint.
2. Looks up the real order data with tools.
3. Checks the company refund policy.
4. Drafts a reply and a proposed refund.
5. Waits for a human support agent to approve, edit or reject it.

> **Status:** Step 0 done (project skeleton and health endpoint). See [Progress](#progress).

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
| LLM | Google Gemini | Free tier, good structured output and tool calling |
| Package manager | uv | Fast, creates the virtual environment for us |
| Tests / lint | pytest, ruff | Standard tools |

## Project layout

```
supportpilot/
  src/supportpilot/    application code
    config.py          reads settings and secrets from .env
    main.py            the FastAPI app and its routes
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

# 3. Start the server
uv run uvicorn supportpilot.main:app --reload

# 4. Open http://127.0.0.1:8000/health  -> {"status":"ok"}
#    Interactive API docs: http://127.0.0.1:8000/docs
```

Run the tests and the linter:

```bash
uv run pytest
uv run ruff check . && uv run ruff format --check .
```

## Progress

- [x] Step 0: project skeleton, health endpoint, tests
- [ ] Step 1: Neon database and schema
- [ ] Step 2: mock data generator
- [ ] Step 3: Gemini provider layer
- [ ] Step 4: ticket classifier
- [ ] Step 5: agent loop with order tools
- [ ] Step 6: policy retrieval (RAG)
- [ ] Step 7: human approval and guardrails
- [ ] Step 8: evaluation harness
- [ ] Step 9: UI and deployment

## Git workflow

Small commits, one logical change each, using [Conventional Commits](https://www.conventionalcommits.org/) (`feat:`, `fix:`, `docs:`, `test:`, `chore:`). Secrets live only in `.env`, which is git-ignored.
