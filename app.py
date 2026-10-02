"""Groundwork — capital request intake. Standard-library HTTP server.

Run:  python app.py            (http://127.0.0.1:8765)
      python app.py --port 9000 --host 0.0.0.0
"""
import argparse
import hashlib
import hmac
import json
import mimetypes
import os
import sys
from http.cookies import SimpleCookie
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# --------------------------------------------------------------------------- auth
# Password gate for public deployments. Set GW_APP_PASSWORD in the environment to
# override the built-in default. Set it to an empty string to disable the login
# entirely (handy for pure local dev):  GW_APP_PASSWORD= python app.py
APP_PASSWORD = os.environ.get("GW_APP_PASSWORD", "Foundationpythonkeyboardinput9798")
AUTH_ON = bool(APP_PASSWORD)
# Session secret: explicit GW_SECRET_KEY wins; otherwise derived from the password
# so the cookie stays valid across restarts without storing any session state.
_SECRET = (os.environ.get("GW_SECRET_KEY")
           or hashlib.sha256(("groundwork::" + APP_PASSWORD).encode()).hexdigest())
SESSION_TOKEN = hmac.new(_SECRET.encode(), b"authenticated", hashlib.sha256).hexdigest()
COOKIE_SECURE = bool(os.environ.get("RENDER"))  # Render terminates HTTPS at its edge


def _cookie(value, max_age):
    parts = [f"gw_session={value}", "Path=/", "HttpOnly", "SameSite=Lax",
             f"Max-Age={max_age}"]
    if COOKIE_SECURE:
        parts.append("Secure")
    return "; ".join(parts)


