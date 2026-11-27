"""FastAPI application entry point."""

from fastapi import FastAPI

app = FastAPI(title="SupportPilot", version="0.1.0")


@app.get("/health")
def health() -> dict[str, str]:
    """Liveness check: returns ok if the server is running."""
    return {"status": "ok"}
