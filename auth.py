"""Authentication and access control for the three editions.

GW_EDITION=v1  One shared password. Everyone sees everything. (Pilot / demo.)
GW_EDITION=v2  Named local accounts. Roles: analyst (own requests only), director
               (everything in their group, read-only on others' work), admin
               (users, settings, API keys, audit log).
GW_EDITION=v3  Microsoft Entra ID single sign-on (OpenID Connect). Roles and
               division/group come from Entra app roles or security groups via
               GW_ROLE_MAP, so access is managed where the company already manages it.
               Adds division leader and finance admin roles. Local login is limited to
               one break-glass admin.

ID tokens are verified in-process (RS256 against the tenant's published keys;
issuer, audience, expiry and nonce checked) with no third-party packages.
"""
import base64
import hashlib
import hmac
import json
import os
import secrets
import time
import urllib.parse
import urllib.request

import store

EDITION = (os.environ.get("GW_EDITION") or "v1").lower()
APP_PASSWORD = os.environ.get("GW_APP_PASSWORD", "Foundationpythonkeyboardinput9798")
SECRET = (os.environ.get("GW_SECRET_KEY") or hashlib.sha256(("groundwork::" + APP_PASSWORD).encode()).hexdigest()).encode()
SECURE = bool(os.environ.get("RENDER"))
TTL = 7 * 86400 if EDITION == "v1" else 10 * 3600

ROLE_RANK = {"analyst": 1, "director": 2, "leader": 3, "finance_admin": 4, "admin": 5}
ROLE_LABEL = {"analyst": "Analyst", "director": "Director", "leader": "Division leader",
              "finance_admin": "Finance admin", "admin": "Platform admin"}
EDITION_ROLES = {"v1": ["admin"], "v2": ["analyst", "director", "admin"],
                 "v3": ["analyst", "director", "leader", "finance_admin", "admin"]}
SHARED = {"id": "shared", "email": "", "name": "Shared login", "role": "admin", "group_id": "",
          "division_id": "", "source": "v1", "active": 1}


# --------------------------------------------------------------------------- passwords & tokens
def hash_pw(pw):
    salt = secrets.token_bytes(16)
    h = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, 200_000)
    return f"pbkdf2$200000${salt.hex()}${h.hex()}"


def check_pw(pw, stored):
    try:
        _, n, salt, h = stored.split("$")
        got = hashlib.pbkdf2_hmac("sha256", (pw or "").encode(), bytes.fromhex(salt), int(n))
        return hmac.compare_digest(got.hex(), h)
    except Exception:
        return False


def _sig(v):
    return hmac.new(SECRET, v.encode(), hashlib.sha256).hexdigest()


def make_token(value, ttl=TTL):
    body = f"{value}.{int(time.time()) + ttl}"
    return f"{body}.{_sig(body)}"


def read_token(tok):
    try:
        body, sig = tok.rsplit(".", 1)
        value, exp = body.rsplit(".", 1)
        if hmac.compare_digest(_sig(body), sig) and int(exp) > time.time():
            return value
    except Exception:
        pass
    return None


def cookie(name, value, max_age):
    parts = [f"{name}={value}", "Path=/", "HttpOnly", "SameSite=Lax", f"Max-Age={max_age}"]
    return "; ".join(parts + (["Secure"] if SECURE else []))


# --------------------------------------------------------------------------- session resolution
def user_from_cookies(ck):
    if EDITION == "v1":
        if not APP_PASSWORD:
            return SHARED
        return SHARED if read_token(ck.get("gw_session", "")) == "shared" else None
    uid = read_token(ck.get("gw_session", ""))
    u = store.get_user(uid) if uid else None
    return u if u and u.get("active") and u.get("source") != "demo" else None


def can_preview(u):
    return bool(u) and (EDITION == "v1" or u.get("role") == "admin")


def effective(u, ck):
    """(acting_user, preview?) — admins (or anyone on v1) may act as a seeded demo persona."""
    pid = read_token(ck.get("gw_preview", "")) if can_preview(u) else None
    if pid:
        p = store.get_user(pid)
        if p and p.get("source") == "demo":
            return p, True
    return u, False


