#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""部署后自检：把工单全链路跑一遍。用法: python3 deploy/smoke_test.py [服务地址]"""
import base64
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8080").rstrip("/")
LOGO = Path(__file__).resolve().parent.parent / "static" / "logo.png"


def req(method, path, data=None, token=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-Token"] = token
    body = json.dumps(data, ensure_ascii=False).encode("utf-8") if data is not None else None
    r = urllib.request.Request(BASE + path, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(r, timeout=10) as resp:
            raw = resp.read()
            ctype = resp.headers.get("Content-Type", "")
            return raw if "csv" in ctype else json.loads(raw.decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise RuntimeError("%s %s -> HTTP %d %s" % (method, path, e.code, e.read().decode("utf-8", "replace")))


def raw_get(path):
    with urllib.request.urlopen(BASE + path, timeout=10) as resp:
        return resp.status, resp.headers.get("Content-Type", ""), resp.read()


def check(label, cond, extra=""):
    if not cond:
        raise AssertionError("FAIL: %s %s" % (label, extra))
    print("  [ok] %s %s" % (label, extra))


def check_icons():
    """网站图标 / 桌面图标 / PWA 清单是否随部署包一起落地。"""
    for path, magic, ctype in [
        ("/favicon.ico", b"\x00\x00\x01\x00", "icon"),
        ("/static/icons/favicon-16.png", b"\x89PNG", "png"),
        ("/static/icons/favicon-32.png", b"\x89PNG", "png"),
        ("/static/icons/favicon-48.png", b"\x89PNG", "png"),
        ("/static/icons/apple-touch-icon.png", b"\x89PNG", "png"),
        ("/static/icons/icon-192.png", b"\x89PNG", "png"),
        ("/static/icons/icon-512.png", b"\x89PNG", "png"),
        ("/static/manifest.webmanifest", b"{", "manifest"),
    ]:
        st, ct, raw = raw_get(path)
        ok = st == 200 and raw.startswith(magic) and ctype in ct
        if path.endswith("webmanifest") and ok:
            ok = len(json.loads(raw.decode("utf-8"))["icons"]) >= 2
        check("图标资源 " + path, ok, "%d bytes" % len(raw))


def main():
    print("自检目标: %s" % BASE)
    check_icons()
    cfg = req("GET", "/api/config/public")
    check("公共配置", len(cfg["floors"]) == 5 and len(cfg["categories"]) >= 5,
          "%d 个楼层 / %d 个类型" % (len(cfg["floors"]), len(cfg["categories"])))

    admin = req("POST", "/api/auth/login", {"username": "admin", "password": "123456"})
    at = admin["token"]
    check("管理员登录", admin["user"]["role"] == "admin")

    staff = req("POST", "/api/auth/bind", {"employee_no": "SMOKE001", "name": "自检员工", "dept": "行政部"})
    st = staff["token"]
    check("员工身份绑定", staff["user"]["employee_no"] == "SMOKE001")

    img = "data:image/png;base64," + base64.b64encode(LOGO.read_bytes()).decode("ascii")
    ticket = req("POST", "/api/tickets", {
        "floor": "2F", "location": "东侧茶水间", "category_id": "water",
        "description": "水龙头持续漏水，地面已有积水，请尽快处理", "images": [img],
        "urgency": "urgent", "contact_name": "自检员工", "contact_phone": "13800000000",
    }, token=st)
    tid = ticket["id"]
    check("创建工单", ticket["status"] == "pending" and ticket["images"], ticket["ticket_no"])
    check("SLA 时限", bool(ticket["response_due_at"] and ticket["resolve_due_at"]))

    mine = req("GET", "/api/tickets/mine", token=st)
    check("我的工单", any(t["id"] == tid for t in mine))

    existing = req("GET", "/api/admin/users?q=SMOKEW01", token=at)
    if existing:
        worker = existing[0]
    else:
        worker = req("POST", "/api/admin/users", {
            "employee_no": "SMOKEW01", "name": "自检师傅", "role": "worker", "dept": "工程部"}, token=at)
    pwd = req("POST", "/api/admin/users/%d/reset_password" % worker["id"], {}, token=at)["password"]
    check("重置后返回统一默认密码", pwd == "123456", pwd)
    wlogin = req("POST", "/api/auth/login", {"username": "SMOKEW01", "password": pwd})
    wt = wlogin["token"]
    check("处理人登录", wlogin["user"]["role"] == "worker")

    pool = req("GET", "/api/pool", token=wt)
    check("公共工单池", any(t["id"] == tid for t in pool))
    check("池内脱敏", all(not t["contact_phone"] for t in pool))

    r = req("POST", "/api/tickets/%d/claim" % tid, {}, token=wt)
    check("抢单", r["status"] == "assigned" and r["assignee_name"] == "自检师傅")
    r = req("POST", "/api/tickets/%d/start" % tid, {}, token=wt)
    check("开始处理", r["status"] == "processing")
    req("POST", "/api/tickets/%d/note" % tid, {"note": "已带工具到场，正在更换阀芯"}, token=wt)
    r = req("POST", "/api/tickets/%d/finish" % tid, {
        "process": "检查发现阀芯老化漏水，已更换新阀芯并清理现场",
        "result": "已修复，试水无渗漏，可正常使用",
        "images": [],
    }, token=wt)
    check("提交完工报告", r["status"] == "pending_accept")
    check("完工报告落库", r.get("finish_process", "").startswith("检查发现") and bool(r.get("finish_result")))

    events = req("GET", "/api/tickets/%d/events" % tid, token=st)
    check("过程时间轴", len(events) >= 5, "%d 条记录" % len(events))
    notes = req("GET", "/api/notifications", token=st)
    check("消息通知", any(n["type"] == "pending_accept" for n in notes))

    r = req("POST", "/api/tickets/%d/confirm" % tid, {"remark": "已查看，没问题", "rating": 5}, token=st)
    check("确认完成", r["status"] == "completed" and r["rating"] == 5)

    t2 = req("POST", "/api/tickets", {
        "floor": "5F", "location": "A 区工位 12", "category_id": "hvac",
        "description": "空调出风口异响，影响开会", "images": [img],
        "urgency": "normal", "contact_name": "自检员工", "contact_phone": "13800000000",
    }, token=st)
    r = req("POST", "/api/admin/tickets/%d/assign" % t2["id"], {"assignee_id": worker["id"]}, token=at)
    check("人工派单", r["status"] == "assigned")

    stats = req("GET", "/api/admin/stats", token=at)
    check("统计看板", stats["total"] >= 2 and len(stats["by_floor"]) >= 2,
          "共 %d 单，完成率 %s%%" % (stats["total"], stats["completion_rate"]))

    csv_data = req("GET", "/api/admin/tickets/export", token=at)
    check("CSV 导出", csv_data.startswith(b"\xef\xbb\xbf") and b"\xe5\xb7\xa5\xe5\x8d\x95\xe5\x8f\xb7" in csv_data,
          "%d bytes" % len(csv_data))

    users = req("GET", "/api/admin/users", token=at)
    check("员工名录", len(users) >= 3, "%d 人" % len(users))

    print("\n全部通过。测试数据保留在数据库中，可在管理端删除或用测试工单号核对。")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("\n自检失败: %s" % exc)
        sys.exit(1)
