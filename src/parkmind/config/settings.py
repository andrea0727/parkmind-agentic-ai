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
    EXPLAIN_MODEL = os.getenv("EXPLAIN_MODEL", ELICIT_MODEL)
    # EXPLAIN is on the plan's path: seconds per request and the client's own retries
    # (the attempt loop already retries once, so 0 by default).
    EXPLAIN_TIMEOUT_SECONDS = float(os.getenv("EXPLAIN_TIMEOUT_SECONDS", "20"))
    EXPLAIN_SDK_RETRIES = int(os.getenv("EXPLAIN_SDK_RETRIES", "0"))
    # G12: LLM calls (retries included) one thread may make; past it the agents use
    # their deterministic path (elicit asks again, explain writes the template).
    LLM_CALLS_PER_THREAD = max(1, int(os.getenv("PARKMIND_LLM_CALLS_PER_THREAD", "40")))
    MAX_PARTY_SIZE = max(1, int(os.getenv("PARKMIND_MAX_PARTY_SIZE", "10")))
    DATABASE_URL = os.getenv(
        "DATABASE_URL", "postgresql://parkmind:parkmind@localhost:5432/parkmind"
    )
    THEMEPARKS_BASE_URL = os.getenv(
        "THEMEPARKS_BASE_URL", "https://api.themeparks.wiki/v1"
    )
    OPEN_METEO_BASE_URL = os.getenv(
        "OPEN_METEO_BASE_URL", "https://api.open-meteo.com/v1"
    )
    # LOAD CONTEXT transport: "in_process" (the direct snapshot path) or
    # "mcp" (data.*/knowledge.* through parkmind-mcp at MCP_URL, in-process fallback).
    CONTEXT_TRANSPORT = os.getenv("PARKMIND_CONTEXT_TRANSPORT", "in_process")
    MCP_URL = os.getenv("PARKMIND_MCP_URL", "http://127.0.0.1:8765/mcp")
    # Knowledge store: "pgvector" (semantic) or "in_memory" (keyword only).
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
