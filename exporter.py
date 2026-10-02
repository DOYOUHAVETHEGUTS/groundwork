"""Exports: Levelpath JSON payload, Markdown, and Word (.docx if python-docx is present)."""
import io
import json

import rubric

FIN_ORDER = [
    ("capex", "One-time / capital cost"),
    ("opex_annual", "Recurring annual cost"),
    ("benefit_annual", "Annual benefit / revenue"),
    ("npv", "NPV @ 20%"),
    ("mirr", "5-year MIRR"),
    ("payback_months", "Payback (months)"),
]


def levelpath_payload(req: dict) -> dict:
    q = req.get("qualification") or {}
    return {
        "schema": "groundwork.five_point.v1",
        "request_id": req.get("id"),
        "title": req.get("title"),
        "department": req.get("department"),
        "request_type": req.get("request_type"),
        "requester": req.get("requester"),
        "five_point": {k: req.get(k, "") for k, _ in rubric.SECTIONS},
        "executive_summary": req.get("executive_summary", ""),
        "operational_impact": req.get("operational_impact", ""),
        "affected_groups": req.get("affected_groups", ""),
        "timing": req.get("timing", ""),
        "financials": {k: req.get(k, "") for k, _ in rubric.FINANCIAL_FIELDS},
        "qualification": {
            "score": q.get("overall"),
            "status": q.get("status"),
            "categories": q.get("categories"),
            "open_items": q.get("missing"),
            "engine": q.get("engine"),
        },
        "original_description": req.get("original_description", ""),
        "guided_improvement": coaching_summary(req),
    }


def coaching_summary(req: dict):
    co = req.get("coaching") or {}
    rounds = [r for r in co.get("rounds") or [] if r.get("answered")]
    if not rounds:
        return None
    esc = co.get("escalation") or {}
    return {
        "status": co.get("status"),
        "original_score": co.get("original_score"),
        "rounds": [{
            "round": r["round"], "score_before": r.get("score_before"),
            "score_after": r.get("score_after"), "passed": r.get("passed"),
            "questions": [{"question": q["question"], "field": q["field"],
                           "answer": (r.get("answers") or {}).get(q["id"], "")}
                          for q in r.get("questions", [])],
            "largest_edits": [{"field": e["label"], "points": e["points"],
                               "calculated": e.get("calculated", False)}
                              for e in (r.get("edits") or [])[:5]],
        } for r in rounds],
        "escalation": ({"to": esc.get("to_email"), "method": esc.get("method"),
                        "delivered": esc.get("delivered"), "simulated": esc.get("simulated")}
                       if esc else None),
    }


def _coaching_lines(req):
    cs = coaching_summary(req)
    if not cs:
        return []
    path = " → ".join([str(cs["original_score"])] + [str(r["score_after"]) for r in cs["rounds"]])
    out = [f"Score path through guided questions: {path}."]
    for r in cs["rounds"]:
        n = sum(1 for q in r["questions"] if q["answer"])
        top = ", ".join(f"{e['field']} (+{e['points']})" for e in r["largest_edits"][:3] if e["points"] > 0)
        out.append(f"Round {r['round']}: {n} of {len(r['questions'])} answered, "
                   f"{r['score_before']} → {r['score_after']}" + (f"; largest gains: {top}." if top else "."))
    if cs["escalation"]:
        out.append(f"Escalated to finance partner {cs['escalation']['to'] or '(not set)'} "
                   f"via {cs['escalation']['method']}.")
    if "Groundwork calc" in " ".join(str(req.get(k, "")) for k in ("npv", "mirr", "payback_months")):
        out.append("NPV, MIRR and/or payback were calculated by Groundwork from the stated "
                   "cost and benefit and should be confirmed by Finance.")
    return out


