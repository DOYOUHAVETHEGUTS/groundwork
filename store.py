"""SQLite persistence: requests, users and org, audit log, images."""
import json
import os
import sqlite3
import threading
import time
import uuid

import rubric

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "data", "groundwork.db")
_lock = threading.RLock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS requests(id TEXT PRIMARY KEY, created REAL, updated REAL, title TEXT, department TEXT,
  request_type TEXT, status TEXT, score INTEGER, payload TEXT);
CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY, email TEXT UNIQUE, name TEXT, role TEXT, group_id TEXT,
  division_id TEXT, pw_hash TEXT, active INTEGER DEFAULT 1, source TEXT, created REAL, last_login REAL);
CREATE TABLE IF NOT EXISTS divisions(id TEXT PRIMARY KEY, name TEXT);
CREATE TABLE IF NOT EXISTS org_groups(id TEXT PRIMARY KEY, name TEXT, division_id TEXT);
CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, user_id TEXT, action TEXT,
  target TEXT, detail TEXT);
CREATE TABLE IF NOT EXISTS images(id TEXT PRIMARY KEY, request_id TEXT, owner TEXT, kind TEXT, label TEXT,
  citation TEXT, mime TEXT, data BLOB, created REAL, meta TEXT);
"""
REQ_COLS = {"owner": "TEXT DEFAULT ''", "group_id": "TEXT DEFAULT ''", "division_id": "TEXT DEFAULT ''",
            "preview": "INTEGER DEFAULT 0"}


def _conn():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def init():
    with _lock, _conn() as c:
        c.executescript(SCHEMA)
        have = {r["name"] for r in c.execute("PRAGMA table_info(requests)")}
        for col, decl in REQ_COLS.items():
            if col not in have:
                c.execute(f"ALTER TABLE requests ADD COLUMN {col} {decl}")


def _next_id(c):
    n = 4471
    for (rid,) in c.execute("SELECT id FROM requests"):
        if str(rid).startswith("CAP-") and str(rid)[4:].isdigit():
            n = max(n, int(rid[4:]))
    return f"CAP-{n + 1}"


# --------------------------------------------------------------------------- requests
def save(req: dict, owner: dict = None) -> dict:
    now = time.time()
    with _lock, _conn() as c:
        rid = req.get("id") or _next_id(c)
        req["id"] = rid
        row = c.execute("SELECT created, owner, group_id, division_id FROM requests WHERE id=?", (rid,)).fetchone()
        if row:
            created = row["created"]
            for k in ("owner", "group_id", "division_id"):
                req[k] = row[k] or req.get(k, "")
        else:
            created = now
            if owner:
                req.setdefault("owner", owner.get("id", ""))
                req.setdefault("group_id", owner.get("group_id", "") or "")
                req.setdefault("division_id", owner.get("division_id", "") or "")
                req.setdefault("owner_name", owner.get("name", ""))
        req["created"], req["updated"] = created, now
        c.execute("""INSERT INTO requests(id,created,updated,title,department,request_type,status,score,payload,
                       owner,group_id,division_id,preview) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                     ON CONFLICT(id) DO UPDATE SET updated=excluded.updated,title=excluded.title,
                       department=excluded.department,request_type=excluded.request_type,status=excluded.status,
                       score=excluded.score,payload=excluded.payload""",
                  (rid, created, now, req.get("title", ""), req.get("department", ""), req.get("request_type", ""),
                   req.get("status", "draft"), req.get("score") or 0, json.dumps(req), req.get("owner", ""),
                   req.get("group_id", ""), req.get("division_id", ""), 1 if req.get("preview") else 0))
    return req


def get(rid):
    with _lock, _conn() as c:
        row = c.execute("SELECT payload FROM requests WHERE id=?", (rid,)).fetchone()
    return rubric.upgrade(json.loads(row["payload"])) if row else None


def list_requests(scope=None, preview=False):
    """scope: {} = everything; {'owner': id}; {'group_ids': [...], 'or_owner': id}; {'division_ids': [...], ...}"""
    scope = scope or {}
    where, args = ["r.preview=?"], [1 if preview else 0]
    ors = []
    if "owner" in scope:
        ors.append("r.owner=?"); args.append(scope["owner"])
    for k, col in (("group_ids", "r.group_id"), ("division_ids", "r.division_id")):
        if scope.get(k):
            ors.append(f"{col} IN ({','.join('?' * len(scope[k]))})"); args += list(scope[k])
    if scope.get("or_owner"):
        ors.append("r.owner=?"); args.append(scope["or_owner"])
    if ors:
        where.append("(" + " OR ".join(ors) + ")")
    elif scope.get("none"):
        where.append("0")
    with _lock, _conn() as c:
        rows = c.execute(f"""SELECT r.id,r.title,r.department,r.request_type,r.status,r.score,r.updated,r.created,
                                    r.owner,r.group_id,r.division_id,r.payload,u.name AS owner_name,
                                    g.name AS group_name, d.name AS division_name
                             FROM requests r LEFT JOIN users u ON u.id=r.owner
                             LEFT JOIN org_groups g ON g.id=r.group_id LEFT JOIN divisions d ON d.id=r.division_id
                             WHERE {' AND '.join(where)} ORDER BY r.updated DESC""", args).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        p = json.loads(d.pop("payload") or "{}")
        co = p.get("coaching") or {}
        q = p.get("qualification") or {}
        d.update(rounds=len([x for x in co.get("rounds") or [] if x.get("answered")]),
                 coaching_status=co.get("status", ""), escalated=bool(co.get("escalation")),
                 gates_failed=[g["label"] for g in q.get("gates") or [] if not g.get("passed")],
                 cre=p.get("cre_driven", ""), quotes=len(rubric.valid_quotes(p)), demo=bool(p.get("demo")),
                 review_notes=len(p.get("review_notes") or []))
        out.append(d)
    return out


def list_all():
    return list_requests({}, False)


def delete(rid):
    with _lock, _conn() as c:
        c.execute("DELETE FROM requests WHERE id=?", (rid,))
        c.execute("DELETE FROM images WHERE request_id=?", (rid,))


def blank(meta=None):
    r = rubric.upgrade({})
    r.update({"id": None, "status": "draft", "score": 0, "qualification": None, "created": None, "updated": None})
    r.update(meta or {})
    return r


# --------------------------------------------------------------------------- users & org
def _user(row):
    if not row:
        return None
    u = dict(row)
    u.pop("pw_hash", None)
    return u


def create_user(email, name, role, group_id="", division_id="", pw_hash="", source="local", uid=None):
    uid = uid or "u_" + uuid.uuid4().hex[:10]
    with _lock, _conn() as c:
        c.execute("""INSERT INTO users(id,email,name,role,group_id,division_id,pw_hash,active,source,created)
                     VALUES(?,?,?,?,?,?,?,1,?,?)""", (uid, email.lower().strip(), name, role, group_id, division_id,
                                                       pw_hash, source, time.time()))
    return get_user(uid)


def get_user(uid, with_hash=False):
    with _lock, _conn() as c:
        row = c.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    return dict(row) if (row and with_hash) else _user(row)


def get_user_by_email(email, with_hash=False):
    with _lock, _conn() as c:
        row = c.execute("SELECT * FROM users WHERE email=?", ((email or "").lower().strip(),)).fetchone()
    return dict(row) if (row and with_hash) else _user(row)


def list_users(source=None, exclude_source=None):
    q, a = "SELECT * FROM users", []
    if source:
        q += " WHERE source=?"; a.append(source)
    elif exclude_source:
        q += " WHERE source<>?"; a.append(exclude_source)
    with _lock, _conn() as c:
        return [_user(r) for r in c.execute(q + " ORDER BY role DESC, name", a).fetchall()]


def update_user(uid, **fields):
    allowed = {k: v for k, v in fields.items() if k in ("name", "role", "group_id", "division_id", "pw_hash",
                                                         "active", "last_login", "email")}
    if not allowed:
        return get_user(uid)
    with _lock, _conn() as c:
        c.execute(f"UPDATE users SET {', '.join(k + '=?' for k in allowed)} WHERE id=?", (*allowed.values(), uid))
    return get_user(uid)


def count_users(exclude_source="demo"):
    with _lock, _conn() as c:
        return c.execute("SELECT COUNT(*) FROM users WHERE source<>?", (exclude_source,)).fetchone()[0]


def ensure_division(name, did=None):
    with _lock, _conn() as c:
        row = c.execute("SELECT id FROM divisions WHERE name=?", (name,)).fetchone()
        if row:
            return row["id"]
        did = did or "d_" + uuid.uuid4().hex[:8]
        c.execute("INSERT INTO divisions(id,name) VALUES(?,?)", (did, name))
        return did


def ensure_group(name, division_id, gid=None):
    with _lock, _conn() as c:
        row = c.execute("SELECT id FROM org_groups WHERE name=? AND division_id=?", (name, division_id)).fetchone()
        if row:
            return row["id"]
        gid = gid or "g_" + uuid.uuid4().hex[:8]
        c.execute("INSERT INTO org_groups(id,name,division_id) VALUES(?,?,?)", (gid, name, division_id))
        return gid


def org(preview=False):
    with _lock, _conn() as c:
        divs = [dict(r) for r in c.execute("SELECT * FROM divisions ORDER BY name")]
        grps = [dict(r) for r in c.execute("SELECT * FROM org_groups ORDER BY name")]
    keep = (lambda i: i.startswith("pv_")) if preview else (lambda i: not i.startswith("pv_"))
    return {"divisions": [d for d in divs if keep(d["id"])], "groups": [g for g in grps if keep(g["id"])]}


# --------------------------------------------------------------------------- audit
def audit(user_id, action, target="", detail=""):
    with _lock, _conn() as c:
        c.execute("INSERT INTO audit(ts,user_id,action,target,detail) VALUES(?,?,?,?,?)",
                  (time.time(), user_id or "", action, target or "", str(detail or "")[:500]))


def list_audit(limit=200, preview=False):
    with _lock, _conn() as c:
        rows = c.execute("""SELECT a.*, u.name AS user_name FROM audit a LEFT JOIN users u ON u.id=a.user_id
                            WHERE (a.user_id LIKE 'pv_%') = ? ORDER BY a.id DESC LIMIT ?""",
                         (1 if preview else 0, limit)).fetchall()
    return [dict(r) for r in rows]


# --------------------------------------------------------------------------- images
def save_image(request_id, owner, kind, label, citation, data, mime="image/png", meta=None):
    iid = "img_" + uuid.uuid4().hex[:12]
    with _lock, _conn() as c:
        c.execute("""INSERT INTO images(id,request_id,owner,kind,label,citation,mime,data,created,meta)
                     VALUES(?,?,?,?,?,?,?,?,?,?)""", (iid, request_id, owner, kind, label, citation, mime,
                                                      sqlite3.Binary(data), time.time(), json.dumps(meta or {})))
    return iid


def get_image(iid):
    with _lock, _conn() as c:
        row = c.execute("SELECT * FROM images WHERE id=?", (iid,)).fetchone()
    return dict(row) if row else None


def delete_image(iid):
    with _lock, _conn() as c:
        c.execute("DELETE FROM images WHERE id=?", (iid,))


def count_images(kind, since=0, request_id=None):
    q, a = "SELECT COUNT(*) FROM images WHERE kind=? AND created>=?", [kind, since]
    if request_id:
        q += " AND request_id=?"; a.append(request_id)
    with _lock, _conn() as c:
        return c.execute(q, a).fetchone()[0]
