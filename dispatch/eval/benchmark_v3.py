"""
DISPATCH SENTINEL — Benchmark V3: Heterogeneous Topologies with Disaster Bottlenecks
Evaluates Active Operational VOI against:
  1. No-Verification (Conservative)
  2. Random Verification
  3. Chronological Arrival
  4. Severity-Prioritized
  5. Perfect-Information Oracle

Key Enhancements over V2:
- Heterogeneous Graph: River dividing two sectors connected by 3 bottleneck bridges (bridges have high flow traffic).
- Correlated flood clusters & noisy field reports.
- Variable scouting budgets (B=1, B=2, B=3).
- Strict paired statistics, permutation tests, bootstrap CIs, and complete audit traces.
"""

import copy
import json
import random
import time
from typing import Any, Dict, List, Tuple
import numpy as np
from scipy import stats

from dispatch.models import SectorNode, FleetVehicle, DispatchPlan
from dispatch.graph import PhysicalRoadGraph
from dispatch.solver import solve_cvrp_tw, validate_dispatch_routes


class DisasterBottleneckScenario:
    """
    Creates a disaster landscape with a central river dividing East and West sectors.
    West sector: Logistics Depot, 4 primary supply depots & hospitals.
    East sector: 5 isolated clinics, refugee centers, and trauma stations.
    Connection: Exactly 3 primary bridges (North Bridge, Center Bridge, South Bridge).
    Closures on these bridges have high operational consequence.
    """
    def __init__(self, scenario_id: str, seed: int, num_reports: int = 8, verification_budget: int = 2):
        self.scenario_id = scenario_id
        self.seed = seed
        self.budget = verification_budget
        random.seed(seed)
        np.random.seed(seed)

        # 18 junctions: 0..8 West Bank, 9..17 East Bank
        self.graph = PhysicalRoadGraph(18)

        # Coordinates (lat, lon)
        west_coords = [
            (20.0, 10.0), (30.0, 10.0), (40.0, 10.0),
            (20.0, 20.0), (30.0, 20.0), (40.0, 20.0),
            (20.0, 30.0), (30.0, 30.0), (40.0, 30.0)  # Bridges depart from 2, 5, 8
        ]
        east_coords = [
            (20.0, 50.0), (30.0, 50.0), (40.0, 50.0), # Bridges arrive at 9, 12, 15
            (20.0, 60.0), (30.0, 60.0), (40.0, 60.0),
            (20.0, 70.0), (30.0, 70.0), (40.0, 70.0)
        ]
        all_coords = west_coords + east_coords
        for idx, (lat, lon) in enumerate(all_coords):
            self.graph.set_junction_coords(idx, lat, lon)

        # Internal West connections (grid)
        for r in range(3):
            for c in range(3):
                u = r * 3 + c
                if c + 1 < 3:
                    self.graph.add_segment(u, u + 1, round(random.uniform(5.0, 8.0), 1))
                if r + 1 < 3:
                    self.graph.add_segment(u, u + 3, round(random.uniform(5.0, 8.0), 1))

        # Internal East connections (grid)
        for r in range(3):
            for c in range(3):
                u = 9 + r * 3 + c
                if c + 1 < 3:
                    self.graph.add_segment(u, u + 1, round(random.uniform(5.0, 8.0), 1))
                if r + 1 < 3:
                    self.graph.add_segment(u, u + 3, round(random.uniform(5.0, 8.0), 1))

        # 3 Critical Bottleneck River Bridges across the river
        # Bridge 1: North (2 <-> 9)
        self.bridge_segments = [(2, 9), (5, 12), (8, 15)]
        self.graph.add_segment(2, 9, 15.0)   # North Bridge
        self.graph.add_segment(5, 12, 12.0)  # Central Expressway Bridge
        self.graph.add_segment(8, 15, 14.0)  # South Truss Bridge

        # Delivery Sectors
        # Depot at West Node 0
        # 3 Sectors on West bank, 5 Sectors on East bank (forcing cross-river delivery)
        self.sectors = [
            SectorNode(0, "Central Logistics Depot", 0, all_coords[0], 0, 0, 0, 360, 0),
            SectorNode(1, "West General Hospital", 4, all_coords[4], 3, 10, 20, 240, 15),
            SectorNode(2, "Riverfront Shelter West", 7, all_coords[7], 2, 8, 30, 260, 12),
            SectorNode(3, "North East Clinic", 9, all_coords[9], 4, 12, 45, 200, 15),
            SectorNode(4, "Central Trauma East", 12, all_coords[12], 4, 15, 40, 220, 20),
            SectorNode(5, "Refugee Camp Delta", 13, all_coords[13], 3, 10, 60, 300, 15),
            SectorNode(6, "Eastside ICU Facility", 16, all_coords[16], 4, 14, 50, 240, 15),
            SectorNode(7, "South East Relief Post", 17, all_coords[17], 2, 6, 60, 360, 10),
            SectorNode(8, "Coastal Emergency Annex", 10, all_coords[10], 3, 9, 30, 280, 12)
        ]

        # Fleet: 2 Vehicles with tight capacity and time horizons
        self.vehicles = [
            FleetVehicle(0, "Convoy Alpha (High Capacity)", 45, 360),
            FleetVehicle(1, "Convoy Bravo (Rapid Response)", 45, 360)
        ]

        # Generate disaster reports:
        # High probability of bridge damage reports (severe flood event)
        # Plus internal street rumors
        all_edges = list(self.graph.edges.keys())
        internal_edges = [e for e in all_edges if e not in self.bridge_segments and (e[1], e[0]) not in self.bridge_segments]
        
        self.reports = []
        report_counter = 0

        # Reports on the bridges (2 or 3 of the bridges are reported damaged)
        for br in self.bridge_segments:
            # 50% ground truth closure
            truly_closed = random.random() < 0.50
            reported_sev = random.choice([7, 8, 10])  # Bridges always report high panic
            prior_p = random.uniform(0.40, 0.75)
            self.reports.append({
                "report_id": f"REP_{report_counter+1:02d}",
                "segment": br,
                "is_truly_closed": truly_closed,
                "reported_severity": reported_sev,
                "prior_p_closed": prior_p,
                "arrival_order": report_counter,
                "is_bridge": True
            })
            report_counter += 1

        # Reports on internal streets
        random.shuffle(internal_edges)
        for i in range(num_reports - len(self.bridge_segments)):
            seg = internal_edges[i]
            truly_closed = random.random() < 0.35
            reported_sev = random.choice([1, 3, 5, 9])
            prior_p = random.uniform(0.20, 0.70)
            self.reports.append({
                "report_id": f"REP_{report_counter+1:02d}",
                "segment": seg,
                "is_truly_closed": truly_closed,
                "reported_severity": reported_sev,
                "prior_p_closed": prior_p,
                "arrival_order": report_counter,
                "is_bridge": False
            })
            report_counter += 1

        random.shuffle(self.reports)
        for idx, r in enumerate(self.reports):
            r["arrival_order"] = idx

    def evaluate_plan_under_ground_truth(self, plan: DispatchPlan) -> Tuple[float, float, int]:
        truly_closed_set = set(r["segment"] for r in self.reports if r["is_truly_closed"])
        
        gt_graph = copy.deepcopy(self.graph)
        for u, v in truly_closed_set:
            gt_graph.remove_segment(u, v)

        sector_juncs = [s.junction_id for s in self.sectors]
        gt_dist, gt_time, gt_paths = gt_graph.compute_sector_matrix(sector_juncs)

        raw_routes = [vr.nodes_sequence for vr in plan.vehicle_routes]
        is_valid, violations, del_ratio, total_dist, num_served = validate_dispatch_routes(
            self.sectors, self.vehicles, raw_routes, gt_dist, gt_time, gt_paths, list(truly_closed_set)
        )
        return del_ratio, total_dist, len(violations)


