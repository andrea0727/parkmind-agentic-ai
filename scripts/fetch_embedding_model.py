"""Downloads the knowledge store's embedding model once (backlog P0-26).

    poetry run python scripts/fetch_embedding_model.py

The model (PARKMIND_EMBEDDING_MODEL, multilingual MiniLM by default, ~220 MB from
Hugging Face) goes into PARKMIND_EMBEDDING_CACHE (~/.cache/parkmind/fastembed
by default: persistent, outside the repo). After this, everything runs offline:
the server only ever loads the model from that cache, never downloads it during
a request. Re-running is cheap when the model is already there.

Exit codes: 0 model ready, 1 download or offline load failed.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from parkmind.config.settings import settings
from parkmind.services.clients.knowledge.embeddings import FastEmbedEmbedder


def main() -> int:
    downloader = FastEmbedEmbedder(
        settings.EMBEDDING_MODEL,
        cache_dir=settings.EMBEDDING_CACHE,
        local_files_only=False,
    )
    print(f"model: {settings.EMBEDDING_MODEL}\ncache: {downloader.fetch()}")
    offline = FastEmbedEmbedder(
        settings.EMBEDDING_MODEL, cache_dir=settings.EMBEDDING_CACHE
    )
    if not offline.available():
        print("the model downloaded but does not load offline", file=sys.stderr)
        return 1
    print(f"ready: loads offline, {len(offline.embed_query('prueba'))} dimensions")
    return 0


if __name__ == "__main__":
    sys.exit(main())
