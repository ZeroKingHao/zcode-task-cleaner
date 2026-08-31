#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""真实环境冒烟测试（连接真实 ZCode 数据，默认 http://127.0.0.1:8765）。

⚠️ 运行前必须先配置下方三个常量。本脚本会：
  1. 只读验证：全量扫描、按授权项目过滤、预览
  2. 真实删除：授权项目下、TARGET_TITLE 匹配的那一个会话（删除前工具会自动备份）
  3. 核验：总数 -1、授权项目剩 KEEP_TITLE 那个会话、其他所有项目零触碰
  4. 恢复保护：客户端运行中时恢复请求应被拒绝

请只对你自己拥有、且愿意承担删除后果的项目运行本脚本。
"""
import json
import os
import sys
import urllib.request

from playwright.sync_api import sync_playwright

# ======== 运行前必改：授权测试的范围 ========
AUTHORIZED_PROJECT = "demo-app"     # 你拥有的项目路径子串（不区分大小写），删除仅限该项目
TARGET_TITLE = "要删除的会话标题子串"   # 该项目下允许删除的一个会话（标题唯一子串）
KEEP_TITLE = "应保留的会话标题子串"     # 该项目下另一个会话，用于验证不误删
# ===========================================

BASE = "http://127.0.0.1:8765"
HERE = os.path.dirname(os.path.abspath(__file__))
SHOTS = os.path.join(HERE, "shots")

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'✅' if cond else '❌'} {name}" + (f"  [{detail}]" if detail and not cond else ""))


def api(path):
    with urllib.request.urlopen(BASE + path, timeout=120) as r:
        return json.load(r)


def main():
    if AUTHORIZED_PROJECT == "demo-app":
        sys.exit("请先在脚本顶部配置 AUTHORIZED_PROJECT / TARGET_TITLE / KEEP_TITLE，"
                 "再对真实数据运行。")

    before = api("/api/scan")["sessions"]
    proj_before = [s for s in before if AUTHORIZED_PROJECT.lower() in s["project"].lower()]
    print(f"真实环境: 共 {len(before)} 个会话, 授权项目 {len(proj_before)} 个")
    check("客户端运行检测为 True", api("/api/state")["running"] is True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 860})
        page.goto(BASE)
        page.wait_for_load_state("networkidle")
        page.wait_for_selector(".session", timeout=15000)
        page.screenshot(path=os.path.join(SHOTS, "10-real.png"), full_page=True)

        # ---- 只读：统计与过滤 ----
        print("\n[1] 只读验证")
        check(f"统计卡-全部={len(before)}",
              page.text_content("#st-total").strip() == str(len(before)))
        page.fill("#search", AUTHORIZED_PROJECT)
        page.wait_for_timeout(500)
        rows = page.locator(".session")
        check("搜索授权项目行数正确", rows.count() == len(proj_before),
              f"实际 {rows.count()}")

        # ---- 只读：预览 ----
        row = page.locator(".session", has_text=TARGET_TITLE)
        check("定位到目标会话行", row.count() == 1)
        row.locator(".btn-eye").click()
        page.wait_for_selector("#modal-preview:not([hidden])")
        page.wait_for_timeout(800)
        chat = page.text_content("#pv-chat")
        check("预览加载出对话内容", len(chat.strip()) > 50, f"长度 {len(chat.strip())}")
        page.keyboard.press("Escape")
        page.wait_for_timeout(300)

        # ---- 授权删除：仅目标会话 ----
        print("\n[2] 授权删除（1 个会话）")
        row.locator("input[type=checkbox]").check()
        page.wait_for_timeout(200)
        check("已选 1 项", page.text_content("#sel-count").strip() == "1")
        page.click("#btn-delete")
        page.wait_for_selector("#modal-confirm:not([hidden])")
        page.wait_for_timeout(450)
        check("确认框含客户端运行警告", page.locator("#cf-running").is_visible())
        page.click("#cf-ok")
        page.wait_for_selector("#modal-report:not([hidden])", timeout=120000)  # VACUUM 大库需时间
        page.wait_for_timeout(300)
        body = page.text_content("#rp-body")
        print("  报告:", body.replace("\n", " ")[:200])
        check("报告-删除成功", "成功 1 / 1" in body, body[:120])
        page.click("#modal-report [data-close]")
        page.wait_for_timeout(800)

        # ---- 删除后核验 ----
        print("\n[3] 删除后核验")
        after = api("/api/scan")["sessions"]
        proj_after = [s for s in after if AUTHORIZED_PROJECT.lower() in s["project"].lower()]
        check("总数 -1", len(after) == len(before) - 1, f"{len(before)} -> {len(after)}")
        check("授权项目剩 1 个", len(proj_after) == 1)
        check("同项目另一会话保留", any(KEEP_TITLE in s["title"] for s in proj_after))
        # 其他项目零触碰
        kw = AUTHORIZED_PROJECT.lower()
        other_ids_before = {s["id"] for s in before if kw not in s["project"].lower()}
        other_ids_after = {s["id"] for s in after if kw not in s["project"].lower()}
        check("其他项目会话零触碰", other_ids_before == other_ids_after)

        # ---- 备份核验 ----
        backups = api("/api/backups")["backups"]
        check("自动备份已生成", len(backups) >= 1)
        bdir = backups[0]
        check("备份包含两个库",
              "cli-db.sqlite" in bdir["files"] and "tasks-index.sqlite" in bdir["files"])
        print(f"  备份: {bdir['ts']}  {bdir['size']/1048576:.1f} MB")

        # ---- 恢复保护（客户端运行中应拒绝）----
        print("\n[4] 恢复保护验证（客户端运行中，应拒绝并给出原因）")
        page.fill("#search", "")
        page.wait_for_timeout(400)
        page.click("#btn-backups")
        page.wait_for_selector("#modal-backups:not([hidden])")
        page.wait_for_timeout(500)
        page.once("dialog", lambda d: d.accept())
        page.locator(".backup-item").first.locator("button").click()
        page.wait_for_timeout(1500)
        toast = page.text_content("#toast")
        check("恢复被拒绝且说明原因", "客户端" in toast and "恢复失败" in toast, toast)
        browser.close()

    print(f"\n========== 结果: {len(PASS)} 通过 / {len(FAIL)} 失败 ==========")
    if FAIL:
        print("失败项:", FAIL)
        sys.exit(1)


if __name__ == "__main__":
    main()
