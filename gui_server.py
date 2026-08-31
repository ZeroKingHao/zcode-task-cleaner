#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
ZCode 会话清理工具 - GUI 后端
================================
本地 Web 界面后端：仅用 Python 标准库，复用 zcode-task-cleaner.py 的核心删除逻辑。

  python gui_server.py [--port 8765] [--open]

浏览器打开 http://127.0.0.1:8765 即可使用。

API:
  GET  /                    前端页面 (gui/)
  GET  /api/state           存储位置 / 客户端运行状态
  GET  /api/scan            全量扫描会话清单
  POST /api/preview         {id} 查看完整对话文本
  POST /api/delete          {ids, purge_checkpoints} 备份+硬删除+VACUUM+日志清洗
  GET  /api/backups         备份列表
  POST /api/restore         {ts} 从备份还原两个数据库
"""
import importlib.util
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

GUI_DIR = Path(__file__).with_name("gui")
PROTECT_MINUTES = 60            # 最近 N 分钟活跃的会话视为"使用中"，禁止删除
MAX_WASH_SIZE = 256 * 1024 * 1024   # 超过此大小的日志文件跳过明文清洗

# ---------------------------------------------------------------------------
# 加载 CLI 核心模块（单一数据来源，避免两份删除逻辑漂移）
# ---------------------------------------------------------------------------
spec = importlib.util.spec_from_file_location(
    "ztc_core", Path(__file__).with_name("zcode-task-cleaner.py"))
ztc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ztc)

# 沙箱支持：ZCODE_HOME 重定向整个 ~/.zcode（测试用，正常使用无需设置）
SANDBOX = bool(os.environ.get("ZCODE_HOME"))
if SANDBOX:
    _home = os.path.abspath(os.environ["ZCODE_HOME"])
    ztc.HOME = _home
    ztc.CLI_DIR = os.path.join(_home, ".zcode", "cli")
    ztc.CLI_DB = os.path.join(ztc.CLI_DIR, "db", "db.sqlite")
    ztc.V2_DIR_DEFAULT = os.path.join(_home, ".zcode", "v2")

V2_DIR = ztc.find_v2_dir()
TASKS_DB = os.path.join(V2_DIR, "tasks-index.sqlite")
# 备份放在工具所在目录，便于查找；沙箱模式下留在沙箱内，防止测试备份混入真实备份
BACKUP_ROOT = (os.path.join(ztc.HOME, ".zcode-cleaner-backup") if SANDBOX
               else os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                 ".zcode-cleaner-backup"))


# ---------------------------------------------------------------------------
# 业务逻辑
# ---------------------------------------------------------------------------
def is_client_running():
    """检测 ZCode 桌面客户端是否正在运行。"""
    try:
        if os.name == "nt":
            # 用字节比较，避免控制台 GBK/UTF-8 编码差异导致解码异常
            out = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq ZCode.exe"],
                capture_output=True, timeout=10).stdout or b""
            return b"ZCode.exe" in out
        out = subprocess.run(["pgrep", "-f", "zcode|ZCode"],
                             capture_output=True, timeout=10).stdout or b""
        return bool(out.strip())
    except Exception:
        return False


def session_size(sid):
    """会话磁盘占用 = agents/exec/artifacts + rollout + debug 下的 model-io。"""
    size = ztc.session_file_size(sid)
    dbg = os.path.join(ztc.CLI_DIR, "debug", f"model-io-{sid}.jsonl")
    if os.path.isfile(dbg):
        size += os.path.getsize(dbg)
    return size


def scan_sessions():
    """全量扫描：任务索引 ∪ 会话库。"""
    con = sqlite3.connect(f"file:{TASKS_DB}?mode=ro", uri=True)
    tasks = con.execute(
        "SELECT task_id, title, workspace_path, task_status, archived, deleted,"
        " pinned, created_at, updated_at FROM tasks").fetchall()
    con.close()

    msgs = {}
    if os.path.isfile(ztc.CLI_DB):
        con = sqlite3.connect(f"file:{ztc.CLI_DB}?mode=ro", uri=True)
        msgs = dict(con.execute(
            "SELECT session_id, COUNT(*) FROM message GROUP BY session_id").fetchall())
        con.close()

    now = time.time() * 1000
    result = []
    for tid, title, wp, status, arch, dele, pinned, created, upd in tasks:
        upd = upd or 0
        result.append({
            "id": tid,
            "title": (title or "").strip() or "(无标题)",
            "project": wp or "(未知项目)",
            "status": status or "",
            "archived": bool(arch), "deleted": bool(dele), "pinned": bool(pinned),
            "created_at": created or 0, "updated_at": upd,
            "msgs": msgs.get(tid, 0),
            "size": session_size(tid),
            "protected": (now - upd) / 60000 < PROTECT_MINUTES,
        })
    result.sort(key=lambda s: -s["updated_at"])
    return result


def preview_session(sid):
    """完整对话文本，按消息(role)/时间分组。"""
    if not os.path.isfile(ztc.CLI_DB):
        raise RuntimeError(f"会话库不存在: {ztc.CLI_DB}")
    con = sqlite3.connect(f"file:{ztc.CLI_DB}?mode=ro", uri=True)

    title = ""
    try:
        tcon = sqlite3.connect(f"file:{TASKS_DB}?mode=ro", uri=True)
        try:
            row = tcon.execute(
                "SELECT title FROM tasks WHERE task_id=?", (sid,)).fetchone()
            if row:
                title = (row[0] or "").strip()
        finally:
            tcon.close()
    except Exception:
        pass

    msgs = con.execute(
        "SELECT id, time_created, data FROM message WHERE session_id=? ORDER BY sequence",
        (sid,)).fetchall()
    parts = con.execute(
        "SELECT message_id, data FROM part WHERE session_id=? ORDER BY sequence",
        (sid,)).fetchall()
    con.close()

    by_msg = {}
    for mid, data in parts:
        try:
            d = json.loads(data)
        except Exception:
            continue
        ptype = d.get("type", "")
        text = d.get("text")
        if ptype in ("text", "reasoning") and text:
            by_msg.setdefault(mid, []).append({"type": ptype, "text": text})

    messages = []
    for mid, tcreated, data in msgs:
        try:
            d = json.loads(data)
        except Exception:
            d = {}
        visible = by_msg.get(mid, [])
        if not visible:
            continue
        messages.append({
            "role": d.get("role", "?"),
            "time": tcreated or 0,
            "parts": visible,
        })
    return {"id": sid, "title": title, "messages": messages}


def make_backup():
    """删除前备份两个数据库（含 WAL/SHM）到 ~/.zcode-cleaner-backup/<时间戳>/。"""
    ts = time.strftime("%Y%m%d-%H%M%S")
    bdir = os.path.join(BACKUP_ROOT, ts)
    os.makedirs(bdir, exist_ok=True)
    pairs = [
        (ztc.CLI_DB, "cli-db.sqlite"),
        (TASKS_DB, "tasks-index.sqlite"),
    ]
    saved = []
    for src, name in pairs:
        if os.path.isfile(src):
            shutil.copy2(src, os.path.join(bdir, name))
            saved.append(name)
        for ext in ("-wal", "-shm"):
            if os.path.isfile(src + ext):
                shutil.copy2(src + ext, os.path.join(bdir, name + ext))
    return {"ts": ts, "dir": bdir, "files": saved}


def wash_logs(sids):
    """日志明文清洗：把已删会话 ID 从文本日志中抹除；
    纯会话级日志文件（debug/model-io-<sid>.jsonl）整文件删除。"""
    roots = [
        os.path.join(ztc.CLI_DIR, "log"),
        os.path.join(ztc.CLI_DIR, "debug"),
        os.path.join(ztc.V2_DIR_DEFAULT, "logs"),
        os.path.join(V2_DIR, "logs"),
    ]
    pattern = re.compile("|".join(re.escape(s) for s in sids))
    replaced_files, replacements, deleted_files = 0, 0, []

    for root in roots:
        if not os.path.isdir(root):
            continue
        for name in os.listdir(root):
            path = os.path.join(root, name)
            if not os.path.isfile(path):
                continue
            # 会话专属文件直接删除
            if any(sid in name for sid in sids):
                try:
                    deleted_files.append(name)
                    os.remove(path)
                except OSError:
                    pass
                continue
            if not name.endswith((".jsonl", ".log", ".txt")):
                continue
            try:
                if os.path.getsize(path) > MAX_WASH_SIZE:
                    continue
                with open(path, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read()
                if not pattern.search(content):
                    continue
                n = len(pattern.findall(content))
                with open(path, "w", encoding="utf-8") as f:
                    f.write(pattern.sub("[cleaned]", content))
                replaced_files += 1
                replacements += n
            except OSError:
                continue
    return {"files_washed": replaced_files, "replacements": replacements,
            "session_logs_deleted": deleted_files}


def vacuum_db(path, report, key):
    """WAL checkpoint + VACUUM，消除磁盘残留。"""
    con = None
    try:
        con = sqlite3.connect(path, timeout=30)
        con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        con.execute("VACUUM")
        report[key] = "ok"
    except Exception as e:
        report[key] = f"失败（{e}）"
    finally:
        if con:
            try:
                con.close()
            except Exception:
                pass


def delete_sessions(ids, purge_checkpoints):
    """完整删除管线，返回报告。失败条目不影响其余条目，列表状态以报告为准。"""
    report = {
        "backup": None, "results": [], "vacuum": {},
        "logs": None, "checkpoints": [],
        "total_msgs": 0, "total_freed": 0, "failed": 0,
    }
    report["backup"] = make_backup()

    projects = set()
    for sid in ids:
        con = None
        try:
            con = sqlite3.connect(f"file:{TASKS_DB}?mode=ro", uri=True)
            row = con.execute(
                "SELECT workspace_path FROM tasks WHERE task_id=?", (sid,)).fetchone()
            con.close()
            con = None
            if row and row[0]:
                projects.add(row[0])
            msgs, freed = ztc.hard_delete(sid, V2_DIR)
            # CLI 核心不覆盖 debug 目录，这里补上
            dbg = os.path.join(ztc.CLI_DIR, "debug", f"model-io-{sid}.jsonl")
            if os.path.isfile(dbg):
                freed += os.path.getsize(dbg)
            report["results"].append(
                {"id": sid, "ok": True, "msgs": msgs, "freed": freed})
            report["total_msgs"] += msgs
            report["total_freed"] += freed
        except Exception as e:
            if con:
                try:
                    con.close()
                except Exception:
                    pass
            report["failed"] += 1
            report["results"].append({"id": sid, "ok": False, "error": str(e)})

    report["logs"] = wash_logs(ids)
    vacuum_db(ztc.CLI_DB, report["vacuum"], "cli_db")
    vacuum_db(TASKS_DB, report["vacuum"], "tasks_db")

    if purge_checkpoints and projects:
        report["checkpoints"] = ztc.purge_project_checkpoints(V2_DIR, projects)
    return report


def list_backups():
    """备份目录列表（新→旧）。"""
    out = []
    if os.path.isdir(BACKUP_ROOT):
        for name in os.listdir(BACKUP_ROOT):
            bdir = os.path.join(BACKUP_ROOT, name)
            if not os.path.isdir(bdir) or not re.fullmatch(r"\d{8}-\d{6}", name):
                continue
            files = os.listdir(bdir)
            size = sum(os.path.getsize(os.path.join(bdir, f))
                       for f in files if os.path.isfile(os.path.join(bdir, f)))
            out.append({"ts": name, "files": files, "size": size})
    out.sort(key=lambda b: b["ts"], reverse=True)
    return out


def restore_backup(ts):
    """还原两个数据库。客户端运行时拒绝（避免内存状态写回复活已删数据）。
    沙箱模式（ZCODE_HOME 重定向）下数据与真实客户端无关，跳过该检测。"""
    if not SANDBOX and is_client_running():
        raise RuntimeError("检测到 ZCode 客户端正在运行，请先完全退出客户端再恢复，"
                           "否则客户端会把内存中的状态写回、覆盖还原结果。")
    bdir = os.path.join(BACKUP_ROOT, ts)
    if not os.path.isdir(bdir):
        raise RuntimeError(f"备份不存在: {bdir}")
    restored = []
    for backup_name, target in [("cli-db.sqlite", ztc.CLI_DB),
                                ("tasks-index.sqlite", TASKS_DB)]:
        src = os.path.join(bdir, backup_name)
        if not os.path.isfile(src):
            continue
        os.makedirs(os.path.dirname(target), exist_ok=True)
        # 先清掉目标残留的 WAL/SHM，避免与备份快照不一致
        for ext in ("-wal", "-shm"):
            if os.path.isfile(target + ext):
                os.remove(target + ext)
        shutil.copy2(src, target)
        for ext in ("-wal", "-shm"):
            if os.path.isfile(src + ext):
                shutil.copy2(src + ext, target + ext)
        restored.append(backup_name)
    if not restored:
        raise RuntimeError("备份中没有可还原的数据库文件")
    return {"restored": restored}


# ---------------------------------------------------------------------------
# HTTP 服务
# ---------------------------------------------------------------------------
_state_lock = threading.Lock()   # 删除/恢复串行化，防止并发写


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):   # 安静模式
        pass

    # ---- 基础 ----
    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        data = body if isinstance(body, bytes) else json.dumps(
            body, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _json_body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    # ---- 路由 ----
    def do_GET(self):
        path = self.path.split("?")[0]
        try:
            if path.startswith("/api/"):
                self._api_get(path)
            elif path == "/" or path == "/index.html":
                self._static("index.html", "text/html; charset=utf-8")
            elif path == "/app.css":
                self._static("app.css", "text/css; charset=utf-8")
            elif path == "/app.js":
                self._static("app.js", "application/javascript; charset=utf-8")
            else:
                self._send(404, {"error": "not found"})
        except BrokenPipeError:
            pass
        except Exception as e:
            self._send(500, {"error": str(e)})

    def do_POST(self):
        path = self.path.split("?")[0]
        try:
            body = self._json_body()
            if path == "/api/preview":
                self._send(200, preview_session(body["id"]))
            elif path == "/api/delete":
                ids = body.get("ids") or []
                if not ids:
                    return self._send(400, {"error": "未选择任何会话"})
                with _state_lock:
                    self._send(200, delete_sessions(ids, bool(body.get("purge_checkpoints"))))
            elif path == "/api/restore":
                with _state_lock:
                    self._send(200, restore_backup(body["ts"]))
            else:
                self._send(404, {"error": "not found"})
        except BrokenPipeError:
            pass
        except Exception as e:
            self._send(500, {"error": str(e)})

    def _api_get(self, path):
        if path == "/api/state":
            sessions = scan_sessions()
            self._send(200, {
                "v2_dir": V2_DIR, "cli_db": ztc.CLI_DB,
                "backup_root": BACKUP_ROOT, "running": is_client_running(),
                "protect_minutes": PROTECT_MINUTES,
                "total": len(sessions),
            })
        elif path == "/api/scan":
            self._send(200, {"sessions": scan_sessions()})
        elif path == "/api/backups":
            self._send(200, {"backups": list_backups()})
        else:
            self._send(404, {"error": "not found"})

    def _static(self, name, ctype):
        f = GUI_DIR / name
        self._send(200, f.read_bytes(), ctype)


def main():
    port = 8765
    open_browser = False
    args = sys.argv[1:]
    if "--open" in args:
        open_browser = True
    for i, a in enumerate(args):
        if a == "--port" and i + 1 < len(args):
            port = int(args[i + 1])

    url = f"http://127.0.0.1:{port}"
    print(f"ZCode 会话清理 GUI")
    print(f"  任务索引: {TASKS_DB}")
    print(f"  会话库:   {ztc.CLI_DB}")
    print(f"  备份目录: {BACKUP_ROOT}")
    print(f"  界面:     {url}  (Ctrl+C 退出)")
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已退出")


if __name__ == "__main__":
    main()