LOGIN_HTML = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Groundwork — sign in</title>
<style>
  :root{color-scheme:dark}
  *{box-sizing:border-box}
  body{margin:0;min-height:100vh;display:grid;place-items:center;
    font:15px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif;
    background:#0e1017;color:#e7e9ee}
  .card{width:min(92vw,380px);background:#171a23;border:1px solid #262b38;
    border-radius:16px;padding:28px 26px;box-shadow:0 20px 60px #0008}
  h1{margin:0 0 4px;font-size:19px}
  p.sub{margin:0 0 20px;color:#9aa1b2;font-size:13px}
  label{display:block;font-size:12px;color:#9aa1b2;margin:0 0 6px}
  input{width:100%;padding:11px 12px;border-radius:10px;border:1px solid #2c3242;
    background:#0e1017;color:#e7e9ee;font-size:15px}
  input:focus{outline:none;border-color:#5b8cff}
  button{width:100%;margin-top:16px;padding:11px;border:0;border-radius:10px;
    background:#5b8cff;color:#0b0d13;font-weight:600;font-size:15px;cursor:pointer}
  button:hover{background:#78a1ff}
  .err{margin-top:12px;min-height:18px;color:#ff8080;font-size:13px}
</style></head><body>
  <form class="card" onsubmit="return signin(event)">
    <h1>Groundwork</h1>
    <p class="sub">Enter the access password to continue.</p>
    <label for="pw">Password</label>
    <input id="pw" type="password" autocomplete="current-password" autofocus>
    <button type="submit">Sign in</button>
    <div class="err" id="err"></div>
  </form>
<script>
async function signin(e){e.preventDefault();
  const err=document.getElementById('err');err.textContent='';
  const r=await fetch('/login',{method:'POST',headers:{'content-type':'application/json'},
    body:JSON.stringify({password:document.getElementById('pw').value})});
  if(r.ok){location.href='/';}
  else{const d=await r.json().catch(()=>({}));err.textContent=d.error||'Incorrect password.';
    document.getElementById('pw').select();}
  return false;}
</script></body></html>"""

import agents
import coach
import config
import exporter
import llm
import notify
import rubric
import store

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATES = os.path.join(BASE_DIR, "templates")
STATIC = os.path.join(BASE_DIR, "static")


class Handler(BaseHTTPRequestHandler):
    server_version = "Groundwork/1.0"

    # ---------------------------------------------------------- helpers
    def _send(self, code, body=b"", ctype="application/json", extra=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj), "application/json")

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode("utf-8"))
        except Exception:
            return {}

    def _file(self, path, ctype=None):
        if not os.path.isfile(path):
            return self._send(404, b"Not found", "text/plain")
        ctype = ctype or (mimetypes.guess_type(path)[0] or "application/octet-stream")
        with open(path, "rb") as f:
            self._send(200, f.read(), ctype)

    def log_message(self, fmt, *args):
        sys.stderr.write("  %s\n" % (fmt % args))

    # ---------------------------------------------------------- auth
    def _authed(self):
        if not AUTH_ON:
            return True
        ck = SimpleCookie(self.headers.get("Cookie", ""))
        tok = ck["gw_session"].value if "gw_session" in ck else ""
        return hmac.compare_digest(tok, SESSION_TOKEN)

    def _require_auth(self):
        """True if the request may proceed; otherwise emit 401/redirect and return False."""
        if self._authed():
            return True
        if urlparse(self.path).path.startswith("/api/"):
            self._json({"error": "auth required"}, 401)
        else:
            self._send(302, b"", "text/plain", {"Location": "/login"})
        return False

    # ---------------------------------------------------------- routes
    def do_GET(self):
        u = urlparse(self.path)
        p, q = u.path, parse_qs(u.query)

        # open (unauthenticated) endpoints
        if p == "/login":
            return self._send(200, LOGIN_HTML, "text/html; charset=utf-8")
        if p == "/logout":
            return self._send(302, b"", "text/plain",
                              {"Set-Cookie": _cookie("", 0), "Location": "/login"})
        if p == "/healthz":
            return self._json({"ok": True})
        if not self._require_auth():
            return

        if p in ("/", "/index.html"):
            return self._file(os.path.join(TEMPLATES, "index.html"), "text/html; charset=utf-8")
        if p.startswith("/static/"):
            return self._file(os.path.join(STATIC, os.path.basename(p)))

        if p == "/api/settings":
            return self._json(config.redacted())
        if p == "/api/schema":
            return self._json({
                "sections": [{"key": k, "label": l} for k, l in rubric.SECTIONS],
                "extra": [{"key": k, "label": l} for k, l in rubric.EXTRA_FIELDS],
                "financial": [{"key": k, "label": l} for k, l in rubric.FINANCIAL_FIELDS],
                "categories": [{"name": n, "weight": w, "note": d} for n, w, d in rubric.CATEGORIES],
                "llm_ready": llm.available(),
                "auth_required": AUTH_ON,
                "threshold": int(config.load().get("min_score_to_advance", 75)),
                "max_rounds": coach.MAX_ROUNDS,
                "demo_scenarios": [{"key": k, **v} for k, v in coach.DEMO_SCENARIOS.items()],
            })
        if p == "/api/requests":
            return self._json({"items": store.list_all()})
        if p.startswith("/api/requests/"):
            rid = p.rsplit("/", 1)[-1]
            r = store.get(rid)
            return self._json(r) if r else self._json({"error": "not found"}, 404)
        if p == "/api/export":
            r = store.get((q.get("id") or [""])[0])
            if not r:
                return self._json({"error": "not found"}, 404)
            fmt = (q.get("format") or ["docx"])[0]
            if fmt == "json":
                data, name, ctype = exporter.to_json_bytes(r)
            elif fmt == "md":
                data, name, ctype = exporter.to_markdown(r).encode("utf-8"), f"{r['id']}.md", "text/markdown"
            else:
                data, name, ctype = exporter.to_docx_bytes(r)
            return self._send(200, data, ctype,
                              {"Content-Disposition": f'attachment; filename="{name}"'})
        return self._send(404, b"Not found", "text/plain")

    def do_POST(self):
        p = urlparse(self.path).path

        if p == "/login":
            pw = (self._body().get("password") or "")
            if not AUTH_ON:
                return self._send(200, json.dumps({"ok": True}), "application/json",
                                  {"Set-Cookie": _cookie(SESSION_TOKEN, 604800)})
            if hmac.compare_digest(pw, APP_PASSWORD):
                return self._send(200, json.dumps({"ok": True}), "application/json",
                                  {"Set-Cookie": _cookie(SESSION_TOKEN, 604800)})
            return self._json({"ok": False, "error": "Incorrect password."}, 401)

        if not self._require_auth():
            return
        b = self._body()

        if p == "/api/settings":
            return self._json(config.save(b))
        if p == "/api/settings/test":
            if b:
                config.save(b)
            return self._json(llm.test_connection())

        if p == "/api/structure":
            text = (b.get("text") or "").strip()
            if len(text) < 8:
                return self._json({"error": "Add a sentence or two so we can organize it."}, 400)
            cfg = config.load()
            meta = {
                "title": b.get("title") or "Untitled request",
                "request_type": b.get("request_type") or "",
                "department": b.get("department") or cfg.get("default_department"),
                "requester": b.get("requester") or "",
                "finance_contact_name": (b.get("finance_contact_name") or "").strip(),
                "finance_contact_email": (b.get("finance_contact_email") or "").strip(),
            }
            req = store.blank(meta)
            req["original_description"] = text
            req.update({k: v for k, v in agents.structure(text, meta).items()
                        if k in req or k in ("engine", "engine_error", "missing_information")})
            return self._json(store.save(req))

        if p == "/api/qualify":
            req = store.get(b.get("id")) if b.get("id") else None
            if req is None:
                req = store.blank()
            for k in rubric.BLANK_REQUEST:
                if k in b:
                    req[k] = b[k]
            for k in ("title", "request_type", "department", "requester"):
                if k in b:
                    req[k] = b[k]
            q = agents.qualify(req)
            req["qualification"] = q
            req["score"], req["status"] = q["overall"], q["status"]
            return self._json(store.save(req))

        if p == "/api/requests":
            req = store.get(b.get("id")) if b.get("id") else store.blank()
            req = req or store.blank()
            req.update({k: v for k, v in b.items() if k in req})
            return self._json(store.save(req))

        if p == "/api/settings/test-alert":
            return self._json(notify.send_test())

        # ---- guided improvement loop ----
        if p in ("/api/coach/questions", "/api/coach/answers", "/api/coach/escalate",
                 "/api/demo/answers"):
            r = store.get(b.get("id"))
            if not r:
                return self._json({"error": "not found"}, 404)
            try:
                if p == "/api/coach/questions":
                    r = coach.start_round(r)
                elif p == "/api/coach/answers":
                    r = coach.submit_answers(r, b.get("answers") or {}, notifier=notify.escalate)
                elif p == "/api/coach/escalate":
                    r.setdefault("coaching", {"rounds": []})["escalation"] = notify.escalate(r)
                else:
                    return self._json({"answers": coach.demo_answers(r)})
            except coach.CoachError as e:
                return self._json({"error": str(e)}, 400)
            return self._json(store.save(r))

        if p == "/api/demo/start":
            scenario = b.get("scenario") if b.get("scenario") in coach.DEMO_SCENARIOS else "pass"
            rules_only = not (b.get("use_model") and llm.available())
            meta = {"title": f"DEMO · BOS van replacement ({coach.DEMO_SCENARIOS[scenario]['label']})",
                    "request_type": "Vehicle / GSE", "department": "Cargo Facilities",
                    "requester": "Demo requester",
                    "finance_contact_name": "Demo Finance Partner",
                    "finance_contact_email": "finance.partner@example.com"}
            req = store.blank(meta)
            req.update({"demo": True, "demo_scenario": scenario, "demo_rules_only": rules_only,
                        "original_description": coach.DEMO_TEXT})
            d = agents.structure(coach.DEMO_TEXT, meta, force_rules=rules_only)
            req.update({k: v for k, v in d.items()
                        if k in req or k in ("engine", "engine_error", "missing_information")})
            q = agents.qualify(req)
            req["qualification"], req["score"], req["status"] = q, q["overall"], q["status"]
            return self._json(store.save(req))

        if p == "/api/handoff":
            r = store.get(b.get("id"))
            if not r:
                return self._json({"error": "not found"}, 404)
            threshold = int(config.load().get("min_score_to_advance", 75))
            ok = (r.get("score") or 0) >= threshold
            if ok:
                r["status"] = "handoff"
                store.save(r)
            return self._json({"ok": ok, "threshold": threshold,
                               "score": r.get("score"),
                               "payload": exporter.levelpath_payload(r)})
        return self._json({"error": "unknown endpoint"}, 404)

    def do_DELETE(self):
        if not self._require_auth():
            return
        p = urlparse(self.path).path
        if p.startswith("/api/requests/"):
            store.delete(p.rsplit("/", 1)[-1])
            return self._json({"ok": True})
        return self._json({"error": "unknown endpoint"}, 404)


def main():
    ap = argparse.ArgumentParser()
    # Env-driven defaults so the same command works locally and on Render.
    # Render injects PORT and requires binding to 0.0.0.0; CLI flags still win.
    ap.add_argument("--host", default=os.environ.get("HOST", "0.0.0.0"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8765")))
    a = ap.parse_args()
    store.init()
    cfg = config.load()
    print("\n  Groundwork — capital request intake")
    print(f"  http://{a.host}:{a.port}")
    print(f"  provider={cfg['provider']}  model={cfg['model']}  "
          f"api_key={'set' if cfg['api_key'] else 'NOT SET (rules mode)'}")
    print(f"  login={'ON — password required' if AUTH_ON else 'OFF (open)'}\n")
    ThreadingHTTPServer((a.host, a.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
