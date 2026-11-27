#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
邮箱中心.py —— 临时邮箱站

在多家免费临时邮箱供应商那里即时开独立收信箱，页面上直接收信、看正文、复制验证码。
自己的 QQ / Gmail 作为长期账号单独管理，走 IMAP 收信，不放在主界面。

启动：
    python 邮箱中心.py                          # 默认 http://127.0.0.1:18899
    python 邮箱中心.py --port 9000 --no-browser
    HOST=0.0.0.0 PORT=8080 python 邮箱中心.py --no-browser   # 公网 / 容器

环境变量：
    HOST      监听地址，默认 127.0.0.1；部署到公网填 0.0.0.0
    PORT      监听端口，默认 18899
    DB_PATH   数据库文件，默认脚本同目录 邮箱中心.db
"""

import argparse
import email
import imaplib
import json
import os
import random
import re
import socket
import sqlite3
import string
import subprocess
import sys
import threading
import time
import hashlib
import urllib.error
import urllib.request
import webbrowser
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from email.header import decode_header
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs, quote

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.environ.get("DB_PATH") or os.path.join(HERE, "邮箱中心.db")
WEB = os.path.join(HERE, "web")
BLOCKLIST_FILE = os.path.join(HERE, "blocklist.txt")
BLOCKLIST_URL = ("https://raw.githubusercontent.com/disposable-email-domains/"
                 "disposable-email-domains/master/disposable_email_blocklist.conf")

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36"


def web_file(name):
    with open(os.path.join(WEB, name), "r", encoding="utf-8") as f:
        return f.read()

CODE_RES = [
    re.compile(r"(?:verification code|your code|code is|passcode|otp|security code|login code)"
               r"[^0-9A-Za-z]{0,80}([0-9]{4,8})", re.I),
    re.compile(r"(?:验证码|校验码|动态码|确认码|激活码)[^0-9]{0,80}([0-9]{4,8})"),
    re.compile(r"\b([0-9]{6})\b"),
    re.compile(r"\b([0-9]{4,8})\b"),
]
LINK_RE = re.compile(r"https?://[^\s\"'<>)\]]+")
# 只在这类邮件里抠码，避免把新闻/账单里的数字当成验证码
CODE_HINT_RE = re.compile(
    r"验证码|校验码|动态码|确认码|激活码|verification|verify|passcode|"
    r"one[-\s]?time|otp|security code|login code|code is|your code",
    re.I)

# 各家的 IMAP 预设。pass 一律是「授权码 / 应用专用密码」，不是登录密码。
PROVIDERS = {
    "gmail":   ("imap.gmail.com",        993, "应用专用密码：myaccount.google.com/apppasswords（要先开两步验证）"),
    "qq":      ("imap.qq.com",           993, "授权码：QQ邮箱 → 设置 → 账户 → 开启 IMAP → 生成授权码"),
    "foxmail": ("imap.qq.com",           993, "同 QQ，用 QQ 邮箱的授权码"),
    "outlook": ("outlook.office365.com", 993, "应用密码：account.microsoft.com/security 开两步验证后生成"),
    "hotmail": ("outlook.office365.com", 993, "同 outlook"),
    "live":    ("outlook.office365.com", 993, "同 outlook"),
    "icloud":  ("imap.mail.me.com",      993, "App 专用密码：appleid.apple.com → 登录与安全"),
    "163":     ("imap.163.com",          993, "客户端授权密码：163邮箱 → 设置 → POP3/SMTP/IMAP"),
    "126":     ("imap.126.com",          993, "同 163"),
    "custom":  ("",                      993, "自己填 IMAP 服务器和端口"),
}

SURNAME = ["wang", "li", "zhang", "liu", "chen", "yang", "huang", "zhao", "wu", "zhou",
           "xu", "sun", "ma", "zhu", "hu", "guo", "he", "gao", "lin", "luo",
           "zheng", "liang", "xie", "song", "tang", "han", "feng", "deng", "cao", "peng"]
GIVEN = ["wei", "fang", "na", "min", "jing", "lei", "qiang", "jun", "yang", "yong",
         "yan", "jie", "tao", "ming", "chao", "hua", "ping", "gang", "hui", "xiao",
         "peng", "hao", "yu", "xin", "chen", "bo", "ning", "kai", "rui", "qian",
         "shan", "lin", "tian", "cheng", "jia", "yi", "ze", "han", "xuan", "meng"]


def now():
    return datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")


# ---- 代理 ---------------------------------------------------------------
# tempmail.lol 按出口 IP 归属国封锁，中国大陆 IP 直接 403，所以只能借道代理。
# 代理值存在 meta 表的 proxy 键里，界面上能改；没配就走直连。
# 支持的写法：http://127.0.0.1:7897 / http://user:pass@host:port / socks5://...
PROXY_KEY = "proxy"
_PROXY_CACHE = {"v": None, "t": 0.0}
_PROXY_TTL = 5.0


def get_proxy():
    """当前生效的代理串，空串 = 直连。环境变量优先级最高（方便临时覆盖）。"""
    env = (os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
           or os.environ.get("ALL_PROXY") or os.environ.get("all_proxy") or "").strip()
    if env:
        return env
    now_t = time.time()
    if _PROXY_CACHE["v"] is not None and now_t - _PROXY_CACHE["t"] < _PROXY_TTL:
        return _PROXY_CACHE["v"]
    try:
        v = (meta_get(PROXY_KEY, "") or "").strip()
    except Exception:
        v = ""
    _PROXY_CACHE["v"] = v
    _PROXY_CACHE["t"] = now_t
    return v


def set_proxy(v):
    meta_set(PROXY_KEY, (v or "").strip())
    _PROXY_CACHE["v"] = None
    _PROXY_CACHE["t"] = 0.0


def _opener(proxy):
    handlers = []
    if proxy:
        handlers.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    else:
        handlers.append(urllib.request.ProxyHandler({}))   # 显式直连，别被环境变量偷偷接管
    return urllib.request.build_opener(*handlers)


def http(url, data=None, headers=None, method=None, timeout=25, proxy=None):
    """proxy=None 表示按 get_proxy() 自动决定；传 "" 表示强制直连。"""
    h = {"User-Agent": UA, "Accept": "application/json"}
    if headers:
        h.update(headers)
    body = None
    if data is not None:
        body = json.dumps(data).encode()
        h.setdefault("Content-Type", "application/json")
    px = get_proxy() if proxy is None else proxy
    op = _opener(px)
    req = urllib.request.Request(url, data=body, headers=h, method=method)
    try:
        with op.open(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", "replace")
            try:
                return r.status, json.loads(raw) if raw.strip() else {}
            except Exception:
                return r.status, raw
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(raw) if raw.strip() else {}
        except Exception:
            return e.code, raw
    except urllib.error.URLError as e:
        # 代理连不上时给个能看懂的错，而不是裸 SSL/Connection 异常
        return 0, {"error": "网络不通（%s）：%s" % (("经代理 " + px) if px else "直连", str(e.reason)[:120])}


def _subject_text(s):
    """邮件主题常是 =?utf-8?b?...?= 这种编码串，直接显示就是乱码。解一下。"""
    if not s:
        return ""
    try:
        parts = decode_header(s)
        out = []
        for txt, enc in parts:
            if isinstance(txt, bytes):
                out.append(txt.decode(enc or "utf-8", "replace"))
            else:
                out.append(txt)
        return "".join(out).strip()
    except Exception:
        return str(s)


def pick_code(text):
    if not text:
        return ""
    for rex in CODE_RES:
        m = rex.search(text)
        if m:
            return m.group(1)
    return ""


def pick_code_strict(text):
    """只认“验证码 / code 后面紧跟数字”的写法。收件箱里混着广告和账单，
    用宽松规则会把 2026 这种年份当验证码。"""
    if not text:
        return ""
    for rex in CODE_RES[:2]:
        m = rex.search(text)
        if m:
            return m.group(1)
    return ""


def pick_link(text):
    if not text:
        return ""
    m = LINK_RE.search(text)
    return m.group(0) if m else ""


# 临时邮箱里混着广告、账单、注册通知。列表上要直接给「复制验证码」按钮，
# 所以这里只认有上下文的码，宁可漏，不能把订单号、年份当验证码。
CODE_CONTEXT_RES = [
    re.compile(r"(?:verification code|your code|code is|passcode|otp|security code|login code)"
               r"[^0-9A-Za-z]{0,80}([0-9]{4,8})", re.I),
    re.compile(r"(?:验证码|校验码|动态码|确认码|激活码)[^0-9]{0,80}([0-9]{4,8})"),
    re.compile(r"\b([0-9]{4,8})\b[^0-9]{0,60}(?:is your|is the)?[^0-9]{0,16}"
               r"(?:verification code|code|otp|验证码)", re.I),
]


def pick_code_mail(subject, body):
    """给临时邮件抓验证码：先看主题，再看正文；抓不到就返回空，前端不显示复制按钮。"""
    s = (subject or "").strip()
    b = (body or "")[:20000]
    for text in (s, b):
        for rex in CODE_CONTEXT_RES:
            m = rex.search(text)
            if m:
                return m.group(1)
    # 主题本身就是一串数字，基本就是验证码
    m = re.fullmatch(r"([0-9]{4,8})", s)
    if m:
        return m.group(1)
    # 正文里光秃秃一串数字：只有整封信确实在讲验证码时才认，优先 6 位
    if CODE_HINT_RE.search(s + "\n" + b):
        m = re.search(r"(?<![0-9])([0-9]{6})(?![0-9])", b) or re.search(r"(?<![0-9])([0-9]{4,8})(?![0-9])", b)
        if m:
            return m.group(1)
    return ""


# ---------------------------------------------------------------- 数据库
def db():
    con = sqlite3.connect(DB, timeout=20)
    con.execute("""CREATE TABLE IF NOT EXISTS account(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        email TEXT UNIQUE NOT NULL,
        provider TEXT NOT NULL,
        imap_host TEXT,
        imap_port INTEGER DEFAULT 993,
        imap_user TEXT,
        imap_pass TEXT,
        note TEXT,
        created TEXT,
        last_uid INTEGER DEFAULT 0,
        last_scan TEXT,
        last_error TEXT
    )""")
    con.execute("""CREATE TABLE IF NOT EXISTS addr(
        address TEXT PRIMARY KEY,
        account_id INTEGER,
        kind TEXT,
        backend TEXT,
        token TEXT,
        password TEXT,
        tag TEXT,
        created TEXT,
        seen TEXT,
        seen_at TEXT,
        seen_subject TEXT
    )""")
    con.execute("""CREATE TABLE IF NOT EXISTS msg(
        id TEXT PRIMARY KEY,
        address TEXT,
        account_id INTEGER,
        subject TEXT,
        sender TEXT,
        mdate TEXT,
        body TEXT,
        code TEXT,
        link TEXT,
        fetched TEXT
    )""")
    con.execute("""CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT)""")
    # 临时邮箱后端会把信箱删掉（tempmail.lol 过期后 token 直接 404）。
    # 死掉的地址标一下，免得每次刷新都白扫一遍、也免得你以为「怎么一直没信」。
    _cols = [r[1] for r in con.execute("PRAGMA table_info(addr)")]
    if "dead" not in _cols:
        con.execute("ALTER TABLE addr ADD COLUMN dead INTEGER DEFAULT 0")
    if "dead_note" not in _cols:
        con.execute("ALTER TABLE addr ADD COLUMN dead_note TEXT")
    if "owner_sid" not in _cols:
        con.execute("ALTER TABLE addr ADD COLUMN owner_sid TEXT")
    _mcols = [r[1] for r in con.execute("PRAGMA table_info(msg)")]
    if "body_html" not in _mcols:
        con.execute("ALTER TABLE msg ADD COLUMN body_html TEXT")
    _acols = [r[1] for r in con.execute("PRAGMA table_info(account)")]
    if "last_uid_junk" not in _acols:
        con.execute("ALTER TABLE account ADD COLUMN last_uid_junk INTEGER DEFAULT 0")
    # 读列表 / 收信查重都按这几个列过滤，加索引免得数据涨了就慢
    con.execute("CREATE INDEX IF NOT EXISTS idx_addr_owner ON addr(owner_sid)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_addr_seen ON addr(seen_at)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_msg_addr ON msg(address)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_msg_code ON msg(code)")
    # 老版本的 box 表迁移一次就够。之前没加这个开关，每调一次 db() 就把删掉的旧地址
    # 又插回来，用户会以为「删不掉」。
    try:
        done = con.execute("SELECT v FROM meta WHERE k='migrated_box'").fetchone()
        has_box = con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='box'").fetchone()
        if has_box and not done:
            for r in con.execute(
                    "SELECT prefix,address,backend,password,token,tag,created,seen FROM box"):
                con.execute(
                    "INSERT OR IGNORE INTO addr(address,account_id,kind,backend,token,password,tag,created,seen)"
                    " VALUES(?,?,?,?,?,?,?,?,?)",
                    (r[1], None, "temp", r[2], r[4], r[3], r[5], r[6], r[7]))
            con.execute("INSERT OR REPLACE INTO meta(k,v) VALUES('migrated_box','1')")
        if has_box:
            con.execute("DROP TABLE box")
    except Exception:
        pass
    con.commit()
    return con


def rows(cur):
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def meta_get(k, default=""):
    con = db()
    r = con.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
    con.close()
    return r[0] if r else default


def meta_set(k, v):
    con = db()
    con.execute("INSERT OR REPLACE INTO meta(k,v) VALUES(?,?)", (k, str(v)))
    con.commit()
    con.close()


# ---------------------------------------------------------------- 域名体检
_BL = None
_BL_LOCK = threading.Lock()


def load_blocklist(force=False):
    """一次性域名黑名单（＝大白话：平台拿来判断「这是不是小号」的域名清单）。"""
    global _BL
    with _BL_LOCK:
        if _BL is not None and not force:
            return _BL
        doms = set()
        if os.path.exists(BLOCKLIST_FILE) and not force:
            try:
                with open(BLOCKLIST_FILE, "r", encoding="utf-8", errors="ignore") as f:
                    for line in f:
                        line = line.strip().lower()
                        if line and not line.startswith("#"):
                            doms.add(line)
            except Exception:
                pass
        if not doms:
            try:
                st, data = http(BLOCKLIST_URL, timeout=60)
                if st == 200 and isinstance(data, str):
                    for line in data.splitlines():
                        line = line.strip().lower()
                        if line and not line.startswith("#"):
                            doms.add(line)
                    with open(BLOCKLIST_FILE, "w", encoding="utf-8") as f:
                        f.write("\n".join(sorted(doms)))
            except Exception:
                pass
        _BL = doms
        return doms


def mx_records(domain):
    """查 MX（＝大白话：这个域名到底能不能收信）。用系统 nslookup，不装额外库。"""
    out = []
    try:
        r = subprocess.run(["nslookup", "-type=mx", domain],
                           capture_output=True, text=True, timeout=12)
        text = (r.stdout or "") + (r.stderr or "")
        for line in text.splitlines():
            line = line.strip()
            if "mail exchanger" in line.lower():
                # Windows 的 nslookup 长这样：
                #   gmail.com  MX preference = 5, mail exchanger = gmail-smtp-in.l.google.com
                # 所以取最后一个 = 后面的那段，别取第一个
                seg = line.split("=")[-1].strip().split()
                if seg:
                    host = seg[-1].rstrip(".")
                    if host and "." in host:
                        out.append(host)
    except Exception:
        pass
    seen, uniq = set(), []
    for x in out:
        if x not in seen:
            seen.add(x)
            uniq.append(x)
    return uniq


def check_domain(domain):
    domain = (domain or "").strip().lower()
    if "@" in domain:
        domain = domain.split("@")[-1]
    if not domain or "." not in domain:
        return {"ok": False, "error": "域名不合法"}
    bl = load_blocklist()
    blocked = domain in bl
    mx = mx_records(domain)
    if blocked:
        verdict = "黑名单"
        note = "这个域名在一次性邮箱黑名单里，多数平台注册会直接被拒"
    elif not mx:
        verdict = "收不到信"
        note = "查不到 MX 记录，这个域名现在收不了邮件"
    else:
        verdict = "可用"
        note = "不在黑名单，MX 正常"
    return {"ok": True, "domain": domain, "blocked": blocked, "mx": mx,
            "verdict": verdict, "note": note, "list_size": len(bl)}


# ---------------------------------------------------------------- 地址生成
def split_addr(a):
    a = (a or "").strip()
    if "@" not in a:
        return a.lower(), ""
    local, _, dom = a.partition("@")
    return local, dom.lower()


# 姓+名直接拼起来会撞上普通词（jie+guo=结果、hua+he=花盒），
# 这些拼出来不像人名，平台看着也怪。撞上就换一个。
BAD_LOCALS = {
    "jieguo", "huahe", "wangluo", "yonghu", "chengxu", "shuju", "wenti",
    "fangfa", "gongsi", "shijian", "dianhua", "yuming", "zhanghao", "mima",
    "xinxi", "xiaoxi", "youjian", "wenzhang", "baogao", "jihua", "mubiao",
    "gaoxiao", "gaosan", "linian", "huajia", "huayu", "yuxin", "yuyan",
}


def human_local():
    for _ in range(40):
        s, g = random.choice(SURNAME), random.choice(GIVEN)
        style = random.randint(0, 3)
        if style == 0:
            cand = s + g
        elif style == 1:
            cand = s + "." + g
        elif style == 2:
            cand = g + s
        else:
            cand = s + g + str(random.randint(1, 99))
        if cand.replace(".", "") not in BAD_LOCALS:
            return cand
    return random.choice(SURNAME) + random.choice(GIVEN) + str(random.randint(100, 999))


# 哪家邮箱真的支持「一个信箱多个地址」。
# gmail 认点号和 +tag，outlook / icloud 认 +tag；
# qq / 163 / 126 压根没有任意别名，写出来的地址是死的，发过去直接退信。
ALIAS_MODE = {
    "gmail": "both",
    "outlook": "plus", "hotmail": "plus", "live": "plus", "icloud": "plus",
    "qq": "none", "foxmail": "none", "163": "none", "126": "none",
}


# ---------------------------------------------------------------- IMAP
def decode_mime(v):
    if not v:
        return ""
    out = []
    try:
        for text, enc in decode_header(v):
            if isinstance(text, bytes):
                out.append(text.decode(enc or "utf-8", errors="replace"))
            else:
                out.append(text)
    except Exception:
        return str(v)
    return "".join(out)


def body_text(msg):
    chunks = []
    try:
        if msg.is_multipart():
            for part in msg.walk():
                ctype = part.get_content_type()
                if ctype in ("text/plain", "text/html"):
                    payload = part.get_payload(decode=True) or b""
                    cs = part.get_content_charset() or "utf-8"
                    chunks.append(payload.decode(cs, errors="replace"))
        else:
            payload = msg.get_payload(decode=True) or b""
            cs = msg.get_content_charset() or "utf-8"
            chunks.append(payload.decode(cs, errors="replace"))
    except Exception:
        pass
    txt = "\n".join(chunks)
    # 先把 href/src 里的地址挖出来留在正文里，否则下面一剥标签，验证链接就没了
    links = re.findall(r"""(?:href|src)\s*=\s*["']([^"']+)["']""", txt, flags=re.I)
    txt = re.sub(r"<(style|script)[\s\S]*?</\1>", " ", txt, flags=re.I)
    txt = re.sub(r"<[^>]+>", " ", txt)
    txt = re.sub(r"[ \t]{2,}", " ", txt)
    txt = txt.strip()
    if links:
        keep = [u for u in links if u.startswith("http")][:5]
        if keep:
            txt = txt + "\n" + "\n".join(keep)
    return txt


def body_html(msg):
    """取邮件里第一个 text/html 分段，原样返回。"""
    try:
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_type() != "text/html":
                    continue
                if (part.get_content_disposition() or "") == "attachment":
                    continue
                payload = part.get_payload(decode=True) or b""
                cs = part.get_content_charset() or "utf-8"
                return payload.decode(cs, errors="replace")
        elif msg.get_content_type() == "text/html":
            payload = msg.get_payload(decode=True) or b""
            cs = msg.get_content_charset() or "utf-8"
            return payload.decode(cs, errors="replace")
    except Exception:
        pass
    return ""


def _img_placeholder(m):
    """图片换成同尺寸占位块：外链图不加载（防追踪），但布局不塌。"""
    tag = m.group(0)
    def _dim(attr):
        hit = re.search(attr + r"""\s*=\s*["']?\s*(\d+)""", tag, flags=re.I)
        return int(hit.group(1)) if hit else 0
    w, h = _dim("width"), _dim("height")
    if not w:
        st = re.search(r"""style\s*=\s*["']([^"']*)["']""", tag, flags=re.I)
        if st:
            hit = re.search(r"width\s*:\s*(\d+)px", st.group(1), flags=re.I)
            if hit:
                w = int(hit.group(1))
    if not h:
        st = re.search(r"""style\s*=\s*["']([^"']*)["']""", tag, flags=re.I)
        if st:
            hit = re.search(r"height\s*:\s*(\d+)px", st.group(1), flags=re.I)
            if hit:
                h = int(hit.group(1))
    if not w and not h:
        w, h = 240, 120
    elif not w:
        w = 240
    elif not h:
        h = max(60, int(w * 0.5))
    w = min(w, 760)
    h = min(h, 900)
    return ('<span style="display:inline-flex;align-items:center;justify-content:center;'
            'width:%dpx;height:%dpx;max-width:100%%;background:#f2efe8;border:1px solid #e6e1d6;'
            'border-radius:4px;color:#a49c8e;font:12px sans-serif">图片</span>' % (w, h))


