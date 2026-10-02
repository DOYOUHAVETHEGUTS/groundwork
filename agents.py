"""Drafting and grading agents. Model when configured, deterministic rules otherwise.

Gates (three quotes, CRE template) are always decided by code, never by the model.
"""
import json
import re

import config
import llm
import polish
import rubric

STANDARD = """A five-point justification has exactly five sections, in this order:
Current Situation, Proposal, Cost, Justification, Alternatives — plus an optional
Appendix with supporting detail (vendor quote letters, photos, data, a process flow).
Finance requires: a measured baseline (counts, ages, utilization, failures) and the
reason the problem happens; one ask that matches the title; costs itemized as
quantity × unit cost with contingency and a funding source; three vendor quotes,
each itemized; which vendor was selected and why (best value overall, not just the
lowest price); real alternatives, not consequences of doing nothing; an explicit
recommendation with a quantified benefit tied to the baseline; risks and
assumptions. CRE-driven requests need the Corporate Real Estate template.
NPV and payback are prepared by Finance with the division template: never compute
or request them."""

STYLE = """Write like a finance analyst's memo to an approval committee:
- Short declarative sentences. Numbers before adjectives. State each fact once:
  never repeat a sentence, and never restate a point in a second section.
- Plain words. Never use: delve, robust, seamless, leverage, utilize, streamline,
  holistic, synergy, pivotal, cutting-edge, transformative, crucial, vital,
  "in today's", "it is important to note", furthermore, moreover, additionally,
  "in conclusion", "not only … but also".
- No marketing tone, rhetorical questions, exclamation marks or em-dash chains.
- Never invent facts, numbers, vendors, dates or savings. If something is missing,
  leave it out and list it in missing_information.
- Third person, US business English."""

STRUCTURE_SYSTEM = f"""You turn a requester's plain description into the first draft of a
five-point justification for Finance.

{STANDARD}

{STYLE}

Put each sentence in exactly one section. Keep the requester's facts and numbers.
Return JSON only:
{{"current_situation":"","proposal":"","cost":"","justification":"","alternatives":"",
  "cost_items":[{{"desc":"","qty":1,"unit":0}}],
  "quotes":[{{"vendor":"","total":"","items":[{{"desc":"","qty":1,"unit":0}}]}}],
  "cre_hint":"yes|no|unknown",
  "missing_information":["short, specific items the requester still needs to supply"]}}"""

GRADER_SYSTEM = f"""You are a capital-request reviewer for a large operations organization.
You grade five-point justifications the way a finance approval committee would:
skeptical, evidence-driven, concise. Judge only what is on the page. Use no outside
or company-specific knowledge.

{STANDARD}

Score each section out of its maximum:
1. Current Situation (25): quantified baseline 10 (counts, utilization, ages, rates —
   not "limited", "old", "growing"); why the problem happens 7 (process or workflow
   cause); real detail and data 4; visuals 4 (photo of the condition 2, process flow
   with the bottleneck 2).
2. Proposal (15): one decision that matches the title, nothing unrelated bundled 6;
   specific scope 5; timeline and constraints (sourcing, contract, install) 4.
3. Cost (20): itemized quantity × unit cost 7; each vendor quote itemized 4;
   contingency 3; totals reconcile 3; funding source 3.
4. Justification (20): explicit recommendation 6; benefit quantified in $, days,
   hours, risk or capacity 7; tied back to the baseline numbers 3; risks and key
   assumptions 4.
5. Alternatives (20): two or more real alternatives with cost and trade-off 6;
   three vendor quotes 6; which vendor was selected and why, all-in 5; options
   compared on one basis (cost, lead time, scope) 3.

Hard caps: 3+ unrelated asks or a title that doesn't match → Proposal ≤ 4.
Cost is one unsupported total → Cost ≤ 6. No explicit recommendation →
Justification ≤ 12. A referenced appendix that is missing → −2 in that section.
Do not score or request NPV, IRR or payback.

Calibration: ~25–40 headings holding claims without numbers, a lump-sum cost,
consequences listed as alternatives, bundled asks. ~65–80 focused and quantified
with an itemized cost and appendices, but missing a Justification or quote detail.
~90–100 all of that plus a quantified benefit tied to the baseline, an explicit
recommendation, three itemized quotes with a reasoned selection, visuals, and no
inconsistencies.

For every point lost, write one finding:
  title ≤ 10 words — plain statement of what went wrong
  why ≤ 30 words — why Finance cares
  evidence ≤ 12 words quoted from the draft, or "Not found in the draft."
  fix ≤ 25 words — one direct instruction
Tone: clear, direct and respectful. Address the requester as "you". No praise padding,
no hedging, no repeated points. {STYLE.splitlines()[0]}

Also give rewrites: for up to three sections, a stronger version that uses ONLY facts
already on the page (leave a section out rather than invent).

Return JSON only:
{{"sections":[{{"name":"Current Situation","score":0,"strengths":[""],
   "findings":[{{"title":"","why":"","evidence":"","fix":"","points_lost":0}}]}}],
  "rewrites":{{"current_situation":"","proposal":"","cost":"","justification":"","alternatives":""}},
  "reviewer_summary":"≤ 80 words"}}"""

