"""The five-point standard, hard requirements, and the deterministic scorer.

Five literal sections, as Finance reads them:
    Current Situation · Proposal · Cost · Justification · Alternatives
plus an optional Appendix / supporting detail.

Hard requirements (independent of the score):
    * Three vendor quotes          -> automatic fail if missing
    * CRE-driven requests          -> CRE template must be requested before submission
    * CRE status must be declared  -> yes / no

Every lost point produces a finding: what went wrong, why it matters to Finance,
the evidence (or "not found"), and the fix. That powers the "what went wrong"
dropdown under each section score.

NPV / MIRR / payback are intentionally out of scope: the division prepares them
with its own template.
"""
import re

SECTIONS = [
    ("current_situation", "Current Situation"),
    ("proposal", "Proposal"),
    ("cost", "Cost"),
    ("justification", "Justification"),
    ("alternatives", "Alternatives"),
]
SECTION_LABELS = dict(SECTIONS)
APPENDIX = ("appendix", "Appendix & Supporting Detail")
WEIGHTS = {"Current Situation": 25, "Proposal": 15, "Cost": 20, "Justification": 20, "Alternatives": 20}
CATEGORIES = [
    ("Current Situation", 25, "Quantified baseline, why the problem happens, real detail, visuals."),
    ("Proposal", 15, "One clear ask that matches the title, specific scope, timing and constraints."),
    ("Cost", 20, "Itemized build-up, itemized vendor quotes, contingency, totals that reconcile, funding source."),
    ("Justification", 20, "Explicit recommendation, quantified benefit tied to the baseline, risks and assumptions."),
    ("Alternatives", 20, "Real options, three vendor quotes, which vendor and why, compared on one basis."),
]
FIELD_LABELS = dict(SECTIONS + [APPENDIX])

BLANK_REQUEST = {
    "title": "", "request_type": "", "department": "", "requester": "",
    "original_description": "",
    "current_situation": "", "proposal": "", "cost": "", "justification": "",
    "alternatives": "", "appendix": "",
    "cre_driven": "",            # "" (not declared) | "yes" | "no"
    "cre_template": "",          # "" | "requested" | "attached"
    "quotes": [],                # [{vendor, date, reference, items:[{desc,qty,unit}], total, lead_time, warranty, notes, selected}]
    "selection_rationale": "",
    "cost_items": [],            # [{desc, qty, unit}]
    "contingency_pct": "",
    "funding_source": "",
    "exhibits": [],              # [{label, title, date, kind, image_id, citation}]
    "flow_steps": [],            # [{name, duration, volume, bottleneck, note}]
    "missing_information": [],
    "finance_contact_name": "", "finance_contact_email": "",
}

# ------------------------------------------------------------------ patterns
NUM = re.compile(
    r"\$\s?\d[\d,]*(?:\.\d+)?\s*(?:k|m|mm|million|billion)?"
    r"|\b\d[\d,]*(?:\.\d+)?\s*(?:%|k\b|m\b|million|billion|hours?|hrs?|fte|months?|years?|yrs?|days?|weeks?"
    r"|lbs?|pounds?|miles?|units?|stands?|engines?|aircraft|bays?|positions?|shipments?|times?|flights?|turns?"
    r"|people|employees|mechanics|agents|buildings?|events?|failures?|breakdowns?|shorts?)"
    r"|\b(?:19|20)\d{2}\b|\b\d[\d,]{2,}(?:\.\d+)?\b|\b\d+\s*(?:of|out of)\s*\d+\b", re.I)
MONEY = re.compile(r"\$\s?(\d[\d,]*(?:\.\d+)?)\s*(k|mm|m|million|thousand|billion)?\b", re.I)
VAGUE = re.compile(r"\b(limited|growing|aging|ageing|old|significant(?:ly)?|various|several|many|multiple|"
                   r"increasing(?:ly)?|frequent(?:ly)?|numerous|substantial|continues to)\b", re.I)
CAUSAL = re.compile(r"\b(because|due to|caused by|as a result|which means|so once|so that|every time|each time|"
                    r"whenever|results? in|leads? to|consumes?|forces?|requires?|so the|once an?|until)\b", re.I)