def clean_email_html(raw):
    """邮件 HTML 清洗：去脚本/表单/外链脚本，事件属性清零，图片换占位。
    真正的隔离靠前端 iframe sandbox，这里只做基础减害。"""
    if not raw:
        return ""
    h = raw
    h = re.sub(r"<script[\s\S]*?</script\s*>", "", h, flags=re.I)
    h = re.sub(r"<(iframe|object|embed|applet|frame|frameset|form|input|button|select|textarea|meta|link|base)\b[^>]*>",
               "", h, flags=re.I)
    h = re.sub(r"</(iframe|object|embed|applet|frame|frameset|form|input|button|select|textarea|meta|link|base)\s*>",
               "", h, flags=re.I)
    h = re.sub(r"""\son[a-z]+\s*=\s*("[^"]*"|'[^']*'|[^\s>]+)""", "", h, flags=re.I)
    h = re.sub(r"""((?:href|src)\s*=\s*)(["'])\s*javascript:[^"']*\2""", r'\1\2#\2', h, flags=re.I)
    h = re.sub(r"<img\b[^>]*>", _img_placeholder, h, flags=re.I)
    # 邮件常见「整页 height:100% / 100vh」会导致一片空白，收掉顶层固定高度
    h = re.sub(r"(?i)(height\s*:\s*)100(?:\.0)?(?:%|vh|dvh|vw)", r"\1auto", h)
    h = re.sub(r"(?i)(min-height\s*:\s*)100(?:\.0)?(?:%|vh|dvh)", r"\1 0", h)
    h = re.sub(r"(<body[^>]*)\sheight\s*=\s*[\"']?\d+[\"']?", r"\1", h, flags=re.I)
    return h


