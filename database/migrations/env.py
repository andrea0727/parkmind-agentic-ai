"""Alembic environment for ParkMind.

Migrations are hand-written SQL, so there is no ORM metadata and autogenerate is
not used (``target_metadata=None``).

The database URL is resolved in this order:
  1. ``config.attributes["connection_url"]`` -- set by
     ``parkmind.services.clients.postgres.migrate`` (tests, scripts);
  2. ``alembic -x url=...`` on the command line;
  3. ``DATABASE_URL`` via ``parkmind.config.settings``.
"""

import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import create_engine, pool

# `parkmind` is not pip-installed (see pytest.ini `pythonpath = src`), so the CLI
# needs the same hint pytest gets.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from parkmind.config.settings import settings
from parkmind.services.clients.postgres.migrate import to_sqlalchemy_url

config = context.config

if config.config_file_name is not None and config.attributes.get(
    "configure_logging", True
):
    fileConfig(config.config_file_name, disable_existing_loggers=False)


def _resolve_url() -> str:
    url = config.attributes.get("connection_url")
    if url is None:
        url = context.get_x_argument(as_dictionary=True).get("url")
    return to_sqlalchemy_url(url or settings.DATABASE_URL)


def run_migrations_offline() -> None:
    context.configure(
        url=_resolve_url(),
        target_metadata=None,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(_resolve_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=None)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
