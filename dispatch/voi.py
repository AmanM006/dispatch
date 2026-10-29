"""
DISPATCH SENTINEL - Rigorous Decision-Theoretic Value of Information (VOI) Engine
Formally implements:
1. Expected Value of Sample Information (EVSI):
   EVSI = E_y [ max_a E_{x|y} [ U(a, x) ] ] - max_a E_x [ U(a, x) ]
   where:
   - x in {OPEN, CLOSED} is the true physical state of the road segment.
   - y in {OBS_OPEN, OBS_CLOSED} is the scouting observation.
   - a in Plans is the CVRP-TW dispatch plan selected.
   - U(a, x) is the fleet operational utility under ground truth x:
     U(a, x) = Delivery_Ratio(a, x) * 100.0 - 0.05 * Distance_km(a, x) - 1000 * Fatal_Violations(a, x)
2. Fast Operational Impact Regret Spread (O-VOI Heuristic):
   O-VOI = abs( U(Plan_{OPEN}) - U(Plan_{CLOSED}) ) * 2 * p * (1 - p)
3. Graph Centrality Baseline (Betweenness Centrality & Flow Importance):
   Centrality = sum of shortest sector-to-sector paths traversing this segment.
"""

from typing import Dict, List, Tuple, Any, Optional
import copy
import numpy as np

from dispatch.models import SectorNode, FleetVehicle, DispatchPlan
from dispatch.graph import PhysicalRoadGraph
from dispatch.solver import solve_cvrp_tw, validate_dispatch_routes


def solve_candidate_plan(
    road_graph: PhysicalRoadGraph,
    sectors: List[SectorNode],
    vehicles: List[FleetVehicle],
    assumed_closed_segments: List[Tuple[int, int]]
) -> DispatchPlan:
    """Computes CVRP-TW optimal dispatch plan given assumed closures."""
    temp_g = copy.deepcopy(road_graph)
    for u, v in assumed_closed_segments:
        temp_g.remove_segment(u, v)
    sector_juncs = [s.junction_id for s in sectors]
    d_mat, t_mat, p_dict = temp_g.compute_sector_matrix(sector_juncs)
    return solve_cvrp_tw(sectors, vehicles, d_mat, t_mat, p_dict, assumed_closed_segments, time_limit_seconds=1)


def evaluate_plan_utility(
    plan: DispatchPlan,
    road_graph: PhysicalRoadGraph,
    sectors: List[SectorNode],
    vehicles: List[FleetVehicle],
    true_closures: List[Tuple[int, int]]
) -> float:
    """
    Evaluates real-world utility of a planned route under actual ground-truth road closures.
    Penalizes failed delivery, travel distance, and heavily penalizes fatal impassable obstructions.
    """
    temp_g = copy.deepcopy(road_graph)
    true_closed_set = set(true_closures)
    for u, v in true_closures:
        temp_g.remove_segment(u, v)

    sector_juncs = [s.junction_id for s in sectors]
    gt_dist, gt_time, gt_paths = temp_g.compute_sector_matrix(sector_juncs)

    raw_routes = [vr.nodes_sequence for vr in plan.vehicle_routes]
    is_valid, violations, del_ratio, total_dist, num_served = validate_dispatch_routes(
        sectors, vehicles, raw_routes, gt_dist, gt_time, gt_paths, list(true_closed_set)
    )

    # Operational utility function
    # Delivery ratio (0-100) - distance cost - fatal barrier penalty
    utility = (del_ratio * 100.0) - (0.05 * total_dist) - (1000.0 * len(violations))
    return utility