def imap_connect(acc, timeout=8):
    host = acc["imap_host"]
    port = int(acc["imap_port"] or 993)
    user = acc["imap_user"] or acc["email"]
    pwd = acc["imap_pass"] or ""
    if not host:
        raise RuntimeError("没填 IMAP 服务器")
    if not pwd:
        raise RuntimeError("这个邮箱还没填授权码，点「改码」补上")
    if port == 143:
        m = imaplib.IMAP4(host, port, timeout=timeout)
        try:
            m.starttls()
        except Exception:
            pass
    else:
        m = imaplib.IMAP4_SSL(host, port, timeout=timeout)
    m.login(user, pwd)
    # QQ / 163 不先发 ID 会报 Unsafe Login，发一下，失败也无所谓
    try:
        m._simple_command("ID", '("name" "mailcenter" "version" "1.0" "vendor" "local")')
        m._untagged_response("OK", [None], "ID")
    except Exception:
        pass
    return m


MAIL_ADDR_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def own_domain_recipient(msg):
    """从邮件头里还原「这封信原本是发给哪个自有域名地址」。
    Cloudflare Email Routing 会保留原 To，并可能补 X-Forwarded-To / X-Original-To；
    Gmail 自己的 Delivered-To 是被改写过的，所以放在后面。"""
    if not OWN_DOMAIN:
        return ""
    for hdr in ("X-Original-To", "X-Forwarded-To", "To", "Cc", "Delivered-To"):
        raw = decode_mime(msg.get(hdr, "") or "")
        if not raw:
            continue
        for a in MAIL_ADDR_RE.findall(raw):
            a = a.lower()
            if a.endswith("@" + OWN_DOMAIN):
                return a
    return ""


def imap_scan(acc, known, limit=80, timeout=8):
    """扫一个账号的新邮件，按收件人地址归位，抠验证码。返回 (新消息数, 错误)。"""
    try:
        m = imap_connect(acc, timeout)
    except Exception as e:
        msg = _clean_err(e)[:180]
        # 连不上/密码错也要记下来，不然页面永远显示「未收过」看不出问题在哪
        try:
            con = db()
            con.execute("UPDATE account SET last_scan=?, last_error=? WHERE id=?",
                        (now(), msg, acc["id"]))
            con.commit()
            con.close()
        except Exception:
            pass
        return 0, msg
    try:
        n1, e1 = _imap_scan_conn(m, acc, known, limit)
        # 转发过来的信经常被 QQ 丢进 Junk，这里只捡收件人是自有域名的
        try:
            n2, e2 = _imap_scan_conn(m, acc, known, limit, "Junk", True)
        except Exception:
            n2, e2 = 0, ""
        return n1 + n2, e1 or e2
    except Exception as e:
        try:
            con = db()
            con.execute("UPDATE account SET last_scan=?, last_error=? WHERE id=?",
                        (now(), str(e)[:180], acc["id"]))
            con.commit()
            con.close()
        except Exception:
            pass
        return 0, str(e)[:180]
    finally:
        try:
            m.logout()
        except Exception:
            pass


def _imap_scan_conn(m, acc, known, limit=80, folder="INBOX", junk_only=False):
    """复用已连接的 IMAP 会话扫增量（IDLE 线程也走这里）。"""
    found = 0
    uid_field = "last_uid_junk" if junk_only else "last_uid"
    typ, _ = m.select(folder, readonly=True)
    if typ != "OK":
        return 0, ""
    typ, data = m.uid("search", None, "ALL")
    if typ != "OK":
        return 0, "搜索失败"
    uids = [int(x) for x in (data[0] or b"").split() if x.isdigit()]
    last = int(acc.get(uid_field) or 0)
    if last < 0:
        # 刚加/刚改授权码：拿当前最大 UID 当基线，历史邮件不灌进来
        base = max(uids) if uids else 0
        con = db()
        con.execute("UPDATE account SET %s=?, last_scan=?, last_error='' WHERE id=?" % uid_field,
                    (base, now(), acc["id"]))
        con.commit()
        con.close()
        return 0, ""
    new = [u for u in uids if u > last][-limit:]
    if not new:
        con = db()
        con.execute("UPDATE account SET last_scan=?, last_error='' WHERE id=?",
                    (now(), acc["id"]))
        con.commit()
        con.close()
        return 0, ""
    known_lower = {a.lower(): a for a in known}
    # 先把网络拉取和解析做完，最后一次性写库。边拉边写会让 SQLite 长期握着写锁，
    # 后台临时邮箱扫描一写就报 database is locked。
    parsed = []
    for uid in new:
        typ, raw = m.uid("fetch", str(uid), "(RFC822)")
        if typ != "OK" or not raw or not raw[0]:
            continue
        try:
            msg = email.message_from_bytes(raw[0][1])
        except Exception:
            continue
        to_hdr = decode_mime(msg.get("To", ""))
        own_to = own_domain_recipient(msg)
        if junk_only and not own_to:
            continue
        subj = decode_mime(msg.get("Subject", ""))
        frm = decode_mime(msg.get("From", ""))
        mdate = decode_mime(msg.get("Date", ""))
        text = body_text(msg)
        html_raw = body_html(msg)
        code = ""
        if CODE_HINT_RE.search(subj + "\n" + text[:20000]):
            code = pick_code_strict(text) or pick_code_strict(subj)
        link = pick_link(text)
        addrs = re.findall(r"[\w.+-]+@[\w.-]+\.\w+", to_hdr)
        target = ""
        # 自有域名优先：同一个 Gmail 下按原始收件人拆成不同地址，互相不串
        if own_to:
            target = own_to
        else:
            for a in addrs:
                if a.lower() in known_lower:
                    target = known_lower[a.lower()]
                    break
        if not target:
            target = acc["email"]
        parsed.append({
            "id": ("%d:j%d" % (acc["id"], uid)) if junk_only else ("%d:%d" % (acc["id"], uid)),
            "target": target, "subj": subj,
            "frm": frm, "mdate": mdate, "text": text, "html": html_raw, "code": code,
            "link": link, "own": bool(own_to),
        })
    con = db()
    try:
        for p in parsed:
            # Gmail 里新出现的前缀：补一条地址记录，历史列表里能直接看到
            if p["own"]:
                con.execute(
                    "INSERT OR IGNORE INTO addr(address,account_id,kind,backend,created)"
                    " VALUES(?,?,?,?,?)",
                    (p["target"], None, "temp", OWN_BACKEND, now()))
            con.execute(
                "INSERT OR REPLACE INTO msg(id,address,account_id,subject,sender,mdate,body,body_html,code,link,fetched)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (p["id"], p["target"], acc["id"], p["subj"], p["frm"], p["mdate"],
                 p["text"][:20000], p["html"][:200000], p["code"], p["link"], now()))
            if p["code"]:
                con.execute("UPDATE addr SET seen=?, seen_at=?, seen_subject=? WHERE address=?",
                            (p["code"], now(), p["subj"][:120], p["target"]))
        con.execute("UPDATE account SET %s=?, last_scan=?, last_error='' WHERE id=?" % uid_field,
                    (max(new), now(), acc["id"]))
        con.commit()
    finally:
        con.close()
    return len(parsed), ""


# ---------------------------------------------------------------- 临时邮箱后端
class TempMailIO:
    """temp-mail.io —— 能指定前缀和域名，实测连发 15 次 15 成功，不卡限流。
    代价：7 个域名（bwmyga / lnovic / ruutukf / gmeenramy / olipii / ooynib / tanpony）
    全在一次性黑名单里。所以它只适合「不挑域名、只要有个邮箱能收码」的站。
    收信：GET /api/v3/email/{地址}/messages，返回 body_text + body_html。
    """
    name = "temp-mail.io"
    base = "https://api.internal.temp-mail.io/api/v3"
    can_prefix = True
    can_domain = True
    recommend = True
    note = "前缀随便指定，域名全在黑名单；不限量"
    blurb = "前缀照填，不限次数"

    @staticmethod
    def domains():
        st, res = http(TempMailIO.base + "/domains", proxy="")
        if st == 200 and isinstance(res, dict):
            return [d["name"] for d in (res.get("domains") or []) if d.get("name")]
        return []

    @staticmethod
    def create(prefix="", domain=""):
        payload = {}
        if prefix:
            payload["name"] = prefix
        else:
            payload["min_name_length"] = 10
            payload["max_name_length"] = 10
        if domain:
            payload["domain"] = domain
        st, res = http(TempMailIO.base + "/email/new", payload, proxy="")
        if st != 200 or not isinstance(res, dict) or not res.get("email"):
            raise RuntimeError("temp-mail.io 建号失败：%s" % str(res)[:140])
        return {"address": res["email"], "token": res.get("token", ""),
                "password": "", "backend": TempMailIO.name}

    @staticmethod
    def messages(token, address=""):
        if not address:
            return []
        st, res = http(TempMailIO.base + "/email/" + quote(address, safe="@") + "/messages", proxy="")
        if st != 200 or not isinstance(res, list):
            return []
        out = []
        for it in res[:40]:
            body = it.get("body_text") or ""
            html = it.get("body_html") or ""
            links = re.findall(r"""(?:href|src)\s*=\s*["']([^"']+)["']""", html, flags=re.I)
            if not body:
                body = re.sub(r"<[^>]+>", " ", html)
            if links:
                keep = [u for u in links if u.startswith("http")][:5]
                if keep:
                    body = body.rstrip() + "\n" + "\n".join(keep)
            subject = _subject_text(it.get("subject")) or "(无主题)"
            out.append({"subject": subject,
                        "from": (it.get("from") or "").strip("<>"),
                        "date": it.get("created_at") or "",
                        "body": body.strip(),
                        "html": html,
                        "code": pick_code_mail(subject, body),
                        "link": pick_link(body)})
        return out

    @staticmethod
    def delete(token, address=""):
        return False


