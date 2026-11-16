#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""完工报告功能回归测试。

在临时数据目录 + 随机端口上启动一套独立服务，覆盖：旧库自动迁移、
必填校验、完工报告与照片落库、详情/时间轴/CSV 导出、重新打开清空。
全程不读写项目里的真实数据库，也不占用 8080 端口。

用法: python3 deploy/test_finish_report.py
"""

import base64
import importlib.util
import json
import shutil
import sqlite3
import struct
import tempfile
import threading
import urllib.error
import urllib.request
import zlib
from http.server import ThreadingHTTPServer
from pathlib import Path

try:
    import sys
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE = Path(__file__).resolve().parent.parent
PASSED = []


def check(name, cond, detail=""):
    if not cond:
        raise AssertionError("FAIL %s | %s" % (name, str(detail)[:400]))
    PASSED.append(name)
    print("  [OK] %s" % name)


def tiny_png():
    """生成一张合法且大于 100 字节的 PNG data URL，供 save_image 使用。"""
    w = h = 32
    raw = bytearray()
    for y in range(h):
        raw.append(0)
        for x in range(w):
            raw += bytes(((x * 37 + y * 91) % 256,
                          (x * 17 + y * 53) % 256,
                          (x * 7 + y * 29) % 256))

    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xffffffff))

    png = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(raw))
           + chunk(b"IEND", b""))
    return "data:image/png;base64," + base64.b64encode(png).decode()


def main():
    spec = importlib.util.spec_from_file_location("wo_server", str(BASE / "server.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)

    tmp = tempfile.TemporaryDirectory()
    root = Path(tmp.name)
    m.DATA = root / "data"
    m.UPLOADS = m.DATA / "uploads"
    m.DB_PATH = m.DATA / "tickets.db"
    m.CONFIG_PATH = root / "config.json"
    shutil.copyfile(str(BASE / "config.json"), str(m.CONFIG_PATH))

    # 1) 模拟「升级前的旧库」：用当前 SCHEMA 去掉新增的三列建表，并放入历史数据
    new_cols = ("  finish_process TEXT DEFAULT '', finish_result TEXT DEFAULT '', "
                "finish_images TEXT DEFAULT '[]',\n")
    old_schema = m.SCHEMA.replace(new_cols, "")
    assert new_cols in m.SCHEMA and old_schema != m.SCHEMA, "SCHEMA 结构已变化，请同步更新本测试"
    m.DATA.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(m.DB_PATH))
    c.executescript(old_schema)
    c.execute("INSERT INTO tickets(ticket_no,description,status) VALUES('OLD-1','历史工单','completed')")
    c.commit()
    c.close()

    m.init_db()
    conn = sqlite3.connect(str(m.DB_PATH))
    cols = {r[1] for r in conn.execute("PRAGMA table_info(tickets)")}
    for col in ("finish_process", "finish_result", "finish_images"):
        check("旧库自动补列 " + col, col in cols, cols)
    check("历史工单数据保留",
          conn.execute("SELECT COUNT(*) FROM tickets WHERE ticket_no='OLD-1'").fetchone()[0] == 1)
    conn.close()

    m.Handler.log_message = lambda self, *args: None
    srv = ThreadingHTTPServer(("127.0.0.1", 0), m.Handler)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def call(method, path, data=None, token=None):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8") if data is not None else None
        req = urllib.request.Request("http://127.0.0.1:%d%s" % (port, path), data=body, method=method)
        if body is not None:
            req.add_header("Content-Type", "application/json")
        if token:
            req.add_header("X-Token", token)
        try:
            with opener.open(req, timeout=10) as r:
                raw = r.read()
                ct = r.headers.get("Content-Type", "")
                return r.status, json.loads(raw) if "json" in ct else raw.decode("utf-8-sig", "replace")
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", "replace")
            try:
                return e.code, json.loads(raw)
            except Exception:
                return e.code, raw

    cfg = m.load_config()
    cat = cfg["categories"][0]["id"]
    img = tiny_png()

    st, r = call("POST", "/api/auth/login", {"username": "admin", "password": "123456"})
    check("管理员登录", st == 200 and "token" in r, r)
    token = r["token"]

    st, t = call("POST", "/api/tickets", {
        "floor": cfg["floors"][0], "location": "回归测试位",
        "category_id": cat, "description": "用于验证完工报告的完整流程",
        "images": [img], "contact_name": "测试员", "contact_phone": "13800000000",
    }, token)
    check("创建工单", st == 200 and t.get("status") == "pending", t)
    tid = t["id"]

    st, r = call("POST", "/api/tickets/%d/claim" % tid, {}, token)
    check("抢单受理（进入已派单）", st == 200 and r.get("status") == "assigned", r)

    st, r = call("POST", "/api/tickets/%d/finish" % tid, {"process": "短", "result": "已修复"}, token)
    check("处理过程不足 5 字被拦截", st == 400, r)
    st, r = call("POST", "/api/tickets/%d/finish" % tid,
                 {"process": "检查后更换了阀芯", "result": ""}, token)
    check("处理结果为空被拦截", st == 400, r)
    st, r = call("POST", "/api/tickets/%d/finish" % tid,
                 {"process": "检查后更换了阀芯", "result": "已修复", "images": [img] * 4}, token)
    check("完工照片超过 3 张被拦截", st == 400, r)

    st, r = call("POST", "/api/tickets/%d/finish" % tid, {
        "process": "拆开水龙头检查，发现阀芯老化漏水，已更换新阀芯并拧紧接口",
        "result": "已修复，现场试水无渗漏，可正常使用",
        "images": [img, img],
    }, token)
    check("提交完工报告", st == 200 and r.get("status") == "pending_accept", r)
    check("主表返回处理过程", str(r.get("finish_process", "")).startswith("拆开水龙头"), r.get("finish_process"))
    check("主表返回处理结果", r.get("finish_result") == "已修复，现场试水无渗漏，可正常使用")
    check("完工照片保存 2 张", len(r.get("finish_images", [])) == 2, r.get("finish_images"))

    st, d = call("GET", "/api/tickets/%d" % tid, None, token)
    check("详情接口可读完工报告", st == 200 and bool(d.get("finish_result")), d)

    st, ev = call("GET", "/api/tickets/%d/events" % tid, None, token)
    check("时间轴留痕含处理摘要",
          st == 200 and any("处理结果" in (x.get("note") or "") for x in ev), ev)

    st, csv_text = call("GET", "/api/admin/tickets/export", None, token)
    check("CSV 新增三列", st == 200 and all(
        k in csv_text for k in ("处理过程", "处理结果", "完工照片", "验收备注")), str(csv_text)[:200])
    check("CSV 含实际处理内容与照片路径", "已更换新阀芯" in csv_text and "/uploads/" in csv_text)

    # 兼容旧客户端：只传 note 也能完工
    st, t2 = call("POST", "/api/tickets", {
        "floor": cfg["floors"][0], "location": "兼容测试位",
        "category_id": cat, "description": "用于验证旧客户端兼容性",
        "images": [img], "contact_phone": "13800000000",
    }, token)
    tid2 = t2["id"]
    call("POST", "/api/tickets/%d/claim" % tid2, {}, token)
    st, r = call("POST", "/api/tickets/%d/finish" % tid2,
                 {"note": "旧版客户端仅传一段处理说明"}, token)
    check("旧版客户端 note 兼容", st == 200 and bool(r.get("finish_process")), r)

    st, r = call("POST", "/api/tickets/%d/confirm" % tid,
                 {"rating": 5, "remark": "验收通过"}, token)
    check("提单人确认完成", st == 200 and r.get("status") == "completed", r)
    st, r = call("POST", "/api/admin/tickets/%d/reopen" % tid, {}, token)
    check("重新打开工单", st == 200, r)
    conn = sqlite3.connect(str(m.DB_PATH))
    row = conn.execute("SELECT finish_process,finish_result,finish_images FROM tickets WHERE id=?",
                       (tid,)).fetchone()
    conn.close()
    check("重新打开后清空完工报告", row == ("", "", "[]"), row)

    srv.shutdown()
    srv.server_close()
    tmp.cleanup()
    print("\n全部 %d 项检查通过。" % len(PASSED))


if __name__ == "__main__":
    main()
