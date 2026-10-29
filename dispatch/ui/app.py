"""
DISPATCH SENTINEL - Complete Command Center UI & Disaster Simulation Twin
Interactive Streamlit application featuring:
1. Authoritative Controller Integration: Zero UI trust bypasses; all reports ingested via DispatchController.
2. Dynamic Bottleneck Disaster Territory (West/East bank split across 3 bridges).
3. Live NVIDIA Nemotron 3.5 Lightning Incident Ingestion via Nebius Token Factory.
4. Active Operational Value-of-Information (VOI) Scouting Recommendations with Restricted-Policy EVSI & Regret Badges.
5. Human-in-the-Loop Operator Verification Queue.
6. Google OR-Tools CVRP-TW Real-Time Dynamic Replanning & Before/After Regret Comparisons.
7. Full Audit Trail of Evidence-to-Action Decisions.
"""

import streamlit as st
import pandas as pd
import numpy as np
import time
import json
import os
import html
import copy
from typing import Dict, List, Tuple, Any

from dispatch.models import (
    IncidentReport,
    VerificationStatus,
    IncidentType,
    SectorNode,
    FleetVehicle,
    DispatchPlan
)
from dispatch.graph import PhysicalRoadGraph
from dispatch.solver import solve_cvrp_tw, validate_dispatch_routes
from dispatch.controller import DispatchController
from dispatch.voi import compute_restricted_policy_evsi, compute_operational_impact_heuristic

