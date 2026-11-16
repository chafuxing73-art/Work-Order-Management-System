#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""企业内部楼层报修工单系统 - 后端服务
仅使用 Python 3 标准库，数据全部保存在本地 SQLite，适合内网离线部署。
启动: python3 server.py
"""

import base64
import csv
import hashlib
import io
import json
import mimetypes
import os
import re
import secrets
import sqlite3
import sys
import threading
import time
import traceback
import urllib.parse
import urllib.request
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE = Path(__file__).resolve().parent
STATIC = BASE / "static"
DATA = BASE / "data"
UPLOADS = DATA / "uploads"
DB_PATH = DATA / "tickets.db"
CONFIG_PATH = BASE / "config.json"

CONFIG_LOCK = threading.RLock()
_wecom_token_cache = {}   # (corp_id, secret) -> {"token":..., "exp":...}
_wecom_lock = threading.Lock()

STATUS = {
    "pending": "待受理",
    "assigned": "已派单",
    "processing": "处理中",
    "pending_accept": "待验收",
    "completed": "已完成",
    "closed": "已关闭",
}
ROLE_NAMES = {"staff": "普通员工", "worker": "处理人", "dispatcher": "行政派单", "admin": "超级管理员"}
ACTIVE_STATUS = ("pending", "assigned", "processing", "pending_accept")


# --------------------------------------------------------------------------- 配置

def load_config():
    with CONFIG_LOCK:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)


def save_config(cfg):
    with CONFIG_LOCK:
        tmp = CONFIG_PATH.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        os.replace(tmp, CONFIG_PATH)


def default_password():
    """统一的默认密码：新建账号 / 后台重置密码 / 初始管理员都用它。
    可在 config.json 的 auth.default_password 修改；留空则回退到 123456。"""
    try:
        pwd = str(load_config().get("auth", {}).get("default_password") or "").strip()
    except Exception:
        pwd = ""
    return pwd or "123456"


def public_config():
    cfg = load_config()
    try:
        with db() as conn:
            departments = [r["dept"] for r in conn.execute(
                "SELECT DISTINCT dept FROM users WHERE dept IS NOT NULL AND dept<>'' ORDER BY dept"
            ).fetchall()]
    except Exception:
        departments = []
    wc = cfg.get("wecom", {})
    return {
        "company": cfg["company"],
        "floors": cfg["floors"],
        "categories": [c for c in cfg["categories"] if c.get("enabled", True)],
        "urgency": cfg["urgency"],
        "sla": cfg["sla"],
        "logo": (STATIC / "logo.png").exists(),
        "port": cfg["server"]["port"],
        "departments": departments,
        "wecom_oauth": bool(wc.get("oauth_enabled") and wc.get("public_base_url")),
    }


# --------------------------------------------------------------------------- 工具

def now_dt():
    return datetime.now()


def fmt(dt):
    return dt.strftime("%Y-%m-%d %H:%M:%S") if dt else None


def parse_dt(s):
    return datetime.strptime(s, "%Y-%m-%d %H:%M:%S") if s else None


def iso_now():
    return fmt(now_dt())


def hash_password(pwd, salt=None):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.sha256((salt + pwd).encode("utf-8")).hexdigest()
    return salt + "$" + digest


def check_password(pwd, stored):
    if not stored or "$" not in stored:
        return False
    salt, digest = stored.split("$", 1)
    return secrets.compare_digest(hashlib.sha256((salt + pwd).encode("utf-8")).hexdigest(), digest)


class HttpError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


def require_keys(data, keys):
    for k in keys:
        if k not in data or (isinstance(data[k], str) and not data[k].strip()):
            raise HttpError(400, "缺少必填项：" + k)


# --------------------------------------------------------------------------- 数据库

@contextmanager
def db():
    conn = sqlite3.connect(str(DB_PATH), timeout=15)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  employee_no TEXT UNIQUE NOT NULL,
  name TEXT NOT NULL,
  dept TEXT DEFAULT '',
  phone TEXT DEFAULT '',
  role TEXT NOT NULL DEFAULT 'staff',
  wecom_userid TEXT DEFAULT '',
  password_hash TEXT DEFAULT '',
  enabled INTEGER DEFAULT 1,
  created_at TEXT
);
CREATE TABLE IF NOT EXISTS sessions (
  token TEXT PRIMARY KEY,
  user_id INTEGER NOT NULL,
  created_at TEXT,
  expires_at TEXT
);
CREATE TABLE IF NOT EXISTS tickets (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ticket_no TEXT UNIQUE NOT NULL,
  floor TEXT, location TEXT,
  category_id TEXT, category_name TEXT,
  description TEXT, images TEXT DEFAULT '[]',
  urgency TEXT, urgency_name TEXT,
  contact_name TEXT, contact_phone TEXT,
  creator_id INTEGER, creator_name TEXT, creator_dept TEXT,
  status TEXT DEFAULT 'pending',
  assignee_id INTEGER, assignee_name TEXT,
  created_at TEXT, assigned_at TEXT, started_at TEXT, completed_at TEXT, closed_at TEXT,
  responded_at TEXT,
  response_due_at TEXT, resolve_due_at TEXT,
  response_overdue INTEGER DEFAULT 0, resolve_overdue INTEGER DEFAULT 0,
  response_notified INTEGER DEFAULT 0, resolve_notified INTEGER DEFAULT 0,
  rating INTEGER, accept_remark TEXT, close_reason TEXT,
  finish_process TEXT DEFAULT '', finish_result TEXT DEFAULT '', finish_images TEXT DEFAULT '[]',
  last_event_at TEXT
);
CREATE TABLE IF NOT EXISTS ticket_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ticket_id INTEGER, at TEXT, actor_id INTEGER, actor_name TEXT, action TEXT, note TEXT
);
CREATE TABLE IF NOT EXISTS notifications (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER, ticket_id INTEGER, type TEXT, title TEXT, content TEXT,
  is_read INTEGER DEFAULT 0, created_at TEXT
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE INDEX IF NOT EXISTS idx_tickets_status ON tickets(status);
CREATE INDEX IF NOT EXISTS idx_tickets_creator ON tickets(creator_id);
CREATE INDEX IF NOT EXISTS idx_tickets_assignee ON tickets(assignee_id);
CREATE INDEX IF NOT EXISTS idx_events_ticket ON ticket_events(ticket_id);
CREATE INDEX IF NOT EXISTS idx_notif_user ON notifications(user_id, is_read);
"""


def init_db():
    DATA.mkdir(parents=True, exist_ok=True)
    UPLOADS.mkdir(parents=True, exist_ok=True)
    with db() as conn:
        conn.executescript(SCHEMA)
        # 兼容升级前已存在的数据库：补齐「完工报告」字段，老数据保留原状。
        existing = {r["name"] for r in conn.execute("PRAGMA table_info(tickets)")}
        for col, decl in (("finish_process", "TEXT DEFAULT ''"),
                          ("finish_result", "TEXT DEFAULT ''"),
                          ("finish_images", "TEXT DEFAULT '[]'")):
            if col not in existing:
                conn.execute("ALTER TABLE tickets ADD COLUMN %s %s" % (col, decl))
        cfg = load_config()
        auth = cfg.get("auth", {})
        username = auth.get("admin_username", "admin")
        row = conn.execute("SELECT id FROM users WHERE employee_no=?", (username,)).fetchone()
        if not row:
            conn.execute(
                "INSERT INTO users(employee_no,name,dept,role,password_hash,enabled,created_at) VALUES(?,?,?,?,?,1,?)",
                (username, "系统管理员", "行政部", "admin", hash_password(auth.get("admin_password", default_password())), iso_now()),
            )
        if "admin_password" in auth:
            auth.pop("admin_password")
            auth["admin_initialized"] = True
            cfg["auth"] = auth
            save_config(cfg)


