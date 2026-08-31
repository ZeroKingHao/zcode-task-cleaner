#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""生成 GUI 测试沙箱：.test-env/ 下伪造一套完整的 ~/.zcode 数据树。

不触碰任何真实数据，全部是虚构的演示会话。
用法: python tests/make_fixture.py
"""
import json
import os
import shutil
import sqlite3
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..", ".test-env")
HOME = os.path.join(ROOT, "home")
DATA = os.path.join(ROOT, "data")           # 充当 dataBaseDir
V2 = os.path.join(DATA, ".zcode", "v2")
CLI = os.path.join(HOME, ".zcode", "cli")
NOW = int(time.time() * 1000)
DAY = 86400000

# (id 后缀, 项目, 标题, task_status, archived, deleted, 更新时间偏移, 消息数)
SEED = [
    ("aa11", r"G:\demo-app", "demo-app 编写 PRD 文档", "completed", 0, 0, -40 * DAY, 4),
    ("bb22", r"G:\demo-app", "demo-app 修复登录页样式", "error", 0, 0, -12 * DAY, 2),
    ("cc33", r"G:\demo-app", "demo-app 旧需求整理（界面已删）", "completed", 1, 1, -60 * DAY, 2),
    ("dd44", r"G:\other-project", "别的项目-不应被测试触碰", "completed", 0, 0, -5 * DAY, 2),
    ("ee55", r"G:\demo-app", "demo-app 刚刚活跃的会话", "completed", 0, 0, -10 * 60 * 1000, 2),
]


def build():
    if os.path.isdir(ROOT):
        for i in range(5):   # Windows: 句柄释放有延迟，重试删除
            try:
                shutil.rmtree(ROOT)
                break
            except PermissionError:
                if i == 4:
                    raise
                time.sleep(1)

    os.makedirs(os.path.join(HOME, ".zcode", "v2"))
    os.makedirs(os.path.join(V2, "logs"))
    os.makedirs(os.path.join(CLI, "db"))
    os.makedirs(os.path.join(CLI, "log"))
    os.makedirs(os.path.join(CLI, "debug"))
    os.makedirs(os.path.join(CLI, "rollout"))

    # dataBaseDir 重定向
    with open(os.path.join(HOME, ".zcode", "v2", "setting.json"), "w", encoding="utf-8") as f:
        json.dump({"dataBaseDir": DATA}, f)

    # ---- 任务索引 ----
    tdb = os.path.join(V2, "tasks-index.sqlite")
    con = sqlite3.connect(tdb)
    con.executescript("""
    CREATE TABLE tasks (task_id TEXT PRIMARY KEY, title TEXT, workspace_path TEXT,
      task_status TEXT, archived INTEGER, deleted INTEGER, pinned INTEGER,
      created_at INTEGER, updated_at INTEGER);
    CREATE TABLE task_group_members (task_id TEXT, group_id TEXT);
    CREATE TABLE task_group_view_node_orders (node_key TEXT, sort_order INTEGER);
    """)
    for suf, proj, title, status, arch, dele, upd_off, _ in SEED:
        sid = f"sess_{suf}-0000-0000-0000-000000000000"
        con.execute("INSERT INTO tasks VALUES (?,?,?,?,?,?,?,?,?)",
                    (sid, title, proj, status, arch, dele, 0,
                     NOW + upd_off - DAY, NOW + upd_off))
    con.commit(); con.close()

    # ---- 会话库（ztc.hard_delete 涉及的全部表）----
    cdb = os.path.join(CLI, "db", "db.sqlite")
    con = sqlite3.connect(cdb)
    con.executescript("""
    CREATE TABLE message (id TEXT, session_id TEXT, time_created INTEGER,
      time_updated INTEGER, data TEXT, sequence INTEGER);
    CREATE TABLE part (id TEXT, message_id TEXT, session_id TEXT,
      time_created INTEGER, time_updated INTEGER, data TEXT, sequence INTEGER);
    CREATE TABLE todo (session_id TEXT, content TEXT);
    CREATE TABLE session_entry (session_id TEXT, type TEXT);
    CREATE TABLE input_history (session_id TEXT, text TEXT);
    CREATE TABLE model_usage (session_id TEXT, input_tokens INTEGER);
    CREATE TABLE turn_usage (session_id TEXT, turn_id TEXT);
    CREATE TABLE tool_usage (session_id TEXT, tool_name TEXT);
    CREATE TABLE session_input (session_id TEXT, kind TEXT);
    CREATE TABLE session_task_link (parent_session_id TEXT, child_session_id TEXT);
    CREATE TABLE session (id TEXT, title TEXT);
    """)
    for suf, proj, title, status, arch, dele, upd_off, nmsgs in SEED:
        sid = f"sess_{suf}-0000-0000-0000-000000000000"
        for i in range(nmsgs):
            role = "user" if i % 2 == 0 else "assistant"
            mid = f"msg-{suf}-{i}"
            con.execute("INSERT INTO message VALUES (?,?,?,?,?,?)",
                        (mid, sid, NOW + upd_off + i * 1000, NOW + upd_off + i * 1000,
                         json.dumps({"role": role, "time": NOW + upd_off}), i))
            con.execute("INSERT INTO part VALUES (?,?,?,?,?,?,?)",
                        (f"part-{suf}-{i}-0", mid, sid, NOW, NOW,
                         json.dumps({"type": "text",
                                     "text": f"[{role}] {title} 第 {i + 1} 条消息：请帮我把侧边栏改成深色模式。"}), 0))
            if role == "assistant":
                con.execute("INSERT INTO part VALUES (?,?,?,?,?,?,?)",
                            (f"part-{suf}-{i}-1", mid, sid, NOW, NOW,
                             json.dumps({"type": "reasoning",
                                         "text": "用户想要修改样式，我需要先找到对应的组件文件…"}), 1))
        con.execute("INSERT INTO session VALUES (?,?)", (sid, title))
        con.execute("INSERT INTO todo VALUES (?,?)", (sid, "示例待办"))
    con.commit(); con.close()

    # ---- 会话磁盘文件（部分会话有，验证大小统计与删除）----
    for suf, *_rest in SEED[:4]:
        sid = f"sess_{suf}-0000-0000-0000-000000000000"
        for sub in ("agents", "exec", "artifacts"):
            d = os.path.join(CLI, sub, sid)
            os.makedirs(d)
            with open(os.path.join(d, "data.txt"), "w", encoding="utf-8") as f:
                f.write(f"artifact of {sid}\n" * 200)
        with open(os.path.join(CLI, "rollout", f"model-io-{sid}.jsonl"), "w", encoding="utf-8") as f:
            f.write(json.dumps({"session": sid, "content": "模型 IO 记录"}) + "\n" * 500)
        with open(os.path.join(CLI, "debug", f"model-io-{sid}.jsonl"), "w", encoding="utf-8") as f:
            f.write(json.dumps({"session": sid, "debug": True}) + "\n" * 100)

    # ---- 日志明文（含全部会话 ID，验证清洗）----
    all_ids = [f"sess_{s[0]}-0000-0000-0000-000000000000" for s in SEED]
    with open(os.path.join(CLI, "log", "zcode-2026-08-30.jsonl"), "w", encoding="utf-8") as f:
        for sid in all_ids:
            f.write(json.dumps({"ts": 1, "msg": f"session {sid} started"}) + "\n")
    with open(os.path.join(V2, "logs", "2026-08-30.log"), "w", encoding="utf-8") as f:
        for sid in all_ids:
            f.write(f"[info] task {sid} updated\n")

    # ---- 检查点（demo-app 项目，任务删光后可被 purge）----
    cp = os.path.join(V2, "checkpoints", "hash0001")
    os.makedirs(cp)
    with open(os.path.join(cp, "state.json"), "w", encoding="utf-8") as f:
        json.dump({"workspacePath": r"G:\demo-app"}, f)

    print(f"沙箱已生成: {os.path.abspath(ROOT)}")
    print(f"  ZCODE_HOME={os.path.abspath(HOME)}")
    for suf, proj, title, *_ in SEED:
        print(f"  sess_{suf}…  {proj:<18} {title}")


if __name__ == "__main__":
    build()
