#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ZCode 会话清理 GUI 自动化测试（Playwright，仅针对沙箱 http://127.0.0.1:8977）。

前置:
  python tests/make_fixture.py
  ZCODE_HOME=<repo>/.test-env/home python gui_server.py --port 8977
"""
import json
import os
import re
import sys
import urllib.request

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8977"
HERE = os.path.dirname(os.path.abspath(__file__))
SHOTS = os.path.join(HERE, "shots")
SANDBOX = os.path.join(HERE, "..", ".test-env")

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'✅' if cond else '❌'} {name}" + (f"  [{detail}]" if detail and not cond else ""))


def api(path):
    with urllib.request.urlopen(BASE + path) as r:
        return json.load(r)


def main():
    os.makedirs(SHOTS, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 860})
        page.goto(BASE)
        page.wait_for_load_state("networkidle")
        page.wait_for_selector(".session", timeout=8000)

        # ---- 1. 扫描与统计 ----
        print("\n[1] 全量扫描与统计卡")
        check("统计卡-全部=5", page.text_content("#st-total").strip() == "5")
        check("统计卡-活跃=4", page.text_content("#st-active").strip() == "4")
        check("统计卡-归档=0", page.text_content("#st-archived").strip() == "0")
        check("统计卡-已删=1", page.text_content("#st-deleted").strip() == "1")
        check("列表渲染 5 行", page.locator(".session").count() == 5)
        check("客户端状态 pill", "客户端" in page.text_content("#running-pill"))
        # 状态图例
        legend = page.text_content(".legend")
        check("图例-已删含义", "数据仍在磁盘" in legend)
        check("图例-使用中含义", "锁定防误删" in legend)
        check("图例-无徽章=活跃", "无徽章 = 活跃" in legend)
        check("徽章悬停提示", "软删除" in page.locator(".session .badge.deleted")
              .first.get_attribute("title"))
        check("作用域悬停提示", "软删除" in page.locator(
            "#scope-seg button[data-scope=deleted]").get_attribute("title"))
        page.screenshot(path=os.path.join(SHOTS, "01-light.png"), full_page=True)

        # ---- 2. 作用域过滤 ----
        print("\n[2] 作用域过滤")
        page.click("#scope-seg button[data-scope=deleted]")
        page.wait_for_timeout(200)
        check("已删作用域=1 行", page.locator(".session").count() == 1)
        check("已删行含「已删」徽章", "已删" in page.text_content(".session"))
        page.click("#scope-seg button[data-scope=all]")
        page.wait_for_timeout(200)
        check("回到全部=5 行", page.locator(".session").count() == 5)

        # ---- 3. 搜索 + 保护 ----
        print("\n[3] 搜索与使用中保护")
        page.fill("#search", "demo-app")
        page.wait_for_timeout(400)
        check("搜索 demo-app=4 行", page.locator(".session").count() == 4)
        prot = page.locator(".session.protected")
        check("1 个「使用中」会话", prot.count() == 1)
        check("使用中会话不可勾选", not prot.locator("input[type=checkbox]").is_visible())
        page.fill("#search", "")
        page.wait_for_timeout(400)

        # ---- 4. 快捷筛选 ----
        print("\n[4] 快捷筛选")
        page.click(".chip[data-quick=error]")
        page.wait_for_timeout(200)
        rows = page.locator(".session")
        check("已出错=1 行", rows.count() == 1)
        check("该行有「已出错」徽章", "已出错" in rows.first.text_content())
        page.click("#qf-clear")
        page.wait_for_timeout(200)
        check("清除筛选=5 行", page.locator(".session").count() == 5)

        # ---- 5. 预览 ----
        print("\n[5] 内容预览")
        page.locator('.session', has_text="修复登录页样式").locator(".btn-eye").click()
        page.wait_for_selector("#modal-preview:not([hidden])")
        page.wait_for_timeout(400)
        chat = page.text_content("#pv-chat")
        check("预览含用户消息", "[user]" in chat)
        check("预览含助手消息", "[assistant]" in chat)
        check("预览含思考过程折叠", "思考过程" in chat)
        page.screenshot(path=os.path.join(SHOTS, "02-preview.png"))
        page.keyboard.press("Escape")
        page.wait_for_timeout(200)
        check("Esc 关闭预览", page.locator("#modal-preview").is_hidden())

        # ---- 6. 深色模式 ----
        print("\n[6] 深色模式")
        page.click("#btn-theme"); page.wait_for_timeout(150)   # system -> light
        page.click("#btn-theme"); page.wait_for_timeout(150)   # light -> dark
        check("html 变为深色", page.get_attribute("html", "data-theme") == "dark")
        page.screenshot(path=os.path.join(SHOTS, "03-dark.png"), full_page=True)
        page.click("#btn-theme"); page.wait_for_timeout(150)   # dark -> system(浅)
        check("主题可循环回系统", page.get_attribute("html", "data-theme") == "light")

        # ---- 7. 确认弹窗取消路径 ----
        print("\n[7] 确认弹窗-取消")
        aa = page.locator('.session', has_text="编写 PRD 文档")
        aa.locator("input[type=checkbox]").check()
        page.wait_for_timeout(150)
        check("底部操作栏出现", page.locator("#actionbar").is_visible())
        check("已选 1 项", page.text_content("#sel-count").strip() == "1")
        page.click("#btn-delete")
        page.wait_for_selector("#modal-confirm:not([hidden])")
        page.wait_for_timeout(450)   # 等入场动画（0.28s）完成再截图
        check("确认框列出会话", "编写 PRD 文档" in page.text_content("#cf-body"))
        check("确认框说明删除范围", "VACUUM" in page.text_content("#cf-body"))
        page.screenshot(path=os.path.join(SHOTS, "04-confirm.png"))
        page.click("#modal-confirm [data-close]")
        page.wait_for_timeout(200)
        check("取消后弹窗关闭", page.locator("#modal-confirm").is_hidden())
        check("取消后列表不变", page.locator(".session").count() == 5)

        # ---- 8. 批量删除（勾选清理检查点）----
        print("\n[8] 批量硬删除")
        bb = page.locator('.session', has_text="修复登录页样式")
        bb.locator("input[type=checkbox]").check()
        page.check("#opt-purge-cp")
        page.wait_for_timeout(150)
        check("已选 2 项", page.text_content("#sel-count").strip() == "2")
        page.click("#btn-delete")
        page.wait_for_selector("#modal-confirm:not([hidden])")
        page.click("#cf-ok")
        page.wait_for_selector("#modal-report:not([hidden])", timeout=60000)
        page.wait_for_timeout(300)
        body = page.text_content("#rp-body")
        check("报告-2 个成功", "成功 2 / 2" in body)
        check("报告-消息数 6", "6 条消息" in body)
        check("报告-VACUUM 成功", body.count("✅") == 2)
        check("报告-日志清洗", "抹除" in body)
        page.screenshot(path=os.path.join(SHOTS, "05-report.png"))
        page.click("#modal-report [data-close]")
        page.wait_for_timeout(600)
        check("删除后剩 3 行", page.locator(".session").count() == 3)
        check("已选清空、操作栏隐藏", page.locator("#actionbar").is_hidden())

        # ---- 9. 底层验证：库/文件/日志 ----
        print("\n[9] 底层数据验证")
        scan = api("/api/scan")["sessions"]
        ids = {s["id"] for s in scan}
        check("索引库已删 2 行", len(scan) == 3)
        cli = os.path.join(SANDBOX, "home", ".zcode", "cli")
        check("rollout 文件已删",
              not os.path.exists(os.path.join(cli, "rollout", "model-io-sess_aa11-0000-0000-0000-000000000000.jsonl")))
        check("debug 副本已删",
              not os.path.exists(os.path.join(cli, "debug", "model-io-sess_aa11-0000-0000-0000-000000000000.jsonl")))
        check("agents 目录已删", not os.path.exists(os.path.join(cli, "agents", "sess_aa11-0000-0000-0000-000000000000")))
        log = open(os.path.join(cli, "log", "zcode-2026-08-30.jsonl"), encoding="utf-8").read()
        check("日志明文已清洗", "sess_aa11" not in log and "[cleaned]" in log)
        check("日志保留其他会话", "sess_dd44" in log)
        cp = os.path.join(SANDBOX, "data", ".zcode", "v2", "checkpoints", "hash0001")
        check("项目未清零-检查点保留", os.path.isdir(cp))

        # ---- 10. 备份与恢复 ----
        print("\n[10] 备份恢复")
        page.click("#btn-backups")
        page.wait_for_selector("#modal-backups:not([hidden])")
        page.wait_for_timeout(400)
        items = page.locator(".backup-item")
        check("备份列表 ≥1 条", items.count() >= 1)
        check("备份含两个库", "cli-db.sqlite" in items.first.text_content())
        page.screenshot(path=os.path.join(SHOTS, "06-backups.png"))
        page.once("dialog", lambda d: d.accept())
        items.first.locator("button").click()
        page.wait_for_timeout(1500)
        page.wait_for_selector("#modal-backups[hidden]", state="attached", timeout=10000)
        page.wait_for_timeout(600)
        check("恢复后回到 5 行", page.locator(".session").count() == 5)
        check("恢复后统计卡=5", page.text_content("#st-total").strip() == "5")
        page.screenshot(path=os.path.join(SHOTS, "07-restored.png"), full_page=True)

        browser.close()

    print(f"\n========== 结果: {len(PASS)} 通过 / {len(FAIL)} 失败 ==========")
    if FAIL:
        print("失败项:", FAIL)
        sys.exit(1)


if __name__ == "__main__":
    main()