def next_ticket_no(conn):
    day = now_dt().strftime("%Y%m%d")
    key = "ticket_seq_" + day
    row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    seq = int(row["value"]) + 1 if row else 1
    conn.execute("INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)", (key, str(seq)))
    prefix = load_config().get("ticket_prefix", "WO")
    return "%s%s-%04d" % (prefix, day, seq)


def issue_token(conn, user_id):
    cfg = load_config()
    days = int(cfg.get("auth", {}).get("session_days", 30))
    token = secrets.token_urlsafe(32)
    conn.execute(
        "INSERT INTO sessions(token,user_id,created_at,expires_at) VALUES(?,?,?,?)",
        (token, user_id, iso_now(), fmt(now_dt() + timedelta(days=days))),
    )
    return token


def user_public(row):
    return {
        "id": row["id"], "employee_no": row["employee_no"], "name": row["name"],
        "dept": row["dept"], "phone": row["phone"], "role": row["role"],
        "role_name": ROLE_NAMES.get(row["role"], row["role"]),
        "wecom_userid": row["wecom_userid"], "enabled": row["enabled"],
    }


def add_event(conn, ticket_id, actor, action, note=""):
    conn.execute(
        "INSERT INTO ticket_events(ticket_id,at,actor_id,actor_name,action,note) VALUES(?,?,?,?,?,?)",
        (ticket_id, iso_now(), actor["id"] if actor else None, actor["name"] if actor else "系统", action, note),
    )
    conn.execute("UPDATE tickets SET last_event_at=? WHERE id=?", (iso_now(), ticket_id))


def notify(conn, user_ids, ticket_id, ntype, title, content, also_wecom=True):
    for uid in dict.fromkeys([u for u in user_ids if u]):
        conn.execute(
            "INSERT INTO notifications(user_id,ticket_id,type,title,content,is_read,created_at) VALUES(?,?,?,?,?,0,?)",
            (uid, ticket_id, ntype, title, content, iso_now()),
        )
    if also_wecom:
        wecom_userids = []
        for uid in dict.fromkeys([u for u in user_ids if u]):
            r = conn.execute("SELECT wecom_userid FROM users WHERE id=?", (uid,)).fetchone()
            if r and r["wecom_userid"]:
                wecom_userids.append(r["wecom_userid"])
        if wecom_userids:
            threading.Thread(target=wecom_send_async, args=(wecom_userids, content), daemon=True).start()


def dispatcher_ids(conn):
    return [r["id"] for r in conn.execute(
        "SELECT id FROM users WHERE role IN ('admin','dispatcher') AND enabled=1"
    ).fetchall()]


def new_ticket_targets(conn):
    """新工单提醒对象：派单员 + （默认）全部在职处理人，便于抢单模式。

    只用于「新工单待受理」这一条通知；SLA 超时仍只通知派单员与接单人，
    避免所有处理人被超时消息反复打扰。
    """
    cfg = load_config().get("wecom", {})
    roles = ["admin", "dispatcher"]
    if cfg.get("notify_new_ticket_to_workers", True):
        roles.append("worker")
    ph = ",".join("?" for _ in roles)
    return [r["id"] for r in conn.execute(
        "SELECT id FROM users WHERE role IN (" + ph + ") AND enabled=1", roles
    ).fetchall()]


# --------------------------------------------------------------------------- 企业微信

def wecom_access_token(corp_id, secret):
    # 应用 Secret 与通讯录同步 Secret 是两套凭证，必须分开缓存
    key = (corp_id, secret)
    with _wecom_lock:
        hit = _wecom_token_cache.get(key)
        if hit and hit["exp"] > time.time():
            return hit["token"]
        url = "https://qyapi.weixin.qq.com/cgi-bin/gettoken?corpid=%s&corpsecret=%s" % (
            urllib.parse.quote(corp_id), urllib.parse.quote(secret))
        with urllib.request.urlopen(url, timeout=6) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        if data.get("errcode") != 0:
            raise RuntimeError("获取企微 token 失败: " + str(data.get("errmsg")))
        _wecom_token_cache[key] = {
            "token": data["access_token"],
            "exp": time.time() + max(60, int(data.get("expires_in", 7200)) - 300),
        }
        return _wecom_token_cache[key]["token"]


def wecom_send_sync(userids, content):
    cfg = load_config().get("wecom", {})
    if not cfg.get("enabled"):
        return "未启用"
    if not (cfg.get("corp_id") and cfg.get("corp_secret") and cfg.get("agent_id")):
        return "参数不全"
    token = wecom_access_token(cfg["corp_id"], cfg["corp_secret"])
    url = "https://qyapi.weixin.qq.com/cgi-bin/message/send?access_token=" + urllib.parse.quote(token)
    payload = {
        "touser": "|".join(userids), "msgtype": "text", "agentid": int(cfg["agent_id"]),
        "text": {"content": content}, "safe": 0,
    }
    req = urllib.request.Request(
        url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=6) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    if data.get("errcode") != 0:
        raise RuntimeError("企微推送失败: " + str(data.get("errmsg")))
    return "ok"


def wecom_send_async(userids, content):
    try:
        result = wecom_send_sync(userids, content)
        print("[wecom] push -> %s : %s" % (",".join(userids), result), flush=True)
    except Exception as exc:
        print("[wecom] push failed: %s" % exc, flush=True)


def wecom_message(title, lines):
    return "【%s】\n%s\n\n%s" % (
        load_config()["company"]["system_name"], title, "\n".join(lines))


WECOM_API = "https://qyapi.weixin.qq.com/cgi-bin"


def wecom_get_json(url):
    with urllib.request.urlopen(url, timeout=8) as resp:
        return json.loads(resp.read().decode("utf-8"))


def wecom_contact_token(cfg):
    """通讯录读取优先用「通讯录同步 Secret」，留空则退回自建应用 Secret。"""
    secret = str(cfg.get("contact_secret") or cfg.get("corp_secret") or "").strip()
    corp_id = str(cfg.get("corp_id") or "").strip()
    if not corp_id or not secret:
        raise RuntimeError("请先填写 CorpID 和 Secret")
    return wecom_access_token(corp_id, secret)


def wecom_departments(token):
    data = wecom_get_json(WECOM_API + "/department/list?access_token=" + urllib.parse.quote(token))
    if data.get("errcode") != 0:
        raise RuntimeError("读取部门失败: " + str(data.get("errmsg")))
    return {str(d["id"]): str(d.get("name") or "") for d in data.get("department", [])}


def wecom_auth_url(cfg, redirect_target):
    """构造企微 OAuth2 静默授权地址。redirect_uri 必须落在已备案的公网可信域名下。"""
    base = str(cfg.get("public_base_url") or "").strip().rstrip("/")
    corp_id = str(cfg.get("corp_id") or "").strip()
    if not (cfg.get("oauth_enabled") and base and corp_id):
        raise HttpError(400, "未启用企业微信免登")
    p = urllib.parse.urlparse(redirect_target)
    redirect = base + (p.path or "/") + (("?" + p.query) if p.query else "")
    return ("https://open.weixin.qq.com/connect/oauth2/authorize?appid=%s&redirect_uri=%s"
            "&response_type=code&scope=snsapi_base&state=wo#wechat_redirect") % (
                urllib.parse.quote(corp_id), urllib.parse.quote(redirect, safe=""))


