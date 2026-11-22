# -*- coding: utf-8 -*-
"""Day 9 公网部署自动化验收脚本（对照 HANDOFF §5.3 全部 6 项清单）。

安全说明：
    1. 使用标准账号密码接口 `/api/auth/login`（通过环境变量 VERIFY_PASSWORD 提供强口令）；
    2. 验证公网环境已关闭 `/api/auth/demo-login`（必须返回 403）；
    3. 验证公网环境已轮换掉仓库公开弱口令 `Passw0rd!`（admin 用该口令登录必须返回 401）；
    4. HTTPS 默认严格校验证书（适用已备案域名）；仅测试自签名证书或 IP 直连 HTTPS 时可显式传 `--insecure`。

用法：
    $env:VERIFY_PASSWORD = "<你的强口令>"
    cd backend
    .\\.venv\\Scripts\\python.exe ..\\deploy\\verify_public.py http://<YOUR_SERVER_IP>
    .\\.venv\\Scripts\\python.exe ..\\deploy\\verify_public.py https://support.example.com
    .\\.venv\\Scripts\\python.exe ..\\deploy\\verify_public.py https://<YOUR_SERVER_IP> --insecure
"""

from __future__ import annotations

import argparse
import json
import os
import ssl
import urllib.error
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

REPO_ROOT = Path(__file__).resolve().parent.parent


def _build_opener(insecure: bool) -> urllib.request.OpenerDirector:
    handlers: list[urllib.request.BaseHandler] = [urllib.request.ProxyHandler({})]
    if insecure:
        ctx = ssl._create_unverified_context()
        handlers.append(urllib.request.HTTPSHandler(context=ctx))
    return urllib.request.build_opener(*handlers)


