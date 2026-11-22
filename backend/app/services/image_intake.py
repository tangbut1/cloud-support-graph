"""报错截图接收服务：魔数校验 + 体积限制 + 落盘 + 视觉识别降级。

【为什么不能只看文件扩展名和 Content-Type】
    扩展名和 HTTP 头都可以被客户端随意伪造（例如把恶意脚本改名为 error.png）。
    读取文件前若干字节的 Magic Bytes（魔数）才能确认真实格式：
        PNG  -> 89 50 4E 47
        JPEG -> FF D8 FF
        WEBP -> RIFF....WEBP

【视觉降级原则】
    多模态理解属于增强能力：如果大模型未配置或超时失败，
    必须返回空字符串，绝不能阻断主聊天与诊断流程。
"""

from __future__ import annotations

import logging
import uuid
from pathlib import Path

from app.agent.llm import LlmClient
from app.config import Settings

logger = logging.getLogger(__name__)

MAX_IMAGE_BYTES = 2 * 1024 * 1024  # 2 MB


class ImageIntakeError(Exception):
    """图片上传校验错误基类。"""


class ImageTooLargeError(ImageIntakeError):
    """图片超过大小上限（对应 HTTP 413）。"""


class InvalidImageError(ImageIntakeError):
    """图片为空或魔数不合法（对应 HTTP 400）。"""


def detect_image_type(data: bytes) -> tuple[str, str]:
    """根据文件头魔数识别图片类型，返回 (mime, ext)。"""
    if not data:
        raise InvalidImageError("上传的图片内容为空")
    if data[:4] == b"\x89\x50\x4e\x47":
        return "image/png", "png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg", "jpg"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp", "webp"
    raise InvalidImageError("仅支持 PNG、JPEG、WEBP 格式图片（文件头校验未通过）")


def validate_and_save_image(data: bytes, upload_dir: Path) -> dict:
    """校验体积与魔数并保存到指定目录，返回 {"path", "mime", "size"}。"""
    if len(data) > MAX_IMAGE_BYTES:
        raise ImageTooLargeError(f"图片大小超过上限（最大允许 {MAX_IMAGE_BYTES // (1024 * 1024)} MB）")
    mime, ext = detect_image_type(data)
    upload_dir.mkdir(parents=True, exist_ok=True)
    target = upload_dir / f"{uuid.uuid4().hex}.{ext}"
    target.write_bytes(data)
    return {
        "path": str(target),
        "mime": mime,
        "size": len(data),
    }


async def describe_image_safe(
    data: bytes,
    mime: str,
    *,
    settings: Settings | None = None,
    llm: LlmClient | None = None,
) -> str:
    """调用视觉模型提取截图关键信息；不可用或失败时静默降级为空串。"""
    client = llm or (LlmClient(settings) if settings is not None else None)
    if client is None or not client.enabled:
        return ""
    try:
        text = await client.describe_image(data, mime)
        return (text or "").strip()
    except Exception as exc:  # pragma: no cover - 视觉接口异常兜底
        logger.warning("截图视觉识别异常，已降级为空串: %s", exc)
        return ""


async def process_uploaded_image(
    data: bytes,
    *,
    upload_dir: Path,
    settings: Settings | None = None,
    llm: LlmClient | None = None,
) -> dict:
    """完整处理一张上传截图：校验 -> 落盘 -> 视觉识别。

    返回：{"path", "mime", "size", "summary"}
    """
    saved = validate_and_save_image(data, upload_dir)
    summary = await describe_image_safe(data, saved["mime"], settings=settings, llm=llm)
    return {**saved, "summary": summary}
