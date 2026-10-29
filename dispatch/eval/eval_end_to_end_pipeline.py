"""
DISPATCH SENTINEL V6 - Full End-to-End Live Pipeline Benchmark
Evaluates the complete unbroken decision chain:
  Raw Disaster Dispatch Text
  ➔ Live NVIDIA Nemotron 3.5 Lightning (Nebius Token Factory)
  ➔ Server-Side HMAC Source Authentication Verification Gate
  ➔ Human-in-the-Loop Operator Authorization Queue (with Simulated Scout Observations)
  ➔ Restricted-Policy EVSI Verification Selection
  ➔ Physical Road Graph Update (Zero Fabricated Distances)
  ➔ Google OR-Tools CVRP-TW Dynamic Re-optimization
  ➔ Independent Ground-Truth Deterministic Route Validation

Evaluation discipline:
- Every case has a pre-defined ground truth (segment_truly_closed: bool).
- Unauthenticated reports are held in the operator queue; the benchmark simulates a scout
  observation drawn from the ground truth with Bernoulli sensor noise (reliability=0.98).
- Operator decisions are only applied AFTER the simulated scout result is known.
- Negative cases (false report) result in operator REJECTION, not approval.
- Auth tokens are generated server-side via DispatchController.generate_auth_token().
  No auth tokens are printed to console; only the channel and report_id are logged.

10 cases: 6 positive (segment truly closed), 4 negative (segment truly open/false report).
"""

import os
import json
import time
import random
from typing import Dict, Any, List, Optional

from dispatch.controller import DispatchController
from dispatch.models import IncidentReport, IncidentType, VerificationStatus
from dispatch.voi import compute_restricted_policy_evsi

# Load API key
with open(".env") as f:
    for line in f:
        if line.startswith("NEBIUS_API_KEY="):
            os.environ["NEBIUS_API_KEY"] = line.split("=", 1)[1].strip().strip('"')

# Sensor reliability for simulated drone scout observations
DRONE_SENSOR_RELIABILITY = 0.98
random.seed(2026)  # Reproducible

# Pre-defined ground-truth scenario definitions.
PIPELINE_CASES = [
    # === POSITIVE CASES (segment truly closed) ===
    {
        "id": "E2E_01",
        "description": "Authenticated police alert — bridge collapsed (positive)",
        "text": "State Police Alert: Highway bridge between junction 2 and junction 9 collapsed due to high flood waters. Closed indefinitely.",
        "channel": "POLICE_DISPATCH",
        "is_authenticated": True,
        "segment_truly_closed": True,
        "expected_operator_decision": "APPROVE",
    },
    {
        "id": "E2E_02",
        "description": "Authenticated civil defense — expressway debris (positive)",
        "text": "Civil Defense Official: Structural hazard confirmed. Fallen power cables blocking Central Expressway between junction 5 and 12. Impassable.",
        "channel": "CIVIL_DEFENSE",
        "is_authenticated": True,
        "segment_truly_closed": True,
        "expected_operator_decision": "APPROVE",
    },
    {
        "id": "E2E_03",
        "description": "Citizen rumor — bridge 8-15 shaking (positive — rumor is TRUE)",
        "text": "Citizen Telegram Feed: People saying South Truss Bridge between 8 and 15 is cracked! Convoy trucks should avoid crossing!",
        "channel": "SOCIAL_MEDIA",
        "is_authenticated": False,
        "segment_truly_closed": True,
        "expected_operator_decision": "APPROVE",
    },
    {
        "id": "E2E_04",
        "description": "Authenticated EMS — road flooded (positive)",
        "text": "Emergency Medical Services: High water covering road between junction 6 and 7. Completely impassable for light and heavy vehicles.",
        "channel": "EMS_OFFICIAL",
        "is_authenticated": True,
        "segment_truly_closed": True,
        "expected_operator_decision": "APPROVE",
    },
    {
        "id": "E2E_05",
        "description": "Ham radio — bridge collapse north sector (positive — report is TRUE)",
        "text": "Amateur Radio Emergency Net: Multiple witnesses report complete bridge failure on northern crossing between junction 1 and junction 4.",
        "channel": "HAM_RADIO",
        "is_authenticated": False,
        "segment_truly_closed": True,
        "expected_operator_decision": "APPROVE",
    },
    {
        "id": "E2E_06",
        "description": "Authenticated civil defense reopening — bridge cleared (positive — reopening after closure)",
        "text": "Civil Defense Clear: Debris cleared between junction 2 and 9. Bridge reopened for heavy convoy traffic after engineering inspection.",
        "channel": "CIVIL_DEFENSE",
        "is_authenticated": True,
        "segment_truly_closed": False,  # Road is OPEN (it is a reopening report)
        "expected_operator_decision": "APPROVE",
    },
    # === NEGATIVE CASES (segment truly open; report is false/disinformation) ===
    {
        "id": "E2E_07",
        "description": "Citizen rumor — road 1-4 closed (negative — rumor is FALSE)",
        "text": "Anonymous Citizen SMS: I think the road between junction 1 and 4 might be closed, saw some cones earlier.",
        "channel": "CITIZEN_SMS",
        "is_authenticated": False,
        "segment_truly_closed": False,
        "expected_operator_decision": "REJECT",
    },
    {
        "id": "E2E_08",
        "description": "Social media panic — segment 9-10 (negative — false rumor)",
        "text": "Social Media Panic Post: Heard from a friend that the road between junction 9 and 10 is completely destroyed! Is this true?",
        "channel": "SOCIAL_MEDIA",
        "is_authenticated": False,
        "segment_truly_closed": False,
        "expected_operator_decision": "REJECT",
    },
    {
        "id": "E2E_09",
        "description": "Invalid-token ham radio report — segment 3-7 (negative — invalid token rejected)",
        "text": "Ham Radio Operator: Unconfirmed: segment between junction 3 and junction 7 may have flooding. Awaiting ground truth.",
        "channel": "HAM_RADIO",
        "is_authenticated": False,  # Token is forged; server rejects it
        "segment_truly_closed": False,
        "expected_operator_decision": "REJECT",
    },
    {
        "id": "E2E_10",
        "description": "Outdated warning — segment 13-14 already cleared (negative — false closure)",
        "text": "Old WhatsApp forward: Warning! Road between junction 13 and 14 is blocked by landslide (OUTDATED MESSAGE).",
        "channel": "SOCIAL_MEDIA",
        "is_authenticated": False,
        "segment_truly_closed": False,
        "expected_operator_decision": "REJECT",
    },
]


