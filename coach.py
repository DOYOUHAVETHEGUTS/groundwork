"""Guided improvement loop.

    score -> 10 questions -> merge answers -> re-score -> show largest edits
          -> (below threshold) 10 NEW questions -> re-score
          -> (still below) escalate to the requester's finance partner

Questions ask for things a business unit actually knows (counts, dates, quotes,
hours, people, what they considered). Groundwork does the finance math itself:
given a one-time cost, annual benefit, recurring cost and useful life it derives
NPV @ 20%, MIRR and payback, clearly labeled for Finance to confirm.

Every step works without an API key (question bank + rules merge); with a model
configured, questions are tailored and answers are written into the draft in
proper prose, with the rules path as the fallback.
"""
import json
import re
import time

import agents
import config
import llm
import rubric

MAX_ROUNDS = 2
PER_ROUND = 10

FIELD_LABELS = dict(rubric.SECTIONS + rubric.EXTRA_FIELDS + rubric.FINANCIAL_FIELDS)
EDITABLE = [k for k, _ in rubric.EXTRA_FIELDS + rubric.SECTIONS + rubric.FINANCIAL_FIELDS]
FIELD_CATEGORY = {
    "background": "Current Situation",
    "proposal": "Proposal", "timing": "Proposal",
    "justification": "Justification", "operational_impact": "Justification",
    "affected_groups": "Justification",
    "risks": "Risks",
    "alternatives": "Alternatives",
    **{k: "Financial Case" for k, _ in rubric.FINANCIAL_FIELDS},
}
WEIGHTS = {n: w for n, w, _ in rubric.CATEGORIES}
CATEGORY_NAMES = [n for n, _, _ in rubric.CATEGORIES]

SHORT_FIELDS = {"affected_groups", "timing", "capex", "opex_annual", "benefit_annual",
                "npv", "mirr", "payback_months"}
BULLET_FIELDS = {"risks", "alternatives"}
LABELED_FIELDS = {"assumptions"}
CALC_MARK = "Groundwork calc"


class CoachError(Exception):
    pass


# --------------------------------------------------------------------------- bank
def _q(id, cat, field, q, why, ex, lead="", prio=0):
    return {"id": id, "category": cat, "field": field, "question": q, "why": why,
            "example": ex, "lead": lead, "prio": prio}


