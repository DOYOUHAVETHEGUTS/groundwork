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
import polish
import rubric

MAX_ROUNDS = 2
PER_ROUND = 10

FIELD_LABELS = dict(rubric.SECTIONS + [rubric.APPENDIX, ("selection_rationale", "Vendor selection")])
EDITABLE = [k for k, _ in rubric.SECTIONS] + ["selection_rationale", "appendix"]
FIELD_CATEGORY = {**{k: lbl for k, lbl in rubric.SECTIONS},
                  "selection_rationale": "Alternatives", "appendix": "Current Situation"}
WEIGHTS = rubric.WEIGHTS
CATEGORY_NAMES = [lbl for _, lbl in rubric.SECTIONS]
BULLET_FIELDS = {"alternatives", "cost", "appendix"}
SET_FIELDS = {"selection_rationale"}


class CoachError(Exception):
    pass


# Guided questions shown at the start of the speaking / writing step.
INTAKE_GUIDE = [
    "What is the problem today, in one or two sentences?",
    "How many do you have, how old are they, and how often do they fail or cause delays? Use numbers.",
    "Why does the problem happen? Where in the process does the work get stuck?",
    "What exactly are you asking for: how many, what item, and where does each one go?",
    "Who quoted it and for how much? Finance requires three vendor quotes.",
    "Which vendor would you pick, and why is it the best value overall, not just the cheapest?",
    "What other options did you consider (repair, lease, phase), and why not those?",
    "What gets better, in numbers, if this is approved?",
    "Is this facility or building work driven by Corporate Real Estate?",
]


# --------------------------------------------------------------------------- bank
def _q(id, cat, field, q, why, ex, prio=0):
    return {"id": id, "category": cat, "field": field, "question": q, "why": why, "example": ex, "prio": prio}


CS, PR, CO, JU, AL = CATEGORY_NAMES
BANK = [
    _q("cs_count", CS, "current_situation", "How many of these do you have today, and how many are actually available on a typical day?",
       "A count and an availability rate turn \"limited\" into something Finance can size.", "6 units; 2 typically down"),
    _q("cs_age", CS, "current_situation", "How old is what you have, and how hard is it used (miles, hours, cycles, shifts)?",
       "Age and usage show whether this is wear-out or misuse.", "2011 model, 96,000 miles, every shift"),
    _q("cs_failures", CS, "current_situation", "How many failures, breakdowns or delays did this cause in the last 12 months?",
       "Failure frequency is the clearest evidence of the cost of doing nothing.", "5 breakdowns, 11 days out of service"),
    _q("cs_mechanism", CS, "current_situation", "Walk through the process: where exactly does the work get stuck, and why does it happen there?",
       "Finance funds fixes to causes, not symptoms.", "Every shipment ties up a stand, so the next removal waits"),
    _q("cs_workaround", CS, "current_situation", "When it's unavailable, what does the team do instead, and how much time does that add each time?",
       "Workarounds are the hidden cost Finance can't see.", "Borrow from another team; ~45 minutes per job"),
    _q("cs_spend", CS, "current_situation", "What did keeping the current setup running cost in the last 12 months (repairs, rentals, overtime)?",
       "Current spend is the baseline the benefit is measured against.", "$4,200 in repairs plus a 3-week rental"),
    _q("ap_evidence", CS, "appendix", "What evidence can you attach: quote letters, photos, incident logs, utilization reports? List each with its date.",
       "Claims backed by exhibits survive review; unsupported claims don't.", "Quote letters dated 3/10–3/14; repair log FY25"),
    _q("pr_scope", PR, "proposal", "Exactly what are you buying: quantity, item or model class, and where each one goes?",
       "A defined scope is easier to approve and harder to inflate.", "One compact crew-cab pickup for the BOS shop"),
    _q("pr_single", PR, "proposal", "If Finance could approve only one thing in this request, what is it?",
       "Anything else belongs in its own five point; bundled asks get sent back.", "The replacement truck only"),
    _q("pr_date", PR, "proposal", "When must this be in service, and what lead time drives that date?",
       "Approvers need to know why now and what slips if it waits.", "By Nov 1; 6-week delivery"),
    _q("pr_constraints", PR, "proposal", "Are there sourcing rules, contract terms or install requirements that limit how this is bought or installed?",
       "Constraints change what Finance can approve.", "Must buy through the corporate fleet contract"),
    _q("co_items", CO, "cost", "List each cost line as quantity × unit cost (equipment, labor, freight, tax, install), separated by semicolons.",
       "Line items are what Finance checks and negotiates.", "Pickup 1 × $27,858; upfit 1 × $1,840; delivery 1 × $650"),
    _q("co_contingency", CO, "cost", "What contingency are you including (percent and dollars), and what is the all-in total?",
       "A stated contingency prevents a second request for overruns.", "5% contingency, $1,517; total $31,865"),
    _q("co_funding", CO, "cost", "How is this funded: capital plan line, cost center, credits or a new request?",
       "Finance has to know whether this is planned money.", "2026 capital plan, cost center 4410"),
    _q("ju_recommend", JU, "justification", "In one sentence, what do you recommend Finance approve, and at what amount?",
       "The committee needs the decision stated, not implied.", "We recommend Vendor B at $31,865"),
    _q("ju_benefit", JU, "justification", "What will be measurably better once this is done: delay days, hours, failures or dollars per year?",
       "A benefit in numbers can be weighed against the cost.", "Saves ~6 hours a week; ends ~19 down-days a year"),
    _q("ju_tie", JU, "justification", "Using your current numbers, what changes? For example: 19 days out of service becomes 2.",
       "Closing the loop on the baseline is what makes the case.", "7 breakdowns a year → under 1"),
    _q("ju_risk", JU, "justification", "What is the biggest risk in delivering this, and how will you manage it?",
       "Implementation risk is the first thing reviewers probe.", "Delivery slip; we will lock the date in the contract"),
    _q("ju_assume", JU, "justification", "What assumptions are your numbers built on (lead times, prices, volumes, useful life)?",
       "Stated assumptions let Finance test the numbers.", "7-year life; current repair rate continues"),
    _q("al_options", AL, "alternatives", "What other ways could you solve this (repair, lease, rent, phase, share), and why aren't they better?",
       "Real alternatives prove the choice was deliberate.", "Leasing costs more over 7 years because …"),
    _q("al_repair", AL, "alternatives", "Why isn't repairing or refurbishing what you have the right answer?",
       "\"Why can't you fix it?\" is always asked.", "Repair quoted at $6,000 and doesn't fix the root cause"),
    _q("al_compare", AL, "alternatives", "How do the options compare on total cost, lead time and what's included?",
       "A side-by-side shows why one option wins.", "A: $28,550, 14 wks, no upfit; B: $30,348, 6 wks"),
]
BANK_BY_ID = {q["id"]: q for q in BANK}


