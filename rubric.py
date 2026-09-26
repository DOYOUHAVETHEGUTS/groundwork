"""The five-point standard, the scoring rubric, and the deterministic fallback.

Calibrated against two real examples:
  * Parcel Product 5-Point (approved)  -> full sections, quantified financials,
    named alternatives with reasons, operational impact with FTE math.
  * BOS Van Replacement (weak)         -> narrative only, price quote but no
    NPV/payback, alternatives named without evaluation, no operational impact.
"""
import re

# ---------------------------------------------------------------- structure
SECTIONS = [
    ("background",        "Background / Current Situation"),
    ("proposal",          "Proposal"),
    ("justification",     "Justification"),
    ("risks",             "Risks"),
    ("alternatives",      "Alternatives"),
]

EXTRA_FIELDS = [
    ("executive_summary",   "Executive Summary"),
    ("operational_impact",  "Operational Impact Assessment"),
    ("affected_groups",     "Who is affected"),
    ("timing",              "Why now / timing"),
]

FINANCIAL_FIELDS = [
    ("capex",           "One-time / capital cost"),
    ("opex_annual",     "Recurring annual cost"),
    ("benefit_annual",  "Annual benefit or revenue"),
    ("npv",             "NPV @ 20%"),
    ("mirr",            "5-year MIRR"),
    ("payback_months",  "Payback (months)"),
    ("assumptions",     "Key financial assumptions"),
]

# name, weight, what a full-credit answer contains
CATEGORIES = [
    ("Current Situation",   15, "Concrete, dated, quantified description of the problem today."),
    ("Proposal",            20, "Specific scope, vendor/solution, timeline to deliver."),
    ("Financial Case",      25, "Cost, benefit, NPV/MIRR/payback and stated assumptions."),
    ("Justification",       15, "Ties the spend to a business outcome, not just condition."),
    ("Risks",               15, "Named risks with mitigations, not just a list."),
    ("Alternatives",        10, "Options evaluated and reasons they were not chosen."),
]

BLANK_REQUEST = {
    "title": "", "request_type": "", "department": "", "requester": "",
    "original_description": "",
    "executive_summary": "", "background": "", "proposal": "",
    "justification": "", "risks": "", "alternatives": "",
    "operational_impact": "", "affected_groups": "", "timing": "",
    "capex": "", "opex_annual": "", "benefit_annual": "",
    "npv": "", "mirr": "", "payback_months": "", "assumptions": "",
    "missing_information": [],
}

NUM = re.compile(
    r"\$\s?[\d]"                                  # $30,348
    r"|[\d][\d,\.]*\s*(%|k\b|m\b|million|billion|hours?|hrs?|fte|months?|years?|days?"
    r"|lbs?|pounds?|miles?|mi\b|units?|parcels?|shipments?|times?|flights?)"
    r"|\b(19|20)\d{2}\b",                         # a model year or a date
    re.I)
MITIGATION = re.compile(r"mitigat|sla|contract|monitor|safeguard|offset|we intend|we will|addressed by", re.I)
EVALUATED = re.compile(r"because|however|shortcoming|rejected|not chosen|more expensive|does not|lacks|considered", re.I)


def _f(v):
    return (str(v or "")).strip()


def _words(v):
    return len(_f(v).split())


def _has_num(v):
    return bool(NUM.search(_f(v)))


# ---------------------------------------------------------------- scoring
def score_request(req: dict) -> dict:
    """Deterministic rubric. Used offline and as the floor when the model is on."""
    cats, strong, missing = [], [], []

    # Current Situation
    s = 25
    if _words(req.get("background")) >= 25:
        s = 70
        strong.append("Current situation is described in enough detail to stand on its own")
    elif _words(req.get("background")) >= 10:
        s = 50
    if _has_num(req.get("background")):
        s = min(100, s + 25)
    else:
        missing.append("Quantify the current situation (failure rate, age, hours lost, volume)")
    cats.append(["Current Situation", s])

    # Proposal
    s = 25
    if _words(req.get("proposal")) >= 20:
        s = 65
        strong.append("A specific solution is proposed")
    elif _words(req.get("proposal")) >= 8:
        s = 45
    if _has_num(req.get("proposal")):
        s = min(100, s + 15)
    if _f(req.get("timing")):
        s = min(100, s + 15)
    else:
        missing.append("State the in-service / go-live date and why that timing")
    cats.append(["Proposal", s])

    # Financial Case — the section the weak example failed
    fin = [k for k, _ in FINANCIAL_FIELDS if _f(req.get(k))]
    s = min(100, 12 + len(fin) * 13)
    for k, label in FINANCIAL_FIELDS:
        if not _f(req.get(k)):
            missing.append(f"Add {label.lower()}")
    if {"npv", "payback_months"} <= set(fin):
        strong.append("NPV and payback are stated, not just a purchase price")
    cats.append(["Financial Case", s])

    # Justification
    s = 30
    if _words(req.get("justification")) >= 20:
        s = 60
    if _f(req.get("operational_impact")):
        s = min(100, s + 20)
        strong.append("Operational impact is assessed")
    else:
        missing.append("Add an operational impact assessment (headcount, turn time, capacity)")
    if _f(req.get("affected_groups")):
        s = min(100, s + 20)
    else:
        missing.append("Identify who is affected and how many people")
    cats.append(["Justification", s])

    # Risks
    r = _f(req.get("risks"))
    s = 20 if not r else (55 if _words(r) < 25 else 70)
    if MITIGATION.search(r):
        s = min(100, s + 30)
        strong.append("Risks are paired with mitigations")
    elif r:
        missing.append("Pair each risk with how it will be mitigated")
    cats.append(["Risks", s])

    # Alternatives
    a = _f(req.get("alternatives"))
    s = 20 if not a else 55
    if EVALUATED.search(a):
        s = 90
        strong.append("Alternatives were evaluated, with reasons for not selecting them")
    elif a:
        missing.append("Say why each alternative was not selected")
    cats.append(["Alternatives", s])

    weights = {n: w for n, w, _ in CATEGORIES}
    overall = round(sum(sc * weights[n] for n, sc in cats) / 100)

    status = "ready" if overall >= 75 else ("review" if overall >= 55 else "need")
    coach = {
        "ready": "This holds together. Confirm the financial assumptions with Finance, then move it into Levelpath.",
        "review": "The narrative is there — the financial case is what is holding the score down. Add NPV, payback and the assumptions behind them.",
        "need": "Right now this reads as a condition report, not an investment case. Quantify the cost of doing nothing and build the financial section before submitting.",
    }[status]

    if not strong:
        strong.append("You have made a start — more detail will strengthen it")

    return {
        "overall": overall,
        "categories": [{"name": n, "score": sc, "weight": weights[n]} for n, sc in cats],
        "strong": strong[:5],
        "missing": list(dict.fromkeys(missing))[:6],
        "status": status,
        "coach": coach,
        "engine": "rules",
    }