def wecom_upsert_user(conn, userid, name, dept, phone):
    """按企微 UserID / 手机号认领本地账号，找不到则新建。返回 (row, 是否新建)。"""
    row = conn.execute(
        "SELECT * FROM users WHERE wecom_userid=? AND wecom_userid<>''", (userid,)).fetchone()
    if not row and phone:
        row = conn.execute(
            "SELECT * FROM users WHERE phone=? AND phone<>''", (phone,)).fetchone()
    if row:
        conn.execute(
            "UPDATE users SET name=?, wecom_userid=?, "
            "dept=CASE WHEN ?<>'' THEN ? ELSE dept END, "
            "phone=CASE WHEN ?<>'' THEN ? ELSE phone END WHERE id=?",
            (name, userid, dept, dept, phone, phone, row["id"]))
        return conn.execute("SELECT * FROM users WHERE id=?", (row["id"],)).fetchone(), False
    emp = "W" + re.sub(r"[^A-Za-z0-9_-]", "", userid)[:24]
    if len(emp) < 2 or conn.execute("SELECT id FROM users WHERE employee_no=?", (emp,)).fetchone():
        emp = "W" + uuid.uuid4().hex[:12].upper()
    uid = conn.execute(
        "INSERT INTO users(employee_no,name,dept,phone,role,wecom_userid,password_hash,enabled,created_at) "
        "VALUES(?,?,?,?,'staff',?,?,1,?)",
        (emp, name, dept, phone, userid, hash_password(default_password()), iso_now()),
    ).lastrowid
    return conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone(), True


# --------------------------------------------------------------------------- 图片

def save_image(data_url):
    m = re.match(r"^data:image/(jpeg|jpg|png|webp);base64,(.+)$", str(data_url), re.S)
    if not m:
        raise HttpError(400, "图片格式不支持，仅支持 JPG / PNG / WEBP")
    try:
        raw = base64.b64decode(m.group(2))
    except Exception:
        raise HttpError(400, "图片数据损坏")
    if len(raw) > 8 * 1024 * 1024:
        raise HttpError(400, "单张图片不能超过 8MB")
    if len(raw) < 100:
        raise HttpError(400, "图片数据异常")
    ext = "jpg" if m.group(1) in ("jpeg", "jpg") else m.group(1)
    day = now_dt().strftime("%Y%m%d")
    folder = UPLOADS / day
    folder.mkdir(parents=True, exist_ok=True)
    name = uuid.uuid4().hex + "." + ext
    (folder / name).write_bytes(raw)
    return "/uploads/%s/%s" % (day, name)


# --------------------------------------------------------------------------- 工单业务

def ticket_view(row, viewer=None, mask=False):
    d = dict(row)
    try:
        d["images"] = json.loads(d.get("images") or "[]")
    except Exception:
        d["images"] = []
    try:
        d["finish_images"] = json.loads(d.get("finish_images") or "[]")
    except Exception:
        d["finish_images"] = []
    d["finish_process"] = d.get("finish_process") or ""
    d["finish_result"] = d.get("finish_result") or ""
    d["status_name"] = STATUS.get(d["status"], d["status"])
    now = now_dt()
    d["response_overdue"] = bool(d["response_overdue"])
    d["resolve_overdue"] = bool(d["resolve_overdue"])
    if d["status"] in ACTIVE_STATUS:
        d["live_response_overdue"] = bool(d["response_due_at"] and parse_dt(d["response_due_at"]) < now) and not d["responded_at"]
        d["live_resolve_overdue"] = bool(d["resolve_due_at"] and parse_dt(d["resolve_due_at"]) < now)
    else:
        d["live_response_overdue"] = False
        d["live_resolve_overdue"] = False
    d["overdue"] = d["live_response_overdue"] or d["live_resolve_overdue"] or d["response_overdue"] or d["resolve_overdue"]
    if mask:
        d.pop("contact_phone", None)
        d["contact_phone"] = ""
        d["creator_name"] = (d["creator_name"] or "")[:1] + "同事"
        d["creator_dept"] = ""
    return d


def get_ticket(conn, ticket_id):
    row = conn.execute("SELECT * FROM tickets WHERE id=?", (int(ticket_id),)).fetchone()
    if not row:
        raise HttpError(404, "工单不存在")
    return row


def can_view(row, user):
    if user["role"] in ("admin", "dispatcher"):
        return True
    if row["creator_id"] == user["id"]:
        return True
    if row["assignee_id"] == user["id"]:
        return True
    # 处理人要能打开「工单池」里的待受理单才能抢单；详情接口会对这类
    # 访问隐藏提单人联系方式，抢单成功后自动可见。
    if row["status"] == "pending" and user["role"] == "worker":
        return True
    return False


def create_ticket(conn, data, user, cfg):
    require_keys(data, ["floor", "category_id", "location", "description", "contact_phone"])
    if data["floor"] not in cfg["floors"]:
        raise HttpError(400, "楼层不在配置范围内")
    cat = next((c for c in cfg["categories"] if c["id"] == data["category_id"] and c.get("enabled", True)), None)
    if not cat:
        raise HttpError(400, "报修类型无效")
    if len(str(data["description"]).strip()) < 5:
        raise HttpError(400, "请至少用 5 个字描述问题，方便师傅判断")
    images = data.get("images") or []
    if not isinstance(images, list) or len(images) == 0:
        raise HttpError(400, "请至少上传 1 张现场照片")
    if len(images) > 6:
        raise HttpError(400, "最多上传 6 张照片")
    urg = next((u for u in cfg["urgency"] if u["id"] == data.get("urgency", cat.get("urgency"))), cfg["urgency"][1])
    saved = [save_image(x) for x in images]
    created = now_dt()
    resp_due = created + timedelta(minutes=int(urg["response_min"]))
    resolve_due = created + timedelta(minutes=int(urg["resolve_min"]))
    no = next_ticket_no(conn)
    cur = conn.execute(
        """INSERT INTO tickets(ticket_no,floor,location,category_id,category_name,description,images,
           urgency,urgency_name,contact_name,contact_phone,creator_id,creator_name,creator_dept,
           status,created_at,response_due_at,resolve_due_at,last_event_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,'pending',?,?,?,?)""",
        (no, data["floor"], str(data["location"]).strip(), cat["id"], cat["name"],
         str(data["description"]).strip(), json.dumps(saved, ensure_ascii=False),
         urg["id"], urg["name"], str(data.get("contact_name") or user["name"]).strip(),
         str(data["contact_phone"]).strip(), user["id"], user["name"], user["dept"],
         fmt(created), fmt(resp_due), fmt(resolve_due), fmt(created)),
    )
    tid = cur.lastrowid
    add_event(conn, tid, user, "创建工单", "%s · %s · %s" % (data["floor"], cat["name"], urg["name"]))
    title = "新工单待受理 %s" % no
    content = wecom_message(title, [
        "楼层：%s %s" % (data["floor"], str(data["location"]).strip()),
        "类型：%s（%s）" % (cat["name"], urg["name"]),
        "问题：%s" % str(data["description"]).strip()[:80],
        "提单人：%s %s" % (user["name"], str(data["contact_phone"]).strip()),
        "响应时限：%s" % fmt(resp_due),
    ])
    notify(conn, new_ticket_targets(conn), tid, "new_ticket", title, content)
    return get_ticket(conn, tid)


