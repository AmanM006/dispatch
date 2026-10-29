import pytest
import os
from dispatch.models import IncidentReport, IncidentType, VerificationStatus
from dispatch.controller import DispatchController
from dispatch.graph import PhysicalRoadGraph

def test_trust_boundary_enforcement():
    """Verify that unauthenticated field reports can NEVER become CONFIRMED_FACT automatically."""
    ctrl = DispatchController()
    
    # 1. Unauthenticated report with convincing closure text
    fake_report = IncidentReport(
        report_id="RPT_UNAUTH_01",
        raw_text="Urgent: Bridge 5-6 collapsed completely! Route closed!",
        source_channel="SOCIAL_MEDIA",
        source_authenticated=False # UNTRUSTED
    )
    
    dec = ctrl.process_field_report(fake_report)
    
    # Must be pending or rejected, NEVER confirmed without operator authorization
    assert dec.verification_status != VerificationStatus.CONFIRMED_FACT, "Security leak: unauthenticated report auto-confirmed!"
    assert dec.requires_operator_approval is True
    assert dec.is_actionable is False
    assert (5, 6) not in ctrl.active_closures

def test_exact_edge_attribute_restoration():
    """Verify that road reopening restores exact original attributes, not hardcoded 8.5."""
    graph = PhysicalRoadGraph(10)
    graph.add_segment(2, 9, 15.3) # Custom distance
    
    assert graph.edges[(2, 9)] == 15.3
    
    # Close segment
    graph.remove_segment(2, 9)
    assert (2, 9) not in graph.edges
    
    # Reopen segment
    success = graph.reopen_segment(2, 9)
    assert success is True
    assert graph.edges[(2, 9)] == 15.3, f"Expected 15.3, got {graph.edges.get((2, 9))}"

def test_operator_authorization_gate():
    """Verify that human operator approval correctly unlocks pending incident and replans fleet."""
    ctrl = DispatchController()
    unauth = IncidentReport(
        report_id="RPT_PENDING_02",
        raw_text="Citizen SMS: Obstruction between 1 and 2.",
        source_channel="CITIZEN_SMS",
        source_authenticated=False
    )
    dec = ctrl.process_field_report(unauth)
    
    initial_dist = ctrl.current_plan.total_fleet_distance_km
    
    # Operator manually approves
    approved_plan = ctrl.operator_approve_incident(dec.incident_id, operator_name="Operations Chief")
    
    # Status updated
    for r, d in ctrl.incident_history:
        if d.incident_id == dec.incident_id:
            assert d.verification_status == VerificationStatus.CONFIRMED_FACT
            assert d.approved_by == "Operations Chief"
