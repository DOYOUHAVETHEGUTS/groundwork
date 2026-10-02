# Groundwork — pre-Levelpath capital request intake

Turns a plain-language request into a five-point justification, scores it against the
standard that gets requests approved, and exports a Word doc + JSON payload for Levelpath.

Python backend, one HTML file front end, **no third-party packages required** (python-docx optional).

## Run locally (trial)

```bash
cd groundwork
pip install -r requirements.txt    # only pulls python-docx (for real .docx export)
python app.py                      # serves on http://localhost:8765
```

`app.py` binds `0.0.0.0:8765` by default and reads `HOST`/`PORT` from the environment,
so the *same* command works locally and on Render. Override with flags or env if you like:

```bash
python app.py --port 9000          # or:  PORT=9000 python app.py
```

Without `python-docx`, `.docx` export falls back to Markdown. Everything else is standard
library — no framework, no build step.

## Deploy to Render

The repo ships a `render.yaml` blueprint, so you have two paths:

**Blueprint (recommended).** Push this folder to a Git repo, then in Render pick
**New → Blueprint** and point it at the repo. It creates a free web service with:

- Build command `pip install -r requirements.txt`
- Start command `python app.py` (binds `0.0.0.0:$PORT` automatically)
- Health check on `/api/schema`

**Manual.** **New → Web Service**, connect the repo, set runtime *Python 3*, the same
build/start commands above, then add the env vars below under **Advanced**.

### Set the API config as Render env vars — don't use the Settings UI on Render

Environment variables always win over `data/settings.json`, and Render's default disk is
**ephemeral** (wiped on every deploy and whenever a free service sleeps). So:

| Env var | Value |
| --- | --- |
| `GW_API_KEY` | your key — mark it **secret** (`sync:false` in the blueprint) |
| `GW_PROVIDER` | `openai` · `azure_openai` · `anthropic` · `custom` |
| `GW_MODEL` | e.g. `gpt-4o-mini` |
| `GW_BASE_URL` | e.g. `https://api.openai.com/v1` |

Setting these makes the whole API config env-locked: `/api/settings` still returns the key
**redacted**, and because `config.load()` re-applies env after the file, a visitor can't
overwrite the key or base URL through the Settings form. The header pill will read
`rules mode` until a key is present, then flip to model-backed.

### Login / access password

The app is gated by a password login when deployed. A visitor hits a sign-in page and,
on the correct password, gets a signed session cookie (`HttpOnly`, `SameSite=Lax`, and
`Secure` on Render); API calls return `401` and the UI bounces to `/login` when the session
is missing or expired. A **Log out** link appears in the header.

| Env var | Effect |
| --- | --- |
| `GW_APP_PASSWORD` | The access password. **Defaults to the built-in value if unset.** Set to an empty value to disable the login (e.g. local dev). |
| `GW_SECRET_KEY` | Session-signing secret. The blueprint auto-generates one; otherwise it's derived from the password so cookies survive restarts. |

```bash
GW_APP_PASSWORD= python app.py     # local: no login
python app.py                      # login on (built-in default password)
```

The health check lives at `/healthz`, which stays open so Render's probe isn't blocked by
the gate.

### Switching LLM providers

Open **Settings** and pick a **Provider** (OpenAI-compatible · Azure OpenAI · Anthropic ·
Custom gateway). Selecting a provider auto-fills its **Base URL** and offers a **Model**
list for that provider — the model box is a free-text field with suggestions, so any current
model works, including ones newer than the presets. Add your key, **Test connection**, then
**Save**. On Render, prefer the `GW_*` env vars over this form (see above), since env config
can't be overwritten through the UI and survives the ephemeral disk.

### Two caveats to plan around

1. **Persistence.** Saved requests live in `data/groundwork.db`. On the free plan they
   vanish on redeploy/sleep. Fine for a trial. To keep them, attach a **persistent disk**
   at `data/` (uncomment the `disk:` block in `render.yaml`) — it's a paid feature and
   disables zero-downtime deploys.
2. **Public exposure.** A Render web service is a public URL with **no auth**. Anyone who
   finds it can submit requests and spend your API budget. For a trial, keep it cheap and
   bounded: small model, low `GW_*` limits, and a hard spend cap set on the provider side.
   Leave **Offline mode** on until you're actively demoing, or ask for a basic-auth shim
   if you need to lock it down.

## Configure the API

Click **Settings** in the header, or set environment variables (env always wins over the file):

| Setting | Env var | Example |
| --- | --- | --- |
| Provider | `GW_PROVIDER` | `openai` · `azure_openai` · `anthropic` · `custom` |
| Base URL | `GW_BASE_URL` | `https://myresource.openai.azure.com` |
| API key | `GW_API_KEY` | `sk-…` |
| Model | `GW_MODEL` | `gpt-4o-mini` |
| API version | `GW_API_VERSION` | `2024-10-21` (Azure) |
| Deployment | `GW_DEPLOYMENT` | `gpt-4o-mini-prod` (Azure) |

The key is stored server-side in `data/settings.json` (chmod 600) and is **never sent to the
browser** — `/api/settings` returns it redacted. Use **Test connection** before saving.

**No key? The app still works.** Every agent falls back to the deterministic rules in
`rubric.py`, and the header pill shows `rules mode`. Tick **Offline mode** to force that
permanently — useful before the API is approved.

## Guided improvement — score → 10 questions → re-score → Finance

After a request is scored, Groundwork coaches it to a deliverable instead of just grading it:

