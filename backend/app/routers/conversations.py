"""会话与 SSE 流式问答路由。

核心职责：
    1. 会话 CRUD（创建、列表、详情、历史消息、附件回看）与多租户隔离（跨客户返回 404）；
    2. POST /api/conversations/{id}/chat/stream：
       - 同时支持 multipart/form-data（message + 可选报错截图 image）与 application/json（{"message": "..."}），
         并通过 openapi_extra 完整暴露给 Swagger / OpenAPI 契约；
       - 请求体按流分块硬限流（JSON ≤64KB、multipart ≤3MB 且图片 ≤2MB），即使 chunked 传输或伪造
         Content-Length 也无法绕过内存保护；
       - OCR 视觉识别摘要使用 <untrusted_screenshot_ocr> 不可信数据标签隔离，配合系统提示词防注入；
       - 通过 LangGraph astream 实时推送 status / citations / token / interrupt / done 事件；
       - 挂起即时落库 pending_human，并在客户端中断（CancelledError）时回查检查点做状态补偿。
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, status
from fastapi.responses import FileResponse, StreamingResponse
from langgraph.checkpoint.sqlite import SqliteSaver
from starlette.datastructures import UploadFile as StarletteUploadFile

from app.agent.graph import build_graph, make_config, open_checkpointer
from app.auth.deps import CurrentUser
from app.config import Settings, get_settings
from app.db.database import connect
from app.db.deps import get_db
from app.mcp.auth_context import create_auth_context
from app.schemas.conversation import (
    CitationDetailOut,
    ConversationCreateOut,
    ConversationCreateRequest,
    ConversationOut,
    MessageOut,
)
from app.services.image_intake import (
    MAX_IMAGE_BYTES,
    ImageTooLargeError,
    InvalidImageError,
    describe_image_safe,
    validate_and_save_image,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/conversations", tags=["会话与问答"])

_SENTENCE_SPLIT = re.compile(r"(?<=[。！？.!?\n])")
_SAFE_ATTACHMENT_NAME = re.compile(r"^[0-9a-f]{32}\.(png|jpg|webp)$")
_OCR_CONTROL_TAGS = re.compile(r"</?untrusted_screenshot_ocr>", re.IGNORECASE)
_MAX_MESSAGE_CHARS = 4000  # 4000 字符（与 AgentReplyRequest 及 OpenAPI maxLength 严格一致）
_MAX_JSON_BODY_BYTES = 64 * 1024  # 64 KB
_MAX_MULTIPART_BODY_BYTES = MAX_IMAGE_BYTES + 1024 * 1024  # 2MB 图片 + 1MB 表单/头余量

_CHAT_STREAM_OPENAPI_EXTRA: dict[str, Any] = {
    "requestBody": {
        "required": True,
        "content": {
            "multipart/form-data": {
                "schema": {
                    "type": "object",
                    "required": ["message"],
                    "properties": {
                        "message": {
                            "type": "string",
                            "maxLength": _MAX_MESSAGE_CHARS,
                            "description": f"客户提问内容（最多 {_MAX_MESSAGE_CHARS} 字符）",
                        },
                        "image": {
                            "type": "string",
                            "format": "binary",
                            "description": "可选报错截图（PNG/JPEG/WEBP，≤2 MB；整个表单 ≤3 MB）",
                        },
                    },
                }
            },
            "application/json": {
                "schema": {
                    "type": "object",
                    "required": ["message"],
                    "properties": {
                        "message": {
                            "type": "string",
                            "maxLength": _MAX_MESSAGE_CHARS,
                            "description": f"客户提问内容（最多 {_MAX_MESSAGE_CHARS} 字符；JSON 整体 ≤64 KB）",
                        },
                    },
                }
            },
        },
    }
}


def _now_text() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _sanitize_ocr_text(raw: str) -> str:
    """对视觉 OCR 提取文本做长度截断与标签清洗，作为不可信数据隔离传入。"""
    cleaned = _OCR_CONTROL_TAGS.sub("", raw or "").strip()
    if len(cleaned) > 1200:
        cleaned = cleaned[:1200] + "..."
    return cleaned


async def _enforce_bounded_request_body(
    request: Request,
    *,
    max_bytes: int,
    limit_detail: str,
) -> None:
    """在 request.form()/request.json() 之前对原始字节流做硬上限校验。

    先查 Content-Length，再逐块消费 request.stream() 并回填 request._body，
    彻底堵住 chunked 传输或伪造较小 Content-Length 绕过上传限流的漏洞。
    """
    content_length_raw = request.headers.get("content-length")
    if content_length_raw and content_length_raw.isdigit():
        if int(content_length_raw) > max_bytes:
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail=limit_detail,
            )

    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        if not chunk:
            continue
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail=limit_detail,
            )
        chunks.append(chunk)
    # 缓存到 Starlette Request._body，供后续 request.json() / request.form() 零开销复用
    request._body = b"".join(chunks)


async def _read_upload_bounded(upload: StarletteUploadFile, max_bytes: int) -> bytes:
    """分块读取上传文件，超出 max_bytes 立即中止并抛 ImageTooLargeError。"""
    chunks: list[bytes] = []
    total = 0
    chunk_size = 64 * 1024
    while True:
        piece = await upload.read(chunk_size)
        if not piece:
            break
        total += len(piece)
        if total > max_bytes:
            raise ImageTooLargeError(
                f"图片大小超过上限（最大允许 {max_bytes // (1024 * 1024)} MB）"
            )
        chunks.append(piece)
    return b"".join(chunks)


def parse_handoff_blob(raw_json: str | None) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """解析 conversations.ai_handoff_summary_json，拆出 (handoff_summary, draft_ticket)。"""
    if not raw_json:
        return None, None
    try:
        parsed = json.loads(raw_json)
    except json.JSONDecodeError:
        return None, None
    if not isinstance(parsed, dict):
        return None, None
    if "handoff_summary" in parsed and isinstance(parsed.get("handoff_summary"), dict):
        return parsed["handoff_summary"], parsed.get("draft_ticket")
    draft = parsed.get("draft_ticket")
    summary = {k: v for k, v in parsed.items() if k != "draft_ticket"}
    return summary, draft


def _split_segments(text: str) -> list[str]:
    """把完整回答按句子/换行切分为若干可读片段，用于 SSE token 推送。"""
    if not text:
        return []
    raw_parts = [p for p in _SENTENCE_SPLIT.split(text) if p]
    segments: list[str] = []
    for part in raw_parts:
        while len(part) > 40:
            segments.append(part[:40])
            part = part[40:]
        if part:
            segments.append(part)
    return segments


def _load_accessible_conversation(
    conn: sqlite3.Connection,
    conversation_id: str,
    user: dict,
) -> sqlite3.Row:
    """读取会话并执行租户隔离：不存在或跨客户访问统一返回 404。"""
    row = conn.execute(
        """
        SELECT c.*, u.display_name AS customer_name
        FROM conversations AS c
        JOIN users AS u ON u.id = c.customer_id
        WHERE c.id = ?
        """,
        (conversation_id,),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="会话不存在")
    if user["role"] == "customer" and row["customer_id"] != user["id"]:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="会话不存在")
    return row


def _resolve_citation_details(conn: sqlite3.Connection, citations: list[str]) -> list[CitationDetailOut]:
    """根据 `[文档名 · 章节]` 标签回查切片原文，便于前端引用徽章点击展开。"""
    details: list[CitationDetailOut] = []
    for tag in citations:
        inner = tag.strip()
        if inner.startswith("[") and inner.endswith("]"):
            inner = inner[1:-1]
        if " · " in inner:
            doc_title, section_title = inner.split(" · ", 1)
            row = conn.execute(
                "SELECT doc_title, section_title, content FROM kb_chunks WHERE doc_title = ? AND section_title = ? LIMIT 1",
                (doc_title.strip(), section_title.strip()),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT doc_title, section_title, content FROM kb_chunks WHERE doc_title = ? LIMIT 1",
                (inner.strip(),),
            ).fetchone()
        if row is not None:
            details.append(
                CitationDetailOut(
                    citation=tag,
                    doc_title=row["doc_title"],
                    section_title=row["section_title"],
                    content=row["content"],
                )
            )
    return details


def _conv_out(row: sqlite3.Row, last_message: str | None = None) -> ConversationOut:
    summary, draft = parse_handoff_blob(row["ai_handoff_summary_json"])
    keys = row.keys()
    return ConversationOut(
        id=row["id"],
        customer_id=row["customer_id"],
        customer_name=row["customer_name"],
        assigned_agent_id=row["assigned_agent_id"],
        thread_id=row["thread_id"],
        title=row["title"] if "title" in keys else None,
        status=row["status"],
        handoff_summary=summary,
        draft_ticket=draft,
        last_message=last_message,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _persist_pending_human(
    db_path: Path,
    conversation_id: str,
    handoff_summary: dict[str, Any],
    draft_ticket: dict[str, Any] | None,
) -> bool:
    """将挂起的交接摘要与草稿工单持久化并切换会话为 pending_human，返回是否成功落库。"""
    handoff_store = dict(handoff_summary)
    if draft_ticket is not None:
        handoff_store["draft_ticket"] = draft_ticket
    finish_now = _now_text()
    db = connect(db_path)
    try:
        cur = db.execute(
            """
            UPDATE conversations
            SET status = 'pending_human',
                ai_handoff_summary_json = ?,
                updated_at = ?
            WHERE id = ? AND status IN ('ai_handling', 'pending_human')
            """,
            (json.dumps(handoff_store, ensure_ascii=False), finish_now, conversation_id),
        )
        db.commit()
        if cur.rowcount == 0:
            row = db.execute(
                "SELECT status FROM conversations WHERE id = ?", (conversation_id,)
            ).fetchone()
            logger.warning(
                "_persist_pending_human 未更新行 conversation_id=%s current_status=%s",
                conversation_id,
                row["status"] if row else None,
            )
            return False
        return True
    finally:
        db.close()


def _compensate_pending_human_sync(
    app_db: Path,
    checkpoint_db: Path,
    conversation_id: str,
    cfg: dict[str, Any] | None,
    interrupt_payload: dict[str, Any] | None,
    merged: dict[str, Any],
) -> bool:
    """纯同步取消补偿（零 await，免疫 ASGI/anyio 取消作用域二次中断）。

    优先使用已捕获的 interrupt_payload；若尚未收到 chunk 即断连，
    通过同步 SqliteSaver 直接读取 checkpoints.db 检查是否已落入 hitl 挂起态并补写 pending_human。
    """
    if interrupt_payload is not None:
        return _persist_pending_human(
            app_db,
            conversation_id,
            interrupt_payload.get("handoff_summary")
            or merged.get("handoff_summary")
            or {},
            interrupt_payload.get("draft_ticket")
            if "draft_ticket" in interrupt_payload
            else merged.get("draft_ticket"),
        )
    if cfg is None or not checkpoint_db.exists():
        return False
    try:
        # 使用短超时(1s)连接，避免写锁竞争长时间阻塞事件循环
        raw_conn = sqlite3.connect(str(checkpoint_db), timeout=1.0, check_same_thread=False)
        try:
            raw_conn.execute("PRAGMA busy_timeout = 1000")
            sync_saver = SqliteSaver(raw_conn)
            sync_graph = build_graph(sync_saver)
            snap = sync_graph.get_state(cfg)
            if snap is not None and "hitl" in set(snap.next or ()):
                vals = snap.values if isinstance(snap.values, dict) else {}
                return _persist_pending_human(
                    app_db,
                    conversation_id,
                    vals.get("handoff_summary") or merged.get("handoff_summary") or {},
                    vals.get("draft_ticket") or merged.get("draft_ticket"),
                )
        finally:
            raw_conn.close()
    except Exception:
        logger.exception("同步取消补偿检查点失败 conversation_id=%s", conversation_id)
    return False


# ---------------------------------------------------------------------------
# 会话管理接口
# ---------------------------------------------------------------------------
@router.post(
    "",
    response_model=ConversationCreateOut,
    status_code=status.HTTP_201_CREATED,
    summary="创建新会话",
)
def create_conversation(
    user: CurrentUser,
    payload: ConversationCreateRequest | None = Body(default=None),
    conn: sqlite3.Connection = Depends(get_db),
) -> ConversationCreateOut:
    if user["role"] == "agent":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="坐席账号不支持新建客户咨询会话",
        )
    clean_title = (payload.title or "").strip() if payload and payload.title else None
    conv_id = str(uuid.uuid4())
    thread_id = str(uuid.uuid4())
    now = _now_text()
    conn.execute(
        """
        INSERT INTO conversations (id, customer_id, thread_id, title, status, created_at, updated_at)
        VALUES (?, ?, ?, ?, 'ai_handling', ?, ?)
        """,
        (conv_id, user["id"], thread_id, clean_title, now, now),
    )
    conn.commit()
    return ConversationCreateOut(
        id=conv_id,
        thread_id=thread_id,
        title=clean_title,
        status="ai_handling",
        created_at=now,
    )


@router.get("", response_model=list[ConversationOut], summary="会话列表（客户只看自己）")
def list_conversations(
    user: CurrentUser,
    status_filter: str | None = Query(default=None, alias="status", description="按会话状态过滤"),
    conn: sqlite3.Connection = Depends(get_db),
) -> list[ConversationOut]:
    sql = """
        SELECT c.*, u.display_name AS customer_name,
               (SELECT m.content FROM messages AS m WHERE m.conversation_id = c.id ORDER BY m.id DESC LIMIT 1) AS last_message
        FROM conversations AS c
        JOIN users AS u ON u.id = c.customer_id
    """
    conditions: list[str] = []
    params: list[object] = []

    if user["role"] == "customer":
        conditions.append("c.customer_id = ?")
        params.append(user["id"])
    if status_filter:
        conditions.append("c.status = ?")
        params.append(status_filter)

    if conditions:
        sql += " WHERE " + " AND ".join(conditions)
    sql += " ORDER BY c.updated_at DESC, c.rowid DESC"

    rows = conn.execute(sql, params).fetchall()
    return [_conv_out(row, last_message=row["last_message"]) for row in rows]


@router.get("/{conversation_id}", response_model=ConversationOut, summary="会话详情")
def get_conversation(
    conversation_id: str,
    user: CurrentUser,
    conn: sqlite3.Connection = Depends(get_db),
) -> ConversationOut:
    row = _load_accessible_conversation(conn, conversation_id, user)
    last_msg_row = conn.execute(
        "SELECT content FROM messages WHERE conversation_id = ? ORDER BY id DESC LIMIT 1",
        (conversation_id,),
    ).fetchone()
    return _conv_out(row, last_message=last_msg_row["content"] if last_msg_row else None)


@router.get(
    "/{conversation_id}/messages",
    response_model=list[MessageOut],
    summary="会话消息列表",
)
def list_messages(
    conversation_id: str,
    user: CurrentUser,
    conn: sqlite3.Connection = Depends(get_db),
) -> list[MessageOut]:
    _load_accessible_conversation(conn, conversation_id, user)
    rows = conn.execute(
        """
        SELECT id, sender_role, content, has_image, image_url, citations_json, trace_id, created_at
        FROM messages
        WHERE conversation_id = ?
        ORDER BY id ASC
        """,
        (conversation_id,),
    ).fetchall()

    result: list[MessageOut] = []
    for row in rows:
        citations: list[str] = []
        if row["citations_json"]:
            try:
                loaded = json.loads(row["citations_json"])
                if isinstance(loaded, list):
                    citations = [str(c) for c in loaded]
            except json.JSONDecodeError:
                citations = []
        result.append(
            MessageOut(
                id=row["id"],
                sender_role=row["sender_role"],
                content=row["content"],
                has_image=bool(row["has_image"]),
                image_url=row["image_url"],
                citations=citations,
                citation_details=_resolve_citation_details(conn, citations) if citations else [],
                trace_id=row["trace_id"],
                created_at=row["created_at"],
            )
        )
    return result


@router.get(
    "/{conversation_id}/attachments/{filename}",
    summary="获取会话中上传的报错截图（强制鉴权与租户隔离）",
)
def get_attachment(
    conversation_id: str,
    filename: str,
    user: CurrentUser,
    conn: sqlite3.Connection = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> FileResponse:
    _load_accessible_conversation(conn, conversation_id, user)
    if not _SAFE_ATTACHMENT_NAME.match(filename):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="附件不存在")
    upload_root = (settings.data_dir / "uploads" / conversation_id).resolve()
    target = (upload_root / filename).resolve()
    if target.parent != upload_root or not target.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="附件不存在")
    ext = target.suffix.lower()
    media_type = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".webp": "image/webp",
    }.get(ext, "application/octet-stream")
    return FileResponse(target, media_type=media_type)


# ---------------------------------------------------------------------------
# SSE 流式问答接口（兼容 multipart/form-data 与 application/json）
# ---------------------------------------------------------------------------
@router.post(
    "/{conversation_id}/chat/stream",
    summary="发送消息并通过 SSE 流式获取智能体响应（支持 multipart 与 JSON）",
    openapi_extra=_CHAT_STREAM_OPENAPI_EXTRA,
)
async def chat_stream(
    conversation_id: str,
    request: Request,
    user: CurrentUser,
    conn: sqlite3.Connection = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> StreamingResponse:
    conv = _load_accessible_conversation(conn, conversation_id, user)
    if conv["customer_id"] != user["id"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="仅会话归属客户可通过该接口发言，坐席请使用坐席回复接口",
        )
    if conv["status"] == "closed":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="该会话已关闭，请新建会话")

    content_type = (request.headers.get("content-type") or "").lower()
    is_json = "application/json" in content_type

    # 0. 流式硬限流前置检查（防 chunked / 谎报 Content-Length 撑爆内存，且按格式返回精准 413 提示）
    if is_json:
        await _enforce_bounded_request_body(
            request,
            max_bytes=_MAX_JSON_BODY_BYTES,
            limit_detail=f"JSON 请求体超过上限（最大允许 {_MAX_JSON_BODY_BYTES // 1024} KB）",
        )
    else:
        await _enforce_bounded_request_body(
            request,
            max_bytes=_MAX_MULTIPART_BODY_BYTES,
            limit_detail=(
                f"上传请求体超过上限（总大小最大允许 {_MAX_MULTIPART_BODY_BYTES // (1024 * 1024)} MB，"
                f"其中单张图片 ≤{MAX_IMAGE_BYTES // (1024 * 1024)} MB）"
            ),
        )

    raw_message = ""
    upload_file: StarletteUploadFile | None = None

    if is_json:
        try:
            body = await request.json()
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="JSON 请求体格式非法"
            ) from exc
        if not isinstance(body, dict) or not isinstance(body.get("message"), str):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="JSON 请求体必须包含字符串字段 message",
            )
        raw_message = body["message"]
    else:
        try:
            form = await request.form()
        except Exception as exc:
            detail_text = str(exc)
            if "exceeded" in detail_text.lower() or "too large" in detail_text.lower():
                raise HTTPException(
                    status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                    detail="表单字段或请求体超过大小上限",
                ) from exc
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="表单请求体格式非法"
            ) from exc
        msg_field = form.get("message")
        if not isinstance(msg_field, str):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="缺少必填字段 message",
            )
        raw_message = msg_field
        img_field = form.get("image")
        if isinstance(img_field, StarletteUploadFile):
            upload_file = img_field

    clean_message = raw_message.strip()
    if not clean_message:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="消息内容不能为空")
    if len(clean_message) > _MAX_MESSAGE_CHARS:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=f"文本消息超过上限（最多允许 {_MAX_MESSAGE_CHARS} 字符）",
        )

    # 1. 可选图片分块限流读取 + 魔数校验落盘（413 / 400 在进入 SSE 前返回标准 HTTP 状态码）
    image_bytes: bytes | None = None
    saved_image: dict | None = None
    image_url: str | None = None
    if upload_file is not None and (upload_file.filename or upload_file.content_type):
        try:
            raw = await _read_upload_bounded(upload_file, MAX_IMAGE_BYTES)
            if raw:
                conv_upload_dir = settings.data_dir / "uploads" / conversation_id
                saved_image = validate_and_save_image(raw, conv_upload_dir)
                image_bytes = raw
                filename = Path(saved_image["path"]).name
                image_url = f"/api/conversations/{conversation_id}/attachments/{filename}"
        except ImageTooLargeError as exc:
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE, detail=str(exc)
            ) from exc
        except InvalidImageError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
            ) from exc

    # 2. 先落库客户消息，保证顺序固定
    now = _now_text()
    cur = conn.execute(
        """
        INSERT INTO messages (conversation_id, sender_role, content, has_image, image_url, created_at)
        VALUES (?, 'customer', ?, ?, ?, ?)
        """,
        (conversation_id, clean_message, 1 if saved_image else 0, image_url, now),
    )
    customer_msg_id = int(cur.lastrowid)
    conn.execute(
        "UPDATE conversations SET updated_at = ? WHERE id = ?",
        (now, conversation_id),
    )
    conn.commit()

    conv_status = str(conv["status"])
    thread_id = str(conv["thread_id"])
    user_id = int(user["id"])
    user_role = str(user["role"])

    async def event_stream():
        merged: dict[str, Any] = {}
        interrupt_payload: dict[str, Any] | None = None
        persisted_pending = False
        cfg: dict[str, Any] | None = None
        try:
            # 3. 人工接管或排队中：不调 AI，直接落库并推 done
            if conv_status in ("human_handling", "pending_human"):
                yield _sse(
                    "done",
                    {
                        "message_id": customer_msg_id,
                        "decision_type": "human_passthrough",
                        "trace_id": None,
                        "conversation_status": conv_status,
                    },
                )
                return

            # 4. 可选视觉前置识别（失败降级为空串；OCR 文本作为不可信数据显式隔离）
            image_summary = ""
            if image_bytes and saved_image:
                yield _sse("status", {"node": "vision", "message": "正在识别报错截图"})
                image_summary = _sanitize_ocr_text(
                    await describe_image_safe(
                        image_bytes, saved_image["mime"], settings=settings
                    )
                )

            effective_query = (
                f"{clean_message}\n\n"
                f"<untrusted_screenshot_ocr>\n{image_summary}\n</untrusted_screenshot_ocr>"
                if image_summary
                else clean_message
            )

            auth_token = create_auth_context(
                user_id=user_id,
                role=user_role,
                secret=settings.jwt_secret,
                ttl_seconds=300,
            )
            cfg = make_config(
                thread_id=thread_id,
                db_path=settings.app_db,
                auth_context_token=auth_token,
            )
            cfg["configurable"]["run_started_at"] = time.time()

            state_input = {
                "user_query": effective_query,
                "image_summary": image_summary,
                "auth_user_id": user_id,
                "auth_role": user_role,
                "conversation_id": conversation_id,
                "thread_id": thread_id,
            }

            pre_conn = connect(settings.app_db)
            try:
                pre_row = pre_conn.execute(
                    "SELECT COALESCE(MAX(id), 0) AS max_id FROM agent_traces WHERE conversation_id = ?",
                    (conversation_id,),
                ).fetchone()
                prev_trace_id = int(pre_row["max_id"]) if pre_row else 0
            finally:
                pre_conn.close()

            yield _sse("status", {"node": "guard", "message": "正在识别意图"})

            async with open_checkpointer(settings.checkpoint_db) as saver:
                graph = build_graph(saver)
                async for chunk in graph.astream(state_input, cfg, stream_mode="updates"):
                    if "__interrupt__" in chunk:
                        intr = chunk["__interrupt__"]
                        if intr:
                            first = intr[0]
                            interrupt_payload = (
                                first.value if hasattr(first, "value") else dict(first)
                            )
                        else:
                            interrupt_payload = {}
                        handoff_summary_now = (
                            interrupt_payload.get("handoff_summary")
                            or merged.get("handoff_summary")
                            or {}
                        )
                        draft_ticket_now = (
                            interrupt_payload.get("draft_ticket")
                            if "draft_ticket" in interrupt_payload
                            else merged.get("draft_ticket")
                        )
                        persisted_pending = _persist_pending_human(
                            settings.app_db,
                            conversation_id,
                            handoff_summary_now,
                            draft_ticket_now,
                        )
                        continue

                    for node_name, update in chunk.items():
                        if not isinstance(update, dict):
                            continue
                        merged.update(update)

                        if node_name == "guard":
                            intent = update.get("intent")
                            if intent == "kb_qa":
                                yield _sse("status", {"node": "rag", "message": "正在检索知识库"})
                            elif intent == "tool_ops":
                                yield _sse("status", {"node": "tools", "message": "正在调用 MCP 工具诊断云实例"})
                            elif intent == "escalate_hitl":
                                yield _sse("status", {"node": "escalate", "message": "正在整理人工交接摘要"})
                            elif intent == "reject_unsafe":
                                yield _sse("status", {"node": "reject", "message": "触发安全策略校验"})
                        elif node_name == "rag":
                            rag_hits = update.get("rag_hits") or []
                            citations_out = update.get("citations") or []
                            hits_payload = [
                                {
                                    "citation": (
                                        f"[{h['doc_title']} · {h['section_title']}]"
                                        if h.get("section_title")
                                        else f"[{h['doc_title']}]"
                                    ),
                                    "doc_title": h.get("doc_title", ""),
                                    "section_title": h.get("section_title"),
                                    "content": h.get("content", ""),
                                    "score": h.get("score", 0.0),
                                }
                                for h in rag_hits
                            ]
                            yield _sse(
                                "citations",
                                {
                                    "citations": citations_out,
                                    "rag_top_score": float(update.get("rag_top_score") or 0.0),
                                    "hits": hits_payload,
                                },
                            )
                            if update.get("rag_accepted"):
                                yield _sse("status", {"node": "answer_kb", "message": "正在结合官方资料生成回答"})
                            else:
                                yield _sse("status", {"node": "refuse", "message": "检索置信度不足，执行严格拒答"})
                        elif node_name == "tools":
                            if update.get("needs_escalation"):
                                yield _sse("status", {"node": "hitl", "message": "检测到严重故障，正在转接人工坐席"})
                        elif node_name == "escalate":
                            yield _sse("status", {"node": "hitl", "message": "正在转接人工坐席审批"})

            # 5. 分支收尾：挂起 vs 正常回答
            if interrupt_payload is not None:
                handoff_summary = (
                    interrupt_payload.get("handoff_summary")
                    or merged.get("handoff_summary")
                    or {}
                )
                draft_ticket = (
                    interrupt_payload.get("draft_ticket")
                    if "draft_ticket" in interrupt_payload
                    else merged.get("draft_ticket")
                )
                if not persisted_pending:
                    persisted_pending = _persist_pending_human(
                        settings.app_db, conversation_id, handoff_summary, draft_ticket
                    )

                if not persisted_pending:
                    yield _sse(
                        "error",
                        {"code": "STATE_CONFLICT", "message": "会话状态已变更，请刷新后查看最新状态"},
                    )
                    return

                yield _sse(
                    "interrupt",
                    {
                        "type": interrupt_payload.get("type", "human_approval"),
                        "question": interrupt_payload.get("question", "是否批准创建工单并回复客户？"),
                        "handoff_summary": handoff_summary,
                        "draft_ticket": draft_ticket,
                    },
                )
                yield _sse(
                    "done",
                    {
                        "message_id": None,
                        "decision_type": "pending_human",
                        "trace_id": None,
                        "conversation_status": "pending_human",
                    },
                )
                return

            final_answer = str(merged.get("final_answer") or "")
            citations = list(merged.get("citations") or [])
            decision_type = str(merged.get("decision_type") or "kb_answer")

            finish_now = _now_text()
            db = connect(settings.app_db)
            try:
                trace_row = db.execute(
                    """
                    SELECT id FROM agent_traces
                    WHERE conversation_id = ? AND id > ?
                    ORDER BY id DESC LIMIT 1
                    """,
                    (conversation_id, prev_trace_id),
                ).fetchone()
                trace_id = int(trace_row["id"]) if trace_row else None

                msg_cur = db.execute(
                    """
                    INSERT INTO messages (
                        conversation_id, sender_role, content, has_image,
                        citations_json, trace_id, created_at
                    ) VALUES (?, 'assistant', ?, 0, ?, ?, ?)
                    """,
                    (
                        conversation_id,
                        final_answer,
                        json.dumps(citations, ensure_ascii=False),
                        trace_id,
                        finish_now,
                    ),
                )
                assistant_msg_id = int(msg_cur.lastrowid)
                db.execute(
                    "UPDATE conversations SET updated_at = ? WHERE id = ?",
                    (finish_now, conversation_id),
                )
                db.commit()
            finally:
                db.close()

            for segment in _split_segments(final_answer):
                yield _sse("token", {"text": segment})
                await asyncio.sleep(0.02)

            yield _sse(
                "done",
                {
                    "message_id": assistant_msg_id,
                    "decision_type": decision_type,
                    "trace_id": trace_id,
                    "conversation_status": "ai_handling",
                },
            )
        except asyncio.CancelledError:
            # 纯同步取消补偿（零 await，确保在 ASGI/anyio 取消作用域内也能可靠完成落库）
            if not persisted_pending:
                _compensate_pending_human_sync(
                    settings.app_db,
                    settings.checkpoint_db,
                    conversation_id,
                    cfg,
                    interrupt_payload,
                    merged,
                )
            logger.info("SSE 客户端断开连接 conversation_id=%s", conversation_id)
            raise
        except Exception:
            logger.exception("SSE 会话执行异常 conversation_id=%s", conversation_id)
            yield _sse(
                "error",
                {"code": "STREAM_ERROR", "message": "服务暂时不可用，请稍后重试或联系人工坐席"},
            )

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
