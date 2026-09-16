import os

from dotenv import load_dotenv
from langgraph.checkpoint.postgres import PostgresSaver

load_dotenv()

database_url = os.getenv("DATABASE_URL")

if not database_url:
    raise RuntimeError("DATABASE_URL is not configured")

with PostgresSaver.from_conn_string(database_url) as checkpointer:
    checkpointer.setup()

print("LangGraph PostgreSQL checkpointer initialized successfully.")