1. **Round 1 — 10 guided questions.** Chosen by *points at stake* (category weight × how far
   below 100 it sits), so the weakest, heaviest sections get the most questions. Every question
   asks for something a business unit actually knows — counts, dates, quotes, hours, headcount,
   options considered. Skipping is fine.
2. **Merge + re-score.** Answers are written into the right sections of the draft (model prose
   when configured; labeled rules merge otherwise). Vague answers ("not sure", "hard to say")
   are recognized and don't count.
3. **Groundwork does the finance math.** Business units rarely know their MIRR, so questions
   ask for the *inputs* — one-time cost, annual benefit, recurring cost, useful life — and
   Groundwork derives **NPV @ 20%, MIRR and payback**, tagged `Groundwork calc — confirm with Finance`.
4. **Results screen** shows before → after, category movement, and the **largest edits ranked by
   points gained**. Points are attributed by re-scoring with each field reverted, so the ranking
   reflects what actually moved the score — not what was longest.
5. **Second chance.** Below the bar after round 1 → 10 *different* questions (no repeats by ID or
   wording) aimed at what's still missing.
6. **Escalation.** Still below the bar after round 2 → the requester's finance partner is alerted
   with the score path, open items, and the specific questions the requester couldn't answer.

Threshold is **Settings → Ready threshold** (default 75). Two rounds maximum per request.

### Finance escalation delivery

| Method | Works on Render free? | Setup |
| --- | --- | --- |
| `log` (default) | ✅ | Nothing. Alert is stored on the request with a one-click email link. |
| `webhook` | ✅ | `GW_ALERT_WEBHOOK_URL` — Groundwork POSTs JSON (`subject`, `body`, `to_email`, `score`, …). A Power Automate *When an HTTP request is received* flow can send it as an Outlook email or Teams post; Slack incoming webhooks read the `text` field. |
| `smtp` | ❌ free tier blocks ports 25/465/587 | `GW_SMTP_HOST/PORT/USER/PASSWORD/FROM`. Use on a paid instance or locally. |

The finance partner is taken from the request's own field (asked on the New Request screen),
falling back to `GW_FINANCE_NAME` / `GW_FINANCE_EMAIL`. **Settings → Send a test alert** verifies the channel.

### Built-in demo

Home → **Watch a guided demo** loads a weak real request (the BOS van, scores 40) and lets you
pick an ending:

| Scenario | Path |
| --- | --- |
| Passes after round 1 | 40 → 91 |
| Needs the second chance | 40 → 72 → 95 |
| Escalates to Finance | 40 → 51 → 59 → finance alerted |

On each question screen, **Autofill demo answers** plays the requester. Demos run on the free
rules engine by default (tick *Use the live AI model* to see tailored questions), are labeled
DEMO, and **never send a real alert** — the escalation is built and shown, marked simulated.
`python selftest.py` asserts all three paths.

## How it scores

Weights calibrated against the two attached examples — the approved Parcel Product 5-Point
and the BOS van request:

| Category | Weight |
| --- | --- |
| Current Situation | 15 |
| Proposal | 20 |
| **Financial Case** | **25** |
| Justification | 15 |
| Risks | 15 |
| Alternatives | 10 |

Ready threshold defaults to 75 (configurable). Smoke test results:
BOS van text → **40 / need**; Parcel product text → **97 / ready**. The gap is almost entirely
the financial case, operational impact, and mitigated risks.

```bash
python selftest.py     # re-runs both examples
```

## Files

```
app.py         stdlib HTTP server + routes
agents.py      structuring & qualification agents (model, with rules fallback)
coach.py       guided rounds: question bank + selection, merge, finance calc, edit ranking, demo
notify.py      finance escalation: webhook / SMTP / log
rubric.py      five-point schema, weights, deterministic scorer
llm.py         provider-agnostic chat client (urllib only)
config.py      settings load/save, env overrides, secret redaction
store.py       SQLite persistence
exporter.py    Word / Markdown / Levelpath JSON
templates/     index.html (single file UI)
data/          settings.json + groundwork.db (gitignored)
```

## API

| Method | Route | Purpose |
| --- | --- | --- |
| GET | `/api/schema` | field + rubric definitions, `llm_ready` flag |
| GET/POST | `/api/settings` | read (redacted) / write config |
| POST | `/api/settings/test` | probe the configured endpoint |
| POST | `/api/structure` | free text → five-point draft |
| POST | `/api/qualify` | draft → score, gaps, rewrite suggestions |
| GET/POST | `/api/requests` | list / upsert |
| GET | `/api/requests/{id}` | fetch one |
| POST | `/api/handoff` | gate on threshold, return Levelpath payload |
| GET | `/api/export?id=&format=docx\|json\|md` | download |
| POST | `/api/coach/questions` | open the next round (10 questions) |
| POST | `/api/coach/answers` | `{id, answers:{qid:text}}` → merge, re-score, edits; escalates after round 2 |
| POST | `/api/coach/escalate` | resend the finance alert |
| POST | `/api/demo/start` | `{scenario: pass\|second\|escalate, use_model}` |
| POST | `/api/demo/answers` | scripted answers for the open round |
| POST | `/api/settings/test-alert` | test the escalation channel |

`levelpath_payload()` in `exporter.py` is the single integration point — when the Levelpath
API is available, POST that dict instead of downloading it.

## Guardrails

- The structuring prompt forbids inventing costs, dates, vendors or volumes; anything absent
  is returned in `missing_information` rather than filled in.
- Model scores are ignored if the model skips categories — the rules score stands.
- The UI always labels which engine produced a draft or a score.
