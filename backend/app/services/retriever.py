"""中文检索与拒答判定：jieba + SQLite FTS5 BM25 + 置信度归一化。

【检索流程】
    用户问题
      -> jieba 分词并去掉无意义虚词（的/怎么/如何…）
      -> 拼成 FTS5 MATCH 表达式（词之间用 OR，换取召回率）
      -> FTS5 用 BM25 排序取出候选切片
      -> 按"词法相关性 + 查询词覆盖率 + 标题命中"合成 0~1 的置信度
      -> 与 RAG_MIN_SCORE 比较：达标才允许交给大模型作答，否则走"拒答"分支

【为什么要归一化到 0~1】
    BM25 的原始分数是无上界的（随文档长度、词频变化），没法直接设阈值。
    这里用 saturation（饱和曲线）把它压到 0~1：raw/(raw+K)，
    raw 越大越接近 1，且 K 可以标定成"多小的分数算弱相关"。

【为什么还要看"覆盖率"】
    单看 BM25 会出现这种情况：问"企业内网 Kubernetes GPU 调度器怎么配"，
    文档里恰好多次出现"企业内网"就得分虚高。覆盖率 = 查询词命中数/总词数，
    能有效把"只蹭到一两个词"的情况压下去——这是拒答准确率的关键。

【为什么加标题命中】
    问了"Nginx 502"，标题里就写着 Nginx 502 的文档显然更该排前面。
    权重给得很小（0.05），只做细微修正。

【阈值说明】
    RAG_MIN_SCORE 默认 0.35，取值经过 docs/MEASUREMENTS_LOG.md 里的标定实验。
    它属于"业务可调参数"，第 5 章会做 A/B 对比（不同阈值下命中率与拒答率的变化）。
"""

from __future__ import annotations

import math
import re
import sqlite3
from dataclasses import dataclass, field

from app.config import get_settings
from app.services.kb_indexer import segment

# ---------------------------------------------------------------------------
# 打分参数（标定后固定；调整需重跑标定实验）
# ---------------------------------------------------------------------------
BM25_SATURATION_K = 6.0   # 饱和曲线参数：raw/(raw+K)，越大越"慢饱和"
W_LEXICAL = 0.45          # 词法相关性权重
W_COVERAGE = 0.50         # 查询词加权覆盖率权重（IDF 加权）
W_TITLE = 0.05            # 标题命中权重
MAX_CANDIDATES = 20       # 先多取一些候选，再按合成分数排序取 top_k

# 疑问虚词：它们几乎出现在任何中文问题里，参与打分只会制造噪声
_STOPWORDS = {
    "的", "了", "是", "在", "和", "与", "及", "或", "我", "你", "他", "她", "它",
    "我们", "你们", "他们", "这", "那", "这个", "那个", "哪些", "什么", "怎么",
    "如何", "为什么", "怎么样", "吗", "呢", "啊", "吧", "呀", "请问", "一下",
    "一个", "可以", "需要", "应该", "是否", "有没有", "帮我", "请问", "然后",
    "现在", "出现", "发生", "问题", "情况", "办法", "处理", "解决",
    "不能", "不要", "要不要", "能不能", "是不是", "会不会", "怎么办", "一直", "多久", "多少",
}

# 技术标识符（含字母或数字，如 ssh / swap / 8080 / 8C16G / OceanBase）：
# 如果某个标识符在整库里出现次数为 0，说明知识库根本没有这个主题，
# 无论别的词蹭得多像，都必须压到阈值以下 —— 这是拒答准确率的关键硬约束。
_TECH_TOKEN_RE = re.compile(r"[A-Za-z0-9]")
UNSUPPORTED_TOPIC_CAP = 0.30


@dataclass
class RetrievalHit:
    """一条检索命中。"""

    chunk_id: int
    doc_id: int
    doc_title: str
    section_title: str | None
    content: str
    bm25_raw: float
    coverage: float
    score: float

    @property
    def citation(self) -> str:
        """展示用引用标签，例如 [Nginx 返回 502 Bad Gateway 排查 · 现象]。"""
        return f"[{self.doc_title} · {self.section_title or '正文'}]"


@dataclass
class RetrievalResult:
    """一次检索的完整结果（含是否放行给大模型作答）。"""

    query: str
    tokens: list[str]
    hits: list[RetrievalHit] = field(default_factory=list)
    top_score: float = 0.0
    threshold: float = 0.0
    accepted: bool = False

    @property
    def citations(self) -> list[str]:
        return [hit.citation for hit in self.hits]


# ---------------------------------------------------------------------------
# 内部工具
# ---------------------------------------------------------------------------
def _meaningful_tokens(query: str) -> list[str]:
    """分词后去掉虚词与单字噪声，得到用于检索的实词。"""
    tokens: list[str] = []
    for token in segment(query):
        if token in _STOPWORDS:
            continue
        if len(token) == 1 and not token.isdigit() and not token.isascii():
            # 单个汉字信息量太低（如"的""了"已过滤，这里挡住剩余的）
            continue
        tokens.append(token)
    return tokens