def _selection_question(req):
    vq = rubric.valid_quotes(req)
    names = ", ".join(q["vendor"] for q in vq[:-1]) + f" and {vq[-1]['vendor']}"
    return _q("sel_vendor", AL, "selection_rationale",
              f"You have quotes from {names}. Which one are you selecting, and why is it the best choice all-in: "
              "price, scope covered, lead time, warranty, support, compliance?",
              "The best quote isn't always the cheapest. Finance needs the reasoning.",
              "Vendor B: not the lowest price, but the only full-scope quote and 8 weeks faster")


def required_actions(req):
    out = []
    for g in rubric.gates(req):
        if not g["passed"]:
            out.append({"id": g["id"], "label": g["label"], "message": g["message"], "severity": g["severity"],
                        "action": "quotes" if g["id"] == "three_quotes" else "cre"})
    return out


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
    if field in SET_FIELDS:
        return f"{cur} {ans}".strip() if cur else ans
    if field in BULLET_FIELDS:
        parts = [p.strip() for p in (re.split(r";\s*|\n", ans) if field == "cost" else [ans]) if p.strip()]
        lines = "\n".join(f"- {p}" for p in parts)
        return f"{cur}\n{lines}" if cur else lines
    if ans[-1:] not in ".!?":
        ans += "."
    if cur and cur[-1:] not in ".!?":
        cur += "."
    return f"{cur} {ans}" if cur else ans


