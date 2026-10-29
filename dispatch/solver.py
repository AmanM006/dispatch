"""
DISPATCH V3 - Solver & Validator Engine
Implements CVRP-TW via Google OR-Tools on CPU.
Features deterministic external route auditor.
"""

import time
from typing import List, Dict, Tuple, Any
import numpy as np
from ortools.constraint_solver import routing_enums_pb2
from ortools.constraint_solver import pywrapcp

from dispatch.models import (
    SectorNode,
    FleetVehicle,
    RouteSegment,
    VehicleRoutePlan,
    DispatchPlan
)

def validate_dispatch_routes(
    sectors: List[SectorNode],
    vehicles: List[FleetVehicle],
    routes: List[List[int]],
    dist_mat: np.ndarray,
    time_mat: np.ndarray,
    paths_dict: Dict[Tuple[int, int], List[int]],
    closed_segments: List[Tuple[int, int]]
) -> Tuple[bool, List[str], float, float, int]:
    """
    Independent external validator.
    Audits routes against hard physical constraints:
    1. No closed road segments traversed.
    2. Vehicle load does not exceed capacity.
    3. Delivery arrival does not exceed deadline.
    4. Vehicle return time does not exceed shift limit.
    """
    violations = []
    total_fleet_distance_km = 0.0
    served_priority = 0
    total_priority = sum(s.priority for s in sectors[1:])
    served_sectors = set()
    closed_set = set((min(u, v), max(u, v)) for u, v in closed_segments)

    for v_idx, route in enumerate(routes):
        if not route or route[0] != 0 or route[-1] != 0:
            violations.append(f"Vehicle {v_idx} route must start and end at Depot (0)")
            continue

        vehicle = vehicles[v_idx]
        current_time = 0
        current_load = 0

        for i in range(len(route) - 1):
            curr_sec = route[i]
            next_sec = route[i+1]

            d = dist_mat[curr_sec][next_sec]
            if d == float('inf'):
                violations.append(f"Vehicle {v_idx} traverses disconnected path ({curr_sec} -> {next_sec})")
            else:
                total_fleet_distance_km += d

            # Physical segment check
            phys_path = paths_dict.get((curr_sec, next_sec), [])
            for p_i in range(len(phys_path) - 1):
                seg = (min(phys_path[p_i], phys_path[p_i+1]), max(phys_path[p_i], phys_path[p_i+1]))
                if seg in closed_set:
                    violations.append(f"SAFETY VIOLATION: Vehicle {v_idx} traversed closed segment {seg}")

            transit_time = time_mat[curr_sec][next_sec]
            arrival_time = current_time + transit_time

            if next_sec != 0:
                sec_obj = sectors[next_sec]
                if arrival_time > sec_obj.latest_time:
                    violations.append(f"Vehicle {v_idx} missed deadline at {sec_obj.name}: arrived {arrival_time}m, deadline {sec_obj.latest_time}m")
                start_service_time = max(arrival_time, sec_obj.earliest_time)
                current_time = start_service_time + sec_obj.service_time
                current_load += sec_obj.demand_pallets
                if current_load > vehicle.capacity:
                    violations.append(f"Vehicle {v_idx} exceeded capacity ({current_load} > {vehicle.capacity}) at {sec_obj.name}")
                served_sectors.add(next_sec)
                served_priority += sec_obj.priority
            else:
                current_time = arrival_time

        if current_time > vehicle.max_shift_minutes:
            violations.append(f"Vehicle {v_idx} exceeded max shift duration ({current_time}m > {vehicle.max_shift_minutes}m)")

    is_valid = len(violations) == 0
    delivery_ratio = served_priority / max(1, total_priority)
    return is_valid, violations, delivery_ratio, round(total_fleet_distance_km, 2), len(served_sectors)


