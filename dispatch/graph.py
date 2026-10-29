"""
DISPATCH V3 - Physical Road Graph & Deterministic Shortest Paths Engine
Maintains physical topological junctions and road segments.
Performs Dijkstra all-pairs paths over open segments.
Eliminates heuristic large-distance penalties.
"""

import heapq
from typing import Dict, List, Tuple, Optional
import numpy as np

class PhysicalRoadGraph:
    def __init__(self, num_junctions: int):
        self.num_junctions = num_junctions
        self.junction_coords: Dict[int, Tuple[float, float]] = {}
        self.adj: Dict[int, List[Tuple[int, float]]] = {i: [] for i in range(num_junctions)}
        self.edges: Dict[Tuple[int, int], float] = {}
        self.original_edges: Dict[Tuple[int, int], float] = {} # Preserves original baseline road attributes

    def set_junction_coords(self, junction_id: int, lat: float, lon: float):
        self.junction_coords[junction_id] = (lat, lon)

    def add_segment(self, u: int, v: int, distance_km: float):
        key = (min(u, v), max(u, v))
        self.adj[u].append((v, distance_km))
        self.adj[v].append((u, distance_km))
        self.edges[key] = distance_km
        if key not in self.original_edges:
            self.original_edges[key] = distance_km

    def reopen_segment(self, u: int, v: int) -> bool:
        """Restores a previously excised segment with its exact original distance."""
        key = (min(u, v), max(u, v))
        if key in self.original_edges:
            orig_d = self.original_edges[key]
            # Ensure not already present in adj
            self.adj[u] = [(nbr, d) for nbr, d in self.adj[u] if nbr != v]
            self.adj[v] = [(nbr, d) for nbr, d in self.adj[v] if nbr != u]
            self.adj[u].append((v, orig_d))
            self.adj[v].append((u, orig_d))
            self.edges[key] = orig_d
            return True
        return False

    def remove_segment(self, u: int, v: int):
        """Excise closed bridge or road segment from the graph."""
        self.adj[u] = [(nbr, d) for nbr, d in self.adj[u] if nbr != v]
        self.adj[v] = [(nbr, d) for nbr, d in self.adj[v] if nbr != u]
        key = (min(u, v), max(u, v))
        if key in self.edges:
            del self.edges[key]

    def shortest_path(self, start: int, goal: int) -> Tuple[float, List[int]]:
        """Dijkstra exact shortest path over open segments."""
        if start == goal:
            return 0.0, [start]

        dist = {i: float('inf') for i in range(self.num_junctions)}
        parent = {i: None for i in range(self.num_junctions)}
        dist[start] = 0.0
        pq = [(0.0, start)]

        while pq:
            d, u = heapq.heappop(pq)
            if d > dist[u]:
                continue
            if u == goal:
                break
            for v, weight in self.adj[u]:
                if dist[u] + weight < dist[v]:
                    dist[v] = dist[u] + weight
                    parent[v] = u
                    heapq.heappush(pq, (dist[v], v))

        if dist[goal] == float('inf'):
            return float('inf'), []

        # Reconstruct path
        path = []
        curr = goal
        while curr is not None:
            path.append(curr)
            curr = parent[curr]
        path.reverse()
        return dist[goal], path

    def compute_sector_matrix(self, sector_junctions: List[int], speed_kmh: float = 40.0) -> Tuple[np.ndarray, np.ndarray, Dict[Tuple[int, int], List[int]]]:
        """
        Computes distance matrix (km), travel time (minutes), and physical road sequences
        between sector locations.
        """
        n = len(sector_junctions)
        dist_mat = np.zeros((n, n))
        time_mat = np.zeros((n, n), dtype=int)
        paths = {}

        for i in range(n):
            for j in range(n):
                if i == j:
                    dist_mat[i][j] = 0.0
                    time_mat[i][j] = 0
                    paths[(i, j)] = [sector_junctions[i]]
                else:
                    u_junc = sector_junctions[i]
                    v_junc = sector_junctions[j]
                    d, p = self.shortest_path(u_junc, v_junc)
                    dist_mat[i][j] = d
                    if d == float('inf'):
                        time_mat[i][j] = 999999
                        paths[(i, j)] = []
                    else:
                        time_mat[i][j] = int(round((d / speed_kmh) * 60.0))
                        paths[(i, j)] = p

        return dist_mat, time_mat, paths
