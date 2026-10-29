"""
DISPATCH SENTINEL V7 - Budgeted Active Verification Selection Benchmark
Tests the complete operational decision loop under constrained scouting budgets:
1. Ingests a batch of concurrent unauthenticated crisis field reports.
2. Performs live spatial triage using NVIDIA Nemotron 3.5 Lightning.
3. Computes Restricted-Policy EVSI for all pending uncertain segments.
4. Ranks the queue and executes a strict verification budget B (e.g. B=2 missions).
5. Reports prioritized for scouting are verified via simulated drone reconnaissance (hidden ground truth).
6. Non-selected reports remain unverified and are NOT automatically applied.
7. Authorized closures are applied to the physical road network.
8. OR-Tools CVRP-TW replans fleet delivery convoys.
9. Independent deterministic route validator audits the final plan.
10. Saves full budgeted decision traces to BUDGETED_VERIFICATION_RESULTS.json.
"""

import os
import json
import time
import random
from typing import Dict, Any, List, Optional, Tuple

from dispatch.controller import DispatchController
from dispatch.models import IncidentReport, IncidentType, VerificationStatus
from dispatch.voi import compute_restricted_policy_evsi
from dispatch.solver import validate_dispatch_routes

# Ensure API Key loaded
with open(".env") as f:
    for line in f:
        if line.startswith("NEBIUS_API_KEY="):
            os.environ["NEBIUS_API_KEY"] = line.split("=", 1)[1].strip().strip('"')

DRONE_SENSOR_RELIABILITY = 0.98
SCOUTING_BUDGET = 2  # Max B verification missions allowed
random.seed(2026)

# Scenario batch with hidden physical ground truth
BATCH_REPORTS = [
    {
        "id": "RPT_CRISIS_01",
        "text": "Citizen Alert: Main river bridge between junction 2 and 9 is underwater and cracked! Stay away!",
        "channel": "SOCIAL_MEDIA",
        "segment_truly_closed": True,  # Critical bottleneck crossing
        "prior_confidence": 0.85
    },
    {
        "id": "RPT_CRISIS_02",
        "text": "Ham Radio: Central Expressway between 5 and 12 has massive downed power lines across all lanes.",
        "channel": "HAM_RADIO",
        "segment_truly_closed": True,  # Key arterial segment
        "prior_confidence": 0.75
    },
    {
        "id": "RPT_CRISIS_03",
        "text": "Telegram Chat: South bridge between junction 8 and 15 might have some debris on the shoulder.",
        "channel": "SOCIAL_MEDIA",
        "segment_truly_closed": False, # Rumor - actually OPEN
        "prior_confidence": 0.40
    },
    {
        "id": "RPT_CRISIS_04",
        "text": "Citizen SMS: I heard road 1 to 4 is blocked by fallen trees.",
        "channel": "CITIZEN_SMS",
        "segment_truly_closed": False, # False alarm - OPEN
        "prior_confidence": 0.50
    },
    {
        "id": "RPT_CRISIS_05",
        "text": "Local Radio: Road between junction 6 and 7 has severe flooding, impassable.",
        "channel": "LOCAL_RADIO",
        "segment_truly_closed": True,  # Truly closed
        "prior_confidence": 0.80
    }
]

def simulate_scout(truly_closed: bool) -> bool:
    if random.random() < DRONE_SENSOR_RELIABILITY:
        return truly_closed
    return not truly_closed

