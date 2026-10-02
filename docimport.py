"""Import an existing Word five-point (.docx) into a Groundwork request.

Maps headings to the five literal sections, keeps tables as pipe-delimited rows
(so cost build-ups can be reconciled), records every picture as an exhibit with
its caption, and turns bid-summary tables into vendor quotes.
"""
import io
import re

import rubric

HEAD_MAP = [
    (re.compile(r"current situation|background", re.I), "current_situation"),
    (re.compile(r"^\W*\d*\W*proposal\b", re.I), "proposal"),
    (re.compile(r"^\W*\d*\W*costs?\b|cost build|financials?\b", re.I), "cost"),
    (re.compile(r"^\W*\d*\W*justification\b", re.I), "justification"),
    (re.compile(r"^\W*\d*\W*alternatives?\b", re.I), "alternatives"),
    (re.compile(r"^\W*(appendix|exhibit|supporting detail|attachment)", re.I), "appendix"),
]
SKIP = re.compile(r"training label|reviewer note \(training\)|quality tier|^\[requester name\]", re.I)
DIAGRAM = re.compile(r"chart|workflow|flow|diagram|timeline|graph|trend|recreated", re.I)
BID_HEAD = re.compile(r"contractor|vendor|supplier|bidder|quote|bid", re.I)
TOTAL_HEAD = re.compile(r"total|price|amount|cost", re.I)


def _iter_blocks(doc):
    from docx.table import Table
    from docx.text.paragraph import Paragraph
    for child in doc.element.body.iterchildren():
        tag = child.tag.split("}")[-1]
        if tag == "p":
            yield Paragraph(child, doc)
        elif tag == "tbl":
            yield Table(child, doc)


def _has_image(p):
    return bool(p._p.xpath(".//*[local-name()='blip']"))


def _table_rows(t):
    rows = []
    for r in t.rows:
        cells, last = [], None
        for c in r.cells:
            if c._tc is last:          # merged cell repeats
                continue
            last = c._tc
            cells.append(re.sub(r"\s+", " ", c.text).strip())
        rows.append(cells)
    return rows


def _section_for(text, style):
    if re.search(r"5-?\s?point", text, re.I):
        return None
    is_head = style.lower().startswith(("heading", "title")) or (len(text.split()) <= 6 and text[:1].isupper())
    if not is_head:
        return None
    for rx, key in HEAD_MAP:
        if rx.search(text):
            return key
    return None


def parse_docx(data: bytes) -> dict:
    from docx import Document
    doc = Document(io.BytesIO(data))
    req = rubric.upgrade({})
    buf = {k: [] for k, _ in rubric.SECTIONS + [rubric.APPENDIX]}
    meta, cur, title, fig, pending_img = [], None, "", 0, False
    app_label = ""
    last_quotes = []
    for b in _iter_blocks(doc):
        if b.__class__.__name__ == "Table":
            rows = _table_rows(b)
            if not rows:
                continue
            flat = " ".join(" ".join(r) for r in rows)
            if SKIP.search(flat) and max(len(r) for r in rows) == 1:      # training/reviewer callout box
                continue
            head = [h.lower() for h in rows[0]]
            if cur is None and head[:2] == ["field", "value"]:      # metadata block
                for r in rows[1:]:
                    if len(r) >= 2 and not SKIP.search(r[0]):
                        meta.append(f"{r[0]}: {r[1]}")
                        if re.match(r"business group|department", r[0], re.I):
                            req["department"] = r[1]
                continue
            if head and BID_HEAD.search(head[0]) and any(TOTAL_HEAD.search(h) for h in head[1:]):
                ti = max(i for i, h in enumerate(head) if TOTAL_HEAD.search(h))
                last_quotes = []
                for r in rows[1:]:
                    if len(r) <= ti or not r[0]:
                        continue
                    q = {"vendor": re.sub(r"\(selected\)|\*", "", r[0], flags=re.I).strip(),
                         "total": r[ti], "items": [], "selected": "(selected)" in r[0].lower(),
                         "notes": "", "_star": "*" in r[0]}
                    if rubric.money(r[ti]) or rubric._num(r[ti]):
                        req["quotes"].append(q)
                        last_quotes.append(q)
            lines = [" | ".join(rows[0])] + [" | ".join(r) for r in rows[1:]]
            buf[cur or "current_situation"].append("\n".join(lines))
            continue
        text = re.sub(r"\s+", " ", b.text).strip()
        style = b.style.name if b.style is not None else ""
        if _has_image(b):
            pending_img = True
            if not text:
                continue
        if not text or SKIP.search(text):
            continue
        if not title and (style.lower().startswith("title") or cur is None and len(text.split()) <= 12):
            title = text
            continue
        key = _section_for(text, style)
        if key:
            cur = key
            if key == "appendix":
                m = re.match(r"\W*(?:appendix|exhibit|attachment)\s+([A-Z]|\d{1,2})", text, re.I)
                app_label = f"Appendix {m.group(1).upper()}" if m else "Appendix"
                buf["appendix"].append(text)
            continue
        if pending_img:
            pending_img = False
            fig += 1
            cap, _, cite = text.partition(" | ")
            if cap and (cap.startswith("[") or len(cap.split()) <= 25):
                req["exhibits"].append({
                    "label": app_label if cur == "appendix" and app_label else f"Figure {fig}",
                    "title": cap.strip("[] "), "kind": "diagram" if DIAGRAM.search(text) else "photo",
                    "citation": cite.strip(), "date": "", "section": cur or "current_situation"})
                if cap.startswith("[") and not cite:
                    continue
                if cite:
                    continue
        if text.startswith("*") and last_quotes:
            for q in last_quotes:
                if q.pop("_star", False):
                    q["notes"] = text.lstrip("* ")
            if any(q.get("selected") for q in last_quotes):
                req["selection_rationale"] = text.lstrip("* ")
        bullet = "- " if style.lower().startswith("list") else ""
        buf[cur or "current_situation"].append(bullet + text)
    for q in req["quotes"]:
        q.pop("_star", None)
    for k in buf:
        req[k] = "\n".join(buf[k]).strip()
    req["title"] = title or "Imported five-point"
    req["original_description"] = "\n".join(meta)
    cre = rubric.cre_status(req)
    if cre["hint"] == "explicit" or re.search(r"corporate real estate", req.get("department", ""), re.I):
        req["cre_hint"] = "explicit"
    req["imported"] = True
    return req
