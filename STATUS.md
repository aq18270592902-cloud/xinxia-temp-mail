# STATUS

更新时间：2026-11-27

## 现在能用的

| 功能 | 状态 | 说明 |
|---|---|---|
| 临时邮箱创建 | 可用 | 四个来源：temp-mail.io、tempmail.lol、tempmail.plus、agvuph.cn（自有域名） |
| 来源记忆 | 可用 | 下次打开还是上次选的；第一次默认 agvuph.cn |
| 地址强密码 | 可用 | 16 位，含大小写/数字/符号，与地址一起存库，可看可复制；重复前缀返回同一条 |
| 收信与实时刷新 | 可用 | 前端 1.5 秒轮询；IMAP 侧 QQ 走 IDLE，Junk 每 6 秒补扫一次 |
| 验证码识别/复制 | 可用 | 首页汇总、地址收件列表、我的邮箱列表三处行内直接复制 |
| HTML 邮件渲染 | 可用 | 沙箱 iframe、表格与内联样式保留、图片占位不塌陷、正文顶部固定验证码 |
| 删除地址/删除账号 | 可用 | 居中弹窗，取消/Esc/点遮罩都能关，确认按钮有处理中状态 |
| 访客隔离 | 可用 | cookie `xsid`；临时地址、邮件、验证码、删除按会话过滤 |
| 我的邮箱 | 可用 | 已去掉访问口令，打开即用；QQ/Gmail 账号对所有访客可见 |
| 桌面通知客户端 | 可用 | 托盘常驻、开机自启、弹窗带验证码；复制按钮写入后立即读回比对 |
| 按钮全状态 | 可用 | 默认/悬停/按下/聚焦/禁用/进行中/成功/失败，锁宽度不跳布局 |
| 移动端 390px | 可用 | 无横向溢出 |

## 实测数字（本机同代码实例，非线上）

| 项目 | 数字 | 条件 |
|---|---|---|
| 首屏到可交互 | 142 ms | `performance.mark('app-ready')`，TTFB 12 ms，DOMContentLoaded 110 ms |
| 切换历史地址 | 21 ms | 命中本地缓存；0 滚动位移 |
| 创建自有域名地址 | < 1.2 s | 含一次 `/api/newtemp` + 刷新 |
| 新信到达 QQ → 入库 | 1.72 s | QQ 直投 + IMAP IDLE |
| 新信到达 QQ → 页面出现 | 约 10.8 s – 48.3 s | 经 Cloudflare 转发回同一个 QQ；瓶颈是 CF→QQ 投递 |
| 列表行内复制验证码 | < 250 ms | 剪贴板读回校验为真实值 |

按钮尺寸实测：创建 112→112 px、列表验证码 77×22 三态不变、弹窗确认 88→88 px、账号「测试」41→41 px。

## 线上状态（2026-11-27）

- 服务器 `47.121.140.215:/opt/xinxia` 已更新并 `systemctl restart xinxia`，服务 active。
- `ADMIN_PASS` 已从 systemd unit 删除，接口不再返回 401。
- 源站直连（Host 头 + 443）返回正常页面和 `/api/state`。
- **公网 https://agvuph.cn 目前返回 Cloudflare 525**：Cloudflare 连源站 TLS 握手失败，属于 Cloudflare 侧的 SSL 模式/回源配置问题，需要登录 Cloudflare 控制台把 SSL/TLS 改成 Full，或换成 Cloudflare Origin 证书。程序本身没有报错。
- 原有 80 / 8766 / 8080 端口仍在监听，8766 与 8080 返回 200。

## 已知限制

- **tempmail.lol 大陆基本不可用**：会加随机尾巴、限流、很快过期，只作备用。
- **agvuph.cn 未备案**：80 端口被阿里云拦截，只能走 443 / Cloudflare。
- **Gmail 链路不稳**：IMAP 经常超时，已降级为最后扫描 + 指数退避；QQ 优先。
- **Cloudflare 转发有延迟**：实测同一 QQ 自发自收 10.8s / 48.3s 两档；Gmail 直发会进 QQ 的 Junk，靠 Junk 补扫兜住。后端从信到达 QQ 到入库约 1.7s，瓶颈在 Cloudflare→QQ 投递，不在程序内。
- **本机 DNS 仍指向源站**：本地直接开 https://agvuph.cn 会证书错误，需要走 Cloudflare 或改 hosts。
- 桌面通知只支持 Windows（PowerShell 5 + WinForms），没有做 macOS/Linux。

## 还没做完

- 桌面通知的「点击复制」只在代码里做了写入后读回校验，尚未在真机完成一次 UI 自动化点击验收（复制逻辑本身已有自检能力）。
- 性能只有本机同代码实例的实测；线上首屏没有单独采样。
- 验证码识别对极少数纯图片验证码无效（没有 OCR）。

## 明天继续的入口

```powershell
# 本地起服务（默认 127.0.0.1:18899）
cd C:\Users\aq\Desktop\youxiang
python 邮箱中心.py --no-browser

# 桌面通知器
cd desktop
powershell -ExecutionPolicy Bypass -File .\install.ps1 -Url https://agvuph.cn

# 部署（服务器）
pscp -pw <密码> 邮箱中心.py root@47.121.140.215:/opt/xinxia/
pscp -pw <密码> -r web root@47.121.140.215:/opt/xinxia/
plink -pw <密码> root@47.121.140.215 "systemctl restart xinxia && systemctl status xinxia --no-pager"

# 看服务日志
plink -pw <密码> root@47.121.140.215 "journalctl -u xinxia -n 80 --no-pager"
```

关键文件：

- `邮箱中心.py`：后端（Python 标准库，无第三方依赖）
- `web/index.html` `web/app.css` `web/app.js`：前端
- `desktop/notify.ps1`：Windows 通知器
- `邮箱中心.db`：本地数据库，**不进仓库**
