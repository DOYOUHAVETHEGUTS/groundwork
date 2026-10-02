"""Self-test: scorer calibration on the training docs, demo paths, gates, polish, access rules.

    python selftest.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import agents, auth, coach, docimport, notify, polish, rubric, store  # noqa: E401,E402

HERE = os.path.dirname(os.path.abspath(__file__))
store.init()
ok = True


def check(cond, msg):
    global ok
    ok &= bool(cond)
    print(("  PASS " if cond else "  FAIL ") + msg)


print("Calibration on the training library (rules engine):")
scores = {}
for key, name in (("weak", "Training_5pt_03_Weak_Engine_Tooling_Request.docx"),
                  ("refined", "Training_5pt_02_Refined_Engine_Transport_Stands.docx"),
                  ("exemplary", "Training_5pt_01_Exemplary_Hangar_Electrical_Upgrade.docx")):
    path = os.path.join(HERE, "training", name)
    if not os.path.exists(path):
        continue
    q = rubric.score_request(docimport.parse_docx(open(path, "rb").read()))
    scores[key] = q
    print(f"  {key:<10}{q['overall']:>4}  {q['status']:<8} gates open: {[g['label'] for g in q['gates'] if not g['passed']]}")
if len(scores) == 3:
    check(scores["weak"]["overall"] < 40 < scores["refined"]["overall"] < scores["exemplary"]["overall"],
          "ordering weak < 40 < refined < exemplary")
    check(any(f["id"] == "pr_bundled" for f in scores["weak"]["findings"]), "weak: bundled asks detected")
    check(any(f["id"] == "pr_title" for f in scores["weak"]["findings"]), "weak: title mismatch detected")
    check(not any(g["id"] == "three_quotes" and g["passed"] for g in scores["refined"]["gates"]),
          "refined: sole-source request fails the three-quote gate")
    check(scores["exemplary"]["cre"]["hint"] == "explicit", "exemplary: CRE-driven request recognized")

print("\nGuided rounds (demo scenarios):")
for scn, want in (("pass", "passed"), ("second", "passed"), ("escalate", "escalated")):
    r = store.blank({"title": "BOS facilities truck", "department": "Cargo Facilities"})
    r.update(demo=True, demo_scenario=scn, demo_rules_only=True, original_description=coach.DEMO_TEXT, **coach.demo_seed(scn))
    r.update({k: v for k, v in agents.structure(coach.DEMO_TEXT, r, force_rules=True).items() if k in rubric.BLANK_REQUEST})
    q = agents.qualify(r)
    r.update(qualification=q, score=q["overall"], status=q["status"])
    path, asked = [q["overall"]], []
    for _ in range(coach.MAX_ROUNDS):
        r = coach.start_round(r)
        rnd = r["coaching"]["rounds"][-1]
        ids = [x["id"] for x in rnd["questions"]]
        check(len(ids) == 10 and not set(ids) & set(asked), f"{scn}: round {rnd['round']} asks 10 new questions")
        asked += ids
        r = coach.submit_answers(r, coach.demo_answers(r), notifier=notify.escalate)
        path.append(r["coaching"]["rounds"][-1]["score_after"])
        if r["coaching"]["rounds"][-1]["passed"]:
            break
    check(r["coaching"]["status"] == want, f"{scn}: {' -> '.join(map(str, path))} ends {r['coaching']['status']}")

print("\nPolish:")
t = polish.clean_text("Furthermore, we utilize the stand daily. We utilize the stand daily.")
check(t == "We use the stand daily.", f"duplicate sentence + AI phrasing removed -> {t!r}")

print("\nAccess rules:")
a = {"id": "a", "role": "analyst", "group_id": "g1", "division_id": "d1"}
d = {"id": "d", "role": "director", "group_id": "g1", "division_id": "d1"}
l = {"id": "l", "role": "leader", "group_id": "g9", "division_id": "d1"}
req_b = {"owner": "b", "group_id": "g1", "division_id": "d1"}
req_x = {"owner": "x", "group_id": "g2", "division_id": "d2"}
check(not auth.can_read(a, req_b), "analyst can't read a colleague's request")
check(auth.can_read(d, req_b) and not auth.can_write(d, req_b), "director reads the group, can't edit")
check(auth.can_read(l, req_b) and not auth.can_read(l, req_x), "leader reads own division only")
print("\nALL PASS" if ok else "\nSOME CHECKS FAILED")
sys.exit(0 if ok else 1)
