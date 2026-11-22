"""检索与拒答测试：命中、排序、置信度归一化、拒答判定。

这是 Day 3 的核心验收：既要"该答的答出来"，也要"不该答的别乱答"。
测试里的阈值全部用配置默认值（RAG_MIN_SCORE），与生产口径一致。
"""

from __future__ import annotations

import pytest

from app.services.kb_indexer import index_all
from app.services.retriever import UNSUPPORTED_TOPIC_CAP, search


@pytest.fixture()
def kb(conn):
    """把 15 篇内置文档索引进测试库。"""
    index_all(conn)
    return conn


# ===========================================================================
# 一、边界与健壮性
# ===========================================================================
def test_punctuation_only_query_returns_empty(kb):
    result = search(kb, "？！。，")
    assert result.tokens == []
    assert result.hits == []
    assert result.accepted is False


def test_stopword_only_query_returns_empty(kb):
    result = search(kb, "怎么办 为什么 如何 的 了")
    assert result.tokens == []
    assert result.accepted is False


def test_scores_are_normalized_and_sorted(kb):
    result = search(kb, "端口被占用怎么找进程")
    assert result.hits
    for hit in result.hits:
        assert 0.0 <= hit.score <= 1.0
        assert 0.0 <= hit.coverage <= 1.0
    scores = [hit.score for hit in result.hits]
    assert scores == sorted(scores, reverse=True)


# ===========================================================================
# 二、该答的要答对
# ===========================================================================
@pytest.mark.parametrize(
    ("query", "expected_keyword"),
    [
        ("Nginx 502 怎么排查", "502"),
        ("磁盘空间满了怎么清理", "磁盘"),
        ("ssh 连不上 一直超时", "SSH"),
        ("云服务器备份和快照怎么做", "备份"),
        ("https 证书过期了怎么处理", "证书"),
        ("8080 端口没放行 外部访问不了", "安全组"),
    ],
)
def test_answerable_questions_are_accepted(kb, query, expected_keyword):
    result = search(kb, query)
    assert result.accepted is True, f"应放行却拒答: {query} (得分 {result.top_score})"
    assert result.top_score >= result.threshold
    assert expected_keyword in result.hits[0].doc_title


def test_title_match_ranks_first(kb):
    result = search(kb, "Nginx 502 Bad Gateway")
    assert "Nginx" in result.hits[0].doc_title


def test_citation_is_human_readable(kb):
    result = search(kb, "磁盘满了")
    citation = result.hits[0].citation
    assert citation.startswith("[") and citation.endswith("]")
    assert " · " in citation          # [文档名 · 章节名]


def test_top_k_limits_returned_hits(kb):
    assert len(search(kb, "服务器 端口 内存 磁盘", top_k=2).hits) <= 2


# ===========================================================================
# 三、不该答的要拒掉
# ===========================================================================
@pytest.mark.parametrize(
    "query",
    [
        "帮我申请一张阿里云代金券",
        "退款流程是什么 多久到账",
        "如何申请软件著作权",
        "公司内网如何配置 VPN 网关",
    ],
)
def test_unanswerable_questions_are_refused(kb, query):
    result = search(kb, query)
    assert result.accepted is False, f"应拒答却放行: {query} (得分 {result.top_score})"
    assert result.top_score < result.threshold


@pytest.mark.parametrize(
    "query",
    [
        "如何把数据库迁移到 OceanBase",
        "云服务器能不能免费升级到 8C16G",
        "帮我写一个 Python 爬虫脚本",
    ],
)
def test_unsupported_technical_topics_are_capped(kb, query):
    """查询里的技术标识符全库不存在时，分数必须被硬压到 0.30 以下。"""
    result = search(kb, query)
    assert result.top_score <= UNSUPPORTED_TOPIC_CAP + 1e-6
    assert result.accepted is False


# ===========================================================================
# 四、阈值可调（第 5 章 A/B 实验的基础）
# ===========================================================================
def test_threshold_override_can_force_refusal(kb):
    result = search(kb, "Nginx 502 怎么排查", min_score=0.99)
    assert result.accepted is False
    assert result.threshold == 0.99


def test_threshold_override_can_force_acceptance(kb):
    result = search(kb, "云服务器能不能免费升级到 8C16G", min_score=0.1)
    assert result.accepted is True          # 阈值放低就会放行（说明是阈值在起作用）
