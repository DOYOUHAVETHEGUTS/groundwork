"""SQLite persistence. One table, JSON blob per request."""
import json, os, sqlite3, threading, time

import rubric

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "data", "groundwork.db")
_lock = threading.Lock()


def _conn():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def init():
    with _lock, _conn() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS requests(
            id TEXT PRIMARY KEY, created REAL, updated REAL,
            title TEXT, department TEXT, request_type TEXT,
            status TEXT, score INTEGER, payload TEXT)""")


def _next_id(c):
    row = c.execute("SELECT id FROM requests ORDER BY created DESC LIMIT 1").fetchone()
    n = 4472
    if row and row["id"].startswith("CAP-"):
        try:
            n = int(row["id"].split("-")[1]) + 1
        except Exception:
            pass
    return f"CAP-{n}"


def save(req: dict) -> dict:
    now = time.time()
    with _lock, _conn() as c:
        rid = req.get("id") or _next_id(c)
        req["id"] = rid
        existing = c.execute("SELECT created FROM requests WHERE id=?", (rid,)).fetchone()
        created = existing["created"] if existing else now
        req["created"], req["updated"] = created, now
        c.execute("""INSERT INTO requests(id,created,updated,title,department,request_type,status,score,payload)
                     VALUES(?,?,?,?,?,?,?,?,?)
                     ON CONFLICT(id) DO UPDATE SET updated=excluded.updated,title=excluded.title,
                       department=excluded.department,request_type=excluded.request_type,
                       status=excluded.status,score=excluded.score,payload=excluded.payload""",
                  (rid, created, now, req.get("title", ""), req.get("department", ""),
                   req.get("request_type", ""), req.get("status", "draft"),
                   req.get("score") or 0, json.dumps(req)))
    return req


def get(rid: str):
    with _lock, _conn() as c:
        row = c.execute("SELECT payload FROM requests WHERE id=?", (rid,)).fetchone()
    return json.loads(row["payload"]) if row else None


def list_all():
    with _lock, _conn() as c:
        rows = c.execute("""SELECT id,title,department,request_type,status,score,updated
                            FROM requests ORDER BY updated DESC""").fetchall()
    return [dict(r) for r in rows]


def delete(rid: str):
    with _lock, _conn() as c:
        c.execute("DELETE FROM requests WHERE id=?", (rid,))


def blank(meta=None):
    r = dict(rubric.BLANK_REQUEST)
    r.update({"id": None, "status": "draft", "score": 0,
              "qualification": None, "created": None, "updated": None})
    r.update(meta or {})
    return r
