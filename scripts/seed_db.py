"""Loads the reproducible development scenario into Postgres (backlog P0-12).

    poetry run alembic -c database/alembic.ini upgrade head   # once, creates the schema
    poetry run python scripts/seed_db.py

Seeds the database named by DATABASE_URL. Safe to re-run: it converges on the
same content. To start from scratch: `docker compose down -v && docker compose up -d`.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import psycopg

from parkmind.services.clients.postgres.connection import connect
from parkmind.services.clients.postgres.seed import seed_dev_scenario


def main() -> int:
    try:
        with connect() as conn:
            seed_dev_scenario(conn)
    except psycopg.errors.UndefinedTable:
        print(
            "The schema is missing. Run first:\n"
            "  poetry run alembic -c database/alembic.ini upgrade head",
            file=sys.stderr,
        )
        return 1
    except psycopg.OperationalError:
        print(
            "PostgreSQL is not reachable. Start it with `docker compose up -d` "
            "and check DATABASE_URL in .env.",
            file=sys.stderr,
        )
        return 1
    print("Seeded the development scenario (thread 'thread_seed').")
    return 0


if __name__ == "__main__":
    sys.exit(main())