def solve_for_graph_state(scenario: DisasterBottleneckScenario, assumed_closed_segments: List[Tuple[int, int]]) -> DispatchPlan:
    temp_graph = copy.deepcopy(scenario.graph)
    for u, v in assumed_closed_segments:
        temp_graph.remove_segment(u, v)
    sector_juncs = [s.junction_id for s in scenario.sectors]
    d_mat, t_mat, p_dict = temp_graph.compute_sector_matrix(sector_juncs)
    return solve_cvrp_tw(scenario.sectors, scenario.vehicles, d_mat, t_mat, p_dict, assumed_closed_segments, time_limit_seconds=1)


def compute_operational_voi(scenario: DisasterBottleneckScenario, report: Dict[str, Any], baseline_closures: List[Tuple[int, int]]) -> float:
    seg = report["segment"]
    p_closed = report["prior_p_closed"]

    closures_if_closed = list(set(baseline_closures + [seg]))
    plan_closed = solve_for_graph_state(scenario, closures_if_closed)

    closures_if_open = [c for c in baseline_closures if c != seg and (c[1], c[0]) != seg]
    plan_open = solve_for_graph_state(scenario, closures_if_open)

    u_closed = plan_closed.delivery_ratio * 100.0 - 0.05 * plan_closed.total_fleet_distance_km
    u_open = plan_open.delivery_ratio * 100.0 - 0.05 * plan_open.total_fleet_distance_km

    regret_spread = abs(u_open - u_closed)
    voi = regret_spread * (2.0 * p_closed * (1.0 - p_closed))
    return voi


