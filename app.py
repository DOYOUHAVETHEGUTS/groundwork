"""Groundwork — five-point capital request intake. Standard library HTTP server.

    python app.py                      # http://localhost:8765
    GW_EDITION=v2 python app.py        # named accounts + director / admin
"""
import argparse
import base64
import json
import mimetypes
import os
import re
import sys
import time
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import agents  # noqa: E402
import auth  # noqa: E402
import coach  # noqa: E402
import config  # noqa: E402
import docimport  # noqa: E402
import exporter  # noqa: E402
import images  # noqa: E402
import llm  # noqa: E402
import notify  # noqa: E402
import preview  # noqa: E402
import rubric  # noqa: E402
import store  # noqa: E402

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATES, STATIC, TRAINING = (os.path.join(BASE_DIR, d) for d in ("templates", "static", "training"))
EDITABLE_META = ("title", "request_type", "department", "requester", "finance_contact_name", "finance_contact_email")
TRAINING_DOCS = [("weak", "Weak", "Training_5pt_03_Weak_Engine_Tooling_Request.docx"),
                 ("refined", "Refined", "Training_5pt_02_Refined_Engine_Transport_Stands.docx"),
                 ("exemplary", "Exemplary", "Training_5pt_01_Exemplary_Hangar_Electrical_Upgrade.docx")]
_training_cache = {}

