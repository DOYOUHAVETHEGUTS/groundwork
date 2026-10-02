"""Finance escalation after two guided rounds without a passing score.

Delivery options (Settings → Finance escalation, or GW_* env vars):
  log      Record the alert on the request and offer a one-click email link (default).
  webhook  HTTPS POST of a JSON payload — Power Automate "When an HTTP request is
           received" (→ Outlook email / Teams post), Teams workflow, Slack, etc.
           Works on Render's free tier, which blocks outbound SMTP ports.
  smtp     Direct email (needs a paid Render instance or local run).

Demo requests never send anything — the alert is built and shown, marked simulated.
"""
import json
import os
import smtplib
import time
import urllib.request
from email.message import EmailMessage
from urllib.parse import quote

import config


def _app_url(req):
    base = os.environ.get("RENDER_EXTERNAL_URL") or os.environ.get("GW_PUBLIC_URL") or ""
    return base.rstrip("/") + "/" if base else ""


def _contact(req, cfg):
    return (req.get("finance_contact_name") or cfg.get("finance_contact_name") or "Finance partner",
            req.get("finance_contact_email") or cfg.get("finance_contact_email") or "")


def build_message(req):
    cfg = config.load()
    co = req.get("coaching") or {}
    rounds = co.get("rounds") or []
    q = req.get("qualification") or {}
    threshold = int(cfg.get("min_score_to_advance", 75))
    history = " → ".join([str(co.get("original_score", rounds[0]["score_before"] if rounds else "?"))]
                         + [str(r.get("score_after", "?")) for r in rounds if r.get("answered")])
    subject = (f"[Groundwork] {req.get('id')} needs Finance support — "
               f"{req.get('title') or 'capital request'} at {req.get('score')}/{threshold}")

    unanswered = []
    for r in rounds:
        bad = set(r.get("not_usable", [])) | set(r.get("skipped", []))
        unanswered += [x["question"] for x in r.get("questions", []) if x["id"] in bad]

    lines = [
        f"{req.get('requester') or 'A requester'} ({req.get('department') or 'department not set'}) "
        f"has worked through two rounds of guided questions on this capital request and it is still "
        f"below the {threshold}-point bar for Levelpath.",
        "",
        f"Request:  {req.get('id')} — {req.get('title')}",
        f"Type:     {req.get('request_type') or '—'}",
        f"Score:    {history}  (needs {threshold})",
        "",
        "Category scores:",
        *[f"  • {c['name']}: {c.get('earned', c['score'])}/{c['weight']}" for c in q.get("categories", [])],
        "",
        *(["Required items not met:"] + [f"  • {g['label']}: {g['message']}" for g in q.get("gates", []) if not g.get("passed")] + [""]
          if any(not g.get("passed") for g in q.get("gates", [])) else []),
        "Largest open findings:",
        *[f"  • {m}" for m in (q.get("missing") or ["—"])],
    ]
    if unanswered:
        lines += ["", "Questions the requester could not answer (likely where Finance can help):",
                  *[f"  • {u}" for u in unanswered[:10]]]
    url = _app_url(req)
    lines += ["", f"Open Groundwork: {url}" if url else "Open Groundwork and search for the request ID above.",
              "", "— Sent automatically by Groundwork capital intake"]
    return subject, "\n".join(lines)


def _mailto(email, subject, body):
    return f"mailto:{quote(email or '')}?subject={quote(subject)}&body={quote(body[:1800])}"


def _send_webhook(url, payload, timeout=15):
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"),
                                 headers={"content-type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return f"HTTP {r.status}"


def _send_smtp(cfg, to, subject, body, timeout=15):
    msg = EmailMessage()
    msg["From"] = cfg.get("smtp_from") or cfg.get("smtp_user")
    msg["To"], msg["Subject"] = to, subject
    msg.set_content(body)
    port = int(cfg.get("smtp_port") or 587)
    cls = smtplib.SMTP_SSL if port == 465 else smtplib.SMTP
    with cls(cfg["smtp_host"], port, timeout=timeout) as s:
        if port != 465:
            s.starttls()
        if cfg.get("smtp_user"):
            s.login(cfg["smtp_user"], cfg.get("smtp_password") or "")
        s.send_message(msg)
    return f"sent via {cfg['smtp_host']}:{port}"


def deliver(method, cfg, to_email, subject, body, payload):
    """Returns (delivered: bool, detail: str)."""
    try:
        if method == "webhook":
            if not cfg.get("alert_webhook_url"):
                return False, "Webhook selected but no webhook URL is configured."
            return True, _send_webhook(cfg["alert_webhook_url"], payload)
        if method == "smtp":
            if not (cfg.get("smtp_host") and to_email):
                return False, "SMTP selected but host or finance email is missing."
            return True, _send_smtp(cfg, to_email, subject, body)
        return False, "Logged in Groundwork — no delivery channel configured. Use the email link."
    except Exception as e:  # network / auth failures must never break the workflow
        hint = " (Render free instances block SMTP — use the webhook option.)" if method == "smtp" else ""
        return False, f"Delivery failed: {str(e)[:240]}{hint}"


def escalate(req: dict) -> dict:
    cfg = config.load()
    name, email = _contact(req, cfg)
    subject, body = build_message(req)
    payload = {
        "text": f"{subject}\n\n{body}", "subject": subject, "body": body,
        "to_name": name, "to_email": email,
        "request_id": req.get("id"), "title": req.get("title"),
        "requester": req.get("requester"), "department": req.get("department"),
        "score": req.get("score"), "threshold": int(cfg.get("min_score_to_advance", 75)),
        "app_url": _app_url(req),
    }
    record = {"created": time.time(), "to_name": name, "to_email": email,
              "subject": subject, "body": body, "mailto": _mailto(email, subject, body)}
    if req.get("demo"):
        record.update(method="demo", delivered=False, simulated=True,
                      detail="Nothing was sent. In production this alert is delivered "
                             "via the method set in Settings → Finance escalation.")
        return record
    method = (cfg.get("alert_method") or "log").lower()
    delivered, detail = deliver(method, cfg, email, subject, body, payload)
    record.update(method=method, delivered=delivered, simulated=False, detail=detail)
    return record


def send_test():
    cfg = config.load()
    name, email = _contact({}, cfg)
    subject = "[Groundwork] Test alert"
    body = f"This is a test of the Groundwork finance escalation channel for {name}."
    method = (cfg.get("alert_method") or "log").lower()
    ok, detail = deliver(method, cfg, email, subject, body,
                         {"text": f"{subject}\n\n{body}", "subject": subject, "body": body,
                          "to_email": email, "test": True})
    return {"ok": ok, "method": method, "detail": detail}
