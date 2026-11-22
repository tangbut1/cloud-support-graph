"""Day 6 图片魔数校验、体积上限、落盘与视觉降级测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.config import Settings
from app.services.image_intake import (
    MAX_IMAGE_BYTES,
    ImageTooLargeError,
    InvalidImageError,
    detect_image_type,
    process_uploaded_image,
)

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
JPEG_BYTES = b"\xff\xd8\xff\xe0" + b"\x00" * 32
WEBP_BYTES = b"RIFF\x24\x00\x00\x00WEBP" + b"\x00" * 24


def test_detect_png_jpeg_webp_magic_bytes():
    assert detect_image_type(PNG_BYTES) == ("image/png", "png")
    assert detect_image_type(JPEG_BYTES) == ("image/jpeg", "jpg")
    assert detect_image_type(WEBP_BYTES) == ("image/webp", "webp")


def test_fake_extension_or_plain_text_rejected():
    with pytest.raises(InvalidImageError):
        detect_image_type(b"#!/bin/bash\nrm -rf / # fake.png")
    with pytest.raises(InvalidImageError):
        detect_image_type(b"")


async def test_oversize_image_raises_too_large(tmp_path: Path):
    oversize = PNG_BYTES + b"\x00" * MAX_IMAGE_BYTES
    with pytest.raises(ImageTooLargeError):
        await process_uploaded_image(oversize, upload_dir=tmp_path / "uploads")


async def test_process_uploaded_image_saves_and_degrades_offline(tmp_path: Path, settings: Settings):
    upload_dir = tmp_path / "uploads"
    result = await process_uploaded_image(PNG_BYTES, upload_dir=upload_dir, settings=settings)

    saved_path = Path(result["path"])
    assert saved_path.exists()
    assert saved_path.parent == upload_dir
    assert saved_path.suffix == ".png"
    assert result["mime"] == "image/png"
    assert result["size"] == len(PNG_BYTES)
    # 离线模式（未配置 LLM）必须降级为空串，不阻断主流程
    assert result["summary"] == ""


async def test_process_uploaded_image_uses_mock_vision(tmp_path: Path):
    class _FakeLlm:
        enabled = True

        async def describe_image(self, data: bytes, mime: str) -> str | None:
            return f"Nginx 502 Bad Gateway ({mime}, {len(data)}B)"

    result = await process_uploaded_image(
        JPEG_BYTES, upload_dir=tmp_path / "uploads", llm=_FakeLlm()
    )
    assert result["mime"] == "image/jpeg"
    assert "Nginx 502" in result["summary"]