def change_status(conn, row, new_status, actor, note="", extra=None):
    old = row["status"]
    if old == new_status:
        raise HttpError(400, "工单已处于该状态")
    now = iso_now()
    fields = {"status": new_status, "last_event_at": now}
    if new_status == "assigned":
        fields["assigned_at"] = now
        fields["responded_at"] = now
        assignee = conn.execute("SELECT * FROM users WHERE id=?", (row["assignee_id"],)).fetchone()
        fields["assignee_name"] = assignee["name"] if assignee else ""
        if row["response_due_at"] and parse_dt(row["response_due_at"]) < now_dt():
            fields["response_overdue"] = 1
    elif new_status == "processing":
        fields["started_at"] = now
    elif new_status == "pending_accept":
        fields["completed_at"] = now
        if row["resolve_due_at"] and parse_dt(row["resolve_due_at"]) < now_dt():
            fields["resolve_overdue"] = 1
    elif new_status == "completed":
        if not row["completed_at"]:
            fields["completed_at"] = now
        fields["closed_at"] = now
    elif new_status == "closed":
        fields["closed_at"] = now
    if extra:
        fields.update(extra)
    sets = ",".join("%s=?" % k for k in fields)
    conn.execute("UPDATE tickets SET %s WHERE id=?" % sets, list(fields.values()) + [row["id"]])
    updated = get_ticket(conn, row["id"])

    label = STATUS.get(new_status, new_status)
    add_event(conn, row["id"], actor, "状态变更：%s → %s" % (STATUS.get(old, old), label), note)

    no = row["ticket_no"]
    targets, ntype, wecom_lines = [], "status", []
    if new_status == "assigned":
        targets = [updated["assignee_id"]]
        ntype = "assigned"
        wecom_lines = ["工单 %s 已指派给你" % no, "位置：%s %s" % (row["floor"], row["location"]),
                       "类型：%s" % row["category_name"], "完成时限：%s" % row["resolve_due_at"]]
    elif new_status == "pending_accept":
        targets = [row["creator_id"]]
        ntype = "pending_accept"
        wecom_lines = ["工单 %s 已完工，等待你确认" % no,
                       "处理结果：%s" % (updated["finish_result"] or "—"),
                       "处理过程：%s" % (updated["finish_process"] or "—")[:100],
                       "点开工单可查看完整完工报告与照片；48 小时内未提出异议将自动完成"]
    elif new_status in ("completed", "closed"):
        targets = [row["creator_id"]]
        ntype = "completed"
        wecom_lines = ["工单 %s 已%s" % (no, "完成" if new_status == "completed" else "关闭"),
                       "如有问题请联系行政"]
    title = "工单 %s %s" % (no, label)
    if targets:
        notify(conn, targets, row["id"], ntype, title, wecom_message(title, wecom_lines))
    return updated


def sla_loop():
    while True:
        try:
            interval = int(load_config().get("sla", {}).get("check_interval_sec", 60))
        except Exception:
            interval = 60
        time.sleep(max(20, interval))
        try:
            with db() as conn:
                now = now_dt()
                rows = conn.execute(
                    "SELECT * FROM tickets WHERE status IN ('pending','assigned','processing','pending_accept')"
                ).fetchall()
                for r in rows:
                    tid = r["id"]
                    if r["status"] == "pending" and r["response_due_at"] and parse_dt(r["response_due_at"]) < now and not r["response_notified"]:
                        conn.execute("UPDATE tickets SET response_overdue=1,response_notified=1 WHERE id=?", (tid,))
                        add_event(conn, tid, None, "响应超时", "超过响应时限仍无人接单")
                        content = wecom_message("工单 %s 响应超时" % r["ticket_no"], [
                            "位置：%s %s" % (r["floor"], r["location"]),
                            "类型：%s（%s）" % (r["category_name"], r["urgency_name"]),
                            "响应时限：%s" % r["response_due_at"], "请尽快派单处理"])
                        notify(conn, dispatcher_ids(conn), tid, "overdue", "工单 %s 响应超时" % r["ticket_no"], content)
                    if r["resolve_due_at"] and parse_dt(r["resolve_due_at"]) < now and not r["resolve_notified"]:
                        conn.execute("UPDATE tickets SET resolve_overdue=1,resolve_notified=1 WHERE id=?", (tid,))
                        add_event(conn, tid, None, "处理超时", "超过完成时限")
                        targets = dispatcher_ids(conn) + ([r["assignee_id"]] if r["assignee_id"] else [])
                        content = wecom_message("工单 %s 处理超时" % r["ticket_no"], [
                            "位置：%s %s" % (r["floor"], r["location"]),
                            "完成时限：%s" % r["resolve_due_at"],
                            "当前状态：%s" % STATUS.get(r["status"], r["status"])])
                        notify(conn, targets, tid, "overdue", "工单 %s 处理超时" % r["ticket_no"], content)
                    if r["status"] == "pending_accept" and r["completed_at"]:
                        hours = int(load_config().get("sla", {}).get("auto_close_hours", 48))
                        if parse_dt(r["completed_at"]) + timedelta(hours=hours) < now:
                            conn.execute("UPDATE tickets SET status='completed',closed_at=? WHERE id=?", (fmt(now), tid))
                            add_event(conn, tid, None, "自动完成", "%d 小时内提单人未提出异议，系统自动完成" % hours)
        except Exception:
            traceback.print_exc()


# --------------------------------------------------------------------------- 统计

def compute_stats(conn, date_from=None, date_to=None):
    sql = "SELECT * FROM tickets WHERE 1=1"
    args = []
    if date_from:
        sql += " AND created_at >= ?"
        args.append(date_from + " 00:00:00")
    if date_to:
        sql += " AND created_at <= ?"
        args.append(date_to + " 23:59:59")
    rows = conn.execute(sql, args).fetchall()
    total = len(rows)
    by_status, by_floor, by_category, by_assignee, by_urgency = {}, {}, {}, {}, {}
    resp_mins, resolve_mins, overdue, backlog, done = [], [], 0, 0, 0
    daily = {}
    for r in rows:
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1
        by_floor[r["floor"]] = by_floor.get(r["floor"], 0) + 1
        by_category[r["category_name"]] = by_category.get(r["category_name"], 0) + 1
        by_urgency[r["urgency_name"]] = by_urgency.get(r["urgency_name"], 0) + 1
        key = r["assignee_name"] or "未指派"
        by_assignee[key] = by_assignee.get(key, 0) + 1
        day = (r["created_at"] or "")[:10]
        daily[day] = daily.get(day, 0) + 1
        t = ticket_view(r)
        if t["overdue"]:
            overdue += 1
        if r["status"] in ACTIVE_STATUS:
            backlog += 1
        if r["status"] in ("completed", "closed"):
            done += 1
        if r["responded_at"] and r["created_at"]:
            resp_mins.append((parse_dt(r["responded_at"]) - parse_dt(r["created_at"])).total_seconds() / 60)
        if r["completed_at"] and r["created_at"]:
            resolve_mins.append((parse_dt(r["completed_at"]) - parse_dt(r["created_at"])).total_seconds() / 60)
    def avg(a):
        return round(sum(a) / len(a), 1) if a else 0
    def top(d):
        return sorted([{"name": k, "count": v} for k, v in d.items()], key=lambda x: -x["count"])
    return {
        "total": total, "done": done, "backlog": backlog, "overdue": overdue,
        "completion_rate": round(done / total * 100, 1) if total else 0,
        "overdue_rate": round(overdue / total * 100, 1) if total else 0,
        "avg_response_min": avg(resp_mins), "avg_resolve_min": avg(resolve_mins),
        "by_status": [{"name": STATUS.get(k, k), "id": k, "count": v} for k, v in by_status.items()],
        "by_floor": top(by_floor), "by_category": top(by_category),
        "by_assignee": top(by_assignee), "by_urgency": top(by_urgency),
        "daily": [{"date": k, "count": v} for k, v in sorted(daily.items())][-14:],
    }


# --------------------------------------------------------------------------- HTTP

