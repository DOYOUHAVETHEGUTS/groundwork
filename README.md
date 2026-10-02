# Groundwork — five-point capital request intake

A front door for capital requests. A business-unit requester describes the need
(spoken or written, with guiding questions), Groundwork drafts the five-point
justification, scores it the way Finance reads it, coaches it with guided
questions, and blocks it until the non-negotiable items are met.

Standard library server + SQLite. `python-docx` (Word import/export) and `Pillow`
(diagrams, image labels) are the only packages.

## Run locally

```bash
pip install -r requirements.txt
python selftest.py                 # calibration, demo paths, gates, access rules
python app.py                      # http://localhost:8765
GW_EDITION=v2 python app.py        # named accounts + director / admin
```

V1 password defaults to the built-in value; set `GW_APP_PASSWORD` to change it,
or `GW_APP_PASSWORD=` (empty) to disable the login locally.

## The standard

Five literal sections, in Finance's order, plus an optional appendix:

| Section | Points | What earns them |
|---|---|---|
| Current Situation | 25 | Measured baseline, why the problem happens, real detail, photo + process flow |
| Proposal | 15 | One ask that matches the title, specific scope, timeline and constraints |
| Cost | 20 | Quantity × unit cost, itemized vendor quotes, contingency, totals that reconcile, funding source |
| Justification | 20 | Explicit recommendation, quantified benefit tied to the baseline, risks and assumptions |
| Alternatives | 20 | Real options (not consequences of inaction), three quotes, which vendor and why, one-basis comparison |

**Required items (override the score):** three vendor quotes — fewer is an
automatic fail; CRE status declared; CRE-driven requests need the CRE template.
A request can't pass a round or hand off while any is open.

Caps: bundled asks / title mismatch → Proposal ≤ 4; lump-sum cost → Cost ≤ 6;
no recommendation → Justification ≤ 12; missing appendix references −2.
NPV, IRR and payback are **not** scored — Finance prepares them with the division template.

Every lost point is a finding with *what went wrong, why Finance cares, evidence
from the draft, and the fix* — the dropdown under each section score.

## The workflow

1. **Describe** — speak or write, guided by nine questions.
2. **Review** — five sections, itemized quote editor, cost build-up, CRE declaration,
   process-flow builder, photos/exhibits. Or **import an existing Word draft**.
3. **Score** — required items, CRE callout, five section cards with findings,
   suggested wording at the bottom.
4. **Improve** — 10 guided questions aimed at the points at stake (always including
   "which vendor and why" once three quotes exist). Re-score, show the largest edits.
   A second round of 10 new questions if needed; after two rounds below the bar the
   finance partner is alerted.
5. **Handoff** — Word / JSON / Markdown. Exports are marked DRAFT until the request passes.

## Editions

| | V1 | V2 | V3 |
|---|---|---|---|
| Sign-in | Shared password | Named accounts (PBKDF2) | Microsoft Entra ID SSO (OIDC) + one break-glass admin |
| Roles | Everyone sees everything | Analyst, Director, Admin | + Division leader, Finance admin |
| Who sees what | — | Analysts: own work. Directors: their group, read-only + notes | Leaders: their division. Finance admin: all |
| Admin | Settings | Users, settings, API keys, audit log, usage | Roles come from Entra app roles via `GW_ROLE_MAP` |

Set `GW_EDITION`. The **V2 / V3 header tabs preview** those editions on any deploy
with seeded personas and sample data (admins only from V2 on); preview data never
mixes with real requests.

V3 setup: register the app in Entra ID, create app roles (e.g. `Groundwork.Analyst`,
`Groundwork.Director`, `Groundwork.FinanceAdmin`, `Groundwork.Admin`), then set
`GW_OIDC_ISSUER`, `GW_OIDC_CLIENT_ID`, `GW_OIDC_CLIENT_SECRET`, `GW_BREAKGLASS_EMAIL`
and `GW_ROLE_MAP`. ID tokens are verified in-process (RS256, issuer, audience,
expiry, nonce). Unmapped users are refused; roles are re-read at every sign-in.

## Images

* **Process flow** — drawn from the requester's steps, bottleneck in red. No API.
* **Example images** — via a separate image API (`GW_IMAGE_PROVIDER`; Claude doesn't
  generate images). Stamped "EXAMPLE" in the pixels and cited. Budgeted per request
  and per month.
* **Reference photos** — freely-licensed Wikimedia Commons images, labeled and cited
  with author and license; original pixels untouched.

## Deploy to Render

`render.yaml` is a blueprint. Health check is `/healthz`. The free tier wipes the
disk on redeploy/sleep and blocks SMTP — fine for a V1 pilot with sample data, not
for V2 accounts or real requests (use a persistent disk or company hosting).

## Files

```
app.py        routes, editions, access checks        rubric.py    five-point scorer, gates, findings
agents.py     drafting + model grader (rules fallback) coach.py     guided rounds, demo scripts
auth.py       V1/V2/V3 sign-in, roles, Entra OIDC      polish.py    duplicate + AI-phrasing cleanup
store.py      requests, users, org, audit, images      images.py    flow diagrams, example/reference images
docimport.py  Word draft import                        exporter.py  Word / Markdown / JSON
notify.py     finance escalation                       preview.py   V2/V3 preview personas
training/     the three anonymized training five-points
```
