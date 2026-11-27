# Application flows

Each section describes one flow: what triggers it, what happens, and which files are involved. This file grows as we build.

## Flow 1: Health check (Step 0)

**Trigger:** anything (a browser, a deploy platform, a monitor) calls `GET /health`.

```mermaid
sequenceDiagram
    participant C as Client
    participant U as Uvicorn server
    participant F as FastAPI app
    C->>U: GET /health
    U->>F: pass request to the app
    F-->>C: 200 {"status": "ok"}
```

**Why it exists:** hosting platforms (Render, Fly.io) call this to know the app is alive. It touches no database or LLM, so if it fails, the server itself is down.

**Files:** `src/supportpilot/main.py` (the route), `tests/test_health.py` (the test).

## Flow 2 onward

Added as we build them. Planned flows:

1. Ticket arrives, then it is classified
2. Agent loop: tool calls to fetch order data
3. Policy retrieval
4. Draft, then human approval, then refund or reply
5. Evaluation run

## Flow 2: Database setup (Step 1)

**Trigger:** you run `uv run python -m supportpilot.init_db` once (and again any time we add a table).

```mermaid
sequenceDiagram
    participant Y as You
    participant I as init_db.py
    participant S as config.py
    participant N as Neon Postgres
    Y->>I: run the script
    I->>S: read DATABASE_URL from .env
    I->>N: CREATE EXTENSION IF NOT EXISTS vector
    I->>N: CREATE TABLE IF NOT EXISTS (for each model)
    N-->>I: done
    I-->>Y: "Database ready: customers, orders, order_items, tickets"
```

**Safe to repeat:** both statements use "if not exists", so running it twice changes nothing.

### How the tables relate

```mermaid
erDiagram
    customers ||--o{ orders : places
    customers ||--o{ tickets : files
    orders ||--|{ order_items : contains
    orders ||--o{ tickets : "is about"
    customers {
        int id PK
        string name
        string phone
    }
    orders {
        int id PK
        int customer_id FK
        string restaurant_name
        string status
        int total_amount
        datetime placed_at
        datetime promised_at
        datetime delivered_at
    }
    order_items {
        int id PK
        int order_id FK
        string name
        int quantity
        int price
        bool packed
    }
    tickets {
        int id PK
        int customer_id FK
        int order_id FK
        text text
        string category
        string status
    }
```

**Why these columns matter later:** the agent will compare `delivered_at` with `promised_at` to detect lateness, and check `packed = false` to confirm a missing item. Those are the facts that make its decisions grounded instead of guessed.

**Files:** `src/supportpilot/init_db.py`, `db.py`, `models.py`.

## Flow 3: Mock data seeding (Step 2)

**Trigger:** you run `uv run python -m supportpilot.seed`.

```mermaid
sequenceDiagram
    participant Y as You
    participant S as seed.py
    participant G as mock_data.py
    participant N as Neon Postgres
    participant L as data/labels.jsonl
    Y->>S: run the script
    S->>G: generate(seed=42)
    G-->>S: 50 customers, 500 orders, 100 ticket specs (in memory)
    S->>N: DELETE all rows (children first)
    S->>N: INSERT customers, orders, items (ids assigned by the database)
    S->>N: INSERT tickets (text now contains the real order id)
    S->>N: COMMIT
    S->>L: write one line per ticket: id, order id, true category, text
```

### How one ticket is built (inside `mock_data.py`)

```mermaid
flowchart TD
    A[Pick a category, e.g. missing_item] --> B[Build an order that MATCHES it]
    B --> C{Category}
    C -->|late_delivery| D[delivered_at = promised_at + 25 to 70 min]
    C -->|missing_item| E[one item gets packed = false]
    C -->|refund_status| F[order status = cancelled]
    C -->|other| G[no order at all]
    D --> H[Fill a text template with real order facts]
    E --> H
    F --> H
    G --> H
    H --> I[Ticket text + true category label]
```

**Why it matters:** because the order data is built to agree with the complaint, the label is trustworthy ground truth. Later the eval harness compares the agent's category and decision with `labels.jsonl`.

**Why the text has `<order>` first:** the database chooses order ids when saving, so the generator cannot know them. The placeholder is replaced with the real id after the orders are saved.

**Reproducible:** the same seed (42) always gives the same data, so everyone gets the same eval set.

**Files:** `src/supportpilot/mock_data.py`, `seed.py`, `data/labels.jsonl`.

## Flow 4: One LLM call (Step 3)

**Trigger:** any part of the app (the classifier in Step 4, the agent in Step 5) calls `generate_structured(system, prompt, schema)`.

```mermaid
flowchart TD
    A[Caller: system prompt + user prompt + Pydantic schema] --> B[Call main model]
    B -->|success| P{Answer matches the schema?}
    B -->|429 or 5xx error| R{Attempts left? max 3}
    R -->|yes: wait 1s, 2s...| B
    R -->|no| F[Call fallback model with the same retry rule]
    B -->|400 or 404: our mistake| X[Raise LLMError immediately]
    F -->|success| P
    F -->|fails| Y[Raise LLMError: both models failed]
    P -->|yes| OK[Return the Pydantic object + Usage: model, tokens, cost]
    P -->|no| Z[Raise LLMError: invalid answer]
```

