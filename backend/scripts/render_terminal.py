# -*- coding: utf-8 -*-
r"""把真实的命令输出渲染成"终端样式"图片，用于报告配图。

【为什么不用抓屏】
    抓屏会被其他窗口遮挡，也可能误抓到个人画面；渲染方式内容完全来自真实输出，
    且同目录保留同名 .txt 原文，可随时核对。

【用法】
    cd backend
    .\.venv\Scripts\python.exe scripts\render_terminal.py <输入.txt> <输出.png> "窗口标题" [宽度]

【依赖】
    playwright（已在 dev 依赖中）+ 本机已安装的 Edge（channel="msedge"，无需下载 Chromium）
"""
from __future__ import annotations

import html
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright


def _read_text(path: Path) -> str:
    raw = path.read_bytes()
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return raw.decode("utf-16")
    if raw[:3] == b"\xef\xbb\xbf":
        return raw[3:].decode("utf-8")
    return raw.decode("utf-8", errors="replace")


def main() -> None:
    txt_path = Path(sys.argv[1])
    png_path = Path(sys.argv[2])
    title = sys.argv[3] if len(sys.argv) > 3 else "PowerShell"
    width = int(sys.argv[4]) if len(sys.argv) > 4 else 1400

    body = html.escape(_read_text(txt_path))
    page_html = f"""<!doctype html>
<html><head><meta charset="utf-8"><style>
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; background: #0c0c0c;
         font-family: "Cascadia Mono", "Consolas", "Microsoft YaHei Mono", monospace; }}
  .win {{ width: {width}px; background: #0c0c0c; }}
  .bar {{ height: 40px; background: #202020; display: flex; align-items: center; padding: 0 16px; gap: 10px; }}
  .dots {{ display: flex; gap: 8px; }}
  .dot {{ width: 12px; height: 12px; border-radius: 50%; }}
  .r {{ background: #ff5f57; }} .y {{ background: #febc2e; }} .g {{ background: #28c840; }}
  .title {{ color: #d0d0d0; font-size: 15px; margin-left: 6px; }}
  pre {{ margin: 0; padding: 18px 22px 26px 22px; color: #cccccc; font-size: 15.5px;
         line-height: 1.55; white-space: pre-wrap; word-break: break-word; }}
</style></head><body>
  <div class="win">
    <div class="bar">
      <div class="dots"><div class="dot r"></div><div class="dot y"></div><div class="dot g"></div></div>
      <div class="title">{html.escape(title)}</div>
    </div>
    <pre>{body}</pre>
  </div>
</body></html>"""

    tmp_html = png_path.with_suffix(".render.html")
    png_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_html.write_text(page_html, encoding="utf-8")

    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)
        page = browser.new_page(viewport={"width": width + 40, "height": 400}, device_scale_factor=2)
        page.goto(tmp_html.resolve().as_uri(), wait_until="load")
        page.wait_for_timeout(400)
        page.locator(".win").screenshot(path=str(png_path))
        browser.close()

    tmp_html.unlink(missing_ok=True)
    print(f"OK {png_path}  {png_path.stat().st_size // 1024} KB")


if __name__ == "__main__":
    main()
