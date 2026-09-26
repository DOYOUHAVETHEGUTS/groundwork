"""Two agents: Structuring and Qualification.

Both degrade gracefully: if no API key is set, or the call fails, the
deterministic rules in rubric.py take over so the app is always usable.
"""
import json
import re

import config
import llm
import rubric

STANDARD = """The five-point capital justification standard used by this organization:
1. Background / Current Situation — what exists today, quantified (age, failure rate,
   volume, hours lost, cost of doing nothing). Dates and numbers, not adjectives.
2. Proposal — the specific solution, vendor, scope, in-service date, what is in and out of scope.
3. Justification — the business outcome the spend buys, tied to revenue, cost, risk,
   safety or capacity. Includes an operational impact assessment (headcount, turn time,
   tonnage, capacity) where relevant.
4. Risks — named risks, each paired with a mitigation (contract SLAs, phasing, monitoring).
5. Alternatives — the other options considered and the specific reason each was not selected.
Financially complete requests state one-time cost, recurring cost, annual benefit,
NPV at a 20% discount rate, 5-year MIRR, payback period, and the assumptions behind them."""

STRUCTURE_SYSTEM = f"""You convert an employee's plain-language capital request into the
five-point structure below. You are a drafting assistant, not an approver.

{STANDARD}

Rules:
- Use ONLY what the requester said, plus safe neutral framing. Never invent costs,
  dates, vendors, volumes, savings or headcount. If a fact is not given, leave the field
  empty and list it in missing_information.
- Write in plain business English, third person, no marketing language.
- missing_information must be specific and answerable, e.g. "Quoted purchase price
  including tax and delivery", not "more detail".

Return JSON only with exactly these keys:
{{"executive_summary":"", "background":"", "proposal":"", "justification":"",
 "risks":"", "alternatives":"", "operational_impact":"", "affected_groups":"",
 "timing":"", "capex":"", "opex_annual":"", "benefit_annual":"", "npv":"",
 "mirr":"", "payback_months":"", "assumptions":"", "missing_information":[]}}"""

QUALIFY_SYSTEM = f"""You score a drafted capital request against the five-point standard
before it is entered into Levelpath.

{STANDARD}

Score each category 0-100 using these weights:
Current Situation 15, Proposal 20, Financial Case 25, Justification 15, Risks 15, Alternatives 10.
Be strict on the Financial Case: a vendor quote alone is not a financial case; NPV,
payback and stated assumptions are what make it one.

Return JSON only:
{{"categories":[{{"name":"","score":0,"weight":0,"comment":""}}],
 "strong":[""], "missing":[""], "coach":"", "rewrite_suggestions":{{"section_key":"suggested stronger text"}}}}
Keep strong to <=5 items, missing to <=6 items, coach to 2 sentences.
rewrite_suggestions may cover at most 2 of: background, proposal, justification, risks, alternatives."""


def _clean(d, keys):
    out = {}
    for k in keys:
        v = d.get(k, "")
        if isinstance(v, list):
            v = "\n".join(f"- {str(i)}" for i in v)
        out[k] = ("" if v is None else str(v)).strip()
    return out


# ------------------------------------------------------------ structuring
def structure(text: str, meta: dict) -> dict:
    keys = [k for k in rubric.BLANK_REQUEST
            if k not in ("title", "request_type", "department", "requester",
                         "original_description", "missing_information")]
    if llm.available():
        try:
            user = json.dumps({
                "title": meta.get("title", ""),
                "request_type": meta.get("request_type", ""),
                "department": meta.get("department", ""),
                "requester_description": text,
            }, indent=2)
            data = llm.chat_json(STRUCTURE_SYSTEM, user)
            out = _clean(data, keys)
            mi = data.get("missing_information") or []
            out["missing_information"] = [str(m) for m in mi][:8]
            out["engine"] = "model:" + str(config.load().get("model"))
            return out
        except llm.LLMError as e:
            fb = _fallback_structure(text)
            fb["engine"] = "rules (model unavailable)"
            fb["engine_error"] = str(e)[:300]
            return fb
    fb = _fallback_structure(text)
    fb["engine"] = "rules"
    return fb


def _fallback_structure(text: str) -> dict:
    """Keyword split so the app still produces a usable draft with no API key."""
    t = (text or "").strip()
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", t) if s.strip()]

    def grab(*pats):
        hits = [s for s in sentences if any(re.search(p, s, re.I) for p in pats)]
        return " ".join(hits)

    problem = grab(r"\bfail|break|old|outdated|worn|rust|slow|manual|error|down|unreliable|expire") \
        or " ".join(sentences[:2])
    ask = grab(r"\breplace|purchase|buy|need|request|install|upgrade|procure|partner")
    risk = grab(r"\brisk|if we don't|stranded|delay|unsafe|penalt|outage")
    alt = grab(r"\balternativ|instead|option|other than|rather than|considered")
    cost = ""
    m = re.search(r"\$\s?[\d,]+(?:\.\d{2})?", t)
    if m:
        cost = m.group(0)

    return {
        "executive_summary": "",
        "background": problem,
        "proposal": ask,
        "justification": "",
        "risks": risk,
        "alternatives": alt,
        "operational_impact": "",
        "affected_groups": "",
        "timing": "",
        "capex": cost,
        "opex_annual": "", "benefit_annual": "", "npv": "", "mirr": "",
        "payback_months": "", "assumptions": "",
        "missing_information": [
            "Quoted one-time cost including tax, delivery and installation",
            "Recurring annual cost after go-live",
            "Annual benefit: hours saved, cost avoided or revenue enabled",
            "NPV at 20%, 5-year MIRR and payback period",
            "Who is affected and how many people",
            "Required in-service date and why",
            "Alternatives considered and why they were not selected",
        ],
    }


# ------------------------------------------------------------ qualification
def qualify(req: dict) -> dict:
    base = rubric.score_request(req)
    if not llm.available():
        return base
    try:
        payload = {k: req.get(k, "") for k in rubric.BLANK_REQUEST if k != "missing_information"}
        data = llm.chat_json(QUALIFY_SYSTEM, json.dumps(payload, indent=2))
        weights = {n: w for n, w, _ in rubric.CATEGORIES}
        cats = []
        for c in data.get("categories", []):
            name = str(c.get("name", "")).strip()
            if name not in weights:
                continue
            cats.append({
                "name": name,
                "score": max(0, min(100, int(float(c.get("score", 0))))),
                "weight": weights[name],
                "comment": str(c.get("comment", ""))[:240],
            })
        if len(cats) < len(weights):          # model skipped categories -> trust rules
            return base
        overall = round(sum(c["score"] * c["weight"] for c in cats) / 100)
        threshold = int(config.load().get("min_score_to_advance", 75))
        status = "ready" if overall >= threshold else ("review" if overall >= 55 else "need")
        return {
            "overall": overall,
            "categories": cats,
            "strong": [str(s) for s in data.get("strong", [])][:5] or base["strong"],
            "missing": [str(s) for s in data.get("missing", [])][:6] or base["missing"],
            "status": status,
            "coach": str(data.get("coach", "")) or base["coach"],
            "rewrite_suggestions": {
                k: str(v)[:1500] for k, v in (data.get("rewrite_suggestions") or {}).items()
                if k in rubric.BLANK_REQUEST
            },
            "engine": "model:" + str(config.load().get("model")),
            "rules_score": base["overall"],
        }
    except (llm.LLMError, ValueError, KeyError) as e:
        base["engine"] = "rules (model unavailable)"
        base["engine_error"] = str(e)[:300]
        return base
