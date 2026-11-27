"""Entry point for Vercel, which looks for an `app` object in a file like this one.

Our code lives in src/supportpilot, so we add src to the import path first.
Locally you still run:  uv run uvicorn supportpilot.main:app
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from supportpilot.main import app  # noqa: F401
