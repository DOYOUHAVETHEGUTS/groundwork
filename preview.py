"""Seeded personas and sample requests so the V2 and V3 tabs can be demonstrated on any
deploy without creating real accounts. Preview data is flagged and never mixes with real
requests; personas can't log in."""
import copy

import agents
import coach
import notify
import rubric
import store

DIVS = {"pv_div_cargo": "Cargo Finance", "pv_div_techops": "Tech Ops Finance"}
GROUPS = {"pv_grp_cargo_fpa": ("Cargo FP&A", "pv_div_cargo"),
          "pv_grp_cargo_ops": ("Cargo Ops Finance", "pv_div_cargo"),
          "pv_grp_to_cap": ("Tech Ops Capital", "pv_div_techops")}
PERSONAS = [  # id, name, role, group, editions
    ("pv_ana_a", "Jordan · Analyst", "analyst", "pv_grp_cargo_fpa", ("v2", "v3")),
    ("pv_ana_b", "Riley · Analyst", "analyst", "pv_grp_cargo_fpa", ("v2", "v3")),
    ("pv_dir", "Casey · Director, Cargo FP&A", "director", "pv_grp_cargo_fpa", ("v2", "v3")),
    ("pv_adm", "Platform admin", "admin", "pv_grp_cargo_fpa", ("v2", "v3")),
    ("pv_ana_c", "Sam · Analyst, Cargo Ops Finance", "analyst", "pv_grp_cargo_ops", ("v3",)),
    ("pv_ana_d", "Morgan · Analyst, Tech Ops Capital", "analyst", "pv_grp_to_cap", ("v3",)),
    ("pv_lead", "Avery · Division leader, Cargo Finance", "leader", "pv_grp_cargo_fpa", ("v3",)),
    ("pv_fin", "Taylor · Finance admin, all divisions", "finance_admin", "pv_grp_cargo_fpa", ("v3",)),
]


def personas(edition):
    return [{"id": i, "name": n, "role": r, "group": GROUPS[g][0], "division": DIVS[GROUPS[g][1]]}
            for i, n, r, g, eds in PERSONAS if edition in eds]


def _van(scn, rounds):
    meta = {"title": "BOS facilities truck replacement", "request_type": "Vehicle / GSE",
            "department": "Cargo Facilities", "requester": ""}
    r = store.blank(meta)
    r.update(demo=True, demo_scenario=scn, demo_rules_only=True, original_description=coach.DEMO_TEXT,
             **coach.demo_seed(scn))
    r.update({k: v for k, v in agents.structure(coach.DEMO_TEXT, meta, force_rules=True).items()
              if k in rubric.BLANK_REQUEST})
    q = agents.qualify(r)
    r.update(qualification=q, score=q["overall"], status=q["status"])
    for _ in range(rounds):
        r = coach.start_round(r)
        r = coach.submit_answers(r, coach.demo_answers(r), notifier=notify.escalate)
        if r["coaching"]["rounds"][-1]["passed"]:
            break
    return r


def _training(name):
    import os
    import docimport
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "training", name)
    if not os.path.exists(path):
        return None
    r = store.blank()
    r.update(docimport.parse_docx(open(path, "rb").read()))
    q = agents.qualify(dict(r, demo_rules_only=True))
    r.update(qualification=q, score=q["overall"], status=q["status"], demo_rules_only=True)
    return r


def seed():
    if store.get_user("pv_adm"):
        return False
    for did, name in DIVS.items():
        store.ensure_division(name, did)
    for gid, (name, did) in GROUPS.items():
        store.ensure_group(name, did, gid)
    for uid, name, role, gid, _ in PERSONAS:
        store.create_user(f"{uid}@preview.local", name, role, gid, GROUPS[gid][1], source="demo", uid=uid)
    plan = [
        (lambda: _van("pass", 1), "pv_ana_a", "BOS facilities truck replacement"),
        (lambda: _van("second", 1), "pv_ana_a", "BOS shop truck (round 1 in progress)"),
        (lambda: _van("escalate", 2), "pv_ana_b", "Ramp tug replacement (one quote only)"),
        (lambda: _training("Training_5pt_03_Weak_Engine_Tooling_Request.docx"), "pv_ana_b", None),
        (lambda: _training("Training_5pt_01_Exemplary_Hangar_Electrical_Upgrade.docx"), "pv_ana_c", None),
        (lambda: _training("Training_5pt_02_Refined_Engine_Transport_Stands.docx"), "pv_ana_d", None),
        (lambda: _van("pass", 0), "pv_ana_c", "Cold-chain cooler expansion (draft)"),
    ]
    for make, owner, title in plan:
        r = make()
        if not r:
            continue
        u = store.get_user(owner)
        r = copy.deepcopy(r)
        r.update(id=None, preview=True, requester=u["name"].split(" ·")[0], owner_name=u["name"])
        if title:
            r["title"] = title
        store.save(r, owner=u)
    store.audit("pv_adm", "preview.seed", "", "Seeded preview personas and sample requests")
    return True
