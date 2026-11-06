"""Turns text into vectors (pipeline step 5). Replaceable behind one small interface.

python -m app.modules.indexing.embeddings    # downloads the configured model ahead of time
"""

import threading
from typing import Protocol

from app.core.config import get_settings


class EmbeddingUnavailable(RuntimeError):
    """The model could not be loaded. Search then falls back to keywords alone."""


class Embedder(Protocol):
    model_name: str

    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


class SentenceTransformerEmbedder:
    """Runs locally. Nothing is sent outside the machine, apart from the first model download."""

    def __init__(self, model_name: str):
        self.model_name = model_name
        self._model = None
        self._lock = threading.Lock()
        # The E5 family was trained with these prefixes and ranks worse without them.
        self._uses_prefixes = "e5" in model_name.lower()

    def _load(self):
        with self._lock:
            if self._model is None:
                try:
                    # Imported here: it takes seconds and hundreds of MB, and many code paths
                    # (tests, rules-only analyses) never need it.
                    from sentence_transformers import SentenceTransformer

                    self._model = SentenceTransformer(self.model_name, device="cpu")
                except Exception as exc:  # no network, disk full, unknown model name…
                    raise EmbeddingUnavailable(str(exc)) from exc
        return self._model

    def _encode(self, texts: list[str]) -> list[list[float]]:
        vectors = self._load().encode(
            texts, batch_size=16, normalize_embeddings=True, show_progress_bar=False
        )
        return [vector.tolist() for vector in vectors]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        prefix = "passage: " if self._uses_prefixes else ""
        return self._encode([prefix + text for text in texts])

    def embed_query(self, text: str) -> list[float]:
        prefix = "query: " if self._uses_prefixes else ""
        return self._encode([prefix + text])[0]


_instances: dict[str, SentenceTransformerEmbedder] = {}


def get_embedder(model_name: str | None = None) -> Embedder | None:
    """None when embeddings are switched off. One instance per model: loading is expensive."""
    settings = get_settings()
    if settings.embedding_provider == "none":
        return None
    name = model_name or settings.embedding_model
    if name not in _instances:
        _instances[name] = SentenceTransformerEmbedder(name)
    return _instances[name]


if __name__ == "__main__":
    embedder = get_embedder()
    if embedder is None:
        raise SystemExit("EMBEDDING_PROVIDER is set to none: nothing to download.")
    print(f"{embedder.model_name}: {len(embedder.embed_query('test'))} dimensions, ready.")
