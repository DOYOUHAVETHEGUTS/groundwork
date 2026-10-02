"""Tiny provider-agnostic chat client. stdlib only (urllib)."""
import json
import urllib.request
import urllib.error

import config


class LLMError(Exception):
    pass


def _post(url, payload, headers, timeout):
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:600]
        raise LLMError(f"{e.code} {e.reason} — {body}")
    except Exception as e:
        raise LLMError(str(e))


def _looks_like_deprecated_sampling_param(err: "LLMError") -> bool:
    """True for a 400 that's specifically about temperature/top_p/top_k being
    unsupported on a given model — newer Claude and OpenAI reasoning models reject
    a non-default value outright rather than just ignoring it. Anything else
    (auth, rate limit, timeout, real validation errors) is left alone."""
    msg = str(err).lower()
    if "400" not in msg:
        return False
    mentions_param = any(p in msg for p in ("temperature", "top_p", "top_k"))
    mentions_reason = any(w in msg for w in ("deprecated", "not support", "unsupported"))
    return mentions_param and mentions_reason


def available():
    cfg = config.load()
    return bool(cfg.get("api_key")) and not cfg.get("offline_mode")


def chat(system: str, user: str, json_mode: bool = True) -> str:
    cfg = config.load()
    if cfg.get("offline_mode"):
        raise LLMError("Offline mode is on — using built-in rules instead.")
    key = cfg.get("api_key")
    if not key:
        raise LLMError("No API key configured. Open Settings to add one.")

    provider = (cfg.get("provider") or "openai").lower()
    base = (cfg.get("base_url") or "").rstrip("/")
    model = cfg.get("model")
    timeout = int(cfg.get("timeout_seconds", 60))

    if provider == "anthropic":
        url = (base or "https://api.anthropic.com") + "/v1/messages"
        headers = {
            "content-type": "application/json",
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
        }
        payload = {
            "model": model,
            "max_tokens": int(cfg.get("max_tokens", 2000)),
            "temperature": float(cfg.get("temperature", 0.2)),
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }
        try:
            data = _post(url, payload, headers, timeout)
        except LLMError as e:
            # Models newer than Claude Opus 4.6 reject any temperature other than
            # 1.0 with a 400. Retry once with it omitted (API defaults to 1.0)
            # instead of forcing the caller to know which generation they're on.
            if not _looks_like_deprecated_sampling_param(e):
                raise
            payload.pop("temperature", None)
            data = _post(url, payload, headers, timeout)
        return "".join(b.get("text", "") for b in data.get("content", []))

    # OpenAI-compatible (openai, azure_openai, custom gateways)
    payload = {
        "model": model,
        "temperature": float(cfg.get("temperature", 0.2)),
        "max_tokens": int(cfg.get("max_tokens", 2000)),
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}

    if provider == "azure_openai":
        dep = cfg.get("deployment") or model
        url = f"{base}/openai/deployments/{dep}/chat/completions?api-version={cfg.get('api_version')}"
        headers = {"content-type": "application/json", "api-key": key}
        payload.pop("model", None)
    else:
        url = (base or "https://api.openai.com/v1") + "/chat/completions"
        headers = {"content-type": "application/json", "authorization": f"Bearer {key}"}

    try:
        data = _post(url, payload, headers, timeout)
    except LLMError as e:
        # Same deal on the OpenAI-compatible side — some reasoning models (o-series
        # and newer) reject a custom temperature the same way.
        if not _looks_like_deprecated_sampling_param(e):
            raise
        payload.pop("temperature", None)
        data = _post(url, payload, headers, timeout)
    try:
        return data["choices"][0]["message"]["content"]
    except Exception:
        raise LLMError(f"Unexpected response shape: {str(data)[:300]}")


def chat_json(system: str, user: str) -> dict:
    raw = chat(system, user, json_mode=True).strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        raw = raw[4:] if raw.lower().startswith("json") else raw
    s, e = raw.find("{"), raw.rfind("}")
    if s == -1 or e == -1:
        raise LLMError("Model did not return JSON.")
    return json.loads(raw[s:e + 1])


def test_connection():
    try:
        out = chat(
            "You are a connectivity probe. Reply with JSON only.",
            'Return exactly {"ok":true}.',
        )
        return {"ok": True, "detail": out.strip()[:120]}
    except LLMError as e:
        return {"ok": False, "detail": str(e)[:400]}