def http_json(
    opener: urllib.request.OpenerDirector,
    method: str,
    url: str,
    payload: dict | None = None,
    token: str | None = None,
) -> tuple[int, dict | list | str]:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with opener.open(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            try:
                return resp.status, json.loads(raw)
            except json.JSONDecodeError:
                return resp.status, raw
    except urllib.error.HTTPError as err:
        raw = err.read().decode("utf-8", errors="replace")
        return err.code, raw


def main() -> None:
    parser = argparse.ArgumentParser(description="Day 9 公网部署 6 项验收脚本")
    parser.add_argument("base_url", nargs="?", default="http://<YOUR_SERVER_IP>", help="目标部署地址（http:// 或 https://）")
    parser.add_argument(
        "--insecure",
        action="store_true",
        help="跳过 TLS 证书校验（仅用于自签名证书或 IP 直连 HTTPS 测试，域名生产验收勿开）",
    )
    args = parser.parse_args()

    base = args.base_url.rstrip("/")
    password = os.environ.get("VERIFY_PASSWORD", "").strip()
    if not password:
        raise SystemExit("请先通过环境变量 VERIFY_PASSWORD 提供验收账号口令（不再使用免密 demo-login）")

    opener = _build_opener(args.insecure)
    tls_note = " (--insecure)" if args.insecure else ""
    lines: list[str] = [f"$ python deploy/verify_public.py {base}{tls_note}", ""]

    # 1. 健康检查
    st, body = http_json(opener, "GET", f"{base}/api/health")
    assert st == 200 and isinstance(body, dict) and body.get("status") == "ok", f"健康检查失败: {st} {body}"
    lines.append(f"[1/6] GET {base}/api/health -> {st} OK  ({json.dumps(body, ensure_ascii=False)})")

    # 2. 校验免密演示登录已关闭(403)、公开弱口令已失效(401)，并通过标准密码接口登录四个角色
    demo_st, _ = http_json(opener, "POST", f"{base}/api/auth/demo-login", {"username": "admin"})
    assert demo_st == 403, f"公网安全告警：demo-login 未关闭（实际返回 {demo_st}，期望 403）"

    weak_st, _ = http_json(
        opener, "POST", f"{base}/api/auth/login", {"username": "admin", "password": "Passw0rd!"}
    )
    assert weak_st == 401, f"公网安全告警：admin 仍可用公开弱口令登录（实际返回 {weak_st}，期望 401）"

    tokens: dict[str, str] = {}
    for uname in ("zhangsan", "lisi", "wanggong", "admin"):
        st, b = http_json(opener, "POST", f"{base}/api/auth/login", {"username": uname, "password": password})
        assert st == 200 and isinstance(b, dict) and b.get("access_token"), f"{uname} 登录失败: {st} {b}"
        tokens[uname] = b["access_token"]
    lines.append("[2/6] 免密 demo-login 已拦截 (403) + 公开弱口令已失效 (401)；四角色标准强口令登录 -> 全部 200 OK")

    # 3. 客户问答有引用 + 不可答拒答（通过 multipart SSE）
    import httpx

    with httpx.Client(timeout=60.0, trust_env=False, verify=not args.insecure) as client:
        c1 = client.post(
            f"{base}/api/conversations",
            json={"title": "公网验收问答"},
            headers={"Authorization": f"Bearer {tokens['zhangsan']}"},
        ).json()["id"]
        with client.stream(
            "POST",
            f"{base}/api/conversations/{c1}/chat/stream",
            data={"message": "Nginx 502 怎么排查"},
            headers={"Authorization": f"Bearer {tokens['zhangsan']}"},
        ) as r:
            raw_sse = "".join(r.iter_text())
        assert "event: citations" in raw_sse and "kb_answer" in raw_sse, f"问答引用校验失败: {raw_sse[:300]}"

        c_ref = client.post(
            f"{base}/api/conversations",
            json={"title": "公网验收拒答"},
            headers={"Authorization": f"Bearer {tokens['zhangsan']}"},
        ).json()["id"]
        with client.stream(
            "POST",
            f"{base}/api/conversations/{c_ref}/chat/stream",
            data={"message": "如何把数据库迁移到 OceanBase"},
            headers={"Authorization": f"Bearer {tokens['zhangsan']}"},
        ) as r:
            raw_ref = "".join(r.iter_text())
        assert "kb_refuse" in raw_ref, f"拒答校验失败: {raw_ref[:300]}"
        lines.append("[3/6] 公网 SSE 问答（命中引用 kb_answer）与超范围提问（严格拒答 kb_refuse） -> 通过")

        # 4. 故障诊断 -> 挂起 -> 坐席批准 -> 建单完整闭环
        c2 = client.post(
            f"{base}/api/conversations",
            json={"title": "公网验收故障闭环"},
            headers={"Authorization": f"Bearer {tokens['zhangsan']}"},
        ).json()["id"]
        with client.stream(
            "POST",
            f"{base}/api/conversations/{c2}/chat/stream",
            data={"message": "我的实例 ubuntu-web-prod 诊断一下"},
            headers={"Authorization": f"Bearer {tokens['zhangsan']}"},
        ) as r:
            raw_hitl = "".join(r.iter_text())
        assert "event: interrupt" in raw_hitl and "pending_human" in raw_hitl, f"挂起失败: {raw_hitl[:300]}"

        res_resume = client.post(
            f"{base}/api/agent/conversations/{c2}/resume",
            json={"action": "approve_ticket_and_reply", "comment": "公网部署验收审批"},
            headers={"Authorization": f"Bearer {tokens['wanggong']}"},
        )
        assert res_resume.status_code == 200
        ticket_no = res_resume.json()["ticket_no"]
        assert ticket_no and ticket_no.startswith("CSG-")
        lines.append(f"[4/6] 故障诊断 -> 挂起 -> 坐席批准建单闭环 -> 通过（生成工单 {ticket_no}）")

        # 5. 跨用户访问返回 404
        cross_conv = client.get(
            f"{base}/api/conversations/{c1}/messages",
            headers={"Authorization": f"Bearer {tokens['lisi']}"},
        )
        assert cross_conv.status_code == 404, f"预期 404，实际 {cross_conv.status_code}"
        lines.append("[5/6] 跨租户隔离（李四访问张三会话消息） -> 404 Not Found（不泄露存在性）")

    # 6. 公网首页 Playwright 可打开并截图
    with sync_playwright() as p:
        b = p.chromium.launch(channel="msedge", headless=True)
        ctx = b.new_context(
            viewport={"width": 1440, "height": 900},
            device_scale_factor=2,
            ignore_https_errors=args.insecure,
        )
        page = ctx.new_page()
        page.goto(f"{base}/login", wait_until="networkidle", timeout=30000)
        page.wait_for_selector("text=系统登录", timeout=15000)
        shot = REPO_ROOT / "docs/report-assets/演示截图/Day9-02-公网首页截图.png"
        page.screenshot(path=str(shot))
        b.close()
    lines.append(f"[6/6] 公网首页浏览器访问与截图 ({base}/login) -> 通过（已保存 {shot.name}）")

    out_txt = REPO_ROOT / "docs/report-assets/关键节点/Day9-01-公网部署验证.txt"
    out_txt.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
