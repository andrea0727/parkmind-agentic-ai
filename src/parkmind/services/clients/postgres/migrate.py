"""Programmatic Alembic entry points (tests, scripts).

The migrations themselves live in ``database/migrations`` at the repo root; this
module only locates them and points Alembic at a database URL.
"""

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy.engine import make_url

# src/parkmind/services/clients/postgres/migrate.py -> repo root
_REPO_ROOT = Path(__file__).resolve().parents[5]
ALEMBIC_INI = _REPO_ROOT / "database" / "alembic.ini"


def to_sqlalchemy_url(url: str) -> str:
    """Return ``url`` with the psycopg 3 driver, e.g.
    ``postgresql://u:p@h/db`` -> ``postgresql+psycopg://u:p@h/db``.
    """
    parsed = make_url(url)
    if parsed.drivername == "postgresql":
        parsed = parsed.set(drivername="postgresql+psycopg")
    return parsed.render_as_string(hide_password=False)


def _config(url: str) -> Config:
    if not ALEMBIC_INI.is_file():
        raise RuntimeError(
            f"Alembic config not found at {ALEMBIC_INI}; migrations are only "
            "available from a source checkout."
        )
    cfg = Config(str(ALEMBIC_INI))
    # Not `set_main_option`: configparser would treat a `%` in the password as
    # interpolation.
    cfg.attributes["connection_url"] = url
    cfg.attributes["configure_logging"] = False
    return cfg


def upgrade(url: str, revision: str = "head") -> None:
    command.upgrade(_config(url), revision)


def downgrade(url: str, revision: str = "base") -> None:
    command.downgrade(_config(url), revision)
