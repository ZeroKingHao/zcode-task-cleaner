"use strict";
/* ZCode 会话清理 - 前端逻辑 */

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => [...document.querySelectorAll(sel)];

const state = {
  sessions: [],       // 全部会话（主会话，子代理挂在 children 里）
  scope: "all",
  quick: "",          // "" | completed | error | 30d
  search: "",
  selected: new Set(),
  running: false,
  protectMinutes: 60,
  expanded: new Set(),   // 展开了子代理列表的主会话 id
  sortKey: "updated_at", // 表头排序：title | msgs | updated_at | size
  sortDesc: true,
};

/* ---------- 单色线性图标（Lucide 风格，跟随文字颜色） ---------- */
const ICONS = {
  eye: '<path d="M2.062 12.348a1 1 0 0 1 0-.696 10.75 10.75 0 0 1 19.876 0 1 1 0 0 1 0 .696 10.75 10.75 0 0 1-19.876 0"/><circle cx="12" cy="12" r="3"/>',
  bot: '<path d="M12 8V4H8"/><rect width="16" height="12" x="4" y="8" rx="2"/><path d="M2 14h2"/><path d="M20 14h2"/><path d="M15 13v2"/><path d="M9 13v2"/>',
  workflow: '<rect width="8" height="8" x="3" y="3" rx="2"/><path d="M7 11v4a2 2 0 0 0 2 2h4"/><rect width="8" height="8" x="13" y="13" rx="2"/>',
  "message-square": '<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>',
  check: '<path d="M20 6 9 17l-5-5"/>',
  "triangle-alert": '<path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3"/><path d="M12 9v4"/><path d="M12 17h.01"/>',
};
function icon(name, size = 15, sw = 1.8) {
  return `<svg class="ic ic-${name}" width="${size}" height="${size}" viewBox="0 0 24 24"
    fill="none" stroke="currentColor" stroke-width="${sw}" stroke-linecap="round"
    stroke-linejoin="round" aria-hidden="true">${ICONS[name]}</svg>`;
}

const REDUCED = matchMedia("(prefers-reduced-motion: reduce)").matches;