def run_budgeted_verification_benchmark():
    print("=== DISPATCH SENTINEL V7: BUDGETED ACTIVE VERIFICATION BENCHMARK ===")
    print(f"Total Candidate Field Reports: {len(BATCH_REPORTS)}")
    print(f"Strict Drone Verification Budget (B): {SCOUTING_BUDGET} reconnaissance missions")
    print()

    ctrl = DispatchController()
    initial_plan = ctrl.current_plan
    print(f"Initial Fleet Baseline: {initial_plan.delivery_ratio*100:.1f}% delivery | {initial_plan.total_fleet_distance_km:.1f} km\n")

    triaged_candidates = []

    # 1. Concurrent Ingestion & Live AI Triage
    print("--- STEP 1: Ingesting & Triaging Reports via Nemotron 3.5 Lightning ---")
    for r in BATCH_REPORTS:
        t0 = time.time()
        dec = ctrl.process_field_report(
            report_id=r["id"],
            raw_text=r["text"],
            source_channel=r["channel"],
            auth_token=None # All untrusted field chatter
        )
        t_ms = (time.time() - t0) * 1000.0

        assert dec.verification_status == VerificationStatus.PENDING_OPERATOR_APPROVAL, "Trust boundary breach!"
        assert dec.is_actionable is False, "Unauthenticated report was auto-actioned!"

        if dec.affected_segment:
            triaged_candidates.append({
                "meta": r,
                "decision": dec,
                "segment": dec.affected_segment,
                "latency_ms": t_ms
            })
            print(f"  Ingested {r['id']}: Segment {dec.affected_segment} | Confidence: {dec.confidence_score*100:.0f}% ({t_ms:.0f}ms)")

    # 2. Compute Restricted-Policy EVSI for all pending items
    print(f"\n--- STEP 2: Computing Restricted EVSI Queue ---")
    scored_queue = []
    for cand in triaged_candidates:
        seg = cand["segment"]
        conf = cand["decision"].confidence_score if cand["decision"].confidence_score > 0 else 0.5
        evsi = compute_restricted_policy_evsi(
            ctrl.road_graph,
            ctrl.sectors,
            ctrl.vehicles,
            seg,
            prior_p_closed=conf,
            current_known_closures=ctrl.active_closures,
            drone_sensor_reliability=DRONE_SENSOR_RELIABILITY,
            verification_cost=0.5
        )
        cand["evsi"] = evsi
        scored_queue.append(cand)
        print(f"  Candidate {cand['meta']['id']} (Seg {seg}): Restricted EVSI = {evsi:+.2f} pts")

    # 3. Budgeted Rational Verification Selection:
    # Strictly respect decision-theoretic threshold: only select missions where net EVSI > 0!
    # Budget B is an UPPER BOUND (at most B missions), not a quota to blindly fill.
    scored_queue.sort(key=lambda x: x["evsi"], reverse=True)
    positive_evsi_candidates = [c for c in scored_queue if c["evsi"] > 0.0]
    selected_for_scouting = positive_evsi_candidates[:SCOUTING_BUDGET]
    unselected_deferred = [c for c in scored_queue if c not in selected_for_scouting]

    print(f"\n--- STEP 3: Selecting Up to B={SCOUTING_BUDGET} Highest Positive-EVSI Missions ---")
    if not selected_for_scouting:
        print(f"  [RATIONAL DECISION: ZERO MISSIONS DISPATCHED]")
        print(f"  All candidate EVSI scores are <= 0. Cost of drone reconnaissance exceeds expected operational gain.")
        print(f"  Rational policy avoids wasteful scouting and preserves aerial assets.")
    else:
        for idx, item in enumerate(selected_for_scouting, 1):
            print(f"  [MISSION #{idx}] Scout Incident {item['meta']['id']} on Segment {item['segment']} (EVSI: {item['evsi']:+.2f} > 0)")

    print(f"\n--- STEP 4: Executing Simulated Recon & Human Authorization Gate ---")
    verification_records = []
    for item in selected_for_scouting:
        r = item["meta"]
        dec = item["decision"]
        seg = item["segment"]

        observed_closed = simulate_scout(r["segment_truly_closed"])
        print(f"  Incident {r['id']} (Seg {seg}) -> Truth: {'CLOSED' if r['segment_truly_closed'] else 'OPEN'} | Drone Observed: {'CLOSED' if observed_closed else 'OPEN'}")

        if observed_closed:
            ctrl.operator_approve_incident(dec.incident_id, operator_name="Watch Officer (Post-Recon)")
            print(f"    => Operator Decision: APPROVED CLOSURE. Physical network updated.")
            op_act = "APPROVED"
        else:
            ctrl.operator_dismiss_incident(dec.incident_id, operator_name="Watch Officer (Post-Recon)")
            print(f"    => Operator Decision: DISMISSED AS FALSE RUMOR. Segment kept open.")
            op_act = "DISMISSED"

        verification_records.append({
            "report_id": r["id"],
            "segment": seg,
            "evsi": item["evsi"],
            "selected_for_scouting": True,
            "ground_truth_closed": r["segment_truly_closed"],
            "drone_observed_closed": observed_closed,
            "operator_decision": op_act
        })

    for item in unselected_deferred:
        r = item["meta"]
        print(f"  Deferred Incident {r['id']} (Seg {item['segment']}): Budget exhausted (EVSI {item['evsi']:+.2f}). Kept PENDING.")
        verification_records.append({
            "report_id": r["id"],
            "segment": item["segment"],
            "evsi": item["evsi"],
            "selected_for_scouting": False,
            "ground_truth_closed": r["segment_truly_closed"],
            "drone_observed_closed": None,
            "operator_decision": "DEFERRED_UNVERIFIED"
        })

    # 5. Final Dynamic Fleet Re-optimization & Deterministic Route Validation
    print(f"\n--- STEP 5: Dynamic Replanning & Independent Safety Validation ---")
    final_plan = ctrl.current_plan
    dist_mat, time_mat, paths_dict = ctrl.road_graph.compute_sector_matrix([s.junction_id for s in ctrl.sectors])
    raw_routes = [vr.nodes_sequence for vr in final_plan.vehicle_routes]
    is_valid, hard_violations, valid_dist, valid_ratio, served_count = validate_dispatch_routes(
        sectors=ctrl.sectors,
        vehicles=ctrl.vehicles,
        routes=raw_routes,
        dist_mat=dist_mat,
        time_mat=time_mat,
        paths_dict=paths_dict,
        closed_segments=ctrl.active_closures
    )

    print(f"Final Fleet Plan: {final_plan.delivery_ratio*100:.1f}% delivery | {final_plan.total_fleet_distance_km:.1f} km")
    print(f"Deterministic Safety Violations: {len(hard_violations)} (Route Feasible: {is_valid})")
    print(f"Active Confirmed Closures: {ctrl.active_closures}")

    # Output Benchmark Artifact
    out_file = "BUDGETED_VERIFICATION_RESULTS.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump({
            "metadata": {
                "benchmark": "DISPATCH V7 Budgeted Active Verification",
                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
                "total_reports": len(BATCH_REPORTS),
                "scouting_budget_B": SCOUTING_BUDGET,
                "drone_sensor_reliability": DRONE_SENSOR_RELIABILITY
            },
            "summary": {
                "initial_delivery_ratio": initial_plan.delivery_ratio,
                "final_delivery_ratio": final_plan.delivery_ratio,
                "final_distance_km": final_plan.total_fleet_distance_km,
                "safety_violations": len(final_plan.hard_violations),
                "selected_missions_count": len(selected_for_scouting),
                "deferred_reports_count": len(unselected_deferred)
            },
            "records": verification_records
        }, f, indent=2)

    print(f"\nSaved budgeted benchmark outcomes to {out_file}")

if __name__ == "__main__":
    run_budgeted_verification_benchmark()