BANK = [
    # Current Situation
    _q("cs_age", "Current Situation", "background",
       "How old is what you have today, and how hard has it been used? Give the year or age plus miles, hours, cycles or volume.",
       "Numbers make the current situation credible instead of anecdotal.",
       "2011 model, 96,000 miles, used every shift"),
    _q("cs_failures", "Current Situation", "background",
       "How many times has it failed or caused a problem in the last 12 months? A count is ideal.",
       "Failure frequency is the clearest evidence of the cost of doing nothing.",
       "5 breakdowns in 12 months, 11 days out of service"),
    _q("cs_downtime", "Current Situation", "background",
       "When it's down, what does the team do instead, and how much extra time does that take?",
       "Workarounds show the hidden cost Finance can't see.",
       "We borrow equipment from another team; adds ~30 min per job"),
    _q("cs_spend", "Current Situation", "background",
       "What did you spend in the last 12 months keeping it running — repairs, parts, rentals, workarounds?",
       "Current spend is the baseline your savings are measured against.",
       "$3,100 in repairs plus a 2-week rental"),
    _q("cs_volume", "Current Situation", "background",
       "How much work depends on it — trips, jobs, shipments or pounds per day or week?",
       "Volume shows how many operations the problem touches.",
       "About 40 runs a week across 2 shifts"),
    # Proposal
    _q("pr_spec", "Proposal", "proposal",
       "Exactly what are you asking to buy or change — make/model, quantity, and what's included?",
       "A specific scope is easier to approve and to price.",
       "Two handheld scanners with chargers and 3-year support"),
    _q("pr_vendor", "Proposal", "proposal",
       "Who is the vendor or contract vehicle, and do you have a dated quote?",
       "A named source and dated quote make the cost verifiable.",
       "Corporate fleet contract, quote dated 2/10"),
    _q("pr_date", "Proposal", "timing",
       "When does this need to be in service, and what's driving that date?",
       "Approvers need to know why now and what slips if it waits.",
       "By Oct 1 — ahead of peak season"),
    _q("pr_scope", "Proposal", "proposal",
       "What is explicitly out of scope for this request?",
       "Stating what's excluded prevents scope creep questions later.",
       "Installation labor and disposal handled separately"),
    _q("pr_rollout", "Proposal", "proposal",
       "Who will receive, set up and support it, and how long until it's usable?",
       "An owner and timeline show the plan is real.",
       "Vendor delivers in 6 weeks; our lead tech sets it up in a day"),
    # Financial Case — inputs first; Groundwork computes NPV/MIRR/payback from them
    _q("fin_capex", "Financial Case", "capex",
       "What's the total one-time cost, including tax, delivery and setup? Lead with the total.",
       "The one-time cost anchors every financial metric.",
       "$18,500 all-in per the vendor quote"),
    _q("fin_benefit", "Financial Case", "benefit_annual",
       "What does this save or earn per year? Lead with the yearly dollar total, then the breakdown.",
       "Annual benefit is what turns a price into an investment case.",
       "$9,000/yr — $3,000 repairs avoided + $6,000 in staff time"),
    _q("fin_opex", "Financial Case", "opex_annual",
       "What will it cost each year to run — maintenance, fuel, licenses, insurance, support?",
       "Recurring cost is netted against the benefit.",
       "$1,200/yr maintenance and licenses"),
    _q("fin_life", "Financial Case", "assumptions",
       "How many years will it be in service before it needs replacing?",
       "Useful life sets the horizon for NPV and payback.",
       "6 years", lead="Useful life"),
    _q("fin_benefit_alt", "Financial Case", "benefit_annual",
       "If this were approved tomorrow, what would you stop paying for each year? Lead with the dollar total.",
       "Avoided spend is a benefit Finance can verify.",
       "$5,000/yr in rentals and overtime"),
    _q("fin_residual", "Financial Case", "assumptions",
       "Is there any trade-in, salvage or resale value for what's being replaced?",
       "Recovered value offsets the one-time cost.",
       "About $1,000 trade-in", lead="Salvage / trade-in"),
    _q("fin_budget", "Financial Case", "assumptions",
       "Is this in the current capital plan? Which cost center or budget line funds it?",
       "Funding source tells Finance whether this is planned or new spend.",
       "In the 2026 capital plan, cost center 1234", lead="Funding"),
    _q("fin_npv", "Financial Case", "npv",
       "If Finance has already run an NPV at 20% for this, what was it? (Skip if not — Groundwork calculates it.)",
       "An existing Finance NPV overrides the Groundwork estimate.",
       "$12,400 per Finance model", prio=-1),
    _q("fin_payback", "Financial Case", "payback_months",
       "Has anyone already estimated the payback period? (Skip if not — Groundwork calculates it.)",
       "An agreed payback figure avoids rework in review.",
       "About 30 months", prio=-1),
    # Justification
    _q("ju_outcome", "Justification", "justification",
       "What business result improves if this is approved — cost, safety, revenue, capacity or on-time performance — and by roughly how much?",
       "Justification ties the spend to an outcome, not just a condition.",
       "Removes delays that hold up ~3 turns a week"),
    _q("ju_hours", "Justification", "operational_impact",
       "How many labor hours a week does this save or free up, and across how many people?",
       "Hours and headcount make the operational impact concrete.",
       "~5 hours a week across 3 agents"),
    _q("ju_people", "Justification", "affected_groups",
       "Which teams, stations or shifts are affected, and about how many people?",
       "Approvers weigh who benefits and who is exposed.",
       "DFW cargo ops, 12 agents across 2 shifts"),
    _q("ju_safety", "Justification", "justification",
       "Is there a safety, compliance or audit concern with the current setup? Any incidents?",
       "Safety and compliance exposure can outweigh the dollars.",
       "One reportable incident last year tied to the old unit"),
    _q("ju_service", "Justification", "operational_impact",
       "Does the current problem affect customers or service — turn times, missed connections, claims?",
       "Customer impact connects the request to revenue.",
       "Two missed connections last quarter traced to it"),
    # Risks
    _q("rk_top", "Risks", "risks",
       "What's the biggest thing that could go wrong with this purchase or rollout, and what will you do to prevent it?",
       "Each risk needs a paired mitigation to score well.",
       "Vendor delay — we will lock the delivery date in the contract", lead="Delivery / rollout"),
    _q("rk_delay", "Risks", "risks",
       "If delivery or setup runs late, what's the fallback plan?",
       "A fallback shows the operation stays covered.",
       "We will rent a unit month-to-month until it arrives", lead="Late delivery"),
    _q("rk_notdone", "Risks", "risks",
       "What happens if this is NOT approved — what breaks, and when?",
       "The risk of inaction is often the strongest argument.",
       "Unit likely fails during peak; we would pay rentals and overtime", lead="If not approved"),
    _q("rk_support", "Risks", "risks",
       "What warranty, SLA or support contract protects this investment?",
       "Contractual protection is a mitigation approvers look for.",
       "3-year warranty and a vendor SLA", lead="Warranty / support"),
    # Alternatives
    _q("al_repair", "Alternatives", "alternatives",
       "Did you consider repairing or refurbishing what you have? Why isn't that the right answer?",
       "Approvers always ask why you can't fix the current one.",
       "Repair quoted at $4,000 but doesn't fix the root problem", lead="Repair / refurbish"),
    _q("al_other", "Alternatives", "alternatives",
       "What other product, vendor or approach did you look at, and why wasn't it chosen?",
       "Showing the options you rejected proves the choice was deliberate.",
       "Larger model considered but costs more and won't fit", lead="Other option"),
    _q("al_lease", "Alternatives", "alternatives",
       "Did you consider leasing, renting or borrowing from another station? Why not?",
       "Lease-vs-buy is a standard Finance question.",
       "Leasing costs more over the useful life", lead="Lease / rent / share"),
    _q("al_nothing", "Alternatives", "alternatives",
       "What's the case against simply doing nothing for another year?",
       "The do-nothing option must be explicitly rejected.",
       "Repairs and downtime already cost more than the payment", lead="Do nothing"),
]
BANK_BY_ID = {q["id"]: q for q in BANK}