RECOMMEND = re.compile(r"\brecommend(?:ed|s|ation)?\b|\bwe (?:request|propose|ask|recommend)\b|"
                       r"\bapproval (?:is )?(?:requested|of)\b|\bpreferred option\b|\(selected\)|\bselect(?:ed)? option\b", re.I)
RISK = re.compile(r"\brisks?\b|assum|mitigat|contingen|escalat|exposure|lead time|sensitiv|if .{0,40}(?:fail|delay|slip)", re.I)
EVAL = re.compile(r"because|however|rejected|not (?:selected|chosen)|higher|lower|cheaper|more expensive|lead time|"
                  r"npv|\$|did not|does not|lacks|exceed|takes? \d|escalate|causing|downtime|risk|costs? more|slower", re.I)
CONSEQUENCE = re.compile(r"\bif (?:not|we do nothing|nothing)\b|\bno alternative\b|\bwill be required\b|\bwithout\b|"
                         r"\bslow\b|\blimited\b|\bwithhold\b|\bdowntime\b", re.I)
OPTIONISH = re.compile(r"option|alternative|lease|rent|repair|refurb|buy|purchase|phase|run to failure|vendor|"
                       r"contractor|outsourc|in-house|do nothing", re.I)
FUNDING = re.compile(r"\bfund(?:ed|ing|s)?\b|\bbudget\b|capital plan|cost cent(?:er|re)|\bcapex\b|\bopex\b|"
                     r"service credits?|\bcredits?\b|\bAR\b|authori[sz]ation", re.I)
TIMING = re.compile(r"\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\b|\bQ[1-4]\b|\b20\d{2}\b|"
                    r"\b\d+\s*(?:weeks?|months?|days?)\b|\bby (?:the )?(?:end|start)\b|\bphase\b", re.I)
CONSTRAINT = re.compile(r"supplier|contract|approved|credit|exception|lead time|install|permit|outage|sole|"
                        r"procure|vendor|scope of work|engineering firm|manage", re.I)
REF = re.compile(r"\b(Appendix|Exhibit|Attachment)\s+([A-Z]|\d{1,2})\b")
CRE_EXPLICIT = re.compile(r"corporate real estate|\bCRE\b", re.I)
CRE_LIKELY = re.compile(r"\b(facilit(?:y|ies)|lease|building|hangar|construction|renovat|real estate|"
                        r"roof|hvac|electrical distribution|office space|warehouse)\b", re.I)
MULT = {"k": 1e3, "thousand": 1e3, "m": 1e6, "mm": 1e6, "million": 1e6, "billion": 1e9}


# ------------------------------------------------------------------ text helpers
def _f(v):
    return str(v or "").strip()


def words(t):
    return len(re.findall(r"[A-Za-z0-9$%][\w$%.,'/-]*", _f(t)))


def sentences(t):
    t = re.sub(r"\s+", " ", _f(t).replace("\n- ", ". ").replace("\n", ". "))
    return [s.strip(" -•.") for s in re.split(r"(?<=[.!?])\s+(?=[A-Z$\d(\[])", t) if len(s.strip(" -•.")) > 3]


COUNTWORD = re.compile(r"\b(two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|fifteen|twenty|thirty|fifty|hundred)\b", re.I)


def nums(t):
    t = re.sub(r"(?m)^\s*\d{1,2}[.)]\s", " ", _f(t))                       # drop list / section ordinals
    out = {re.sub(r"\s+", " ", m.group(0).lower()) for m in NUM.finditer(t)}
    out |= {m.group(0) for m in re.finditer(r"(?<![\w.])\$?\d[\d,]*(?:\.\d+)?%?(?![\w])", t)}
    out |= {m.group(0).lower() + "#" for m in COUNTWORD.finditer(t)}
    return out


def all_money(t):
    return [float(m.group(1).replace(",", "")) * MULT.get((m.group(2) or "").lower(), 1)
            for m in MONEY.finditer(_f(t))]


def money(t):
    v = all_money(t)
    return v[0] if v else None


def _num(v):
    try:
        return float(str(v).replace("$", "").replace(",", "").strip() or 0)
    except ValueError:
        return 0.0


