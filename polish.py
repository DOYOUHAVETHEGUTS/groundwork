"""Output polish: the draft must read like an analyst wrote it.

* Removes repeated sentences inside a section and across sections (the
  "double-typed sentence" problem), keeping the first occurrence.
* Replaces stock AI phrasing with plain words and drops filler openers.
* Leaves tables (pipe rows) and numbers untouched.
"""
import re

import rubric

SWAPS = [
    (r"\butiliz(e|es|ed|ing)\b", lambda m: {"e": "use", "es": "uses", "ed": "used", "ing": "using"}[m.group(1)]),
    (r"\bleverag(e|es|ed|ing)\b", lambda m: {"e": "use", "es": "uses", "ed": "used", "ing": "using"}[m.group(1)]),
    (r"\bin order to\b", "to"), (r"\bstreamlin(e|es|ed|ing)\b", lambda m: "simplif" + {"e": "y", "es": "ies", "ed": "ied", "ing": "ying"}[m.group(1)]),
    (r"\bseamless(ly)?\b", ""), (r"\brobust\b", "reliable"), (r"\bholistic\b", "complete"),
    (r"\bcutting-edge\b", "current"), (r"\bstate-of-the-art\b", "current"), (r"\bpivotal\b", "key"),
    (r"\bit is (important|worth) (to note|noting) that\s*", ""), (r"\bplays? a (crucial|vital|key) role in\b", "affects"),
    (r"\bat the end of the day,?\s*", ""), (r"\bin today's [a-z -]+(world|environment|landscape),?\s*", ""),
    (r"\ba wide (range|variety) of\b", "many"), (r"\bdue to the fact that\b", "because"),
]
FILLER = re.compile(r"^(Furthermore|Moreover|Additionally|In addition|Notably|Importantly|In conclusion|Overall|"
                    r"Ultimately|Essentially),\s+(\w)")
TELLS = re.compile(r"\b(delve|tapestry|synerg\w*|game-changer|transformative|unlock|elevate|empower|navigate the)\b", re.I)


def _norm(s):
    return re.sub(r"[^a-z0-9 ]", "", s.lower()).strip()


def _similar(a, b):
    A, B = set(a.split()), set(b.split())
    return len(A & B) / max(1, len(A | B)) >= 0.88


def _swap(text):
    for rx, rep in SWAPS:
        text = re.sub(rx, rep, text, flags=re.I)
    text = FILLER.sub(lambda m: m.group(2).upper(), text)
    return re.sub(r"[ \t]{2,}", " ", re.sub(r"\s+([,.;:])", r"\1", text)).replace("..", ".").strip()


def clean_text(text, seen=None):
    """Polish one section. `seen` carries normalized sentences from earlier sections."""
    seen = seen if seen is not None else []
    out_lines = []
    for line in str(text or "").split("\n"):
        if "|" in line or not line.strip():
            out_lines.append(line.rstrip())
            continue
        prefix = re.match(r"^\s*([-•*]|\d{1,2}[.)])\s+", line)
        body = line[prefix.end():] if prefix else line
        kept = []
        for s in re.split(r"(?<=[.!?])\s+(?=[A-Z$\d(\[])", body.strip()):
            s = _swap(s)
            n = _norm(s)
            if len(n) >= 20 and any(_similar(n, p) for p in seen):
                continue
            if len(n) >= 20:
                seen.append(n)
            if s:
                kept.append(s)
        if kept:
            out_lines.append((prefix.group(0) if prefix else "") + " ".join(kept))
    return "\n".join(out_lines).strip()


def polish_request(req: dict) -> dict:
    seen = []
    for key, _ in rubric.SECTIONS + [rubric.APPENDIX]:
        if req.get(key):
            req[key] = clean_text(req[key], seen)
    if req.get("selection_rationale"):
        req["selection_rationale"] = clean_text(req["selection_rationale"], [])
    return req


def ai_tells(text):
    return sorted({m.group(0).lower() for m in TELLS.finditer(str(text or ""))})