def _public(q):
    return {k: q[k] for k in ("id", "category", "field", "question", "why", "example")} | {
        "field_label": FIELD_LABELS.get(q["field"], q["field"])}


# --------------------------------------------------------------------------- answers
NON_ANSWER = re.compile(
    r"^\s*(n/?a|na|none|no|nope|idk|unknown|tbd|skip|pass|not sure|unsure|"
    r"don'?t know|no idea|nothing|-+|\?+)\s*[.!]?\s*$", re.I)
HEDGE = re.compile(
    r"not sure|don'?t know|no idea|unsure|\btbd\b|to be determined|hard to say|no clue|"
    r"need to (check|confirm|ask)|will (need to )?(check|confirm)|ask finance|"
    r"finance would (know|have)|not calculated|haven'?t (run|calculated|done)", re.I)


def is_non_answer(a: str) -> bool:
    a = (a or "").strip()
    if len(a) < 2 or NON_ANSWER.match(a):
        return True
    return bool(HEDGE.search(a)) and not re.search(r"\d", a) and len(a.split()) < 20


def _merge_rules(cur: str, answer: str, field: str, lead: str = "") -> str:
    cur, ans = (cur or "").strip(), answer.strip()
    if field in BULLET_FIELDS:
        line = f"- {lead}: {ans}" if lead else f"- {ans}"
        return f"{cur}\n{line}" if cur else line
    if field in LABELED_FIELDS:
        ans = ans.rstrip(" .;")
        piece = f"{lead}: {ans}" if lead else ans
        return f"{cur}; {piece}" if cur else piece
    if field in SHORT_FIELDS:
        return f"{cur} · {ans}" if cur else ans
    if ans[-1:] not in ".!?":
        ans += "."
    if cur and cur[-1:] not in ".!?":
        cur += "."
    return f"{cur} {ans}" if cur else ans


# --------------------------------------------------------------------------- finance math
MONEY = re.compile(r"\$\s?(\d[\d,]*(?:\.\d+)?)\s*(k|mm|m|million|thousand)?\b", re.I)


def parse_money(t):
    m = MONEY.search(str(t or ""))
    if not m:
        return None
    v = float(m.group(1).replace(",", ""))
    suf = (m.group(2) or "").lower()
    if suf in ("k", "thousand"):
        v *= 1e3
    elif suf in ("m", "mm", "million"):
        v *= 1e6
    return v


def _life(req):
    txt = str(req.get("assumptions") or "")
    m = (re.search(r"useful life[^0-9]{0,25}(\d{1,2})", txt, re.I)
         or re.search(r"\b(\d{1,2})\s*-?\s*(?:years?|yrs?)\b", txt, re.I))
    return (max(1, min(int(m.group(1)), 15)), True) if m else (5, False)


def _is_calc(v):
    return not str(v or "").strip() or CALC_MARK in str(v)


