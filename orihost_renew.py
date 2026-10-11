#!/usr/bin/env python3
"""
Orihost 免费服务器自动续期
流程: 登录 -> 每台服务器 Console 页 -> Renew -> Read Article(新标签停留) -> 过 Turnstile -> Claim Renewal

安装:  pip install playwright && playwright install chromium
运行:  ORIHOST_EMAIL=xxx ORIHOST_PASSWORD=xxx python orihost_renew.py
代理:  可选 NODE_LINK=vless://... (支持 vless/vmess/trojan/ss/socks5/http)
       由 node_proxy.py(同 hohai-checkin) 用 sing-box 转成本地 127.0.0.1 代理给浏览器;
       代理起不来时自动回落直连
Linux 服务器无桌面: sudo apt install xvfb && xvfb-run -a python orihost_renew.py
定时:  0 9 */3 * * cd /path && xvfb-run -a python orihost_renew.py >> renew.log 2>&1
注意: Cloudflare Turnstile 对无头模式拦截很严, 所以这里用有头模式(HEADLESS=0).
"""
import os, re, sys
from datetime import datetime
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
from node_proxy import setup_node_proxy, probe_exit_ip

BASE = "https://panel.orihost.com"
PROFILE = os.getenv("ORIHOST_PROFILE", "./orihost_profile")
EMAIL = os.getenv("ORIHOST_EMAIL", "")
PASSWORD = os.getenv("ORIHOST_PASSWORD", "")
COOKIE = os.getenv("ORIHOST_COOKIE", "").strip()   # 浏览器里复制的整段 Cookie 请求头, 优先使用
HEADLESS = os.getenv("HEADLESS", "0") == "1"
READ_SECONDS = int(os.getenv("READ_SECONDS", "15"))
NODE_LINK = os.getenv("NODE_LINK", "")


def log(m):
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {m}", flush=True)


def solve_turnstile(page, timeout=30):
    """点击 Cloudflare Turnstile 复选框, 等待通过."""
    for _ in range(timeout // 3):
        frame = page.frame_locator("iframe[src*='challenges.cloudflare.com']").first
        try:
            frame.locator("input[type=checkbox], label, body").first.click(timeout=3000)
        except Exception:
            pass
        page.wait_for_timeout(3000)
        # 通过后会写入隐藏字段 cf-turnstile-response
        tok = page.locator("input[name='cf-turnstile-response']")
        if tok.count() and (tok.first.input_value() or "").strip():
            return True
    return False


def setup_network():
    """返回 (本地代理URL或None, 脱敏出口IP, 模式). 代理失败回落直连."""
    if NODE_LINK.strip():
        try:
            proxy, ip, _ = setup_node_proxy(NODE_LINK, workdir=os.getenv("RUNNER_TEMP", "/tmp"))
            return proxy, ip or "未知", "代理"
        except Exception as e:
            log(f"代理启动失败, 回落直连: {e}")
            return None, probe_exit_ip() or "未知", "直连(代理失败)"
    return None, probe_exit_ip() or "未知", "直连"


def add_cookies(ctx):
    if not COOKIE:
        return
    jar = []
    for part in COOKIE.split(";"):
        if "=" in part:
            k, v = part.strip().split("=", 1)
            jar.append({"name": k, "value": v, "domain": "panel.orihost.com",
                        "path": "/", "secure": True})
    ctx.add_cookies(jar)
    log(f"已注入 {len(jar)} 个 cookie")


def ensure_login(page):
    page.goto(BASE, wait_until="networkidle")
    if COOKIE and "/auth" in page.url:
        log("Cookie 已失效, 改用账号密码登录")
    if "/auth" not in page.url:
        log("已是登录状态")
        return
    if not (EMAIL and PASSWORD):
        sys.exit("未登录且没有设置 ORIHOST_EMAIL / ORIHOST_PASSWORD")
    page.goto(f"{BASE}/auth/login", wait_until="networkidle")
    got_it = page.get_by_role("button", name=re.compile("got it", re.I))
    if got_it.count():
        got_it.first.click()
    page.locator("input[type=email], input[name=email], input[name=user], input[name=username]").first.fill(EMAIL)
    page.locator("input[type=password]").first.fill(PASSWORD)
    if not solve_turnstile(page):
        log("登录页 Turnstile 可能未通过, 仍尝试提交")
    page.get_by_role("button", name=re.compile(r"sign in|login|log in", re.I)).first.click()
    page.wait_for_url(lambda u: "/auth" not in u, timeout=30000)
    log("登录成功")


def server_urls(page):
    page.goto(BASE, wait_until="networkidle")
    urls = set()
    for a in page.locator("a[href*='/server/']").all():
        h = a.get_attribute("href") or ""
        m = re.search(r"/server/([0-9a-f]{8})", h)
        if m:
            urls.add(f"{BASE}/server/{m.group(1)}")
    return sorted(urls)


def renewal_days(page):
    m = re.search(r"renewal in\s*:?\s*(\d+)\s*day", page.inner_text("body"), re.I)
    return int(m.group(1)) if m else None


def renew(page, ctx, url):
    page.goto(url, wait_until="networkidle")
    before = renewal_days(page)
    page.get_by_role("button", name=re.compile(r"^\s*renew\s*$", re.I)).first.click()
    page.wait_for_timeout(1500)

    # Read Article: 打开新标签, 停留几秒
    with ctx.expect_page() as newp:
        page.get_by_role("button", name=re.compile("read article", re.I)).first.click()
    article = newp.value
    try:
        article.wait_for_load_state("domcontentloaded", timeout=30000)
    except PWTimeout:
        pass
    article.wait_for_timeout(READ_SECONDS * 1000)
    article.close()
    page.bring_to_front()
    page.wait_for_timeout(2000)

    # 过 Turnstile 后点 Claim Renewal
    solve_turnstile(page)
    claim = page.get_by_role("button", name=re.compile("claim renewal", re.I)).first
    claim.wait_for(state="visible", timeout=15000)
    for _ in range(10):
        if claim.is_enabled():
            break
        solve_turnstile(page, timeout=6)
    claim.click()
    page.wait_for_timeout(4000)
    page.reload(wait_until="networkidle")
    after = renewal_days(page)
    log(f"{url}: {before} 天 -> {after} 天")
    return after is not None and before is not None and after > before


def main():
    proxy_url, ip, mode = setup_network()
    log(f"网络模式: {mode}, 出口 IP: {ip}")
    with sync_playwright() as p:
        kw = dict(headless=HEADLESS, viewport={"width": 1366, "height": 900},
                  args=["--disable-blink-features=AutomationControlled"])
        if proxy_url:
            kw["proxy"] = {"server": proxy_url}   # 本地无认证 mixed 端口
        ctx = p.chromium.launch_persistent_context(PROFILE, **kw)
        add_cookies(ctx)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        ensure_login(page)
        urls = server_urls(page)
        log(f"找到 {len(urls)} 台服务器")
        ok = 0
        for u in urls:
            try:
                ok += renew(page, ctx, u)
            except Exception as e:
                log(f"{u}: 续期失败 {e}")
                page.screenshot(path=f"fail_{u.rsplit('/',1)[-1]}.png")
        log(f"完成: {ok}/{len(urls)} 台成功续期")
        ctx.close()
        sys.exit(0 if ok or not urls else 1)


if __name__ == "__main__":
    main()
