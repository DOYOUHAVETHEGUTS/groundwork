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
    "temperature": 0.2,
    "max_tokens": 2000,
    "timeout_seconds": 60,
    # --- behavior ---
    "offline_mode": False,         # True = deterministic rules only, never calls the API
    "org_name": "American Airlines Cargo",
    "default_department": "Cargo",
    "min_score_to_advance": 75,
}

ENV_MAP = {
    "provider": "GW_PROVIDER",
    "base_url": "GW_BASE_URL",
    "api_key": "GW_API_KEY",
    "model": "GW_MODEL",
    "api_version": "GW_API_VERSION",
    "deployment": "GW_DEPLOYMENT",
}

SECRET_KEYS = {"api_key"}


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
        elif k in ("max_tokens", "timeout_seconds", "min_score_to_advance"):
            v = int(v)
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
