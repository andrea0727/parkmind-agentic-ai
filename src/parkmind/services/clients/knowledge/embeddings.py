"""Text embedders for the knowledge store (P0-26). Adapter-internal: no port.

* ``FastEmbedEmbedder`` -- the configured multilingual model
  (``paraphrase-multilingual-MiniLM-L12-v2``, 384 dimensions) run locally with
  ONNX. The weights are downloaded **once** into a persistent cache
  (``scripts/fetch_embedding_model.py``); at runtime the model is only ever
  loaded from that cache (``local_files_only``), lazily and once per process,
  so a request never triggers a download. If the model is not there, every
  call raises ``KnowledgeUnavailableError`` and search degrades (section 43).
* ``HashingEmbedder`` -- deterministic hashed bag of words and character
  trigrams, standard library only. It is what tests and CI index with: lexical,
  not semantic, but stable across machines and needing no download.

``embedder_id`` names the model; the index stores it with every vector, so
vectors from different models are never compared.
"""

import hashlib
import math
import re
import threading
import unicodedata
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Protocol

from parkmind.services.ports import KnowledgeUnavailableError

DIMENSION = 384
DEFAULT_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
DEFAULT_CACHE = Path.home() / ".cache" / "parkmind" / "fastembed"


class Embedder(Protocol):
    @property
    def embedder_id(self) -> str: ...

    @property
    def dimension(self) -> int: ...

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


def fold(text: str) -> str:
    """Lowercase without accents: 'Montaña' and 'montana' are the same token."""
    decomposed = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", fold(text))


class HashingEmbedder:
    """Deterministic lexical embedding: signed feature hashing, L2-normalized."""

    def __init__(self, dimension: int = DIMENSION, *, salt: str = "v1") -> None:
        self._dimension = dimension
        self._salt = salt

    @property
    def embedder_id(self) -> str:
        return f"hashing-{self._salt}@{self._dimension}"

    @property
    def dimension(self) -> int:
        return self._dimension

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._embed(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._embed(text)

    def _embed(self, text: str) -> list[float]:
        vector = [0.0] * self._dimension
        tokens = words(text)
        features = tokens + [
            w[i : i + 3] for w in tokens if len(w) > 3 for i in range(len(w) - 2)
        ]
        for feature in features:
            digest = hashlib.blake2b(
                f"{self._salt}:{feature}".encode(), digest_size=8
            ).digest()
            bucket = int.from_bytes(digest[:4], "little") % self._dimension
            sign = 1.0 if digest[4] & 1 else -1.0
            vector[bucket] += sign
        norm = math.sqrt(sum(v * v for v in vector)) or 1.0
        return [v / norm for v in vector]


class FastEmbedEmbedder:
    """The local ONNX model, loaded from the persistent cache only."""

    def __init__(
        self,
        model_name: str = DEFAULT_MODEL,
        *,
        cache_dir: str | Path = DEFAULT_CACHE,
        local_files_only: bool = True,
    ) -> None:
        self._model_name = model_name
        self._cache_dir = Path(cache_dir).expanduser()
        self._local_files_only = local_files_only
        self._model: Any = None
        self._lock = threading.Lock()

    @property
    def embedder_id(self) -> str:
        return self._model_name

    @property
    def dimension(self) -> int:
        return DIMENSION

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        model = self._load()
        return [[float(x) for x in vector] for vector in model.embed(list(texts))]

    def embed_query(self, text: str) -> list[float]:
        return self.embed_documents([text])[0]

    def available(self) -> bool:
        """Whether the model loads from the cache (never downloads)."""
        try:
            self._load()
        except KnowledgeUnavailableError:
            return False
        return True

    def fetch(self) -> Path:
        """Download the model into the cache (setup only), then return the cache dir."""
        from fastembed import TextEmbedding

        self._cache_dir.mkdir(parents=True, exist_ok=True)
        TextEmbedding(model_name=self._model_name, cache_dir=str(self._cache_dir))
        return self._cache_dir

    def _load(self) -> Any:
        if self._model is not None:
            return self._model
        with self._lock:
            if self._model is None:
                try:
                    from fastembed import TextEmbedding

                    self._model = TextEmbedding(
                        model_name=self._model_name,
                        cache_dir=str(self._cache_dir),
                        local_files_only=self._local_files_only,
                    )
                except (
                    Exception
                ) as exc:  # the library raises several types for a missing model
                    raise KnowledgeUnavailableError(
                        f"embedding model {self._model_name!r} is not available in "
                        f"{self._cache_dir} ({type(exc).__name__}); run "
                        "scripts/fetch_embedding_model.py once"
                    ) from exc
        return self._model
