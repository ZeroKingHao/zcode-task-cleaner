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
import sqlite3
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
        page.wait_for_timeout(900)   # 等统计数字 count-up 动画结束

        # ---- 1. 扫描与统计 ----
        print("\n[1] 全量扫描与统计卡")
        check("统计卡-全部=5", page.text_content("#st-total").strip() == "5")
        check("统计卡-活跃=4", page.text_content("#st-active").strip() == "4")
        check("统计卡-归档=0", page.text_content("#st-archived").strip() == "0")
        check("统计卡-已删=1", page.text_content("#st-deleted").strip() == "1")
        check("列表渲染 5 行", page.locator(".session").count() == 5)
        check("客户端状态 pill", "客户端" in page.text_content("#running-pill"))
        # 状态说明（已收进「?」帮助弹层）
        page.click("#btn-help")
        page.wait_for_selector("#modal-help:not([hidden])")
        page.wait_for_timeout(300)
        help_text = page.text_content("#modal-help")
        check("图例-已删含义", "数据仍" in help_text and "已删" in help_text)
        check("图例-使用中含义", "防误删" in help_text)
        check("图例-无徽章=活跃", "无徽章" in help_text and "活跃会话" in help_text)
        page.keyboard.press("Escape")
        page.wait_for_timeout(200)
        check("徽章悬停提示", "软删除" in page.locator(".session .badge.deleted")
              .first.get_attribute("title"))
        check("作用域悬停提示", "软删除" in page.locator(
            "#scope-seg button[data-scope=deleted]").get_attribute("title"))
        page.screenshot(path=os.path.join(SHOTS, "01-light.png"), full_page=True)

        # ---- 1b. 表头与排序 ----
        print("\n[1b] 表头与排序")
        head = page.text_content("#list-head")
        check("表头-四列齐全", all(k in head for k in
              ("标题", "消息数", "最后更新时间", "内存占用")))
        check("默认按更新时间降序", "刚刚活跃" in page.locator(".session:not(.child)").first.text_content())
        check("默认排序列高亮", "updated_at" in page.locator(
            "#list-head .th-sort.on").get_attribute("data-sort"))
        page.click('.th-sort[data-sort=msgs]')          # 消息数降序：aa11(10 条) 第一
        page.wait_for_timeout(250)
        check("消息数降序-最多在前", "编写 PRD" in page.locator(".session:not(.child)").first.text_content())
        page.click('.th-sort[data-sort=msgs]')          # 切升序：aa11 最少排最后
        page.wait_for_timeout(250)
        check("消息数升序-最多在后", "编写 PRD" in page.locator(".session:not(.child)").last.text_content())
        page.click('.th-sort[data-sort=updated_at]')    # 切到时间列（默认降序）
        page.click('.th-sort[data-sort=updated_at]')    # 再点切升序：cc33(60 天) 第一
        page.wait_for_timeout(250)
        check("时间升序-最旧在前", "旧需求整理" in page.locator(".session:not(.child)").first.text_content())
        check("升序箭头", "▲" in page.locator('.th-sort[data-sort=updated_at]').text_content())
        page.click('.th-sort[data-sort=title]')         # 标题拼音升序：别的(bié) < demo-app(d) < …修复(xiū)
        page.wait_for_timeout(250)
        rows_t = page.locator(".session:not(.child)")
        check("标题拼音排序", "别的项目" in rows_t.first.text_content()
              and "修复登录" in rows_t.last.text_content())
        page.click('.th-sort[data-sort=updated_at]')    # 恢复默认：时间降序
        page.wait_for_timeout(250)
        check("恢复时间降序", "刚刚活跃" in page.locator(".session:not(.child)").first.text_content())

        # ---- 1c. 冻结窗格：下滑时「全选/摘要、使用说明、表头」三行整体吸顶 ----
        print("\n[1c] 冻结窗格")
        check("头部两行结构", page.locator("#list-head .lh-tools").count() == 1
              and page.locator("#list-head .lh-cols").count() == 1)
        page.set_viewport_size({"width": 1280, "height": 430})
        page.wait_for_timeout(200)
        page.evaluate("() => { document.querySelector('.list-wrap').scrollTop = 500; }")
        page.wait_for_timeout(300)
        tops = page.evaluate("""() => ({
            scrolled: document.querySelector('.list-wrap').scrollTop,
            wrap: document.querySelector('.list-wrap').getBoundingClientRect().top,
            head: document.querySelector('#list-head').getBoundingClientRect().top })""")
        check("下滑后头部仍吸顶", tops["scrolled"] > 50 and abs(tops["head"] - tops["wrap"]) < 2,
              str(tops))
        page.screenshot(path=os.path.join(SHOTS, "01c-frozen.png"))
        page.set_viewport_size({"width": 1280, "height": 860})
        page.evaluate("() => { document.querySelector('.list-wrap').scrollTop = 0; }")
        page.wait_for_timeout(200)

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

        # ---- 4b. 子代理展开/折叠 ----
        print("\n[4b] 子代理展开/折叠")
        mains = lambda: page.locator(".session:not(.child)")
        aa = page.locator('.session:not(.child)', has_text="编写 PRD 文档")
        check("有子代理的行显示展开箭头", aa.locator(".s-toggle:not(.placeholder)").count() == 1)
        dd = page.locator('.session:not(.child)', has_text="别的项目")
        check("无子代理的行箭头为占位", dd.locator(".s-toggle.placeholder").count() == 1)
        check("初始无子代理行", page.locator(".session.child").count() == 0)
        aa.locator(".s-toggle").click()
        page.wait_for_timeout(300)
        kids = page.locator(".session.child")
        check("展开后 3 个子代理行", kids.count() == 3)
        check("子代理行显示 sessionId（短形展示，全文在悬浮提示）",
              "sess_subagent_agent_11111111" in kids.first.locator(".c-sid").get_attribute("title"))
        check("徽章区分子代理/工作流",
              "子代理" in kids.first.text_content()
              and "工作流" in page.locator('.session.child', has_text="工作流").text_content())
        check("子代理行无复选框（随主会话删除）",
              kids.first.locator("input[type=checkbox]").count() == 0)
        # 列对齐：主行与子行的消息/更新/占用列 x 坐标必须完全一致（grid 同模板）
        xs = page.evaluate("""() => [...document.querySelectorAll('.session')].map(
            r => Math.round(r.children[3].getBoundingClientRect().x))""")
        check("消息列所有行严格对齐", len(set(xs)) == 1, str(xs))
        page.screenshot(path=os.path.join(SHOTS, "01b-subagents.png"), full_page=True)
        kids.first.locator(".btn-eye").click()      # 子代理也可预览
        page.wait_for_selector("#modal-preview:not([hidden])")
        page.wait_for_timeout(400)
        check("子代理预览标题带主会话名",
              "子代理 · " in page.text_content("#pv-title")
              and "编写 PRD" in page.text_content("#pv-title"))
        check("子代理预览有内容", "请帮我处理" in page.text_content("#pv-chat"))
        page.keyboard.press("Escape")
        page.wait_for_timeout(250)
        aa.locator(".s-toggle").click()
        page.wait_for_timeout(300)
        check("再点折叠收起", page.locator(".session.child").count() == 0)
        page.fill("#search", "sess_subagent_agent_1111")
        page.wait_for_timeout(400)
        check("搜索子代理 ID 命中主会话行",
              mains().count() == 1 and "编写 PRD" in mains().first.text_content())
        page.fill("#search", "")
        page.wait_for_timeout(400)

        # ---- 5. 预览 ----
        print("\n[5] 内容预览")
        page.locator('.session', has_text="修复登录页样式").locator(".btn-eye").click()
        page.wait_for_selector("#modal-preview:not([hidden])")
        page.wait_for_timeout(400)
        chat = page.text_content("#pv-chat")
        check("预览含用户消息", "请帮我处理" in chat)
        check("预览含助手消息", "收到" in chat)
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
        check("确认框说明连带删除子代理", "3 个子代理" in page.text_content("#cf-body"))
        check("确认框说明删除范围", "VACUUM" in page.text_content("#cf-body"))
        check("备份选项默认勾选", page.is_checked("#opt-backup"))
        check("勾选备份显示还原说明", "备份与恢复" in page.text_content("#cf-backup-note"))
        page.uncheck("#opt-backup")
        page.wait_for_timeout(100)
        check("取消备份显示不可恢复警告", "无法通过备份恢复" in page.text_content("#cf-backup-note"))
        page.screenshot(path=os.path.join(SHOTS, "04-confirm.png"))
        page.check("#opt-backup")
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
        check("报告-2 个主会话成功", "成功 2 / 2" in body)
        check("报告-消息数 12（主 6 + 子代理 6）", "12 条消息" in body)
        check("报告-连带删除 3 个子代理", "连带删除子代理会话 3 个" in body)
        check("报告-VACUUM 成功", page.locator("#rp-body .ic-check").count() == 2)
        check("报告-日志清洗", "抹除" in body)
        check("报告-含备份时间戳", re.search(r"备份：\d{8}-\d{6}", body) is not None)
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
        check("子代理 ID 也被清洗", "sess_subagent_agent_1111" not in log)
        check("日志保留其他会话", "sess_dd44" in log)
        cdb = os.path.join(cli, "db", "db.sqlite")
        scon = sqlite3.connect(f"file:{cdb}?mode=ro", uri=True)
        kids_left = scon.execute(
            "SELECT COUNT(*) FROM session WHERE parent_id IS NOT NULL").fetchone()[0]
        scon.close()
        check("子代理会话行已连带删除", kids_left == 0, f"剩 {kids_left} 行")
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
        page.wait_for_timeout(1000)   # 等 count-up 结束再读统计带
        check("恢复后回到 5 行", page.locator(".session").count() == 5)
        check("恢复后统计卡=5", page.text_content("#st-total").strip() == "5")
        scan = api("/api/scan")["sessions"]
        aa_restored = next(s for s in scan if "PRD" in s["title"])
        check("恢复后子代理随库还原", len(aa_restored["children"]) == 3)
        page.screenshot(path=os.path.join(SHOTS, "07-restored.png"), full_page=True)

        # ---- 11. 不备份直接删除 ----
        print("\n[11] 不备份直接删除")
        backups_before = len(api("/api/backups")["backups"])
        cc = page.locator('.session', has_text="编写 PRD 文档")
        cc.locator("input[type=checkbox]").check()
        page.wait_for_timeout(150)
        page.click("#btn-delete")
        page.wait_for_selector("#modal-confirm:not([hidden])")
        page.wait_for_timeout(450)
        page.uncheck("#opt-backup")
        page.wait_for_timeout(100)
        check("未勾选备份警告可见", "无法通过备份恢复" in page.text_content("#cf-backup-note"))
        page.screenshot(path=os.path.join(SHOTS, "20-no-backup-confirm.png"))
        page.click("#cf-ok")
        page.wait_for_selector("#modal-report:not([hidden])", timeout=60000)
        page.wait_for_timeout(300)
        body = page.text_content("#rp-body")
        check("报告-显示未备份", "本次未备份" in body)
        check("报告-1 个成功", "成功 1 / 1" in body)
        page.screenshot(path=os.path.join(SHOTS, "21-no-backup-report.png"))
        page.click("#modal-report [data-close]")
        page.wait_for_timeout(600)
        check("删除后剩 4 行", page.locator(".session").count() == 4)
        backups_after = len(api("/api/backups")["backups"])
        check("未产生新备份", backups_after == backups_before,
              f"before={backups_before} after={backups_after}")

        browser.close()

    print(f"\n========== 结果: {len(PASS)} 通过 / {len(FAIL)} 失败 ==========")
    if FAIL:
        print("失败项:", FAIL)
        sys.exit(1)


if __name__ == "__main__":
    main()