def _build_match_expression(tokens: list[str]) -> str:
    """拼 FTS5 MATCH 表达式：每个词加双引号防语法错误，词间 OR 提高召回。"""
    return " OR ".join('"' + token.replace('"', '""') + '"' for token in tokens)


def _saturate(raw: float, k: float = BM25_SATURATION_K) -> float:
    """把无上界的 BM25 原始分压到 0~1。"""
    if raw <= 0:
        return 0.0
    return raw / (raw + k)


def _document_frequencies(conn: sqlite3.Connection, tokens: list[str]) -> dict[str, int]:
    """统计每个查询词出现在多少个切片里（用于算 IDF）。"""
    freqs: dict[str, int] = {}
    for token in tokens:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM kb_chunks_fts WHERE kb_chunks_fts MATCH ?",
            ('"' + token.replace('"', '""') + '"',),
        ).fetchone()
        freqs[token] = int(row["n"])
    return freqs


def _token_idf(document_frequency: int, total_chunks: int) -> float:
    """词的区分度：越少见越重要，df=0（库里根本没有）权重最高。

    这行是整个拒答机制的关键：
    问"能不能免费升级到 8C16G"时，"免费/8C16G"在库里一次都没出现（df=0），
    IDF 很高却没被命中 -> 加权覆盖率被拉低 -> 分数掉到阈值以下 -> 正确拒答。
    如果只用"命中词数/总词数"，这些词被忽略掉，"云服务器"一个常见词就能把分抬高。
    """
    return math.log(1.0 + total_chunks / (1.0 + document_frequency))


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------
def search(
    conn: sqlite3.Connection,
    query: str,
    *,
    top_k: int | None = None,
    min_score: float | None = None,
) -> RetrievalResult:
    """检索知识库并判定是否达到作答阈值。

    参数留空时从配置读取（RAG_TOP_K / RAG_MIN_SCORE）。
    """
    settings = get_settings()
    top_k = settings.rag_top_k if top_k is None else top_k
    threshold = settings.rag_min_score if min_score is None else min_score

    tokens = _meaningful_tokens(query)
    result = RetrievalResult(query=query, tokens=tokens, threshold=threshold)
    if not tokens:
        return result

    rows = conn.execute(
        """
        SELECT c.id            AS chunk_id,
               c.doc_id        AS doc_id,
               c.doc_title     AS doc_title,
               c.section_title AS section_title,
               c.content       AS content,
               c.segmented_text AS segmented_text,
               bm25(kb_chunks_fts) AS rank
        FROM kb_chunks_fts AS f
        JOIN kb_chunks AS c ON c.id = f.chunk_id
        WHERE kb_chunks_fts MATCH ?
        ORDER BY rank
        LIMIT ?
        """,
        (_build_match_expression(tokens), MAX_CANDIDATES),
    ).fetchall()

    if not rows:
        return result

    total_tokens = len(tokens)
    total_chunks = max(1, int(conn.execute("SELECT COUNT(*) AS n FROM kb_chunks").fetchone()["n"]))
    freqs = _document_frequencies(conn, tokens)
    idf = {token: _token_idf(freqs[token], total_chunks) for token in tokens}
    total_idf = sum(idf.values()) or 1.0
    # 查询里出现、但全库从未出现过的技术标识符
    missing_tech_tokens = [t for t in tokens if _TECH_TOKEN_RE.search(t) and freqs[t] == 0]

    scored: list[RetrievalHit] = []
    for row in rows:
        # bm25() 返回负数：越小（越负）= 越相关
        bm25_raw = max(0.0, -float(row["rank"]))
        haystack = f"{row['doc_title']} {row['section_title'] or ''} {row['segmented_text']}"
        # 加权覆盖率：命中词的 IDF 之和 / 全部查询词的 IDF 之和
        matched_idf = sum(idf[token] for token in tokens if token in haystack)
        coverage = matched_idf / total_idf
        title_hits = sum(1 for token in tokens if token in row["doc_title"])
        title_ratio = title_hits / total_tokens

        score = (
            W_LEXICAL * _saturate(bm25_raw)
            + W_COVERAGE * coverage
            + W_TITLE * title_ratio
        )

        # 硬约束：查询要求的技术标识符全库不存在 -> 判定为"知识库未覆盖"，分数封顶
        if missing_tech_tokens and any(token not in haystack for token in missing_tech_tokens):
            score = min(score, UNSUPPORTED_TOPIC_CAP)
        scored.append(
            RetrievalHit(
                chunk_id=int(row["chunk_id"]),
                doc_id=int(row["doc_id"]),
                doc_title=str(row["doc_title"]),
                section_title=row["section_title"],
                content=str(row["content"]),
                bm25_raw=bm25_raw,
                coverage=round(coverage, 4),
                score=round(min(1.0, score), 4),
            )
        )

    scored.sort(key=lambda hit: hit.score, reverse=True)
    result.hits = scored[:top_k]
    result.top_score = result.hits[0].score if result.hits else 0.0
    result.accepted = result.top_score >= threshold
    return result
