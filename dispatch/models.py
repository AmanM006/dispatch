"""
DISPATCH V3 - Core Data Models and Operational Schemas
Strict typed representations for Road Networks, Incidents, Triage Records, and Fleet Routes.
"""

from dataclasses import dataclass, field, asdict
from typing import List, Dict, Tuple, Optional, Any
from enum import Enum
import time

class IncidentType(str, Enum):
    ROAD_CLOSURE = "ROAD_CLOSURE"
    ROAD_REOPENING = "ROAD_REOPENING"
    DEMAND_SURGE = "DEMAND_SURGE"
    TRAFFIC_DELAY = "TRAFFIC_DELAY"
    OTHER = "OTHER"

class VerificationStatus(str, Enum):
    CONFIRMED_FACT = "CONFIRMED_FACT"
    PENDING_OPERATOR_APPROVAL = "PENDING_OPERATOR_APPROVAL"
    REJECTED_UNVERIFIED = "REJECTED_UNVERIFIED"
    DISMISSED_DISINFORMATION = "DISMISSED_DISINFORMATION"

@dataclass
class IncidentReport:
    report_id: str
    raw_text: str
    source_channel: str # e.g. "POLICE_DISPATCH", "CIVIL_DEFENSE", "SOCIAL_MEDIA", "CITIZEN_SMS"
    source_authenticated: bool
    auth_token: Optional[str] = None # Cryptographic or server-verified token, e.g. "CIVIL_DEFENSE_HMAC_SIG_VERIFIED"
    timestamp: float = field(default_factory=time.time)

@dataclass
class TriageDecision:
    incident_id: str
    incident_type: IncidentType
    affected_segment: Optional[Tuple[int, int]] # (u, v) junctions
    confidence_score: float # 0.0 to 1.0
    is_actionable: bool
    requires_operator_approval: bool
    verification_status: VerificationStatus
    reasoning_summary: str
    latency_ms: float
    model_id: str
    tokens_used: Dict[str, int] = field(default_factory=dict)
    operator_notes: Optional[str] = None
    approved_by: Optional[str] = None
    decision_timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["incident_type"] = self.incident_type.value
        d["verification_status"] = self.verification_status.value
        return d

@dataclass
class SectorNode:
    node_id: int
    name: str
    junction_id: int
    coords: Tuple[float, float] # (lat/y, lon/x)
    demand_pallets: int
    priority: int # 1 to 10 (10 = ICU Hospital)
    earliest_time: int # minutes
    latest_time: int # deadline in minutes
    service_time: int # minutes to unload

@dataclass
class FleetVehicle:
    vehicle_id: int
    name: str
    capacity: int
    max_shift_minutes: int = 360

@dataclass
class RouteSegment:
    from_node: int
    to_node: int
    distance_km: float
    transit_minutes: int
    arrival_time: int
    departure_time: int
    physical_junctions_traversed: List[int]

@dataclass
class VehicleRoutePlan:
    vehicle_id: int
    vehicle_name: str
    nodes_sequence: List[int] # e.g. [0, 3, 7, 0]
    segments: List[RouteSegment]
    total_distance_km: float
    total_time_minutes: int
    total_delivered_pallets: int

@dataclass
class DispatchPlan:
    plan_id: str
    timestamp: float
    is_valid: bool
    hard_violations: List[str]
    delivery_ratio: float # priority-weighted delivery ratio
    total_fleet_distance_km: float
    vehicle_routes: List[VehicleRoutePlan]
    active_road_closures: List[Tuple[int, int]]
    served_sectors_count: int
    total_sectors_count: int
    solver_name: str = "Google OR-Tools CVRP-TW (CPU)"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