class TempMailLol:
    """tempmail.lol —— 唯一「域名干净 + 名字像真人」的一家。
    3 个根域（marchslurp / arcadehearth / roomsranked）都不在一次性黑名单里，
    所以这是真要过风控时唯一能用的。

    接口（2026-11 实测，老的 /generate 已经下线成一张 HTML 说明页了）：
      建号  POST /v2/inbox/create  {"domain": "...", "prefix": "..."}  → 201 {address, token}
      收信  GET  /v2/inbox?token=...                                  → {emails: [...], expired: bool}
      域名参数真的生效；prefix 会被追加 6 位十六进制（shaoyang → shaoyang4884be），
      也就是「指定前缀」只能算建议，不能当精确地址用。

    限流：8 秒一个稳（实测 105 秒连出 12 个零失败）；一次性 1 秒连发 8 个也能过，
    但撞上 429 之后冷却不是 90 秒——实测连续 5 分钟都还是 429。所以这里自己排队，
    宁可慢，不要把自己锁死。
    """
    name = "tempmail.lol"
    base = "https://api.tempmail.lol"
    can_prefix = True          # 只是建议前缀，后面会被追加随机尾巴
    can_domain = False         # 域名由服务端随机分配，旧的三条静态域名已被服务端拒绝
    min_gap = 8.0              # 两次建号之间至少隔这么久，别撞限流
    note = "域名干净、名字像真人；约 8 秒一个"
    blurb = "会加随机尾巴，限流，很快过期"
    DOMAINS = []               # 服务端不再接受指定域名，传域名会报 Invalid domain

    _lock = threading.Lock()
    _last = [0.0]
    # 收信接口也有全局节流：新号多的时候（比如一口气造 10 个）每个号 4 秒问一次，
    # 叠起来就是每秒好几次，免费层会 429。这里兜一个全局最小间隔。
    _read_lock = threading.Lock()
    _read_last = [0.0]
    READ_GAP = 0.45

    @staticmethod
    def domains():
        return list(TempMailLol.DOMAINS)

    @staticmethod
    def _read_turn():
        with TempMailLol._read_lock:
            gap = TempMailLol.READ_GAP - (time.time() - TempMailLol._read_last[0])
            if gap > 0:
                time.sleep(gap)
            TempMailLol._read_last[0] = time.time()

    @staticmethod
    def _wait_turn():
        """排队：保证同一进程内两次建号至少隔 min_gap 秒。"""
        with TempMailLol._lock:
            gap = TempMailLol.min_gap - (time.time() - TempMailLol._last[0])
            if gap > 0:
                time.sleep(gap)
            TempMailLol._last[0] = time.time()

    @staticmethod
    def create(prefix="", domain="", _retry=2):
        payload = {}
        if domain:
            payload["domain"] = domain
        pre = (prefix or "").strip().lower()
        pre = re.sub(r"[^a-z0-9]", "", pre)
        if pre:
            payload["prefix"] = pre[:24]
        for attempt in range(_retry + 1):
            TempMailLol._wait_turn()
            st, res = http(TempMailLol.base + "/v2/inbox/create", payload or {}, proxy=None)
            if st == 429:
                if attempt < _retry:
                    time.sleep(20)
                    continue
                raise RuntimeError("tempmail.lol 限流了（免费层），等几分钟再来")
            if st == 403:
                # 免费层按出口 IP 归属国封锁，中国大陆直接挡，换 UA / 换头都没用。
                msg = str(res)
                if "country" in msg.lower() or "free tier" in msg.lower():
                    raise RuntimeError(
                        "tempmail.lol 拒绝了中国大陆 IP（免费层按国家封）。"
                        "配置本机代理（meta 里的 proxy，如 http://127.0.0.1:7897）再试。")
                raise RuntimeError("tempmail.lol 返回 403：%s" % msg[:140])
            if st in (200, 201) and isinstance(res, dict) and res.get("address"):
                return {"address": res["address"], "token": res.get("token", ""),
                        "password": "", "backend": TempMailLol.name}
            raise RuntimeError("tempmail.lol 建号失败：%s" % str(res)[:140])
        raise RuntimeError("tempmail.lol 限流了（免费层），等几分钟再来")

    @staticmethod
    def messages(token, address=""):
        if not token:
            return []
        TempMailLol._read_turn()
        st, res = http(TempMailLol.base + "/v2/inbox?token=" + quote(token, safe=""), proxy=None)
        if st == 429:
            # 免费层收信接口有全局节流，撞上了不能当成「没有邮件」
            raise ProviderBusy("tempmail.lol 正在限流，稍后自动重试")
        if st in (404, 410):
            # 信箱过期后 token 直接查不到（不是返回 expired=true），这是它唯一的死亡信号
            raise MailboxGone("tempmail.lol 信箱已过期（token 查不到）")
        if st != 200 or not isinstance(res, dict):
            return []
        if res.get("expired") is True:
            raise MailboxGone("tempmail.lol 信箱已过期")
        out = []
        for it in (res.get("emails") or [])[:40]:
            raw = it.get("body") or it.get("html") or ""
            links = re.findall(r"""(?:href|src)\s*=\s*["']([^"']+)["']""", raw, flags=re.I)
            body = re.sub(r"<(style|script)[\s\S]*?</\1>", " ", raw, flags=re.I)
            body = re.sub(r"<[^>]+>", " ", body)
            body = body.replace("&nbsp;", " ").replace("&amp;", "&")
            body = re.sub(r"[ \t]{2,}", " ", body).strip()
            if links:
                keep = [u for u in links if u.startswith("http")][:5]
                if keep:
                    body = body + "\n" + "\n".join(keep)
            subject = _subject_text(it.get("subject")) or "(无主题)"
            # date 有时是毫秒时间戳，有时是 ISO 字符串，两种都吃
            ts = it.get("date") or it.get("created_at")
            when = ""
            if isinstance(ts, (int, float)) and ts > 0:
                sec = ts / 1000.0 if ts > 1e11 else float(ts)
                when = datetime.fromtimestamp(sec).strftime("%Y-%m-%d %H:%M:%S")
            elif isinstance(ts, str) and ts:
                when = ts[:19].replace("T", " ")
            out.append({"subject": subject, "from": (it.get("from") or "").strip("<>"),
                        "date": when, "body": body,
                        "html": raw if "<" in raw else "",
                        "code": pick_code_mail(subject, body),
                        "link": pick_link(body)})
        return out

    @staticmethod
    def delete(token, address=""):
        return False


class TempMailPlus:
    """tempmail.plus —— 不限量、前缀随便写（实测 zhangsan@mailto.plus 直接可用），
    但 9 个域名（mailto.plus / fexpost.com / fexbox.org / mailbox.in.ua / rover.info /
    chitthi.in / fextemp.com / any.pink / merepost.com）全在一次性黑名单里。
    当 temp-mail.io 也挂了时的替补。
    """
    name = "tempmail.plus"
    base = "https://tempmail.plus/api"
    can_prefix = True
    can_domain = True
    note = "不限量、前缀随意；域名全在黑名单"
    blurb = "前缀照填，同前缀可能共用信箱"
    DOMAINS = ["mailto.plus", "fexpost.com", "fexbox.org", "mailbox.in.ua",
               "rover.info", "chitthi.in", "fextemp.com", "any.pink", "merepost.com"]

    @staticmethod
    def domains():
        return list(TempMailPlus.DOMAINS)

    @staticmethod
    def create(prefix="", domain=""):
        pre = (prefix or "").strip().lower()
        if not pre:
            pre = human_local()
        dom = domain if domain in TempMailPlus.DOMAINS else random.choice(TempMailPlus.DOMAINS)
        addr = "%s@%s" % (pre, dom)
        st, res = http(TempMailPlus.base + "/mails?email=" + quote(addr, safe="@") + "&limit=1")
        if st != 200 or not isinstance(res, dict) or not res.get("result"):
            raise RuntimeError("tempmail.plus 用不了这个前缀：%s" % str(res)[:140])
        return {"address": addr, "token": "", "password": "", "backend": TempMailPlus.name}

    @staticmethod
    def messages(token, address=""):
        if not address:
            return []
        st, res = http(TempMailPlus.base + "/mails?email=" + quote(address, safe="@") + "&limit=20")
        if st != 200 or not isinstance(res, dict):
            return []
        out = []
        for it in (res.get("mail_list") or [])[:20]:
            mid = it.get("mail_id")
            body, html = "", ""
            st2, det = http(TempMailPlus.base + "/mails/%s?email=%s" % (mid, quote(address, safe="@")))
            if st2 == 200 and isinstance(det, dict):
                body = det.get("text") or ""
                html = det.get("html") or ""
                subject = _subject_text(det.get("subject") or it.get("subject")) or "(无主题)"
                sender = det.get("from_mail") or it.get("from_mail") or ""
                when = det.get("date") or it.get("time") or ""
            else:
                subject = _subject_text(it.get("subject")) or "(无主题)"
                sender = it.get("from_mail") or ""
                when = it.get("time") or ""
            if html:
                links = re.findall(r"""(?:href|src)\s*=\s*["']([^"']+)["']""", html, flags=re.I)
                if links:
                    keep = [u for u in links if u.startswith("http")][:5]
                    if keep:
                        body = (body or re.sub(r"<[^>]+>", " ", html)).rstrip() + "\n" + "\n".join(keep)
            out.append({"subject": subject, "from": sender, "date": when,
                        "body": (body or "").strip(),
                        "code": pick_code_mail(subject, body),
                        "link": pick_link(body)})
        return out

    @staticmethod
    def delete(token, address=""):
        return False


class MailboxGone(Exception):
    """后端明确说「这个信箱没了」。和网络抖动区分开，好把地址标成失效、不再白扫。"""


class ProviderBusy(Exception):
    """后端在限流：不是没信，是这会儿没让查。前端要把它显示出来，别装作空收件箱。"""


# 顺序 = 默认尝试顺序。io 最稳、能控前缀，排第一并作为默认推荐；lol 域名干净但很快失效；
# plus 兜底。
TEMP_BACKENDS = {
    "temp-mail.io": TempMailIO,
    "tempmail.lol": TempMailLol,
    "tempmail.plus": TempMailPlus,
}

# 自有域名：Cloudflare Email Routing 把 *@agvuph.cn 全量转发到自己的 Gmail，
# 项目再读 Gmail 的 IMAP，按原始收件人（To / X-Forwarded-To / X-Original-To）
# 还原成一个个独立地址。创建时不需要发任何网络请求，只是登记前缀。
OWN_DOMAIN = (os.environ.get("OWN_DOMAIN") or "agvuph.cn").strip().lower().lstrip("@")
OWN_BACKEND = "owndomain"

