"""知识库相关请求/响应模型。"""

from __future__ import annotations

from pydantic import BaseModel, Field


class KbDocumentIn(BaseModel):
    """新增或更新一篇知识库文档（保存后自动重建切片索引）。"""

    title: str = Field(min_length=1, max_length=120, description="文档标题，全局唯一")
    category: str | None = Field(default=None, max_length=32, description="分类，如 网络安全/应用服务")
    markdown: str = Field(min_length=1, description="Markdown 正文，用 ## 分节以获得更精细的切片")


class KbDocumentOut(BaseModel):
    """文档列表项。"""

    id: int
    title: str
    category: str | None
    version: int
    updated_at: str
    chunk_count: int


class KbSearchRequest(BaseModel):
    """检索预览请求。"""

    query: str = Field(min_length=1, max_length=200, description="用户问题原文")
    top_k: int | None = Field(default=None, ge=1, le=10, description="返回条数，留空用配置值")
    min_score: float | None = Field(default=None, ge=0.0, le=1.0, description="拒答阈值，留空用配置值")


class KbHitOut(BaseModel):
    """一条检索命中（含打分构成，便于调参与解释）。"""

    chunk_id: int
    doc_title: str
    section_title: str | None
    content: str
    bm25_raw: float
    coverage: float
    score: float
    citation: str


class KbReindexResponse(BaseModel):
    """知识库全量重建索引响应。"""

    documents: int
    chunks: int


class KbSearchResponse(BaseModel):
    """检索结果：命中列表 + 是否放行给大模型作答。"""

    query: str
    tokens: list[str]
    threshold: float
    top_score: float
    accepted: bool
    hits: list[KbHitOut] = Field(default_factory=list)