st.set_page_config(
    page_title="DISPATCH SENTINEL — Emergency Operations Command Center",
    page_icon="🚨",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Styling for Mission-Critical Command Center
st.markdown("""
<style>
    .reportview-container {
        background: #0b0f19;
    }
    .metric-card {
        background-color: #151c2e;
        border: 1px solid #23304d;
        border-radius: 8px;
        padding: 14px;
        margin-bottom: 10px;
    }
    .badge-voi-high {
        background-color: #e53e3e;
        color: white;
        padding: 4px 8px;
        border-radius: 4px;
        font-weight: bold;
        font-size: 0.85em;
    }
    .badge-voi-med {
        background-color: #dd6b20;
        color: white;
        padding: 4px 8px;
        border-radius: 4px;
        font-weight: bold;
        font-size: 0.85em;
    }
    .badge-voi-low {
        background-color: #718096;
        color: white;
        padding: 4px 8px;
        border-radius: 4px;
        font-weight: bold;
        font-size: 0.85em;
    }
</style>
""", unsafe_allow_html=True)


class DisasterCommandTwin:
    """Disaster Simulation Digital Twin wrapping authoritative DispatchController."""
    def __init__(self):
        self.controller = DispatchController()
        # Initialize bridge metadata for river crossings
        self.bridge_names = {
            (2, 9): "North Shore Suspension Bridge (Junction 2 ↔ 9)",
            (5, 12): "Central Expressway Bridge (Junction 5 ↔ 12)",
            (8, 15): "South Truss Industrial Bridge (Junction 8 ↔ 15)"
        }

    @property
    def current_plan(self) -> DispatchPlan:
        return self.controller.current_plan

    @property
    def previous_plan(self) -> DispatchPlan:
        return self.controller.previous_plan

    @property
    def road_graph(self) -> PhysicalRoadGraph:
        return self.controller.road_graph

    @property
    def active_closures(self) -> List[Tuple[int, int]]:
        return self.controller.active_closures

    @property
    def sectors(self) -> List[SectorNode]:
        return self.controller.sectors

    @property
    def incident_history(self) -> List[Tuple[IncidentReport, Any]]:
        return self.controller.incident_history

    @property
    def audit_log(self) -> List[Dict[str, Any]]:
        return self.controller.audit_log

    def compute_scouting_metrics(self, segment: Tuple[int, int], prior_p_closed: float = 0.5) -> Tuple[float, float]:
        """Calculates both Restricted EVSI and O-VOI Regret Spread."""
        evsi = compute_restricted_policy_evsi(
            self.road_graph,
            self.sectors,
            self.controller.vehicles,
            segment,
            prior_p_closed,
            self.active_closures,
            drone_sensor_reliability=0.98,
            verification_cost=0.5
        )
        heuristic = compute_operational_impact_heuristic(
            self.road_graph,
            self.sectors,
            self.controller.vehicles,
            segment,
            prior_p_closed,
            self.active_closures
        )
        return evsi, heuristic


# Ensure API Key loaded
if "NEBIUS_API_KEY" not in os.environ:
    try:
        with open(".env") as f:
            for line in f:
                if line.startswith("NEBIUS_API_KEY="):
                    os.environ["NEBIUS_API_KEY"] = line.split("=", 1)[1].strip().strip('"')
    except Exception:
        pass

# Initialize session state
if "twin" not in st.session_state:
    st.session_state.twin = DisasterCommandTwin()

twin: DisasterCommandTwin = st.session_state.twin
ctrl: DispatchController = twin.controller

# Top Title & Subtitle
st.title("🚨 DISPATCH SENTINEL: Emergency Operations Command Center")
st.caption("Evidence-to-Action Active Logistics | Powered by Nebius Token Factory (NVIDIA Nemotron 3.5 Lightning) & Google OR-Tools CVRP-TW")

# KPI Summary Bar
plan = twin.current_plan
prev = twin.previous_plan

k1, k2, k3, k4, k5 = st.columns(5)
with k1:
    st.metric(
        "Priority Delivery Ratio",
        f"{plan.delivery_ratio * 100.0:.1f}%",
        delta=f"{(plan.delivery_ratio - prev.delivery_ratio)*100.0:+.1f}%" if prev else None
    )
with k2:
    st.metric(
        "Fleet Travel Distance",
        f"{plan.total_fleet_distance_km:.1f} km",
        delta=f"{plan.total_fleet_distance_km - prev.total_fleet_distance_km:+.1f} km" if prev else None,
        delta_color="inverse"
    )
with k3:
    st.metric(
        "Deterministic Safety Violations",
        f"{len(plan.hard_violations)} FATAL",
        delta="Feasible (Zero Fatalities)" if len(plan.hard_violations) == 0 else "SAFETY BREACH",
        delta_color="normal" if len(plan.hard_violations) == 0 else "inverse"
    )
with k4:
    st.metric(
        "Active Road Closures",
        f"{len(twin.active_closures)} Segments",
        delta=f"{len(twin.bridge_names)} river crossings"
    )
with k5:
    st.metric(
        "Critical Sectors Served",
        f"{plan.served_sectors_count} / {plan.total_sectors_count}",
        delta=f"{plan.served_sectors_count - prev.served_sectors_count:+d}" if prev else None
    )

st.markdown("---")

# Layout: 60% Left (Map & Operations), 40% Right (Live Ingestion & VOI Queue)
col_left, col_right = st.columns([3, 2])

with col_left:
    st.subheader("🗺️ Disaster Logistics Territory & Fleet Schedules")
    
    # Map Visualization
    sec_df = []
    for s in twin.sectors:
        sec_df.append({
            "lat": s.coords[0],
            "lon": s.coords[1],
            "Name": s.name,
            "Priority": s.priority,
            "Demand (Pallets)": s.demand_pallets,
            "Latest Delivery Window": f"{s.latest_time}m"
        })
    st.map(pd.DataFrame(sec_df), latitude="lat", longitude="lon", size=35)

    # Active Turn-by-Turn Schedules
    st.subheader("🚚 Optimized Convoy Schedules (OR-Tools CVRP-TW)")
    for vr in plan.vehicle_routes:
        with st.expander(f"🚛 {vr.vehicle_name} — {vr.total_distance_km:.1f} km | {vr.total_delivered_pallets} pallets | {vr.total_time_minutes:.0f} min", expanded=True):
            r_names = [twin.sectors[n].name for n in vr.nodes_sequence]
            st.write(f"**Route Sequence:** {' ➔ '.join(r_names)}")
            
            rows = []
            for s in vr.segments:
                rows.append({
                    "From Sector": twin.sectors[s.from_node].name,
                    "To Sector": twin.sectors[s.to_node].name,
                    "Distance (km)": f"{s.distance_km:.1f}",
                    "Transit (min)": f"{s.transit_minutes:.1f}",
                    "Arrival (min)": f"{s.arrival_time:.1f}",
                    "Departure (min)": f"{s.departure_time:.1f}",
                    "Physical Path (Junctions)": " ➔ ".join(str(j) for j in s.physical_junctions_traversed)
                })
            st.dataframe(pd.DataFrame(rows), use_container_width=True)

    # Active Bottleneck Bridges Status
    st.subheader("🌉 Bottleneck River Crossing Bridges")
    for br, name in twin.bridge_names.items():
        is_closed = br in twin.active_closures or (br[1], br[0]) in twin.active_closures
        c_status, c_act = st.columns([3, 1])
        with c_status:
            if is_closed:
                st.error(f"🛑 **{name}** — CLOSED / IMPASSABLE")
            else:
                st.success(f"🟢 **{name}** — OPEN & OPERATIONAL")
        with c_act:
            if is_closed:
                if st.button("Re-open via Auth", key=f"reopen_{br[0]}_{br[1]}"):
                    # Ingest official reopening dispatch through authoritative controller
                    reopen_msg = f"Civil Defense Official: Inspection completed for bridge {br[0]}-{br[1]}. Segment reopened."
                    ctrl.process_field_report(
                        report_id=f"REOPEN_{br[0]}_{br[1]}",
                        raw_text=reopen_msg,
                        source_channel="CIVIL_DEFENSE",
                        auth_token=DispatchController.generate_auth_token("CIVIL_DEFENSE", reopen_msg)
                    )
                    st.rerun()
            else:
                if st.button("Close via Auth", key=f"close_{br[0]}_{br[1]}"):
                    # Ingest official closure dispatch through authoritative controller
                    close_msg = f"Civil Defense Alert: Flash flood structural hazard on bridge {br[0]}-{br[1]}. Road closed."
                    ctrl.process_field_report(
                        report_id=f"CLOSE_{br[0]}_{br[1]}",
                        raw_text=close_msg,
                        source_channel="CIVIL_DEFENSE",
                        auth_token=DispatchController.generate_auth_token("CIVIL_DEFENSE", close_msg)
                    )
                    st.rerun()


with col_right:
    st.subheader("📡 Field Incident Ingestion (Authoritative Controller)")

    # Preset disaster scenario injector for live demo
    st.markdown("**⚡ Simulated Incident Scenarios (Authoritative Ingestion)**")
    p_col1, p_col2 = st.columns(2)
    with p_col1:
        if st.button("🚨 Official: Bridge 5-12 Collapse (Simulated Auth Token)", use_container_width=True):
            with st.spinner("Processing official dispatch via DispatchController..."):
                raw_msg = "Civil Defense Dispatch: Structural collapse on Central Expressway Bridge between junction 5 and 12. Complete obstruction."
                ctrl.process_field_report(
                    report_id=f"SIM_OFFICIAL_{int(time.time()*10)%1000}",
                    raw_text=raw_msg,
                    source_channel="CIVIL_DEFENSE",
                    auth_token=DispatchController.generate_auth_token("CIVIL_DEFENSE", raw_msg)
                )
            st.rerun()

    with p_col2:
        if st.button("⚠️ Citizen Rumor: South Bridge 8-15 (Unauthenticated)", use_container_width=True):
            with st.spinner("Triaging citizen chatter via DispatchController..."):
                ctrl.process_field_report(
                    report_id=f"SIM_RUMOR_{int(time.time()*10)%1000}",
                    raw_text="Citizen Telegram Feed: People saying South Truss Bridge between 8 and 15 is shaking violently and cracked! Avoid crossing!",
                    source_channel="SOCIAL_MEDIA",
                    auth_token=None # NO VALID TOKEN -> FORCED TO OPERATOR QUEUE
                )
            st.rerun()

    # Manual Form Ingestion (Server-Side Authentication Verification)
    with st.expander("📝 Manual Incident Report Submission", expanded=False):
        with st.form("manual_incident_form"):
            raw_text = st.text_area("Field Report Text", value="Police Dispatch: Flash flood blocking highway segment between junction 2 and 9.")
            channel = st.selectbox("Source Channel", ["CITIZEN_SMS", "SOCIAL_MEDIA", "HAM_RADIO", "CIVIL_DEFENSE", "POLICE_DISPATCH"])
            submit_btn = st.form_submit_button("⚡ Ingest via Controller")
            if submit_btn:
                with st.spinner("Ingesting through authoritative DispatchController..."):
                    # Server determines authenticity; client cannot supply auth tokens
                    dec = ctrl.process_field_report(
                        report_id=f"MANUAL_{int(time.time()*100)%1000}",
                        raw_text=raw_text,
                        source_channel=channel,
                        auth_token=None
                    )
                    st.info(f"Ingested as {dec.verification_status.value} (Actionable: {dec.is_actionable})")
                st.rerun()

    # Active Value-of-Information (VOI) Scouting Queue
    st.subheader("🎯 Active Information-Theoretic Scouting Queue")
    st.caption("Restricted EVSI & Operational Regret Spread: Ranks which unverified field rumors to physically scout.")

    # Gather unverified reports from authoritative controller history
    unverified_items = [
        (r, d) for r, d in ctrl.incident_history
        if d.verification_status in [VerificationStatus.PENDING_OPERATOR_APPROVAL, VerificationStatus.REJECTED_UNVERIFIED]
        and d.affected_segment is not None
    ]

    if not unverified_items:
        st.info("No unverified field reports currently in queue.")
    else:
        # Score each with exact Restricted EVSI and O-VOI heuristic
        scored_items = []
        for r, d in unverified_items:
            seg = d.affected_segment
            evsi_val, o_voi_val = twin.compute_scouting_metrics(seg, prior_p_closed=d.confidence_score if d.confidence_score > 0 else 0.5)
            scored_items.append((r, d, evsi_val, o_voi_val))

        scored_items.sort(key=lambda x: x[2], reverse=True)

        for r, d, evsi_score, o_voi_score in scored_items:
            seg = d.affected_segment
            badge_class = "badge-voi-high" if evsi_score >= 10.0 else ("badge-voi-med" if evsi_score >= 1.0 else "badge-voi-low")
            
            # Sanitize untrusted incident inputs against HTML injection
            safe_text = html.escape(str(r.raw_text))
            safe_channel = html.escape(str(r.source_channel))
            safe_id = html.escape(str(d.incident_id))
            safe_type = html.escape(str(d.incident_type.value))

            with st.container():
                st.markdown(f"""
                <div class="metric-card">
                    <div style="display:flex; justify-content:space-between; align-items:center;">
                        <strong>Incident {safe_id} ({safe_type})</strong>
                        <span class="{badge_class}">Restricted EVSI: {evsi_score:+.2f} pts | Regret: {o_voi_score:.1f}</span>
                    </div>
                    <p style="margin-top:6px; font-size:0.9em;"><em>"{safe_text}"</em></p>
                    <p style="font-size:0.85em; color:#a0aec0;">
                        Segment: <strong>Junction {seg[0]} ↔ {seg[1]}</strong> | 
                        AI Confidence: <strong>{d.confidence_score*100:.0f}%</strong> | 
                        Channel: <strong>{safe_channel}</strong> (Auth: <strong>{r.source_authenticated}</strong>)
                    </p>
                </div>
                """, unsafe_allow_html=True)

                act1, act2 = st.columns(2)
                with act1:
                    if st.button(f"🔍 Simulate Scout Drone Recon (Confirm Closure)", key=f"conf_{d.incident_id}_{seg[0]}_{seg[1]}"):
                        ctrl.operator_approve_incident(d.incident_id, operator_name="Simulated Drone Recon Unit (Operator Verified)")
                        st.success(f"Simulated recon confirmed closure at {seg}! Human watch officer authorized fleet re-route.")
                        st.rerun()
                with act2:
                    if st.button(f"🟢 Simulate Scout Drone Recon (Confirm Clear)", key=f"clear_{d.incident_id}_{seg[0]}_{seg[1]}"):
                        ctrl.operator_dismiss_incident(d.incident_id, operator_name="Simulated Drone Recon Unit (Operator Dismissed)")
                        st.info(f"Simulated recon confirmed segment {seg} clear. Human watch officer dismissed rumor.")
                        st.rerun()

    # Decision Audit Trail
    st.subheader("📋 Decision Audit Log")
    with st.expander("View Full Evidence-to-Action Ledger", expanded=False):
        for log in reversed(ctrl.audit_log[-8:]):
            st.write(f"• `{time.strftime('%H:%M:%S', time.localtime(log['timestamp']))}` — **{log['action']}**")