/* ---------- 工具 ---------- */
function fmtSize(n) {
  if (n >= 1073741824) return (n / 1073741824).toFixed(2) + " GB";
  if (n >= 1048576) return (n / 1048576).toFixed(1) + " MB";
  if (n >= 1024) return Math.round(n / 1024) + " KB";
  return n + " B";
}
function fmtTime(ms) {
  if (!ms) return "–";
  const d = new Date(ms);
  const p = (x) => String(x).padStart(2, "0");
  const now = new Date();
  const ymd = `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
  const today = `${now.getFullYear()}-${p(now.getMonth() + 1)}-${p(now.getDate())}`;
  return (ymd === today ? "今天 " : ymd + " ") + `${p(d.getHours())}:${p(d.getMinutes())}`;
}
function esc(s) {
  return String(s).replace(/[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
/* 会话 ID 展示用短形：取 UUID 首段（11 11 1111-1111），无 UUID 则截前 10 位；全文放 title */
function shortId(id) {
  const m = String(id).match(/[0-9a-f]{8}-[0-9a-f]{4}/i);
  if (m) return m[0];
  return id.length > 10 ? id.slice(0, 10) + "…" : id;
}
/* 数字载入滚动（尊重系统减弱动效设置） */
function countUp(el, target, format) {
  const finalText = format(target);
  if (REDUCED || el._v === target) { el.textContent = finalText; return; }
  el._v = target;
  const t0 = performance.now(), dur = 620;
  const step = (t) => {
    const p = Math.min((t - t0) / dur, 1);
    el.textContent = format(Math.round(target * (1 - Math.pow(1 - p, 3))));
    if (p < 1) requestAnimationFrame(step);
    else el.textContent = finalText;
  };
  requestAnimationFrame(step);
}
function toast(msg, ms = 2600) {
  const el = $("#toast");
  el.textContent = msg;
  el.hidden = false;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => (el.hidden = true), ms);
}
async function api(path, body) {
  const opt = body
    ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }
    : {};
  const res = await fetch(path, opt);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
}

/* ---------- 主题 ---------- */
const THEMES = ["light", "dark", "system"];
function applyTheme(mode) {
  const dark = mode === "dark" ||
    (mode === "system" && matchMedia("(prefers-color-scheme: dark)").matches);
  document.documentElement.dataset.theme = dark ? "dark" : "light";
}
function initTheme() {
  let mode = localStorage.getItem("ztc-theme") || "system";
  applyTheme(mode);
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => applyTheme(mode));
  $("#btn-theme").addEventListener("click", () => {
    mode = THEMES[(THEMES.indexOf(mode) + 1) % THEMES.length];
    localStorage.setItem("ztc-theme", mode);
    applyTheme(mode);
    toast({ light: "已切换：浅色模式", dark: "已切换：深色模式", system: "已切换：跟随系统" }[mode]);
  });
}

/* ---------- 扫描与渲染 ---------- */
async function loadState() {
  try {
    const st = await api("/api/state");
    state.running = st.running;
    state.protectMinutes = st.protect_minutes;
    state.backupRoot = st.backup_root;
    const pill = $("#running-pill");
    pill.textContent = st.running ? "客户端运行中" : "客户端未运行";
    pill.className = "pill " + (st.running ? "bad" : "ok");
    pill.title = st.running
      ? "建议删除前完全退出客户端，避免数据被写回"
      : "客户端未运行，可以安全操作";
    const pm = $("#protect-min");
    if (pm) pm.textContent = st.protect_minutes;
  } catch (e) {
    const pill = $("#running-pill");
    pill.textContent = "状态未知"; pill.className = "pill";
  }
}

async function scan() {
  $("#empty").hidden = true;
  $$("#list .session").forEach((el) => el.remove());
  $("#list-summary").textContent = "扫描中…";
  try {
    const data = await api("/api/scan");
    state.sessions = data.sessions;
    state.selected.clear();
  } catch (e) {
    $("#list-summary").textContent = "";
    $("#empty-text").textContent = "扫描失败：" + e.message;
    $("#empty").hidden = false;
    return;
  }
  renderStats();
  renderList();
}

function renderStats() {
  const s = state.sessions;
  const active = s.filter((x) => !x.archived && !x.deleted);
  const archived = s.filter((x) => x.archived && !x.deleted);
  const deleted = s.filter((x) => x.deleted);
  const bytes = [active, archived, deleted].map((rows) =>
    rows.reduce((a, x) => a + (x.size || 0), 0));
  const totalBytes = bytes.reduce((a, b) => a + b, 0);

  countUp($("#st-total"), s.length, (n) => n);
  countUp($("#st-active"), active.length, (n) => n);
  countUp($("#st-archived"), archived.length, (n) => n);
  countUp($("#st-deleted"), deleted.length, (n) => n);
  countUp($("#st-size"), totalBytes, fmtSize);
  const vaultCount = $("#vault-count");
  if (vaultCount) countUp(vaultCount, s.length, (n) => n);

  const claimBytes = bytes[1] + bytes[2];
  const claimCount = archived.length + deleted.length;
  countUp($("#reclaim-size"), claimBytes, fmtSize);
  countUp($("#reclaim-count"), claimCount, (n) => n);
  const claimPct = $("#reclaim-pct");
  if (claimPct) {
    claimPct.textContent = (totalBytes ? Math.round((claimBytes / totalBytes) * 100) : 0) + "%";
  }
  const claimCard = $("#reclaim");
  if (claimCard) {
    claimCard.style.setProperty("--reclaim",
      (totalBytes ? (claimBytes / totalBytes) * 100 : 0) + "%");
  }

  // 范围行底部细条：按会话数占比
  const shares = {
    all: s.length ? 100 : 0,
    active: s.length ? (active.length / s.length) * 100 : 0,
    archived: s.length ? (archived.length / s.length) * 100 : 0,
    deleted: s.length ? (deleted.length / s.length) * 100 : 0,
  };
  $$("#scope-seg button").forEach((b) => {
    b.style.setProperty("--share", shares[b.dataset.scope].toFixed(2) + "%");
  });

  renderVaultRing(bytes);
}

/* 存储环按文件占用分段：活跃 / 归档 / 已删 */
function renderVaultRing(bytes) {
  const total = bytes.reduce((a, b) => a + b, 0);
  const segs = [
    [".seg-active", bytes[0]],
    [".seg-archived", bytes[1]],
    [".seg-deleted", bytes[2]],
  ];
  let offset = 0;
  for (const [sel, val] of segs) {
    const el = document.querySelector(sel);
    if (!el) continue;
    const share = total ? (val / total) * 100 : 0;
    const len = share > 0 ? Math.max(share - 1.1, 0.8) : 0;
    el.style.strokeDasharray = `${len} ${100 - len}`;
    el.style.strokeDashoffset = `${-offset}`;
    offset += share;
  }
  const ring = $("#vault-ring");
  if (!ring) return;
  const pct = (v) => (total ? Math.round((v / total) * 100) + "%" : "0%");
  ring.title = `按占用空间 · 活跃 ${pct(bytes[0])} / 归档 ${pct(bytes[1])} / 已删 ${pct(bytes[2])}`;
}

function visibleSessions() {
  const now = Date.now();
  const kw = state.search.trim().toLowerCase();
  return state.sessions.filter((s) => {
    if (state.scope === "active" && (s.archived || s.deleted)) return false;
    if (state.scope === "archived" && (!s.archived || s.deleted)) return false;
    if (state.scope === "deleted" && !s.deleted) return false;
    if (state.quick === "completed" && s.status !== "completed") return false;
    if (state.quick === "error" && s.status !== "error") return false;
    if (state.quick === "30d" && s.updated_at > now - 30 * 86400000) return false;
    if (kw && !(s.title.toLowerCase().includes(kw)
      || s.project.toLowerCase().includes(kw)
      || s.id.toLowerCase().includes(kw)
      || (s.children || []).some((c) => c.id.toLowerCase().includes(kw)))) return false;
    return true;
  });
}

function badgeHtml(s) {
  let html = "";
  if (s.deleted) html += `<span class="badge deleted" title="在客户端界面删除过（软删除）：仅从界面隐藏，数据仍完整保留在磁盘">已删</span>`;
  else if (s.archived) html += `<span class="badge archived" title="客户端已归档：可能被折叠不显示，数据仍在磁盘">归档</span>`;
  if (s.status === "completed") html += `<span class="badge completed" title="任务已正常完成">已完成</span>`;
  else if (s.status === "error") html += `<span class="badge error" title="任务以错误结束">已出错</span>`;
  else if (s.status === "running") html += `<span class="badge running" title="任务正在运行">进行中</span>`;
  if (s.protected) html += `<span class="badge using" title="最近 ${state.protectMinutes} 分钟内活跃，为防止误删正在使用的会话，已锁定勾选">使用中</span>`;
  return html;
}

/* 项目头像：按项目名取稳定色相，Apple 色板 */
const AVATAR_COLORS = [
  "#0a84ff", "#5e5ce6", "#af52de", "#ff375f", "#ff9f0a",
  "#30d158", "#64d2ff", "#ff6482", "#ac8e68", "#66d4cf",
];
function avatarHtml(project) {
  const name = project.replace(/\\/g, "/").split("/").pop() || project || "?";
  let h = 0;
  for (const c of name) h = (h * 31 + c.charCodeAt(0)) >>> 0;
  const color = AVATAR_COLORS[h % AVATAR_COLORS.length];
  return `<div class="avatar" style="background-color:${color}">${esc(name[0] || "?").toUpperCase()}</div>`;
}

/* 子代理类型 → 图标 / 徽章 */
function childMeta(c) {
  return c.type === "workflow_child"
    ? { iconKey: "workflow", label: "工作流" }
    : { iconKey: "bot", label: "子代理" };
}

const CHEVRON_SVG = `<svg width="12" height="12" viewBox="0 0 24 24" fill="none"
  stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round">
  <path d="M9 5l7 7-7 7"/></svg>`;

/* ---------- 排序 ---------- */
function sortRows(list) {
  const k = state.sortKey;
  return [...list].sort((a, b) => {
    let c;
    if (k === "title") c = a.title.localeCompare(b.title, "zh");
    else c = (a[k] || 0) - (b[k] || 0);
    return state.sortDesc ? -c : c;
  });
}

function renderThead() {
  $$("#list-head .th-sort").forEach((btn) => {
    const on = btn.dataset.sort === state.sortKey;
    btn.classList.toggle("on", on);
    btn.querySelector(".arr").textContent = on ? (state.sortDesc ? "▼" : "▲") : "";
  });
}

function renderList() {
  const list = $("#list");
  $$("#list .session").forEach((el) => el.remove());
  const rows = sortRows(visibleSessions());
  renderThead();

  rows.forEach((s, i) => {
    const proj = s.project.replace(/\\/g, "/").split("/").pop() || s.project;
    const kids = s.children || [];
    const open = state.expanded.has(s.id);
    const el = document.createElement("div");
    el.className = "session" + (s.protected ? " protected" : "");
    el.style.animationDelay = `${Math.min(i * 28, 320)}ms`;   // 逐行渐入
    el.innerHTML = `
      <input type="checkbox" data-id="${esc(s.id)}" ${state.selected.has(s.id) ? "checked" : ""}>
      ${avatarHtml(s.project)}
      <div class="s-main">
        <div class="s-title-row">
          ${kids.length
        ? `<button class="s-toggle${open ? " open" : ""}" data-toggle="${esc(s.id)}"
             title="包含 ${kids.length} 个子代理会话，点击${open ? "折叠" : "展开"}；删除主会话时将一并删除">${CHEVRON_SVG}</button>`
        : `<span class="s-toggle placeholder"></span>`}
          <span class="s-title" title="${esc(s.title)}">${esc(s.title)}</span>
          ${badgeHtml(s)}
        </div>
        <div class="s-sub"><span class="s-proj">${esc(proj)}</span><span class="s-dot">·</span><span class="sid" title="${esc(s.id)}">${esc(shortId(s.id))}</span></div>
      </div>
      <div class="s-col n c-msgs">${s.msgs}</div>
      <div class="s-col t c-time">${fmtTime(s.updated_at)}</div>
      <div class="s-col n c-size">${fmtSize(s.size)}</div>
      <button class="btn-eye" data-preview="${esc(s.id)}">${icon("eye", 13)}<span>预览</span></button>`;
    el.querySelector("input").addEventListener("change", (e) => {
      e.target.checked ? state.selected.add(s.id) : state.selected.delete(s.id);
      renderActionbar();
    });
    const tg = el.querySelector("[data-toggle]");
    if (tg) tg.addEventListener("click", () => {
      state.expanded.has(s.id) ? state.expanded.delete(s.id) : state.expanded.add(s.id);
      renderList();
    });
    el.querySelector(".btn-eye").addEventListener("click", () => openPreview(s.id));
    list.appendChild(el);

    // 展开的子代理行：只展示会话 ID 与基础信息，跟随主会话一起删除
    if (open) kids.forEach((c) => {
      const meta = childMeta(c);
      const cel = document.createElement("div");
      cel.className = "session child";
      cel.innerHTML = `
        <span></span>
        <span class="c-branch">└ ${icon(meta.iconKey, 13)}</span>
        <div class="s-main">
          <div class="s-title-row">
            <span class="s-toggle placeholder"></span>
            <span class="c-sid" title="${esc(c.id)}">${esc(shortId(c.id))}</span>
            <span class="badge child-badge" title="挂在主会话下，删除主会话时一并删除">${meta.label}</span>
          </div>
        </div>
        <div class="s-col n c-msgs">${c.msgs}</div>
        <div class="s-col t c-time">${fmtTime(c.updated_at)}</div>
        <div class="s-col n c-size">${fmtSize(c.size)}</div>
        <button class="btn-eye" data-preview="${esc(c.id)}">${icon("eye", 13)}<span>预览</span></button>`;
      cel.querySelector(".btn-eye").addEventListener("click", () => openPreview(c.id));
      list.appendChild(cel);
    });
  });

  const totalMsgs = rows.reduce((a, s) => a + s.msgs, 0);
  const totalSize = rows.reduce((a, s) => a + s.size, 0);
  $("#list-summary").innerHTML = rows.length
    ? `<b>${rows.length}</b> 个会话 · <b>${totalMsgs}</b> 条消息 · <b>${fmtSize(totalSize)}</b>`
    : "";
  $("#empty").hidden = rows.length > 0;
  const end = $("#list-end");
  if (end) {
    end.hidden = rows.length === 0;
    end.textContent = rows.length ? `已显示全部 ${rows.length} 个会话` : "";
  }
  if (!rows.length) {
    $("#empty-text").textContent = state.sessions.length
      ? "没有符合条件的会话（换个筛选条件试试）"
      : "没有发现任何会话数据";
  }
  renderActionbar();
}

function renderActionbar() {
  const sel = state.sessions.filter((s) => state.selected.has(s.id));
  const bar = $("#actionbar");
  bar.hidden = sel.length === 0;
  $("#sel-count").textContent = sel.length;
  $("#sel-size").textContent = fmtSize(sel.reduce((a, s) => a + s.size, 0));
  $("#sel-msgs").textContent = sel.reduce((a, s) => a + s.msgs, 0);
  $("#check-all").checked = sel.length > 0
    && sel.length === visibleSessions().filter((s) => !s.protected).length;
}

/* ---------- 预览 ---------- */
function findSession(id) {
  for (const s of state.sessions) {
    if (s.id === id) return s;
    const c = (s.children || []).find((c) => c.id === id);
    if (c) return { id: c.id, title: `${childMeta(c).label} · ${s.title}`, project: s.project };
  }
  return null;
}

async function openPreview(id) {
  const s = findSession(id);
  $("#pv-title").textContent = s ? s.title : id;
  $("#pv-sub").textContent = `${s ? s.project + " · " : ""}${shortId(id)} · 加载中…`;
  $("#pv-chat").innerHTML = "";
  $("#modal-preview").hidden = false;
  try {
    const data = await api("/api/preview", { id });
    $("#pv-sub").textContent =
      `${s ? s.project + " · " : ""}${shortId(id)} · ${data.messages.length} 条消息`;
    const chat = $("#pv-chat");
    if (!data.messages.length) {
      chat.innerHTML = `<div class="empty"><div class="empty-icon">${icon("message-square", 44, 1.5)}</div>
        <div>此会话没有文本消息（可能只有工具调用产物）</div></div>`;
      return;
    }
    for (const m of data.messages) {
      const div = document.createElement("div");
      div.className = "msg " + (m.role === "user" ? "user" : "assistant");
      const who = m.role === "user" ? "我" : m.role === "assistant" ? "ZCode" : m.role;
      const texts = m.parts.filter((p) => p.type === "text");
      const thinks = m.parts.filter((p) => p.type === "reasoning");
      let inner = "";
      for (const t of texts) inner += `<div class="bubble">${esc(t.text)}</div>`;
      if (thinks.length) {
        inner += `<details class="reasoning"><summary>思考过程（${thinks.length} 段）</summary>` +
          thinks.map((t) => `<div class="bubble">${esc(t.text)}</div>`).join("") +
          `</details>`;
      }
      div.innerHTML = `<div class="who">${who} · ${fmtTime(m.time)}</div>${inner}`;
      chat.appendChild(div);
    }
  } catch (e) {
    $("#pv-sub").textContent = shortId(id);
    $("#pv-chat").innerHTML =
      `<div class="empty"><div class="empty-icon">${icon("triangle-alert", 44, 1.5)}</div><div>加载失败：${esc(e.message)}</div></div>`;
  }
}

/* ---------- 删除 ---------- */
function renderBackupNote() {
  const note = $("#cf-backup-note");
  if ($("#opt-backup").checked) {
    note.classList.remove("off");
    note.innerHTML = `将备份两个数据库到<br><b>${esc(state.backupRoot || "~/.zcode-cleaner-backup/")}</b>，可在「备份与恢复」中还原`;
  } else {
    note.classList.add("off");
    note.innerHTML = `${icon("triangle-alert", 13)} 不备份直接删除：删除完成后无法通过备份恢复`;
  }
}

function confirmDelete() {
  const sel = state.sessions.filter((s) => state.selected.has(s.id));
  if (!sel.length) return;
  const kidTotal = sel.reduce((a, s) => a + (s.children || []).length, 0);
  const kidMsgs = sel.reduce((a, s) =>
    a + (s.children || []).reduce((b, c) => b + c.msgs, 0), 0);
  $("#cf-body").innerHTML = `
    将彻底删除 <b>${sel.length}</b> 个会话（共 <b>${sel.reduce((a, s) => a + s.msgs, 0)}</b> 条消息，
    释放约 <b>${fmtSize(sel.reduce((a, s) => a + s.size, 0))}</b>）：
    <ul>${sel.slice(0, 8).map((s) => `<li>${esc(s.title)} <span style="color:var(--text-3)">${esc(s.id.slice(0, 24))}…</span>${(s.children || []).length ? ` <span style="color:var(--text-3)">（含 ${(s.children || []).length} 个子代理）</span>` : ""}</li>`).join("")}
    ${sel.length > 8 ? `<li>…等共 ${sel.length} 项</li>` : ""}</ul>
    ${kidTotal ? `<p>同时连带删除 <b>${kidTotal}</b> 个子代理会话（${kidMsgs} 条消息，随主会话一起清除）。</p>` : ""}
    删除范围：消息正文（12 张表）· 任务索引 · agents/exec/artifacts 会话产物 ·
    rollout 模型日志 · debug 副本 · 日志明文清洗 · WAL checkpoint + VACUUM。`;
  $("#opt-backup").checked = true;   // 每次打开默认勾选，安全优先
  renderBackupNote();
  $("#cf-running").hidden = !state.running;
  $("#modal-confirm").hidden = false;
}

async function doDelete() {
  const backup = $("#opt-backup").checked;
  $("#modal-confirm").hidden = true;
  $("#pg-text").textContent = backup
    ? "正在删除（含备份 / VACUUM / 日志清洗，可能需要几十秒）…"
    : "正在删除（无备份 / VACUUM / 日志清洗，可能需要几十秒）…";
  $("#modal-progress").hidden = false;
  try {
    const ids = [...state.selected];
    const rep = await api("/api/delete", {
      ids,
      purge_checkpoints: $("#opt-purge-cp").checked,
      backup,
    });
    $("#modal-progress").hidden = true;
    const okN = rep.results.filter((r) => r.ok && !r.child).length;
    const mainN = rep.results.filter((r) => !r.child).length;
    const kidN = rep.results.filter((r) => r.ok && r.child).length;
    const OK = icon("check", 12), WARNI = icon("triangle-alert", 12);
    const lines = [
      `<span class="${rep.failed ? "report-fail" : "report-ok"}">
        成功 ${okN} / ${mainN}</span> 个会话，
      共 ${rep.total_msgs} 条消息，释放 ${fmtSize(rep.total_freed)}。`,
      ...(kidN ? [`连带删除子代理会话 ${kidN} 个（消息数与释放空间已计入上表）`] : []),
      rep.backup
        ? `备份：<b>${esc(rep.backup.ts)}</b>（可在「备份与恢复」中还原）`
        : `<span class="report-fail">本次未备份</span>，删除结果无法通过备份恢复`,
      `VACUUM：会话库 ${rep.vacuum.cli_db === "ok" ? OK : WARNI + " " + esc(rep.vacuum.cli_db)} ·
       任务索引 ${rep.vacuum.tasks_db === "ok" ? OK : WARNI + " " + esc(rep.vacuum.tasks_db)}`,
      `日志清洗：${rep.logs.files_washed} 个文件抹除 ${rep.logs.replacements} 处会话 ID` +
      (rep.logs.session_logs_deleted.length
        ? `，删除会话级日志 ${rep.logs.session_logs_deleted.length} 个` : ""),
    ];
    if (rep.checkpoints?.length)
      lines.push(`检查点清理：${rep.checkpoints.length} 个项目目录`);
    const fails = rep.results.filter((r) => !r.ok);
    if (fails.length)
      lines.push(`<span class="report-fail">失败 ${fails.length} 个</span>（多为数据库被占用，
        退出客户端后重试）：` + fails.map((f) => esc(f.id.slice(0, 18)) + "…").join("、"));
    $("#rp-body").innerHTML = lines.join("<br>");
    $("#modal-report").hidden = false;
    state.selected.clear();
    await Promise.all([loadState(), scan()]);
  } catch (e) {
    $("#modal-progress").hidden = true;
    toast("删除失败：" + e.message, 5000);
  }
}

/* ---------- 备份 ---------- */
async function openBackups() {
  $("#bk-list").innerHTML = "<div class='backup-item'>加载中…</div>";
  $("#modal-backups").hidden = false;
  try {
    const data = await api("/api/backups");
    $("#bk-root").textContent = "备份位置：" + (await api("/api/state")).backup_root;
    const box = $("#bk-list");
    box.innerHTML = "";
    if (!data.backups.length) {
      box.innerHTML = "<div class='backup-item' style='color:var(--text-3)'>暂无备份（执行删除时会自动创建）</div>";
      return;
    }
    for (const b of data.backups) {
      const el = document.createElement("div");
      el.className = "backup-item";
      el.innerHTML = `
        <span class="backup-ts">${b.ts}</span>
        <span class="backup-files">${b.files.join(", ")} · ${fmtSize(b.size)}</span>
        <button class="btn ghost">恢复此备份</button>`;
      el.querySelector("button").addEventListener("click", async () => {
        if (!confirm(`用备份 ${b.ts} 覆盖当前两个数据库？
（当前数据库中的全部数据将被替换为备份时刻的状态）`)) return;
        el.querySelector("button").textContent = "恢复中…";
        try {
          await api("/api/restore", { ts: b.ts });
          toast("已恢复备份 " + b.ts);
          $("#modal-backups").hidden = true;
          await Promise.all([loadState(), scan()]);
        } catch (e) {
          el.querySelector("button").textContent = "恢复此备份";
          toast("恢复失败：" + e.message, 6000);
        }
      });
      box.appendChild(el);
    }
  } catch (e) {
    $("#bk-list").innerHTML = `<div class='backup-item'>加载失败：${esc(e.message)}</div>`;
  }
}

/* ---------- 事件绑定 ---------- */
function bind() {
  $("#btn-refresh").addEventListener("click", async () => {
    toast("正在扫描…");
    await Promise.all([loadState(), scan()]);
  });
  $("#btn-help").addEventListener("click", () => { $("#modal-help").hidden = false; });
  $("#scope-seg").addEventListener("click", (e) => {
    const btn = e.target.closest("button[data-scope]");
    if (!btn) return;
    state.scope = btn.dataset.scope;
    $$("#scope-seg button").forEach((b) => {
      b.classList.toggle("on", b === btn);
      b.setAttribute("aria-pressed", b === btn ? "true" : "false");
    });
    renderList();
  });
  $$(".chip[data-quick]").forEach((chip) => {
    chip.addEventListener("click", () => {
      state.quick = state.quick === chip.dataset.quick ? "" : chip.dataset.quick;
      $$(".chip[data-quick]").forEach((c) => c.classList.toggle("on", c.dataset.quick === state.quick));
      $("#qf-clear").hidden = !state.quick;
      renderList();
    });
  });
  $("#qf-clear").addEventListener("click", () => {
    state.quick = "";
    $$(".chip[data-quick]").forEach((c) => c.classList.remove("on"));
    $("#qf-clear").hidden = true;
    renderList();
  });
  let searchTimer;
  $("#search").addEventListener("input", (e) => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => {
      state.search = e.target.value;
      renderList();
    }, 150);
  });
  $("#check-all").addEventListener("change", (e) => {
    const vis = visibleSessions().filter((s) => !s.protected);
    vis.forEach((s) => e.target.checked ? state.selected.add(s.id) : state.selected.delete(s.id));
    renderList();
  });
  $("#list-head").addEventListener("click", (e) => {
    const btn = e.target.closest(".th-sort");
    if (!btn) return;
    const key = btn.dataset.sort;
    if (state.sortKey === key) state.sortDesc = !state.sortDesc;
    else { state.sortKey = key; state.sortDesc = key !== "title"; }  // 标题默认升序，数值/时间默认降序
    renderList();
  });
  $("#btn-delete").addEventListener("click", confirmDelete);
  $("#opt-backup").addEventListener("change", renderBackupNote);
  $("#cf-ok").addEventListener("click", doDelete);
  $("#btn-preview-sel").addEventListener("click", () => {
    const first = [...state.selected][0];
    if (first) openPreview(first);
  });
  $("#btn-backups").addEventListener("click", openBackups);
  $("#rp-backups").addEventListener("click", () => {
    $("#modal-report").hidden = true;
    openBackups();
  });
  // 所有弹窗的关闭按钮 + 点击遮罩关闭
  $$(".modal").forEach((m) => {
    m.addEventListener("click", (e) => {
      if (e.target === m || e.target.closest("[data-close]")) m.hidden = true;
    });
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") $$(".modal").forEach((m) => (m.hidden = true));
  });
}

/* ---------- 启动 ---------- */
initTheme();
bind();
Promise.all([loadState(), scan()]);
