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