DETERMINISTIC = ("dup_", "ref_", "co_reconcile", "al_quotes", "co_quotes_itemized", "pr_bundled", "pr_title")


def _threshold():
    return int(config.load().get("min_score_to_advance", 75))


def use_model(req: dict) -> bool:
    return llm.available() and not req.get("demo_rules_only")


# ------------------------------------------------------------------ structure
ROUTES = [
    ("cost", re.compile(r"\$|\bcost|\bprice|\bquote|\bbudget|\bpaid\b|\bspend|\bfund", re.I)),
    ("alternatives", re.compile(r"alternative|\boption|instead|\blease|\brent\b|\brepair|other vendor|considered|"
                                r"\bor a\b.*\b(ranger|model|brand)|ford|chevy", re.I)),
    ("proposal", re.compile(r"\breplace|\bpurchase|\bbuy\b|\brequest|\bpropos|\binstall|\bupgrade|we want|we need to get|"
                            r"\bacquire", re.I)),
    ("justification", re.compile(r"\bwill\b|\bwould\b|\bimprov|\breduc|\bsave|\bsafety|\bbenefit|\bbetter\b|"
                                 r"\bmaneuver|\bavoid|\bprevent", re.I)),
]


def _rules_structure(text):
    out = {k: [] for k, _ in rubric.SECTIONS}
    for s in rubric.sentences(text):
        dest = next((k for k, rx in ROUTES if rx.search(s)), "current_situation")
        out[dest].append(s.rstrip(".") + ".")
    d = {k: " ".join(v) for k, v in out.items()}
    d["missing_information"] = [f"{lbl}: nothing yet" for k, lbl in rubric.SECTIONS if not d[k]]
    d["engine"] = "rules"
    return d


def structure(text: str, meta: dict, force_rules: bool = False) -> dict:
    d = None
    if llm.available() and not force_rules:
        try:
            raw = llm.chat_json(STRUCTURE_SYSTEM, json.dumps({"title": meta.get("title"), "description": text}))
            d = {k: str(raw.get(k) or "").strip() for k, _ in rubric.SECTIONS}
            for k in ("cost_items", "quotes"):
                d[k] = raw.get(k) if isinstance(raw.get(k), list) else []
            d["quotes"] = [q for q in d["quotes"] if str(q.get("vendor") or "").strip()]
            d["missing_information"] = [str(x) for x in raw.get("missing_information") or []][:10]
            if str(raw.get("cre_hint", "")).lower() == "yes":
                d["cre_hint"] = "explicit"
            d["engine"] = "model:" + str(config.load().get("model"))
        except (llm.LLMError, ValueError, KeyError, TypeError, AttributeError) as e:
            d = _rules_structure(text)
            d["engine"], d["engine_error"] = "rules (model unavailable)", str(e)[:300]
    d = d or _rules_structure(text)
    return polish.polish_request(d)


# ------------------------------------------------------------------ qualify
def _draft_for_model(req):
    r = rubric.upgrade(dict(req))
    lines = [f"TITLE: {r.get('title')}"]
    for k, lbl in rubric.SECTIONS:
        lines += ["", f"## {lbl}", r.get(k) or "(empty)"]
    if r.get("cost_items"):
        lines += ["", "COST ITEMS:"] + [f"- {i.get('desc')}: {i.get('qty')} × ${rubric._num(i.get('unit')):,.2f}"
                                        for i in r["cost_items"]]
        if r.get("contingency_pct"):
            lines.append(f"- Contingency: {r['contingency_pct']}%")
    if r.get("funding_source"):
        lines.append(f"FUNDING SOURCE: {r['funding_source']}")
    vq = r.get("quotes") or []
    lines += ["", f"VENDOR QUOTES ({len(vq)}):"]
    for q in vq:
        items = rubric.quote_items(q)
        lines.append(f"- {q.get('vendor')}{' (SELECTED)' if q.get('selected') else ''}: "
                     f"${rubric.quote_total(q):,.0f}; {len(items)} line items; lead time {q.get('lead_time') or 'n/a'}")
    if r.get("selection_rationale"):
        lines.append(f"SELECTION RATIONALE: {r['selection_rationale']}")
    ex = r.get("exhibits") or []
    lines += ["", f"EXHIBITS ({len(ex)}): " + "; ".join(f"{e.get('label')}: {e.get('title')} [{e.get('kind')}]" for e in ex)]
    if r.get("flow_steps"):
        lines.append("PROCESS FLOW: " + " → ".join(
            (s.get("name") or "") + (" [BOTTLENECK]" if s.get("bottleneck") else "") for s in r["flow_steps"]))
    if r.get("appendix"):
        lines += ["", "## Appendix", r["appendix"]]
    return "\n".join(lines)