class Handler(BaseHTTPRequestHandler):
    server_version = "WorkOrder/1.0"

    def log_message(self, fmtstr, *args):
        print("[%s] %s" % (now_dt().strftime("%H:%M:%S"), fmtstr % args), flush=True)

    def send_bytes(self, body, status=200, content_type="application/json; charset=utf-8", extra=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def send_json(self, obj, status=200):
        self.send_bytes(json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8"), status)

    def body_json(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length > 24 * 1024 * 1024:
            raise HttpError(413, "请求内容过大")
        raw = self.rfile.read(length) if length else b"{}"
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            raise HttpError(400, "请求格式错误")

    def current_user(self):
        token = self.headers.get("X-Token") or ""
        if not token:
            auth = self.headers.get("Authorization") or ""
            if auth.startswith("Bearer "):
                token = auth[7:]
        if not token:
            return None
        with db() as conn:
            row = conn.execute(
                """SELECT u.* FROM sessions s JOIN users u ON u.id=s.user_id
                   WHERE s.token=? AND s.expires_at>? AND u.enabled=1""",
                (token, iso_now()),
            ).fetchone()
        return dict(row) if row else None

    def require(self, *roles):
        u = self.current_user()
        if not u:
            raise HttpError(401, "登录已失效，请重新登录")
        if roles and u["role"] not in roles:
            raise HttpError(403, "当前账号没有此操作权限")
        return u

    def do_GET(self):
        self.dispatch("GET")

    def do_POST(self):
        self.dispatch("POST")

    def do_PUT(self):
        self.dispatch("PUT")

    def do_DELETE(self):
        self.dispatch("DELETE")

    def dispatch(self, method):
        parsed = urllib.parse.urlparse(self.path)
        path = urllib.parse.unquote(parsed.path)
        query = {k: v[0] for k, v in urllib.parse.parse_qs(parsed.query, keep_blank_values=True).items()}
        try:
            if path.startswith("/api/"):
                result = self.api(method, path, query)
                if result is None:
                    return
                if isinstance(result, tuple) and len(result) == 2 and isinstance(result[0], bytes):
                    self.send_bytes(result[0], 200, result[1])
                else:
                    self.send_json(result)
                return
            self.serve_static(path)
        except HttpError as e:
            self.send_json({"error": e.message}, e.code)
        except Exception:
            traceback.print_exc()
            self.send_json({"error": "服务器内部错误，请查看服务端日志"}, 500)

    def serve_static(self, path):
        if path in ("/", "/index.html"):
            target = STATIC / "index.html"
        elif path in ("/admin", "/admin.html"):
            target = STATIC / "admin.html"
        elif path.startswith("/uploads/"):
            target = (DATA / path.lstrip("/")).resolve()
            if UPLOADS.resolve() not in target.parents:
                raise HttpError(403, "禁止访问")
        elif path.startswith("/static/"):
            target = (BASE / path.lstrip("/")).resolve()
            if STATIC.resolve() not in target.parents and target != STATIC.resolve():
                raise HttpError(403, "禁止访问")
        else:
            target = (STATIC / path.lstrip("/")).resolve()
            if STATIC.resolve() not in target.parents and target != STATIC.resolve():
                raise HttpError(403, "禁止访问")
        if not target.is_file():
            raise HttpError(404, "页面不存在")
        ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript", "application/json"):
            ctype += "; charset=utf-8"
        self.send_bytes(target.read_bytes(), 200, ctype)

    # ------------------------------------------------------------------ API

    def api(self, method, path, q):
        cfg = load_config()

        if path == "/api/config/public" and method == "GET":
            return public_config()

        if path == "/api/wecom/auth_url" and method == "GET":
            cfg = load_config().get("wecom", {})
            return {"url": wecom_auth_url(cfg, q.get("redirect", ""))}

        if path == "/api/auth/wecom" and method == "POST":
            # 企微扫码免登：code -> UserID -> 通讯录详情 -> 本地账号（自动建档）
            d = self.body_json()
            code = str(d.get("code") or "").strip()
            cfg = load_config().get("wecom", {})
            base = str(cfg.get("public_base_url") or "").strip()
            corp_id = str(cfg.get("corp_id") or "").strip()
            secret = str(cfg.get("corp_secret") or "").strip()
            if not (cfg.get("oauth_enabled") and base and corp_id and secret):
                raise HttpError(400, "未启用企业微信免登")
            if not code:
                raise HttpError(400, "缺少授权 code")
            try:
                token = wecom_access_token(corp_id, secret)
                info = wecom_get_json(
                    WECOM_API + "/auth/getuserinfo?access_token=%s&code=%s"
                    % (urllib.parse.quote(token), urllib.parse.quote(code)))
                if info.get("errcode") != 0:
                    raise RuntimeError(str(info.get("errmsg")))
                userid = str(info.get("userid") or "").strip()
                if not userid:
                    raise RuntimeError("非企业成员，无法使用")
            except Exception as exc:
                raise HttpError(400, "企业微信身份校验失败：%s" % exc)
            name, dept, phone = userid, "", ""
            try:
                ctoken = wecom_contact_token(cfg)
                detail = wecom_get_json(
                    WECOM_API + "/user/get?access_token=%s&userid=%s"
                    % (urllib.parse.quote(ctoken), urllib.parse.quote(userid)))
                if detail.get("errcode") == 0:
                    name = str(detail.get("name") or userid).strip()
                    phone = str(detail.get("mobile") or "").strip()
                    dl = detail.get("department") or []
                    if dl:
                        dept = wecom_departments(ctoken).get(str(dl[0]), "")
            except Exception as exc:
                print("[wecom] 读取成员详情失败，按 UserID 建档: %s" % exc, flush=True)
            with db() as conn:
                row, _ = wecom_upsert_user(conn, userid, name, dept, phone)
                if not row["enabled"]:
                    raise HttpError(403, "该账号已停用，请联系行政")
                return {"token": issue_token(conn, row["id"]), "user": user_public(row)}

        if path == "/api/auth/bind" and method == "POST":
            d = self.body_json()
            no = str(d.get("employee_no") or "").strip()
            name = str(d.get("name") or "").strip()
            dept = str(d.get("dept") or "").strip()
            phone = str(d.get("phone") or "").strip()
            if not (1 <= len(name) <= 20):
                raise HttpError(400, "请填写姓名")
            with db() as conn:
                if no:
                    # 兼容通道：后台预置工号 / 自检脚本仍按工号精确匹配
                    if not (2 <= len(no) <= 32):
                        raise HttpError(400, "工号格式不正确")
                    row = conn.execute("SELECT * FROM users WHERE employee_no=?", (no,)).fetchone()
                    if row:
                        if row["name"] != name:
                            raise HttpError(403, "工号与姓名不匹配，请联系行政核对通讯录")
                        if not row["enabled"]:
                            raise HttpError(403, "该账号已停用，请联系行政")
                    else:
                        uid = conn.execute(
                            "INSERT INTO users(employee_no,name,dept,phone,role,enabled,created_at) "
                            "VALUES(?,?,?,?,'staff',1,?)",
                            (no, name, dept, phone, iso_now()),
                        ).lastrowid
                        row = conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
                else:
                    # 新通道：姓名 + 部门 + 手机号。手机号天生唯一，作为身份主键，
                    # 同时是师傅到场前的联系方式；工号由系统内部生成，员工无需填写。
                    if not re.match(r"^[0-9+\- ]{6,20}$", phone):
                        raise HttpError(400, "请填写有效的手机号")
                    row = conn.execute("SELECT * FROM users WHERE phone=?", (phone,)).fetchone()
                    if row:
                        if not row["enabled"]:
                            raise HttpError(403, "该账号已停用，请联系行政")
                        conn.execute(
                            "UPDATE users SET name=?, "
                            "dept=CASE WHEN dept IS NULL OR dept='' THEN ? ELSE dept END WHERE id=?",
                            (name, dept, row["id"]))
                        row = conn.execute("SELECT * FROM users WHERE id=?", (row["id"],)).fetchone()
                    else:
                        # 认领后台预置账号：同名且尚未登记手机号的唯一账号，
                        # 这样师傅不用填工号也能继承「处理人」角色。
                        cand = conn.execute(
                            "SELECT * FROM users WHERE name=? AND (phone IS NULL OR phone='')", (name,)
                        ).fetchall()
                        if len(cand) > 1:
                            raise HttpError(403, "存在同名账号，请联系行政在后台补充手机号")
                        if len(cand) == 1:
                            row = cand[0]
                            conn.execute(
                                "UPDATE users SET phone=?, "
                                "dept=CASE WHEN dept IS NULL OR dept='' THEN ? ELSE dept END WHERE id=?",
                                (phone, dept, row["id"]))
                            row = conn.execute("SELECT * FROM users WHERE id=?", (row["id"],)).fetchone()
                        else:
                            emp = "P" + re.sub(r"\D", "", phone)
                            if len(emp) < 2 or conn.execute(
                                    "SELECT id FROM users WHERE employee_no=?", (emp,)).fetchone():
                                emp = "P" + uuid.uuid4().hex[:12].upper()
                            uid = conn.execute(
                                "INSERT INTO users(employee_no,name,dept,phone,role,password_hash,enabled,created_at) "
                                "VALUES(?,?,?,?,'staff',?,1,?)",
                                (emp, name, dept, phone, hash_password(default_password()), iso_now()),
                            ).lastrowid
                            row = conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
                token = issue_token(conn, row["id"])
                return {"token": token, "user": user_public(row)}

        if path == "/api/auth/login" and method == "POST":
            d = self.body_json()
            require_keys(d, ["username", "password"])
            with db() as conn:
                row = conn.execute("SELECT * FROM users WHERE employee_no=?", (str(d["username"]).strip(),)).fetchone()
                if not row or not row["enabled"] or row["role"] not in ("admin", "dispatcher", "worker"):
                    raise HttpError(401, "账号不存在或未开通后台权限")
                if not check_password(str(d["password"]), row["password_hash"]):
                    raise HttpError(401, "密码错误")
                return {"token": issue_token(conn, row["id"]), "user": user_public(row)}

        if path == "/api/auth/logout" and method == "POST":
            token = self.headers.get("X-Token") or ""
            with db() as conn:
                conn.execute("DELETE FROM sessions WHERE token=?", (token,))
            return {"ok": True}

        if path == "/api/auth/password" and method == "POST":
            u = self.require()
            d = self.body_json()
            require_keys(d, ["old_password", "new_password"])
            if len(str(d["new_password"])) < 6:
                raise HttpError(400, "新密码至少 6 位")
            with db() as conn:
                row = conn.execute("SELECT password_hash FROM users WHERE id=?", (u["id"],)).fetchone()
                if not check_password(str(d["old_password"]), row["password_hash"]):
                    raise HttpError(400, "原密码错误")
                conn.execute("UPDATE users SET password_hash=? WHERE id=?", (hash_password(str(d["new_password"])), u["id"]))
            return {"ok": True}

        if path == "/api/me" and method == "GET":
            u = self.require()
            with db() as conn:
                unread = conn.execute("SELECT COUNT(*) c FROM notifications WHERE user_id=? AND is_read=0", (u["id"],)).fetchone()["c"]
                pool_count = conn.execute("SELECT COUNT(*) c FROM tickets WHERE status='pending'").fetchone()["c"]
                my_active = conn.execute(
                    "SELECT COUNT(*) c FROM tickets WHERE assignee_id=? AND status IN ('assigned','processing')",
                    (u["id"],)).fetchone()["c"]
            result = user_public(u)
            result["unread"] = unread
            result["pool_count"] = pool_count
            result["my_active"] = my_active
            return result

        if path == "/api/tickets" and method == "POST":
            u = self.require()
            d = self.body_json()
            with db() as conn:
                row = create_ticket(conn, d, u, cfg)
                return ticket_view(row, u)

        if path == "/api/tickets/mine" and method == "GET":
            u = self.require()
            with db() as conn:
                rows = conn.execute("SELECT * FROM tickets WHERE creator_id=? ORDER BY id DESC LIMIT 100", (u["id"],)).fetchall()
            return [ticket_view(r, u) for r in rows]

        if path == "/api/tickets/assigned" and method == "GET":
            u = self.require()
            with db() as conn:
                rows = conn.execute(
                    "SELECT * FROM tickets WHERE assignee_id=? AND status IN ('assigned','processing','pending_accept') ORDER BY id DESC",
                    (u["id"],)).fetchall()
            return [ticket_view(r, u) for r in rows]

        if path == "/api/pool" and method == "GET":
            u = self.require("worker", "dispatcher", "admin")
            with db() as conn:
                rows = conn.execute("SELECT * FROM tickets WHERE status='pending' ORDER BY id DESC LIMIT 100").fetchall()
            return [ticket_view(r, u, mask=True) for r in rows]

        m = re.match(r"^/api/tickets/(\d+)$", path)
        if m and method == "GET":
            u = self.require()
            with db() as conn:
                row = get_ticket(conn, m.group(1))
                if not can_view(row, u):
                    raise HttpError(403, "无权查看该工单")
                # 仅从工单池查看、尚未接单的处理人：隐藏提单人姓名全称/部门/电话
                privileged = (u["role"] in ("admin", "dispatcher")
                              or row["creator_id"] == u["id"]
                              or row["assignee_id"] == u["id"])
                return ticket_view(row, u, mask=not privileged)

        m = re.match(r"^/api/tickets/(\d+)/events$", path)
        if m and method == "GET":
            u = self.require()
            with db() as conn:
                row = get_ticket(conn, m.group(1))
                if not can_view(row, u):
                    raise HttpError(403, "无权查看该工单")
                events = conn.execute("SELECT * FROM ticket_events WHERE ticket_id=? ORDER BY id", (row["id"],)).fetchall()
                return [dict(e) for e in events]

        if path == "/api/notifications" and method == "GET":
            u = self.require()
            with db() as conn:
                rows = conn.execute("SELECT * FROM notifications WHERE user_id=? ORDER BY id DESC LIMIT 50", (u["id"],)).fetchall()
            return [dict(r) for r in rows]

        if path == "/api/notifications/read" and method == "POST":
            u = self.require()
            with db() as conn:
                conn.execute("UPDATE notifications SET is_read=1 WHERE user_id=?", (u["id"],))
            return {"ok": True}

        # ---- 状态操作 ----
        m = re.match(r"^/api/tickets/(\d+)/(claim|start|finish|confirm|note|remark)$", path)
        if m and method == "POST":
            u = self.require()
            tid, action = m.group(1), m.group(2)
            d = self.body_json()
            with db() as conn:
                row = get_ticket(conn, tid)
                if action == "claim":
                    if u["role"] not in ("worker", "dispatcher", "admin"):
                        raise HttpError(403, "只有处理人可以抢单")
                    if row["status"] != "pending":
                        raise HttpError(400, "该工单已被受理")
                    conn.execute("UPDATE tickets SET assignee_id=? WHERE id=?", (u["id"], row["id"]))
                    row = get_ticket(conn, tid)
                    return ticket_view(change_status(conn, row, "assigned", u, "%s 抢单成功" % u["name"]))
                if action == "start":
                    if row["assignee_id"] != u["id"] and u["role"] not in ("admin", "dispatcher"):
                        raise HttpError(403, "只能处理派给自己的工单")
                    if row["status"] != "assigned":
                        raise HttpError(400, "当前状态不能开始处理")
                    return ticket_view(change_status(conn, row, "processing", u, "开始处理"))
                if action == "finish":
                    if row["assignee_id"] != u["id"] and u["role"] not in ("admin", "dispatcher"):
                        raise HttpError(403, "只能完工派给自己的工单")
                    if row["status"] not in ("assigned", "processing"):
                        raise HttpError(400, "当前状态不能提交完工")
                    process = str(d.get("process") or "").strip()
                    result = str(d.get("result") or "").strip()
                    # 兼容升级前的旧客户端（只传 note）：把 note 当作处理过程，结果补默认值。
                    legacy = str(d.get("note") or "").strip()
                    if legacy and not process:
                        process = legacy
                    if legacy and not result:
                        result = "已处理完成"
                    if len(process) < 5:
                        raise HttpError(400, "请至少用 5 个字描述处理过程")
                    if not result:
                        raise HttpError(400, "请填写处理结果")
                    imgs = d.get("images") or []
                    if not isinstance(imgs, list) or len(imgs) > 3:
                        raise HttpError(400, "完工照片最多 3 张")
                    saved_imgs = [save_image(x) for x in imgs]
                    extra = {
                        "finish_process": process,
                        "finish_result": result,
                        "finish_images": json.dumps(saved_imgs, ensure_ascii=False),
                    }
                    summary = "处理过程：%s；处理结果：%s" % (process, result)
                    return ticket_view(change_status(conn, row, "pending_accept", u, summary, extra))
                if action == "confirm":
                    if row["creator_id"] != u["id"] and u["role"] not in ("admin", "dispatcher"):
                        raise HttpError(403, "只有提单人可以确认")
                    if row["status"] != "pending_accept":
                        raise HttpError(400, "当前状态不能确认")
                    extra = {"accept_remark": str(d.get("remark") or "").strip()}
                    if d.get("rating"):
                        extra["rating"] = max(1, min(5, int(d["rating"])))
                    return ticket_view(change_status(conn, row, "completed", u, str(d.get("remark") or "确认无误"), extra))
                if action in ("note", "remark"):
                    if not can_view(row, u):
                        raise HttpError(403, "无权操作该工单")
                    note = str(d.get("note") or "").strip()
                    if not note:
                        raise HttpError(400, "内容不能为空")
                    add_event(conn, row["id"], u, "处理备注" if action == "note" else "提单人补充", note)
                    targets = [row["assignee_id"], row["creator_id"]] if action == "note" else dispatcher_ids(conn) + [row["assignee_id"]]
                    notify(conn, [x for x in targets if x and x != u["id"]], row["id"], "note",
                           "工单 %s 有新留言" % row["ticket_no"],
                           wecom_message("工单 %s 有新留言" % row["ticket_no"], ["%s：%s" % (u["name"], note[:100])]))
                    return {"ok": True}

        # ---- 管理端 ----
        if path == "/api/admin/tickets" and method == "GET":
            self.require("admin", "dispatcher", "worker")
            sql = "SELECT * FROM tickets WHERE 1=1"
            args = []
            for key, col in (("status", "status"), ("floor", "floor"), ("category", "category_id"),
                             ("urgency", "urgency"), ("assignee", "assignee_id")):
                if q.get(key):
                    sql += " AND %s=?" % col
                    args.append(q[key])
            if q.get("from"):
                sql += " AND created_at>=?"
                args.append(q["from"] + " 00:00:00")
            if q.get("to"):
                sql += " AND created_at<=?"
                args.append(q["to"] + " 23:59:59")
            if q.get("q"):
                sql += " AND (ticket_no LIKE ? OR description LIKE ? OR location LIKE ? OR creator_name LIKE ?)"
                kw = "%" + q["q"] + "%"
                args.extend([kw, kw, kw, kw])
            if q.get("overdue") == "1":
                sql += " AND (response_overdue=1 OR resolve_overdue=1 OR (status IN ('pending','assigned','processing','pending_accept') AND resolve_due_at<?))"
                args.append(iso_now())
            sql += " ORDER BY id DESC LIMIT 500"
            with db() as conn:
                rows = conn.execute(sql, args).fetchall()
            return [ticket_view(r) for r in rows]

        if path == "/api/admin/tickets/export" and method == "GET":
            self.require("admin", "dispatcher")
            with db() as conn:
                rows = conn.execute("SELECT * FROM tickets ORDER BY id DESC").fetchall()
            buf = io.StringIO()
            w = csv.writer(buf)
            w.writerow(["工单号", "状态", "楼层", "具体位置", "类型", "紧急程度", "问题描述",
                        "提单人", "部门", "联系电话", "接单人", "创建时间", "派单时间", "完工时间",
                        "关闭时间", "响应时限", "完成时限", "响应超时", "处理超时", "评分", "验收备注",
                        "处理过程", "处理结果", "完工照片"])
            for r in rows:
                t = ticket_view(r)
                w.writerow([r["ticket_no"], t["status_name"], r["floor"], r["location"], r["category_name"],
                            r["urgency_name"], r["description"], r["creator_name"], r["creator_dept"],
                            r["contact_phone"], r["assignee_name"] or "", r["created_at"], r["assigned_at"] or "",
                            r["completed_at"] or "", r["closed_at"] or "", r["response_due_at"] or "",
                            r["resolve_due_at"] or "", "是" if t["response_overdue"] else "否",
                            "是" if t["resolve_overdue"] else "否", r["rating"] or "", r["accept_remark"] or "",
                            t["finish_process"], t["finish_result"], " ".join(t["finish_images"])])
            data = ("\ufeff" + buf.getvalue()).encode("utf-8")
            return (data, "text/csv; charset=utf-8")

        if path == "/api/admin/stats" and method == "GET":
            self.require("admin", "dispatcher")
            with db() as conn:
                return compute_stats(conn, q.get("from"), q.get("to"))

        if path == "/api/admin/users" and method == "GET":
            self.require("admin", "dispatcher")
            with db() as conn:
                if q.get("q"):
                    kw = "%" + q["q"] + "%"
                    rows = conn.execute("SELECT * FROM users WHERE employee_no LIKE ? OR name LIKE ? OR dept LIKE ? ORDER BY id", (kw, kw, kw)).fetchall()
                else:
                    rows = conn.execute("SELECT * FROM users ORDER BY id").fetchall()
            return [user_public(r) for r in rows]

        if path == "/api/admin/users" and method == "POST":
            self.require("admin", "dispatcher")
            d = self.body_json()
            require_keys(d, ["employee_no", "name"])
            with db() as conn:
                if conn.execute("SELECT id FROM users WHERE employee_no=?", (str(d["employee_no"]).strip(),)).fetchone():
                    raise HttpError(400, "该工号已存在")
                uid = conn.execute(
                    "INSERT INTO users(employee_no,name,dept,phone,role,wecom_userid,password_hash,enabled,created_at) "
                    "VALUES(?,?,?,?,?,?,?,1,?)",
                    (str(d["employee_no"]).strip(), str(d["name"]).strip(), str(d.get("dept") or "").strip(),
                     str(d.get("phone") or "").strip(), str(d.get("role") or "staff"),
                     str(d.get("wecom_userid") or "").strip(), hash_password(default_password()), iso_now()),
                ).lastrowid
                return user_public(conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone())

        m = re.match(r"^/api/admin/users/(\d+)$", path)
        if m and method == "PUT":
            self.require("admin", "dispatcher")
            d = self.body_json()
            with db() as conn:
                row = conn.execute("SELECT * FROM users WHERE id=?", (m.group(1),)).fetchone()
                if not row:
                    raise HttpError(404, "用户不存在")
                fields = {}
                for k in ("name", "dept", "phone", "role", "wecom_userid", "enabled"):
                    if k in d:
                        fields[k] = d[k]
                if "role" in fields and fields["role"] not in ROLE_NAMES:
                    raise HttpError(400, "角色无效")
                if fields:
                    conn.execute("UPDATE users SET %s WHERE id=?" % ",".join("%s=?" % k for k in fields),
                                 list(fields.values()) + [row["id"]])
                return user_public(conn.execute("SELECT * FROM users WHERE id=?", (row["id"],)).fetchone())

        m = re.match(r"^/api/admin/users/(\d+)/reset_password$", path)
        if m and method == "POST":
            self.require("admin", "dispatcher")
            pwd = default_password()
            with db() as conn:
                row = conn.execute("SELECT * FROM users WHERE id=?", (m.group(1),)).fetchone()
                if not row:
                    raise HttpError(404, "用户不存在")
                conn.execute("UPDATE users SET password_hash=? WHERE id=?", (hash_password(pwd), row["id"]))
            return {"password": pwd, "username": row["employee_no"]}

        m = re.match(r"^/api/admin/tickets/(\d+)/(assign|close|reopen)$", path)
        if m and method == "POST":
            u = self.require("admin", "dispatcher")
            tid, action = m.group(1), m.group(2)
            d = self.body_json()
            with db() as conn:
                row = get_ticket(conn, tid)
                if action == "assign":
                    aid = int(d.get("assignee_id") or 0)
                    target = conn.execute("SELECT * FROM users WHERE id=?", (aid,)).fetchone()
                    if not target or target["role"] not in ("worker", "dispatcher", "admin") or not target["enabled"]:
                        raise HttpError(400, "请选择有效的处理人")
                    conn.execute("UPDATE tickets SET assignee_id=?,assignee_name=? WHERE id=?", (aid, target["name"], row["id"]))
                    row = get_ticket(conn, tid)
                    if row["status"] == "pending":
                        return ticket_view(change_status(conn, row, "assigned", u, "行政指派给 %s" % target["name"]))
                    add_event(conn, row["id"], u, "改派", "改派给 %s" % target["name"])
                    return ticket_view(get_ticket(conn, tid))
                if action == "close":
                    reason = str(d.get("reason") or "").strip()
                    if not reason:
                        raise HttpError(400, "请填写关闭原因")
                    return ticket_view(change_status(conn, row, "closed", u, reason, {"close_reason": reason}))
                if action == "reopen":
                    if row["status"] not in ("closed", "completed"):
                        raise HttpError(400, "只有已关闭/已完成的工单可以重新打开")
                    conn.execute("UPDATE tickets SET status='pending',assignee_id=NULL,assignee_name='',"
                                 "closed_at=NULL,completed_at=NULL,close_reason=NULL,"
                                 "finish_process='',finish_result='',finish_images='[]' WHERE id=?", (row["id"],))
                    add_event(conn, row["id"], u, "重新打开", str(d.get("reason") or "重新受理"))
                    return ticket_view(get_ticket(conn, tid))

        if path == "/api/admin/config" and method == "GET":
            self.require("admin", "dispatcher")
            result = load_config()
            result["wecom"] = dict(result.get("wecom", {}))
            result["auth"] = {"admin_username": result.get("auth", {}).get("admin_username", "admin")}
            return result

        if path == "/api/admin/config" and method == "PUT":
            self.require("admin")
            d = self.body_json()
            cfg = load_config()
            for key in ("company", "floors", "categories", "urgency", "sla"):
                if key in d:
                    cfg[key] = d[key]
            if "wecom" in d:
                # 合并而非整体替换：设置页未展示的字段（如通讯录 Secret）不能被冲掉
                cfg.setdefault("wecom", {}).update(d["wecom"])
            if not isinstance(cfg["floors"], list) or not cfg["floors"]:
                raise HttpError(400, "至少配置一个楼层")
            if not isinstance(cfg["categories"], list) or not cfg["categories"]:
                raise HttpError(400, "至少配置一个报修类型")
            save_config(cfg)
            return {"ok": True, "config": cfg}

        if path == "/api/admin/wecom/sync" and method == "POST":
            self.require("admin")
            cfg = load_config().get("wecom", {})
            try:
                token = wecom_contact_token(cfg)
                dep_map = wecom_departments(token)
                data = wecom_get_json(
                    WECOM_API + "/user/list?access_token=%s&department_id=1&fetch_child=1"
                    % urllib.parse.quote(token))
                if data.get("errcode") != 0:
                    raise RuntimeError("读取成员失败: " + str(data.get("errmsg")))
            except Exception as exc:
                raise HttpError(400, "同步失败：%s。请确认服务器能访问 qyapi.weixin.qq.com，"
                                     "且 Secret 具备通讯录读取权限（推荐在企微后台「通讯录同步」"
                                     "取 Secret 填入下方）。" % exc)
            added = updated = 0
            with db() as conn:
                for w in data.get("userlist", []):
                    userid = str(w.get("userid") or "").strip()
                    if not userid:
                        continue
                    dl = w.get("department") or []
                    dept = dep_map.get(str(dl[0]), "") if dl else ""
                    _, is_new = wecom_upsert_user(
                        conn, userid, str(w.get("name") or userid).strip(),
                        dept, str(w.get("mobile") or "").strip())
                    if is_new:
                        added += 1
                    else:
                        updated += 1
            return {"ok": True, "added": added, "updated": updated,
                    "total": added + updated, "departments": len(dep_map)}

        if path == "/api/admin/wecom/test" and method == "POST":
            u = self.require("admin", "dispatcher")
            d = self.body_json()
            userid = str(d.get("userid") or u["wecom_userid"] or "").strip()
            if not userid:
                raise HttpError(400, "请先填写接收人的企业微信 UserID")
            result = wecom_send_sync([userid], wecom_message("企微推送测试", ["如果你收到这条消息，说明应用消息已配置成功。"]))
            if result != "ok":
                raise HttpError(400, result)
            return {"ok": True}

        raise HttpError(404, "接口不存在: " + path)


def main():
    init_db()
    if len(sys.argv) >= 2 and sys.argv[1] == "--reset-admin":
        # 不传参数 = 重置为统一默认密码；也可 --reset-admin 自定义密码
        new_pwd = sys.argv[2] if len(sys.argv) >= 3 and sys.argv[2].strip() else default_password()
        with db() as conn:
            cfg = load_config()
            username = cfg.get("auth", {}).get("admin_username", "admin")
            conn.execute("UPDATE users SET password_hash=?,enabled=1 WHERE employee_no=?",
                         (hash_password(new_pwd), username))
        print("管理员 %s 的密码已重置为：%s" % (username, new_pwd), flush=True)
        return
    cfg = load_config()
    threading.Thread(target=sla_loop, daemon=True).start()
    host = cfg["server"].get("host", "0.0.0.0")
    port = int(cfg["server"].get("port", 8080))
    srv = ThreadingHTTPServer((host, port), Handler)
    srv.daemon_threads = True
    print("=" * 58, flush=True)
    print("  %s 已启动" % cfg["company"]["system_name"], flush=True)
    print("  员工端(扫码):  http://<服务器IP>:%d/?f=2F" % port, flush=True)
    print("  管理端:        http://<服务器IP>:%d/admin" % port, flush=True)
    print("  数据目录:      %s" % DATA, flush=True)
    print("=" * 58, flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n服务已停止", flush=True)
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()
