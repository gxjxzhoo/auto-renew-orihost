#!/usr/bin/env python3
"""
Orihost 免费服务器自动续期（SeleniumBase UC 模式 + uc_gui_click_captcha，同 hohai-checkin 的过盾方式）

流程: 登录(优先 cookie, 失效用账号密码) -> 每台服务器 Console 页 -> Renew -> Read Article(新标签停留)
      -> 过 Turnstile -> Claim Renewal -> 对比剩余天数 -> 导出最新 cookie

环境变量:
  ORIHOST_COOKIE    可选, 整段 Cookie 请求头, 优先使用
  ORIHOST_EMAIL     账号(邮箱/用户名)
  ORIHOST_PASSWORD  密码
  NODE_LINK         可选, 代理节点(vless/vmess/trojan/ss/socks5/http), node_proxy.py 转本地代理, 失败回落直连
  READ_SECONDS      看文章停留秒数, 默认 15
  COOKIE_OUT        导出 cookie 文件, 默认 cookie.txt

本地: pip install -r requirements.txt && python orihost_renew.py
Linux 无桌面: SeleniumBase 会自动用 xvfb (需 apt install xvfb)
"""
import os
import re
import sys
import time
from datetime import datetime

from node_proxy import setup_node_proxy, probe_exit_ip

BASE = "https://panel.orihost.com"
EMAIL = os.getenv("ORIHOST_EMAIL", "")
PASSWORD = os.getenv("ORIHOST_PASSWORD", "")
COOKIE = os.getenv("ORIHOST_COOKIE", "").strip()
NODE_LINK = os.getenv("NODE_LINK", "")
READ_SECONDS = int(os.getenv("READ_SECONDS", "15"))
COOKIE_OUT = os.getenv("COOKIE_OUT", "cookie.txt")

TS_IFRAME = 'iframe[src*="challenges.cloudflare.com"]'
X_RENEW = "//button[normalize-space()='Renew']"
X_READ = "//button[contains(normalize-space(),'Read Article')]"
X_CLAIM = "//button[contains(normalize-space(),'Claim Renewal')]"
X_SIGNIN = "//button[@type='submit' or contains(normalize-space(),'Sign In')]"


def log(*a):
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}]", *a, flush=True)


# ---------------------------------------------------------------- 网络

def setup_network():
    """返回 (本地代理URL或None, 脱敏出口IP, 模式)。代理失败回落直连。"""
    if NODE_LINK.strip():
        try:
            proxy, ip, _ = setup_node_proxy(NODE_LINK, workdir=os.getenv("RUNNER_TEMP", "/tmp"))
            return proxy, ip or "未知", "代理"
        except Exception as e:
            log("代理启动失败，回落直连：", e)
            return None, probe_exit_ip() or "未知", "直连(代理失败)"
    return None, probe_exit_ip() or "未知", "直连"


# ---------------------------------------------------------------- Turnstile

def ts_token(sb):
    try:
        return sb.execute_script(
            "var e=document.querySelector('input[name=\"cf-turnstile-response\"]');"
            "return e?e.value:'';") or ""
    except Exception:
        return ""


def btn_enabled(sb, xpath):
    try:
        el = sb.find_element(xpath, by="xpath", timeout=1)
        return el.is_enabled() and el.get_attribute("disabled") is None
    except Exception:
        return False


def pass_turnstile(sb, wait_btn_xpath, timeout=90):
    """系统级鼠标点 Turnstile，直到目标按钮可点或拿到 token。"""
    end = time.time() + 20
    while time.time() < end and not sb.is_element_present(TS_IFRAME):
        sb.sleep(1)
    sb.sleep(3)
    start, clicks = time.time(), 0
    while time.time() - start < timeout:
        if btn_enabled(sb, wait_btn_xpath) or ts_token(sb):
            log(f"Turnstile 已通过（点击 {clicks} 次）")
            return True
        if clicks < 4 and (clicks == 0 or time.time() - start > clicks * 20):
            try:
                sb.uc_gui_click_captcha()
            except Exception as e:
                log("uc_gui_click_captcha 异常：", e)
            clicks += 1
        sb.sleep(2)
    return False


# ---------------------------------------------------------------- 登录 / cookie

def logged_in(sb):
    return "/auth" not in sb.get_current_url()