def compute_restricted_policy_evsi(
    road_graph: PhysicalRoadGraph,
    sectors: List[SectorNode],
    vehicles: List[FleetVehicle],
    candidate_segment: Tuple[int, int],
    prior_p_closed: float,
    current_known_closures: List[Tuple[int, int]],
    drone_sensor_reliability: float = 0.98,
    verification_cost: float = 0.0
) -> float:
    """
    Computes Expected Value of Sample Information under a Restricted-Policy Class (Restricted EVSI):
    
    FORMAL SPECIFICATION & MATHEMATICAL BOUNDARIES:
    - Prior Decision: The fleet executes the conservative safety policy (plan_prior), treating the unverified
      candidate segment as closed to guarantee zero fatalities.
    - Post-Observation Policy:
        - If scout observes CLOSED (y = OBS_CLOSED): The fleet executes plan_if_obs_c (conditioned on segment closed).
        - If scout observes OPEN (y = OBS_OPEN): The fleet executes plan_if_obs_o (conditioned on segment open).
    - Sensor Model: Binary symmetric channel with drone sensor reliability eta (default 0.98).
    - Net Value of Information:
        Net_EVSI = E_y [ Expected_Utility(Plan_{y}) ] - Expected_Utility(Plan_{prior}) - verification_cost
      NOTE: Net EVSI is NOT artificially clipped to zero; it can be negative if verification cost exceeds utility gain.
    
    LIMITATION NOTICE: This calculates EVSI restricted to the three canonical policy candidates (conservative base,
    optimal plan given closed, optimal plan given open). It does NOT perform unconstrained global dynamic programming
    over all combinatorial sub-tour partitions.
    """
    p_c = float(np.clip(prior_p_closed, 0.001, 0.999))
    p_o = 1.0 - p_c
    seg = (min(candidate_segment), max(candidate_segment))

    # Base plan without scouting (Conservative disaster safety: unverified roads avoided)
    base_plan = solve_candidate_plan(road_graph, sectors, vehicles, list(set(current_known_closures + [seg])))
    
    # Expected utility of base plan under true states
    u_base_if_closed = evaluate_plan_utility(base_plan, road_graph, sectors, vehicles, list(set(current_known_closures + [seg])))
    u_base_if_open = evaluate_plan_utility(base_plan, road_graph, sectors, vehicles, current_known_closures)
    expected_u_prior = (p_c * u_base_if_closed) + (p_o * u_base_if_open)

    # Observation probabilities via sensor reliability eta
    eta = float(np.clip(drone_sensor_reliability, 0.5, 1.0))
    p_obs_closed = (p_c * eta) + (p_o * (1.0 - eta))
    p_obs_open = 1.0 - p_obs_closed

    # Exact Bayes update of posterior probabilities
    p_c_given_obs_c = (p_c * eta) / max(1e-7, p_obs_closed)
    p_c_given_obs_o = (p_c * (1.0 - eta)) / max(1e-7, p_obs_open)

    # Policy if scout reports CLOSED:
    plan_if_obs_c = solve_candidate_plan(road_graph, sectors, vehicles, list(set(current_known_closures + [seg])))
    u_obs_c_if_closed = evaluate_plan_utility(plan_if_obs_c, road_graph, sectors, vehicles, list(set(current_known_closures + [seg])))
    u_obs_c_if_open = evaluate_plan_utility(plan_if_obs_c, road_graph, sectors, vehicles, current_known_closures)
    expected_u_given_obs_c = (p_c_given_obs_c * u_obs_c_if_closed) + ((1.0 - p_c_given_obs_c) * u_obs_c_if_open)

    # Policy if scout reports OPEN:
    open_closures = [c for c in current_known_closures if c != seg and (c[1], c[0]) != seg]
    plan_if_obs_o = solve_candidate_plan(road_graph, sectors, vehicles, open_closures)
    u_obs_o_if_closed = evaluate_plan_utility(plan_if_obs_o, road_graph, sectors, vehicles, list(set(current_known_closures + [seg])))
    u_obs_o_if_open = evaluate_plan_utility(plan_if_obs_o, road_graph, sectors, vehicles, current_known_closures)
    expected_u_given_obs_o = (p_c_given_obs_o * u_obs_o_if_closed) + ((1.0 - p_c_given_obs_o) * u_obs_o_if_open)

    # Total expected utility with scouting
    expected_u_with_scouting = (p_obs_closed * expected_u_given_obs_c) + (p_obs_open * expected_u_given_obs_o)

    # Net EVSI (can be negative if verification_cost > operational gain)
    net_evsi = (expected_u_with_scouting - expected_u_prior) - verification_cost
    return net_evsi


