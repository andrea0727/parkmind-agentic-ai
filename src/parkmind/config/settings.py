"""
Loads configuration from environment variables (.env, see .env.example at
repo root). Plain and simple on purpose — no pydantic-settings needed for
a project this size.
"""

import os
from dotenv import load_dotenv

load_dotenv()


class Settings:
    ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
    DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://parkmind:parkmind@localhost:5432/parkmind")
    THEMEPARKS_BASE_URL = os.getenv("THEMEPARKS_BASE_URL", "https://api.themeparks.wiki/v1")
    OPEN_METEO_BASE_URL = os.getenv("OPEN_METEO_BASE_URL", "https://api.open-meteo.com/v1")


settings = Settings()