def inject_cookie(sb):
    if not COOKIE:
        return
    n = 0
    for part in COOKIE.split(";"):
        if "=" in part:
            k, v = part.strip().split("=", 1)
            try:
                sb.driver.add_cookie({"name": k, "value": v, "domain": "panel.orihost.com",
                                      "path": "/", "secure": True})
                n += 1
            except Exception as e:
                log(f"cookie {k} 注入失败：{e}")
    log(f"已注入 {n} 个 cookie")


def save_cookies(sb):
    jar = [c for c in sb.driver.get_cookies() if "orihost.com" in c.get("domain", "")]
    if not jar:
        return
    header = "; ".join(f"{c['name']}={c['value']}" for c in jar)
    if os.getenv("GITHUB_ACTIONS"):
        for c in jar:
            if len(c["value"]) > 6:
                print(f"::add-mask::{c['value']}", flush=True)
    fd = os.open(COOKIE_OUT, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(header)
    log(f"已导出 {len(jar)} 个 cookie" + (" (未变化)" if header == COOKIE else ""))


def login(sb):
    sb.uc_open_with_reconnect(BASE + "/", 4)
    sb.sleep(3)
    if COOKIE:
        inject_cookie(sb)
        sb.uc_open_with_reconnect(BASE + "/", 3)
        sb.sleep(4)
        if logged_in(sb):
            log("Cookie 登录成功")
            return
        log("Cookie 已失效，改用账号密码登录")
    elif logged_in(sb):
        log("已是登录状态")
        return

    if not (EMAIL and PASSWORD):
        raise RuntimeError("未登录且没有设置 ORIHOST_EMAIL / ORIHOST_PASSWORD")
    sb.uc_open_with_reconnect(BASE + "/auth/login", 4)
    sb.wait_for_element('input[type="password"]', timeout=40)
    try:
        sb.click("//button[normalize-space()='Got it']", by="xpath", timeout=2)
    except Exception:
        pass
    user_sel = ('form input:not([type="password"]):not([type="hidden"])'
                ':not([type="checkbox"]):not([type="submit"])')
    sb.type(user_sel, EMAIL)
    sb.type('input[type="password"]', PASSWORD)
    if not pass_turnstile(sb, X_SIGNIN):
        sb.save_screenshot("fail_login.png")
        raise RuntimeError("登录页 Turnstile 未通过")
    sb.click(X_SIGNIN, by="xpath")
    end = time.time() + 40
    while time.time() < end and not logged_in(sb):
        sb.sleep(1)
    if not logged_in(sb):
        sb.save_screenshot("fail_login.png")
        raise RuntimeError("账号密码登录失败（仍停在 /auth）")
    log("账号密码登录成功")


# ---------------------------------------------------------------- 续期

KILL_OVERLAY_JS = """
var n=0;
document.querySelectorAll('iframe,div,ins').forEach(function(e){
  var src=(e.src||'')+'';
  if(src.indexOf('challenges.cloudflare.com')>=0) return;
  if(e.querySelector && e.querySelector('iframe[src*="challenges.cloudflare.com"]')) return;
  var st=getComputedStyle(e);
  if(st.position==='fixed' && parseInt(st.zIndex||'0')>=2147483000){e.remove();n++;}
});
return n;
"""


def kill_overlays(sb):
    """移除广告插页（全屏 fixed、z-index 最大的 iframe/div），保留 Turnstile。"""
    try:
        n = sb.execute_script(KILL_OVERLAY_JS) or 0
        if n:
            log(f"已移除 {n} 个广告遮罩")
        return n
    except Exception:
        return 0


def close_extra_tabs(sb, main):
    for h in list(sb.driver.window_handles):
        if h != main:
            try:
                sb.driver.switch_to.window(h)
                sb.driver.close()
            except Exception:
                pass
    sb.driver.switch_to.window(main)


def safe_click(sb, xpath, timeout=20):
    sb.wait_for_element(xpath, by="xpath", timeout=timeout)
    kill_overlays(sb)
    try:
        sb.click(xpath, by="xpath", timeout=5)
    except Exception as e:
        if "intercepted" not in str(e) and "not clickable" not in str(e):
            raise
        kill_overlays(sb)
        sb.js_click(xpath, by="xpath")



def server_urls(sb):
    sb.uc_open_with_reconnect(BASE + "/", 3)
    try:
        sb.wait_for_element("a[href*='/server/']", timeout=40)
    except Exception:
        log("首页没找到服务器链接")
    urls = set()
    for a in sb.find_elements("a[href*='/server/']"):
        m = re.search(r"/server/([0-9a-zA-Z]{8})", a.get_attribute("href") or "")
        if m:
            urls.add(f"{BASE}/server/{m.group(1)}")
    return sorted(urls)


def renewal_days(sb):
    try:
        txt = sb.get_text("body")
    except Exception:
        return None
    m = re.search(r"renewal in\s*:?\s*(\d+)\s*day", txt, re.I)
    return int(m.group(1)) if m else None


def open_server(sb, url):
    sb.uc_open_with_reconnect(url, 3)
    end = time.time() + 40
    while time.time() < end and renewal_days(sb) is None:
        sb.sleep(1)
    return renewal_days(sb)


def read_article(sb, sid, main):
    old = set(sb.driver.window_handles)
    safe_click(sb, X_READ)
    end = time.time() + 15
    while time.time() < end and len(sb.driver.window_handles) == len(old):
        sb.sleep(1)
    new = [h for h in sb.driver.window_handles if h not in old]
    if new:
        sb.driver.switch_to.window(new[0])
        sb.sleep(READ_SECONDS)
    else:
        log(f"{sid}: 没有弹出文章标签，直接等待 {READ_SECONDS}s")
        sb.sleep(READ_SECONDS)
    close_extra_tabs(sb, main)  # 文章页和广告弹窗一起关掉
    sb.sleep(2)
    kill_overlays(sb)


def renew(sb, url):
    sid = url.rsplit("/", 1)[-1]
    try:
        before = open_server(sb, url)
        sb.sleep(2)
        kill_overlays(sb)
        main = sb.driver.current_window_handle
        safe_click(sb, X_RENEW)
        sb.sleep(2)
        close_extra_tabs(sb, main)

        for i in range(3):  # 广告可能吃掉 Read Article 的点击, 最多点 3 次
            read_article(sb, sid, main)
            if sb.is_element_present(X_CLAIM, by="xpath"):
                break
            sb.sleep(5)
            if sb.is_element_present(X_CLAIM, by="xpath"):
                break
            log(f"{sid}: 第 {i + 1} 次阅读后没出现 Claim Renewal，重试")
        sb.wait_for_element(X_CLAIM, by="xpath", timeout=10)

        if not pass_turnstile(sb, X_CLAIM):
            raise RuntimeError("续期弹窗 Turnstile 未通过")
        safe_click(sb, X_CLAIM)
        sb.sleep(5)
        close_extra_tabs(sb, main)
        after = open_server(sb, url)
        log(f"{sid}: {before} 天 -> {after} 天")
        if before is not None and after is not None and after > before:
            return True
        raise RuntimeError("剩余天数没有增加")
    except Exception:
        try:
            sb.save_screenshot(f"fail_{sid}.png")
        except Exception:
            pass
        raise


# ---------------------------------------------------------------- main

def main():
    from seleniumbase import SB

    proxy_url, ip, mode = setup_network()
    log(f"网络模式：{mode}，出口 IP：{ip}")
    kw = dict(uc=True, test=True, headless=False, locale_code="en", ad_block=True,
              xvfb=sys.platform.startswith("linux"))
    if proxy_url:
        kw["proxy"] = proxy_url.split("://", 1)[-1]  # SB 要 host:port

    ok, urls = 0, []
    with SB(**kw) as sb:
        try:
            login(sb)
        except Exception:
            try:
                sb.save_screenshot("fail_login.png")
            except Exception:
                pass
            raise
        save_cookies(sb)  # 登录成功立即导出，续期失败也不影响 cookie 更新

        urls = server_urls(sb)
        log(f"找到 {len(urls)} 台服务器")
        for u in urls:
            for attempt in range(1, 3):
                try:
                    if renew(sb, u):
                        ok += 1
                        break
                except Exception as e:
                    log(f"{u} 第 {attempt} 次续期失败：{str(e).splitlines()[0][:300]}")
        save_cookies(sb)

    log(f"完成：{ok}/{len(urls)} 台成功续期")
    sys.exit(0 if ok or not urls else 1)


if __name__ == "__main__":
    main()