# ---- 访客隔离 ----------------------------------------------------------
# 每个浏览器一个随机 sid，地址入库时记下属于哪个 sid；临时邮箱读取按 sid 过滤。
# 「我的邮箱」不再需要口令，对所有访客开放。
SID_COOKIE = "xsid"


PASS_SYMBOLS = "!@#$%^&*-_=+?"
_PASS_RNG = random.SystemRandom()


def gen_password(n=16):
    """给新邮箱生成注册用的强密码：大小写、数字、符号都有。"""
    pools = [string.ascii_lowercase, string.ascii_uppercase, string.digits, PASS_SYMBOLS]
    chars = [_PASS_RNG.choice(p) for p in pools]
    allc = "".join(pools)
    chars += [_PASS_RNG.choice(allc) for _ in range(max(0, n - len(chars)))]
    _PASS_RNG.shuffle(chars)
    return "".join(chars)


# ---------------------------------------------------------------- 账号 / 地址操作
def list_accounts():
    con = db()
    out = rows(con.execute(
        "SELECT id,email,provider,imap_host,imap_port,imap_user,note,created,last_scan,last_error"
        " FROM account ORDER BY id DESC"))
    for a in out:
        a["has_pass"] = bool(con.execute(
            "SELECT imap_pass FROM account WHERE id=?", (a["id"],)).fetchone()[0])
        a["addr_count"] = con.execute(
            "SELECT COUNT(*) FROM addr WHERE account_id=?", (a["id"],)).fetchone()[0]
        a["code_count"] = con.execute(
            "SELECT COUNT(*) FROM addr WHERE account_id=? AND seen!=''", (a["id"],)).fetchone()[0]
    con.close()
    return out


def add_account(email_addr, provider, pwd, host="", port=993, note=""):
    email_addr = (email_addr or "").strip()
    if "@" not in email_addr:
        raise RuntimeError("邮箱格式不对")
    provider = (provider or "custom").strip().lower()
    if provider not in PROVIDERS:
        provider = "custom"
    preset_host, preset_port, _ = PROVIDERS[provider]
    host = (host or "").strip() or preset_host
    con = db()
    try:
        con.execute(
            "INSERT INTO account(email,provider,imap_host,imap_port,imap_user,imap_pass,note,created)"
            " VALUES(?,?,?,?,?,?,?,?)",
            (email_addr, provider, host, int(port or preset_port), email_addr, pwd or "", note or "", now()))
    except sqlite3.IntegrityError:
        con.execute(
            "UPDATE account SET provider=?, imap_host=?, imap_port=?, imap_pass=?, note=? WHERE email=?",
            (provider, host, int(port or preset_port), pwd or "", note or "", email_addr))
    con.commit()
    aid = con.execute("SELECT id FROM account WHERE email=?", (email_addr,)).fetchone()[0]
    con.execute("INSERT OR IGNORE INTO addr(address,account_id,kind,backend,created) VALUES(?,?,?,?,?)",
                (email_addr, aid, "main", "imap", now()))
    con.commit()
    con.close()
    return aid


def remove_account(aid):
    con = db()
    con.execute("DELETE FROM addr WHERE account_id=?", (aid,))
    con.execute("DELETE FROM msg WHERE account_id=?", (aid,))
    con.execute("DELETE FROM account WHERE id=?", (aid,))
    con.commit()
    con.close()
    return True


def provider_list():
    """给页面用的服务商预设。pass 一律是授权码 / 应用专用密码，不是登录密码。"""
    out = []
    for k, v in PROVIDERS.items():
        out.append({"key": k, "host": v[0], "port": v[1], "note": v[2]})
    return out


def acc_get(aid):
    con = db()
    r = rows(con.execute(
        "SELECT id,email,provider,imap_host,imap_port,imap_user,imap_pass,last_uid"
        " FROM account WHERE id=?", (aid,)))
    con.close()
    return r[0] if r else None


ERR_HINTS = [
    ("AUTHENTICATIONFAILED", "授权码不对"),
    ("LOGIN fail", "登录被拒"),
    ("Login fail", "登录被拒"),
    ("authentication failed", "授权码不对"),
    ("Invalid credentials", "授权码不对"),
    ("Application-specific password required", "要用应用专用密码，不是登录密码"),
    ("IMAP access is disabled", "邮箱里没开 IMAP"),
    ("[ALERT] Please log in via your web browser", "先去网页版登录一次解锁"),
    ("timed out", "连不上服务器，超时"),
    ("getaddrinfo", "服务器地址解析不了"),
    ("Connection refused", "服务器拒绝连接"),
    ("certificate", "证书校验失败"),
    ("EOF occurred", "连接被对端掐断"),
    ("Too many", "试太频繁了，等几分钟"),
]


def _clean_err(e):
    """IMAP 库抛的错常常是 b'...' 这种字节串，而且一长串英文。
    剥掉字节壳，认出常见的几种，翻成一句人话。"""
    s = str(e)
    if len(s) > 3 and s[0] == "b" and s[1] in "'\"":
        q = s[1]
        body = s[2:-1] if s.endswith(q) else s[2:]
        try:
            body = body.encode("latin-1", "ignore").decode("unicode_escape", "ignore")
        except Exception:
            pass
        s = body
    s = s.replace("\\r", " ").replace("\\n", " ").strip()
    low = s.lower()
    for k, v in ERR_HINTS:
        if k.lower() in low:
            return v
    if not s:
        s = e.__class__.__name__
    return s[:110]


def acc_test(aid):
    """连一次 IMAP，确认授权码对不对。结果写回 last_scan / last_error，页面直接看得见。"""
    acc = acc_get(aid)
    if not acc:
        return False, "没有这个账号"
    try:
        m = imap_connect(acc)
    except Exception as e:
        msg = _clean_err(e)[:180]
        con = db()
        con.execute("UPDATE account SET last_scan=?, last_error=? WHERE id=?", (now(), msg, aid))
        con.commit()
        con.close()
        return False, msg
    n = 0
    try:
        m.select("INBOX", readonly=True)
        typ, data = m.uid("search", None, "ALL")
        if typ == "OK":
            n = len((data[0] or b"").split())
    except Exception:
        pass
    finally:
        try:
            m.logout()
        except Exception:
            pass
    con = db()
    con.execute("UPDATE account SET last_scan=?, last_error='' WHERE id=?", (now(), aid))
    con.commit()
    con.close()
    return True, "连上了，收件箱 %d 封" % n


def acc_add(email_addr, provider, pwd, host="", port=0):
    """加一个自己的邮箱。加完立刻试连一次，错了马上告诉页面错在哪。"""
    email_addr = (email_addr or "").strip()
    if "@" not in email_addr:
        raise RuntimeError("邮箱格式不对")
    pwd = (pwd or "").strip()
    if not pwd:
        raise RuntimeError("要填授权码（不是登录密码）")
    con = db()
    existed = con.execute("SELECT 1 FROM account WHERE email=?", (email_addr,)).fetchone() is not None
    con.close()
    aid = add_account(email_addr, provider, pwd, host, port or 993)
    # 重新添加 = 可能换了授权码。last_uid 置 -1：下次扫描先取当前最大 UID 当基线，
    # 只收这之后的新信，不把整个收件箱倒进来。
    con = db()
    con.execute("UPDATE account SET last_uid=-1 WHERE id=?", (aid,))
    con.commit()
    con.close()
    ok, msg = acc_test(aid)
    if ok:
        threading.Thread(target=_scan_one_quiet, args=(aid,), daemon=True).start()
    elif not existed:
        # 新号连不上就别留一条死记录；老号只更新授权码失败时保留，别把用户原来的号删掉
        con = db()
        con.execute("DELETE FROM account WHERE id=?", (aid,))
        con.execute("DELETE FROM addr WHERE account_id=?", (aid,))
        con.commit()
        con.close()
    return {"id": aid, "ok": ok, "msg": msg}


def _scan_one_quiet(aid):
    """加完账号后台补一次收信，不然要等下一轮定时扫描才看得到码。"""
    try:
        acc = acc_get(aid)
        if not acc:
            return
        con = db()
        known = [r[0] for r in con.execute(
            "SELECT address FROM addr WHERE account_id=?", (aid,))]
        con.close()
        imap_scan(acc, known or [acc["email"]])
    except Exception:
        pass


def repair_account_addrs():
    """每个账号至少要有自己那条主地址。以前清列表会把它一起删掉，
    结果账号还在、地址没了，收到码也没地方显示。启动时补一次。"""
    con = db()
    accs = rows(con.execute("SELECT id,email FROM account"))
    for a in accs:
        con.execute(
            "INSERT OR IGNORE INTO addr(address,account_id,kind,backend,created) VALUES(?,?,?,?,?)",
            (a["email"], a["id"], "main", "imap", now()))
    con.commit()
    con.close()


def acc_public():
    out = []
    for a in list_accounts():
        a = dict(a)
        a["hint"] = PROVIDERS.get(a.get("provider") or "custom", PROVIDERS["custom"])[2]
        out.append(a)
    return out


def list_addrs(owner_sid=""):
    con = db()
    sql = ("SELECT a.address,a.account_id,a.kind,a.backend,a.tag,a.created,a.seen,a.seen_at,a.seen_subject,"
           " a.dead,a.dead_note,a.password,a.owner_sid,"
           " (SELECT email FROM account WHERE id=a.account_id) AS owner"
           " FROM addr a WHERE a.owner_sid=?"
           " ORDER BY a.seen_at DESC, a.created DESC")
    out = rows(con.execute(sql, (owner_sid,)))
    con.close()
    bl = load_blocklist()
    for a in out:
        dom = (a.get("address") or "").split("@")[-1].lower()
        a["domain"] = dom
        a["blocked"] = dom in bl
        a["own"] = a.get("kind") == "main"
    return out


def add_addrs(account_id, items, kind, tag=""):
    con = db()
    made = []
    for a in items:
        a = (a or "").strip()
        if "@" not in a:
            continue
        try:
            con.execute(
                "INSERT OR IGNORE INTO addr(address,account_id,kind,backend,tag,created) VALUES(?,?,?,?,?,?)",
                (a, account_id, kind, "imap", tag or "", now()))
            if con.total_changes:
                made.append(a)
        except Exception:
            pass
    con.commit()
    con.close()
    return made


def remove_addr(address, sid=""):
    con = db()
    row = con.execute("SELECT owner_sid,kind FROM addr WHERE address=?", (address,)).fetchone()
    if not row:
        con.close()
        return False
    if row[0] != sid:
        con.close()
        return False
    con.execute("DELETE FROM addr WHERE address=?", (address,))
    con.execute("DELETE FROM msg WHERE address=?", (address,))
    con.commit()
    con.close()
    return True


def _excerpt(text, limit=96):
    """列表里的一行正文摘要：压掉空白、截断。"""
    s = re.sub(r"\s+", " ", str(text or "")).strip()
    return s[:limit]


