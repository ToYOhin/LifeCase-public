"""Regression coverage for safe Live Agent retrieval query construction."""

import pytest

from lifecase.agent.demo import _safe_retrieval_query, _warranty_alias_terms


def test_english_goal_drops_task_words_and_prioritizes_product_identifiers() -> None:
    query = _safe_retrieval_query(
        "Verify HAIXING-H8 E21 warranty coverage and prepare facts for on-site inspection."
    )

    assert query.startswith('"haixing-h8" OR "e21"')
    assert " AND " not in query
    for stopword in ("verify", "prepare", "facts", "for"):
        assert f'"{stopword}"' not in query


@pytest.mark.parametrize(
    ("prefix", "expected_term"), (("核查保修 ", "核查保修"), ("repair ", "repair"))
)
def test_oversized_identifier_does_not_discard_later_safe_terms(
    prefix: str, expected_term: str
) -> None:
    query = _safe_retrieval_query(prefix + "A" * 255 + "1")

    assert query
    assert len(query) <= 256
    assert "aaa" not in query
    assert f'"{expected_term}"' in query


def test_only_oversized_identifier_uses_nonempty_safe_fallback() -> None:
    assert _safe_retrieval_query("A" * 255 + "1") == '"case" OR "goal"'


@pytest.mark.parametrize(
    ("goal", "expected_terms"),
    (
        ("Check warranty coverage", {"warranty", "保修期"}),
        ("核验保修范围", {"warranty", "保修期"}),
        ("Prepare repair materials", {"repair", "维修单"}),
        ("整理维修材料", {"repair", "维修单"}),
        ("Verify purchase date", {"purchase", "购买日"}),
        ("核验购买日期", {"purchase", "购买日"}),
        ("Record fault details", {"fault", "故障现"}),
        ("记录故障情况", {"fault", "故障现"}),
        ("Prepare service request", {"service", "报修回"}),
        ("准备报修申请", {"service", "报修回"}),
    ),
)
def test_warranty_aliases_accept_english_and_chinese_intent_markers(
    goal: str,
    expected_terms: set[str],
) -> None:
    assert expected_terms <= set(_warranty_alias_terms(goal))