def _post_merge(req, answers_by_field):
    """Structured side effects of answers: selected vendor, contingency %."""
    sel = answers_by_field.get("selection_rationale")
    if sel:
        vq = rubric.valid_quotes(req)
        hit = min(((sel.lower().find(q["vendor"].lower()), q) for q in vq if q["vendor"].lower() in sel.lower()),
                  key=lambda x: x[0], default=(None, None))[1]
        if hit:
            for q in req.get("quotes") or []:
                q["selected"] = q is hit
    if not str(req.get("contingency_pct") or "").strip():
        m = re.search(r"(\d+(?:\.\d+)?)\s*%\s*contingency|contingency\D{0,20}?(\d+(?:\.\d+)?)\s*%", req.get("cost") or "", re.I)
        if m:
            req["contingency_pct"] = m.group(1) or m.group(2)


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
    out = {c: [] for c in CATEGORY_NAMES}
    for q in BANK:
        if q["id"] in asked_ids:
            continue
        filled = bool(str(req.get(q["field"]) or "").strip())
        rank = (q["prio"], 0 if not filled or q["field"] in BULLET_FIELDS else -1)
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


QUESTION_SYSTEM = f"""You coach a business-unit employee (not a finance professional) to
strengthen a five-point capital request before it reaches Finance.

{agents.STANDARD}

Write questions this person can answer from their own operational knowledge:
counts, dates, ages, quotes, hours, headcount, incidents, options they considered.

Rules:
- Follow the category allocation you are given; it targets the points at stake.
- Never ask about NPV, IRR or payback; Finance prepares those.
- One fact per question, plain language, under 30 words, no finance jargon.
- Never repeat or rephrase a previously asked question, and don't ask for anything
  already clearly stated in the draft.
- "field" is where the answer belongs: one of {", ".join(EDITABLE)}.
- "category" is one of: {", ".join(CATEGORY_NAMES)}.
- "why": one short sentence on how the answer strengthens the request.
- "example": a short sample answer showing the format.
{agents.STYLE}

Return JSON only: {{"questions":[{{"category":"","field":"","question":"","why":"","example":""}}]}}"""


def _model_questions(req, qual, prior, round_no, n=PER_ROUND):
    if n <= 0:
        return []
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
                    "example": str(q.get("example", ""))[:160], "prio": 0})
        if len(out) >= n:
            break
    return out


def generate_questions(req, qual, prior, round_no):
    prior_ids = {q["id"] for q in prior}
    engine, err, qs = "rules", "", []
    # The vendor-selection question is mandatory once three quotes exist and no reason is given.
    if len(rubric.valid_quotes(req)) >= 3 and rubric.words(req.get("selection_rationale")) < 10 \
            and "sel_vendor" not in prior_ids:
        qs.append(_selection_question(req))
    if agents.use_model(req):
        try:
            qs += _model_questions(req, qual, prior + qs, round_no, PER_ROUND - len(qs))
            engine = "model:" + str(config.load().get("model"))
        except (llm.LLMError, ValueError, KeyError, TypeError) as e:
            engine, err = "rules (model unavailable)", str(e)[:300]
    if len(qs) < PER_ROUND:
        texts = [q["question"] for q in prior + qs]
        taken = prior_ids | {q["id"] for q in qs}
        need = PER_ROUND - len(qs)
        primary = _rules_questions(req, qual, taken, need)
        spares = [q for q in _rules_questions(req, qual, taken, PER_ROUND * 3) if q["id"] not in {p["id"] for p in primary}]
        for q in primary + spares:
            if len(qs) >= PER_ROUND:
                break
            if not _too_similar(q["question"], texts):
                qs.append(dict(q))
                texts.append(q["question"])
        if engine.startswith("model") and any(q["id"] in BANK_BY_ID for q in qs):
            engine += " + question bank"
    qs = qs[:PER_ROUND]
    qs.sort(key=lambda q: CATEGORY_NAMES.index(q["category"]) if q["category"] in CATEGORY_NAMES else 99)
    return qs, engine, err

# --------------------------------------------------------------------------- merge
MERGE_SYSTEM = f"""You update a five-point capital request draft with the requester's answers to
guided questions. You are a drafting assistant, not an approver.

{agents.STANDARD}

Rules:
- Integrate each answer into the field it targets (or a clearly better-fitting field).
- Keep every existing fact. Tighten wording, but never drop numbers, names or dates.
- Use ONLY facts already in the draft or in the answers. Never invent anything.
- Cost: one line item per line, "- description: qty × $unit". Keep the stated total.
- Alternatives: each option with its cost and why it was not chosen.
- selection_rationale: which vendor and the all-in reasons, in two or three sentences.
{agents.STYLE}

Return JSON only: {{"updated": {{"<field>": "<complete new text for that field>"}}}}
Include only fields you changed."""