def qualify(req: dict) -> dict:
    th = _threshold()
    base = rubric.score_request(req, th)
    if not use_model(req):
        return base
    try:
        data = llm.chat_json(GRADER_SYSTEM, _draft_for_model(req))
        by_name = {str(s.get("name", "")).strip().lower(): s for s in data.get("sections") or []}
        cats = []
        for c in base["categories"]:
            m = by_name.get(c["name"].lower())
            if not m:
                raise ValueError(f"model omitted section {c['name']}")
            earned = max(0.0, min(float(c["weight"]), float(m.get("score", 0))))
            caps = {f["id"] for f in c["findings"]}
            if "pr_bundled" in caps or "pr_title" in caps:
                earned = min(earned, 4 if "pr_bundled" in caps else earned)
            if c["name"] == "Cost" and any(f["id"] == "co_itemized" and f["points_lost"] >= 7 for f in c["findings"]):
                earned = min(earned, 6)
            if "ju_recommend" in caps:
                earned = min(earned, 12)
            mf = [{"id": f"m_{c['name'][:3].lower()}_{i}", "section": c["name"],
                   "title": polish.clean_text(str(f.get("title", ""))[:120]),
                   "why": polish.clean_text(str(f.get("why", ""))[:300]),
                   "evidence": str(f.get("evidence", "") or "Not found in the draft.")[:160],
                   "fix": polish.clean_text(str(f.get("fix", ""))[:300]),
                   "points_lost": float(f.get("points_lost") or 0),
                   "severity": "major" if float(f.get("points_lost") or 0) >= 4 else "minor"}
                  for i, f in enumerate(m.get("findings") or []) if f.get("title")]
            keep = [f for f in c["findings"] if f["id"].startswith(DETERMINISTIC)
                    and not any(_overlap(f["title"], x["title"]) for x in mf)]
            fs = sorted(mf + keep, key=lambda f: -f["points_lost"])
            cats.append({**c, "earned": round(earned, 1), "score": round(100 * earned / c["weight"]),
                         "findings": fs, "strengths": [polish.clean_text(str(x)) for x in (m.get("strengths") or []) if x][:3]
                         or c["strengths"]})
        overall = round(sum(c["earned"] for c in cats))
        status = "blocked" if base["blocked"] else ("ready" if overall >= th else "review" if overall >= 55 else "need")
        findings = sorted([f for c in cats for f in c["findings"]], key=lambda f: -f["points_lost"])
        rw = {k: polish.clean_text(str(v)) for k, v in (data.get("rewrites") or {}).items()
              if k in rubric.SECTION_LABELS and str(v or "").strip()}
        return {**base, "overall": overall, "status": status, "categories": cats, "findings": findings,
                "missing": [f["title"] for f in findings[:8]],
                "strong": [s for c in cats for s in c["strengths"]][:6] or base["strong"],
                "rewrite_suggestions": rw, "reviewer_summary": polish.clean_text(str(data.get("reviewer_summary", ""))),
                "rules_score": base["overall"], "engine": "model:" + str(config.load().get("model")),
                "coach": base["coach"] if status in ("blocked", base["status"]) else
                {"ready": "This holds together. Close any remaining findings, then hand off.",
                 "review": "The structure is there. The findings below are what separates this from approval-ready.",
                 "need": "This reads as a summary, not a case Finance can approve."}.get(status, base["coach"])}
    except (llm.LLMError, ValueError, KeyError, TypeError, AttributeError) as e:
        base["engine"], base["engine_error"] = "rules (model unavailable)", str(e)[:300]
        return base


def _overlap(a, b):
    A, B = set(re.findall(r"[a-z]{4,}", a.lower())), set(re.findall(r"[a-z]{4,}", b.lower()))
    return bool(A) and len(A & B) / max(1, len(A | B)) > 0.4