def apply_finance_calc(req: dict) -> dict:
    """Derive NPV @20%, 5-yr-style MIRR and payback from stated inputs. Only fills
    fields that are empty or were previously Groundwork-calculated."""
    capex, benefit = parse_money(req.get("capex")), parse_money(req.get("benefit_annual"))
    if not capex or not benefit:
        return {}
    opex_v = parse_money(req.get("opex_annual"))
    opex = opex_v or 0.0
    n, life_stated = _life(req)
    r, net = 0.20, benefit - opex
    npv = -capex + sum(net / (1 + r) ** t for t in range(1, n + 1))
    fv = sum(net * (1 + r) ** (n - t) for t in range(1, n + 1))
    mirr = (fv / capex) ** (1 / n) - 1 if fv > 0 else None
    payback = capex / (net / 12) if net > 0 else None
    out = {"npv": npv, "mirr": mirr, "payback_months": payback, "years": n,
           "net_annual": net, "capex": capex, "benefit": benefit, "opex": opex}

    tag = f"({CALC_MARK} — confirm with Finance)"
    if _is_calc(req.get("npv")):
        req["npv"] = f"{'-' if npv < 0 else ''}${abs(npv):,.0f} {tag}"
    if _is_calc(req.get("mirr")):
        req["mirr"] = f"{mirr * 100:.1f}% over {n} yrs {tag}" if mirr is not None else f"n/a — no positive return {tag}"
    if _is_calc(req.get("payback_months")):
        req["payback_months"] = (f"{payback:.0f} {tag}" if payback and payback <= n * 12
                                 else f"Not reached within {n}-year life {tag}")
    line = (f"{CALC_MARK}ulation: {n}-year life{'' if life_stated else ' (assumed — not stated)'}, "
            f"20% discount and reinvestment rate, net annual benefit ${net:,.0f} "
            f"(benefit ${benefit:,.0f} less recurring ${opex:,.0f}"
            f"{'' if opex_v else ', recurring cost not stated so assumed $0'}); confirm with Finance")
    a = str(req.get("assumptions") or "")
    a = re.sub(rf";?\s*{CALC_MARK}ulation:[^;]*(;|$)", "", a).strip(" ;")
    req["assumptions"] = f"{a}; {line}" if a else line
    return out


# --------------------------------------------------------------------------- selection
def _stakes(qual):
    cats = {c["name"]: c["score"] for c in (qual or {}).get("categories", [])}
    return {n: WEIGHTS[n] * (100 - cats.get(n, 0)) / 100 for n in CATEGORY_NAMES}


def allocate(qual, n=PER_ROUND, capacity=None):
    """Largest-remainder allocation of n questions by points at stake, capped by capacity."""
    stake = _stakes(qual)
    cap = capacity or {c: n for c in CATEGORY_NAMES}
    total = sum(s for c, s in stake.items() if cap.get(c)) or 1
    raw = {c: (stake[c] / total * n if cap.get(c) else 0) for c in CATEGORY_NAMES}
    alloc = {c: min(int(raw[c]), cap.get(c, 0)) for c in CATEGORY_NAMES}
    order = sorted(CATEGORY_NAMES, key=lambda c: (-(raw[c] - int(raw[c])), -stake[c]))
    while sum(alloc.values()) < n:
        grew = False
        for c in order + sorted(CATEGORY_NAMES, key=lambda c: -stake[c]):
            if sum(alloc.values()) >= n:
                break
            if alloc[c] < cap.get(c, 0):
                alloc[c] += 1
                grew = True
        if not grew:
            break
    return {c: v for c, v in alloc.items() if v}


def _candidates(req, asked_ids):
    capex_known = bool(parse_money(req.get("capex")))
    out = {c: [] for c in CATEGORY_NAMES}
    for q in BANK:
        if q["id"] in asked_ids:
            continue
        f = q["field"]
        filled = bool(str(req.get(f) or "").strip())
        if q["id"] == "fin_capex" and capex_known:
            continue
        if f in ("npv", "payback_months", "opex_annual", "benefit_annual") and filled and not _is_calc(req.get(f)):
            continue
        rank = (q["prio"], 0 if not filled or f in BULLET_FIELDS | LABELED_FIELDS else -1)
        out[q["category"]].append((rank, q))
    for c in out:
        out[c] = [q for _, q in sorted(out[c], key=lambda x: (-x[0][0], -x[0][1]))]
    return out


def _rules_questions(req, qual, prior_ids, n=PER_ROUND):
    cands = _candidates(req, prior_ids)
    alloc = allocate(qual, n, {c: len(v) for c, v in cands.items()})
    picked = []
    for c in CATEGORY_NAMES:
        picked += cands[c][:alloc.get(c, 0)]
    return picked


def _norm(t):
    return set(re.findall(r"[a-z]{4,}", (t or "").lower()))


def _too_similar(t, prior_texts):
    a = _norm(t)
    return any(a and (len(a & b) / max(1, len(a | b))) > 0.55 for b in map(_norm, prior_texts))


QUESTION_SYSTEM = f"""You coach an airline business-unit employee (not a finance
professional) to strengthen a capital request before it reaches Finance.

{agents.STANDARD}

Write questions this person can answer from their own operational knowledge:
counts, dates, ages, quotes, hours, headcount, incidents, options they considered.

Rules:
- Follow the category allocation you are given; it targets the points at stake.
- Never ask them to calculate NPV, MIRR or payback — Groundwork calculates those.
  Ask for the inputs instead: total one-time cost, annual recurring cost, annual
  savings or revenue as a dollar total, and useful life in years.
- One fact per question, plain language, under 30 words, no finance jargon.
- Never repeat or rephrase a previously asked question, and do not ask for
  anything already clearly stated in the draft.
- "field" is where the answer belongs: one of {", ".join(EDITABLE)}.
- "category" is one of: {", ".join(CATEGORY_NAMES)}.
- "why": one short sentence on how the answer strengthens the request.
- "example": a short sample answer showing the format (not a claim about this request).

Return JSON only: {{"questions":[{{"category":"","field":"","question":"","why":"","example":""}}]}}"""