def main_codes(limit=8):
    """主页要直接展示「我的邮箱」里最近的验证码，不用点进二级页。"""
    con = db()
    out = rows(con.execute(
        "SELECT m.id,m.address,m.subject,m.sender,m.mdate,m.code,"
        "       substr(m.body,1,180) AS excerpt"
        " FROM msg m JOIN account a ON a.email=m.address"
        " WHERE m.code IS NOT NULL AND m.code<>''"
        " ORDER BY m.rowid DESC LIMIT ?", (int(limit),)))
    con.close()
    for m in out:
        m["from"] = m.get("sender", "")
        m["date"] = m.get("mdate", "")
        m["excerpt"] = _excerpt(m.get("excerpt"), 84)
    return out


def latest_codes(limit=30):
    """给桌面通知客户端用：最近带验证码的邮件，按入库顺序倒排。
    客户端按 id 去重，只弹新到的。"""
    con = db()
    out = rows(con.execute(
        "SELECT id,address,sender,subject,code,mdate,fetched FROM msg"
        " WHERE code IS NOT NULL AND code<>'' ORDER BY rowid DESC LIMIT ?", (int(limit),)))
    con.close()
    return {"messages": out}


def read_msgs(address, light=False, local=False, sid=""):
    """light=True 时不带正文，给前端轮询列表用；正文点开再单独取。
    local=True 只读本地库、不碰供应商接口，用来切历史地址时先把已收邮件铺出来。
    临时邮箱只能读自己会话建过的地址；「我的邮箱」里的账号信件公共可见。"""
    con = db()
    a = con.execute("SELECT address,backend,token,account_id,kind,owner_sid FROM addr WHERE address=?",
                    (address,)).fetchone()
    if not a:
        con.close()
        return {"ok": False, "error": "没有这个地址"}
    address, backend, token, aid, kind, owner_sid = a
    if kind == "temp" and owner_sid != sid:
        con.close()
        return {"ok": False, "error": "没有这个地址"}
    if backend in TEMP_BACKENDS and not local:
        dead = False
        try:
            live = TEMP_BACKENDS[backend].messages(token or "", address)
        except MailboxGone as e:
            # 后端明确说没了：标死，以后不再扫这个地址
            live, note, dead = [], str(e)[:160], True
        except Exception as e:
            live = []
            note = str(e)[:160]
        else:
            note = ""
        if dead:
            con.execute("UPDATE addr SET dead=1, dead_note=? WHERE address=?", (note, address))
            con.commit()
        # tempmail.lol 的收件箱是「读一次就清空」的：前端每 5 秒轮询一次，
        # 只要不落库，第一眼看到的那封信下一秒就没了。所以读到就先存下来。
        for mm in live:
            mid = hashlib.sha1(("%s|%s|%s" % (address, mm.get("subject", ""),
                                              mm.get("date", ""))).encode("utf-8")).hexdigest()[:20]
            mm["id"] = mid
            con.execute("INSERT OR IGNORE INTO msg(id,address,account_id,subject,sender,mdate,body,body_html,code,link,fetched)"
                        " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                        (mid, address, None, _subject_text(mm.get("subject", "")), mm.get("from", ""),
                         str(mm.get("date", "")), mm.get("body", ""), mm.get("html", ""),
                         mm.get("code", ""), mm.get("link", ""), now()))
        if live:
            con.commit()
        cols = ("id,subject,sender,mdate,substr(body,1,220) AS excerpt,code,link" if light
                else "id,subject,sender,mdate,body,code,link")
        msgs = rows(con.execute(
            "SELECT %s FROM msg WHERE address=? ORDER BY rowid DESC LIMIT 40" % cols,
            (address,)))
        con.close()
        for m in msgs:
            m["from"] = m.get("sender", "")
            m["date"] = m.get("mdate", "")
            if light:
                m["excerpt"] = _excerpt(m.get("excerpt"))
        codes = [m["code"] for m in msgs if m.get("code")]
        return {"ok": True, "address": address, "messages": msgs, "codes": codes,
                "latest_code": codes[0] if codes else "", "note": note, "dead": dead}
    # 按入库顺序倒排：mdate 是邮件头里的字符串，经常是空的或格式不一，靠它排会拿错「最新」
    cols = ("id,address,subject,sender,mdate,substr(body,1,220) AS excerpt,code,link" if light
            else "id,address,subject,sender,mdate,body,code,link")
    msgs = rows(con.execute(
        "SELECT %s FROM msg WHERE address=? ORDER BY rowid DESC LIMIT 40" % cols,
        (address,)))
    con.close()
    for m in msgs:
        m["from"] = m.get("sender", "")
        m["date"] = m.get("mdate", "")
        if light:
            m["excerpt"] = _excerpt(m.get("excerpt"))
    codes = [m["code"] for m in msgs if m.get("code")]
    return {"ok": True, "address": address, "messages": msgs,
            "codes": codes, "latest_code": codes[0] if codes else ""}


def get_msg(mid, sid=""):
    con = db()
    hit = rows(con.execute(
        "SELECT m.id,m.address,m.subject,m.sender,m.mdate,m.body,m.body_html,m.code,m.link,m.fetched,"
        " a.owner_sid,a.kind FROM msg m LEFT JOIN addr a ON a.address=m.address WHERE m.id=?",
        (mid,)))
    con.close()
    if not hit:
        return {"ok": False, "error": "没有这封邮件"}
    row = hit[0]
    if row.get("kind") == "temp" and row.get("owner_sid") != sid:
        return {"ok": False, "error": "没有这封邮件"}
    row.pop("owner_sid", None)
    row.pop("kind", None)
    row["html"] = clean_email_html(row.pop("body_html", "") or "")
    return {"ok": True, "message": row}


_DOM_CACHE = {}
_DOM_LOCK = threading.Lock()


def backend_domains(name, force=False):
    """后端支持的域名池。缓存 10 分钟，别每开一次弹窗就去问一次接口。"""
    cls = TEMP_BACKENDS.get(name)
    if not cls:
        return []
    with _DOM_LOCK:
        hit = _DOM_CACHE.get(name)
        if hit and not force and time.time() - hit[0] < 600:
            return hit[1]
    try:
        doms = cls.domains() or []
    except Exception:
        doms = []
    with _DOM_LOCK:
        _DOM_CACHE[name] = (time.time(), doms)
    return doms


