# auto-renew-orihost

Orihost 免费服务器自动续期（Renew → Read Article → Turnstile → Claim Renewal，每次 +7 天）。

## 使用
在仓库 Settings → Secrets and variables → Actions 添加：

| Secret | 说明 |
| --- | --- |
| `ORIHOST_COOKIE` | 可选，优先使用。电脑浏览器登录面板后 F12 → Network → 任一 panel.orihost.com 请求头里整段 Cookie；失效时自动回落账号密码 |
| `GH_PAT` | 可选，用于自动回写 ORIHOST_COOKIE。classic 令牌勾 `repo`，或 fine-grained 令牌给本仓库 Secrets 读写权限 |
| `ORIHOST_EMAIL` | 面板登录邮箱/用户名 |
| `ORIHOST_PASSWORD` | 面板密码 |
| `NODE_LINK` | 可选，代理节点链接（vless/vmess/trojan/ss/socks5/http），由 `node_proxy.py` 用 sing-box 转本地代理，失败回落直连 |

登录顺序：先用 `ORIHOST_COOKIE`，失效则用账号密码登录；登录成功后导出最新 cookie，配置了 `GH_PAT` 时自动写回 `ORIHOST_COOKIE`。

工作流每 3 天北京时间 09:00 运行，也可在 Actions 页面手动触发。失败时会上传截图。

## 本地运行
```bash
pip install -r requirements.txt && playwright install chromium
ORIHOST_EMAIL=xxx ORIHOST_PASSWORD=xxx python orihost_renew.py
```