def snippet(text, pattern=None, n=12):
    """Up to n words of evidence from the draft, around a match if given."""
    t = re.sub(r"\s+", " ", _f(text))
    if not t:
        return ""
    if pattern is not None:
        m = pattern.search(t)
        if m:
            t = t[max(0, t.rfind(" ", 0, max(0, m.start() - 30)) + 1):]
    w = t.split()
    return " ".join(w[:n]) + ("…" if len(w) > n else "")


# ------------------------------------------------------------------ structured helpers
def upgrade(req: dict) -> dict:
    """Bring older drafts (Background / financial fields) forward to the five-point layout."""
    if _f(req.get("background")) and not _f(req.get("current_situation")):
        req["current_situation"] = req["background"]
    extra = [_f(req.get(k)) for k in ("operational_impact", "affected_groups") if _f(req.get(k))]
    if extra and not any(x in _f(req.get("justification")) for x in extra):
        req["justification"] = " ".join([_f(req.get("justification"))] + extra).strip()
    if _f(req.get("timing")) and _f(req.get("timing")) not in _f(req.get("proposal")):
        req["proposal"] = (_f(req.get("proposal")) + " Timing: " + _f(req["timing"])).strip()
    if not _f(req.get("cost")):
        bits = [f"{lbl}: {_f(req.get(k))}" for k, lbl in (("capex", "One-time cost"),
                                                           ("opex_annual", "Recurring annual cost")) if _f(req.get(k))]
        if bits:
            req["cost"] = "; ".join(bits)
    for k, v in BLANK_REQUEST.items():
        if k not in req or req[k] is None:
            req[k] = [] if isinstance(v, list) else v
    return req


def quote_items(q):
    return [i for i in (q.get("items") or [])
            if _f(i.get("desc")) and _num(i.get("qty") or 1) > 0 and _num(i.get("unit")) > 0]


def quote_total(q):
    items = quote_items(q)
    if items:
        return sum(_num(i.get("qty") or 1) * _num(i.get("unit")) for i in items)
    return money(q.get("total")) or _num(q.get("total"))


def valid_quotes(req):
    return [q for q in (req.get("quotes") or []) if _f(q.get("vendor")) and quote_total(q) > 0]


def quote_itemized(q):
    return len(quote_items(q)) >= 2


def selected_quote(req):
    return next((q for q in valid_quotes(req) if q.get("selected")), None)


def cost_lines(req):
    """(lines, structured): structured cost items, else money-bearing rows from the Cost text."""
    items = [i for i in (req.get("cost_items") or []) if _f(i.get("desc")) and _num(i.get("unit")) > 0]
    if items:
        return [(i["desc"], _num(i.get("qty") or 1) * _num(i.get("unit"))) for i in items], True
    out, amt_col, qty_tbl = [], None, False
    numrx = r"(?<![\w.])\$?\s?(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d{4,}(?:\.\d+)?)(?![\w%])"
    for ln in _f(req.get("cost")).split("\n"):
        if "|" in ln:
            cells = [c.strip() for c in ln.split("|")]
            if not re.search(r"\d", " ".join(cells[1:])):          # header row
                heads = [h.lower() for h in cells]
                cands = [i for i, h in enumerate(heads) if re.search(r"cost|amount|total|price|\$|value", h)
                         and not re.search(r"comment|note", h)]
                amt_col = cands[-1] if cands else None
                qty_tbl = any(re.search(r"\bqty\b|quantity|units?\b", h) for h in heads)
                continue
            cell = cells[amt_col] if amt_col is not None and amt_col < len(cells) else " ".join(cells[1:])
            vals = [float(x.replace(",", "")) for x in re.findall(numrx, cell)]
            if vals:
                out.append((cells[0], vals[0]))
        elif "$" in ln:
            amt_col = None
            vals = [v for v in (float(x.replace(",", "")) for x in re.findall(numrx, ln)) if not 1900 <= v <= 2100]
            if vals:
                out.append((ln.strip(" -"), vals[0]))
    return out, qty_tbl


