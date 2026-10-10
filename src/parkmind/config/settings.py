"""
Loads configuration from environment variables (.env, see .env.example at
repo root). Plain and simple on purpose — no pydantic-settings needed for
a project this size.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


class Settings:
    ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
    ELICIT_MODEL = os.getenv("ELICIT_MODEL", "claude-haiku-4-5-20251001")
    DATABASE_URL = os.getenv(
        "DATABASE_URL", "postgresql://parkmind:parkmind@localhost:5432/parkmind"
    )
    THEMEPARKS_BASE_URL = os.getenv(
        "THEMEPARKS_BASE_URL", "https://api.themeparks.wiki/v1"
    )
    OPEN_METEO_BASE_URL = os.getenv(
        "OPEN_METEO_BASE_URL", "https://api.open-meteo.com/v1"
    )
    # Knowledge store (P0-26): "pgvector" (semantic) or "in_memory" (keyword only).
    KNOWLEDGE_BACKEND = os.getenv("PARKMIND_KNOWLEDGE_BACKEND", "pgvector")
    EMBEDDING_MODEL = os.getenv(
        "PARKMIND_EMBEDDING_MODEL",
        "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
    )
    # Persistent, outside the repo: fastembed's own default is the OS temp dir.
    EMBEDDING_CACHE = os.getenv(
        "PARKMIND_EMBEDDING_CACHE",
        str(Path.home() / ".cache" / "parkmind" / "fastembed"),
    )


settings = Settings()
