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
