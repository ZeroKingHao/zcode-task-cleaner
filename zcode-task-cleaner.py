#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
ZCode 会话硬删除工具
====================
列出 / 预览 / 彻底删除 ZCode 的会话数据（任务索引 + 会话库 + 磁盘文件）。

存储结构（工具自动发现，无需配置）:
  - 任务索引: <dataBaseDir>\.zcode\v2\tasks-index.sqlite   (dataBaseDir 读自 ~/.zcode/v2/setting.json)
  - 会话库:   ~/.zcode/cli/db/db.sqlite
  - 会话文件: ~/.zcode/cli/{agents,exec,artifacts}/<sess_id>  和  rollout/model-io-<sess_id>.jsonl
  - 检查点:   <v2>\checkpoints\<hash>\  (按项目组织, 依据 state.json 的 workspacePath)

用法:
  python zcode-task-cleaner.py list  [--scope 作用域] [--project 子串] [--older-than 天] [--task ID ...]
  python zcode-task-cleaner.py delete [同上过滤] [--export 目录] [--purge-checkpoints] [--yes]

  --scope: active=活跃任务(默认) / archived=仅归档 / deleted=界面已删除(软删除) / all=全部
  delete 不加 --yes 时只做预览（dry-run），不删除任何数据。

示例:
  python zcode-task-cleaner.py list --scope deleted                 # 看界面已删的任务
  python zcode-task-cleaner.py delete --scope deleted               # 预览清理它们
  python zcode-task-cleaner.py delete --scope deleted --yes         # 真正硬删除
  python zcode-task-cleaner.py delete --project myapp --scope all --export ./backup --yes
  python zcode-task-cleaner.py delete --older-than 30 --scope all --yes   # 清理 30 天前的所有任务
