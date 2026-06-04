"""_llm.parse_indexed_verdicts: guards against index shift / extra / missing keys
mis-aligning verdicts to items (#4)."""
from knowledge_weaver._llm import parse_indexed_verdicts


def test_parse_valid():
    assert parse_indexed_verdicts('{"1":"same","2":"different"}', 2) == {"1": "same", "2": "different"}


def test_parse_strips_fence():
    assert parse_indexed_verdicts('```json\n{"1":"keep"}\n```', 1) == {"1": "keep"}


def test_parse_rejects_extra_key():
    # model returned 3 keys for a 2-item chunk -> reject whole chunk (None)
    assert parse_indexed_verdicts('{"1":"a","2":"b","3":"c"}', 2) is None


def test_parse_rejects_missing_key():
    assert parse_indexed_verdicts('{"1":"a"}', 2) is None


def test_parse_rejects_shifted_keys():
    # keys start at 2 (off-by-one) -> would assign verdicts to wrong items -> reject
    assert parse_indexed_verdicts('{"2":"a","3":"b"}', 2) is None


def test_parse_rejects_nonjson():
    assert parse_indexed_verdicts("not json at all", 2) is None
    assert parse_indexed_verdicts('["a","b"]', 2) is None
