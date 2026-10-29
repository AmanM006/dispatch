"""
DISPATCH V3 - State Controller & Pipeline Orchestrator
Coordinates the full lifecycle:
1. Maintains current road graph and active closures.
2. Ingests incident reports.
3. Dispatches AI triage (Nemotron via Token Factory).
4. Routes confirmed vs pending operator approvals.
5. Applies human approval decisions to graph.
6. Solves CVRP-TW and produces before-and-after operational metrics.
7. Logs auditable decision history.
"""

import os
import time
import copy
import threading
import secrets
from typing import List, Dict, Tuple, Optional, Any

from dispatch.models import (
    IncidentReport,
    TriageDecision,
    IncidentType,
    VerificationStatus,
    SectorNode,
    FleetVehicle,
    DispatchPlan
)
from dispatch.graph import PhysicalRoadGraph
from dispatch.solver import solve_cvrp_tw
from dispatch.triage import triage_incident_with_nemotron

class DispatchController:
    def __init__(self, seed: int = 42):
        self.seed = seed
        self.sectors: List[SectorNode] = []
        self.vehicles: List[FleetVehicle] = []
        self.road_graph: Optional[PhysicalRoadGraph] = None
        self.active_closures: List[Tuple[int, int]] = []
        self.current_plan: Optional[DispatchPlan] = None
        self.previous_plan: Optional[DispatchPlan] = None
        self.incident_history: List[Tuple[IncidentReport, TriageDecision]] = []
        self.audit_log: List[Dict[str, Any]] = []

        self.initialize_default_disaster_territory()

    def initialize_default_disaster_territory(self):
        """Builds a realistic 16-junction urban/coastal emergency sector."""
        self.road_graph = PhysicalRoadGraph(16)
        
        # 4x4 Grid coordinates (km offsets from central emergency ops center)
        coords = [
            (25.0, 25.0), (25.0, 35.0), (25.0, 45.0), (25.0, 55.0),
            (35.0, 25.0), (35.0, 35.0), (35.0, 45.0), (35.0, 55.0),
            (45.0, 25.0), (45.0, 35.0), (45.0, 45.0), (45.0, 55.0),
            (55.0, 25.0), (55.0, 35.0), (55.0, 45.0), (55.0, 55.0)
        ]
        for idx, (lat, lon) in enumerate(coords):
            self.road_graph.set_junction_coords(idx, lat, lon)

        # Standard physical road segments with road distances
        for r in range(4):
            for c in range(4):
                u = r * 4 + c
                if c + 1 < 4:
                    self.road_graph.add_segment(u, u + 1, 8.5)
                if r + 1 < 4:
                    self.road_graph.add_segment(u, u + 4, 9.0)

        # Emergency delivery locations
        self.sectors = [
            SectorNode(0, "Central Emergency Depot", 0, coords[0], 0, 0, 0, 360, 0),
            SectorNode(1, "Mercy General Hospital (ICU)", 5, coords[5], 3, 10, 30, 180, 15),
            SectorNode(2, "St. Jude Trauma Center", 10, coords[10], 4, 10, 45, 210, 15),
            SectorNode(3, "Civic Community Center (Shelter)", 6, coords[6], 2, 5, 20, 240, 10),
            SectorNode(4, "North High School Relief Camp", 2, coords[2], 3, 5, 60, 300, 12),
            SectorNode(5, "Red Cross Field Clinic", 13, coords[13], 2, 8, 30, 240, 15),
            SectorNode(6, "Harbor Evacuation Point", 15, coords[15], 3, 4, 90, 360, 10),
            SectorNode(7, "South Ridge Settlement", 12, coords[12], 2, 3, 60, 330, 10),
            SectorNode(8, "Eastside Public School Shelter", 7, coords[7], 1, 4, 30, 270, 10)
        ]

        self.vehicles = [
            FleetVehicle(0, "Relief Unit Alpha (Heavy Truck)", 12, 360),
            FleetVehicle(1, "Relief Unit Bravo (Rapid Response)", 10, 360)
        ]

        self.active_closures = []
        self.replan_fleet_routes()

    def replan_fleet_routes(self) -> DispatchPlan:
        """Computes updated all-pairs matrices and solves CVRP-TW."""
        sector_juncs = [s.junction_id for s in self.sectors]
        dist_mat, time_mat, paths_dict = self.road_graph.compute_sector_matrix(sector_juncs)

        self.previous_plan = self.current_plan
        self.current_plan = solve_cvrp_tw(
            sectors=self.sectors,
            vehicles=self.vehicles,
            dist_mat=dist_mat,
            time_mat=time_mat,
            paths_dict=paths_dict,
            closed_segments=self.active_closures,
            time_limit_seconds=2
        )

        self.audit_log.append({
            "timestamp": time.time(),
            "action": "FLEET_REPLAN",
            "active_closures": copy.deepcopy(self.active_closures),
            "delivery_ratio": self.current_plan.delivery_ratio,
            "total_distance_km": self.current_plan.total_fleet_distance_km,
            "violations": len(self.current_plan.hard_violations)
        })

        return self.current_plan

    # Server authentication configuration
    # In strict production mode (ENVIRONMENT=production), DISPATCH_HMAC_SECRET must be explicitly provided.
    _IS_PROD: bool = os.environ.get("ENVIRONMENT", "development").lower() == "production"
    _ENV_SECRET: str = os.environ.get("DISPATCH_HMAC_SECRET", "")
    if _IS_PROD and not _ENV_SECRET:
        raise RuntimeError("FATAL: DISPATCH_HMAC_SECRET environment variable is strictly required in production mode.")
    _HMAC_SECRET: bytes = _ENV_SECRET.encode() if _ENV_SECRET else secrets.token_bytes(32)

    # Server simulation mode: defaults strictly to False in production
    _SIMULATION_MODE: bool = os.environ.get("DISPATCH_SIMULATION_MODE", "0").lower() in ("1", "true", "yes")
    # Token replay window in seconds (5 minutes)
    _TOKEN_EXPIRY_SECONDS: int = 300
    # Thread-safe server-side nonce registry with timestamp-based expiry cleanup
    # NOTE FOR MULTI-WORKER DEPLOYMENTS: In a scaled multi-worker deployment (e.g. gunicorn/uvicorn with multiple workers),
    # this in-memory registry protects only the local process. A distributed atomic cache (e.g. Redis with TTL)
    # must be used to enforce nonce single-use across independent process boundaries.
    _NONCE_LOCK = threading.Lock()
    _CONSUMED_NONCES: Dict[str, float] = {}  # Map: nonce -> insertion timestamp

    @classmethod
    def generate_auth_token(cls, channel: str, message: str, nonce: Optional[str] = None) -> str:
        """
        Generate a cryptographically valid HMAC-SHA256 authentication token.
        Token format: DISPATCH_HMAC_V2:<nonce>:<timestamp>:<sig>
        Binds channel, complete canonical message, nonce, and timestamp.
        """
        import hmac as _hmac
        import hashlib
        import uuid
        nonce = nonce or uuid.uuid4().hex[:16]
        timestamp = str(int(time.time()))
        canonical_msg = message.strip()
        payload = f"{channel}:{nonce}:{timestamp}:{canonical_msg}".encode()
        sig = _hmac.new(cls._HMAC_SECRET, payload, hashlib.sha256).hexdigest()
        return f"DISPATCH_HMAC_V2:{nonce}:{timestamp}:{sig}"

    @classmethod
    def verify_source_authenticity(cls, channel: str, auth_token: Optional[str], message: str = "") -> bool:
        """
        Authoritative server-side authentication verification gate.
        Enforces:
        1. HMAC-SHA256 verification of channel + nonce + timestamp + full message.
        2. Nonce single-use replay protection (consumed tokens cannot be reused).
        3. Expiry validation (within 300s window).
        4. Constant-time comparison.
        """
        import hmac as _hmac
        import hashlib
        if not auth_token:
            return False

        # --- Path 1: DISPATCH_HMAC_V2 token ---
        if auth_token.startswith("DISPATCH_HMAC_V2:"):
            parts = auth_token.split(":", 3)
            if len(parts) != 4:
                return False
            _, nonce, token_time_str, provided_sig = parts
            try:
                token_time = int(token_time_str)
            except ValueError:
                return False

            # Expiry check (300s window)
            current_time = time.time()
            if abs(current_time - token_time) > cls._TOKEN_EXPIRY_SECONDS:
                return False

            # Replay protection: Check and consume nonce atomically
            if nonce in cls._CONSUMED_NONCES:
                return False  # REPLAY DETECTED!

            canonical_msg = message.strip()
            payload = f"{channel}:{nonce}:{token_time_str}:{canonical_msg}".encode()
            expected_sig = _hmac.new(cls._HMAC_SECRET, payload, hashlib.sha256).hexdigest()

            if _hmac.compare_digest(expected_sig, provided_sig):
                with cls._NONCE_LOCK:
                    # Periodic cleanup of expired nonces (> 300s old)
                    current_wall_time = time.time()
                    expired_keys = [k for k, ts in cls._CONSUMED_NONCES.items() if current_wall_time - ts > cls._TOKEN_EXPIRY_SECONDS]
                    for k in expired_keys:
                        del cls._CONSUMED_NONCES[k]

                    if nonce in cls._CONSUMED_NONCES:
                        return False  # Atomic race condition prevention: already consumed
                    cls._CONSUMED_NONCES[nonce] = current_wall_time
                return True
            return False

        # --- Path 2: Simulation fixture (ONLY when server simulation mode is explicitly enabled) ---
        if cls._SIMULATION_MODE and auth_token.startswith("SIMULATED_FIXTURE:"):
            return True

        return False

    def process_field_report(
        self,
        report_or_id: Any = None,
        raw_text: Optional[str] = None,
        source_channel: Optional[str] = None,
        auth_token: Optional[str] = None,
        report_id: Optional[str] = None
    ) -> TriageDecision:
        """
        Authoritative single-entry incident ingestion pipeline.
        Supports passing either an IncidentReport object or explicit kwargs: (report_id, raw_text, source_channel, auth_token).
        
        STRICT TRUST BOUNDARY:
        1. An LLM inference classification NEVER establishes physical truth on its own.
        2. Client boolean inputs are discarded; server-side token validation is enforced.
        3. Only reports with cryptographically validated server-side signatures (source_authenticated == True)
           AND classified as high-confidence actionable may be auto-actioned.
        4. All unauthenticated reports MUST be routed to the Human-in-the-Loop Operator Verification Queue.
        """
        if isinstance(report_or_id, IncidentReport):
            r_id = report_or_id.report_id
            text = report_or_id.raw_text
            channel = report_or_id.source_channel
            token = report_or_id.auth_token
            is_authenticated = self.verify_source_authenticity(channel, token, text)
            if not is_authenticated and getattr(report_or_id, "source_authenticated", False) and token:
                is_authenticated = self.verify_source_authenticity(channel, token, text)
        else:
            r_id = str(report_id or report_or_id or f"INC_{int(time.time()*1000)%10000}")
            text = str(raw_text or "")
            channel = str(source_channel or "CITIZEN_SMS")
            token = auth_token
            is_authenticated = self.verify_source_authenticity(channel, token, text)

        report = IncidentReport(
            report_id=r_id,
            raw_text=text,
            source_channel=channel,
            source_authenticated=is_authenticated,
            auth_token=token
        )

        decision = triage_incident_with_nemotron(report)
        
        # Enforce strict trust boundary in code:
        if not is_authenticated:
            decision.verification_status = VerificationStatus.PENDING_OPERATOR_APPROVAL
            decision.is_actionable = False
            decision.requires_operator_approval = True

        self.incident_history.append((report, decision))

        # Only apply automatically if both authenticated AND confirmed
        if (is_authenticated 
            and decision.verification_status == VerificationStatus.CONFIRMED_FACT 
            and decision.is_actionable):
            self._apply_decision_to_graph(decision)
            self.replan_fleet_routes()

        return decision

    def operator_approve_incident(self, incident_id: str, operator_name: str = "Chief Dispatcher") -> Optional[DispatchPlan]:
        """Operator explicitly authorizes a pending or escalated report."""
        for report, dec in self.incident_history:
            if dec.incident_id == incident_id:
                dec.verification_status = VerificationStatus.CONFIRMED_FACT
                dec.is_actionable = True
                dec.requires_operator_approval = False
                dec.approved_by = operator_name
                dec.decision_timestamp = time.time()
                self._apply_decision_to_graph(dec)
                return self.replan_fleet_routes()
        return None

    def operator_dismiss_incident(self, incident_id: str, operator_name: str = "Chief Dispatcher"):
        """Operator rejects an unverified rumor or disinformation report."""
        for report, dec in self.incident_history:
            if dec.incident_id == incident_id:
                dec.verification_status = VerificationStatus.DISMISSED_DISINFORMATION
                dec.is_actionable = False
                dec.requires_operator_approval = False
                dec.approved_by = operator_name
                dec.decision_timestamp = time.time()
                break

    def _apply_decision_to_graph(self, dec: TriageDecision):
        """Applies road closures or reopenings to physical graph."""
        if not dec.affected_segment:
            return

        u, v = dec.affected_segment
        seg = (min(u, v), max(u, v))

        if dec.incident_type == IncidentType.ROAD_CLOSURE:
            if seg not in self.active_closures:
                self.active_closures.append(seg)
                self.road_graph.remove_segment(u, v)
        elif dec.incident_type == IncidentType.ROAD_REOPENING:
            if seg in self.active_closures:
                self.active_closures.remove(seg)
                reopened = self.road_graph.reopen_segment(u, v)
                if not reopened:
                    # STRICT GRAPH INTEGRITY: NEVER fabricate an edge distance if original attributes are unknown.
                    # Record actionable error in audit log and retain closure state.
                    self.active_closures.append(seg)
                    self.audit_log.append({
                        "timestamp": time.time(),
                        "action": f"ERROR: Failed road reopening for segment ({u}, {v}). Original attributes not found in graph specification.",
                        "error": "NONEXISTENT_ORIGINAL_SEGMENT",
                        "segment": seg
                    })