**Why each rule exists**
- **Structured output:** the model is forced to answer in JSON that fits our schema, so downstream code never parses free text.
- **Retries with growing waits:** Google sometimes returns 503 "high demand" for a few seconds. This happened to us on the first day while testing.
- **No retry on 400/404:** these mean our request is wrong, so retrying wastes time. We hit this too: older model names answered 404.
- **Fallback model:** if the main model stays down, a second one keeps tickets flowing.
- **Usage record:** every call reports the model used, tokens and dollar cost, which Step 8 turns into "cost per ticket".

**Files:** `src/supportpilot/llm.py`, `config.py` (model names), `tests/test_llm.py`.

## Flow 5: Classifying a ticket (Step 4)

**Trigger:** a ticket needs a category. Today that is `classify_labels.py`; from Step 5 on, the agent does it first.

```mermaid
sequenceDiagram
    participant C as Caller
    participant K as classifier.py
    participant L as llm.py
    participant R as RateLimiter
    participant G as Gemini
    C->>K: classify(llm, ticket text)
    K->>K: strip any <ticket> tags the customer typed, then wrap the text in <ticket> tags
    K->>L: generate_structured(system prompt, wrapped text, Classification schema)
    L->>R: wait for the next free slot (one call per 5 s)
    R-->>L: go
    L->>G: request (JSON answer required)
    G-->>L: {"category": ..., "confidence": ..., "priority": ..., "reason": ...}
    L->>L: validate against the schema (category must be one of 8)
    L-->>K: Classification + Usage
    K-->>C: Classification + Usage
```

**What protects against a malicious ticket**
1. The customer's text is wrapped in tags, and the prompt says it is data, not instructions.
2. Tags typed by the customer are removed, so they cannot fake the end of the ticket.
3. The answer must fit the schema, so the model cannot return a new category or free text.
4. The classifier only labels. It never moves money. Real protection for refunds comes in Step 7 (human approval).

**Why a rate limiter:** the free tier allows 15 calls per minute per model. The limiter spaces calls 5 seconds apart (12 per minute), so a batch of 100 tickets takes about 8 minutes but never errors.

**Files:** `src/supportpilot/classifier.py`, `llm.py`, `classify_labels.py`, `tests/test_classifier.py`.

## Flow 6: The agent investigates a ticket (Step 5)

**Trigger:** `uv run python -m supportpilot.run_ticket <ticket_id>` today. Later, a new ticket arriving in the app.

```mermaid
sequenceDiagram
    participant R as run_ticket.py
    participant C as classifier
    participant A as agent loop
    participant M as Gemini
    participant T as tools.py
    participant D as Neon DB
    R->>C: classify(ticket text)
    C-->>R: category, priority
    R->>A: run_agent(ticket, category, linked order id)
    loop until finish or a limit is hit (max 6 steps, max $0.02)
        A->>M: transcript so far, what is your next action?
        M-->>A: call_tool(get_order_details, order 172)  OR  finish(report)
        A->>T: run the tool (customer id comes from the ticket)
        T->>D: read-only query
        D-->>T: rows
        T-->>A: JSON facts (minutes late, items, packed flags)
    end
    A-->>R: status, report (verdict, evidence, draft reply), trace
    R->>R: save traces/ticket_N.jsonl
```

### When the loop stops
```mermaid
flowchart TD
    S[Ask the model for its next action] --> E{LLM error?}
    E -->|yes| X1[status = llm_error]
    E -->|no| F{Action}
    F -->|finish with a report| OK[status = finished]
    F -->|call a tool| T[Run the tool, add the result to the transcript]
    F -->|bad action: no tool name, or finish without a report| B[Tell the model what was wrong, keep going]
    T --> C{Cost over budget?}
    B --> C
    C -->|yes| X2[status = budget_exceeded]
    C -->|no| N{Steps left?}
    N -->|yes| S
    N -->|no| X3[status = step_limit]
```

A run that stops early is never lost: the result says why, so a human can pick up the ticket.

### What keeps the agent safe
| Risk | Defence |
|---|---|
| The agent changes data | All tools are read-only. A test checks every tool name starts with `get_`. |
| The agent reads another customer's order | The customer id comes from the ticket, set by our code. The model only supplies an order id, and a foreign order gets the same "not found" answer as a missing one. |
| A ticket says "refund me" or "ignore your rules" | The ticket and tool output are wrapped in tags and marked as data. The agent has no refund tool at all, so even a tricked agent cannot move money. |
| The agent loops forever or burns money | Step limit (6) and cost limit ($0.02). |
| The agent makes up facts | The prompt requires evidence from tool results, and the full trace is saved so a reviewer can check each claim. |

**Files:** `src/supportpilot/tools.py`, `agent.py`, `run_ticket.py`, `tests/test_tools.py`, `tests/test_agent.py`. Sample output: `docs/examples/trace_missing_item.jsonl`.