def _merge(req, usable):
    """Returns (updates, engine, error, sources{field:[qid]})."""
    sources = {}
    for q, _ in usable:
        sources.setdefault(q["field"], []).append(q["id"])
    work = {k: str(req.get(k) or "") for k in EDITABLE}

    def rules_all(items):
        for q, a in items:
            work[q["field"]] = _merge_rules(work[q["field"]], a, q["field"])

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
                      "calculated": False,
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
                   "questions": [_public(q) for q in qs], "required_actions": required_actions(req),
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
    _post_merge(req, {q["field"]: a for q, a in usable})
    polish.polish_request(req)
    q_after = agents.qualify(req)
    req["qualification"], req["score"], req["status"] = q_after, q_after["overall"], q_after["status"]

    threshold = _threshold()
    passed = q_after["overall"] >= threshold and not q_after.get("blocked")
    rnd.update({
        "answered": True, "submitted": time.time(), "answers": answers,
        "answered_count": len(given), "usable_count": len(usable),
        "not_usable": [q["id"] for q, a in given if is_non_answer(a)],
        "skipped": [q["id"] for q, a in qa if not a],
        "score_before": q_before["overall"], "categories_before": _cats(q_before),
        "score_after": q_after["overall"], "categories_after": _cats(q_after),
        "edits": compute_edits(before, req, q_before, q_after, sources),
        "merge_engine": m_engine, "merge_error": m_err,
        "blocked": q_after.get("blocked", False), "required_actions": required_actions(req),
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
    "maintenance vehicle. We want to replace it with a compact crew cab pickup. We have a fleet contract "
    "quote for a crew cab pickup at $30,348 total. A smaller truck maneuvers better in ramp congestion. "
    "Alternatives are a Ford Ranger or Chevy Colorado.")


def _items(*rows):
    return [{"desc": d, "qty": q, "unit": u} for d, q, u in rows]


DEMO_QUOTES = [
    {"vendor": "Fleet Partner A", "date": "2026-03-10", "lead_time": "14 weeks", "warranty": "3 yr / 36,000 mi",
     "notes": "Excludes bed cap and toolbox upfit.",
     "items": _items(("Compact crew-cab pickup", 1, 27900), ("Delivery and registration", 1, 650))},
    {"vendor": "Fleet Partner B", "date": "2026-03-14", "lead_time": "6 weeks",
     "warranty": "3 yr / 36,000 mi plus fleet maintenance SLA", "notes": "",
     "items": _items(("Compact crew-cab pickup", 1, 27858), ("Bed cap and toolbox upfit", 1, 1840),
                     ("Delivery and registration", 1, 650))},
    {"vendor": "Fleet Partner C", "date": "2026-03-12", "lead_time": "10 weeks", "warranty": "3 yr / 36,000 mi",
     "notes": "", "items": _items(("Compact crew-cab pickup", 1, 30400), ("Bed cap and toolbox upfit", 1, 1950),
                                  ("Delivery and registration", 1, 900))},
]

DEMO_SCENARIOS = {
    "pass": {"label": "Passes after round 1",
             "blurb": "Three itemized quotes are on file. The requester answers all 10 questions with specifics and clears the bar."},
    "second": {"label": "Needs the second chance",
               "blurb": "Round 1 fills in the story but dodges cost and benefit. Round 2 asks new questions and it passes."},
    "escalate": {"label": "Blocked, escalates to Finance",
                 "blurb": "Only one vendor quote and vague answers. The hard barrier holds through both rounds, so Finance is alerted."},
}


def demo_seed(scenario):
    import copy
    quotes = copy.deepcopy(DEMO_QUOTES if scenario != "escalate" else [DEMO_QUOTES[1]])
    return {"quotes": quotes, "cre_driven": "no",
            "cost_items": copy.deepcopy(DEMO_QUOTES[1]["items"]), "contingency_pct": ""}


DEMO_STRONG = {
    "cs_count": "We have one shop vehicle for tool and parts runs; there is no spare, so when it is down we have none.",
    "cs_age": "The van is a 2007 Chrysler Town & Country with 140,761 miles; it runs about 6 days a week across all three shifts.",
    "cs_failures": "It broke down 7 times in the last 12 months and was out of service 19 days in total.",
    "cs_mechanism": "Every tool run starts at the shop and ends at the aircraft; with the van down, the mechanic walks tools out or waits for a tug, so the repair starts late and the turn slips.",
    "cs_workaround": "When it's down we borrow a tug from ramp or walk tools out, which adds about 45 minutes per job.",
    "cs_spend": "We spent $4,200 on repairs in the last 12 months, including a 3-week rental.",
    "ap_evidence": "Appendix A: three vendor quote letters dated 3/10, 3/12 and 3/14/2026. Appendix B: fleet repair log, FY2025.",
    "pr_scope": "One compact crew-cab pickup with a locking bed cap and toolbox, replacing van #22991 at the BOS facilities shop.",
    "pr_single": "Only the replacement truck. Nothing else is bundled into this request.",
    "pr_date": "In service by November 1, 2026, ahead of winter operations; Fleet Partner B delivers in 6 weeks.",
    "pr_constraints": "It must be bought through the corporate fleet contract, and the old van is disposed of by Fleet separately.",
    "co_items": "Compact crew-cab pickup: 1 × $27,858; Bed cap and toolbox upfit: 1 × $1,840; Delivery and registration: 1 × $650",
    "co_contingency": "5% contingency of $1,517 for upfit changes and registration fees; total $31,865.",
    "co_funding": "Funded from the 2026 Cargo Facilities capital plan, cost center 4410.",
    "ju_recommend": "We recommend approving Fleet Partner B at $31,865 including contingency.",
    "ju_benefit": "It ends about 19 days a year without a vehicle and recovers roughly 6 hours a week of mechanic time.",
    "ju_tie": "Breakdowns drop from 7 a year to under 1, and out-of-service days from 19 to about 2.",
    "ju_risk": "The main risk is a delivery slip past November; we will mitigate it by ordering this month and fixing the date in the fleet contract.",
    "ju_assume": "Assumes a 7-year useful life and that the current repair rate would continue if we keep the van.",
    "al_options": "Leasing was considered but rejected because it costs more over 7 years for a vehicle used daily; a short-term rental runs about $1,350 every 3 weeks.",
    "al_repair": "Repair was considered and rejected because it would cost about $6,000 and still leave a 19-year-old vehicle never built for maintenance work.",
    "al_compare": "A: $28,550, 14 weeks, no upfit. B: $30,348, 6 weeks, full scope. C: $33,250, 10 weeks, full scope.",
    "sel_vendor": "Fleet Partner B. It costs $1,798 more than A, but A excludes the bed cap and toolbox (about $1,840 to add) and takes 14 weeks instead of 6. C covers the same scope for $2,902 more. B is the lowest all-in cost for the full scope and the fastest delivery.",
}

DEMO_WEAK = {
    "cs_count": "We don't have enough of them.",
    "cs_age": "It's pretty old and has a lot of miles on it at this point.",
    "cs_failures": "It breaks down fairly often, not sure exactly how many times.",
    "cs_mechanism": "It just slows everything down when it's broken.",
    "cs_workaround": "We make do by borrowing equipment from other teams when it's down.",
    "cs_spend": "Not sure, Finance would have that.",
    "ap_evidence": "Not sure what we can attach.",
    "pr_scope": "A newer truck of some kind.",
    "pr_single": "The truck mostly.",
    "pr_date": "As soon as possible.",
    "pr_constraints": "Fleet would handle it.",
    "co_items": "I'd need to check with the vendor on the breakdown.",
    "co_contingency": "Not sure what contingency to use.",
    "co_funding": "Not sure if it's in the plan.",
    "ju_recommend": "We think the new truck would be better.",
    "ju_benefit": "It should save time, hard to say how much.",
    "ju_tie": "Things would be better than they are now.",
    "ju_risk": "Delivery could take a while.",
    "ju_assume": "Not sure.",
    "al_options": "We looked at a couple of other trucks.",
    "al_repair": "We could repair it.",
    "al_compare": "They're all about the same.",
    "sel_vendor": "Probably the cheapest one.",
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
        weak = (scenario == "escalate" or
                (scenario == "second" and rnd["round"] == 1 and q["category"] in ("Cost", "Justification")))
        src = DEMO_WEAK if weak else DEMO_STRONG
        if q["id"] in src:
            out[q["id"]] = src[q["id"]]
            continue
        pool = _pool(src, q["field"]) or _pool(src, "current_situation")
        i = used.get(q["field"], 0)
        used[q["field"]] = i + 1
        out[q["id"]] = pool[i % len(pool)] if pool else ""
    return out
