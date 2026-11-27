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

## Flow 7: Policy loading and search (Step 6)

### Loading (run once, and again whenever a policy file changes)

**Trigger:** `uv run python -m supportpilot.policies`

```mermaid
flowchart TD
    A[Read every policies/*.md file] --> B[Compute a fingerprint of each file's text]
    B --> C{Fingerprint same as the one stored in Neon?}
    C -->|yes| D[Skip: no API call]
    C -->|no, or a new file| E[Send all changed texts to Gemini in ONE embedding call]
    E --> F[Save text + 768 numbers in policy_chunks]
    A --> G{A row in Neon has no file anymore?}
    G -->|yes| H[Delete the row]
```

### Searching (used by the agent in Step 7)

```mermaid
sequenceDiagram
    participant C as Caller
    participant P as policies.py
    participant E as Gemini embeddings
    participant N as Neon pgvector
    C->>P: search_policies("my raita was not in the bag")
    P->>E: embed_query(text)
    E-->>P: 768 numbers
    P->>N: ORDER BY cosine distance to those numbers LIMIT 3
    N-->>P: the 3 closest policies
    P-->>C: title, text and similarity score for each
```

**What an embedding is:** a list of 768 numbers that represents the *meaning* of a text. Texts with similar meaning get lists that point in nearly the same direction. "The raita was not in the bag" and "Missing item" share almost no words, but their numbers are close, so search finds the policy. Closeness is measured as **cosine similarity**: 1.0 means the same direction and 0 means unrelated.

**Why policies go in the database and not in the prompt:** with 16 short policies we could paste them all into every prompt. That works today but costs more tokens per ticket as the policy set grows, and it dilutes the model's attention. Retrieval gives the agent only the 2 to 3 relevant policies. The design also scales to hundreds of policies.

**Files:** `src/supportpilot/embeddings.py`, `policies.py`, `models.py` (the `PolicyChunk` table), `policies/*.md`, `tests/test_policies.py`.

## Flow 8: From ticket to approved decision (Step 7)

This is the main flow of the whole product.

```mermaid
sequenceDiagram
    participant H as Human reviewer
    participant API as FastAPI
    participant P as pipeline.py
    participant AI as classifier + agent (Gemini)
    participant G as guardrails.py
    participant DB as Neon
    H->>API: POST /tickets/6/process
    API->>P: process_ticket
    P->>AI: classify the ticket
    P->>DB: search policies (pgvector), top 3
    P->>AI: agent investigates with tools, reads the policies, suggests action + amount
    P->>DB: re-read the order itself (does not trust the agent's version)
    P->>G: apply_guardrails(model suggestion, order facts, history)
    G-->>P: final action, amount, who must approve, flags
    P->>DB: save Proposal (pending) + audit event "proposal_created by ai"
    API-->>H: the proposal
    H->>API: POST /proposals/5/decision (approve / edit / reject, name, role)
    API->>P: decide
    P->>P: role senior enough for the amount? note present on reject?
    P->>DB: save the decision + audit event with the human's name
    API-->>H: the final proposal
```

### What the guardrails do (in order)

```mermaid
flowchart TD
    A[Model suggestion] --> B{Agent finished?}
    B -->|no: step limit, budget, LLM error| E[Escalate: no money, team lead]
    B -->|yes| C{5 or more earlier tickets?}
    C -->|yes| E
    C -->|no| D{Model asked to escalate?}
    D -->|yes| E
    D -->|no| F{Wants compensation?}
    F -->|no| R[Reply only, amount 0]
    F -->|yes| G{Category is late_delivery or missing_item?}
    G -->|no: needs photo / payment record / human| E
    G -->|yes| H{Verdict supported AND data shows something owed?}
    H -->|no| R
    H -->|yes| I[Amount = the model's amount, but never above the ceiling computed from the order data]
    I --> J[Pick the lowest role that may approve that amount]
```

### The ceiling is computed, not asked
- **Late delivery:** minutes late (from timestamps) selects 0, 10%, 20% or 50% of the order, each with a cap (Rs 50, 100, 300).
- **Missing item:** price times quantity of the items the restaurant did not pack.
- The model's number can only *lower* the result. So "refund me Rs 5000" in a ticket cannot produce more than the policy allows, even if the model believed it.

### Approving
| Decision | Rule |
|---|---|
| approve | the role must be allowed to approve the proposed amount |
| edit | the role must be allowed to approve the *edited* amount, so a junior cannot edit an amount up past their limit |
| reject | needs a written note. The ticket goes to `manual_handling` |

A proposal can be decided exactly once. Every step writes an audit event with who, when and what.

### Ticket statuses
`open` (just arrived) then `awaiting_approval` (AI proposal ready) then `resolved` (approved or edited), or `manual_handling` (a human rejected the AI's draft).

**Files:** `src/supportpilot/guardrails.py`, `pipeline.py`, `main.py`, `models.py` (`Proposal`, `AuditEvent`), `tests/test_guardrails.py`, `tests/test_pipeline.py`.

## Flow 9: The evaluation run (Step 8)

```mermaid
flowchart TD
    A[labels.jsonl: 100 tickets with the true category] --> B[make_messy: Gemini rewrites each in 6 styles]
    B --> C[fix_order_numbers: replace any invented order number]
    C --> D[messy_tickets.jsonl]
    D --> E[eval_cases: attach the expected answer from order facts and policy]
    F[20 hand-written attacks on real orders] --> E
    E --> G[eval_run: process_ticket for every case, 4 at a time, rolled back each time]
    G --> H[eval_results.jsonl: saved after every case, resumable]
    H --> I[report: docs/eval-report.md]
```

Key points:
- The **expected answer never comes from the model.** It is computed from the order data and the policy tables.
- Each case runs in a database transaction that is **rolled back**, so the eval leaves no data behind.
- A case that failed because Gemini was down is **run again** next time, not counted as a wrong answer.
- Metrics: category accuracy, correct decision (exact amount), **unsafe** (too much money), "model asked above the ceiling" (the model was fooled and the guardrail had to step in), escalation rate, cost per ticket.

**Files:** `make_messy.py`, `eval_cases.py`, `eval_run.py`, `tests/test_eval.py`, `data/*.jsonl`, `docs/eval-report.md`.

## Flow 10: The review screen (Step 9)

```mermaid
sequenceDiagram
    participant B as Browser (index.html)
    participant API as FastAPI
    B->>API: GET /  (the page itself)
    B->>API: GET /tickets?status=awaiting_approval
    API-->>B: the queue
    B->>API: GET /tickets/6
    API-->>B: ticket + proposal + audit trail
    B->>API: POST /tickets/6/process  (only if not processed yet)
    B->>API: POST /proposals/5/decision  (approve / edit / reject)
    API-->>B: the final proposal, or 403 / 409 / 422 with a reason shown on the page
```

Everything the server sends is put on the page as plain text (`textContent`), never as HTML, so a hostile ticket cannot run code in a reviewer's browser.

**Files:** `static/index.html`, `main.py` (`GET /`), `tests/test_health.py`.
