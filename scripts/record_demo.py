# -*- coding: utf-8 -*-
"""录制"登录 → 咨询 → 诊断挂起 → 坐席批单 → 客户看单 → 管理员 Trace"演示视频。

用法（PowerShell，密码不入库）：
    $env:DEMO_URL="http://<YOUR_SERVER_IP>"
    $env:DEMO_PASSWORD="<线上统一口令>"
    $env:PLAYWRIGHT_BROWSERS_PATH="D:\dsh\cache\playwright"
    cd backend
    .\.venv\Scripts\python.exe ..\scripts\record_demo.py

产物：D:\dsh\tmp\csg_video\*.webm，再用 ffmpeg 转 MP4：
    ffmpeg -y -i <webm> -vf "scale=1920:1080,fps=30" -c:v libx264 -preset medium \
        -crf 20 -pix_fmt yuv420p -movflags +faststart -an demo.mp4

依赖：pip install playwright；playwright install ffmpeg（录制需要其自带 ffmpeg）。
"""
import os
import traceback

from playwright.sync_api import sync_playwright

BASE = os.environ.get("DEMO_URL", "http://<YOUR_SERVER_IP>").rstrip("/")
PWD = os.environ.get("DEMO_PASSWORD", "")
VDIR = os.environ.get("DEMO_VIDEO_DIR", r"D:\dsh\tmp\csg_video")
VIEW_W, VIEW_H = 1920, 1080

INIT_JS = """
(() => {
  if (window.__hudInit) return;
  window.__hudInit = true;
  const mount = () => {
    if (!document.body) { setTimeout(mount, 30); return; }
    if (!document.getElementById('__sub_bar')) {
      const bar = document.createElement('div');
      bar.id = '__sub_bar';
      bar.style.cssText = 'position:fixed;left:50%;bottom:58px;transform:translateX(-50%);' +
        'max-width:1560px;padding:15px 32px;border-radius:999px;background:rgba(17,17,19,0.80);' +
        'color:#fff;font-size:26px;line-height:1.35;font-family:"Microsoft YaHei","PingFang SC",sans-serif;' +
        'letter-spacing:0.02em;z-index:2147483647;pointer-events:none;' +
        'box-shadow:0 10px 30px rgba(0,0,0,0.28);opacity:0;transition:opacity .3s ease;' +
        'text-align:center;white-space:nowrap;';
      document.body.appendChild(bar);
    }
    window.__setSub = (t) => {
      const bar = document.getElementById('__sub_bar');
      if (!bar) return;
      if (!t) { bar.style.opacity = '0'; return; }
      bar.textContent = t;
      bar.style.opacity = '1';
    };
  };
  mount();
  document.addEventListener('mousedown', (e) => {
    const s = 18;
    const c = document.createElement('div');
    c.style.cssText = 'position:fixed;left:' + (e.clientX - s / 2) + 'px;top:' + (e.clientY - s / 2) +
      'px;width:' + s + 'px;height:' + s + 'px;border-radius:50%;border:2px solid rgba(255,255,255,.9);' +
      'background:rgba(255,255,255,.28);z-index:2147483646;pointer-events:none;transition:all .5s ease;';
    document.body.appendChild(c);
    requestAnimationFrame(() => {
      c.style.width = '58px'; c.style.height = '58px';
      c.style.left = (e.clientX - 29) + 'px'; c.style.top = (e.clientY - 29) + 'px';
      c.style.opacity = '0';
    });
    setTimeout(() => c.remove(), 700);
  }, true);
})();
"""


def sub(page, text, hold=0.0):
    page.evaluate("t => { if (window.__setSub) window.__setSub(t); }", text)
    if hold:
        page.wait_for_timeout(int(hold * 1000))


def login(page, user, hold=1.0):
    page.goto(BASE + "/login", wait_until="domcontentloaded")
    page.wait_for_selector("[data-testid=login-username]", timeout=40000)
    page.wait_for_timeout(900)
    page.click("[data-testid=login-username]")
    page.type("[data-testid=login-username]", user, delay=110)
    page.click("[data-testid=login-password]")
    page.type("[data-testid=login-password]", PWD, delay=60)
    page.wait_for_timeout(420)
    page.click("[data-testid=login-submit]")


def logout(page):
    page.get_by_text("切换/退出", exact=False).first.click()
    page.wait_for_timeout(500)


