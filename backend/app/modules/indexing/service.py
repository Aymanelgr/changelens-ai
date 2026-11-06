"""Computes the vectors a run needs (pipeline step 5).

Indexing happens at the start of an analysis rather than at import. An import stays fast and
works even when the model is missing, and only versions actually analyzed pay the cost. The
full-text columns need no work here: PostgreSQL maintains them as generated columns.
"""

import hashlib
from collections.abc import Callable, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.code_analysis.models import CodeSymbol, RepositorySnapshot
from app.modules.indexing.embeddings import Embedder
from app.modules.ingestion.models import Document, DocumentVersion, Segment

_BATCH = 32
_SYMBOL_BODY_CHARS = 1000


def symbol_embedding_text(symbol: CodeSymbol) -> str:
    """Signature, docstring and start of the body, as section 3 step 5 specifies."""
    parts = [symbol.file_path, symbol.qualified_name, symbol.signature, symbol.docstring]
    head = "\n".join(part for part in parts if part)
    return f"{head}\n{symbol.source_text[:_SYMBOL_BODY_CHARS]}"


def _embed_in_batches(
    embedder: Embedder, texts: list[str], progress: Callable[[int, int], None] | None
) -> list[list[float]]:
    vectors: list[list[float]] = []
    for start in range(0, len(texts), _BATCH):
        vectors += embedder.embed_documents(texts[start : start + _BATCH])
        if progress:
            progress(len(vectors), len(texts))
    return vectors


def ensure_indexed(
    db: Session,
    embedder: Embedder,
    project_id: int,
    version_ids: Sequence[int],
    snapshot_ids: Sequence[int],
    progress: Callable[[int, int], None] | None = None,
) -> dict[str, int]:
    """Fills in missing vectors. A text already embedded by this model in this project, in any
    earlier version, reuses its vector: unchanged content costs nothing (step 5)."""
    model = embedder.model_name
    segments = db.scalars(
        select(Segment).where(
            Segment.document_version_id.in_(version_ids),
            (Segment.embedding.is_(None)) | (Segment.embedding_model != model),
        )
    ).all()
    symbols = db.scalars(
        select(CodeSymbol).where(
            CodeSymbol.snapshot_id.in_(snapshot_ids),
            (CodeSymbol.embedding.is_(None)) | (CodeSymbol.embedding_model != model),
        )
    ).all()
    if not segments and not symbols:
        return {"embedded": 0, "reused": 0}

    known = dict(
        db.execute(
            select(Segment.sha256, Segment.embedding)
            .join(DocumentVersion, DocumentVersion.id == Segment.document_version_id)
            .join(Document, Document.id == DocumentVersion.document_id)
            .where(
                Document.project_id == project_id,
                Segment.embedding_model == model,
                Segment.sha256.in_({segment.sha256 for segment in segments}),
            )
        ).all()
    )
    symbol_hashes = {
        symbol.id: hashlib.sha256(symbol_embedding_text(symbol).encode()).hexdigest()
        for symbol in symbols
    }
    known_symbols = dict(
        db.execute(
            select(CodeSymbol.embedding_sha256, CodeSymbol.embedding)
            .join(RepositorySnapshot, RepositorySnapshot.id == CodeSymbol.snapshot_id)
            .where(
                RepositorySnapshot.project_id == project_id,
                CodeSymbol.embedding_model == model,
                CodeSymbol.embedding_sha256.in_(set(symbol_hashes.values())),
            )
        ).all()
    )

    to_embed: list[tuple[Segment | CodeSymbol, str]] = []
    reused = 0
    for segment in segments:
        vector = known.get(segment.sha256)
        if vector is None:
            to_embed.append((segment, segment.text))
        else:
            segment.embedding, segment.embedding_model = vector, model
            reused += 1
    for symbol in symbols:
        symbol.embedding_sha256 = symbol_hashes[symbol.id]
        vector = known_symbols.get(symbol.embedding_sha256)
        if vector is None:
            to_embed.append((symbol, symbol_embedding_text(symbol)))
        else:
            symbol.embedding, symbol.embedding_model = vector, model
            reused += 1

    vectors = _embed_in_batches(embedder, [text for _, text in to_embed], progress)
    for (row, _), vector in zip(to_embed, vectors, strict=True):
        row.embedding, row.embedding_model = vector, model
    return {"embedded": len(to_embed), "reused": reused}