def _model_questions(req, qual, prior, round_no, n=PER_ROUND):
    alloc = allocate(qual, n)
    user = json.dumps({
        "questions_needed": n,
        "category_allocation": alloc,
        "category_scores": {c["name"]: c["score"] for c in qual.get("categories", [])},
        "open_items": qual.get("missing", []),
        "draft": {k: req.get(k, "") for k in EDITABLE},
        "previously_asked": [q["question"] for q in prior],
        "round": round_no,
    }, indent=2)
    data = llm.chat_json(QUESTION_SYSTEM, user)
    seen = [q["question"] for q in prior]
    out = []
    for i, q in enumerate(data.get("questions") or []):
        text = str(q.get("question", "")).strip()
        field, cat = str(q.get("field", "")).strip(), str(q.get("category", "")).strip()
        if not text or field not in FIELD_LABELS or _too_similar(text, seen):
            continue
        if cat not in WEIGHTS:
            cat = FIELD_CATEGORY.get(field, "Justification")
        seen.append(text)
        out.append({"id": f"m{round_no}_{len(out) + 1}", "category": cat, "field": field,
                    "question": text[:300], "why": str(q.get("why", ""))[:200],
                    "example": str(q.get("example", ""))[:160], "lead": "", "prio": 0})
        if len(out) >= n:
            break
    return out


def generate_questions(req, qual, prior, round_no):
    prior_ids = {q["id"] for q in prior}
    engine, err = "rules", ""
    qs = []
    if agents.use_model(req):
        try:
            qs = _model_questions(req, qual, prior, round_no)
            engine = "model:" + str(config.load().get("model"))
        except (llm.LLMError, ValueError, KeyError, TypeError) as e:
            engine, err = "rules (model unavailable)", str(e)[:300]
    if len(qs) < PER_ROUND:   # top up (or fully supply) from the bank, never repeating
        texts = [q["question"] for q in prior + qs]
        taken = prior_ids | {q["id"] for q in qs}
        need = PER_ROUND - len(qs)
        # The first `need` follow the points-at-stake allocation; the rest are spares
        # used only if a primary pick is too close to a model-written question.
        primary = _rules_questions(req, qual, taken, need)
        spares = [q for q in _rules_questions(req, qual, taken, PER_ROUND * 3)
                  if q["id"] not in {p["id"] for p in primary}]
        for q in primary + spares:
            if len(qs) >= PER_ROUND:
                break
            if not _too_similar(q["question"], texts):
                qs.append(dict(q))
                texts.append(q["question"])
        qs.sort(key=lambda q: CATEGORY_NAMES.index(q["category"])
                if q["category"] in CATEGORY_NAMES else 99)   # five-point order for display
        if engine.startswith("model") and len(qs) and any(q["id"] in BANK_BY_ID for q in qs):
            engine += " + question bank"
    return qs[:PER_ROUND], engine, err


# --------------------------------------------------------------------------- merge
MERGE_SYSTEM = """You update a capital request draft with the requester's answers to
guided questions. You are a drafting assistant, not an approver.

Rules:
- Integrate each answer into the field it targets (or a clearly better-fitting field).
- Keep every existing fact. You may tighten wording but never drop numbers, names or dates.
- Use ONLY facts already in the draft or in the answers. Never invent numbers, vendors,
  dates, savings or headcount.
- Risks: pair each risk with its mitigation when the answer gives one.
- Alternatives: state why each option was not selected when the answer gives a reason.
- capex, opex_annual and benefit_annual are short values that lead with the dollar
  figure, e.g. "$11,400 per year — repairs avoided plus technician time".
- Leave npv, mirr and payback_months unchanged unless an answer states them directly.
- Plain business English, third person, no marketing language.

Return JSON only: {"updated": {"<field>": "<complete new text for that field>"}}
Include only fields you changed."""


