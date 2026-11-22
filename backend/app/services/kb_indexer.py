"""知识库索引器：Markdown 切片 -> jieba 分词 -> 写入 kb_chunks / FTS5。

【为什么需要"切片"】
    整篇文档作为一个检索单位太粗：一篇 1500 字的文档里可能只有一节和用户问题相关，
    如果把整篇都塞进大模型上下文，既浪费 Token 又稀释证据。
    切成"章节级"小块后，检索能精确命中"某篇文档的某一节"，引用可以精确到 [文档名 · 章节名]。

【为什么入库前要 jieba 分词】
    SQLite 的 FTS5 用 unicode61 分词器，它按空格切词；
    "数据库连接超时"对它是**一个词**，用户搜"连接超时"匹配不上。
    所以入库时先用 jieba 切成"数据库 连接 超时"存进 segmented_text，
    检索时对查询做同样处理，两边都是空格分隔的词，才能对上。
    —— 中文分词必须在"写入"和"查询"两侧用同一套规则，这是关键。

【幂等性】
    同一篇文档重复索引时：先更新 kb_documents，再删掉它的旧切片重新插入。
    删除 kb_chunks 会通过触发器自动清掉 FTS 里的对应行（见 database.py 的 kb_chunks_ad）。
"""

from __future__ import annotations

import logging
import re
import sqlite3
from datetime import datetime, timezone

import jieba

from app.db.kb_docs import KB_DOCUMENTS

# jieba 首次加载词典约 0.5 秒，日志很吵，压到 WARNING
jieba.setLogLevel(logging.WARNING)

# 只保留中文、字母、数字组成的词，过滤掉纯标点与空白
_TOKEN_RE = re.compile(r"^[\u4e00-\u9fa5a-zA-Z0-9]+$")

# Markdown 二级标题（## 标题）
_SECTION_RE = re.compile(r"^##\s+(.+)$", re.MULTILINE)

# 单块最大字符数：超过就按句子再切，避免一个切片过大稀释相关性
_MAX_CHUNK_CHARS = 420


def segment(text: str) -> list[str]:
    """jieba 分词 + 过滤纯标点/空白。"""
    return [token for token in jieba.lcut(text) if _TOKEN_RE.match(token)]


def segment_for_index(text: str) -> str:
    """把文本变成 FTS5 能用的空格分隔词串（索引侧）。

    为什么索引侧用 cut_for_search 而不是 lcut：
        jieba 会把"磁盘空间"当成一个整词，若只存整词，用户查"磁盘"（更短的词）
        在 FTS5 里就匹配不上（FTS5 按整词匹配）。
        cut_for_search 会额外切出子词，于是"磁盘空间"同时存入"磁盘 / 空间 / 磁盘空间"，
        查询侧用短词也能命中。这是 jieba 官方推荐的"搜索模式"用法。
        代价是索引体积略大，但切片只有 58 个，完全可以接受。
    """
    return " ".join(
        token for token in jieba.cut_for_search(text) if _TOKEN_RE.match(token)
    )


# ---------------------------------------------------------------------------
# 切片
# ---------------------------------------------------------------------------
def split_sections(markdown: str) -> list[tuple[str, str]]:
    """按 ## 标题把 Markdown 切成 (章节名, 正文) 列表。"""
    matches = list(_SECTION_RE.finditer(markdown))
    if not matches:
        body = markdown.strip()
        return [("正文", body)] if body else []

    sections: list[tuple[str, str]] = []

    # 第一个标题之前如果有导语，单独成节
    if matches[0].start() > 0:
        intro = markdown[: matches[0].start()].strip()
        if intro:
            sections.append(("导语", intro))

    for index, match in enumerate(matches):
        title = match.group(1).strip()
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(markdown)
        body = markdown[start:end].strip()
        if body:
            sections.append((title, body))
    return sections


def _split_long_text(text: str, max_chars: int = _MAX_CHUNK_CHARS) -> list[str]:
    """超长章节按句末标点再切，保证单块不超过 max_chars。"""
    if len(text) <= max_chars:
        return [text]

    pieces: list[str] = []
    buffer = ""
    for sentence in re.split(r"(?<=[。；！？\n])", text):
        if not sentence:
            continue
        if buffer and len(buffer) + len(sentence) > max_chars:
            pieces.append(buffer.strip())
            buffer = sentence
        else:
            buffer += sentence
    if buffer.strip():
        pieces.append(buffer.strip())
    return pieces or [text]


def chunk_document(markdown: str) -> list[dict[str, str]]:
    """把一篇文档切成若干块：返回 [{section_title, content}, ...]。"""
    chunks: list[dict[str, str]] = []
    for section_title, body in split_sections(markdown):
        for piece in _split_long_text(body):
            chunks.append({"section_title": section_title, "content": piece})
    return chunks


# ---------------------------------------------------------------------------
# 写入
# ---------------------------------------------------------------------------
def _now_text() -> str:
    """UTC 时间，与数据库 datetime('now') 基准一致。"""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def index_document(
    conn: sqlite3.Connection,
    *,
    title: str,
    category: str | None,
    markdown: str,
) -> dict[str, int]:
    """索引（或重建）一篇文档，返回 {"doc_id": id, "chunks": 切片数}。"""
    now = _now_text()
    existing = conn.execute(
        "SELECT id FROM kb_documents WHERE title = ?", (title,)
    ).fetchone()

    if existing is None:
        cur = conn.execute(
            "INSERT INTO kb_documents (title, category, raw_markdown, version, updated_at) "
            "VALUES (?, ?, ?, 1, ?)",
            (title, category, markdown, now),
        )
        doc_id = int(cur.lastrowid)
    else:
        doc_id = int(existing["id"])
        conn.execute(
            "UPDATE kb_documents SET category = ?, raw_markdown = ?, "
            "version = version + 1, updated_at = ? WHERE id = ?",
            (category, markdown, now, doc_id),
        )
        # 重建切片：先清空旧切片（触发器会同步清 FTS），再重新插入
        conn.execute("DELETE FROM kb_chunks WHERE doc_id = ?", (doc_id,))

    chunks = chunk_document(markdown)
    for chunk in chunks:
        conn.execute(
            "INSERT INTO kb_chunks (doc_id, doc_title, section_title, content, segmented_text) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                doc_id,
                title,
                chunk["section_title"],
                chunk["content"],
                # 重要：把"标题 + 章节名 + 正文"一起分词后存入 segmented_text。
                # 原因：FTS5 的 unicode61 分词器会把连续中文当成一个整词，
                # 而 doc_title / section_title 列是原样写入的（触发器不会分词），
                # 于是"磁盘空间不足排查与清理"在 FTS 里是 1 个 token，
                # 用户搜"磁盘"永远匹配不到。把标题也分词并入索引列即可解决，
                # 同时标题词还能提升该切片的 BM25 相关性。
                segment_for_index(
                    f"{title} {chunk['section_title'] or ''} {chunk['content']}"
                ),
            ),
        )

    conn.commit()
    return {"doc_id": doc_id, "chunks": len(chunks)}


def index_all(
    conn: sqlite3.Connection, documents: list[dict] | None = None
) -> dict[str, int]:
    """索引全部知识库文档（幂等），返回 {"documents": n, "chunks": m}。"""
    docs = documents if documents is not None else KB_DOCUMENTS
    total_chunks = 0
    for doc in docs:
        result = index_document(
            conn,
            title=doc["title"],
            category=doc.get("category"),
            markdown=doc["markdown"],
        )
        total_chunks += result["chunks"]
    return {"documents": len(docs), "chunks": total_chunks}
