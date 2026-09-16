import os

import psycopg
from dotenv import load_dotenv

load_dotenv()

database_url = os.getenv("DATABASE_URL")

if not database_url:
    raise RuntimeError("DATABASE_URL is not configured")

with psycopg.connect(database_url) as connection:
    with connection.cursor() as cursor:
        cursor.execute("SELECT current_database(), version();")
        database, version = cursor.fetchone()

        print(f"Database: {database}")
        print(f"PostgreSQL: {version}")