# --------------------------------------------------------------------------- access rules
def scope(u):
    r = u.get("role")
    if u.get("id") == "shared" or r in ("admin", "finance_admin"):
        return {}
    if r == "leader":
        return {"division_ids": [u.get("division_id")], "or_owner": u["id"]}
    if r == "director":
        return {"group_ids": [u.get("group_id")], "or_owner": u["id"]}
    return {"owner": u["id"]}


def can_read(u, req):
    if not req:
        return False
    r = u.get("role")
    if u.get("id") == "shared" or r in ("admin", "finance_admin") or req.get("owner") == u.get("id"):
        return True
    if r == "leader":
        return req.get("division_id") == u.get("division_id")
    if r == "director":
        return req.get("group_id") == u.get("group_id")
    return False


def can_write(u, req):
    return bool(req) and (u.get("id") == "shared" or u.get("role") == "admin" or req.get("owner") == u.get("id")
                          or not req.get("owner"))


def is_admin(u):
    return u.get("id") == "shared" or u.get("role") == "admin"


def can_dashboard(u):
    return u.get("id") == "shared" or ROLE_RANK.get(u.get("role"), 0) >= 2


def public(u):
    return {k: u.get(k) for k in ("id", "email", "name", "role", "group_id", "division_id", "source")} | {
        "role_label": ROLE_LABEL.get(u.get("role"), u.get("role"))}


# --------------------------------------------------------------------------- local login (v2, v3 break-glass)
def bootstrap():
    """v2/v3: make sure an admin exists so the deploy is never locked out."""
    if EDITION == "v1" or store.count_users() > 0:
        return None
    email = (os.environ.get("GW_BREAKGLASS_EMAIL") if EDITION == "v3" else "") or os.environ.get("GW_ADMIN_EMAIL") \
        or "admin@groundwork.local"
    email = email.lower()
    pw = os.environ.get("GW_ADMIN_PASSWORD") or APP_PASSWORD
    did = store.ensure_division(os.environ.get("GW_DEFAULT_DIVISION") or "Finance")
    gid = store.ensure_group(os.environ.get("GW_DEFAULT_GROUP") or "Group 1", did)
    store.create_user(email, "Administrator", "admin", gid, did, hash_pw(pw))
    return email


def local_login(email, pw):
    if EDITION == "v1":
        return SHARED if (not APP_PASSWORD or hmac.compare_digest(pw or "", APP_PASSWORD)) else None
    u = store.get_user_by_email(email, with_hash=True)
    if not u or not u.get("active") or u.get("source") not in ("local",):
        return None
    if EDITION == "v3":
        breakglass = (os.environ.get("GW_BREAKGLASS_EMAIL") or "").lower()
        if not breakglass or u["email"] != breakglass:
            return None
    if not check_pw(pw, u.get("pw_hash") or ""):
        return None
    store.update_user(u["id"], last_login=time.time())
    return store.get_user(u["id"])


# --------------------------------------------------------------------------- OIDC (v3)
OIDC = {"issuer": os.environ.get("GW_OIDC_ISSUER", "").rstrip("/"),
        "client_id": os.environ.get("GW_OIDC_CLIENT_ID", ""),
        "client_secret": os.environ.get("GW_OIDC_CLIENT_SECRET", ""),
        "redirect_uri": os.environ.get("GW_OIDC_REDIRECT_URI") or
        ((os.environ.get("RENDER_EXTERNAL_URL") or "").rstrip("/") + "/auth/sso/callback")}
_CACHE = {}


def role_map():
    try:
        return json.loads(os.environ.get("GW_ROLE_MAP") or "{}")
    except ValueError:
        return {}


def sso_configured():
    return EDITION == "v3" and all(OIDC[k] for k in ("issuer", "client_id", "client_secret"))


def _get_json(url):
    if url not in _CACHE or _CACHE[url][0] < time.time():
        with urllib.request.urlopen(url, timeout=15) as r:
            _CACHE[url] = (time.time() + 3600, json.loads(r.read()))
    return _CACHE[url][1]


def discovery():
    return _get_json(OIDC["issuer"] + "/.well-known/openid-configuration")


