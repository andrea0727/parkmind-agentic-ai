# PostgreSQL 16 + pgvector (P0-26: the knowledge store's semantic search).
#
# Base: the official postgres image through its AWS ECR Public mirror -- the same
# image CI runs (#81), and no Docker Hub rate limit. There is no pgvector image
# outside Docker Hub, but the official image already carries the PGDG apt source
# (/etc/apt/sources.list.d/pgdg.list), so the extension is one package away.
# CI installs the same package into its service container (.github/workflows/ci.yml).
FROM public.ecr.aws/docker/library/postgres:16

RUN apt-get -o Acquire::Retries=5 update \
    && apt-get -o Acquire::Retries=5 install -y --no-install-recommends \
        "postgresql-${PG_MAJOR}-pgvector" \
    && rm -rf /var/lib/apt/lists/*
