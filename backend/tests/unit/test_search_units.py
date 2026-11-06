"""The parts of search that need no database: query building and rank fusion."""

from app.modules.indexing.search import RRF_K, SearchHit, build_tsquery, distinct_targets, fuse


def hit(row_id, target="t", keyword=None, vector=None, cosine=None):
    return SearchHit(
        "segment",
        row_id,
        target,
        keyword_rank=keyword,
        vector_rank=vector,
        cosine=cosine,
    )


def test_tsquery_ors_the_words_and_keeps_numbers():
    query = build_tsquery("Les virements supérieurs à 10 000 MAD")
    assert "'virements'" in query
    assert "'10'" in query
    assert " | " in query


def test_tsquery_cannot_be_broken_by_punctuation():
    assert "&" not in build_tsquery("a & b ! (c) 'd' virement:*")


def test_tsquery_of_nothing_is_none():
    assert build_tsquery("à ? !") is None


def test_rrf_favours_a_result_found_by_both_lists():
    both_kw = hit(1, keyword=5)
    both_vec = hit(1, vector=5, cosine=0.7)
    top_keyword_only = hit(2, keyword=1)
    fused = fuse([top_keyword_only, both_kw], [both_vec])
    assert [item.row_id for item in fused] == [1, 2]
    assert fused[0].score == 2 / (RRF_K + 5)
    assert fused[0].keyword_rank == 5
    assert fused[0].vector_rank == 5
    assert fused[0].cosine == 0.7


def test_rrf_ties_are_broken_the_same_way_every_time():
    first = fuse([hit(2, keyword=1)], [hit(1, vector=1)])
    second = fuse([hit(2, keyword=1)], [hit(1, vector=1)])
    assert [h.row_id for h in first] == [h.row_id for h in second] == [1, 2]


def test_one_result_per_target():
    hits = [hit(1, "a"), hit(2, "a"), hit(3, "b")]
    assert [h.row_id for h in distinct_targets(hits)] == [1, 3]