def reconcile(req):
    """(True|False|None, detail): do the parts add up to the stated total?"""
    items = [i for i in (req.get("cost_items") or []) if _f(i.get("desc")) and _num(i.get("unit")) > 0]
    if items:
        sub = sum(_num(i.get("qty") or 1) * _num(i.get("unit")) for i in items)
        pct = _num(req.get("contingency_pct"))
        total = sub * (1 + pct / 100)
        stated = stated_total(req.get("cost"))
        if stated and abs(stated - total) / max(stated, 1) > 0.01:
            return False, f"line items plus contingency come to ${total:,.0f} but the Cost section states ${stated:,.0f}"
        return True, f"${total:,.0f}" + (f" including {pct:g}% contingency" if pct else "")
    lines, _ = cost_lines(req)
    totals = [v for d, v in lines if re.search(r"\btotal\b", d, re.I) and not re.search(r"sub-?total", d, re.I)]
    parts = [v for d, v in lines if not re.search(r"total", d, re.I)]
    if totals and len(parts) >= 2:
        grand, s = max(totals), sum(parts)
        if abs(grand - s) / max(grand, 1) <= 0.005:
            return True, f"items sum to the ${grand:,.0f} total"
        return False, f"items sum to ${s:,.0f} but the total shown is ${grand:,.0f}"
    return None, ""


def stated_total(text):
    """The total the author explicitly states ("total $X" / "$X total" / "$X all-in"), last one wins."""
    hits = [(m.start(), m.group(1)) for m in re.finditer(r"total\D{0,15}\$\s?(\d[\d,]*(?:\.\d+)?)", _f(text), re.I)]
    hits += [(m.start(), m.group(1)) for m in re.finditer(r"\$\s?(\d[\d,]*(?:\.\d+)?)\s*(?:total|all[- ]in)", _f(text), re.I)]
    return float(sorted(hits)[-1][1].replace(",", "")) if hits else None


def cre_status(req):
    text = " ".join(_f(req.get(k)) for k in ("title", "department", "request_type", "current_situation", "proposal"))
    hint = "explicit" if CRE_EXPLICIT.search(text) else ("likely" if CRE_LIKELY.search(text) else "")
    return {"declared": _f(req.get("cre_driven")), "template": _f(req.get("cre_template")), "hint": hint}


def gates(req: dict) -> list:
    out = []
    n = len(valid_quotes(req))
    out.append({
        "id": "three_quotes", "label": "Three vendor quotes", "severity": "auto_fail", "passed": n >= 3,
        "message": (f"{n} vendor quotes on file." if n >= 3 else
                    f"Automatic fail: three vendor quotes are required and {n} {'is' if n == 1 else 'are'} on file. "
                    "Enter each quote with its line items and attach the vendor quote letters in the appendix."),
    })
    cre = cre_status(req)
    if cre["declared"] == "yes":
        ok = cre["template"] in ("requested", "attached")
        out.append({"id": "cre_template", "label": "CRE template", "severity": "required", "passed": ok,
                    "message": f"CRE-driven. The CRE template is {cre['template']}." if ok else
                    "CRE-driven request: request the Corporate Real Estate template and attach it before submission."})
    elif cre["declared"] != "no":
        why = {"explicit": "The draft mentions Corporate Real Estate. ",
               "likely": "This looks like facility or building work. "}.get(cre["hint"], "")
        out.append({"id": "cre_declared", "label": "CRE-driven?", "severity": "required", "passed": False,
                    "message": why + "Declare whether this request is CRE-driven. CRE-driven requests need the CRE template."})
    return out


def _visuals(req):
    ex = req.get("exhibits") or []
    photos = sum(1 for e in ex if (e.get("kind") or "") in ("photo", "image", "example", "reference"))
    flow = (len([s for s in (req.get("flow_steps") or []) if _f(s.get("name"))]) >= 3
            or any((e.get("kind") or "") in ("diagram", "flow") for e in ex))
    return photos, flow


