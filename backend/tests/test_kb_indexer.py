"""知识库索引器测试：切片、分词、幂等重建。

为什么这些细节要测：
    切片粒度决定检索精度；分词决定中文能不能被搜到；
    幂等决定"改一篇文章后重建索引"会不会产生重复切片（重复切片会让
    同一段落占满 top_k，把其他有用证据挤掉）。
"""

from __future__ import annotations

from app.db.kb_docs import KB_DOCUMENTS
from app.services.kb_indexer import (
    _MAX_CHUNK_CHARS,
    chunk_document,
    index_all,
    index_document,
    segment,
    split_sections,
)


def test_segment_splits_chinese_into_words():
    tokens = segment("阿里云服务器8080端口未放行")
    assert "服务器" in tokens
    assert "8080" in tokens
    # 关键：不能把整句当一个词
    assert len(tokens) > 3


def test_segment_drops_pure_punctuation():
    tokens = segment("Nginx 502，怎么办？！")
    assert all(token.strip() for token in tokens)
    assert "，" not in tokens
    assert "！" not in tokens


def test_split_sections_by_markdown_heading():
    markdown = "## 现象\n访问超时\n\n## 排查步骤\n1. 检查安全组\n"
    sections = split_sections(markdown)
    assert [title for title, _ in sections] == ["现象", "排查步骤"]
    assert "检查安全组" in sections[1][1]


def test_split_sections_without_heading_returns_single_section():
    sections = split_sections("一段没有小标题的正文内容")
    assert len(sections) == 1
    assert sections[0][0] == "正文"


def test_chunk_document_respects_max_size():
    long_text = "## 长章节\n" + ("这是一句用于测试切片长度的说明。" * 60)
    chunks = chunk_document(long_text)
    assert len(chunks) > 1
    assert all(len(chunk["content"]) <= _MAX_CHUNK_CHARS for chunk in chunks)


def test_index_document_creates_doc_and_chunks(conn):
    result = index_document(
        conn,
        title="测试文档",
        category="测试",
        markdown="## 第一节\n内容一\n\n## 第二节\n内容二\n",
    )
    assert result["chunks"] == 2

    docs = conn.execute("SELECT * FROM kb_documents WHERE id = ?", (result["doc_id"],)).fetchone()
    assert docs["title"] == "测试文档" and docs["version"] == 1

    fts_rows = conn.execute("SELECT COUNT(*) AS n FROM kb_chunks_fts").fetchone()["n"]
    assert fts_rows == 2


def test_reindex_is_idempotent_and_bumps_version(conn):
    """重复索引同一篇文档：切片不重复、版本号 +1、旧 FTS 记录被清掉。"""
    markdown = "## 现象\n旧内容\n"
    first = index_document(conn, title="重复索引文档", category="测试", markdown=markdown)
    assert conn.execute("SELECT COUNT(*) AS n FROM kb_chunks").fetchone()["n"] == 1

    second = index_document(conn, title="重复索引文档", category="测试", markdown=markdown)
    assert second["doc_id"] == first["doc_id"]
    assert conn.execute("SELECT COUNT(*) AS n FROM kb_chunks").fetchone()["n"] == 1   # 没有重复
    assert conn.execute("SELECT COUNT(*) AS n FROM kb_chunks_fts").fetchone()["n"] == 1

    version = conn.execute(
        "SELECT version FROM kb_documents WHERE id = ?", (first["doc_id"],)
    ).fetchone()["version"]
    assert version == 2

    # 更新正文后，旧内容的索引记录必须消失
    index_document(conn, title="重复索引文档", category="测试", markdown="## 现象\n新内容\n")
    old_hits = conn.execute(
        "SELECT COUNT(*) AS n FROM kb_chunks_fts WHERE kb_chunks_fts MATCH ?", ('"旧内容"',)
    ).fetchone()["n"]
    assert old_hits == 0


def test_index_all_imports_bundled_documents(conn):
    result = index_all(conn)
    assert result["documents"] == len(KB_DOCUMENTS) == 15
    assert result["chunks"] >= 15
    assert conn.execute("SELECT COUNT(*) AS n FROM kb_chunks_fts").fetchone()["n"] == result["chunks"]