"""
import argparse
import json
import os
import re
import shutil
import sqlite3
import sys
import time

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HOME = os.path.expanduser("~")
CLI_DIR = os.path.join(HOME, ".zcode", "cli")
V2_DIR_DEFAULT = os.path.join(HOME, ".zcode", "v2")
CLI_DB = os.path.join(CLI_DIR, "db", "db.sqlite")

# db.sqlite 中与会话关联的表 -> 按 session id 删除
RELATED_TABLES = [
    ("message", "session_id"), ("part", "session_id"), ("todo", "session_id"),
    ("session_entry", "session_id"), ("input_history", "session_id"),
    ("model_usage", "session_id"), ("turn_usage", "session_id"),
    ("tool_usage", "session_id"), ("session_input", "session_id"),
    ("session_task_link", "parent_session_id"), ("session_task_link", "child_session_id"),
    ("session", "id"),
]


def find_v2_dir():
    """定位 tasks-index.sqlite 所在的 v2 目录（支持 dataBaseDir 重定向）。"""
    sj = os.path.join(V2_DIR_DEFAULT, "setting.json")
    if os.path.isfile(sj):
        try:
            base = json.load(open(sj, encoding="utf-8")).get("dataBaseDir")
            if base:
                v2 = os.path.join(base, ".zcode", "v2")
                if os.path.isfile(os.path.join(v2, "tasks-index.sqlite")):
                    return v2
        except Exception:
            pass
    return V2_DIR_DEFAULT


def fmt_size(n):
    if n >= 1048576:
        return f"{n / 1048576:.1f}M"
    if n >= 1024:
        return f"{n / 1024:.0f}K"
    return f"{n}B"


def fmt_time(ms):
    try:
        return time.strftime("%m-%d %H:%M", time.localtime(ms / 1000))
    except Exception:
        return "?"


def dir_size(path):
    total = 0
    for root, _, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


def session_file_size(sid):
    """会话在 agents/exec/artifacts/rollout 下的磁盘占用。"""
    size = 0
    for sub in ("agents", "exec", "artifacts"):
        p = os.path.join(CLI_DIR, sub, sid)
        if os.path.isdir(p):
            size += dir_size(p)
    rf = os.path.join(CLI_DIR, "rollout", f"model-io-{sid}.jsonl")
    if os.path.isfile(rf):
        size += os.path.getsize(rf)
    return size


def load_sessions(v2_dir):
    """合并任务索引 + 会话库，返回全部会话信息列表。"""
    con = sqlite3.connect(f"file:{os.path.join(v2_dir, 'tasks-index.sqlite')}?mode=ro", uri=True)
    tasks = con.execute(
        "SELECT task_id, title, workspace_path, archived, deleted, updated_at FROM tasks"
    ).fetchall()
    con.close()

    msgs = {}
    if os.path.isfile(CLI_DB):
        con = sqlite3.connect(f"file:{CLI_DB}?mode=ro", uri=True)
        for sid, cnt in con.execute("SELECT session_id, COUNT(*) FROM message GROUP BY session_id"):
            msgs[sid] = cnt
        con.close()

    now = time.time() * 1000
    result = []
    for tid, title, wp, arch, dele, upd in tasks:
        result.append({
            "id": tid, "title": (title or "").strip() or "(无标题)",
            "project": wp or "(未知项目)", "archived": bool(arch), "deleted": bool(dele),
            "updated_at": upd or 0, "msgs": msgs.get(tid, 0),
            "size": session_file_size(tid), "age_min": max(0, (now - (upd or 0)) / 60000),
        })
    result.sort(key=lambda s: -s["updated_at"])
    return result


def status_label(s):
    return "已删" if s["deleted"] else ("归档" if s["archived"] else "活跃")


def apply_filters(sess, args):
    now_ms = time.time() * 1000

    def keep(s):
        # 明确指定了会话 ID 时，忽略 scope 限制
        if not args.task:
            scope = args.scope
            if scope == "active" and (s["archived"] or s["deleted"]):
                return False
            if scope == "archived" and (not s["archived"] or s["deleted"]):
                return False
            if scope == "deleted" and not s["deleted"]:
                return False
        if args.project and args.project.lower() not in s["project"].lower():
            return False
        if args.older_than is not None:
            if s["updated_at"] > now_ms - args.older_than * 86400000:
                return False
        if args.task:
            if not any(s["id"].startswith(t) for t in args.task):
                return False
        return True

    selected = [s for s in sess if keep(s)]
    # 保护：最近 keep_recent 分钟内活跃的会话一律跳过（防止删除正在使用的会话）
    protected, safe = [], []
    for s in selected:
        (protected if s["age_min"] < args.keep_recent else safe).append(s)
    return safe, protected


def print_table(rows, title):
    print(f"\n=== {title}（{len(rows)} 个） ===")
    if not rows:
        print("  （空）")
        return
    print(f"{'状态':<4} {'更新时间':<12} {'消息':>5} {'占用':>8}  {'项目':<28} 标题 / ID")
    print("-" * 110)
    for s in rows:
        proj = s["project"].replace("\\", "/").split("/")[-1] or s["project"]
        print(f"{status_label(s):<4} {fmt_time(s['updated_at']):<12} {s['msgs']:>5} "
              f"{fmt_size(s['size']):>8}  {proj[:26]:<28} {s['title'][:38]}")
        print(f"      {s['id']}")
    tm = sum(s["msgs"] for s in rows)
    ts = sum(s["size"] for s in rows)
    print("-" * 110)
    print(f"合计: {len(rows)} 个会话, {tm} 条消息, {fmt_size(ts)} 文件")


def export_session(sid, out_dir):
    """删除前把会话全部消息导出为 JSON（安全网）。"""
    if not os.path.isfile(CLI_DB):
        return None
    con = sqlite3.connect(f"file:{CLI_DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        messages = [dict(r) for r in con.execute(
            "SELECT * FROM message WHERE session_id=?", (sid,))]
        parts = [dict(r) for r in con.execute(
            "SELECT * FROM part WHERE session_id=?", (sid,))]
    finally:
        con.close()
    path = os.path.join(out_dir, f"{sid}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"session_id": sid, "messages": messages, "parts": parts},
                  f, ensure_ascii=False, indent=1)
    return path


def hard_delete(sid, v2_dir):
    """硬删除单个会话：两个库 + 磁盘文件。返回 (删掉的消息数, 释放的字节数)。"""
    # 1. 会话库（事务，失败抛异常）
    msgs = 0
    con = sqlite3.connect(CLI_DB, timeout=15)
    try:
        cur = con.cursor()
        cur.execute("BEGIN IMMEDIATE")
        msgs = cur.execute(
            "SELECT COUNT(*) FROM message WHERE session_id=?", (sid,)).fetchone()[0]
        for table, col in RELATED_TABLES:
            cur.execute(f"DELETE FROM {table} WHERE {col}=?", (sid,))
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()

    # 2. 任务索引
    con = sqlite3.connect(os.path.join(v2_dir, "tasks-index.sqlite"), timeout=15)
    try:
        con.execute("BEGIN IMMEDIATE")
        con.execute("DELETE FROM tasks WHERE task_id=?", (sid,))
        con.execute("DELETE FROM task_group_members WHERE task_id=?", (sid,))
        con.execute("DELETE FROM task_group_view_node_orders WHERE node_key=?", (sid,))
        con.commit()
    finally:
        con.close()

    # 3. 磁盘文件
    freed = 0
    for sub in ("agents", "exec", "artifacts"):
        p = os.path.join(CLI_DIR, sub, sid)
        if os.path.isdir(p):
            freed += dir_size(p)
            shutil.rmtree(p, ignore_errors=True)
    rf = os.path.join(CLI_DIR, "rollout", f"model-io-{sid}.jsonl")
    if os.path.isfile(rf):
        freed += os.path.getsize(rf)
        try:
            os.remove(rf)
        except OSError:
            pass
    return msgs, freed


def purge_project_checkpoints(v2_dir, projects):
    """若某项目已无任何任务，删除其 checkpoints 目录。"""
    cp_dir = os.path.join(v2_dir, "checkpoints")
    if not os.path.isdir(cp_dir):
        return []
    con = sqlite3.connect(
        f"file:{os.path.join(v2_dir, 'tasks-index.sqlite')}?mode=ro", uri=True)
    remaining = {r[0] for r in con.execute("SELECT DISTINCT workspace_path FROM tasks")}
    con.close()
    purged = []
    for name in os.listdir(cp_dir):
        sj = os.path.join(cp_dir, name, "state.json")
        if not os.path.isfile(sj):
            continue
        try:
            wp = json.load(open(sj, encoding="utf-8")).get("workspacePath")
        except Exception:
            continue
        if wp in projects and wp not in remaining:
            shutil.rmtree(os.path.join(cp_dir, name), ignore_errors=True)
            purged.append(f"{name} ({wp})")
    return purged


def main():
    ap = argparse.ArgumentParser(description="ZCode 会话硬删除工具")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add_filters(p):
        p.add_argument("--scope", choices=["active", "archived", "deleted", "all"],
                       default="active",
                       help="作用域: active=活跃(默认) archived=仅归档 deleted=界面已删 all=全部")
        p.add_argument("--project", help="按项目路径子串过滤（不区分大小写）")
        p.add_argument("--task", nargs="+", help="指定会话 ID（sess_xxx，支持前缀）")
        p.add_argument("--older-than", type=float, metavar="天", help="只处理 N 天前更新的")
        p.add_argument("--keep-recent", type=float, default=60, metavar="分钟",
                       help="跳过最近 N 分钟活跃的会话，防止误删正在使用的（默认 60，0 关闭）")

    add_filters(sub.add_parser("list", help="列出会话"))
    dp = sub.add_parser("delete", help="硬删除会话（默认 dry-run 预览）")
    add_filters(dp)
    dp.add_argument("--export", metavar="目录", help="删除前把会话消息导出为 JSON")
    dp.add_argument("--purge-checkpoints", action="store_true",
                    help="项目任务清零时顺带删除该项目的检查点目录")
    dp.add_argument("--yes", action="store_true", help="真正执行删除（不加则只预览）")

    args = ap.parse_args()
    v2_dir = find_v2_dir()
    print(f"任务索引: {os.path.join(v2_dir, 'tasks-index.sqlite')}")
    print(f"会话库:   {CLI_DB}")

    sessions = load_sessions(v2_dir)
    print(f"共发现 {len(sessions)} 个会话 "
          f"(活跃 {sum(1 for s in sessions if not s['archived'] and not s['deleted'])} / "
          f"归档 {sum(1 for s in sessions if s['archived'] and not s['deleted'])} / "
          f"界面已删 {sum(1 for s in sessions if s['deleted'])})")

    if args.cmd == "list":
        rows, hidden = apply_filters(sessions, args)
        print_table(rows, "符合条件的会话")
        if hidden:
            print(f"\n(另有 {len(hidden)} 个最近 {args.keep_recent:.0f} 分钟内活跃的会话受保护未列出)"
                  if args.keep_recent else "")
        return

    # delete：先预览
    rows, protected = apply_filters(sessions, args)
    print_table(rows, "将被硬删除的会话")
    if protected:
        print(f"\n[保护] {len(protected)} 个最近活跃的会话已跳过:")
        for s in protected:
            print(f"  - {fmt_time(s['updated_at'])} {s['title'][:40]}")
    if not rows:
        print("\n没有符合条件的会话，结束。")
        return

    if not args.yes:
        print("\n[DRY-RUN] 以上为预览。确认无误后加 --yes 执行删除。")
        print(f"  python {sys.argv[0]} delete <相同过滤参数> --yes")
        return

    if args.export:
        os.makedirs(args.export, exist_ok=True)
        print(f"\n导出消息到: {args.export}")

    total_msgs, total_freed, failed = 0, 0, []
    for s in rows:
        try:
            if args.export:
                export_session(s["id"], args.export)
            msgs, freed = hard_delete(s["id"], v2_dir)
            total_msgs += msgs
            total_freed += freed
            print(f"[已删] {s['title'][:40]} ({msgs} 条消息, {fmt_size(freed)})")
        except Exception as e:
            failed.append(s["id"])
            print(f"[失败] {s['title'][:40]}: {e}")

    if args.purge_checkpoints:
        purged = purge_project_checkpoints(v2_dir, {s["project"] for s in rows})
        for p in purged:
            print(f"[检查点已清] {p}")

    print(f"\n完成: 删除 {len(rows) - len(failed)}/{len(rows)} 个会话, "
          f"{total_msgs} 条消息, 释放 {fmt_size(total_freed)} 文件空间")
    if failed:
        print(f"失败 {len(failed)} 个（多为数据库被占用，关闭 ZCode 客户端后重试）: {failed}")
    print("提示: 重启 ZCode 客户端以刷新其内存中的任务列表缓存。")


if __name__ == "__main__":
    main()
