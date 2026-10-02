"""Settings: file-backed, env-overridable. No secrets in the browser."""
import json, os, threading

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
SETTINGS_PATH = os.path.join(DATA_DIR, "settings.json")
_lock = threading.Lock()

DEFAULTS = {
    # --- API / model ---
    "provider": "openai",          # openai | azure_openai | anthropic | custom
    "base_url": "https://api.openai.com/v1",
    "api_key": "",
    "model": "gpt-4o-mini",
    "api_version": "2024-10-21",   # azure_openai only
    "deployment": "",              # azure_openai only (defaults to model)
    "temperature": 0,              # grading runs deterministic; auto-retried without it if a model rejects it
    "max_tokens": 4000,
    "timeout_seconds": 60,
    # --- behavior ---
    "offline_mode": False,         # True = deterministic rules only, never calls the API
    "org_name": "American Airlines Cargo",
    "default_department": "Cargo",
    "min_score_to_advance": 75,
    # --- finance escalation (after two coaching rounds without a passing score) ---
    "finance_contact_name": "",
    "finance_contact_email": "",
    "alert_method": "log",         # log | webhook | smtp   (log = in-app record + email link)
    "alert_webhook_url": "",       # HTTPS POST — Power Automate, Teams workflow, Slack, etc.
    "smtp_host": "",
    "smtp_port": 587,
    "smtp_user": "",
    "smtp_password": "",
    "smtp_from": "",
    # --- example images (separate from the text model; Claude does not generate images) ---
    "image_provider": "none",      # none | openai | azure_openai
    "image_api_key": "",
    "image_model": "gpt-image-1",
    "image_base_url": "",
    "image_deployment": "",
    "image_api_version": "",
    "image_per_request": 4,        # budget: AI example images per request
    "image_monthly_cap": 40,       # budget: AI example images per calendar month, whole deploy
}

ENV_MAP = {
    "provider": "GW_PROVIDER",
    "base_url": "GW_BASE_URL",
    "api_key": "GW_API_KEY",
    "model": "GW_MODEL",
    "api_version": "GW_API_VERSION",
    "deployment": "GW_DEPLOYMENT",
    "finance_contact_name": "GW_FINANCE_NAME",
    "finance_contact_email": "GW_FINANCE_EMAIL",
    "alert_method": "GW_ALERT_METHOD",
    "alert_webhook_url": "GW_ALERT_WEBHOOK_URL",
    "smtp_host": "GW_SMTP_HOST",
    "smtp_port": "GW_SMTP_PORT",
    "smtp_user": "GW_SMTP_USER",
    "smtp_password": "GW_SMTP_PASSWORD",
    "smtp_from": "GW_SMTP_FROM",
    "image_provider": "GW_IMAGE_PROVIDER",
    "image_api_key": "GW_IMAGE_API_KEY",
    "image_model": "GW_IMAGE_MODEL",
    "image_base_url": "GW_IMAGE_BASE_URL",
    "image_deployment": "GW_IMAGE_DEPLOYMENT",
    "image_per_request": "GW_IMAGE_PER_REQUEST",
    "image_monthly_cap": "GW_IMAGE_MONTHLY_CAP",
}

SECRET_KEYS = {"api_key", "smtp_password", "alert_webhook_url", "image_api_key"}


def load():
    cfg = dict(DEFAULTS)
    if os.path.exists(SETTINGS_PATH):
        try:
            with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
                cfg.update(json.load(f))
        except Exception:
            pass
    for key, env in ENV_MAP.items():          # env always wins
        if os.environ.get(env):
            cfg[key] = os.environ[env]
    return cfg


def save(patch: dict):
    cfg = dict(DEFAULTS)
    if os.path.exists(SETTINGS_PATH):
        try:
            with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
                cfg.update(json.load(f))
        except Exception:
            pass
    for k, v in (patch or {}).items():
        if k not in DEFAULTS:
            continue
        if k in SECRET_KEYS and v == "":      # blank means "leave it alone"
            continue
        if k in ("temperature",):
            v = float(v)
        elif k in ("max_tokens", "timeout_seconds", "min_score_to_advance", "smtp_port",
                   "image_per_request", "image_monthly_cap"):
            v = int(v or 0)
        elif k == "offline_mode":
            v = bool(v)
        cfg[k] = v
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
    try:
        os.chmod(SETTINGS_PATH, 0o600)
    except Exception:
        pass
    return load()


def redacted():
    """Safe to send to the browser."""
    cfg = load()
    out = dict(cfg)
    for k in SECRET_KEYS:
        out[k + "_set"] = bool(cfg.get(k))
        out[k] = ""
    out["env_locked"] = [k for k, e in ENV_MAP.items() if os.environ.get(e)]
    return out
