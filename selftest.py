"""Smoke test: structure + score the weak (BOS van) and strong (Parcel) examples."""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import agents, exporter, rubric, store

BOS = ("Facilities in BOS runs a 2007 Chrysler Town and Country with 140,761 miles for tool and "
       "personnel transport. The fuel gauge needle falls off so we cannot tell fuel level, there is "
       "exterior rust and the seating and carpeting are unserviceable. It was never designed as a "
       "maintenance vehicle. We have a fleet contract quote for a crew cab pickup at $30,348 total. "
       "A smaller truck maneuvers better in ramp congestion. Alternatives are a Ford Ranger or Chevy Colorado.")

store.init()
d = agents.structure(BOS, {"title": "BOS Van #22991 Replacement", "request_type": "Vehicle / GSE",
                           "department": "Cargo Facilities"})
req = store.blank({"title": "BOS Van #22991 Replacement", "request_type": "Vehicle / GSE",
                   "department": "Cargo Facilities"})
req["original_description"] = BOS
req.update({k: v for k, v in d.items() if k in req})
q = agents.qualify(req)
req["qualification"], req["score"], req["status"] = q, q["overall"], q["status"]
req = store.save(req)
print(f"WEAK  -> {q['overall']}/100 ({q['status']}) engine={q['engine']}")
for c in q["categories"]:
    print(f"        {c['name']:<20}{c['score']:>4}")
print("        missing:", q["missing"][:3])

strong = dict(req)
strong.update({
    "id": None,
    "title": "Domestic Cargo Parcel Product Launch",
    "background": ("Cargo engaged Cirrus Global Advisors for a 12-week study delivered in four phases "
                   "between July 18 and September 25. Delta launched a comparable product with SmartKargo "
                   "in January 2024. US parcel market is $204B and 25.2B parcels in 2025, 4.1% CAGR."),
    "proposal": ("Enter an agreement with SmartKargo to launch a domestic eCommerce parcel product by "
                 "3Q 2026 with 2-to-3-day SLAs across 31 O&Ds and 17 cities."),
    "justification": ("Asset-light entry using belly capacity formerly used for USPS mail. Attainable 1% "
                      "share of a 500M-578M parcel TAM is worth $80M-$85M annually."),
    "operational_impact": ("Incremental tonnage of 0.1% to 1.3% of system pounds 2026-2030. Workforce "
                           "planning shows 0.1 FTE in airport ops and 1.0 FTE in cargo ops. OPP turn "
                           "studies in 2022 and 2023 show no negative turn impact."),
    "affected_groups": "Cargo ops at DFW, ORD, LAX, MIA plus 13 outsourced stations; 3 incremental FTE",
    "timing": "Launch 3Q 2026; L5 and L6 hires start April 2026 to build the sales funnel",
    "risks": ("Slow adoption — mitigated by a light fixed-cost model. Competitive response — hub location "
              "differentiates. Integration delay — mitigated by contract SLAs. Poor first/last mile SLA "
              "performance — AA Procurement will contract SLAs and can engage providers directly."),
    "alternatives": ("16 other tech platforms were reviewed via RFI. Each had shortcomings: cross-border "
                     "designs never tested domestically, home-grown front ends, no white-label experience, "
                     "and none offered a turn-key first/last mile network, so a formal RFP was not pursued."),
    "capex": "$400,000 one-time ($375K SmartKargo integration + $25K internal IT)",
    "opex_annual": "$0.50/parcel technology fee plus $75K/month minimum from month 13",
    "benefit_annual": "$50.3M revenue in year 5",
    "npv": "$14.5M at 20%", "mirr": "51.8%", "payback_months": "10",
    "assumptions": "6/1/2026 launch; fuel CPG from the 5YP 10+2 Nov 20 peg; $0.07/lb outsourced handling",
    "missing_information": [],
})
q2 = agents.qualify(strong)
strong["qualification"], strong["score"], strong["status"] = q2, q2["overall"], q2["status"]
strong = store.save(strong)
print(f"\nSTRONG-> {q2['overall']}/100 ({q2['status']}) engine={q2['engine']}")
for c in q2["categories"]:
    print(f"        {c['name']:<20}{c['score']:>4}")

data, name, ctype = exporter.to_docx_bytes(strong)
open(os.path.join(os.path.dirname(DB := store.DB_PATH), name), "wb").write(data)
print(f"\nExport OK: {name} ({len(data)} bytes, {ctype})")
print("Levelpath payload keys:", list(exporter.levelpath_payload(strong).keys()))
print("Requests in DB:", [(r['id'], r['score'], r['status']) for r in store.list_all()])

# ---------------------------------------------------------------- guided improvement
import coach, notify
print("\nGuided improvement (demo scenarios, rules engine):")
expect = {"pass": "passed", "second": "passed", "escalate": "escalated"}
for scn, want in expect.items():
    meta = {"title": f"selftest {scn}", "request_type": "Vehicle / GSE", "department": "Cargo Facilities"}
    r = store.blank(meta)
    r.update(demo=True, demo_scenario=scn, demo_rules_only=True, original_description=coach.DEMO_TEXT)
    r.update({k: v for k, v in agents.structure(coach.DEMO_TEXT, meta, force_rules=True).items() if k in r})
    q = agents.qualify(r)
    r["qualification"], r["score"], r["status"] = q, q["overall"], q["status"]
    path, asked = [q["overall"]], []
    for _ in range(coach.MAX_ROUNDS):
        r = coach.start_round(r)
        rnd = r["coaching"]["rounds"][-1]
        assert len(rnd["questions"]) == coach.PER_ROUND, "each round must ask 10 questions"
        ids = [x["id"] for x in rnd["questions"]]
        assert not set(ids) & set(asked), "round 2 must not repeat round 1"
        asked += ids
        r = coach.submit_answers(r, coach.demo_answers(r), notifier=notify.escalate)
        path.append(r["coaching"]["rounds"][-1]["score_after"])
        if r["coaching"]["rounds"][-1]["passed"]:
            break
    got = r["coaching"]["status"]
    assert got == want, f"{scn}: expected {want}, got {got}"
    print(f"  {scn:<9} {' -> '.join(map(str, path)):<14} {got}")
    store.save(r)
