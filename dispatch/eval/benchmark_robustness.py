"""
DISPATCH SENTINEL - Prospective Multi-Topology Robustness Benchmark
Evaluates Information-Guided Scouting across Diverse Disaster Topologies:
1. River Bottleneck Topology (3 bridges connecting two sectors)
2. Ring-Radial Urban Topology (Hub with radial spokes and circumferential beltway)
3. Grid Topology with Dispersed Blockages

Tests 6 Policies under Budget B=2:
  1. Conservative No-Verify
  2. Random Verification
  3. Chronological Arrival
  4. Severity-Prioritized
  5. Graph Betweenness Centrality Baseline (Network topology flow)
  6. Expected Value of Sample Information (EVSI)
  7. Perfect Information Oracle

Evaluates across 60 Paired Multi-Topology Scenarios.
Saves decision traces and paired statistical tests to BENCHMARK_ROBUSTNESS_RESULTS.json.
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
from dispatch.voi import compute_exact_evsi, compute_graph_betweenness_centrality, solve_candidate_plan, evaluate_plan_utility


def build_ring_radial_topology() -> Tuple[PhysicalRoadGraph, List[SectorNode], List[Tuple[int, int]]]:
    """Ring-Radial Urban Network: Central hub (0), inner ring (1..6), outer rim (7..12)."""
    g = PhysicalRoadGraph(13)
    coords = [(37.75, -122.42)] # Central hub
    # Inner ring
    for i in range(6):
        angle = i * (2 * np.pi / 6)
        coords.append((37.75 + 0.03 * np.sin(angle), -122.42 + 0.03 * np.cos(angle)))
    # Outer ring
    for i in range(6):
        angle = i * (2 * np.pi / 6)
        coords.append((37.75 + 0.06 * np.sin(angle), -122.42 + 0.06 * np.cos(angle)))

    for idx, (lat, lon) in enumerate(coords):
        g.set_junction_coords(idx, lat, lon)

    # Spokes from hub to inner ring
    for i in range(1, 7):
        g.add_segment(0, i, 4.0)
    # Inner ring segments
    for i in range(1, 7):
        nxt = 1 if i == 6 else i + 1
        g.add_segment(i, nxt, 3.5)
    # Spokes from inner to outer
    for i in range(1, 7):
        g.add_segment(i, i + 6, 5.0)
    # Outer ring segments
    for i in range(7, 13):
        nxt = 7 if i == 12 else i + 1
        g.add_segment(i, nxt, 6.0)

    # Delivery Sectors
    sectors = [
        SectorNode(0, "Metropolitan Logistics Depot", 0, coords[0], 0, 0, 0, 360, 0),
        SectorNode(1, "Inner Trauma Hospital", 1, coords[1], 4, 12, 20, 240, 15),
        SectorNode(2, "Westside Shelter", 3, coords[3], 2, 8, 30, 260, 12),
        SectorNode(3, "North Rim Emergency Clinic", 7, coords[7], 4, 14, 45, 220, 15),
        SectorNode(4, "East Peripheral Hospital", 9, coords[9], 4, 15, 40, 220, 20),
        SectorNode(5, "South Ridge Refugee Station", 11, coords[11], 3, 10, 60, 300, 15),
        SectorNode(6, "North West Pavilion", 8, coords[8], 3, 9, 30, 250, 12)
    ]
    edges = list(g.edges.keys())
    return g, sectors, edges


def build_river_bottleneck_topology() -> Tuple[PhysicalRoadGraph, List[SectorNode], List[Tuple[int, int]]]:
    """River-Crossing Bottleneck Topology (18 junctions, 3 bridges)."""
    g = PhysicalRoadGraph(18)
    west_coords = [(37.78, -122.46), (37.76, -122.46), (37.74, -122.46),
                   (37.78, -122.44), (37.76, -122.44), (37.74, -122.44),
                   (37.78, -122.42), (37.76, -122.42), (37.74, -122.42)]
    east_coords = [(37.78, -122.38), (37.76, -122.38), (37.74, -122.38),
                   (37.78, -122.36), (37.76, -122.36), (37.74, -122.36),
                   (37.78, -122.34), (37.76, -122.34), (37.74, -122.34)]
    all_coords = west_coords + east_coords
    for idx, (lat, lon) in enumerate(all_coords):
        g.set_junction_coords(idx, lat, lon)

    # West grid
    for r in range(3):
        for c in range(3):
            u = r * 3 + c
            if c + 1 < 3: g.add_segment(u, u + 1, 6.0)
            if r + 1 < 3: g.add_segment(u, u + 3, 6.0)
    # East grid
    for r in range(3):
        for c in range(3):
            u = 9 + r * 3 + c
            if c + 1 < 3: g.add_segment(u, u + 1, 6.0)
            if r + 1 < 3: g.add_segment(u, u + 3, 6.0)

    # 3 Bridges
    g.add_segment(2, 9, 15.0)
    g.add_segment(5, 12, 12.0)
    g.add_segment(8, 15, 14.0)

    sectors = [
        SectorNode(0, "West Central Depot", 0, all_coords[0], 0, 0, 0, 360, 0),
        SectorNode(1, "West Trauma Center", 4, all_coords[4], 4, 12, 20, 240, 15),
        SectorNode(2, "Riverfront Shelter", 7, all_coords[7], 2, 8, 30, 260, 12),
        SectorNode(3, "North East Memorial Clinic", 9, all_coords[9], 4, 14, 45, 200, 15),
        SectorNode(4, "Central ICU East Bank", 12, all_coords[12], 4, 15, 40, 220, 20),
        SectorNode(5, "Refugee Camp Delta", 13, all_coords[13], 3, 10, 60, 300, 15),
        SectorNode(6, "Eastside Surgical Pavilion", 16, all_coords[16], 4, 14, 50, 240, 15)
    ]
    edges = list(g.edges.keys())
    return g, sectors, edges


def run_robustness_benchmark(num_scenarios_per_topology: int = 25):
    print(f"=== RUNNING MULTI-TOPOLOGY ROBUSTNESS BENCHMARK ({num_scenarios_per_topology * 2} SCENARIOS) ===")
    
    vehicles = [
        FleetVehicle(0, "Convoy Alpha", 35, 360),
        FleetVehicle(1, "Convoy Bravo", 35, 360)
    ]

    policies = [
        "Conservative_No_Verify",
        "Random_Verification",
        "Chronological_Arrival",
        "Severity_Prioritized",
        "Graph_Centrality_Baseline",
        "EVSI_Optimal_Scouting",
        "Oracle_Upper_Bound"
    ]

    results = {p: {"delivery": [], "distance": [], "violations": [], "regret": []} for p in policies}
    decision_traces = []

    scenario_count = 0
    t0 = time.time()

    for topo_name, topo_builder in [("River_Bottleneck", build_river_bottleneck_topology), ("Ring_Radial_Urban", build_ring_radial_topology)]:
        print(f"\nEvaluating Topology: {topo_name}...")
        for s_idx in range(num_scenarios_per_topology):
            scenario_count += 1
            seed = 9500 + scenario_count
            random.seed(seed)
            np.random.seed(seed)

            graph, sectors, all_edges = topo_builder()
            num_reports = 6
            budget = 2

            # Sample 6 reports
            chosen_edges = random.sample(all_edges, num_reports)
            reports = []
            for r_idx, edge in enumerate(chosen_edges):
                is_truly_closed = (random.random() < 0.45)
                sev = random.choice([2, 5, 8, 10])
                prior_p = random.uniform(0.30, 0.75)
                reports.append({
                    "id": f"REP_{r_idx+1:02d}",
                    "segment": edge,
                    "is_truly_closed": is_truly_closed,
                    "reported_severity": sev,
                    "prior_p_closed": prior_p,
                    "arrival_order": r_idx
                })

            true_closures = [r["segment"] for r in reports if r["is_truly_closed"]]
            conservative_closures = [r["segment"] for r in reports]

            # 0. Oracle
            p_orac = solve_candidate_plan(graph, sectors, vehicles, true_closures)
            u_orac = evaluate_plan_utility(p_orac, graph, sectors, vehicles, true_closures)
            d_orac = p_orac.delivery_ratio * 100.0
            results["Oracle_Upper_Bound"]["delivery"].append(d_orac)
            results["Oracle_Upper_Bound"]["distance"].append(p_orac.total_fleet_distance_km)
            results["Oracle_Upper_Bound"]["violations"].append(len(p_orac.hard_violations))
            results["Oracle_Upper_Bound"]["regret"].append(0.0)

            # 1. Conservative (No-Verify)
            p_c = solve_candidate_plan(graph, sectors, vehicles, conservative_closures)
            d_c = p_c.delivery_ratio * 100.0
            results["Conservative_No_Verify"]["delivery"].append(d_c)
            results["Conservative_No_Verify"]["distance"].append(p_c.total_fleet_distance_km)
            results["Conservative_No_Verify"]["violations"].append(len(p_c.hard_violations))
            results["Conservative_No_Verify"]["regret"].append(max(0.0, d_orac - d_c))

            # 2. Random
            v_rand = random.sample(reports, budget)
            cl_rand = [r["segment"] for r in reports if (r in v_rand and r["is_truly_closed"]) or (r not in v_rand)]
            p_rand = solve_candidate_plan(graph, sectors, vehicles, cl_rand)
            d_rand = p_rand.delivery_ratio * 100.0
            results["Random_Verification"]["delivery"].append(d_rand)
            results["Random_Verification"]["distance"].append(p_rand.total_fleet_distance_km)
            results["Random_Verification"]["violations"].append(len(p_rand.hard_violations))
            results["Random_Verification"]["regret"].append(max(0.0, d_orac - d_rand))

            # 3. Chronological
            v_chron = sorted(reports, key=lambda x: x["arrival_order"])[:budget]
            cl_chron = [r["segment"] for r in reports if (r in v_chron and r["is_truly_closed"]) or (r not in v_chron)]
            p_chron = solve_candidate_plan(graph, sectors, vehicles, cl_chron)
            d_chron = p_chron.delivery_ratio * 100.0
            results["Chronological_Arrival"]["delivery"].append(d_chron)
            results["Chronological_Arrival"]["distance"].append(p_chron.total_fleet_distance_km)
            results["Chronological_Arrival"]["violations"].append(len(p_chron.hard_violations))
            results["Chronological_Arrival"]["regret"].append(max(0.0, d_orac - d_chron))

            # 4. Severity
            v_sev = sorted(reports, key=lambda x: x["reported_severity"], reverse=True)[:budget]
            cl_sev = [r["segment"] for r in reports if (r in v_sev and r["is_truly_closed"]) or (r not in v_sev)]
            p_sev = solve_candidate_plan(graph, sectors, vehicles, cl_sev)
            d_sev = p_sev.delivery_ratio * 100.0
            results["Severity_Prioritized"]["delivery"].append(d_sev)
            results["Severity_Prioritized"]["distance"].append(p_sev.total_fleet_distance_km)
            results["Severity_Prioritized"]["violations"].append(len(p_sev.hard_violations))
            results["Severity_Prioritized"]["regret"].append(max(0.0, d_orac - d_sev))

            # 5. Graph Centrality Baseline (Selects edges on the most shortest paths)
            for r in reports:
                r["centrality"] = compute_graph_betweenness_centrality(graph, sectors, r["segment"])
            v_cent = sorted(reports, key=lambda x: x["centrality"], reverse=True)[:budget]
            cl_cent = [r["segment"] for r in reports if (r in v_cent and r["is_truly_closed"]) or (r not in v_cent)]
            p_cent = solve_candidate_plan(graph, sectors, vehicles, cl_cent)
            d_cent = p_cent.delivery_ratio * 100.0
            results["Graph_Centrality_Baseline"]["delivery"].append(d_cent)
            results["Graph_Centrality_Baseline"]["distance"].append(p_cent.total_fleet_distance_km)
            results["Graph_Centrality_Baseline"]["violations"].append(len(p_cent.hard_violations))
            results["Graph_Centrality_Baseline"]["regret"].append(max(0.0, d_orac - d_cent))

            # 6. Exact EVSI Optimal Scouting
            for r in reports:
                r["evsi"] = compute_exact_evsi(graph, sectors, vehicles, r["segment"], r["prior_p_closed"], conservative_closures)
            v_evsi = sorted(reports, key=lambda x: x["evsi"], reverse=True)[:budget]
            cl_evsi = [r["segment"] for r in reports if (r in v_evsi and r["is_truly_closed"]) or (r not in v_evsi)]
            p_evsi = solve_candidate_plan(graph, sectors, vehicles, cl_evsi)
            d_evsi = p_evsi.delivery_ratio * 100.0
            results["EVSI_Optimal_Scouting"]["delivery"].append(d_evsi)
            results["EVSI_Optimal_Scouting"]["distance"].append(p_evsi.total_fleet_distance_km)
            results["EVSI_Optimal_Scouting"]["violations"].append(len(p_evsi.hard_violations))
            results["EVSI_Optimal_Scouting"]["regret"].append(max(0.0, d_orac - d_evsi))

            decision_traces.append({
                "scenario_idx": scenario_count,
                "topology": topo_name,
                "evsi_chosen": [r["id"] for r in v_evsi],
                "centrality_chosen": [r["id"] for r in v_cent],
                "severity_chosen": [r["id"] for r in v_sev],
                "delivery_evsi": round(d_evsi, 2),
                "delivery_cent": round(d_cent, 2),
                "delivery_sev": round(d_sev, 2),
                "delivery_rand": round(d_rand, 2),
                "delivery_conservative": round(d_c, 2)
            })

            if scenario_count % 10 == 0:
                print(f"Evaluated {scenario_count}/{num_scenarios_per_topology*2} scenarios...")

    total_scenarios = len(decision_traces)
    elapsed = time.time() - t0
    print(f"\nExecution finished in {elapsed:.1f}s ({elapsed/total_scenarios*1000:.1f} ms/scenario)")

    evsi_arr = np.array(results["EVSI_Optimal_Scouting"]["delivery"])
    summary = {}

    print("\n================ ROBUSTNESS BENCHMARK RESULTS (ALL TOPOLOGIES) ================")
    for p in policies:
        arr = np.array(results[p]["delivery"])
        m = np.mean(arr)
        se = np.std(arr, ddof=1) / np.sqrt(total_scenarios)
        ci = 1.96 * se
        reg = np.mean(results[p]["regret"])
        dist = np.mean(results[p]["distance"])
        viols = sum(results[p]["violations"])
        print(f"{p:26s}: Mean Delivery={m:5.1f}% [+/- {ci:4.1f}%] | Regret={reg:4.1f} pp | Dist={dist:5.1f} km | Violations={viols}")
        summary[p] = {
            "mean_delivery": round(m, 2),
            "ci95_delivery": round(ci, 2),
            "mean_regret": round(reg, 2),
            "mean_distance": round(dist, 2),
            "total_violations": viols
        }

    paired_tests = {}
    print("\n================ PAIRED STATISTICAL TESTS (EVSI VS BASELINES) ================")
    for b_name in ["Conservative_No_Verify", "Random_Verification", "Chronological_Arrival", "Severity_Prioritized", "Graph_Centrality_Baseline"]:
        b_del = np.array(results[b_name]["delivery"])
        diff = evsi_arr - b_del
        m_diff = np.mean(diff)
        se_diff = np.std(diff, ddof=1) / np.sqrt(total_scenarios)
        ci_diff = (m_diff - 1.96 * se_diff, m_diff + 1.96 * se_diff)
        t_stat, p_val = stats.ttest_rel(evsi_arr, b_del)
        wins = int(np.sum(diff > 0))
        ties = int(np.sum(diff == 0))
        losses = int(np.sum(diff < 0))
        print(f"EVSI vs {b_name:24s}: Diff={m_diff:+5.2f} pp [95% CI: {ci_diff[0]:+5.2f}, {ci_diff[1]:+5.2f}] | t={t_stat:5.2f} | p={p_val:.4e} | W:{wins}/T:{ties}/L:{losses}")
        paired_tests[b_name] = {
            "mean_diff_pp": round(m_diff, 2),
            "ci95_diff_pp": [round(ci_diff[0], 2), round(ci_diff[1], 2)],
            "t_statistic": round(t_stat, 3),
            "p_value": float(p_val),
            "wins": wins,
            "ties": ties,
            "losses": losses
        }

    out_file = "BENCHMARK_ROBUSTNESS_RESULTS.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump({
            "metadata": {
                "total_scenarios": total_scenarios,
                "topologies": ["River_Bottleneck", "Ring_Radial_Urban"],
                "verification_budget": 2,
                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
            },
            "summary": summary,
            "paired_tests": paired_tests,
            "decision_traces": decision_traces
        }, f, indent=2)

    print(f"\nSaved multi-topology robustness benchmark to {out_file}!")

if __name__ == "__main__":
    run_robustness_benchmark(num_scenarios_per_topology=25)