def to_markdown(req: dict) -> str:
    L = [f"# {req.get('title') or 'Capital Request'}",
         f"*{req.get('department','')} · {req.get('request_type','')} · {req.get('id','')}*", ""]
    if req.get("executive_summary"):
        L += ["## Executive Summary", req["executive_summary"], ""]
    for key, label in rubric.SECTIONS:
        L += [f"## {label}", req.get(key) or "_Not provided_", ""]
        if key == "justification":
            if req.get("operational_impact"):
                L += ["### Operational Impact Assessment", req["operational_impact"], ""]
            if req.get("affected_groups"):
                L += [f"**Who is affected:** {req['affected_groups']}", ""]
            if req.get("timing"):
                L += [f"**Why now:** {req['timing']}", ""]
    L += ["## Financials", "", "| Item | Value |", "| --- | --- |"]
    for k, label in FIN_ORDER:
        L.append(f"| {label} | {req.get(k) or '—'} |")
    L.append("")
    if req.get("assumptions"):
        L += ["**Key assumptions:** " + req["assumptions"], ""]
    q = req.get("qualification") or {}
    if q:
        L += ["---", f"*Groundwork qualification score: {q.get('overall')} / 100 "
              f"({q.get('status')}) — engine: {q.get('engine')}*"]
        if q.get("missing"):
            L += ["", "**Open items before submission:**"] + [f"- {m}" for m in q["missing"]]
    cl = _coaching_lines(req)
    if cl:
        L += ["", "### Guided improvement"] + [f"- {x}" for x in cl]
    return "\n".join(L)


def to_docx_bytes(req: dict):
    """Returns (bytes, filename, mimetype). Falls back to markdown if python-docx absent."""
    try:
        from docx import Document
        from docx.shared import Pt
    except ImportError:
        md = to_markdown(req).encode("utf-8")
        return md, f"{req.get('id','request')}-5point.md", "text/markdown"

    doc = Document()
    doc.add_heading(req.get("title") or "Capital Request", level=0)
    p = doc.add_paragraph(f"{req.get('department','')}  |  {req.get('request_type','')}  |  {req.get('id','')}")
    p.runs[0].font.size = Pt(9)

    if req.get("executive_summary"):
        doc.add_heading("Executive Summary", level=1)
        doc.add_paragraph(req["executive_summary"])

    doc.add_heading("5-Point Justification", level=1)
    for key, label in rubric.SECTIONS:
        doc.add_heading(label, level=2)
        for line in (req.get(key) or "Not provided").split("\n"):
            line = line.strip()
            if not line:
                continue
            if line.startswith(("-", "*", "•")):
                doc.add_paragraph(line.lstrip("-*• ").strip(), style="List Bullet")
            else:
                doc.add_paragraph(line)
        if key == "justification":
            if req.get("operational_impact"):
                doc.add_heading("Operational Impact Assessment", level=3)
                doc.add_paragraph(req["operational_impact"])
            if req.get("affected_groups"):
                doc.add_paragraph(f"Who is affected: {req['affected_groups']}")
            if req.get("timing"):
                doc.add_paragraph(f"Why now: {req['timing']}")

    doc.add_heading("Financials", level=1)
    t = doc.add_table(rows=1, cols=2)
    t.style = "Light Grid Accent 1"
    t.rows[0].cells[0].text, t.rows[0].cells[1].text = "Item", "Value"
    for k, label in FIN_ORDER:
        c = t.add_row().cells
        c[0].text, c[1].text = label, str(req.get(k) or "—")
    if req.get("assumptions"):
        doc.add_paragraph("Key assumptions: " + req["assumptions"])

    q = req.get("qualification") or {}
    if q:
        doc.add_heading("Groundwork Qualification", level=1)
        doc.add_paragraph(f"Score: {q.get('overall')} / 100 ({q.get('status')}) — engine: {q.get('engine')}")
        for m in q.get("missing") or []:
            doc.add_paragraph(m, style="List Bullet")
    cl = _coaching_lines(req)
    if cl:
        doc.add_heading("Guided Improvement", level=2)
        for x in cl:
            doc.add_paragraph(x, style="List Bullet")

    buf = io.BytesIO()
    doc.save(buf)
    return (buf.getvalue(), f"{req.get('id','request')}-5point.docx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document")


def to_json_bytes(req: dict):
    payload = json.dumps(levelpath_payload(req), indent=2).encode("utf-8")
    return payload, f"{req.get('id','request')}-levelpath.json", "application/json"