def proxy_state():
    """代理当前状态。used_by 说明哪些后端靠它吃饭。"""
    px = get_proxy()
    return {"proxy": px, "on": bool(px), "env": bool(os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy") or os.environ.get("ALL_PROXY") or os.environ.get("all_proxy")),
            "hint": "留空 = 直连。tempmail.lol 会按出口 IP 封锁中国大陆，需要填代理才能用。"}


def proxy_test():
    """走当前代理问一下出口 IP，确认代理真能用。
    用 cloudflare 的 trace 端点：纯文本、没有多余请求，代理也不会掐它。"""
    px = get_proxy()
    st, res = http("https://www.cloudflare.com/cdn-cgi/trace", timeout=15)
    if st != 200 or not isinstance(res, str):
        return {"ok": False, "proxy": px, "error": str(res)[:200]}
    kv = {}
    for line in res.splitlines():
        if "=" in line:
            k, _, v = line.partition("=")
            kv[k.strip()] = v.strip()
    ip = kv.get("ip", "")
    if not ip:
        return {"ok": False, "proxy": px, "error": "代理通了，但没拿到出口 IP"}
    return {"ok": True, "proxy": px, "ip": ip, "loc": kv.get("loc", ""),
            "warp": kv.get("warp", "")}


def temp_info():
    """给前端用的后端清单：能不能定前缀、能不能定域名、域名黑不黑。"""
    bl = load_blocklist()
    out = []
    for name, cls in TEMP_BACKENDS.items():
        doms = backend_domains(name)
        out.append({
            "key": name,
            "name": name,
            "can_prefix": bool(getattr(cls, "can_prefix", False)),
            "can_domain": bool(getattr(cls, "can_domain", False)) and bool(doms),
            "note": getattr(cls, "note", ""),
            "blurb": getattr(cls, "blurb", ""),
            "recommend": bool(getattr(cls, "recommend", False)),
            "domain_count": len(doms),
            "domains": [{"domain": d, "blocked": d.lower() in bl} for d in doms],
        })
    if OWN_DOMAIN:
        out.append({
            "key": OWN_BACKEND,
            "name": OWN_DOMAIN,
            "can_prefix": True,
            "can_domain": False,
            "note": "自己的域名：Cloudflare Email Routing 把该域全量转发到 Gmail，"
                    "本项目读 Gmail 的 IMAP，按原始收件人还原地址",
            "blurb": "自己的域名，转发到 Gmail",
            "recommend": False,
            "domain_count": 1,
            "domains": [{"domain": OWN_DOMAIN, "blocked": False}],
            "own": True,
        })
    return out


def _temp_prefix(base, i, n):
    base = (base or "").strip().lower()
    base = re.sub(r"[^a-z0-9._-]", "", base)
    if not base:
        return human_local()
    if n > 1:
        return "%s%02d" % (base, i + 1)
    return base


def _temp_spread_pick(seq, bl):
    """第 seq 个临时邮箱该用哪家、哪个域名。
    后端轮换 + 域名错开，保证连续生成的后缀不一样；优先挑没进黑名单的域名。"""
    order = list(TEMP_BACKENDS)
    if not order:
        return None, ""
    name = order[seq % len(order)]
    doms = []
    if getattr(TEMP_BACKENDS[name], "can_domain", False):
        try:
            doms = backend_domains(name) or []
        except Exception:
            doms = []
    clean = [d for d in doms if d.lower() not in bl] or doms
    dom = clean[(seq // len(order)) % len(clean)] if clean else ""
    return name, dom


OWN_PREFIX_RE = re.compile(r"[a-z0-9](?:[a-z0-9._-]{0,22}[a-z0-9])?")


def make_own_addrs(n, prefix, tag="", owner_sid=""):
    """自有域名地址：只登记，不发任何网络请求。前缀留空就随机。"""
    base = (prefix or "").strip().lower()
    if base and not OWN_PREFIX_RE.fullmatch(base):
        return [], ["前缀只能用字母、数字和 . _ -，1–24 位，不能以符号开头或结尾"]
    made = []
    con = db()
    try:
        for i in range(n):
            pre = base or human_local()
            if base and n > 1:
                pre = "%s%02d" % (base, i + 1)
            addr = "%s@%s" % (pre, OWN_DOMAIN)
            old = con.execute("SELECT password,owner_sid FROM addr WHERE address=?", (addr,)).fetchone()
            pw = (old[0] if old and old[0] else "") or gen_password()
            owner = (old[1] if old else None) or owner_sid
            con.execute(
                "INSERT OR IGNORE INTO addr(address,account_id,kind,backend,token,tag,created,seen,password,owner_sid)"
                " VALUES(?,?,?,?,?,?,?,?,?,?)",
                (addr, None, "temp", OWN_BACKEND, "", tag or "", now(), "", pw, owner))
            con.execute("UPDATE addr SET dead=0, dead_note=?, password=?, owner_sid=? WHERE address=?",
                        ("", pw, owner, addr))
            made.append({"address": addr, "backend": OWN_BACKEND,
                         "domain": OWN_DOMAIN, "blocked": False, "password": pw})
        con.commit()
    finally:
        con.close()
    return made, []


def make_temp(n, prefix, tag, backend="", domain="", owner_sid=""):
    """造临时邮箱。backend 留空 = 多家供应商轮着来，域名也跟着换。
    返回 (made, errors)。made 里带 blocked 标记，前端可以直接标红。"""
    made, errors = [], []
    try:
        n = int(n)
    except Exception:
        n = 1
    n = max(1, min(n, 20))
    if backend == OWN_BACKEND:
        return make_own_addrs(n, prefix, tag, owner_sid)
    bl = load_blocklist()
    con = db()
    seq0 = con.execute("SELECT COUNT(*) FROM addr WHERE kind='temp'").fetchone()[0]
    con.close()
    for i in range(n):
        pre = _temp_prefix(prefix, i, n)
        if backend in TEMP_BACKENDS:
            order = [(backend, domain)]
        else:
            name, dom = _temp_spread_pick(seq0 + i, bl)
            order = [(name, dom)] + [(x, "") for x in TEMP_BACKENDS if x != name]
        hit = None
        for name, dom in order:
            cls = TEMP_BACKENDS[name]
            # 这个后端能不能吃下我要的前缀 / 域名？吃不下就别浪费一次额度。
            if prefix and not getattr(cls, "can_prefix", False):
                errors.append("%s 不支持指定前缀，跳过" % name)
                continue
            dom = dom if getattr(cls, "can_domain", False) else ""
            if dom:
                pool = [d.lower() for d in backend_domains(name)]
                if pool and dom.lower() not in pool:
                    errors.append("%s 没有 %s 这个域名" % (name, dom))
                    continue
            try:
                info = cls.create(pre if getattr(cls, "can_prefix", False) else "", dom)
            except Exception as e:
                # 后端突然改域名规则时，退回“让服务端自己分域名”再试一次
                if dom and "invalid domain" in str(e).lower():
                    try:
                        info = cls.create(pre if getattr(cls, "can_prefix", False) else "", "")
                    except Exception as e2:
                        errors.append("%s: %s" % (name, _clean_err(e2)[:140]))
                        continue
                else:
                    errors.append("%s: %s" % (name, _clean_err(e)[:140]))
                    continue
            hit = info
            break
        if not hit:
            errors.append("前缀 %s 所有后端都失败" % pre)
            continue
        addr = hit["address"]
        dom = addr.split("@")[-1].lower()
        con = db()
        old = con.execute("SELECT password,owner_sid FROM addr WHERE address=?", (addr,)).fetchone()
        pw = (old[0] if old and old[0] else "") or gen_password()
        owner = (old[1] if old else None) or owner_sid
        con.execute("INSERT OR REPLACE INTO addr(address,account_id,kind,backend,token,password,tag,created,seen,owner_sid)"
                    " VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (addr, None, "temp", hit["backend"], hit["token"],
                     pw, tag or "", now(), "", owner))
        con.commit()
        con.close()
        made.append({"address": addr, "backend": hit["backend"],
                     "domain": dom, "blocked": dom in bl, "password": pw})
        if i + 1 < n:
            time.sleep(1.2)
    return made, errors


# ---------------------------------------------------------------- 后台扫描
_SCAN_LOCK = threading.Lock()
_SCAN_STATE = {"running": False, "last": "", "found": 0, "error": ""}


_IMAP_LAST = [0.0]
_IMAP_RUN_LOCK = threading.Lock()

# 每个账号各自的退避：连不上就 20s → 40s → 80s … 最多 10 分钟，
# 免得密码错了还每 8 秒去撞一次，把服务商惹到限流。
_ACC_BACKOFF = {}
_ACC_LOCK = threading.Lock()

# QQ 用 IMAP IDLE 实时推：这些账号由 idle 线程盯着，轮询就不必再连它。
_IDLE_ACTIVE = set()
_IDLE_LOCK = threading.Lock()
_IDLE_STARTED = set()
_IDLE_STATE = {}
_IDLE_SUPERVISOR = None


def _idle_mark(aid, on):
    with _IDLE_LOCK:
        if on:
            _IDLE_ACTIVE.add(aid)
        else:
            _IDLE_ACTIVE.discard(aid)


def _idle_is_active(aid):
    with _IDLE_LOCK:
        return aid in _IDLE_ACTIVE


def _acc_ready(aid):
    with _ACC_LOCK:
        nxt, _ = _ACC_BACKOFF.get(aid, (0.0, 0))
    return time.time() >= nxt


def _acc_ok(aid):
    with _ACC_LOCK:
        _ACC_BACKOFF.pop(aid, None)


def _acc_fail(aid):
    with _ACC_LOCK:
        fails = _ACC_BACKOFF.get(aid, (0.0, 0))[1] + 1
        wait = min(600, 20 * (2 ** (fails - 1)))
        _ACC_BACKOFF[aid] = (time.time() + wait, fails)


def _known_addrs(aid, email):
    con = db()
    ks = [r[0] for r in con.execute("SELECT address FROM addr WHERE account_id=?", (aid,))]
    con.close()
    return ks or [email]


def _scan_one(acc):
    try:
        n, err = imap_scan(acc, _known_addrs(acc["id"], acc["email"]), timeout=7)
        if err:
            _acc_fail(acc["id"])
        else:
            _acc_ok(acc["id"])
        return n, err
    except Exception as e:
        _acc_fail(acc["id"])
        return 0, str(e)[:120]


def scan_accounts_now(wait=0, force=False):
    """扫所有自己的 IMAP 邮箱。后台和手动共用，同一时刻只跑一份，免得重复登录、抢写锁。
    QQ 并发扫、最多等 12 秒；Gmail 只丢到后台，不挡整轮。"""
    acquired = _IMAP_RUN_LOCK.acquire(timeout=wait) if wait else _IMAP_RUN_LOCK.acquire(blocking=False)
    if not acquired:
        return {"ok": False, "found": 0, "errors": ["正在收信，稍等"]}
    got, errs = 0, []
    try:
        con = db()
        accs = rows(con.execute(
            "SELECT id,email,provider,imap_host,imap_port,imap_user,imap_pass,last_uid FROM account"))
        con.close()
        fast, slow = [], []
        for acc in accs:
            # IDLE 已经盯着的账号，轮询不再重复连
            if _idle_is_active(acc["id"]):
                continue
            if not force and not _acc_ready(acc["id"]):
                continue
            if acc.get("provider") in ("qq", "foxmail"):
                fast.append(acc)
            else:
                slow.append(acc)
        if fast:
            ex = ThreadPoolExecutor(max_workers=min(4, len(fast)))
            futs = {ex.submit(_scan_one, a): a for a in fast}
            try:
                for f in as_completed(futs, timeout=12):
                    n, err = f.result()
                    got += n
                    if err:
                        errs.append("%s：%s" % (futs[f]["email"], err))
            except Exception:
                errs.append("QQ 本轮超时，下一轮继续")
            finally:
                ex.shutdown(wait=False)
        # Gmail / 其他链路慢，扔后台；这一轮的返回值不等它
        for acc in slow:
            threading.Thread(target=_scan_one, args=(acc,), daemon=True).start()
        return {"ok": True, "found": got, "errors": errs}
    finally:
        _IMAP_RUN_LOCK.release()


def _idle_loop(aid, stop_evt):
    """QQ IMAP IDLE：服务器一喊 EXISTS，就立刻拉增量。断了自动重连。"""
    fails = 0
    while not stop_evt.is_set():
        con = db()
        row = con.execute(
            "SELECT id,email,provider,imap_host,imap_port,imap_user,imap_pass,last_uid"
            " FROM account WHERE id=?", (aid,)).fetchone()
        con.close()
        if not row:
            break
        cols = ("id", "email", "provider", "imap_host", "imap_port", "imap_user", "imap_pass", "last_uid")
        acc = dict(zip(cols, row))
        m = None
        try:
            m = imap_connect(acc, timeout=35)
            m.select("INBOX", readonly=True)
            _idle_mark(aid, True)
            _acc_ok(aid)
            fails = 0
            _IDLE_STATE[aid] = {"at": now(), "ok": True, "event": "connected"}
            # 连上先补一次，IDLE 期间漏掉的信也在这里找回来
            _imap_scan_conn(m, acc, _known_addrs(aid, acc["email"]))
            while not stop_evt.is_set():
                # 上一轮可能查过 Junk，进 IDLE 前切回收件箱
                m.select("INBOX", readonly=True)
                tag = m._new_tag()
                m.send(tag + b" IDLE\r\n")
                while True:
                    line = m.readline()
                    if not line:
                        raise IOError("IMAP 连接断了")
                    if line.startswith(b"+"):
                        break
                changed = False
                m.sock.settimeout(6)
                try:
                    while True:
                        line = m.readline()
                        if not line:
                            raise IOError("IMAP 连接断了")
                        up = line.upper()
                        if b"EXISTS" in up or b"RECENT" in up:
                            changed = True
                            break
                except socket.timeout:
                    pass
                finally:
                    try:
                        m.sock.settimeout(35)
                    except Exception:
                        pass
                m.send(b"DONE\r\n")
                while True:
                    line = m.readline()
                    if not line:
                        raise IOError("IMAP 连接断了")
                    if line.startswith(tag.strip()):
                        break
                if changed:
                    _IDLE_STATE[aid] = {"at": now(), "ok": True, "event": "exists"}
                    con = db()
                    row = con.execute(
                        "SELECT id,email,provider,imap_host,imap_port,imap_user,imap_pass,last_uid"
                        " FROM account WHERE id=?", (aid,)).fetchone()
                    con.close()
                    if not row:
                        break
                    acc = dict(zip(cols, row))
                    _imap_scan_conn(m, acc, _known_addrs(aid, acc["email"]))
                # Junk 不会推 IDLE：每一轮顺手查一次，转发信常被 QQ 丢这里
                try:
                    con = db()
                    row = con.execute(
                        "SELECT id,email,provider,imap_host,imap_port,imap_user,imap_pass,last_uid"
                        " FROM account WHERE id=?", (aid,)).fetchone()
                    con.close()
                    if row:
                        acc = dict(zip(cols, row))
                        _imap_scan_conn(m, acc, _known_addrs(aid, acc["email"]), 40, "Junk", True)
                except Exception:
                    pass
        except Exception:
            _idle_mark(aid, False)
            _acc_fail(aid)
            fails += 1
            _IDLE_STATE[aid] = {"at": now(), "ok": False, "error": str(sys.exc_info()[1])[:140]}
            if fails > 5:
                break
            stop_evt.wait(min(60, 5 * (2 ** (fails - 1))))
        finally:
            if m is not None:
                try:
                    m.logout()
                except Exception:
                    pass
    _idle_mark(aid, False)
    with _IDLE_LOCK:
        _IDLE_STARTED.discard(aid)


def idle_supervisor(stop_evt):
    """盯账号表：QQ / foxmail 每个账号起一条 IDLE 线程，删号后线程自己退。"""
    while not stop_evt.is_set():
        try:
            con = db()
            ids = [r[0] for r in con.execute(
                "SELECT id FROM account WHERE provider IN ('qq','foxmail')")]
            con.close()
            for aid in ids:
                with _IDLE_LOCK:
                    if aid in _IDLE_STARTED:
                        continue
                    _IDLE_STARTED.add(aid)
                threading.Thread(target=_idle_loop, args=(aid, stop_evt), daemon=True).start()
        except Exception:
            pass
        stop_evt.wait(30)


def scan_imap(force=False):
    """扫所有 IMAP 账号。返回新抓到的邮件数。
    后台每 5 秒跑一轮，IMAP 默认 8 秒最多连一次；失败按账号退避。"""
    gap = float(os.environ.get("IMAP_GAP") or 5)
    if not force and time.time() - _IMAP_LAST[0] < max(4.0, gap):
        return 0
    _IMAP_LAST[0] = time.time()
    return scan_accounts_now(force=force).get("found", 0)


def scan_all(quiet=True, force=False):
    if not _SCAN_LOCK.acquire(blocking=False):
        return {"ok": False, "error": "正在扫，稍等"}
    _SCAN_STATE["running"] = True
    total, errs = 0, []
    out = {}

    def run_temps():
        try:
            out["temp"] = scan_temps()
        except Exception as e:
            out["terr"] = "temp: %s" % str(e)[:100]

    def run_imap():
        try:
            out["imap"] = scan_imap(force)
        except Exception as e:
            out["ierr"] = "imap: %s" % str(e)[:100]

    try:
        # 临时邮箱和 IMAP 两条线并发跑：临时邮箱要挨个问供应商，串行会把 IMAP 拖到几十秒
        t1 = threading.Thread(target=run_temps, daemon=True)
        t2 = threading.Thread(target=run_imap, daemon=True)
        t1.start()
        t2.start()
        t1.join()
        t2.join(20)
        total += int(out.get("temp") or 0) + int(out.get("imap") or 0)
        for k in ("terr", "ierr"):
            if out.get(k):
                errs.append(out[k])
        _SCAN_STATE.update({"last": now(), "found": total, "error": "; ".join(errs)[:300]})
        return {"ok": True, "found": total, "errors": errs}
    finally:
        _SCAN_STATE["running"] = False
        _SCAN_LOCK.release()


_TEMP_LAST = {}
_TEMP_LOCK = threading.Lock()


# 新造的号要盯紧：tempmail.lol 的信箱活不长，等你 60 秒扫一次，
# 信可能已经跟着信箱一起没了。所以按「刚造出来的头 20 分钟」加密轮询。
FRESH_WINDOW = 20 * 60
FRESH_GAP = 4.0
OLD_GAP = 30.0


def scan_temps(min_gap=None):
    """临时邮箱没法用 IMAP 推，只能挨个去后端问。带节流，别把 tempmail.lol 问爆。"""
    con = db()
    addrs = [(r[0], r[1], r[2], r[3]) for r in con.execute(
        "SELECT address, created, dead, backend FROM addr WHERE kind='temp'"
        " ORDER BY rowid DESC LIMIT 40")]
    con.close()
    got = 0
    for a, created, dead, backend in addrs:
        # 自有域名走 IMAP 收信，不用去供应商那里问
        if dead or backend == OWN_BACKEND:
            continue
        gap = OLD_GAP
        if created:
            try:
                born = datetime.strptime(created[:19], "%Y-%m-%d %H:%M:%S")
                if (datetime.now() - born).total_seconds() < FRESH_WINDOW:
                    gap = FRESH_GAP
            except Exception:
                pass
        if min_gap is not None:
            gap = min(gap, min_gap)
        with _TEMP_LOCK:
            last = _TEMP_LAST.get(a, 0)
        if time.time() - last < gap:
            continue
        with _TEMP_LOCK:
            _TEMP_LAST[a] = time.time()
        try:
            r = read_msgs(a)
        except Exception:
            continue
        if not r.get("ok"):
            continue
        msgs = r.get("messages") or []
        if not msgs:
            continue
        latest = r.get("latest_code") or ""
        subj = msgs[0].get("subject") or ""
        con = db()
        old = con.execute("SELECT seen FROM addr WHERE address=?", (a,)).fetchone()
        if latest and (not old or old[0] != latest):
            got += 1
        con.execute("UPDATE addr SET seen=?, seen_at=?, seen_subject=? WHERE address=?",
                    (latest or (old[0] if old else ""), now(), subj, a))
        con.commit()
        con.close()
    return got


def scan_worker(stop_evt, interval):
    while not stop_evt.is_set():
        try:
            con = db()
            n = con.execute("SELECT COUNT(*) FROM addr WHERE kind='temp'").fetchone()[0]
            n += con.execute("SELECT COUNT(*) FROM account").fetchone()[0]
            con.close()
            if n:
                scan_all()
        except Exception:
            pass
        stop_evt.wait(interval)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False)
        raw = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        for ck in getattr(self, "_cookies", []):
            self.send_header("Set-Cookie", ck)
        self._cookies = []
        self.end_headers()
        self.wfile.write(raw)

    def _auth(self):
        """每个浏览器一个 sid，用来隔离临时邮箱；「我的邮箱」不再需要登录。"""
        jar = {}
        for part in (self.headers.get("Cookie") or "").split(";"):
            if "=" in part:
                k, v = part.strip().split("=", 1)
                jar[k] = v
        sid = (jar.get(SID_COOKIE) or "").strip()
        self._cookies = getattr(self, "_cookies", [])
        if len(sid) < 16:
            sid = hashlib.sha256(os.urandom(24)).hexdigest()[:32]
            self._cookies.append(
                "%s=%s; Path=/; Max-Age=31536000; HttpOnly; SameSite=Lax" % (SID_COOKIE, sid))
        self.sid = sid

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode() or "{}")
        except Exception:
            return {}

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        self._auth()
        try:
            if u.path in ("/", "/index.html"):
                return self._send(200, web_file("index.html"), "text/html; charset=utf-8")
            if u.path == "/app.css":
                return self._send(200, web_file("app.css"), "text/css; charset=utf-8")
            if u.path == "/app.js":
                return self._send(200, web_file("app.js"), "application/javascript; charset=utf-8")
            if u.path == "/favicon.svg":
                return self._send(200, web_file("favicon.svg"), "image/svg+xml")
            if u.path == "/api/state":
                return self._send(200, {
                    "addrs": list_addrs(self.sid),
                    "accounts": acc_public(),
                    "scan": _SCAN_STATE,
                    "idle": _IDLE_STATE,
                    "temps": temp_info(),
                    "proxy": proxy_state(),
                    "main_codes": main_codes(),
                    "admin": True,
                })
            if u.path == "/api/providers":
                return self._send(200, {"providers": provider_list()})
            if u.path == "/api/accounts":
                return self._send(200, {"accounts": acc_public()})
            if u.path == "/api/domains":
                return self._send(200, {"temps": temp_info()})
            if u.path == "/api/msgs":
                light = (q.get("light") or ["0"])[0] in ("1", "true", "yes")
                local = (q.get("local") or ["0"])[0] in ("1", "true", "yes")
                return self._send(200, read_msgs((q.get("address") or [""])[0], light, local, self.sid))
            if u.path == "/api/msg":
                return self._send(200, get_msg((q.get("id") or [""])[0], self.sid))
            if u.path == "/api/updates":
                return self._send(200, latest_codes())
            if u.path == "/api/check":
                return self._send(200, check_domain((q.get("domain") or [""])[0]))
            return self._send(404, {"error": "not found"})
        except Exception as e:
            return self._send(500, {"error": "%s: %s" % (type(e).__name__, str(e)[:200])})

    def do_POST(self):
        u = urlparse(self.path)
        b = self._body()
        self._auth()
        try:
            if u.path == "/api/login":
                return self._send(200, {"ok": True})
            if u.path == "/api/logout":
                return self._send(200, {"ok": True})
            if u.path == "/api/scan":
                return self._send(200, scan_accounts_now(wait=25, force=True))
            if u.path == "/api/newtemp":
                made, errors = make_temp(int(b.get("n") or 1), b.get("prefix", ""),
                                         b.get("tag", ""), b.get("backend", ""),
                                         b.get("domain", ""), self.sid)
                return self._send(200, {"made": made, "errors": errors})
            if u.path == "/api/rm":
                return self._send(200, {"ok": remove_addr(b.get("address", ""), self.sid)})
            if u.path == "/api/accadd":
                return self._send(200, acc_add(b.get("email", ""), b.get("provider", ""),
                                                b.get("pass", ""), b.get("host", ""),
                                                int(b.get("port") or 0)))
            if u.path == "/api/accrm":
                return self._send(200, {"ok": remove_account(int(b.get("id") or 0))})
            if u.path == "/api/acctest":
                ok, msg = acc_test(int(b.get("id") or 0))
                return self._send(200, {"ok": ok, "msg": msg})
            return self._send(404, {"error": "not found"})
        except Exception as e:
            msg = str(e) if isinstance(e, RuntimeError) else "%s: %s" % (type(e).__name__, str(e))
            return self._send(500, {"error": msg[:200]})


