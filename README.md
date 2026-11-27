# 信匣 · 临时邮箱

一个只用 Python 标准库写的临时邮箱站。选服务商、填前缀、创建地址，页面自动收信，验证码直接复制。也可以接入自己的 QQ / Gmail，用 IMAP 长期收信。

无第三方依赖，不用 `pip install`，`python 邮箱中心.py` 就能跑。

## 功能

- 三家免费临时邮箱供应商：temp-mail.io（默认）、tempmail.lol、tempmail.plus
- 自有域名收信：`agvuph.cn` 全量转发到自己的 QQ 邮箱（Gmail 也能用，国内服务器到 Gmail 的 IMAP 不稳定），项目读 IMAP 后按原始收件人还原成独立地址
- 每个地址自动生成 16 位强密码（大小写 + 数字 + 符号），给注册目标站时用，不是邮箱登录密码
- 自定义前缀：留空随机；非法字符、超长会提示，不会静默失败
- 自动收信：前台定时轮询 + 后台定时扫描，没有「刷新」按钮也能出新邮件（也保留了手动刷新）
- 验证码识别：按上下文抓 4–8 位数字，列表上直接给复制按钮
- 主页直接展示「我的邮箱」收到的最新验证码
- 自己的 QQ / Gmail 走 IMAP 收信，授权码只存在本机 SQLite
- 历史地址可切换、可删除；白底细线排版，桌面和移动端都适配

## 运行

需要 Python 3.8+，不需要安装任何依赖。

```bash
python 邮箱中心.py
```

默认打开 `http://127.0.0.1:18899`。

```bash
python 邮箱中心.py --port 9000 --no-browser
HOST=0.0.0.0 PORT=8080 python 邮箱中心.py --no-browser   # 公网 / 容器
```

### 环境变量

| 变量 | 默认值 | 说明 |
|---|---|---|
| `HOST` | `127.0.0.1` | 监听地址，放公网填 `0.0.0.0` |
| `PORT` | `18899` | 监听端口 |
| `DB_PATH` | 脚本同目录 `邮箱中心.db` | SQLite 数据库位置 |
| `OWN_DOMAIN` | `agvuph.cn` | 自有收信域名，填了就出现在来源列表里 |

## 访客隔离

- 每个浏览器一个随机会话（cookie `xsid`），新建的临时地址记在这个会话名下；列表、邮件、验证码、删除都按会话过滤，别人看不到，同名也不会串。
- 首页对普通访客只返回他自己的临时地址和邮件；「我的邮箱」不需要口令，打开就能看（账号信件对所有人可见）。
- 没有管理员账号，也没有 401 拦截；`accounts`、`main_codes` 直接随首页返回。

也可以用 `--host` / `--port` 覆盖。

## 目录

```
邮箱中心.py        服务端（HTTP 路由 + 供应商适配 + IMAP + SQLite）
web/index.html     页面
web/app.css        样式
web/app.js         前端逻辑
blocklist.txt      一次性邮箱域名黑名单（接口拉不到时的兜底）
```

## 收信说明

- 临时地址由供应商托管，能不能收到取决于对方是否真的投递到该地址。地址过期后供应商会收回信箱，页面会标「已过期」。
- tempmail.lol 会给前缀自动加 6 位随机尾巴，且按出口 IP 限制中国大陆访问；需要自备代理（环境变量 `HTTPS_PROXY`，或在数据表 `meta` 里写 `proxy`）。
- 自己的邮箱需要在服务商后台开启 IMAP，并生成独立的授权码 / 应用专用密码，不要填登录密码。

## 自有域名收信（Cloudflare Email Routing）

临时邮箱供应商的域名多半被 Qoder 这类站点拉黑，收不到验证码。用自己域名可以绕开：把整个域名做 catch-all，全部转发到一个 Gmail，项目再读这个 Gmail，按邮件原始收件人拆成一个个地址。

### 1. Cloudflare 后台

1. 域名的 NS 指向 Cloudflare（注册商处改 NS，等生效）。
2. Email → Email Routing → 启用，按提示给域名加 MX / SPF 记录。
3. Destination addresses 里加你的 Gmail，点验证邮件里的链接。
4. Routing rules 加一条 Catch-all：`*@agvuph.cn` → 转发到该 Gmail。
5. 收尾：Cloudflare Email Routing 只负责转发，不存邮件；收信有几秒到十几秒延迟。

### 2. Gmail 过滤器（重要，别让验证码进垃圾箱）

Gmail → 设置 → 过滤器 → 创建新过滤器：

- 匹配条件：`收件人` 包含 `@agvuph.cn`
- 操作：勾选「永不进入垃圾邮件」+「应用标签：自有域名」

转发邮件经常被判成垃圾，加了这条过滤器才稳定。

### 3. 项目里接线

「我的邮箱」页添加这个 Gmail：开启两步验证 → 生成 16 位应用专用密码 → 填进去。后台扫描会读 INBOX 的新邮件，按 `X-Original-To` / `X-Forwarded-To` / `To` 里的 `@agvuph.cn` 地址归位：

- 同一地址的邮件归到一组；
- 不同前缀互不串；
- 以前没登记过的前缀，收到信会自动补一条地址记录。

然后在主页来源里选 `agvuph.cn`，填前缀就建，留空随机。

## 部署

推荐让 Nginx 反代到本机端口：

```nginx
server {
    listen 80;
    server_name example.com;

    location / {
        proxy_pass http://127.0.0.1:18899;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }
}
```

服务端只监听 `127.0.0.1`，由 Nginx 对外，不要把带数据库的目录直接暴露成静态站点。

## 隐私

数据库里保存的是你自己添加的邮箱授权码，以及收到的邮件正文。`邮箱中心.db` 属于私密数据，已在 `.gitignore` 里排除，请不要提交到公开仓库。

## 许可

MIT

## 桌面通知客户端（Windows）

`desktop/` 下是一个托盘常驻的小程序，新邮件到了会在屏幕右下角弹窗，验证码可以直接点「复制验证码」。

只依赖 Windows 自带的 PowerShell 5 + WinForms，不用装 Python 或 pip 包。

1. 如果需要改服务器地址，复制 `desktop/config.example.json` 为 `desktop/config.json`，改 `BaseUrl` 即可（不用填口令）。
2. 右键「以管理员身份运行」`desktop/install.bat`，或者：
   ```powershell
   powershell -ExecutionPolicy Bypass -File .\desktop\install.ps1 -Url https://agvuph.cn
   ```
3. 脚本会写配置、在「启动」文件夹放快捷方式（开机自启），并立刻启动一份。

托盘图标右键：立即检查 / 暂停或继续 / 打开网站 / 退出。程序按 `/api/updates` 轮询，用邮件 id 去重，同一封不会重复弹；连不上时按 10 秒 → 20 秒 → … 最多 5 分钟退避，不刷日志。

想开机自启但不弹黑框：启动项指向 `notify.vbs`，它用隐藏窗口跑 `notify.ps1`。
