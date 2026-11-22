"""知识库路由：文档管理、重建索引、检索预览。

权限：全部仅管理员可用。
    客户并不直接访问这些接口——他们提问时由聊天接口在内部调用检索服务；
    这里是"运营侧"的调试与管理入口。

检索预览（/api/kb/search-preview）是本项目调参的关键工具：
    它把 bm25 原始分、覆盖率、合成分、是否放行全部暴露出来，
    第 5 章的阈值 A/B 实验就是靠它批量跑出来的。
"""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException, status

from app.auth.deps import AdminOnly
from app.db.deps import get_db
from app.schemas.kb import (
    KbDocumentIn,
    KbDocumentOut,
    KbHitOut,
    KbReindexResponse,
    KbSearchRequest,
    KbSearchResponse,
)
from app.services.kb_indexer import index_all, index_document
from app.services.retriever import search

router = APIRouter(prefix="/api/kb", tags=["知识库"])


@router.get("/docs", response_model=list[KbDocumentOut], summary="知识库文档列表（仅管理员）")
def list_documents(admin: AdminOnly, conn: sqlite3.Connection = Depends(get_db)) -> list[KbDocumentOut]:
    rows = conn.execute(
        """
        SELECT d.id, d.title, d.category, d.version, d.updated_at,
               COUNT(c.id) AS chunk_count
        FROM kb_documents AS d
        LEFT JOIN kb_chunks AS c ON c.doc_id = d.id
        GROUP BY d.id
        ORDER BY d.id
        """
    ).fetchall()
    return [
        KbDocumentOut(
            id=row["id"],
            title=row["title"],
            category=row["category"],
            version=row["version"],
            updated_at=row["updated_at"],
            chunk_count=int(row["chunk_count"]),
        )
        for row in rows
    ]


@router.post(
    "/docs",
    response_model=KbDocumentOut,
    status_code=status.HTTP_201_CREATED,
    summary="新增/更新文档并重建索引（仅管理员）",
)
def upsert_document(
    payload: KbDocumentIn,
    admin: AdminOnly,
    conn: sqlite3.Connection = Depends(get_db),
) -> KbDocumentOut:
    result = index_document(
        conn, title=payload.title, category=payload.category, markdown=payload.markdown
    )
    row = conn.execute(
        "SELECT id, title, category, version, updated_at FROM kb_documents WHERE id = ?",
        (result["doc_id"],),
    ).fetchone()
    return KbDocumentOut(
        id=row["id"],
        title=row["title"],
        category=row["category"],
        version=row["version"],
        updated_at=row["updated_at"],
        chunk_count=result["chunks"],
    )


@router.post("/reindex", response_model=KbReindexResponse, summary="重建全部文档索引（仅管理员）")
def reindex(admin: AdminOnly, conn: sqlite3.Connection = Depends(get_db)) -> KbReindexResponse:
    res = index_all(conn)
    return KbReindexResponse(documents=int(res["documents"]), chunks=int(res["chunks"]))


@router.post("/search-preview", response_model=KbSearchResponse, summary="检索预览与拒答判定（仅管理员）")
def search_preview(
    payload: KbSearchRequest,
    admin: AdminOnly,
    conn: sqlite3.Connection = Depends(get_db),
) -> KbSearchResponse:
    result = search(conn, payload.query, top_k=payload.top_k, min_score=payload.min_score)
    return KbSearchResponse(
        query=result.query,
        tokens=result.tokens,
        threshold=result.threshold,
        top_score=result.top_score,
        accepted=result.accepted,
        hits=[
            KbHitOut(
                chunk_id=hit.chunk_id,
                doc_title=hit.doc_title,
                section_title=hit.section_title,
                content=hit.content,
                bm25_raw=round(hit.bm25_raw, 4),
                coverage=hit.coverage,
                score=hit.score,
                citation=hit.citation,
            )
            for hit in result.hits
        ],
    )