def _merge(req, usable):
    """Returns (updates, engine, error, sources{field:[qid]})."""
    sources = {}
    for q, _ in usable:
        sources.setdefault(q["field"], []).append(q["id"])
    work = {k: str(req.get(k) or "") for k in EDITABLE}

    def rules_all(items):
        for q, a in items:
            work[q["field"]] = _merge_rules(work[q["field"]], a, q["field"], q.get("lead", ""))

    if agents.use_model(req) and usable:
        try:
            user = json.dumps({
                "draft": work,
                "answers": [{"field": q["field"], "question": q["question"], "answer": a}
                            for q, a in usable],
            }, indent=2)
            data = llm.chat_json(MERGE_SYSTEM, user)
            for k, v in (data.get("updated") or {}).items():
                if k in work and isinstance(v, (str, int, float)) and str(v).strip():
                    work[k] = str(v).strip()
            # Guardrail: no numeric answer may be silently dropped by the model.
            blob = " ".join(work.values())
            missed = [(q, a) for q, a in usable
                      if (nums := re.findall(r"\d[\d,]*", a)) and not any(x in blob for x in nums[:3])]
            rules_all(missed)
            return work, "model:" + str(config.load().get("model")), "", sources
        except (llm.LLMError, ValueError, KeyError, TypeError) as e:
            work = {k: str(req.get(k) or "") for k in EDITABLE}
            rules_all(usable)
            return work, "rules (model unavailable)", str(e)[:300], sources
    rules_all(usable)
    return work, "rules", "", sources


def compute_edits(before, after, q_before, q_after, sources=None):
    cb = {c["name"]: c["score"] for c in q_before.get("categories", [])}
    ca = {c["name"]: c["score"] for c in q_after.get("categories", [])}
    edits = []
    for f in EDITABLE:
        b, a = str(before.get(f) or "").strip(), str(after.get(f) or "").strip()
        if a == b:
            continue
        if b and a.startswith(b):
            kind, added = "extended", a[len(b):].strip(" ·;.\n")
        else:
            kind, added = ("added" if not b else "rewritten"), a
        edits.append({"field": f, "label": FIELD_LABELS.get(f, f),
                      "category": FIELD_CATEGORY.get(f), "kind": kind,
                      "before": b, "after": a, "added": added,
                      "words_added": len(added.split()),
                      "calculated": CALC_MARK in a and CALC_MARK not in b,
                      "from_questions": (sources or {}).get(f, [])})
    # Attribution: each category's real gain is split by each field's marginal
    # contribution — re-score with just that field reverted and see what it was
    # worth. (Rules rubric as the yardstick; it's deterministic and free.) Only
    # if no single field is decisive on its own do we fall back to words added.
    def rcat(d):
        return {c["name"]: c["score"] for c in rubric.score_request(d)["categories"]}
    after_rules = rcat(after)
    by_cat = {}
    for e in edits:
        by_cat.setdefault(e["category"], []).append(e)
    for cat, es in by_cat.items():
        if cat not in WEIGHTS:
            for e in es:
                e["points"] = 0.0
            continue
        delta = (ca.get(cat, 0) - cb.get(cat, 0)) * WEIGHTS[cat] / 100
        marg = []
        for e in es:
            reverted = dict(after)
            reverted[e["field"]] = e["before"]
            marg.append(max(0, after_rules[cat] - rcat(reverted)[cat]))
        share = marg if sum(marg) > 0 else [max(1, e["words_added"]) for e in es]
        for e, m in zip(es, share):
            e["points"] = round(delta * m / sum(share), 1)
            e["category_before"], e["category_after"] = cb.get(cat), ca.get(cat)
    # ties: the requester's own answers before derived numbers, then by substance
    edits.sort(key=lambda e: (-e["points"], e["calculated"] or e["field"] == "assumptions", -e["words_added"]))
    return edits


# --------------------------------------------------------------------------- rounds
def _threshold():
    return int(config.load().get("min_score_to_advance", 75))


def _cats(q):
    return [{"name": c["name"], "score": c["score"], "weight": c["weight"]} for c in q.get("categories", [])]


def start_round(req: dict) -> dict:
    co = req.setdefault("coaching", {"rounds": [], "status": "not_started", "escalation": None})
    rounds = co["rounds"]
    if rounds and not rounds[-1].get("answered"):
        return req                                     # already waiting on answers
    if len(rounds) >= MAX_ROUNDS:
        raise CoachError("Both guided rounds are complete for this request.")
    qual = req.get("qualification") or agents.qualify(req)
    if not req.get("qualification"):
        req["qualification"], req["score"], req["status"] = qual, qual["overall"], qual["status"]
    co.setdefault("original_score", qual["overall"])
    prior = [q for r in rounds for q in r["questions"]]
    n = len(rounds) + 1
    qs, engine, err = generate_questions(req, qual, prior, n)
    if not qs:
        raise CoachError("No new questions are left to ask — edit the draft directly.")
    rounds.append({"round": n, "created": time.time(), "engine": engine, "engine_error": err,
                   "questions": [_public(q) | {"lead": q.get("lead", "")} for q in qs],
                   "answered": False, "score_before": qual["overall"],
                   "categories_before": _cats(qual)})
    co["status"] = f"round{n}_questions"
    co["threshold"] = _threshold()
    return req