LOGIN_HTML = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Groundwork — sign in</title>
<style>:root{color-scheme:dark}*{box-sizing:border-box}body{margin:0;min-height:100vh;display:grid;place-items:center;
font:15px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif;background:#0e1017;color:#e7e9ee}
.card{width:min(92vw,400px);background:#171a23;border:1px solid #262b38;border-radius:16px;padding:28px 26px}
h1{margin:0 0 4px;font-size:20px}p.sub{margin:0 0 18px;color:#9aa1b2;font-size:13px}
label{display:block;font-size:12px;color:#9aa1b2;margin:12px 0 6px}input{width:100%;padding:11px 12px;border-radius:10px;
border:1px solid #2c3242;background:#0e1017;color:#e7e9ee;font-size:15px}input:focus{outline:none;border-color:#5b8cff}
button,.sso{display:block;width:100%;margin-top:16px;padding:11px;border:0;border-radius:10px;background:#5b8cff;
color:#0b0d13;font-weight:600;font-size:15px;cursor:pointer;text-align:center;text-decoration:none}
.err{margin-top:12px;min-height:18px;color:#ff8080;font-size:13px}.ed{font:11px ui-monospace,monospace;color:#9aa1b2;
letter-spacing:.12em;text-transform:uppercase;margin-bottom:8px}details{margin-top:14px;color:#9aa1b2;font-size:13px}</style>
</head><body><form class="card" onsubmit="return signin(event)"><div class="ed">__EDITION__</div><h1>Groundwork</h1>
<p class="sub">__SUB__</p>__FIELDS__<div class="err" id="err">__ERR__</div></form>
<script>async function signin(e){e.preventDefault();const err=document.getElementById('err');err.textContent='';
const em=document.getElementById('em');const r=await fetch('/login',{method:'POST',headers:{'content-type':'application/json'},
body:JSON.stringify({email:em?em.value:'',password:document.getElementById('pw').value})});
if(r.ok)location.href='/';else{const d=await r.json().catch(()=>({}));err.textContent=d.error||'Sign-in failed.';}return false;}
</script></body></html>"""


def login_page(err=""):
    pw = '<label for="pw">Password</label><input id="pw" type="password" autocomplete="current-password" autofocus>'
    em = '<label for="em">Work email</label><input id="em" type="email" autocomplete="username" autofocus>'
    if auth.EDITION == "v1":
        sub, fields, ed = "Enter the access password to continue.", pw + "<button>Sign in</button>", "V1 · shared access"
    elif auth.EDITION == "v2":
        sub, fields, ed = "Sign in with your Groundwork account.", em + pw.replace(" autofocus", "") + "<button>Sign in</button>", "V2 · named accounts"
    else:
        sso = ('<a class="sso" href="/auth/sso/start">Sign in with Microsoft</a>' if auth.sso_configured()
               else '<p class="sub">Single sign-on isn\'t configured yet. Set GW_OIDC_ISSUER, GW_OIDC_CLIENT_ID and GW_OIDC_CLIENT_SECRET.</p>')
        sub, ed = "Use your company account.", "V3 · company single sign-on"
        fields = sso + "<details><summary>Break-glass administrator</summary>" + em + pw.replace(" autofocus", "") + "<button>Sign in</button></details>"
    return LOGIN_HTML.replace("__EDITION__", ed).replace("__SUB__", sub).replace("__FIELDS__", fields).replace("__ERR__", err)


def _training(key):
    if key not in _training_cache:
        name = next((f for k, _, f in TRAINING_DOCS if k == key), None)
        path = os.path.join(TRAINING, name or "")
        if not name or not os.path.exists(path):
            return None
        r = docimport.parse_docx(open(path, "rb").read())
        r.update(id=f"TRAINING-{key.upper()}", demo_rules_only=True, training=True)
        q = agents.qualify(r)
        r.update(qualification=q, score=q["overall"], status=q["status"])
        _training_cache[key] = r
    return _training_cache[key]


def _relabel(req):
    for i, e in enumerate(req.get("exhibits") or []):
        e["label"] = f"Exhibit {chr(65 + i)}" if i < 26 else f"Exhibit {i + 1}"


class Handler(BaseHTTPRequestHandler):
    server_version = "Groundwork/2.0"

    # ---------------------------------------------------------- plumbing
    def _send(self, code, body=b"", ctype="application/json", extra=None):
        body = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            for vv in (v if isinstance(v, list) else [v]):
                self.send_header(k, vv)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, obj, code=200, extra=None):
        self._send(code, json.dumps(obj, default=str), "application/json", extra)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if not n or n > 12 * 1024 * 1024:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode("utf-8"))
        except Exception:
            return {}

    def _file(self, path, ctype=None):
        if not os.path.isfile(path):
            return self._send(404, b"Not found", "text/plain")
        with open(path, "rb") as f:
            self._send(200, f.read(), ctype or mimetypes.guess_type(path)[0] or "application/octet-stream")

    def log_message(self, fmt, *args):
        sys.stderr.write("  %s\n" % (fmt % args))

    def _cookies(self):
        ck = SimpleCookie(self.headers.get("Cookie", ""))
        return {k: v.value for k, v in ck.items()}

    def _ctx(self):
        """Resolve the signed-in user. Returns (user, acting, preview) or None after replying 401/302."""
        u = auth.user_from_cookies(self._cookies())
        if not u:
            if urlparse(self.path).path.startswith("/api/"):
                self._json({"error": "auth required"}, 401)
            else:
                self._send(302, b"", "text/plain", {"Location": "/login"})
            return None
        eff, pv = auth.effective(u, self._cookies())
        return u, eff, pv

    def _load(self, rid, eff, pv, write=False):
        r = store.get(rid)
        if not r or bool(r.get("preview")) != pv or not (auth.can_write(eff, r) if write else auth.can_read(eff, r)):
            self._json({"error": "not found" if not r or not auth.can_read(eff, r) else "read-only for your role"},
                       404 if not r or not auth.can_read(eff, r) else 403)
            return None
        return r

    def _save(self, r, eff, pv, action=None):
        r["preview"] = pv
        r = store.save(r, owner=eff)
        if action:
            store.audit(eff["id"], action, r["id"])
        return r

    # ---------------------------------------------------------- GET
    def do_GET(self):
        u = urlparse(self.path)
        p, q = u.path, parse_qs(u.query)
        arg = lambda k, d="": (q.get(k) or [d])[0]  # noqa: E731
        if p == "/login":
            return self._send(200, login_page(), "text/html; charset=utf-8")
        if p == "/logout":
            return self._send(302, b"", "text/plain", {"Set-Cookie": [auth.cookie("gw_session", "", 0),
                                                                       auth.cookie("gw_preview", "", 0)],
                                                       "Location": "/login"})
        if p == "/healthz":
            return self._json({"ok": True, "edition": auth.EDITION})
        if p == "/auth/sso/start":
            if not auth.sso_configured():
                return self._send(200, login_page("Single sign-on isn't configured."), "text/html; charset=utf-8")
            url, tok = auth.sso_start()
            return self._send(302, b"", "text/plain", {"Location": url, "Set-Cookie": auth.cookie("gw_sso", tok, 600)})
        if p == "/auth/sso/callback":
            try:
                user = auth.sso_callback(arg("code"), arg("state"), self._cookies().get("gw_sso"))
            except Exception as e:
                return self._send(200, login_page(str(e)[:200]), "text/html; charset=utf-8")
            store.audit(user["id"], "login.sso", user["email"])
            return self._send(302, b"", "text/plain", {"Location": "/", "Set-Cookie": [
                auth.cookie("gw_session", auth.make_token(user["id"]), auth.TTL), auth.cookie("gw_sso", "", 0)]})

        ctx = self._ctx()
        if not ctx:
            return
        user, eff, pv = ctx
        if p in ("/", "/index.html"):
            return self._file(os.path.join(TEMPLATES, "index.html"), "text/html; charset=utf-8")
        if p.startswith("/static/"):
            return self._file(os.path.join(STATIC, os.path.basename(p)))
        if p == "/api/me":
            return self._json({"user": auth.public(user), "acting": auth.public(eff), "preview": pv,
                               "edition": auth.EDITION, "can_preview": auth.can_preview(user),
                               "is_admin": auth.is_admin(eff), "can_dashboard": auth.can_dashboard(eff),
                               "roles": auth.EDITION_ROLES[auth.EDITION], "sso": auth.sso_configured()})
        if p == "/api/schema":
            cfg = config.load()
            return self._json({
                "sections": [{"key": k, "label": l} for k, l in rubric.SECTIONS],
                "categories": [{"name": n, "weight": w, "note": d} for n, w, d in rubric.CATEGORIES],
                "llm_ready": llm.available(), "image_ready": images.image_ready(cfg),
                "image_budget": {"per_request": cfg["image_per_request"], "monthly_cap": cfg["image_monthly_cap"]},
                "auth_required": True, "edition": auth.EDITION,
                "threshold": int(cfg.get("min_score_to_advance", 75)), "max_rounds": coach.MAX_ROUNDS,
                "guide": coach.INTAKE_GUIDE,
                "demo_scenarios": [{"key": k, **v} for k, v in coach.DEMO_SCENARIOS.items()],
                "training": [{"key": k, "label": l} for k, l, _ in TRAINING_DOCS]})
        if p == "/api/settings":
            if not auth.is_admin(eff):
                return self._json({"error": "admins only"}, 403)
            return self._json(config.redacted())
        if p == "/api/requests":
            return self._json({"items": store.list_requests(auth.scope(eff), pv)})
        if p.startswith("/api/requests/"):
            r = self._load(p.rsplit("/", 1)[-1], eff, pv)
            if r:
                r["_can_write"] = auth.can_write(eff, r)
                self._json(r)
            return
        if p == "/api/export":
            r = _training(arg("id").split("-")[-1].lower()) if arg("id").startswith("TRAINING-") else self._load(arg("id"), eff, pv)
            if not r:
                return
            fmt = arg("format", "docx")
            if fmt == "json":
                data, name, ctype = exporter.to_json_bytes(r)
            elif fmt == "md":
                data, name, ctype = exporter.to_markdown(r).encode(), f"{r['id']}.md", "text/markdown"
            else:
                data, name, ctype = exporter.to_docx_bytes(r)
            store.audit(eff["id"], "request.export", r["id"], fmt)
            return self._send(200, data, ctype, {"Content-Disposition": f'attachment; filename="{name}"'})
        if p == "/api/flow.png":
            r = self._load(arg("id"), eff, pv)
            if not r:
                return
            try:
                return self._send(200, images.flow_png(r.get("flow_steps") or [], "Process flow"), "image/png")
            except images.ImageError as e:
                return self._json({"error": str(e)}, 400)
        if p.startswith("/api/images/"):
            im = store.get_image(p.rsplit("/", 1)[-1])
            if not im or (im["request_id"] and not self._load(im["request_id"], eff, pv)):
                return None if im else self._json({"error": "not found"}, 404)
            return self._send(200, bytes(im["data"]), im["mime"])
        if p == "/api/references":
            try:
                return self._json({"items": images.reference_search(arg("q"))})
            except images.ImageError as e:
                return self._json({"error": str(e)}, 400)
        if p == "/api/training":
            out = []
            for k, label, _ in TRAINING_DOCS:
                r = _training(k)
                if r:
                    out.append({"key": k, "label": label, "id": r["id"], "title": r["title"], "score": r["score"],
                                "status": r["status"], "gates": r["qualification"]["gates"]})
            return self._json({"items": out})
        if p.startswith("/api/training/"):
            key = p.rsplit("/", 1)[-1]
            if arg("download"):
                name = next((f for k, _, f in TRAINING_DOCS if k == key), "")
                return self._file(os.path.join(TRAINING, name),
                                  "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
            r = _training(key)
            return self._json(r) if r else self._json({"error": "not found"}, 404)
        if p == "/api/dashboard":
            if not auth.can_dashboard(eff):
                return self._json({"error": "directors and above"}, 403)
            items = store.list_requests(auth.scope(eff), pv)
            by = lambda k: {s: sum(1 for i in items if i[k] == s) for s in {i[k] for i in items}}  # noqa: E731
            gates = {}
            for i in items:
                for g in i["gates_failed"]:
                    gates[g] = gates.get(g, 0) + 1
            scored = [i["score"] for i in items if i["score"]]
            return self._json({"items": items, "summary": {
                "total": len(items), "by_status": by("status"), "gates_failed": gates,
                "escalated": sum(1 for i in items if i["escalated"]),
                "avg_score": round(sum(scored) / len(scored)) if scored else None,
                "ready": sum(1 for i in items if i["status"] in ("ready", "handoff"))},
                "scope": {"director": "your group", "leader": "your division"}.get(eff.get("role"), "all divisions")})
        if p.startswith("/api/admin/"):
            if not auth.is_admin(eff):
                return self._json({"error": "admins only"}, 403)
            if p == "/api/admin/users":
                return self._json({"items": store.list_users(source="demo") if pv else store.list_users(exclude_source="demo"),
                                   "org": store.org(pv), "roles": auth.EDITION_ROLES["v3" if pv else auth.EDITION]})
            if p == "/api/admin/audit":
                return self._json({"items": store.list_audit(200, pv)})
            if p == "/api/admin/usage":
                cfg = config.load()
                m0 = time.mktime(time.localtime()[:2] + (1, 0, 0, 0, 0, 0, -1))
                return self._json({"images_this_month": store.count_images("generated", m0),
                                   "monthly_cap": cfg["image_monthly_cap"], "per_request": cfg["image_per_request"],
                                   "llm_ready": llm.available(), "image_ready": images.image_ready(cfg),
                                   "provider": cfg["provider"], "model": cfg["model"]})
        if p == "/api/preview/personas":
            if not auth.can_preview(user):
                return self._json({"error": "not allowed"}, 403)
            return self._json({"v2": preview.personas("v2"), "v3": preview.personas("v3")})
        return self._send(404, b"Not found", "text/plain")

    # ---------------------------------------------------------- POST
    def do_POST(self):
        p = urlparse(self.path).path
        if p == "/login":
            b = self._body()
            u = auth.local_login(b.get("email"), b.get("password"))
            if not u:
                return self._json({"ok": False, "error": "Incorrect email or password." if auth.EDITION != "v1"
                                   else "Incorrect password."}, 401)
            store.audit(u["id"], "login.local", u.get("email", ""))
            return self._json({"ok": True}, extra={"Set-Cookie": auth.cookie("gw_session", auth.make_token(u["id"]), auth.TTL)})
        ctx = self._ctx()
        if not ctx:
            return
        user, eff, pv = ctx
        b = self._body()

        # ---- preview personas (V2 / V3 tabs)
        if p == "/api/preview/enter":
            if not auth.can_preview(user):
                return self._json({"error": "not allowed"}, 403)
            preview.seed()
            pu = store.get_user(b.get("persona") or "")
            if not pu or pu.get("source") != "demo":
                return self._json({"error": "unknown persona"}, 400)
            store.audit(pu["id"], "preview.enter", "", f"by {user.get('email') or user['id']}")
            return self._json({"ok": True}, extra={"Set-Cookie": auth.cookie("gw_preview", auth.make_token(pu["id"], 4 * 3600), 4 * 3600)})
        if p == "/api/preview/exit":
            return self._json({"ok": True}, extra={"Set-Cookie": auth.cookie("gw_preview", "", 0)})

        # ---- settings (admins)
        if p.startswith("/api/settings"):
            if not auth.is_admin(eff) or pv:
                return self._json({"error": "admins only" if not pv else "settings are disabled in preview"}, 403)
            if p == "/api/settings":
                store.audit(eff["id"], "settings.save", "", ", ".join(sorted(k for k in b if k in config.DEFAULTS)))
                return self._json(config.save(b))
            if p == "/api/settings/test":
                if b:
                    config.save(b)
                return self._json(llm.test_connection())
            if p == "/api/settings/test-alert":
                return self._json(notify.send_test())

        # ---- admin: users and org
        if p.startswith("/api/admin/"):
            if not auth.is_admin(eff):
                return self._json({"error": "admins only"}, 403)
            if p == "/api/admin/org":
                did = store.ensure_division(b.get("division") or "Finance", ("pv_d_" + str(int(time.time()))) if pv else None)
                gid = store.ensure_group(b["group"], did, ("pv_g_" + str(int(time.time()))) if pv else None) if b.get("group") else None
                store.audit(eff["id"], "admin.org", b.get("division", ""), b.get("group", ""))
                return self._json({"division_id": did, "group_id": gid, "org": store.org(pv)})
            if p == "/api/admin/users":
                if auth.EDITION == "v1" and not pv:
                    return self._json({"error": "V1 uses one shared password. Set GW_EDITION=v2 for named accounts."}, 400)
                if auth.EDITION == "v3" and not pv:
                    return self._json({"error": "In V3, people are added in Entra ID and mapped by role; they appear here on first sign-in."}, 400)
                email, role = (b.get("email") or "").strip().lower(), b.get("role")
                if not re.match(r"[^@\s]+@[^@\s]+$", email) or role not in auth.ROLE_RANK:
                    return self._json({"error": "A valid email and role are required."}, 400)
                if store.get_user_by_email(email):
                    return self._json({"error": "That email already has an account."}, 400)
                if not pv and len(b.get("password") or "") < 12:
                    return self._json({"error": "Use a password of at least 12 characters."}, 400)
                nu = store.create_user(email, b.get("name") or email, role, b.get("group_id", ""), b.get("division_id", ""),
                                       auth.hash_pw(b["password"]) if b.get("password") else "",
                                       source="demo" if pv else "local", uid=("pv_" + os.urandom(4).hex()) if pv else None)
                store.audit(eff["id"], "admin.user.create", nu["id"], f"{email} as {role}")
                return self._json(nu)
            if p == "/api/admin/users/update":
                tu = store.get_user(b.get("id") or "")
                if not tu or (tu.get("source") == "demo") != pv:
                    return self._json({"error": "not found"}, 404)
                if tu["id"] == eff["id"] and (b.get("role") not in (None, "admin") or b.get("active") is False):
                    return self._json({"error": "You can't remove your own admin access."}, 400)
                fields = {k: b[k] for k in ("name", "role", "group_id", "division_id") if k in b}
                if "active" in b:
                    fields["active"] = 1 if b["active"] else 0
                if b.get("password"):
                    if len(b["password"]) < 12:
                        return self._json({"error": "Use a password of at least 12 characters."}, 400)
                    fields["pw_hash"] = auth.hash_pw(b["password"])
                store.audit(eff["id"], "admin.user.update", tu["id"], ", ".join(k for k in fields if k != "pw_hash")
                            + (" + password reset" if "pw_hash" in fields else ""))
                return self._json(store.update_user(tu["id"], **fields))

        # ---- creating requests
        if p == "/api/structure":
            text = (b.get("text") or "").strip()
            if len(text) < 8:
                return self._json({"error": "Add a sentence or two so we can organize it."}, 400)
            cfg = config.load()
            meta = {k: (b.get(k) or "").strip() for k in EDITABLE_META}
            meta["title"] = meta["title"] or "Untitled request"
            meta["department"] = meta["department"] or cfg.get("default_department", "")
            req = store.blank(meta)
            req["original_description"] = text
            d = agents.structure(text, meta)
            req.update({k: v for k, v in d.items() if k in rubric.BLANK_REQUEST or k in ("engine", "engine_error")})
            return self._json(self._save(req, eff, pv, "request.create"))
        if p == "/api/import":
            try:
                data = base64.b64decode(b.get("data") or "")
                req = store.blank()
                req.update(docimport.parse_docx(data))
            except Exception as e:
                return self._json({"error": f"Couldn't read that Word file: {str(e)[:160]}"}, 400)
            req["original_description"] = req.get("original_description") or f"Imported from {b.get('filename', 'a Word file')}"
            q = agents.qualify(req)
            req.update(qualification=q, score=q["overall"], status=q["status"])
            return self._json(self._save(req, eff, pv, "request.import"))
        if p == "/api/demo/start":
            scen = b.get("scenario") if b.get("scenario") in coach.DEMO_SCENARIOS else "pass"
            rules_only = not (b.get("use_model") and llm.available())
            meta = {"title": f"DEMO · BOS facilities truck ({coach.DEMO_SCENARIOS[scen]['label']})",
                    "request_type": "Vehicle / GSE", "department": "Cargo Facilities", "requester": "Demo requester",
                    "finance_contact_name": "Demo Finance Partner", "finance_contact_email": "finance.partner@example.com"}
            req = store.blank(meta)
            req.update(demo=True, demo_scenario=scen, demo_rules_only=rules_only, original_description=coach.DEMO_TEXT,
                       **coach.demo_seed(scen))
            req.update({k: v for k, v in agents.structure(coach.DEMO_TEXT, meta, force_rules=rules_only).items()
                        if k in rubric.BLANK_REQUEST or k in ("engine", "engine_error")})
            qq = agents.qualify(req)
            req.update(qualification=qq, score=qq["overall"], status=qq["status"])
            return self._json(self._save(req, eff, pv, "demo.start"))

        # ---- editing an existing request
        if p in ("/api/requests", "/api/qualify"):
            if b.get("id"):
                req = self._load(b["id"], eff, pv, write=True)
                if not req:
                    return
            else:
                req = store.blank()
            for k in list(rubric.BLANK_REQUEST) + list(EDITABLE_META):
                if k in b and k not in ("missing_information",):
                    req[k] = b[k]
            if p == "/api/qualify":
                qq = agents.qualify(req)
                req.update(qualification=qq, score=qq["overall"], status=qq["status"])
            return self._json(self._save(req, eff, pv, "request.score" if p == "/api/qualify" else "request.save"))

        if p in ("/api/coach/questions", "/api/coach/answers", "/api/coach/escalate", "/api/demo/answers"):
            r = self._load(b.get("id"), eff, pv, write=p != "/api/demo/answers")
            if not r:
                return
            try:
                if p == "/api/coach/questions":
                    r = coach.start_round(r)
                elif p == "/api/coach/answers":
                    r = coach.submit_answers(r, b.get("answers") or {}, notifier=notify.escalate)
                    if (r.get("coaching") or {}).get("escalation"):
                        store.audit(eff["id"], "escalation", r["id"], r["coaching"]["escalation"].get("method"))
                elif p == "/api/coach/escalate":
                    r.setdefault("coaching", {"rounds": []})["escalation"] = notify.escalate(r)
                else:
                    return self._json({"answers": coach.demo_answers(r)})
            except coach.CoachError as e:
                return self._json({"error": str(e)}, 400)
            return self._json(self._save(r, eff, pv, p.replace("/api/", "").replace("/", ".")))

        if p == "/api/review-note":
            r = self._load(b.get("id"), eff, pv)
            if not r:
                return
            if not auth.can_dashboard(eff):
                return self._json({"error": "directors and above"}, 403)
            note = (b.get("note") or "").strip()[:1200]
            if not note:
                return self._json({"error": "Write a note first."}, 400)
            r.setdefault("review_notes", []).append({"by": eff["id"], "name": eff.get("name"), "ts": time.time(), "note": note})
            store.audit(eff["id"], "review.note", r["id"])
            return self._json(store.save(r))

        # ---- images
        if p.startswith("/api/images/"):
            r = self._load(b.get("id"), eff, pv, write=True)
            if not r:
                return
            cfg = config.load()
            section = b.get("section") if b.get("section") in dict(rubric.SECTIONS + [rubric.APPENDIX]) else "current_situation"
            try:
                if p == "/api/images/generate":
                    m0 = time.mktime(time.localtime()[:2] + (1, 0, 0, 0, 0, 0, -1))
                    if store.count_images("generated", request_id=r["id"]) >= int(cfg["image_per_request"]):
                        return self._json({"error": f"This request has used its {cfg['image_per_request']} example images."}, 400)
                    if store.count_images("generated", m0) >= int(cfg["image_monthly_cap"]):
                        return self._json({"error": "The monthly example-image budget is used up. An admin can raise it."}, 400)
                    png, cite = images.generate_example(b.get("subject", ""), cfg)
                    kind, title = "example", "Example: " + (b.get("subject") or "")[:90]
                    iid = store.save_image(r["id"], eff["id"], "generated", title, cite, png)
                elif p == "/api/images/reference":
                    png, cite = images.reference_fetch(b.get("candidate") or {})
                    kind, title = "reference", "Reference example: " + str((b.get("candidate") or {}).get("title", ""))[:90]
                    iid = store.save_image(r["id"], eff["id"], "reference", title, cite, png)
                elif p == "/api/images/upload":
                    png = images.upload_label(base64.b64decode(b.get("data") or ""), b.get("mime", ""))
                    kind = b.get("kind") if b.get("kind") in ("photo", "diagram", "quote", "data") else "photo"
                    title, cite = (b.get("title") or "Site photo")[:120], (b.get("source") or "Provided by requester")[:300]
                    iid = store.save_image(r["id"], eff["id"], "upload", title, cite, png)
                elif p == "/api/images/delete":
                    r["exhibits"] = [e for e in r.get("exhibits") or [] if e.get("image_id") != b.get("image_id")]
                    store.delete_image(b.get("image_id") or "")
                    _relabel(r)
                    return self._json(self._save(r, eff, pv, "image.delete"))
                else:
                    return self._json({"error": "unknown endpoint"}, 404)
            except images.ImageError as e:
                return self._json({"error": str(e)}, 400)
            r.setdefault("exhibits", []).append({"label": "", "title": title, "kind": kind, "image_id": iid,
                                                 "citation": cite, "date": b.get("date", ""), "section": section})
            _relabel(r)
            return self._json(self._save(r, eff, pv, "image." + kind))

        if p == "/api/handoff":
            r = self._load(b.get("id"), eff, pv)
            if not r:
                return
            th = int(config.load().get("min_score_to_advance", 75))
            q = r.get("qualification") or {}
            ok = (r.get("score") or 0) >= th and not q.get("blocked")
            if ok and auth.can_write(eff, r):
                r["status"] = "handoff"
                self._save(r, eff, pv, "request.handoff")
            return self._json({"ok": ok, "threshold": th, "score": r.get("score"),
                               "gates": [g for g in q.get("gates") or [] if not g["passed"]],
                               "payload": exporter.levelpath_payload(r)})
        return self._json({"error": "unknown endpoint"}, 404)

    def do_DELETE(self):
        ctx = self._ctx()
        if not ctx:
            return
        _, eff, pv = ctx
        p = urlparse(self.path).path
        if p.startswith("/api/requests/"):
            r = self._load(p.rsplit("/", 1)[-1], eff, pv, write=True)
            if r:
                store.delete(r["id"])
                store.audit(eff["id"], "request.delete", r["id"])
                self._json({"ok": True})
            return
        return self._json({"error": "unknown endpoint"}, 404)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default=os.environ.get("HOST", "0.0.0.0"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8765")))
    a = ap.parse_args()
    store.init()
    boot = auth.bootstrap()
    cfg = config.load()
    print("\n  Groundwork — five-point capital request intake")
    print(f"  http://{a.host}:{a.port}   edition={auth.EDITION}")
    print(f"  provider={cfg['provider']} model={cfg['model']} api_key={'set' if cfg['api_key'] else 'NOT SET (rules mode)'}"
          f"  images={'on' if images.image_ready(cfg) else 'off'}")
    if boot:
        print(f"  created first admin account: {boot} (password from GW_ADMIN_PASSWORD, else GW_APP_PASSWORD)")
    print()
    ThreadingHTTPServer((a.host, a.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
