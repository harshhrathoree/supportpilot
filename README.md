# SupportPilot

An AI support copilot for a food-delivery company. When a customer complains ("my biryani arrived cold and an item was missing"), the system:

1. Classifies the complaint.
2. Looks up the real order data with tools.
3. Checks the company refund policy.
4. Drafts a reply and a proposed refund.
5. Waits for a human support agent to approve, edit or reject it.

> **Status:** Live demo: https://supportpilot-sigma.vercel.app . Steps 0 to 9 built; final eval numbers are being added. See [Progress](#progress). See [Progress](#progress).

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
    embeddings.py      turns text into embeddings (numbers that capture meaning) with Gemini
    policies.py        loads policy files into Neon and searches them by meaning
    check_retrieval.py script: checks that search finds the right policy for 18 sample queries
    guardrails.py      plain-code rules that cap the AI's suggestion and decide who may approve
    pipeline.py        process_ticket (the AI side) and decide (the human side)
    main.py            the FastAPI app: queue, process a ticket, approve / edit / reject, and the web page
    static/index.html  the review screen (plain HTML + JavaScript)
    make_messy.py      script: Gemini rewrites the 100 tickets in 6 messy styles for the eval
    eval_cases.py      the eval cases (messy + 20 attacks) and their correct answers
    eval_run.py        script: runs the eval, scores it, writes docs/eval-report.md
  data/labels.jsonl    ground-truth category for each of the 100 tickets
  docs/examples/       a sample agent trace you can read without running anything
  policies/            the 16 company refund and escalation policies (plain markdown)
  tests/               automated tests
  docs/                flow.md, code-walkthrough.md, eval-report.md
  app.py, vercel.json  entry point and settings for the Vercel deployment
  Dockerfile           container image (an alternative way to deploy; not tested locally)
  .github/workflows/   CI: ruff and pytest on every push
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

# 7. Load the policies and their embeddings into Neon (safe to repeat)
uv run python -m supportpilot.policies

# 8. Start the server (API and review screen)
uv run uvicorn supportpilot.main:app --reload

# 9. Open http://127.0.0.1:8000/        -> the review screen
#    http://127.0.0.1:8000/health      -> {"status":"ok"}
#    http://127.0.0.1:8000/docs        -> interactive API docs
```

Run the tests and the linter:

```bash
uv run pytest
uv run ruff check . && uv run ruff format --check .
```

## The API (Step 7)

With the server running, open http://127.0.0.1:8000/docs for an interactive page where you can try every endpoint.

| Endpoint | What it does |
|---|---|
| `GET /tickets?status=awaiting_approval` | the queue |
| `GET /tickets/{id}` | one ticket, its AI proposal and its audit history |
| `POST /tickets/{id}/process` | run the AI on the ticket and create a proposal |
| `POST /proposals/{id}/decision` | a human approves, edits or rejects |

**Who may approve what** (from `policies/refund-approval-limits.md`): up to Rs 200 a support agent, up to Rs 500 a team lead, above that an operations manager. The AI never pays anything. It proposes, code caps the amount, and a human decides.

**Known limit:** this demo has no login. The reviewer's name and role come from the request and are *not verified*. A real deployment must take them from an authenticated session.

## Progress

- [x] Step 0: project skeleton, health endpoint, tests
- [x] Step 1: Neon database and schema
- [x] Step 2: mock data generator
- [x] Step 3: Gemini provider layer
- [x] Step 4: ticket classifier (100/100 on the first template set; a harder set comes in Step 8)
- [x] Step 5: agent loop with order tools (evidence and draft reply; refund decisions come in Steps 6 and 7)
- [x] Step 6: policy retrieval (RAG): 16 policies embedded in Neon, searched with pgvector
- [x] Step 7: human approval and guardrails (AI proposes, code caps it, a human decides, everything is audited)
- [x] Step 8: evaluation harness (messy and adversarial sets, see [`docs/eval-report.md`](docs/eval-report.md))
- [x] Step 9a: review screen, Dockerfile, CI
- [x] Step 9b: live deployment on Vercel (https://supportpilot-sigma.vercel.app)

## Live demo and deployment

- **URL:** https://supportpilot-sigma.vercel.app (the review screen). API docs at `/docs`.
- **Hosting:** Vercel (free Hobby plan). The whole FastAPI app runs as one serverless function. `app.py` is the entry point Vercel looks for, and `vercel.json` sets a 120 second limit (one AI run took about 17 seconds in my live test).
- **Secrets:** `GEMINI_API_KEY` and `DATABASE_URL` are Vercel environment variables, never in git. The database URL is Neon's pooled connection, because serverless functions open many short connections.
- **Deploying:** `npx vercel deploy --prod`. The repo is also linked to GitHub, so a push can trigger a deploy.
- **First request** after a quiet period is a little slower (a serverless cold start).

**Honest limits of the public demo:**
- **There is no login.** Anyone with the link can run the AI on a ticket or approve a proposal. That is acceptable for a demo with mock data and no real payments. It is not acceptable for a real product.
- **Gemini quota.** Each ticket can be processed only once and there are 100 tickets, so abuse is bounded, but the free-tier limit is shared by all visitors.
- **The rate limiter is per instance** (it lives in memory). With several instances running, the total can exceed my 12 requests a minute and the retry code absorbs any 429 errors.

## Git workflow

Small commits, one logical change each, using [Conventional Commits](https://www.conventionalcommits.org/) (`feat:`, `fix:`, `docs:`, `test:`, `chore:`). Secrets live only in `.env`, which is git-ignored.