def submit_answers(req: dict, answers: dict, notifier=None) -> dict:
    co = req.get("coaching") or {}
    rounds = co.get("rounds") or []
    if not rounds or rounds[-1].get("answered"):
        raise CoachError("There is no open question round — generate questions first.")
    rnd = rounds[-1]
    answers = {k: str(v or "").strip()[:1500] for k, v in (answers or {}).items()}
    qa = [(q, answers.get(q["id"], "")) for q in rnd["questions"]]
    given = [(q, a) for q, a in qa if a]
    if not given:
        raise CoachError("Answer at least one question before re-scoring.")
    usable = [(q, a) for q, a in given if not is_non_answer(a)]

    before = {k: str(req.get(k) or "") for k in EDITABLE}
    q_before = req.get("qualification") or agents.qualify(req)
    merged, m_engine, m_err, sources = _merge(req, usable)
    req.update(merged)
    calc = apply_finance_calc(req)
    for k in ("npv", "mirr", "payback_months", "assumptions"):
        if req.get(k) != before.get(k) and CALC_MARK in str(req.get(k)):
            sources.setdefault(k, [])
    q_after = agents.qualify(req)
    req["qualification"], req["score"], req["status"] = q_after, q_after["overall"], q_after["status"]

    threshold = _threshold()
    passed = q_after["overall"] >= threshold
    rnd.update({
        "answered": True, "submitted": time.time(), "answers": answers,
        "answered_count": len(given), "usable_count": len(usable),
        "not_usable": [q["id"] for q, a in given if is_non_answer(a)],
        "skipped": [q["id"] for q, a in qa if not a],
        "score_before": q_before["overall"], "categories_before": _cats(q_before),
        "score_after": q_after["overall"], "categories_after": _cats(q_after),
        "edits": compute_edits(before, req, q_before, q_after, sources),
        "merge_engine": m_engine, "merge_error": m_err,
        "finance_calc": {k: (round(v, 4) if isinstance(v, float) else v) for k, v in calc.items()},
        "passed": passed, "threshold": threshold,
    })
    if passed:
        co["status"] = "passed"
    elif rnd["round"] < MAX_ROUNDS:
        co["status"] = f"round{rnd['round']}_failed"
    else:
        co["status"] = "escalated"
        req["status"] = "escalated"
        if notifier:
            co["escalation"] = notifier(req)
    return req


# --------------------------------------------------------------------------- demo
DEMO_TEXT = (
    "Facilities in BOS runs a 2007 Chrysler Town and Country with 140,761 miles for tool and "
    "personnel transport. The fuel gauge needle falls off so we cannot tell fuel level, there is "
    "exterior rust and the seating and carpeting are unserviceable. It was never designed as a "
    "maintenance vehicle. We have a fleet contract quote for a crew cab pickup at $30,348 total. "
    "A smaller truck maneuvers better in ramp congestion. Alternatives are a Ford Ranger or Chevy Colorado.")

DEMO_SCENARIOS = {
    "pass": {"label": "Passes after round 1",
             "blurb": "The requester answers all 10 questions with specifics and clears the bar in one round."},
    "second": {"label": "Needs the second chance",
               "blurb": "Round 1 fills in the story but dodges the money questions. Round 2 asks new ones and it passes."},
    "escalate": {"label": "Escalates to Finance",
                 "blurb": "Vague answers in both rounds leave it below the bar, so the finance partner is alerted."},
}

