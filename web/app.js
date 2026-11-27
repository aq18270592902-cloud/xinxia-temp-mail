(function () {
  "use strict";

  var LS_KEY = "xinxia.active";
  var LS_PROV = "xinxia.provider";
  var LS_READ = "xinxia.read";
  var LS_ACC = "xinxia.account";
  var state = {
    view: "home",
    addrs: [],
    accounts: [],
    temps: [],
    active: "",
    msgs: [],
    sig: "",
    msgPending: false,
    mainCodes: [],
    codesSig: "",
    accActive: "",
    accMsgs: [],
    accSig: null,
    accPending: false,
    accListSig: "",
    admin: false
  };
  var LOCAL_CACHE = {};
  var fetchingMsgs = false;
  var fetchingAcc = false;

  var PROVIDER = { qq: "QQ 邮箱", gmail: "Gmail" };
  var HINTS = {
    qq: "QQ 邮箱：网页版 设置 → 账户 → 开启 IMAP/SMTP（需短信验证）→ 生成 16 位授权码。填授权码，不是 QQ 密码。",
    gmail: "Gmail：先开两步验证，再到 myaccount.google.com/apppasswords 生成 16 位应用专用密码。填应用专用密码，不是登录密码。"
  };

  function $(id) { return document.getElementById(id); }

  function toast(msg, bad) {
    var el = $("toast");
    el.textContent = msg;
    el.classList.toggle("bad", !!bad);
    el.classList.add("on");
    clearTimeout(toast._t);
    toast._t = setTimeout(function () { el.classList.remove("on"); }, 2800);
  }

  /* 居中确认弹窗，替代 window.confirm */
  var modalResolve = null;
  var modalFocus = null;
  var modalTask = null;

  function askConfirm(opts) {
    if (modalResolve) { closeModal(false); }
    modalFocus = document.activeElement;
    modalTask = typeof opts.task === "function" ? opts.task : null;
    $("modalTitle").textContent = opts.title || "确认";
    $("modalDesc").textContent = opts.desc || "";
    var ok = $("modalOk");
    clearTimeout(ok._doneT);
    ok.className = "btn " + (opts.danger === false ? "btn-primary" : "btn-danger");
    ok.textContent = opts.okText || "确定";
    ok.style.width = "";
    ok.removeAttribute("aria-busy");
    delete ok.dataset.busy;
    delete ok.dataset.origHtml;
    $("modal").hidden = false;
    document.body.classList.add("modal-on");
    $("modalCancel").focus();
    return new Promise(function (resolve) { modalResolve = resolve; });
  }

  function finishModal(ok) {
    if (!modalResolve) { return; }
    var resolve = modalResolve;
    modalResolve = null;
    modalTask = null;
    $("modal").hidden = true;
    document.body.classList.remove("modal-on");
    if (modalFocus && modalFocus.focus) { modalFocus.focus(); }
    modalFocus = null;
    resolve(!!ok);
  }

  function modalBusy() { return $("modalOk").dataset.busy === "1"; }

  function closeModal(ok) {
    if (modalBusy()) { return; }
    finishModal(ok);
  }

  function copyText(text, okMsg) {
    return new Promise(function (resolve) {
      function done() { toast(okMsg || "已复制"); resolve(true); }
      function fail() { toast("复制失败，请手动选中", true); resolve(false); }
      function fallback() {
        var ta = document.createElement("textarea");
        ta.value = text;
        ta.setAttribute("readonly", "");
        ta.style.position = "fixed";
        ta.style.top = "-1000px";
        document.body.appendChild(ta);
        ta.select();
        var ok = false;
        try { ok = document.execCommand("copy"); } catch (e) { ok = false; }
        document.body.removeChild(ta);
        if (ok) { done(); } else { fail(); }
      }
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(done).catch(fallback);
      } else {
        fallback();
      }
    });
  }

  /* ---------- 按钮状态：默认 / 悬停 / 按下 / 聚焦 / 禁用 / 进行中 / 成功 / 失败
     进行中锁宽度，文案再长也不会把按钮撑大或引起页面位移。 ---------- */

  function btnBusy(btn, label) {
    if (!btn || btn.dataset.busy === "1") { return false; }
    clearTimeout(btn._doneT);
    btn.dataset.busy = "1";
    btn.dataset.origHtml = btn.innerHTML;
    // 锁死外框尺寸：文字换了、宽度不变，页面不会抖
    btn.style.width = btn.offsetWidth + "px";
    btn.classList.remove("is-ok", "is-fail");
    btn.classList.add("is-busy");
    btn.setAttribute("aria-busy", "true");
    if (label) { btn.textContent = label; }
    return true;
  }

  function btnDone(btn, ok, label) {
    if (!btn) { return; }
    btn.classList.remove("is-busy");
    btn.classList.add(ok ? "is-ok" : "is-fail");
    if (label) { btn.textContent = label; }
    clearTimeout(btn._doneT);
    btn._doneT = setTimeout(function () {
      btn.classList.remove("is-ok", "is-fail");
      if (btn.dataset.origHtml != null) { btn.innerHTML = btn.dataset.origHtml; }
      btn.style.width = "";
      btn.removeAttribute("aria-busy");
      delete btn.dataset.busy;
      delete btn.dataset.origHtml;
    }, ok ? 1200 : 1800);
  }

  function copyWith(btn, text, okMsg) {
    if (!btn) { return; }
    if (!btnBusy(btn, btn.classList.contains("m-copy") ? "" : "复制中…")) { return; }
    copyText(text, okMsg).then(function (ok) {
      btnDone(btn, ok, ok ? "已复制" : "复制失败");
    });
  }

  function senderName(raw) {
    var s = String(raw || "");
    var m = s.match(/^\s*(.+?)\s*<[^>]*>?\s*$/);
    var name = m ? m[1].replace(/^["']|["']$/g, "").trim() : s.replace(/<[^>]*>/g, "").trim();
    return name || s || "未知发件人";
  }

  function pad(n) { return n < 10 ? "0" + n : String(n); }

  function fmtFullDate(raw) {
    var d = new Date(String(raw || "").trim());
    if (!isNaN(d.getTime())) {
      return d.getFullYear() + "年" + (d.getMonth() + 1) + "月" + d.getDate() + "日 "
        + pad(d.getHours()) + ":" + pad(d.getMinutes());
    }
    return String(raw || "").trim();
  }

  function fmtDate(raw) {
    var s = String(raw || "").trim();
    if (!s) { return ""; }
    var d = new Date(s);
    if (!isNaN(d.getTime())) {
      var now = new Date();
      if (d.toDateString() === now.toDateString()) {
        return pad(d.getHours()) + ":" + pad(d.getMinutes());
      }
      return (d.getMonth() + 1) + "月" + d.getDate() + "日 " + pad(d.getHours()) + ":" + pad(d.getMinutes());
    }
    var m = s.match(/(\d{1,2}:\d{2})/);
    return m ? m[1] : s.slice(0, 16);
  }

  function readIds() {
    try {
      var v = JSON.parse(localStorage.getItem(LS_READ) || "[]");
      return Array.isArray(v) ? v : [];
    } catch (e) { return []; }
  }

  function isRead(id) { return readIds().indexOf(id) >= 0; }

  function markRead(id) {
    var ids = readIds();
    if (ids.indexOf(id) >= 0) { return; }
    ids.push(id);
    if (ids.length > 400) { ids = ids.slice(-400); }
    try { localStorage.setItem(LS_READ, JSON.stringify(ids)); } catch (e) {}
  }

  function copyIcon() {
    var ns = "http://www.w3.org/2000/svg";
    var svg = document.createElementNS(ns, "svg");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("width", "11");
    svg.setAttribute("height", "11");
    svg.setAttribute("fill", "none");
    svg.setAttribute("stroke", "currentColor");
    svg.setAttribute("stroke-width", "2");
    svg.setAttribute("stroke-linecap", "round");
    svg.setAttribute("stroke-linejoin", "round");
    svg.setAttribute("aria-hidden", "true");
    var r = document.createElementNS(ns, "rect");
    r.setAttribute("x", "9"); r.setAttribute("y", "9");
    r.setAttribute("width", "12"); r.setAttribute("height", "12"); r.setAttribute("rx", "2");
    var p = document.createElementNS(ns, "path");
    p.setAttribute("d", "M5 15V5a2 2 0 0 1 2-2h10");
    svg.appendChild(r);
    svg.appendChild(p);
    return svg;
  }

  function findAddr(address) {    for (var i = 0; i < state.addrs.length; i++) {
      if (state.addrs[i].address === address) { return state.addrs[i]; }
    }
    return null;
  }

  function findAccount(email) {
    for (var i = 0; i < state.accounts.length; i++) {
      if (state.accounts[i].email === email) { return state.accounts[i]; }
    }
    return null;
  }

  function provMeta(key) {
    for (var i = 0; i < state.temps.length; i++) {
      var t = state.temps[i];
      if ((t.key || t.name) === key) { return t; }
    }
    return null;
  }

  function sigOf(list) {
    return (list || []).map(function (m) { return m.id; }).join(",");
  }

  function cachePut(address, list) {
    if (address && list) { LOCAL_CACHE[address] = list; }
  }

  /* ---------- 视图切换 ---------- */

  function showView(name, push) {
    state.view = name;
    var isAcc = name === "accounts";
    $("homeView").hidden = isAcc;
    $("accountView").hidden = !isAcc;
    document.body.classList.toggle("on-accounts", isAcc);
    if (push) {
      var url = isAcc ? "#me" : location.pathname + location.search;
      history.pushState({ view: name }, "", url);
    }
    window.scrollTo(0, 0);
    if (isAcc) {
      renderAccountView();
    }
  }

  function renderAccountView() {
    if (state.view !== "accounts") { return; }
    var body = $("accBody");
    body.hidden = false;
    if (!state.accActive || !findAccount(state.accActive)) {
      state.accActive = state.accounts.length ? state.accounts[0].email : "";
    }
    if (state.accActive) { localStorage.setItem(LS_ACC, state.accActive); }
    renderAccounts();
    renderAccInbox();
  }

  /* ---------- 数据 ---------- */

  function fetchState() {
    return fetch("/api/state").then(function (r) { return r.json(); }).then(function (j) {
      state.accounts = j.accounts || [];
      state.admin = !!j.admin;
      state.temps = j.temps || [];
      state.mainCodes = j.main_codes || [];
      var temps = (j.addrs || []).filter(function (a) { return a.kind === "temp"; });
      state.addrs = temps;
      renderProviders();
      renderMainCodes();

      var saved = localStorage.getItem(LS_KEY) || "";
      if (saved && !findAddr(saved)) {
        saved = "";
        localStorage.removeItem(LS_KEY);
      }
      if (!saved) {
        var live = temps.filter(function (a) { return !a.dead; });
        if (live.length) { saved = live[0].address; }
        else if (temps.length) { saved = temps[0].address; }
        if (saved) { localStorage.setItem(LS_KEY, saved); }
      }
      var changed = saved !== state.active;
      state.active = saved;
      if (changed) {
        state.msgs = LOCAL_CACHE[saved] || [];
        state.sig = sigOf(state.msgs);
        state.msgPending = !!saved;
        showPane($("msgList"), $("reader"));
        renderMsgs();
        if (saved) { loadLocalMsgs(saved); }
      }

      var accSaved = state.accActive || localStorage.getItem(LS_ACC) || "";
      state.accActive = findAccount(accSaved) ? accSaved : (state.accounts[0] ? state.accounts[0].email : "");
      if (state.accActive) { localStorage.setItem(LS_ACC, state.accActive); }

      renderMailbox();
      if (state.view === "accounts") {
        renderAccountView();
      }
    }).catch(function () {});
  }

  function renderProviders() {
    var sel = $("genProv");
    if (!sel) { return; }
    var list = state.temps || [];
    if (!list.length) { return; }
    var sig = list.map(function (t) {
      return (t.key || t.name) + t.name + (t.recommend ? "*" : "") + (t.blurb || "");
    }).join("|");
    if (sel._sig !== sig) {
      sel.textContent = "";
      list.forEach(function (t) {
        var o = document.createElement("option");
        o.value = t.key || t.name;
        o.textContent = t.name + "（" + (t.recommend ? "推荐，" : "") + (t.blurb || "") + "）";
        sel.appendChild(o);
      });
      sel._sig = sig;
    }
    var fallback = "";
    // 默认来源：上次选的（localStorage）优先；第一次打开就用自己的域名
    for (var j = 0; j < list.length; j++) {
      if ((list[j].key || list[j].name) === "owndomain") { fallback = "owndomain"; break; }
    }
    if (!fallback) {
      for (var i = 0; i < list.length; i++) {
        if (list[i].recommend) { fallback = list[i].key || list[i].name; break; }
      }
    }
    if (!fallback) { fallback = list[0].key || list[0].name; }
    var names = list.map(function (t) { return t.key || t.name; });
    var saved = localStorage.getItem(LS_PROV) || "";
    var want = (saved && names.indexOf(saved) >= 0) ? saved : "";
    sel.value = want || fallback;
    localStorage.setItem(LS_PROV, sel.value);
    updatePrefixHint();
  }

  function updatePrefixHint() {
    var el = $("prefixHint");
    if (!el) { return; }
    var meta = provMeta($("genProv").value);
    var text = "";
    if (meta && meta.name === "tempmail.lol") {
      text = "这个服务商会在前缀后加 6 位随机尾巴";
    } else if (meta && meta.key === "owndomain") {
      text = "转发到你的 Gmail，收信有十几秒延迟；前缀留空随机";
    }
    el.textContent = text;
    el.hidden = false;   // 始终占位，切换服务商时下面的内容不跳
  }

  function backendLabel(backend) {
    if (backend === "owndomain") {
      for (var i = 0; i < state.temps.length; i++) {
        if (state.temps[i].key === "owndomain") { return state.temps[i].name; }
      }
      return "自己的域名";
    }
    return backend || "";
  }

  function readPrefix() {
    var raw = ($("genPrefix").value || "").trim().toLowerCase();
    if (!raw) { return { value: "", error: "" }; }
    if (!/^[a-z0-9._-]+$/.test(raw)) {
      return { value: raw, error: "前缀只能用字母、数字和 . _ -" };
    }
    if (raw.length > 24) {
      return { value: raw, error: "前缀最多 24 位" };
    }
    if (!/^[a-z0-9]/.test(raw) || !/[a-z0-9]$/.test(raw)) {
      return { value: raw, error: "前缀不能以 . _ - 开头或结尾" };
    }
    return { value: raw, error: "" };
  }

  /* ---------- 临时邮箱 ---------- */

  function renderMailbox() {
    var box = $("mailbox");
    var start = $("start");
    document.body.classList.toggle("has-mail", !!state.active);
    if (!state.active) {
      box.hidden = true;
      start.hidden = false;
      return;
    }
    box.hidden = false;
    start.hidden = true;
    $("curAddr").textContent = state.active;

    var meta = findAddr(state.active);
    var dead = !!(meta && meta.dead);
    var passWrap = $("addrPassWrap");
    if (passWrap) {
      if (meta && meta.password) {
        $("addrPass").textContent = meta.password;
        passWrap.hidden = false;
      } else {
        passWrap.hidden = true;
      }
    }
    var tag = $("provTag");
    tag.textContent = meta ? (backendLabel(meta.backend) + (dead ? " · 已过期" : "")) : "";
    tag.classList.toggle("dead", dead);
    tag.hidden = !meta;
    $("expiredBox").hidden = !dead;
    $("inbox").hidden = dead;

    var sw = $("addrSwitch");
    var wrap = $("switchWrap");
    var list = state.addrs.slice(0, 15);
    if (list.length > 1) {
      wrap.hidden = false;
      sw.textContent = "";
      list.forEach(function (a) {
        var o = document.createElement("option");
        o.value = a.address;
        o.textContent = a.address + (a.dead ? "（已过期）" : "");
        if (a.address === state.active) { o.selected = true; }
        sw.appendChild(o);
      });
    } else {
      wrap.hidden = true;
    }
  }

  // 先读本地库：切地址时立刻有内容，供应商那边慢或限流都不影响这一步
  function loadLocalMsgs(address) {
    return fetch("/api/msgs?address=" + encodeURIComponent(address) + "&light=1&local=1")
      .then(function (r) { return r.json(); })
      .then(function (j) {
        if (state.active !== address || !j.ok) { return; }
        var list = j.messages || [];
        cachePut(address, list);
        var sig = sigOf(list);
        if (sig !== state.sig || state.msgPending) {
          state.msgs = list;
          state.sig = sig;
          state.msgPending = false;
          renderMsgs();
        }
      })
      .catch(function () {});
  }

  function pollMsgs() {
    if (!state.active || fetchingMsgs) { return; }
    var activeMeta = findAddr(state.active);
    if (activeMeta && activeMeta.dead) { return; }
    fetchingMsgs = true;
    var addr = state.active;
    fetch("/api/msgs?address=" + encodeURIComponent(addr) + "&light=1")
      .then(function (r) { return r.json(); })
      .then(function (j) {
        if (state.active !== addr) { return; }
        var noteEl = $("liveNote");
        if (j.dead) {
          if (noteEl) { noteEl.hidden = true; }
          fetchState();
          return;
        }
        var note = j.note || "";
        if (note) {
          noteEl.textContent = note;
          noteEl.title = note;
          noteEl.hidden = false;
        } else {
          noteEl.hidden = true;
          noteEl.title = "";
        }
        state.msgs = j.messages || [];
        state.msgPending = false;
        cachePut(addr, state.msgs);
        var sig = sigOf(state.msgs);
        if (sig !== state.sig) {
          state.sig = sig;
          renderMsgs();
        }
        if (note.indexOf("过期") >= 0 || note.indexOf("查不到") >= 0) { fetchState(); }
      })
      .catch(function () {})
      .then(function () { fetchingMsgs = false; });
  }

  function buildRow(m) {
    var read = isRead(m.id);

    var row = document.createElement("div");
    row.className = "msg-row " + (read ? "read" : "unread");
    row.setAttribute("data-id", m.id);
    row.setAttribute("role", "button");
    row.tabIndex = 0;

    var dot = document.createElement("span");
    dot.className = "m-dot";
    dot.setAttribute("aria-hidden", "true");

    var from = document.createElement("span");
    from.className = "m-from";
    from.textContent = senderName(m.sender || m.from);
    from.title = m.sender || m.from || "";

    var body = document.createElement("span");
    body.className = "m-body";

    var line = document.createElement("span");
    line.className = "m-line";

    var name = document.createElement("b");
    name.className = "m-subj";
    name.textContent = m.subject || "(无主题)";
    line.appendChild(name);

    if (m.code) {
      var btn = document.createElement("button");
      btn.type = "button";
      btn.className = "m-copy";
      btn.title = "复制验证码 " + m.code;
      btn.setAttribute("aria-label", "复制验证码 " + m.code);
      var digits = document.createElement("span");
      digits.textContent = m.code;
      btn.appendChild(digits);
      btn.appendChild(copyIcon());
      btn.addEventListener("click", function (e) {
        e.stopPropagation();
        copyWith(btn, m.code, "验证码已复制");
      });
      line.appendChild(btn);
    }
    body.appendChild(line);

    if (m.excerpt) {
      var ex = document.createElement("span");
      ex.className = "m-excerpt";
      ex.textContent = m.excerpt;
      body.appendChild(ex);
    }

    var time = document.createElement("span");
    time.className = "m-time";
    time.textContent = fmtDate(m.mdate || m.date);

    row.appendChild(dot);
    row.appendChild(from);
    row.appendChild(body);
    row.appendChild(time);
    return row;
  }

  function renderMsgList(container, msgs) {
    msgs = msgs || [];
    // 列表按行增量更新：只插入新行、移除消失的行，不整块清空重画，
    // 这样新邮件到达时滚动位置和焦点都不会被拽走。
    var old = {};
    var kids = container.children;
    for (var i = 0; i < kids.length; i++) {
      var k = kids[i];
      if (k.classList && k.classList.contains("msg-row")) {
        old[k.getAttribute("data-id")] = k;
      }
    }
    var ph = container.querySelector(".empty");
    if (msgs.length && ph) { container.removeChild(ph); }
    if (!msgs.length) {
      container.textContent = "";
      var empty = document.createElement("div");
      empty.className = "empty";
      var t = document.createElement("strong");
      t.textContent = "还没有收到邮件";
      empty.appendChild(t);
      container.appendChild(empty);
      return 0;
    }
    var unread = 0;
    var cursor = container.firstChild;
    msgs.forEach(function (m) {
      if (!isRead(m.id)) { unread++; }
      var node = old[m.id];
      if (node) {
        delete old[m.id];
      } else {
        node = buildRow(m);
        node.classList.add("fresh");
        setTimeout(function () { node.classList.remove("fresh"); }, 1800);
      }
      if (node === cursor) {
        cursor = cursor.nextSibling;
      } else {
        container.insertBefore(node, cursor);
      }
    });
    Object.keys(old).forEach(function (id) {
      var n = old[id];
      if (n.parentNode === container) { container.removeChild(n); }
    });
    return unread;
  }

  function renderPending(container) {
    container.textContent = "";
    var box = document.createElement("div");
    box.className = "empty";
    var t = document.createElement("strong");
    t.textContent = "正在收取…";
    box.appendChild(t);
    container.appendChild(box);
  }

  function countText(total, unread) {
    return total + " 封" + (unread ? " · " + unread + " 未读" : "");
  }

  function renderMsgs() {
    if (!state.msgs.length && state.msgPending) {
      renderPending($("msgList"));
      $("inboxCount").textContent = "正在收取…";
      return;
    }
    var unread = renderMsgList($("msgList"), state.msgs);
    $("inboxCount").textContent = countText(state.msgs.length, unread);
  }

  function renderMainCodes() {
    var sec = $("codes");
    var list = $("codesList");
    var items = state.mainCodes || [];
    var sig = items.map(function (m) { return m.id + ":" + m.code; }).join("|");
    if (sig === state.codesSig) { return; }
    state.codesSig = sig;
    if (!items.length) {
      sec.hidden = true;
      list.textContent = "";
      return;
    }
    sec.hidden = false;
    $("codesCount").textContent = items.length + " 条";
    list.textContent = "";
    items.forEach(function (m) {
      var row = document.createElement("div");
      row.className = "code-row";

      var mail = document.createElement("span");
      mail.className = "code-mail";
      mail.textContent = m.address;
      mail.title = m.address;

      var main = document.createElement("div");
      main.className = "code-main";
      var subj = document.createElement("div");
      subj.className = "code-subj";
      subj.textContent = m.subject || "(无主题)";
      main.appendChild(subj);
      if (m.excerpt) {
        var ex = document.createElement("div");
        ex.className = "code-ex";
        ex.textContent = m.excerpt;
        main.appendChild(ex);
      }

      var btn = document.createElement("button");
      btn.type = "button";
      btn.className = "m-copy";
      btn.title = "复制验证码 " + m.code;
      btn.setAttribute("aria-label", "复制验证码 " + m.code);
      var digits = document.createElement("span");
      digits.textContent = m.code;
      btn.appendChild(digits);
      btn.appendChild(copyIcon());
      btn.addEventListener("click", function () {
        copyWith(btn, m.code, "验证码已复制");
      });

      var time = document.createElement("span");
      time.className = "code-time";
      time.textContent = fmtDate(m.mdate || m.date);

      row.appendChild(mail);
      row.appendChild(main);
      row.appendChild(btn);
      row.appendChild(time);
      list.appendChild(row);
    });
  }

  function showPane(listEl, readerEl) {
    if (listEl.parentNode) { listEl.parentNode.style.minHeight = ""; }
    listEl.hidden = false;
    readerEl.hidden = true;
    readerEl.textContent = "";
  }

  function htmlDoc(html) {
    var reset = "<style>"
      + "html,body{margin:0;padding:0;background:#fff;color:#1a1815;height:auto!important;min-height:0!important}"
      + "body{font:14px/1.7 -apple-system,BlinkMacSystemFont,'Segoe UI','PingFang SC','Microsoft YaHei',sans-serif;"
      + "word-break:break-word;overflow-x:hidden}"
      + "table{border-collapse:collapse;max-width:100%!important}"
      + "img{max-width:100%!important}"
      + "a{color:#2a574c}"
      + "</style>";
    var script = "<script>(function(){function r(){try{var h=Math.max(document.documentElement.scrollHeight,"
      + "document.body.scrollHeight);parent.postMessage({t:'xx-h',h:h},'*')}catch(e){}}"
      + "r();window.addEventListener('load',r);setTimeout(r,120);setTimeout(r,600);"
      + "if(window.ResizeObserver){new ResizeObserver(r).observe(document.body)}else{setInterval(r,1200)}})();"
      + "<\/script>";
    return "<!doctype html><html><head><meta charset='utf-8'>"
      + "<meta http-equiv='Content-Security-Policy' content=\"default-src 'none'; style-src 'unsafe-inline'; "
      + "script-src 'unsafe-inline'; img-src data:;\">"
      + reset + "</head><body>" + html + script + "</body></html>";
  }

  window.addEventListener("message", function (e) {
    if (!e.data || e.data.t !== "xx-h") { return; }
    var frames = document.querySelectorAll("iframe.reader-html");
    for (var i = 0; i < frames.length; i++) {
      if (e.source === frames[i].contentWindow) {
        var h = parseInt(e.data.h, 10) || 0;
        if (h > 0) { frames[i].style.height = Math.min(2600, Math.max(120, h + 2)) + "px"; }
      }
    }
  });

  function openMessage(id, listEl, readerEl) {
    markRead(id);
    if (listEl === $("msgList")) { renderMsgs(); } else { renderAccMsgs(); }

    fetch("/api/msg?id=" + encodeURIComponent(id)).then(function (r) { return r.json(); }).then(function (j) {
      if (!j.ok) { toast(j.error || "打开失败", true); return; }
      var m = j.message;
      readerEl.textContent = "";

      var back = document.createElement("button");
      back.type = "button";
      back.className = "reader-back";
      back.textContent = "← 返回收件箱";
      back.addEventListener("click", function () {
        var keepY = window.scrollY;
        showPane(listEl, readerEl);
        window.scrollTo(0, keepY);
      });
      readerEl.appendChild(back);

      var h = document.createElement("h3");
      h.textContent = m.subject || "(无主题)";
      readerEl.appendChild(h);

      var meta = document.createElement("p");
      meta.className = "reader-meta";
      meta.textContent = (m.sender || senderName(m.sender)) + " · " + fmtFullDate(m.mdate || m.date);
      readerEl.appendChild(meta);

      if (m.code) {
        var box = document.createElement("div");
        box.className = "code-box";
        var lab = document.createElement("span");
        lab.textContent = "验证码";
        var strong = document.createElement("strong");
        strong.textContent = m.code;
        var btn = document.createElement("button");
        btn.type = "button";
        btn.className = "btn btn-dark";
        btn.textContent = "复制验证码";
        btn.addEventListener("click", function () { copyWith(btn, m.code, "验证码已复制"); });
        box.appendChild(lab);
        box.appendChild(strong);
        box.appendChild(btn);
        readerEl.appendChild(box);
      }

      if (m.html) {
        var frame = document.createElement("iframe");
        frame.className = "reader-html";
        frame.setAttribute("sandbox", "allow-scripts");
        frame.setAttribute("title", "邮件正文");
        frame.style.height = "320px";
        frame.srcdoc = htmlDoc(m.html);
        readerEl.appendChild(frame);
      } else {
        var body = document.createElement("pre");
        body.className = "reader-body";
        body.textContent = m.body || "（这封邮件没有可显示的正文）";
        readerEl.appendChild(body);
      }

      // 换到读信视图时锁住容器高度，页面不会因为列表被收起而整体上跳
      var holder = listEl.parentNode;
      holder.style.minHeight = holder.getBoundingClientRect().height.toFixed(1) + "px";
      var keepY = window.scrollY;
      listEl.hidden = true;
      readerEl.hidden = false;
      window.scrollTo(0, keepY);
    }).catch(function () { toast("打开失败", true); });
  }

  function setActive(address) {
    state.active = address;
    state.msgs = LOCAL_CACHE[address] ? LOCAL_CACHE[address].slice() : [];
    state.sig = sigOf(state.msgs);
    state.msgPending = !!address;
    localStorage.setItem(LS_KEY, address);
    renderMailbox();
    showPane($("msgList"), $("reader"));
    renderMsgs();
    if (address) {
      var p = loadLocalMsgs(address);
      pollMsgs();
      return p;
    }
    return Promise.resolve();
  }

  function generate(btn) {
    btn = btn || $("mkTemp");
    if (btn.dataset.busy === "1") { return; }
    var backend = $("genProv").value || "";
    if (!backend) {
      toast("先选一个服务商", true);
      return;
    }
    var pre = readPrefix();
    if (pre.error) {
      toast(pre.error, true);
      $("genPrefix").focus();
      return;
    }
    if (!btnBusy(btn, "创建中…")) { return; }
    fetch("/api/newtemp", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ n: 1, backend: backend, prefix: pre.value })
    }).then(function (r) { return r.json(); }).then(function (j) {
      var made = j.made || [], errs = j.errors || [];
      if (!made.length) {
        btnDone(btn, false, "创建失败");
        toast(errs[0] || "生成失败，换一次试试", true);
        return;
      }
      $("genPrefix").value = "";
      btnDone(btn, true, "已创建");
      return fetchState().then(function () { setActive(made[0].address); });
    }).catch(function () {
      btnDone(btn, false, "创建失败");
      toast("创建失败", true);
    });
  }

  /* ---------- 手动刷新 / 删除 ---------- */

  function refreshInbox() {
    var btn = $("refreshNow");
    if (!state.active || !btnBusy(btn, "")) { return; }
    var addr = state.active;
    fetch("/api/msgs?address=" + encodeURIComponent(addr) + "&light=1")
      .then(function (r) { return r.json(); })
      .then(function (j) {
        if (state.active !== addr) { return; }
        var noteEl = $("liveNote");
        var note = j.note || "";
        if (note) {
          noteEl.textContent = note;
          noteEl.title = note;
          noteEl.hidden = false;
        } else {
          noteEl.hidden = true;
          noteEl.title = "";
        }
        if (!j.ok) {
          btnDone(btn, false, "");
          toast(j.error || "刷新失败", true);
          return;
        }
        state.msgs = j.messages || [];
        state.msgPending = false;
        cachePut(addr, state.msgs);
        state.sig = sigOf(state.msgs);
        renderMsgs();
        if (j.dead) { fetchState(); }
        btnDone(btn, true, "");
        toast(note ? "已刷新" : "已是最新");
      })
      .catch(function () {
        btnDone(btn, false, "");
        toast("刷新失败", true);
      });
  }

  function refreshAccount() {
    var btn = $("accRefresh");
    if (!state.accActive || !btnBusy(btn, "")) { return; }
    fetch("/api/scan", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}"
    }).then(function (r) { return r.json(); }).then(function (j) {
      if (j.errors && j.errors.length) { toast(j.errors[0], true); }
      else { toast(j.found ? ("收到 " + j.found + " 封新邮件") : "已是最新"); }
      btnDone(btn, !(j.errors && j.errors.length), "");
      var email = state.accActive;
      return fetchState().then(function () {
        if (state.accActive === email) { return fetchAccMsgs(); }
      });
    }).catch(function () {
      btnDone(btn, false, "");
      toast("刷新失败", true);
    });
  }

  function deleteAddr() {
    var addr = state.active;
    if (!addr) { return; }
    askConfirm({
      title: "删除这个地址",
      desc: "删除 " + addr + "？本地记录和已收邮件会一起删掉，服务商那边的信箱不受影响。",
      okText: "删除",
      task: function () { return doDeleteAddr(addr); }
    });
  }

  function doDeleteAddr(addr) {
    return fetch("/api/rm", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ address: addr })
    }).then(function (r) { return r.json(); }).then(function (j) {
      if (!j.ok) { toast("删除失败", true); return false; }
      delete LOCAL_CACHE[addr];
      state.addrs = state.addrs.filter(function (a) { return a.address !== addr; });
      localStorage.removeItem(LS_KEY);
      state.active = "";
      state.msgs = [];
      state.sig = "";
      state.msgPending = false;
      showPane($("msgList"), $("reader"));
      renderMsgs();
      renderMailbox();
      var next = "";
      for (var i = 0; i < state.addrs.length; i++) {
        if (!state.addrs[i].dead) { next = state.addrs[i].address; break; }
      }
      if (!next && state.addrs.length) { next = state.addrs[0].address; }
      toast("已删除");
      if (next) { setActive(next); }
      fetchState();
      return true;
    }).catch(function () { toast("删除失败", true); return false; });
  }

  /* ---------- 我的邮箱 ---------- */

  function syncProvider() {
    var p = $("aProv").value || "qq";
    $("aPassLabel").textContent = p === "gmail" ? "应用专用密码" : "授权码";
    $("aPass").placeholder = p === "gmail" ? "16 位应用专用密码" : "16 位授权码，不是登录密码";
    $("aHint").textContent = HINTS[p] || HINTS.qq;
  }

  function renderAccounts() {
    var box = $("accList");
    var sig = state.accActive + "|" + state.accounts.map(function (a) {
      return [a.id, a.email, a.provider, a.last_error || "", a.has_pass ? 1 : 0].join(":");
    }).join("|");
    if (sig === state.accListSig) { return; }
    state.accListSig = sig;
    box.textContent = "";
    if (!state.accounts.length) {
      var empty = document.createElement("p");
      empty.className = "acc-empty";
      empty.textContent = "还没有接入邮箱，点右上角「添加邮箱」。";
      box.appendChild(empty);
      return;
    }
    state.accounts.forEach(function (a) {
      var item = document.createElement("div");
      item.className = "acc-item" + (a.email === state.accActive ? " on" : "");
      item.setAttribute("role", "button");
      item.tabIndex = 0;
      item.setAttribute("data-email", a.email);

      var top = document.createElement("div");
      top.className = "acc-item-top";
      var mail = document.createElement("span");
      mail.className = "acc-item-mail";
      mail.textContent = a.email;
      var prov = document.createElement("span");
      prov.className = "acc-item-prov";
      prov.textContent = PROVIDER[a.provider] || a.provider;
      top.appendChild(mail);
      top.appendChild(prov);

      var sub = document.createElement("div");
      sub.className = "status " + (a.last_error ? "bad" : (a.last_scan ? "ok" : "muted"));
      var dot = document.createElement("i");
      sub.appendChild(dot);
      var text = document.createElement("span");
      if (a.last_error) {
        text.textContent = "未连接 · " + a.last_error;
      } else if (a.last_scan) {
        text.textContent = "已连接 · 自动收信";
      } else {
        text.textContent = "待连接";
      }
      sub.appendChild(text);

      var acts = document.createElement("div");
      acts.className = "acc-item-acts";
      var edit = document.createElement("button");
      edit.type = "button";
      edit.className = "acc-act";
      edit.textContent = "改码";
      edit.addEventListener("click", function (e) { e.stopPropagation(); editAccount(a); });

      var test = document.createElement("button");
      test.type = "button";
      test.className = "acc-act";
      test.textContent = "测试";
      test.addEventListener("click", function (e) { e.stopPropagation(); testAccount(a.id, test); });

      var del = document.createElement("button");
      del.type = "button";
      del.className = "acc-act danger";
      del.textContent = "删除";
      del.addEventListener("click", function (e) { e.stopPropagation(); removeAccount(a); });

      acts.appendChild(edit);
      acts.appendChild(test);
      acts.appendChild(del);

      item.appendChild(top);
      item.appendChild(sub);
      item.appendChild(acts);

      item.addEventListener("click", function () { selectAccount(a.email); });
      item.addEventListener("keydown", function (e) {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          selectAccount(a.email);
        }
      });
      box.appendChild(item);
    });
  }

  function selectAccount(email) {
    state.accActive = email;
    state.accMsgs = LOCAL_CACHE[email] ? LOCAL_CACHE[email].slice() : [];
    state.accSig = sigOf(state.accMsgs);
    state.accPending = !!email;
    localStorage.setItem(LS_ACC, email);
    $("accReader").hidden = true;
    $("accReader").textContent = "";
    $("accMsgList").hidden = false;
    renderAccounts();
    renderAccMsgs();
    renderAccInbox();
  }

  function fetchAccMsgs() {
    if (!state.admin) { return Promise.resolve(); }
    var acc = findAccount(state.accActive);
    if (!acc) { return Promise.resolve(); }
    var email = acc.email;
    return fetch("/api/msgs?address=" + encodeURIComponent(email) + "&light=1")
      .then(function (r) {
        if (r.status === 401) {
          state.admin = false;
          renderAccountView();
          return {};
        }
        return r.json();
      })
      .then(function (j) {
        if (state.accActive !== email) { return; }
        var msgs = j.messages || [];
        cachePut(email, msgs);
        var sig = sigOf(msgs);
        var wasPending = state.accPending;
        state.accPending = false;
        if (sig !== state.accSig || wasPending) {
          state.accSig = sig;
          state.accMsgs = msgs;
          renderAccMsgs();
        }
      })
      .catch(function () {
        if (state.accActive !== email) { return; }
        state.accPending = false;
        renderAccMsgs();
      });
  }

  function renderAccInbox() {
    var acc = findAccount(state.accActive);
    $("accTitle").textContent = acc ? acc.email : "收件箱";
    if (!acc) {
      state.accMsgs = [];
      state.accPending = false;
      renderAccMsgs();
      return;
    }
    fetchAccMsgs();
  }

  function renderAccMsgs() {
    if (!state.accMsgs.length && state.accPending) {
      renderPending($("accMsgList"));
      $("accCount").textContent = "正在收取…";
      return;
    }
    var unread = renderMsgList($("accMsgList"), state.accMsgs);
    $("accCount").textContent = countText(state.accMsgs.length, unread);
  }

  function pollAccMsgs() {
    if (!state.admin || state.view !== "accounts" || !state.accActive || fetchingAcc) { return; }
    var acc = findAccount(state.accActive);
    if (!acc) { return; }
    fetchingAcc = true;
    fetch("/api/msgs?address=" + encodeURIComponent(acc.email) + "&light=1")
      .then(function (r) { return r.json(); })
      .then(function (j) {
        if (state.view !== "accounts" || state.accActive !== acc.email) { return; }
        var msgs = j.messages || [];
        cachePut(acc.email, msgs);
        var sig = sigOf(msgs);
        var seenIds = {};
        (state.accMsgs || []).forEach(function (m) { seenIds[m.id] = 1; });
        var fresh = msgs.filter(function (m) { return !seenIds[m.id]; }).length;
        state.accPending = false;
        if (sig !== state.accSig) {
          state.accSig = sig;
          state.accMsgs = msgs;
          renderAccMsgs();
          if (fresh > 0 && state.view === "accounts") {
            toast("收到 " + fresh + " 封新邮件");
          }
        }
      })
      .catch(function () {})
      .then(function () { fetchingAcc = false; });
  }

  function editAccount(a) {
    $("accForm").hidden = false;
    $("aEmail").value = a.email;
    $("aProv").value = a.provider === "gmail" ? "gmail" : "qq";
    syncProvider();
    $("aPass").value = "";
    $("aPass").focus();
  }

  function testAccount(id, btn) {
    if (!btnBusy(btn, "测试中")) { return; }
    fetch("/api/acctest", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id: id })
    }).then(function (r) { return r.json(); }).then(function (j) {
      toast(j.ok ? "连接正常" : ("连不上：" + (j.msg || "")), !j.ok);
      btnDone(btn, !!j.ok, j.ok ? "正常" : "失败");
      setTimeout(fetchState, 1300);
    }).catch(function () {
      toast("测试失败", true);
      btnDone(btn, false, "失败");
    });
  }

  function removeAccount(a) {
    askConfirm({
      title: "删除这个邮箱",
      desc: "删除 " + a.email + "？本地保存的授权码和收信记录会一起删掉。",
      okText: "删除",
      task: function () {
        return fetch("/api/accrm", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ id: a.id })
        }).then(function (r) { return r.json(); }).then(function (j) {
          if (!j.ok) { toast("删除失败", true); return false; }
          delete LOCAL_CACHE[a.email];
          if (state.accActive === a.email) {
            state.accActive = "";
            state.accMsgs = [];
            state.accSig = "";
            state.accPending = false;
          }
          toast("已删除");
          fetchState();
          return true;
        }).catch(function () { toast("删除失败", true); return false; });
      }
    });
  }

  function submitAccount(e) {
    e.preventDefault();
    var email = ($("aEmail").value || "").trim();
    var provider = $("aProv").value || "qq";
    var pass = ($("aPass").value || "").trim();
    if (email.indexOf("@") < 0) { toast("邮箱地址格式不对", true); return; }
    if (!pass) { toast("要填授权码", true); return; }

    var btn = $("aGo");
    if (!btnBusy(btn, "连接中…")) { return; }
    fetch("/api/accadd", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email: email, provider: provider, pass: pass })
    }).then(function (r) { return r.json(); }).then(function (j) {
      if (j.error) { btnDone(btn, false, "连接失败"); toast(j.error, true); return; }
      if (j.ok) {
        toast(j.msg || "已连接");
        btnDone(btn, true, "已连接");
        $("aPass").value = "";
        $("aEmail").value = "";
        state.accActive = email;
        localStorage.setItem(LS_ACC, email);
        setTimeout(function () {
          $("accForm").hidden = true;
          fetchState().then(function () {
            if (findAccount(email)) { selectAccount(email); }
          });
        }, 700);
      } else {
        btnDone(btn, false, "连接失败");
        toast("连不上：" + (j.msg || ""), true);
      }
    }).catch(function () {
      btnDone(btn, false, "连接失败");
      toast("连接失败", true);
    });
  }

  /* ---------- 事件 ---------- */

  $("mkTemp").addEventListener("click", function () { generate(); });
  $("rebuild").addEventListener("click", function () {
    var meta = findAddr(state.active);
    var sel = $("genProv");
    if (meta && meta.backend && sel) {
      sel.value = meta.backend;
      localStorage.setItem(LS_PROV, meta.backend);
    }
    generate($("rebuild"));
  });
  $("copyAddr").addEventListener("click", function () {
    if (state.active) { copyWith($("copyAddr"), state.active, "地址已复制"); }
  });
  $("copyPass").addEventListener("click", function () {
    var meta = findAddr(state.active);
    if (meta && meta.password) { copyWith($("copyPass"), meta.password, "密码已复制"); }
  });
  $("addrSwitch").addEventListener("change", function () {
    var sel = this;
    if (!sel.value) { return; }
    sel.classList.add("is-busy");
    sel.setAttribute("aria-busy", "true");
    var p = setActive(sel.value) || Promise.resolve();
    var t0 = Date.now();
    Promise.resolve(p).then(function () {
      var wait = Math.max(0, 200 - (Date.now() - t0));
      setTimeout(function () {
        sel.classList.remove("is-busy");
        sel.removeAttribute("aria-busy");
      }, wait);
    });
  });
  $("genProv").addEventListener("change", function () {
    localStorage.setItem(LS_PROV, this.value);
    updatePrefixHint();
  });
  $("refreshNow").addEventListener("click", refreshInbox);
  $("accRefresh").addEventListener("click", refreshAccount);
  $("delAddr").addEventListener("click", deleteAddr);

  $("msgList").addEventListener("click", function (e) {
    var row = e.target.closest("[data-id]");
    if (row) { openMessage(row.getAttribute("data-id"), $("msgList"), $("reader")); }
  });
  $("msgList").addEventListener("keydown", function (e) {
    if (e.key !== "Enter" && e.key !== " ") { return; }
    var row = e.target.closest ? e.target.closest(".msg-row") : null;
    if (row && e.target === row) {
      e.preventDefault();
      openMessage(row.getAttribute("data-id"), $("msgList"), $("reader"));
    }
  });

  $("accMsgList").addEventListener("click", function (e) {
    var row = e.target.closest("[data-id]");
    if (row) { openMessage(row.getAttribute("data-id"), $("accMsgList"), $("accReader")); }
  });
  $("accMsgList").addEventListener("keydown", function (e) {
    if (e.key !== "Enter" && e.key !== " ") { return; }
    var row = e.target.closest ? e.target.closest(".msg-row") : null;
    if (row && e.target === row) {
      e.preventDefault();
      openMessage(row.getAttribute("data-id"), $("accMsgList"), $("accReader"));
    }
  });

  $("openAdmin").addEventListener("click", function () {
    showView("accounts", true);
    fetchState();
  });
  $("modalCancel").addEventListener("click", function () { closeModal(false); });
  $("modalMask").addEventListener("click", function () { closeModal(false); });
  $("modalOk").addEventListener("click", function () {
    var btn = this;
    if (!modalTask) { closeModal(true); return; }
    if (!btnBusy(btn, "处理中")) { return; }
    var p;
    try { p = modalTask(); } catch (e) { p = Promise.reject(e); }
    Promise.resolve(p).then(function (ok) {
      if (ok === false) { btnDone(btn, false, "失败"); return; }
      btnDone(btn, true, "完成");
      setTimeout(function () { finishModal(true); }, 420);
    }, function () { btnDone(btn, false, "失败"); });
  });
  document.addEventListener("keydown", function (e) {
    if (!$("modal").hidden && e.key === "Escape") {
      e.preventDefault();
      closeModal(false);
    }
  });
  $("backHome").addEventListener("click", function (e) {
    e.preventDefault();
    showView("home", true);
  });
  $("brandHome").addEventListener("click", function (e) {
    if (state.view === "accounts") {
      e.preventDefault();
      showView("home", true);
    }
  });
  window.addEventListener("popstate", function () {
    showView(location.hash === "#me" ? "accounts" : "home", false);
  });

  $("accAddToggle").addEventListener("click", function () {
    var f = $("accForm");
    f.hidden = !f.hidden;
    if (!f.hidden) { $("aEmail").focus(); }
  });
  $("aProv").addEventListener("change", syncProvider);
  $("accForm").addEventListener("submit", submitAccount);

  /* ---------- 启动 ---------- */

  syncProvider();
  fetchState().then(function () {
    pollMsgs();
    if (location.hash === "#me") { showView("accounts", false); }
    // 首屏可交互时刻，留给性能实测读取
    try { if (window.performance && performance.mark) { performance.mark("app-ready"); } } catch (e) {}
  });
  setInterval(pollMsgs, 1500);
  setInterval(pollAccMsgs, 1500);
  setInterval(fetchState, 4000);
})();