# ------------------------------------------------------------------ scoring
def score_request(req: dict, threshold: int = 75) -> dict:
    req = upgrade(dict(req))
    S = {k: _f(req.get(k)) for k, _ in SECTIONS}
    allt = " ".join(S.values())
    secs = {lbl: {"earned": 0.0, "max": WEIGHTS[lbl], "findings": [], "strengths": []} for _, lbl in SECTIONS}
    L = ""

    def add(pts):
        secs[L]["earned"] += pts

    def lose(fid, title, why, fix, lost, evidence="", severity=None):
        secs[L]["findings"].append({
            "id": fid, "section": L, "title": title, "why": why, "fix": fix,
            "evidence": evidence or "Not found in the draft.", "points_lost": round(lost, 1),
            "severity": severity or ("major" if lost >= 4 else "minor")})

    def good(msg):
        secs[L]["strengths"].append(msg)

    # ---------------- Current Situation (25)
    L, cs = "Current Situation", S["current_situation"]
    n = len(nums(cs))
    pts = 10 if n >= 5 else 7 if n >= 3 else 3 if n >= 1 else 0
    if len(VAGUE.findall(cs)) >= 2 and n < 8:
        pts = max(0, pts - 3)
    add(pts)
    if pts < 10:

        lose("cs_quant", "The current state isn't measured" if n < 3 else "The baseline needs more numbers",
             "Finance can't size a problem described as \"limited\" or \"growing\". Counts, ages, utilization and "
             "failure rates are what make the need credible.",
             "State the baseline in numbers: how many you have, how many are in use, their age, and the failures or "
             "delays in the last 12 months.", 10 - pts, snippet(cs, VAGUE))
    else:
        good("The current state is measured, not described")
    cause = bool(CAUSAL.search(cs))
    pts = 7 if cause and words(cs) >= 40 else 4 if cause else 0
    add(pts)
    if pts < 7:
        lose("cs_cause", "It says the problem exists, not why it happens",
             "Approvers fund fixes to causes. Without the workflow reason, the request reads as a symptom and invites "
             "\"why can't you work around it?\"",
             "Explain the mechanism in one or two sentences: what in the process causes the shortfall, and when it hits.",
             7 - pts, snippet(cs))
    else:
        good("Explains why the problem happens")
    w = words(cs)
    pts = 4 if w >= 120 else 2 if w >= 60 else 0
    add(pts)
    if pts < 4:
        lose("cs_detail", "Too high-level to evaluate",
             "Short, summary-only five points are the most common reason Finance sends a request back. The research "
             "behind the request has to be on the page.",
             "Add the data you gathered (inventory, incident log, utilization, dates). A table is fine.",
             4 - pts, f"{w} words in this section")
    photos, flow = _visuals(req)
    add((2 if photos else 0) + (2 if flow else 0))
    if not photos:
        lose("cs_photo", "No pictures of the current condition",
             "A photo of the asset or the workaround does more than a paragraph. Missing visuals are one of the most "
             "common weaknesses Finance sees.",
             "Add at least one labeled photo: a real site photo, or a clearly labeled example image.", 2)
    if not flow:
        lose("cs_flow", "No process flow showing the bottleneck",
             "A simple flow of the steps, with the bottleneck marked, shows Finance where time or capacity is lost.",
             "Build the process flow in Groundwork (three or more steps) and mark the bottleneck.", 2)
    if photos and flow:
        good("Visuals show the condition and the bottleneck")

    # ---------------- Proposal (15)
    L, pr = "Proposal", S["proposal"]
    bundled = next((s for s in sentences(pr) if s.count(",") >= 2 and re.search(r",\s*and\s", s)
                    and re.search(r"fund|request|purchase|cover|approve|buy", s, re.I)), None)
    title = _f(req.get("title"))
    ids = [t for t in re.findall(r"\b(?:[A-Z]\d{0,4}|[A-Z]{1,4}\d+[A-Z\d-]*)\b", title) if t not in ("A", "I")]
    mismatch = [t for t in ids if not re.search(rf"\b{re.escape(t)}\b", allt)]
    pts = 6 if pr else 0
    if bundled:
        pts = 0
        lose("pr_bundled", "Several unrelated asks are bundled into one request",
             "Each ask needs its own decision. Bundling forces Finance to approve or reject everything together and "
             "hides which item is actually urgent.",
             "Split this into separate five points. Keep one ask here: the one the title names.", 6, snippet(bundled))
    if mismatch:
        pts = max(0, pts - 3)
        lose("pr_title", "The title doesn't match the request",
             f"The title names \"{' '.join(mismatch)}\" but the body never does. A mismatched title is the first "
             "thing a reviewer catches, and it undermines everything after it.",
             "Rename the request so the title states exactly what is being bought.", 3, title)
    add(pts)
    if pts == 6:
        good("One clear ask that matches the title")
    has_n, w = bool(nums(pr)), words(pr)
    pts = 5 if has_n and w >= 25 else 3 if (has_n or w >= 25) else 1 if pr else 0
    add(pts)
    if pts < 5:
        lose("pr_scope", "The scope isn't specific",
             "Finance approves a defined scope: what, how many, from whom, where. Vague scope becomes scope creep.",
             "State the quantity, the item or model class, the vendor, and where it goes.", 5 - pts, snippet(pr))
    tm = bool(TIMING.search(pr)) or bool(re.search(r"timeline|milestone|schedule", _f(req.get("appendix")), re.I))
    ct = bool(CONSTRAINT.search(pr + " " + S["cost"]))
    add((2 if tm else 0) + (2 if ct else 0))
    if not tm:
        lose("pr_timing", "No timeline", "A request with no in-service date can't be prioritized against others.",
             "Give the in-service date and the lead time that drives it.", 2)
    if not ct:
        lose("pr_constraints", "Constraints and sourcing rules aren't stated",
             "Contract terms, approved suppliers, funding exceptions and install requirements change what Finance "
             "can approve.", "Name any sourcing restriction, contract term or install constraint.", 2)
    if bundled:
        secs[L]["earned"] = min(secs[L]["earned"], 4)

    # ---------------- Cost (20)
    L, co = "Cost", S["cost"]
    lines, structured = cost_lines(req)
    n_lines = len([l for l in lines if not re.search(r"total", l[0], re.I)])
    lump = n_lines < 2 and money(co) is not None
    pts = 7 if n_lines >= 4 or (structured and n_lines >= 2) else 5 if n_lines == 3 else 2 if n_lines == 2 else 0
    add(pts)
    if pts < 7:
        lose("co_itemized", "Cost is a single total, not a build-up" if n_lines < 2 else "The cost build-up is thin",
             "A lump sum can't be checked or negotiated. Finance needs quantity × unit cost for each line to see where "
             "the money goes.",
             "Break the total into line items (quantity × unit cost), plus tax, freight, install and contingency.",
             7 - pts, snippet(co, MONEY))
    else:
        good("Costs are itemized")
    vq = valid_quotes(req)
    itemized = [q for q in vq if quote_itemized(q)]
    pts = 4 if vq and len(itemized) == len(vq) else 2 if itemized else 0
    add(pts)
    if pts < 4:
        lose("co_quotes_itemized", "Vendor quotes aren't itemized",
             "Each vendor quote has to be broken out line by line so bids can be compared item for item, not only on "
             "the bottom line.", "Enter every quote with its line items: equipment, labor, freight, tax, warranty.",
             4 - pts, f"{len(itemized)} of {len(vq)} quotes itemized" if vq else "")
    has_cont = bool(re.search(r"contingen", co, re.I)) or _num(req.get("contingency_pct")) > 0
    add(3 if has_cont else 0)
    if not has_cont:
        lose("co_contingency", "No contingency shown",
             "Without a stated contingency, any overrun comes back as a second request.",
             "Add a contingency line (commonly 5–10%) and say why that level fits.", 3)
    ok, detail = reconcile(req)
    if ok:
        add(3)
        good("Totals reconcile")
    else:
        lose("co_reconcile", "The numbers don't add up" if ok is False else "Totals can't be checked",
             "A total that doesn't tie to its parts is the fastest way to lose a reviewer's confidence.",
             "Make the line items, contingency and total tie exactly.", 3,
             detail or "No line items to reconcile against.", "major" if ok is False else None)
    fund = bool(FUNDING.search(co + " " + S["proposal"])) or bool(_f(req.get("funding_source")))
    add(3 if fund else 0)
    if not fund:
        lose("co_funding", "Funding source isn't stated",
             "Finance has to know whether this is planned capital, OpEx, credits or new money.",
             "Name the funding source: capital plan line, cost center, credits or an approved exception.", 3)
    if lump:
        secs[L]["earned"] = min(secs[L]["earned"], 6)

    # ---------------- Justification (20)
    L, ju = "Justification", S["justification"]
    rec = bool(RECOMMEND.search(ju + " " + S["proposal"] + " " + S["alternatives"])) or bool(selected_quote(req))
    add(6 if rec else 0)
    if not rec:
        lose("ju_recommend", "No explicit recommendation",
             "The committee needs the decision you're asking for in one sentence. Leaving it implied reads as "
             "uncertainty.", "Close with: \"We recommend [option] at $[amount] because [reason].\"", 6)
    qn = [x for x in nums(ju) if re.search(r"\$|%|hour|hr|day|week|month|fte|turn|delay|aircraft|event|engine|stand", x)]
    pts = 7 if len(qn) >= 2 else 4 if qn else 0
    add(pts)
    if pts < 7:
        lose("ju_benefit", "The benefit isn't quantified",
             "\"Reduce delays\" and \"mitigate risk\" can't be weighed against the cost. Dollars, days, hours or "
             "capacity can.",
             "Put a number on the benefit: delay days avoided, hours saved, failures prevented, or dollars per year.",
             7 - pts, snippet(ju))
    tied = bool(nums(ju) & nums(S["current_situation"])) or (bool(qn) and re.search(r"reduc|eliminat|avoid|from .{1,20} to", ju, re.I))
    add(3 if tied else 0)
    if not tied:
        lose("ju_tie", "The benefit isn't tied back to the baseline",
             "The justification should close the loop on the problem stated in Current Situation, using the same numbers.",
             "Reference the baseline directly, for example \"cuts the 19 days out of service to 2\".", 3)
    rk = bool(RISK.search(ju + " " + S["proposal"] + " " + S["cost"] + " " + S["alternatives"]))
    add(4 if rk else 0)
    if not rk:
        lose("ju_risk", "Risks and assumptions aren't stated",
             "Lead times, price escalation and implementation risk are what Finance probes first.",
             "List the key assumptions and the main implementation risk with its mitigation.", 4)
    if not rec:
        secs[L]["earned"] = min(secs[L]["earned"], 12)

    # ---------------- Alternatives (20)
    L, al = "Alternatives", S["alternatives"]
    items = [s for s in re.split(r"\n|(?<=[.;])\s+(?=(?:Option|Alternative)\b)", al) if words(s) >= 4]
    conseq = [s for s in items if CONSEQUENCE.search(s) and (not OPTIONISH.search(s) or re.search(r"no alternative", s, re.I))]
    real = [s for s in items if EVAL.search(s) and OPTIONISH.search(s) and s not in conseq]
    n_real = len(real)
    pts = 6 if n_real >= 2 else 3 if n_real == 1 else 0
    add(pts)
    if pts < 6:
        lose("al_real", "These are consequences of not acting, not alternatives" if conseq and not real
             else "Fewer than two real alternatives",
             "An alternative is another way to solve the problem (lease, repair, phase, a different vendor) with its "
             "cost and trade-off. What happens if nothing is done belongs in the Justification.",
             "List at least two real options, each with its cost and why it was not chosen.", 6 - pts,
             snippet(conseq[0] if conseq else al))
    n = len(vq)
    pts = 6 if n >= 3 else 3 if n == 2 else 1 if n == 1 else 0
    add(pts)
    if pts < 6:
        lose("al_quotes", "Fewer than three vendor quotes",
             "Three quotes are non-negotiable. They prove the market was tested and the price is fair.",
             "Get quotes from three vendors, enter each one itemized, and attach the quote letters in the appendix.",
             6 - pts, f"{n} quote{'' if n == 1 else 's'} on file", "major")
    sel = selected_quote(req)
    rat = _f(req.get("selection_rationale")) or (_f(sel.get("notes")) if sel else "")
    price_only = bool(rat) and bool(re.search(r"\b(lowest|cheapest|least expensive|best price)\b", rat, re.I)) and words(rat) < 18
    pts = 5 if sel and words(rat) >= 10 and not price_only else 3 if sel and rat else 2 if sel else 0
    add(pts)
    if pts < 5:
        lose("al_selection", "It doesn't say which vendor you're picking and why" if not sel else
             ("The choice is justified on price alone" if price_only else "The vendor choice needs a reason"),
             "The best quote isn't always the cheapest. Finance needs the all-in reasoning: scope covered, lead time, "
             "warranty, support, compliance and total cost.",
             "Name the selected vendor and give two or three reasons it is the best value overall.", 5 - pts,
             snippet(rat))
    comp = sum(1 for q in vq if quote_total(q) > 0) >= 2 or len(all_money(al)) >= 2
    add(3 if comp else 0)
    if not comp:
        lose("al_compare", "Options aren't compared on the same basis",
             "Without a side-by-side on cost, lead time and scope, there's no way to see why one option wins.",
             "Compare the options in one table: total cost, lead time, scope covered, key risk.", 3)
    if n_real >= 2 and n >= 3 and sel:
        good("Real options, three quotes, and a reasoned selection")

    # ---------------- cross-cutting: repeated sentences, appendix integrity
    seen = {}
    for key, lbl in SECTIONS:
        L = lbl
        for s in sentences(S[key]):
            k = re.sub(r"[^a-z0-9 ]", "", s.lower()).strip()
            if len(k) < 25:
                continue
            if k in seen:
                lose("dup_" + key, "A sentence is repeated",
                     "Repeated sentences make a draft read as unedited, which costs credibility with reviewers.",
                     f"Delete it here; it already appears in {seen[k]}.", 1, snippet(s), "minor")
                add(-1)
            else:
                seen[k] = lbl
    app = _f(req.get("appendix"))
    labels = {m.group(2).upper() for m in REF.finditer(app)}
    for e in req.get("exhibits") or []:
        m = re.search(r"\b([A-Z]|\d{1,2})\s*$", _f(e.get("label")))
        if m:
            labels.add(m.group(1).upper())
    for key, lbl in SECTIONS:
        L = lbl
        for ref in sorted({m.group(0) for m in REF.finditer(S[key])}):
            if ref.split()[-1].upper() not in labels:
                lose("ref_" + ref.split()[-1], f"{ref} is referenced but missing",
                     "A reference that doesn't resolve tells the reviewer the package is incomplete.",
                     f"Add {ref} or remove the reference.", 2, ref, "major")
                add(-2)
    letters = sorted(x for x in labels if len(x) == 1 and x.isalpha())
    if letters:
        gaps = [chr(c) for c in range(ord("A"), ord(letters[-1])) if chr(c) not in letters]
        if gaps:
            L = "Current Situation"
            lose("ref_gap", "Appendix lettering skips " + ", ".join(gaps),
                 "Gaps in exhibit lettering look like something was removed.",
                 "Re-letter the appendices so they run continuously.", 1, ", ".join(letters), "minor")
            add(-1)

    # ---------------- assemble
    cats, findings, strong = [], [], []
    for _, lbl in SECTIONS:
        s = secs[lbl]
        s["earned"] = max(0.0, min(s["max"], s["earned"]))
        s["findings"].sort(key=lambda f: -f["points_lost"])
        cats.append({"name": lbl, "weight": s["max"], "earned": round(s["earned"], 1),
                     "score": round(100 * s["earned"] / s["max"]), "findings": s["findings"],
                     "strengths": s["strengths"]})
        findings += s["findings"]
        strong += s["strengths"]
    overall = round(sum(c["earned"] for c in cats))
    g = gates(req)
    blocked = any(not x["passed"] for x in g)
    status = "blocked" if blocked else ("ready" if overall >= threshold else "review" if overall >= 55 else "need")
    findings.sort(key=lambda f: -f["points_lost"])
    coach = {
        "blocked": "This can't go to Finance yet. The required items above fail the request regardless of score.",
        "ready": "This holds together. Close any remaining findings, then hand off.",
        "review": "The structure is there. The findings below are what separates this from approval-ready.",
        "need": "This reads as a summary, not a case Finance can approve. Start with the largest finding in each section.",
    }[status]
    return {
        "overall": overall, "status": status, "threshold": threshold,
        "categories": cats, "gates": g, "blocked": blocked,
        "findings": findings, "strong": strong[:6] or ["You've made a start"],
        "missing": [f["title"] for f in findings[:8]],
        "cre": cre_status(req), "coach": coach, "engine": "rules",
    }