def main() -> None:
    if not PWD:
        raise SystemExit("请先设置环境变量 DEMO_PASSWORD")
    os.makedirs(VDIR, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", args=["--force-device-scale-factor=1"])
        ctx = browser.new_context(
            viewport={"width": VIEW_W, "height": VIEW_H},
            record_video_dir=VDIR,
            record_video_size={"width": VIEW_W, "height": VIEW_H},
        )
        page = ctx.new_page()
        page.add_init_script(INIT_JS)
        video = None
        ok, err = False, ""
        try:
            page.goto(BASE + "/login", wait_until="domcontentloaded")
            page.wait_for_selector("[data-testid=login-username]", timeout=40000)
            sub(page, "① 打开云服务智能分诊平台登录页", 2.0)

            page.click("[data-testid=login-username]")
            page.type("[data-testid=login-username]", "zhangsan", delay=110)
            page.click("[data-testid=login-password]")
            page.type("[data-testid=login-password]", PWD, delay=60)
            sub(page, "② 输入客户账号登录（张三 · 客户角色）", 1.2)
            page.click("[data-testid=login-submit]")
            page.wait_for_selector("[data-testid=new-conversation-btn]", timeout=45000)
            page.wait_for_timeout(1400)
            sub(page, "③ 进入客户工作台：我的云实例与工单", 1.8)

            page.click("[data-testid=new-conversation-btn]")
            page.wait_for_selector("[data-testid=chat-input]", timeout=20000)
            page.wait_for_timeout(900)
            sub(page, "④ 新建咨询会话，向智能助手提问", 0.9)
            page.click("[data-testid=chat-input]")
            page.type("[data-testid=chat-input]", "Nginx 502 怎么排查", delay=95)
            sub(page, "⑤ 提问：Nginx 502 怎么排查", 0.9)
            page.click("[data-testid=chat-send-btn]")
            sub(page, "⑥ 正在检索知识库 + 大模型生成回答（流式输出）", 0.6)
            page.wait_for_function(
                "() => document.querySelectorAll('.markdown-body').length > 0", timeout=120000
            )
            page.wait_for_function(
                "() => document.querySelectorAll('svg.animate-spin').length === 0", timeout=120000
            )
            page.wait_for_timeout(2200)
            sub(page, "⑦ 回答完成：结论带官方文档引用（可溯源）", 2.4)

            sub(page, "⑧ 对故障云实例执行一键诊断", 0.5)
            page.get_by_role("button", name="诊断此实例").first.click()
            sub(page, "⑨ 诊断触发高危处置 → 自动转人工坐席（HITL 挂起）", 0.7)
            page.wait_for_selector("[data-testid=pending-human-banner]", timeout=90000)
            page.wait_for_timeout(2600)
            sub(page, "⑩ 会话已挂起，等待人工坐席接管", 2.2)

            logout(page)
            page.wait_for_selector("[data-testid=login-username]", timeout=30000)
            sub(page, "⑪ 切换为坐席账号登录（王工 · 技术支持坐席）", 0.6)
            login(page, "wanggong")
            page.wait_for_selector("[data-testid^=queue-item-]", timeout=45000)
            page.wait_for_timeout(1500)
            sub(page, "⑫ 坐席工作台：待接管队列已出现该会话", 2.0)
            page.locator("[data-testid^=queue-item-]").last.click()
            page.wait_for_selector("[data-testid=approve-ticket-btn]", timeout=30000)
            page.wait_for_timeout(1200)
            sub(page, "⑬ 查看 AI 诊断结论与工单草稿", 1.8)
            page.click("[data-testid=approve-ticket-btn]")
            sub(page, "⑭ 批准建单并回复：断点恢复、工单落库", 0.8)
            page.wait_for_selector("[data-testid=agent-banner]", timeout=60000)
            page.wait_for_timeout(2600)
            sub(page, "⑮ 审批通过，工单已创建（CSG 编号）", 2.4)

            logout(page)
            page.wait_for_selector("[data-testid=login-username]", timeout=30000)
            sub(page, "⑯ 切回客户账号，查看工单进度", 0.6)
            login(page, "zhangsan")
            page.wait_for_selector("[data-testid=new-conversation-btn]", timeout=45000)
            page.click("[data-testid=tab-tickets]")
            page.wait_for_timeout(1800)
            page.wait_for_selector("text=CSG-", timeout=30000)
            sub(page, "⑰ 客户侧看到工单号与状态，业务闭环完成", 2.8)

            logout(page)
            page.wait_for_selector("[data-testid=login-username]", timeout=30000)
            sub(page, "⑱ 切换管理员账号，查看全链路观测", 0.6)
            login(page, "admin")
            page.wait_for_timeout(3000)
            sub(page, "⑲ Trace 看板：每次调用的意图、决策、耗时、Token", 2.6)
            page.mouse.wheel(0, 420)
            page.wait_for_timeout(2000)
            sub(page, "⑳ 演示结束 · 智能分诊与人机协同工单闭环", 2.6)
            sub(page, "", 0.6)
            ok = True
        except Exception as e:  # noqa: BLE001
            err = str(e)
            traceback.print_exc()
        finally:
            video = page.video.path() if page.video else None
            ctx.close()
            browser.close()
    print("RECORD_OK:", ok)
    print("ERROR:", err)
    print("VIDEO:", video)


if __name__ == "__main__":
    main()
