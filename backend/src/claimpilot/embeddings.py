"""Local embeddings (no API key). `hash` provider is a deterministic stand-in for tests."""

from __future__ import annotations

import hashlib
import math
import re
from functools import lru_cache
from typing import Protocol

from claimpilot.config import EMBEDDING_DIM, get_settings


class Embedder(Protocol):
    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...
    def embed_query(self, text: str) -> list[float]: ...


class HashEmbedder:
    """Bag-of-words hashed into EMBEDDING_DIM buckets, L2-normalized. Lexical, but deterministic."""

    def _embed(self, text: str) -> list[float]:
        vec = [0.0] * EMBEDDING_DIM
        for token in re.findall(r"[a-z0-9]+", text.lower()):
            h = int(hashlib.md5(token.encode()).hexdigest(), 16)
            vec[h % EMBEDDING_DIM] += 1.0
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._embed(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._embed(text)


class FastEmbedder:
    def __init__(self, model_name: str) -> None:
        from fastembed import TextEmbedding

        self.model = TextEmbedding(model_name)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [v.tolist() for v in self.model.passage_embed(texts)]

    def embed_query(self, text: str) -> list[float]:
        return next(iter(self.model.query_embed(text))).tolist()


@lru_cache
def get_embedder() -> Embedder:
    settings = get_settings()
    if settings.embedding_provider == "hash":
        return HashEmbedder()
    if settings.embedding_provider == "fastembed":
        return FastEmbedder(settings.embedding_model)
    raise ValueError(f"Unknown EMBEDDING_PROVIDER {settings.embedding_provider!r}")
