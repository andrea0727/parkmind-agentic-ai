# ParkMind demo app: React UI (ui/web) built in stage 1, served by FastAPI on :8000 (stage 2).
FROM node:22-slim AS ui
WORKDIR /ui
COPY ui/web/package.json ui/web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY ui/web/ ./
RUN npm run build

FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 POETRY_VIRTUALENVS_CREATE=false
WORKDIR /app
RUN pip install poetry==2.* poetry-plugin-export
COPY pyproject.toml poetry.lock ./
RUN poetry install --only main --no-root
COPY src ./src
COPY database ./database
COPY scripts ./scripts
COPY demo ./demo
COPY server.py ./
COPY --from=ui /ui/dist ./ui/web/dist
EXPOSE 8000
# One worker on purpose: sessions and session-only data live in this process's memory.
# The dev scenario is loaded only when PARKMIND_SEED_DEMO=1 (docker-compose.yml sets it).
CMD ["sh", "-c", "alembic -c database/alembic.ini upgrade head && { [ \"$PARKMIND_SEED_DEMO\" != 1 ] || python scripts/seed_db.py; } && python server.py"]
