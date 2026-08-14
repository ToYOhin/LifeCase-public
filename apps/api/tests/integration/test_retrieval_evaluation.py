"""Synthetic Chinese lexical-retrieval evaluation for the M1-05 tokenizer."""

import hashlib
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from lifecase.retrieval import RetrievalService
from lifecase.storage import apply_migrations, connect_database

RECALL_AT_3_MINIMUM = 1.0
MRR_MINIMUM = 0.9
_NOW = "2026-08-01T00:00:00.000000Z"

_DOCUMENTS = (
    ("seg_purchase", "订单显示电热水壶购买日期为二零二六年七月十二日。"),
    ("seg_fault", "保修申请需要提交故障照片和产品序列号。"),
    ("seg_delivery", "快递签收后发现包装破损，需要保存外箱材料。"),
    ("seg_refund", "退款记录中的订单编号为合成编号七三九一。"),
    ("seg_subscription", "订阅取消将在下个计费周期生效。"),
    ("seg_expense", "差旅报销需要上传电子发票和行程说明。"),
    ("seg_distractor", "本段仅用于增加合成材料中的非相关候选。"),
)

_QUERIES = (
    ("购买日期", "seg_purchase"),
    ("故障照片", "seg_fault"),
    ("包装破损", "seg_delivery"),
    ("订单编号", "seg_refund"),
    ("计费周期", "seg_subscription"),
    ("电子发票", "seg_expense"),
)


@dataclass(frozen=True, slots=True)
class _Metrics:
    recall_at_3: float
    mrr: float


def test_trigram_meets_chinese_threshold_and_unicode61_is_not_assumed(
    tmp_path: Path,
) -> None:
    connection = connect_database(tmp_path / "lifecase.sqlite3")
    apply_migrations(connection)
    try:
        _seed_dataset(connection)
        service = RetrievalService(connection)
        service.rebuild_index()

        trigram_rankings = {
            query: [
                result.segment_id
                for result in service.search(case_id="case_zh_eval", query=query, limit=3)
            ]
            for query, _expected in _QUERIES
        }
        unicode61_rankings = _unicode61_rankings(connection)

        trigram_metrics = _metrics(trigram_rankings)
        unicode61_metrics = _metrics(unicode61_rankings)

        assert trigram_metrics.recall_at_3 >= RECALL_AT_3_MINIMUM
        assert trigram_metrics.mrr >= MRR_MINIMUM
        assert trigram_metrics == _Metrics(recall_at_3=1.0, mrr=1.0)
        assert unicode61_metrics == _Metrics(recall_at_3=0.0, mrr=0.0)
    finally:
        connection.close()


def _seed_dataset(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        INSERT INTO case_record (
            id, type, template_version, title, goal, status,
            priority, version, created_at, updated_at
        ) VALUES ('case_zh_eval', 'warranty', 1, '中文合成检索评估',
                  '验证本地词法召回', 'intake', 0, 1, ?, ?)
        """,
        (_NOW, _NOW),
    )
    connection.execute(
        """
        INSERT INTO source (
            id, case_id, original_name, media_type, sha256,
            size_bytes, object_path, imported_at
        ) VALUES ('source_zh_eval', 'case_zh_eval', 'synthetic-zh.txt',
                  'text/plain', ?, 0, ?, ?)
        """,
        ("0" * 64, f"objects/00/00/{'0' * 64}", _NOW),
    )
    for sequence, (segment_id, text) in enumerate(_DOCUMENTS):
        connection.execute(
            """
            INSERT INTO source_segment (
                id, source_id, sequence, page, bbox_json, text,
                extraction_method, confidence, text_sha256, extractor_version
            ) VALUES (?, 'source_zh_eval', ?, NULL, NULL, ?,
                      'direct', 1.0, ?, 'synthetic.zh.v1')
            """,
            (segment_id, sequence, text, hashlib.sha256(text.encode()).hexdigest()),
        )


def _unicode61_rankings(connection: sqlite3.Connection) -> dict[str, list[str]]:
    connection.execute(
        """
        CREATE VIRTUAL TABLE temp.unicode61_baseline USING fts5(
            segment_id UNINDEXED,
            text,
            tokenize='unicode61'
        )
        """
    )
    connection.execute(
        """
        INSERT INTO unicode61_baseline(rowid, segment_id, text)
        SELECT rowid, id, text FROM source_segment ORDER BY sequence
        """
    )
    return {
        query: [
            str(row[0])
            for row in connection.execute(
                """
                SELECT segment_id FROM unicode61_baseline
                WHERE unicode61_baseline MATCH ?
                ORDER BY bm25(unicode61_baseline), segment_id
                LIMIT 3
                """,
                (query,),
            ).fetchall()
        ]
        for query, _expected in _QUERIES
    }


def _metrics(rankings: dict[str, list[str]]) -> _Metrics:
    recall_hits = 0
    reciprocal_rank_total = 0.0
    for query, expected_segment_id in _QUERIES:
        ranked = rankings[query]
        if expected_segment_id in ranked[:3]:
            recall_hits += 1
        if expected_segment_id in ranked:
            reciprocal_rank_total += 1.0 / (ranked.index(expected_segment_id) + 1)
    query_count = len(_QUERIES)
    return _Metrics(
        recall_at_3=recall_hits / query_count,
        mrr=reciprocal_rank_total / query_count,
    )