DEMO_STRONG = {
    "cs_age": "The van is a 2007 Chrysler Town & Country with 140,761 miles; it runs about 6 days a week across all three shifts.",
    "cs_failures": "It has broken down 7 times in the last 12 months and was out of service 19 days in total.",
    "cs_downtime": "When it's down we borrow a tug from ramp or walk tools out, which adds about 45 minutes per job.",
    "cs_spend": "We spent $4,200 on repairs in the last 12 months, including a 3-week rental.",
    "cs_volume": "It makes about 25 tool and parts runs per week between the shop and the ramp.",
    "pr_spec": "One compact crew-cab pickup (Ford Ranger class) with a locking bed cap and toolbox, replacing van #22991.",
    "pr_vendor": "Purchased through the corporate fleet contract; the quote is dated 3/14/2026 at $30,348 all-in.",
    "pr_date": "In service by November 1, 2026 — ahead of winter ops and holiday peak, when a breakdown hurts most.",
    "pr_scope": "Out of scope: upfitting beyond the toolbox, and disposal of the old van, which Fleet handles separately.",
    "pr_rollout": "Fleet delivers in about 8 weeks; our lead mechanic handles setup and ramp credentialing in 2 days.",
    "fin_capex": "$30,348 all-in per the fleet contract quote, including tax and delivery.",
    "fin_benefit": "$11,400 per year — $4,200 in repairs and rentals avoided plus about $7,200 in recovered technician time (6 hrs/week at a $23/hr loaded rate).",
    "fin_opex": "$1,800 per year for maintenance and insurance under the fleet program.",
    "fin_life": "7 years.",
    "fin_benefit_alt": "$11,400 per year — we'd stop paying about $4,200 in repairs and rentals and recover roughly $7,200 in technician time.",
    "fin_residual": "About $1,500 in salvage value for the old van.",
    "fin_budget": "It's in the 2026 Cargo Facilities capital plan under cost center 4410.",
    "fin_npv": "",
    "fin_payback": "",
    "ju_outcome": "It removes tool-run delays that currently hold up about 3 aircraft turns a week and ends the fuel run-outs on the airfield.",
    "ju_hours": "About 6 hours a week across 4 mechanics — time now lost to breakdowns and workarounds.",
    "ju_people": "BOS Cargo Facilities — 4 mechanics and 1 supervisor across 3 shifts.",
    "ju_safety": "Yes — the broken fuel gauge caused 2 run-outs on the airfield this year, which is a ramp safety issue.",
    "ju_service": "Two delayed ULD builds last quarter were traced to the van being down.",
    "rk_top": "Delivery could slip past November; we will mitigate by ordering this month and fixing the delivery date in the fleet contract.",
    "rk_delay": "If it arrives late, we will keep a short-term rental under contract rather than run the old van on the AOA.",
    "rk_notdone": "The van will keep failing, likely during peak, and we will be paying for rentals and overtime while it's down.",
    "rk_support": "A 3-year/36,000-mile manufacturer warranty plus the fleet program's maintenance SLA.",
    "al_repair": "Repair was considered but rejected because it would cost about $6,000 and still leave a 19-year-old vehicle never built for maintenance work.",
    "al_other": "A full-size pickup was considered, but it's more expensive and harder to maneuver in ramp congestion, so the compact truck was chosen.",
    "al_lease": "Leasing was considered; however, the fleet purchase is cheaper over 7 years and we use it daily, so renting doesn't pay off.",
    "al_nothing": "Doing nothing was rejected because repairs, rentals and lost time already cost about $11,400 a year and the safety issue remains.",
}

DEMO_WEAK = {
    "cs_age": "It's pretty old and has a lot of miles on it at this point.",
    "cs_failures": "It breaks down fairly often, not sure exactly how many times.",
    "cs_downtime": "We make do by borrowing equipment from other teams when it's down, which slows everyone down.",
    "cs_spend": "Not sure, Finance would have that.",
    "cs_volume": "The team uses it every day for most jobs.",
    "pr_spec": "A newer truck of some kind.",
    "pr_vendor": "We'd go through whoever Fleet usually uses.",
    "pr_date": "As soon as possible.",
    "pr_scope": "Nothing specific.",
    "pr_rollout": "Fleet would handle it.",
    "fin_capex": "Whatever the quote says — I'd need to check.",
    "fin_benefit": "It should save money on repairs but I don't know how much.",
    "fin_opex": "Probably similar to today, I'd need to check.",
    "fin_life": "A while, not sure.",
    "fin_benefit_alt": "Hard to say.",
    "fin_residual": "No idea.",
    "fin_budget": "Not sure if it's in the plan.",
    "fin_npv": "",
    "fin_payback": "",
    "ju_outcome": "It would make the team more efficient.",
    "ju_hours": "Some time each week, hard to say.",
    "ju_people": "The facilities team.",
    "ju_safety": "Not that I know of.",
    "ju_service": "Maybe indirectly.",
    "rk_top": "Delivery could take a while.",
    "rk_delay": "We'd keep using the old van.",
    "rk_notdone": "The van will eventually stop working.",
    "rk_support": "Not sure what warranty comes with it.",
    "al_repair": "We could repair it.",
    "al_other": "We looked at a couple of other trucks.",
    "al_lease": "Didn't really look at that.",
    "al_nothing": "Not a good idea.",
}


def _pool(src, field):
    return [src[q["id"]] for q in BANK if q["field"] == field and src.get(q["id"])]


def demo_answers(req: dict) -> dict:
    """Scripted answers for the open round, so a demo can be clicked through."""
    rounds = (req.get("coaching") or {}).get("rounds") or []
    if not rounds or rounds[-1].get("answered"):
        raise CoachError("No open question round to fill.")
    rnd = rounds[-1]
    scenario = req.get("demo_scenario") or "pass"
    out, used = {}, {}
    for q in rnd["questions"]:
        weak_cat = (scenario == "escalate" or
                    (scenario == "second" and rnd["round"] == 1 and q["category"] == "Financial Case"))
        src = DEMO_WEAK if weak_cat else DEMO_STRONG
        if q["id"] in src:
            out[q["id"]] = src[q["id"]]
            continue
        pool = _pool(src, q["field"]) or _pool(src, "background")   # model-written question
        i = used.get(q["field"], 0)
        used[q["field"]] = i + 1
        out[q["id"]] = pool[i % len(pool)] if pool else ""
    return out
