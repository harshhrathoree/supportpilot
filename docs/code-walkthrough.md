# Code walkthrough

A beginner-friendly explanation of every piece of code, in the order we wrote it. Read the matching section after each step.

## Step 0: Project skeleton

### Big picture
A Python project needs three things: a list of its dependencies, the code itself, and a way to run and test it. `uv` manages the first, `src/supportpilot/` holds the second, and `pytest` plus `uvicorn` handle the third.

### `pyproject.toml`: the project's ID card
- `[project]` holds the name, version and the Python version we need (`>=3.12`).
- `dependencies` lists libraries the app needs to run:
  - **fastapi**: the web framework. It lets us write `@app.get("/health")` and get a working web endpoint.
  - **uvicorn**: the server program that listens for HTTP requests and hands them to FastAPI. FastAPI defines *what* to do; Uvicorn is what receives the requests.
  - **pydantic-settings**: reads configuration (like API keys) from environment variables or a `.env` file.
- `[dependency-groups] dev` lists tools only used while developing: **pytest** (tests), **httpx2** (the library FastAPI's test client uses to make fake requests), **ruff** (a linter and formatter that catches mistakes and keeps the style consistent).
- `[build-system]` tells Python how to package the project. We don't touch it.
- `[tool.ruff]` and `[tool.pytest.ini_options]` are settings for those tools: a 100-character line limit, and tests live in `tests/`.

`uv sync` reads this file and installs everything into `.venv/`, a private folder of libraries for this project only. That keeps it from clashing with other Python projects.

### `.gitignore`
Lists files Git must never track. The important one is `.env`, because it will hold secret keys. Also ignored: `.venv/` (big and rebuildable) and cache folders.

### `.env.example` and `.env`
`.env.example` is a template that **is** committed, so anyone can see which settings are needed. You copy it to `.env`, which is **not** committed, and put the real keys there.

### `src/supportpilot/__init__.py`
An (almost) empty file. Its presence tells Python that the folder is a package that can be imported as `supportpilot`. The one line in it is a docstring, a description of the package.

### `src/supportpilot/config.py`: settings in one place
```python
class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    gemini_api_key: str = ""
    database_url: str = ""


settings = Settings()
```
- `BaseSettings` (from pydantic-settings) automatically fills the fields from environment variables with the same name, like `GEMINI_API_KEY`, or from `.env`.
- `env_file=".env"` says to also read the `.env` file.
- `extra="ignore"` means unknown keys in `.env` won't cause errors.
- The last line creates one `settings` object. Other files do `from supportpilot.config import settings`. We never read secrets directly anywhere else, so there's one place to change.

### `src/supportpilot/main.py`: the web app
```python
app = FastAPI(title="SupportPilot", version="0.1.0")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
```
- `app = FastAPI(...)` creates the application object. Uvicorn is told to run `supportpilot.main:app`, meaning "the `app` variable in the `main` module".
- `@app.get("/health")` is a **decorator**. It registers the function below it as the handler for `GET /health`.
- The function returns a dictionary and FastAPI converts it to JSON.
- `-> dict[str, str]` is a **type hint**. It says the function returns a dictionary of strings. FastAPI uses it to document and validate the response.

### `tests/test_health.py`: our first test
```python
client = TestClient(app)


def test_health_returns_ok():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
```
- `TestClient` calls the app in-memory, with no real server needed.
- A test is a function whose name starts with `test_`. `pytest` finds and runs these automatically.
- `assert` means "this must be true, or fail the test". We check the HTTP status is 200 (success) and the body is what we expect.
- Why test something this tiny? It proves the whole toolchain (install, import, run, test) works. Every later step builds on that.

## Step 1: Database and schema

### Big picture
The agent needs real data to check a complaint against: who ordered what, when it was promised and when it arrived. We store that in **Postgres** hosted on **Neon**. We talk to it from Python with **SQLAlchemy**, a library that lets us describe tables as Python classes instead of writing SQL by hand.

New dependencies (added with `uv add`):
- **sqlalchemy**: the library that maps Python classes to database tables (an "ORM").
- **psycopg**: the driver, the low-level piece that actually speaks to Postgres over the network.

### `src/supportpilot/db.py`: the connection
- `class Base(DeclarativeBase)`: an empty parent class. Every table class we write inherits from it, so SQLAlchemy can keep a list of all tables in `Base.metadata`.
- `to_driver_url(url)`: Neon's connection string starts with `postgresql://`. SQLAlchemy needs `postgresql+psycopg://` to know which driver to use. This function swaps the prefix, so you can paste Neon's string into `.env` unchanged.
- `get_engine()`: creates the **engine**, which manages a pool of reusable connections. `@cache` means it's built once and then reused, because opening connections is slow. `pool_pre_ping=True` tests a connection before using it. Neon pauses idle databases to save resources, so an old connection may be dead, and this avoids random errors.
- `get_session()`: a **session** is one conversation with the database: you add or query objects, then `commit()` to save them or roll back on error. `expire_on_commit=False` keeps objects readable after a commit.

### `src/supportpilot/models.py`: the tables
Each class is a table, and each `Mapped[...] = mapped_column(...)` line is a column.
- **Customer**: name and phone. `created_at` is filled by the database itself (`server_default=func.now()`).
- **Order**: one purchase. `customer_id` is a **foreign key**: a number that must match an existing customer's `id`, which is how tables link. `promised_at` (the ETA we gave) and `delivered_at` (when it really arrived) let the agent detect late deliveries. `delivered_at` is `datetime | None` because an order not yet delivered has no value, and the `| None` makes the column nullable.
- **OrderItem**: one dish in an order. `packed` is `False` when the restaurant forgot it, the proof for a "missing item" complaint.
- **Ticket**: the complaint. `category` is empty until the classifier fills it in (Step 4). `status` moves from `open` to `awaiting_approval` to `resolved`.
- `relationship(...)` is a Python-side shortcut, not a column. With it, `order.items` gives you the list of items, and `order.customer` gives the customer, with no manual joins.
- The `index=True` on foreign keys makes lookups like "all orders of customer 5" fast.

### `src/supportpilot/init_db.py`: create the tables
- `CREATE EXTENSION IF NOT EXISTS vector` turns on **pgvector** in Neon, which lets Postgres store embeddings (lists of numbers that represent meaning). We use it in Step 6.
- `Base.metadata.create_all(engine)` creates every table that doesn't exist yet. Existing tables are untouched, so it is safe to run again.
- The line `from supportpilot import models  # noqa: F401` looks unused, but importing it is what registers the table classes with `Base`. Without it, `create_all` would see zero tables. The `noqa` comment tells the linter to ignore that warning.
- `if __name__ == "__main__":` runs the code only when you execute the file directly, not when something imports it.

> Later we may add Alembic migrations for changing existing tables. For now `create_all` is enough while the schema is still settling.

### `tests/test_models.py`
- Tests use an **in-memory SQLite** database (`create_engine("sqlite://")`), so they are instant and never touch your real Neon data.
- A **fixture** (`@pytest.fixture def session`) builds a fresh empty database for each test and hands it over. Any test that lists `session` as a parameter receives it automatically.
- `test_to_driver_url_...` checks the URL prefix swap.
- `test_ticket_links_to_order_and_items` builds a customer, an order with a missing raita, and a ticket, saves them, reads the ticket back, and checks the link to the unpacked item works. It proves the relationships behave the way the agent will need.

## Step 2: Mock data generator

### Big picture
We have no real customers, so we invent them. The important rule is that the invented data must be **consistent**: if a ticket says "my order came 40 minutes late", the order record must really show a 40-minute delay. Otherwise the agent could never verify claims, and our accuracy numbers would mean nothing. The code is split in two so each half is easy to test:
- `mock_data.py` builds everything **in memory** and never touches the database.
- `seed.py` **saves** it to Neon.

### `src/supportpilot/mock_data.py`

**Constants at the top**
- `ANCHOR` is a fixed "now" (27 Nov 2026). Using a fixed date instead of the real current time means the data is the same whenever you generate it.
- `TICKET_MIX` says how many tickets of each category to make (18 late, 18 missing, and so on, 100 total).
- `MENUS` is six fake restaurants with dishes and prices. `TEMPLATES` is five complaint sentences per category, with `{placeholders}` such as `{item}` and `{restaurant}`.

**`random.Random(seed)`**
A random number generator that starts from a seed number. The same seed always produces the same "random" numbers, so `generate(42)` returns identical data every time. We pass this `rng` object around instead of using the global `random`, so no hidden state creeps in.

**`make_customer`** builds a random name and a 10-digit phone number starting with 9.

**`make_order`** builds one order, with its items.
- It picks a restaurant and 2 to 4 dishes from its menu, and a quantity of 1 or 2 for each.
- `promised_at` is 30 to 45 minutes after `placed_at`.
- `delivered_at = promised_at + delay`. By default the delay is between -10 (early) and +5 minutes, which counts as on time. The `late_by` argument forces a specific lateness.
- `drop_item=True` marks one random item `packed=False`.
- The `*` in the signature means everything after it must be passed by name (`late_by=40`), which stops mistakes like mixing up arguments.
- We only create the objects here. `Order(customer=customer, ...)` links the order to the customer, which is how the order gets saved later with the customer.

**`make_ticket`** builds one ticket for a category. For each category it builds an order that matches:
- `late_delivery`: the order arrives 25 to 70 minutes late, and `{mins}` in the text is that same number.
- `missing_item`: one item is unpacked, and `{item}` in the text is that exact item.
- `refund_status`: the order is `cancelled`.
- `payment_issue` / `refund_status`: paid by UPI or card (cash on delivery has nothing to refund).
- `other`: no order, since questions like "how do I change my phone number" aren't about an order.
- `template.format(**fields)` fills the placeholders. About 20% of tickets are lower-cased, because real customers often don't capitalise.
- The `<order>` placeholder stays in the text for now (see `seed.py`).

**`TicketSpec`** is a `@dataclass`, a class that is just a bundle of fields, with `__init__` generated for you. It holds a ticket that isn't saved yet: the customer, the order, the true `category`, and the text. Its `render()` swaps `<order>` for the real order id.

**`generate`** puts it all together:
1. 50 customers.
2. 100 tickets, according to `TICKET_MIX` (each creates its own matching order).
3. A shuffle, so the categories aren't in blocks.
4. About 300 extra "background" orders with no complaint, up to 500 orders in total, so the agent must find the right order among many.

### `src/supportpilot/seed.py`
- `reset_tables`: deletes every row from every table. `reversed(Base.metadata.sorted_tables)` deletes children before parents, since a foreign key does not let you delete a customer who still has orders. `session.expunge_all()` tells the session to forget the objects it loaded earlier. Without it, new rows that reuse old ids collide with stale objects, which gave us warnings in the tests.
- `load`:
  1. `session.add_all(data.customers)` saves customers, and SQLAlchemy automatically saves their orders and items too, because of the relationships.
  2. `session.flush()` sends the INSERTs to the database **without committing**. The database assigns ids, and now `order.id` has a value.
  3. We create each `Ticket` using the real order id, and replace `<order>` in the text.
  4. It returns the list of labels.
- `write_labels` writes `data/labels.jsonl`: **JSON Lines**, one JSON object per line. It's easy to read line by line and easy to append to, and is the usual format for eval data.
- `main` runs it all in one session. The labels file is written only **after** `commit()`, so a failure can't leave you with labels that don't match the database.
- **Warning:** `seed` deletes existing data. That's fine now, but it must never be pointed at a database with real data.

### `tests/test_mock_data.py`
Each test checks one promise the generator makes:
- counts match the plan (50 / 500 / 100, and each category count)
- the same seed gives the same data, and a different seed differs
- late tickets have orders at least 25 minutes late, and every other delivered order is on time or at most 5 minutes late
- missing-item tickets have exactly one unpacked item, and its name appears in the text
- refund-status orders are cancelled, and `other` tickets have no order
- `load` saves everything, no `<order>` placeholder is left in any text, and the labels point at real tickets
- loading twice replaces rather than duplicates

### Honest limitation
Each category has only five sentence templates, so a classifier will find this set **too easy**, and an accuracy score on it would flatter the system. In Step 8, before we trust any numbers, we will have Gemini write varied and messy tickets (typos, mixed Hindi and English, vague wording) and add them to the eval set.