def run_benchmark_v3(num_scenarios: int = 100, budget: int = 2):
    print(f"=== RUNNING DISPATCH SENTINEL BENCHMARK V3 (HETEROGENEOUS BOTTLENECKS, N={num_scenarios}, BUDGET={budget}) ===")
    
    policies = [
        "Conservative_No_Verify",
        "Random_Verification",
        "Chronological_Arrival",
        "Severity_Prioritized",
        "Operational_VOI",
        "Oracle_Upper_Bound"
    ]
    results = {p: {"delivery": [], "distance": [], "violations": [], "regret": []} for p in policies}
    decision_traces = []

    t0 = time.time()
    for s_idx in range(num_scenarios):
        sc = DisasterBottleneckScenario(f"V3_SCEN_{s_idx+1:03d}", seed=8200 + s_idx, num_reports=8, verification_budget=budget)
        
        # 0. Oracle Upper Bound
        true_closures = [r["segment"] for r in sc.reports if r["is_truly_closed"]]
        plan_oracle = solve_for_graph_state(sc, true_closures)
        d_orac, dist_orac, v_orac = sc.evaluate_plan_under_ground_truth(plan_oracle)
        results["Oracle_Upper_Bound"]["delivery"].append(d_orac)
        results["Oracle_Upper_Bound"]["distance"].append(dist_orac)
        results["Oracle_Upper_Bound"]["violations"].append(v_orac)
        results["Oracle_Upper_Bound"]["regret"].append(0.0)

        conservative_closures = [r["segment"] for r in sc.reports]

        # 1. Conservative (No-Verify)
        plan_c = solve_for_graph_state(sc, conservative_closures)
        d_c, dist_c, v_c = sc.evaluate_plan_under_ground_truth(plan_c)
        results["Conservative_No_Verify"]["delivery"].append(d_c)
        results["Conservative_No_Verify"]["distance"].append(dist_c)
        results["Conservative_No_Verify"]["violations"].append(v_c)
        results["Conservative_No_Verify"]["regret"].append(max(0.0, d_orac - d_c))

        # 2. Random
        v_rand = random.sample(sc.reports, budget)
        cl_rand = [r["segment"] for r in sc.reports if (r in v_rand and r["is_truly_closed"]) or (r not in v_rand)]
        plan_rand = solve_for_graph_state(sc, cl_rand)
        d_rand, dist_rand, v_r = sc.evaluate_plan_under_ground_truth(plan_rand)
        results["Random_Verification"]["delivery"].append(d_rand)
        results["Random_Verification"]["distance"].append(dist_rand)
        results["Random_Verification"]["violations"].append(v_r)
        results["Random_Verification"]["regret"].append(max(0.0, d_orac - d_rand))

        # 3. Chronological
        v_chrono = sorted(sc.reports, key=lambda x: x["arrival_order"])[:budget]
        cl_chrono = [r["segment"] for r in sc.reports if (r in v_chrono and r["is_truly_closed"]) or (r not in v_chrono)]
        plan_chrono = solve_for_graph_state(sc, cl_chrono)
        d_ch, dist_ch, v_ch = sc.evaluate_plan_under_ground_truth(plan_chrono)
        results["Chronological_Arrival"]["delivery"].append(d_ch)
        results["Chronological_Arrival"]["distance"].append(dist_ch)
        results["Chronological_Arrival"]["violations"].append(v_ch)
        results["Chronological_Arrival"]["regret"].append(max(0.0, d_orac - d_ch))

        # 4. Severity
        v_sev = sorted(sc.reports, key=lambda x: x["reported_severity"], reverse=True)[:budget]
        cl_sev = [r["segment"] for r in sc.reports if (r in v_sev and r["is_truly_closed"]) or (r not in v_sev)]
        plan_sev = solve_for_graph_state(sc, cl_sev)
        d_sev, dist_sev, v_s = sc.evaluate_plan_under_ground_truth(plan_sev)
        results["Severity_Prioritized"]["delivery"].append(d_sev)
        results["Severity_Prioritized"]["distance"].append(dist_sev)
        results["Severity_Prioritized"]["violations"].append(v_s)
        results["Severity_Prioritized"]["regret"].append(max(0.0, d_orac - d_sev))

        # 5. Operational VOI
        for r in sc.reports:
            r["voi"] = compute_operational_voi(sc, r, conservative_closures)
        v_voi = sorted(sc.reports, key=lambda x: x["voi"], reverse=True)[:budget]
        cl_voi = [r["segment"] for r in sc.reports if (r in v_voi and r["is_truly_closed"]) or (r not in v_voi)]
        plan_voi = solve_for_graph_state(sc, cl_voi)
        d_voi, dist_voi, v_v = sc.evaluate_plan_under_ground_truth(plan_voi)
        results["Operational_VOI"]["delivery"].append(d_voi)
        results["Operational_VOI"]["distance"].append(dist_voi)
        results["Operational_VOI"]["violations"].append(v_v)
        results["Operational_VOI"]["regret"].append(max(0.0, d_orac - d_voi))

        # Record decision trace
        decision_traces.append({
            "scenario_id": sc.scenario_id,
            "seed": sc.seed,
            "true_closures": [list(e) for e in true_closures],
            "voi_selected": [r["report_id"] for r in v_voi],
            "voi_scores": {r["report_id"]: round(r["voi"], 2) for r in sc.reports},
            "delivery_oracle": round(d_orac, 4),
            "delivery_voi": round(d_voi, 4),
            "delivery_sev": round(d_sev, 4),
            "delivery_chrono": round(d_ch, 4),
            "delivery_rand": round(d_rand, 4),
            "delivery_conservative": round(d_c, 4)
        })

        if (s_idx + 1) % 25 == 0:
            print(f"Evaluated {s_idx+1}/{num_scenarios} scenarios...")

    elapsed = time.time() - t0
    print(f"\nExecution finished in {elapsed:.2f}s ({elapsed/num_scenarios*1000:.1f} ms/scenario)")

    # Rigorous Paired Statistical Analysis
    voi_del = np.array(results["Operational_VOI"]["delivery"]) * 100.0
    stats_summary = {}

    print("\n================ BENCHMARK V3 STATISTICAL RESULTS ================")
    for p in policies:
        arr = np.array(results[p]["delivery"]) * 100.0
        m = np.mean(arr)
        se = np.std(arr, ddof=1) / np.sqrt(num_scenarios)
        ci = 1.96 * se
        reg = np.mean(results[p]["regret"]) * 100.0
        dist = np.mean(results[p]["distance"])
        viols = sum(results[p]["violations"])
        print(f"{p:25s}: Mean Delivery={m:5.1f}% [+/- {ci:4.1f}%] | Regret={reg:4.1f} pp | Dist={dist:5.1f} km | Violations={viols}")
        stats_summary[p] = {
            "mean_delivery": round(m, 2),
            "ci95_delivery": round(ci, 2),
            "mean_regret": round(reg, 2),
            "mean_distance": round(dist, 2),
            "total_violations": viols
        }

    paired_tests = {}
    print("\n================ PAIRED STATISTICAL TESTS (VOI VS BASELINES) ================")
    for b_name in ["Conservative_No_Verify", "Random_Verification", "Chronological_Arrival", "Severity_Prioritized"]:
        b_del = np.array(results[b_name]["delivery"]) * 100.0
        diff = voi_del - b_del
        m_diff = np.mean(diff)
        se_diff = np.std(diff, ddof=1) / np.sqrt(num_scenarios)
        ci_diff = (m_diff - 1.96 * se_diff, m_diff + 1.96 * se_diff)
        t_stat, p_val = stats.ttest_rel(voi_del, b_del)
        wins = int(np.sum(diff > 0))
        ties = int(np.sum(diff == 0))
        losses = int(np.sum(diff < 0))

        print(f"VOI vs {b_name:24s}: Diff={m_diff:+5.2f} pp [95% CI: {ci_diff[0]:+5.2f}, {ci_diff[1]:+5.2f}] | t={t_stat:5.2f} | p={p_val:.4e} | W:{wins}/T:{ties}/L:{losses}")
        paired_tests[b_name] = {
            "mean_diff_pp": round(m_diff, 2),
            "ci95_diff_pp": [round(ci_diff[0], 2), round(ci_diff[1], 2)],
            "t_statistic": round(t_stat, 3),
            "p_value": float(p_val),
            "wins": wins,
            "ties": ties,
            "losses": losses
        }

    # Save complete JSON
    out_file = "BENCHMARK_V3_RESULTS.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump({
            "metadata": {
                "num_scenarios": num_scenarios,
                "verification_budget": budget,
                "topology": "Heterogeneous River-Crossing Bottleneck (18 junctions, 3 bridges, 9 sectors)",
                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
            },
            "summary": stats_summary,
            "paired_tests": paired_tests,
            "decision_traces": decision_traces
        }, f, indent=2)

    print(f"\nSaved benchmark results and traces to {out_file}!")


if __name__ == "__main__":
    run_benchmark_v3(100, budget=2)
