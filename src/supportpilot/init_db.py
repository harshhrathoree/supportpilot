"""Create the database tables. Safe to run many times.

Run with:  uv run python -m supportpilot.init_db
"""

from sqlalchemy import text

from supportpilot import models  # noqa: F401  (importing registers the tables on Base)
from supportpilot.db import Base, get_engine


def init_db() -> None:
    engine = get_engine()
    with engine.begin() as conn:
        # pgvector adds a "vector" column type, used for policy search in Step 6.
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    Base.metadata.create_all(engine)  # only creates tables that don't exist yet


if __name__ == "__main__":
    init_db()
    print("Database ready:", ", ".join(Base.metadata.tables))