def solve_cvrp_tw(
    sectors: List[SectorNode],
    vehicles: List[FleetVehicle],
    dist_mat: np.ndarray,
    time_mat: np.ndarray,
    paths_dict: Dict[Tuple[int, int], List[int]],
    closed_segments: List[Tuple[int, int]],
    time_limit_seconds: int = 2
) -> DispatchPlan:
    """
    Solves Capacitated Vehicle Routing Problem with Time Windows and Disjunctions via Google OR-Tools.
    """
    num_locations = len(sectors)
    num_vehicles = len(vehicles)
    manager = pywrapcp.RoutingIndexManager(num_locations, num_vehicles, 0)
    routing = pywrapcp.RoutingModel(manager)

    # 1. Distance Arc Costs
    def distance_callback(from_index, to_index):
        u = manager.IndexToNode(from_index)
        v = manager.IndexToNode(to_index)
        d = dist_mat[u][v]
        return 999999 if d == float('inf') else int(round(d * 100))

    transit_idx = routing.RegisterTransitCallback(distance_callback)
    routing.SetArcCostEvaluatorOfAllVehicles(transit_idx)

    # 2. Capacity Dimension
    def demand_callback(from_index):
        u = manager.IndexToNode(from_index)
        return sectors[u].demand_pallets

    demand_idx = routing.RegisterUnaryTransitCallback(demand_callback)
    routing.AddDimensionWithVehicleCapacity(
        demand_idx,
        0,
        [v.capacity for v in vehicles],
        True,
        "Capacity"
    )

    # 3. Time Windows Dimension
    def time_callback(from_index, to_index):
        u = manager.IndexToNode(from_index)
        v = manager.IndexToNode(to_index)
        t = time_mat[u][v]
        srv = sectors[u].service_time
        return 999999 if t >= 999999 else t + srv

    time_idx = routing.RegisterTransitCallback(time_callback)
    routing.AddDimension(
        time_idx,
        60, # allow waiting slack
        max(v.max_shift_minutes for v in vehicles),
        False,
        "Time"
    )
    time_dimension = routing.GetDimensionOrDie("Time")

    for loc_idx in range(num_locations):
        index = manager.NodeToIndex(loc_idx)
        time_dimension.CumulVar(index).SetRange(
            sectors[loc_idx].earliest_time,
            sectors[loc_idx].latest_time
        )

    # 4. Priority-Weighted Disjunctions
    for loc_idx in range(1, num_locations):
        index = manager.NodeToIndex(loc_idx)
        penalty = int(sectors[loc_idx].priority * 50000)
        routing.AddDisjunction([index], penalty)

    search_params = pywrapcp.DefaultRoutingSearchParameters()
    search_params.first_solution_strategy = routing_enums_pb2.FirstSolutionStrategy.PARALLEL_CHEAPEST_INSERTION
    search_params.local_search_metaheuristic = routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    search_params.time_limit.seconds = time_limit_seconds

    solution = routing.SolveWithParameters(search_params)

    raw_routes = []
    vehicle_routes = []

    if solution:
        for v_idx in range(num_vehicles):
            index = routing.Start(v_idx)
            route_nodes = []
            segments = []
            curr_time = 0
            curr_load = 0

            while not routing.IsEnd(index):
                node = manager.IndexToNode(index)
                route_nodes.append(node)
                next_index = solution.Value(routing.NextVar(index))
                next_node = manager.IndexToNode(next_index)

                d = dist_mat[node][next_node]
                t_transit = time_mat[node][next_node]
                arr_time = curr_time + t_transit
                dep_time = arr_time + sectors[next_node].service_time if next_node != 0 else arr_time

                segments.append(RouteSegment(
                    from_node=node,
                    to_node=next_node,
                    distance_km=0.0 if d == float('inf') else round(d, 2),
                    transit_minutes=t_transit,
                    arrival_time=arr_time,
                    departure_time=dep_time,
                    physical_junctions_traversed=paths_dict.get((node, next_node), [])
                ))
                curr_time = dep_time
                if next_node != 0:
                    curr_load += sectors[next_node].demand_pallets
                index = next_index

            route_nodes.append(manager.IndexToNode(index))
            raw_routes.append(route_nodes)

            v_dist = sum(s.distance_km for s in segments)
            vehicle_routes.append(VehicleRoutePlan(
                vehicle_id=v_idx,
                vehicle_name=vehicles[v_idx].name,
                nodes_sequence=route_nodes,
                segments=segments,
                total_distance_km=round(v_dist, 2),
                total_time_minutes=curr_time,
                total_delivered_pallets=curr_load
            ))
    else:
        # Trivial empty route fallback if infeasible
        raw_routes = [[0, 0] for _ in range(num_vehicles)]
        vehicle_routes = [
            VehicleRoutePlan(v.vehicle_id, v.name, [0, 0], [], 0.0, 0, 0) for v in vehicles
        ]

    # Validate output plan independently
    is_valid, violations, del_ratio, total_dist, num_served = validate_dispatch_routes(
        sectors, vehicles, raw_routes, dist_mat, time_mat, paths_dict, closed_segments
    )

    return DispatchPlan(
        plan_id=f"PLAN_{int(time.time()*1000)}",
        timestamp=time.time(),
        is_valid=is_valid,
        hard_violations=violations,
        delivery_ratio=del_ratio,
        total_fleet_distance_km=total_dist,
        vehicle_routes=vehicle_routes,
        active_road_closures=closed_segments,
        served_sectors_count=num_served,
        total_sectors_count=len(sectors) - 1
    )