class QuietServer(ThreadingHTTPServer):
    """浏览器每 3 秒轮询一次，关页面时连接被掐断是常态，不用往控制台刷堆栈。"""
    daemon_threads = True
    allow_reuse_address = True

    def handle_error(self, request, client_address):
        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionResetError, ConnectionAbortedError, BrokenPipeError)):
            return
        super().handle_error(request, client_address)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"),
                    help="监听地址；公网或容器部署填 0.0.0.0")
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", "18899")))
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--interval", type=int, default=5, help="后台收信节拍（秒），实际间隔由 scan_temps 按新老号自己定")
    args = ap.parse_args()

    host = (args.host or "127.0.0.1").strip()
    port = int(args.port)

    con = db()
    try:
        # WAL 让「后台扫临时邮箱」和「IMAP 收信」同时写库不互相卡死
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA synchronous=NORMAL")
    except Exception:
        pass
    con.close()
    try:
        repair_account_addrs()
    except Exception:
        pass
    stop_evt = threading.Event()
    t = threading.Thread(target=scan_worker, args=(stop_evt, max(3, args.interval)), daemon=True)
    t.start()
    threading.Thread(target=idle_supervisor, args=(stop_evt,), daemon=True).start()

    url_host = "127.0.0.1" if host in ("0.0.0.0", "::", "") else host
    url = "http://%s:%d" % (url_host, port)
    try:
        srv = QuietServer((host, port), Handler)
    except OSError as e:
        print("启动失败：监听 %s:%d 出错（%s）" % (host, port, e))
        print("端口被占用就换一个：PORT=8080 python 邮箱中心.py")
        return

    print("=" * 60)
    print("  已启动：%s" % url)
    print("  监听：%s:%d" % (host, port))
    print("  数据：%s" % DB)
    print("=" * 60)
    if not args.no_browser and url_host in ("127.0.0.1", "localhost", "::1"):
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        stop_evt.set()
        print("")
        print("停了。")


if __name__ == "__main__":
    main()