def sso_start():
    state, nonce = secrets.token_urlsafe(16), secrets.token_urlsafe(16)
    q = {"client_id": OIDC["client_id"], "response_type": "code", "redirect_uri": OIDC["redirect_uri"],
         "response_mode": "query", "scope": "openid profile email", "state": state, "nonce": nonce}
    return discovery()["authorization_endpoint"] + "?" + urllib.parse.urlencode(q), make_token(f"{state}:{nonce}", 600)


def _b64(s):
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


SHA256_DI = bytes.fromhex("3031300d060960864801650304020105000420")


def rsa_verify(n, e, msg, sig):
    k = (n.bit_length() + 7) // 8
    if len(sig) != k:
        return False
    em = pow(int.from_bytes(sig, "big"), e, n).to_bytes(k, "big")
    t = SHA256_DI + hashlib.sha256(msg).digest()
    return hmac.compare_digest(em, b"\x00\x01" + b"\xff" * (k - len(t) - 3) + b"\x00" + t)


def verify_id_token(tok, nonce, jwks=None, issuer=None, audience=None, now=None):
    h64, p64, s64 = tok.split(".")
    head, claims = json.loads(_b64(h64)), json.loads(_b64(p64))
    if head.get("alg") != "RS256":
        raise PermissionError("Unsupported token algorithm.")
    keys = (jwks or _get_json(discovery()["jwks_uri"])).get("keys", [])
    key = next((k for k in keys if k.get("kid") == head.get("kid")), None)
    if not key:
        raise PermissionError("Signing key not found.")
    n, e = int.from_bytes(_b64(key["n"]), "big"), int.from_bytes(_b64(key["e"]), "big")
    if not rsa_verify(n, e, f"{h64}.{p64}".encode(), _b64(s64)):
        raise PermissionError("Token signature is invalid.")
    now = now or time.time()
    if claims.get("iss") != (issuer or discovery()["issuer"]):
        raise PermissionError("Token issuer mismatch.")
    aud = claims.get("aud")
    if (audience or OIDC["client_id"]) not in (aud if isinstance(aud, list) else [aud]):
        raise PermissionError("Token audience mismatch.")
    if claims.get("exp", 0) < now - 60 or claims.get("nbf", 0) > now + 60:
        raise PermissionError("Token expired.")
    if nonce is not None and claims.get("nonce") != nonce:
        raise PermissionError("Token nonce mismatch.")
    return claims


def map_claims(claims):
    """Highest role among the user's Entra app roles / groups that appear in GW_ROLE_MAP."""
    rm = role_map()
    hits = [rm[x] for x in (claims.get("roles") or []) + (claims.get("groups") or []) if x in rm]
    if not hits:
        raise PermissionError("Your account isn't assigned a Groundwork role. Ask your administrator.")
    return max(hits, key=lambda h: ROLE_RANK.get(h.get("role"), 0))


def provision(claims):
    m = map_claims(claims)
    email = (claims.get("email") or claims.get("preferred_username") or "").lower()
    if not email:
        raise PermissionError("The identity provider didn't return an email address.")
    did = store.ensure_division(m.get("division") or "Finance")
    gid = store.ensure_group(m.get("group") or "Unassigned", did)
    u = store.get_user_by_email(email)
    if u:
        u = store.update_user(u["id"], role=m["role"], division_id=did, group_id=gid,
                              name=claims.get("name") or u["name"], last_login=time.time())
    else:
        u = store.create_user(email, claims.get("name") or email, m["role"], gid, did, source="sso")
        store.update_user(u["id"], last_login=time.time())
    return u


def sso_callback(code, state, cookie_val):
    st = read_token(cookie_val or "")
    if not st or ":" not in st or st.split(":")[0] != state:
        raise PermissionError("Sign-in session expired. Try again.")
    nonce = st.split(":", 1)[1]
    body = urllib.parse.urlencode({"grant_type": "authorization_code", "code": code,
                                   "redirect_uri": OIDC["redirect_uri"], "client_id": OIDC["client_id"],
                                   "client_secret": OIDC["client_secret"], "scope": "openid profile email"}).encode()
    req = urllib.request.Request(discovery()["token_endpoint"], body,
                                 {"content-type": "application/x-www-form-urlencoded"}, method="POST")
    with urllib.request.urlopen(req, timeout=20) as r:
        tok = json.loads(r.read())
    return provision(verify_id_token(tok["id_token"], nonce))