def compute_restricted_policy_evsi_breakdown(
    road_graph: PhysicalRoadGraph,
    sectors: List[SectorNode],
    vehicles: List[FleetVehicle],
    candidate_segment: Tuple[int, int],
    prior_p_closed: float,
    current_known_closures: List[Tuple[int, int]],
    drone_sensor_reliability: float = 0.98,
    verification_cost: float = 0.0
) -> Dict[str, Any]:
    """
    Computes Restricted-Policy EVSI and returns the exact mathematical breakdown of all intermediate terms
    for the live decision inspector.
    """
    p_c = float(np.clip(prior_p_closed, 0.001, 0.999))
    p_o = 1.0 - p_c
    seg = (min(candidate_segment), max(candidate_segment))

    base_plan = solve_candidate_plan(road_graph, sectors, vehicles, list(set(current_known_closures + [seg])))
    u_base_if_closed = evaluate_plan_utility(base_plan, road_graph, sectors, vehicles, list(set(current_known_closures + [seg])))
    u_base_if_open = evaluate_plan_utility(base_plan, road_graph, sectors, vehicles, current_known_closures)
    expected_u_prior = (p_c * u_base_if_closed) + (p_o * u_base_if_open)

    eta = float(np.clip(drone_sensor_reliability, 0.5, 1.0))
    p_obs_closed = (p_c * eta) + (p_o * (1.0 - eta))
    p_obs_open = 1.0 - p_obs_closed

    p_c_given_obs_c = (p_c * eta) / max(1e-7, p_obs_closed)
    p_c_given_obs_o = (p_c * (1.0 - eta)) / max(1e-7, p_obs_open)

    plan_if_obs_c = solve_candidate_plan(road_graph, sectors, vehicles, list(set(current_known_closures + [seg])))
    u_obs_c_if_closed = evaluate_plan_utility(plan_if_obs_c, road_graph, sectors, vehicles, list(set(current_known_closures + [seg])))
    u_obs_c_if_open = evaluate_plan_utility(plan_if_obs_c, road_graph, sectors, vehicles, current_known_closures)
    expected_u_given_obs_c = (p_c_given_obs_c * u_obs_c_if_closed) + ((1.0 - p_c_given_obs_c) * u_obs_c_if_open)

    open_closures = [c for c in current_known_closures if c != seg and (c[1], c[0]) != seg]
    plan_if_obs_o = solve_candidate_plan(road_graph, sectors, vehicles, open_closures)
    u_obs_o_if_closed = evaluate_plan_utility(plan_if_obs_o, road_graph, sectors, vehicles, list(set(current_known_closures + [seg])))
    u_obs_o_if_open = evaluate_plan_utility(plan_if_obs_o, road_graph, sectors, vehicles, current_known_closures)
    expected_u_given_obs_o = (p_c_given_obs_o * u_obs_o_if_closed) + ((1.0 - p_c_given_obs_o) * u_obs_o_if_open)

    expected_u_with_scouting = (p_obs_closed * expected_u_given_obs_c) + (p_obs_open * expected_u_given_obs_o)
    net_evsi = (expected_u_with_scouting - expected_u_prior) - verification_cost

    return {
        "candidate_segment": list(seg),
        "prior_probability_closed": round(p_c, 3),
        "prior_probability_open": round(p_o, 3),
        "drone_sensor_reliability": round(eta, 3),
        "verification_cost": round(verification_cost, 2),
        "pre_observation_utility": round(expected_u_prior, 2),
        "post_observation_expected_utility": round(expected_u_with_scouting, 2),
        "u_obs_closed": round(expected_u_given_obs_c, 2),
        "u_obs_open": round(expected_u_given_obs_o, 2),
        "net_restricted_evsi": round(net_evsi, 2),
        "action_recommendation": "DISPATCH_DRONE_RECON" if net_evsi > 0.0 else "DECLINE_VERIFICATION",
        "governor_status": "ACTIONABLE" if net_evsi > 0.0 else "REJECTED_NEGATIVE_EVSI"
    }


# Retain compute_exact_evsi as an alias pointing to compute_restricted_policy_evsi with documented disclaimer
def compute_exact_evsi(*args, **kwargs) -> float:
    """Deprecated alias for compute_restricted_policy_evsi."""
    return compute_restricted_policy_evsi(*args, **kwargs)


def compute_operational_impact_heuristic(
    road_graph: PhysicalRoadGraph,
    sectors: List[SectorNode],
    vehicles: List[FleetVehicle],
    candidate_segment: Tuple[int, int],
    prior_p_closed: float,
    current_known_closures: List[Tuple[int, int]]
) -> float:
    """
    Fast Operational Impact Regret Spread Heuristic (O-VOI Heuristic):
    Measures the absolute difference between counterfactual plan utilities, scaled by uncertainty spread.
    """
    seg = (min(candidate_segment), max(candidate_segment))
    p = float(np.clip(prior_p_closed, 0.0, 1.0))

    closures_if_closed = list(set(current_known_closures + [seg]))
    plan_closed = solve_candidate_plan(road_graph, sectors, vehicles, closures_if_closed)

    closures_if_open = [c for c in current_known_closures if c != seg and (c[1], c[0]) != seg]
    plan_open = solve_candidate_plan(road_graph, sectors, vehicles, closures_if_open)

    u_closed = plan_closed.delivery_ratio * 100.0 - 0.05 * plan_closed.total_fleet_distance_km
    u_open = plan_open.delivery_ratio * 100.0 - 0.05 * plan_open.total_fleet_distance_km

    regret_spread = abs(u_open - u_closed)
    heuristic_score = regret_spread * (2.0 * p * (1.0 - p))
    return heuristic_score


def compute_graph_betweenness_centrality(
    road_graph: PhysicalRoadGraph,
    sectors: List[SectorNode],
    candidate_segment: Tuple[int, int]
) -> float:
    """
    Classical Graph Centrality Baseline:
    Calculates the frequency that candidate_segment lies on shortest paths between sector pairs.
    """
    seg = (min(candidate_segment), max(candidate_segment))
    sector_juncs = [s.junction_id for s in sectors]
    d_mat, t_mat, paths = road_graph.compute_sector_matrix(sector_juncs)

    traversal_count = 0
    for (i, j), path in paths.items():
        if i == j or len(path) < 2:
            continue
        for idx in range(len(path) - 1):
            e = (min(path[idx], path[idx+1]), max(path[idx], path[idx+1]))
            if e == seg:
                traversal_count += 1
                break

    return float(traversal_count)
