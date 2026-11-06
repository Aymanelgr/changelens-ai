"""Hybrid search: keywords and meaning, fused by Reciprocal Rank Fusion (pipeline step 6).

Vector search is an exact scan of the vectors of the analyzed versions. An approximate index
such as HNSW would filter by version only after searching, which loses results when a project
holds many versions, and a scan is fast enough at this scale.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.code_analysis.models import CodeSymbol
from app.modules.ingestion.models import Document, DocumentVersion, Segment

# The constant from the original RRF paper: it dampens the difference between the top ranks.
RRF_K = 60
_WORD_RE = re.compile(r"[^\W\d_]{3,}|\d+", re.UNICODE)


class SearchMode(StrEnum):
    """The three baselines compared by the evaluation (part D)."""

    KEYWORD = "keyword"
    EMBEDDING = "embedding"
    HYBRID = "hybrid"


@dataclass
class SearchHit:
    kind: str  # "segment" or "symbol"
    row_id: int
    target_key: str
    keyword_rank: int | None = None
    vector_rank: int | None = None
    cosine: float | None = None
    score: float = 0.0
    row: Segment | CodeSymbol | None = field(default=None, repr=False)
    document_name: str | None = None


def segment_target_key(document_name: str, section_path: str | None) -> str:
    return f"doc:{document_name}#{section_path}" if section_path else f"doc:{document_name}"


def symbol_target_key(symbol: CodeSymbol) -> str:
    return f"code:{symbol.file_path}::{symbol.qualified_name}"


def build_tsquery(text: str) -> str | None:
    """Words joined by OR. Requiring all of them would make almost every search come back empty,
    since a passage about a rule rarely repeats the whole requirement. ts_rank does the sorting."""
    words = sorted({word.lower() for word in _WORD_RE.findall(text)})
    return " | ".join(f"'{word}'" for word in words) or None


def _segments_base(version_ids: Sequence[int]):
    return (
        select(Segment, Document.name)
        .join(DocumentVersion, DocumentVersion.id == Segment.document_version_id)
        .join(Document, Document.id == DocumentVersion.document_id)
        .where(Segment.document_version_id.in_(version_ids))
    )


def _keyword_hits(
    db: Session, text: str, version_ids: Sequence[int], snapshot_ids: Sequence[int], limit: int
) -> list[SearchHit]:
    tsquery_text = build_tsquery(text)
    if tsquery_text is None:
        return []
    query = func.to_tsquery("french", tsquery_text)
    scored: list[tuple[float, SearchHit]] = []

    rank = func.ts_rank(Segment.tsv, query)
    rows = db.execute(
        _segments_base(version_ids)
        .add_columns(rank)
        .where(Segment.tsv.op("@@")(query))
        .order_by(rank.desc(), Segment.id)
        .limit(limit)
    ).all()
    for segment, name, value in rows:
        scored.append(
            (
                value,
                SearchHit(
                    "segment",
                    segment.id,
                    segment_target_key(name, segment.section_path),
                    row=segment,
                    document_name=name,
                ),
            )
        )

    rank = func.ts_rank(CodeSymbol.tsv, query)
    symbols = db.execute(
        select(CodeSymbol, rank)
        .where(CodeSymbol.snapshot_id.in_(snapshot_ids), CodeSymbol.tsv.op("@@")(query))
        .order_by(rank.desc(), CodeSymbol.id)
        .limit(limit)
    ).all()
    for symbol, value in symbols:
        scored.append(
            (value, SearchHit("symbol", symbol.id, symbol_target_key(symbol), row=symbol))
        )

    # The row id breaks ties, so the same input always gives the same order.
    scored.sort(key=lambda item: (-item[0], item[1].kind, item[1].row_id))
    hits = [hit for _, hit in scored[:limit]]
    for position, hit in enumerate(hits, start=1):
        hit.keyword_rank = position
    return hits


def _vector_hits(
    db: Session,
    vector: list[float],
    model: str,
    version_ids: Sequence[int],
    snapshot_ids: Sequence[int],
    limit: int,
) -> list[SearchHit]:
    scored: list[tuple[float, SearchHit]] = []

    distance = Segment.embedding.cosine_distance(vector)
    rows = db.execute(
        _segments_base(version_ids)
        .add_columns(distance)
        .where(Segment.embedding_model == model)
        .order_by(distance, Segment.id)
        .limit(limit)
    ).all()
    for segment, name, value in rows:
        scored.append(
            (
                value,
                SearchHit(
                    "segment",
                    segment.id,
                    segment_target_key(name, segment.section_path),
                    cosine=1 - value,
                    row=segment,
                    document_name=name,
                ),
            )
        )

    distance = CodeSymbol.embedding.cosine_distance(vector)
    symbols = db.execute(
        select(CodeSymbol, distance)
        .where(CodeSymbol.snapshot_id.in_(snapshot_ids), CodeSymbol.embedding_model == model)
        .order_by(distance, CodeSymbol.id)
        .limit(limit)
    ).all()
    for symbol, value in symbols:
        scored.append(
            (
                value,
                SearchHit(
                    "symbol", symbol.id, symbol_target_key(symbol), cosine=1 - value, row=symbol
                ),
            )
        )

    scored.sort(key=lambda item: (item[0], item[1].kind, item[1].row_id))
    hits = [hit for _, hit in scored[:limit]]
    for position, hit in enumerate(hits, start=1):
        hit.vector_rank = position
    return hits


def fuse(keyword: list[SearchHit], vector: list[SearchHit]) -> list[SearchHit]:
    """score = sum of 1 / (60 + rank) over the lists a result appears in. It uses ranks only,
    so a ts_rank and a cosine, which live on unrelated scales, never have to be compared."""
    merged: dict[tuple[str, int], SearchHit] = {}
    for hit in [*keyword, *vector]:
        key = (hit.kind, hit.row_id)
        kept = merged.setdefault(key, hit)
        if hit.keyword_rank is not None:
            kept.keyword_rank = hit.keyword_rank
        if hit.vector_rank is not None:
            kept.vector_rank = hit.vector_rank
            kept.cosine = hit.cosine
    for hit in merged.values():
        hit.score = sum(
            1 / (RRF_K + rank) for rank in (hit.keyword_rank, hit.vector_rank) if rank is not None
        )
    return sorted(merged.values(), key=lambda h: (-h.score, h.kind, h.row_id))


def distinct_targets(hits: list[SearchHit]) -> list[SearchHit]:
    """One result per target: three paragraphs of one section are one thing to review."""
    seen: set[str] = set()
    unique = []
    for hit in hits:
        if hit.target_key not in seen:
            seen.add(hit.target_key)
            unique.append(hit)
    return unique


def search(
    db: Session,
    text: str,
    vector: list[float] | None,
    model: str | None,
    version_ids: Sequence[int],
    snapshot_ids: Sequence[int],
    top_k: int,
    mode: SearchMode = SearchMode.HYBRID,
) -> list[SearchHit]:
    """Returns at most top_k distinct targets, best first. Each list goes deeper than top_k,
    because several of its results can land on the same target."""
    depth = top_k * 3
    keyword = (
        _keyword_hits(db, text, version_ids, snapshot_ids, depth)
        if mode in {SearchMode.KEYWORD, SearchMode.HYBRID}
        else []
    )
    semantic = (
        _vector_hits(db, vector, model, version_ids, snapshot_ids, depth)
        if vector is not None and model and mode in {SearchMode.EMBEDDING, SearchMode.HYBRID}
        else []
    )
    return distinct_targets(fuse(keyword, semantic))[:top_k]
