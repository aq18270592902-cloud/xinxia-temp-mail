# 信匣 · 临时邮箱

一个只用 Python 标准库写的临时邮箱站。选服务商、填前缀、创建地址，页面自动收信，验证码直接复制。也可以接入自己的 QQ / Gmail，用 IMAP 长期收信。

无第三方依赖，不用 `pip install`，`python 邮箱中心.py` 就能跑。

## 功能

- 三家免费临时邮箱供应商：temp-mail.io（默认）、tempmail.lol、tempmail.plus
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