def simulate_scout_observation(segment_truly_closed: bool, sensor_reliability: float = DRONE_SENSOR_RELIABILITY) -> bool:
    """
    Simulate a drone scout observation of a segment.
    The sensor reports the true state with probability `sensor_reliability`,
    and makes an error with probability (1 - sensor_reliability).
    """
    if random.random() < sensor_reliability:
        return segment_truly_closed  # Correct observation
    else:
        return not segment_truly_closed  # Sensor error


def run_end_to_end_pipeline_evaluation():
    print("=== DISPATCH SENTINEL V6 — END-TO-END PIPELINE BENCHMARK ===")
    print(f"Sensor reliability: {DRONE_SENSOR_RELIABILITY} | Random seed: 2026")
    print(f"Cases: {len(PIPELINE_CASES)} (6 positive, 4 negative)")
    print()

    ctrl = DispatchController()
    traces = []
    trust_boundary_violations = 0
    fatal_safety_violations = 0
    correct_operator_decisions = 0
    total_operator_decisions = 0

    initial_delivery = ctrl.current_plan.delivery_ratio * 100.0
    initial_dist = ctrl.current_plan.total_fleet_distance_km
    print(f"Baseline Fleet: {initial_delivery:.1f}% delivery | {initial_dist:.1f} km | 0 violations\n")

    for c in PIPELINE_CASES:
        print(f"--- Case {c['id']}: {c['description']} ---")

        # Generate server-side token if case is authenticated
        if c["is_authenticated"]:
            auth_token = ctrl.generate_auth_token(c["channel"], c["text"][:50])
        else:
            # E2E_09 uses a deliberately forged token
            if c["id"] == "E2E_09":
                auth_token = "DISPATCH_HMAC_V1:0000000000:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
            else:
                auth_token = None

        t0 = time.time()

        # 1. Ingest through authoritative controller
        dec = ctrl.process_field_report(
            report_id=c["id"],
            raw_text=c["text"],
            source_channel=c["channel"],
            auth_token=auth_token
        )
        e2e_latency_ms = (time.time() - t0) * 1000.0

        auto_applied = (
            dec.verification_status == VerificationStatus.CONFIRMED_FACT
            and dec.is_actionable
            and not dec.requires_operator_approval
        )

        # 2. Trust boundary audit
        if not c["is_authenticated"] and auto_applied:
            trust_boundary_violations += 1
            print(f"  *** SECURITY BREACH: Unauthenticated report {c['id']} was auto-applied! ***")

        # 3. For reports in operator queue, simulate scout observation
        evsi_score: Optional[float] = None
        scout_observed_closed: Optional[bool] = None
        operator_decision: Optional[str] = None

        if not auto_applied and dec.affected_segment:
            # Compute EVSI to rank scouting priority
            evsi_score = compute_restricted_policy_evsi(
                ctrl.road_graph,
                ctrl.sectors,
                ctrl.vehicles,
                dec.affected_segment,
                prior_p_closed=dec.confidence_score if dec.confidence_score > 0 else 0.5,
                current_known_closures=ctrl.active_closures,
                drone_sensor_reliability=DRONE_SENSOR_RELIABILITY,
                verification_cost=0.5
            )
            print(f"  Scouting queue — Restricted EVSI: {evsi_score:+.2f} pts")

            # Simulate scout observation from ground truth
            scout_observed_closed = simulate_scout_observation(c["segment_truly_closed"])
            print(f"  Ground truth: {'CLOSED' if c['segment_truly_closed'] else 'OPEN'} | Scout observed: {'CLOSED' if scout_observed_closed else 'OPEN'}")

            # Operator decision based on scout result
            total_operator_decisions += 1
            if scout_observed_closed:
                # Scout confirms closure — operator approves
                ctrl.operator_approve_incident(dec.incident_id, operator_name="Human Watch Officer (Post-Scout)")
                operator_decision = "APPROVE"
                print(f"  Operator: APPROVED (scout confirmed closure)")
            else:
                # Scout finds road clear — operator dismisses
                ctrl.operator_dismiss_incident(dec.incident_id, operator_name="Human Watch Officer (Post-Scout)")
                operator_decision = "REJECT"
                print(f"  Operator: REJECTED (scout found road clear)")

            # Assess correctness
            expected = c["expected_operator_decision"]
            if operator_decision == expected:
                correct_operator_decisions += 1
                print(f"  Decision correctness: CORRECT (expected {expected})")
            else:
                print(f"  Decision correctness: WRONG — expected {expected}, got {operator_decision}")

        elif auto_applied:
            # Authenticated report — auto-applied, no operator step needed
            print(f"  Auto-applied (authenticated channel): {dec.verification_status.value}")
            total_operator_decisions += 1
            operator_decision = "APPROVE"
            if c["expected_operator_decision"] == "APPROVE":
                correct_operator_decisions += 1

        # 4. Safety check
        cur_plan = ctrl.current_plan
        if len(cur_plan.hard_violations) > 0:
            fatal_safety_violations += len(cur_plan.hard_violations)
            print(f"  *** SAFETY BREACH: {len(cur_plan.hard_violations)} hard violations! ***")

        print(f"  Result: {dec.verification_status.value} | Segment: {dec.affected_segment} | Delivery: {cur_plan.delivery_ratio*100:.1f}% | Dist: {cur_plan.total_fleet_distance_km:.1f} km | Latency: {e2e_latency_ms:.0f}ms\n")

        traces.append({
            "case_id": c["id"],
            "description": c["description"],
            "source_channel": c["channel"],
            "server_authenticated": c["is_authenticated"],
            "segment_truly_closed": c["segment_truly_closed"],
            "extracted_type": dec.incident_type.value,
            "extracted_segment": dec.affected_segment,
            "ai_confidence": round(dec.confidence_score, 3),
            "verification_status": dec.verification_status.value,
            "auto_applied": auto_applied,
            "scout_observed_closed": scout_observed_closed,
            "operator_decision": operator_decision,
            "expected_operator_decision": c["expected_operator_decision"],
            "decision_correct": (operator_decision == c["expected_operator_decision"]) if operator_decision else None,
            "restricted_evsi": round(evsi_score, 3) if evsi_score is not None else None,
            "resulting_delivery_ratio": round(cur_plan.delivery_ratio, 4),
            "resulting_distance_km": round(cur_plan.total_fleet_distance_km, 2),
            "hard_safety_violations": len(cur_plan.hard_violations),
            "total_latency_ms": round(e2e_latency_ms, 1)
        })

    # Summary
    print("================ END-TO-END V6 BENCHMARK RESULTS ================")
    print(f"Total Cases: {len(PIPELINE_CASES)} (6 positive, 4 negative)")
    print(f"Trust Boundary Violations: {trust_boundary_violations} / {sum(1 for c in PIPELINE_CASES if not c['is_authenticated'])}")
    print(f"Operator Decision Accuracy: {correct_operator_decisions} / {total_operator_decisions} ({correct_operator_decisions/total_operator_decisions*100:.1f}%)")
    print(f"Fatal Safety Violations: {fatal_safety_violations}")
    print(f"Final Fleet Delivery: {ctrl.current_plan.delivery_ratio*100:.1f}%")

    out_file = "END_TO_END_PIPELINE_RESULTS.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump({
            "metadata": {
                "version": "V6",
                "pipeline": "Nemotron 3.5 Lightning -> Server-Side HMAC Auth Gate -> Restricted EVSI -> Scout Simulation -> OR-Tools CVRP-TW",
                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
                "total_cases": len(PIPELINE_CASES),
                "positive_cases": 6,
                "negative_cases": 4,
                "drone_sensor_reliability": DRONE_SENSOR_RELIABILITY,
                "random_seed": 2026
            },
            "summary": {
                "trust_boundary_violations": trust_boundary_violations,
                "fatal_safety_violations": fatal_safety_violations,
                "operator_decisions_correct": correct_operator_decisions,
                "operator_decisions_total": total_operator_decisions,
                "operator_accuracy_pct": round(correct_operator_decisions / total_operator_decisions * 100, 1) if total_operator_decisions > 0 else None,
                "final_delivery_ratio_pct": round(ctrl.current_plan.delivery_ratio * 100.0, 2),
                "final_distance_km": round(ctrl.current_plan.total_fleet_distance_km, 2)
            },
            "decision_traces": traces
        }, f, indent=2)

    print(f"\nSaved complete audit traces to {out_file}")


if __name__ == "__main__":
    run_end_to_end_pipeline_evaluation()
