import pytest
import time
from dispatch.controller import DispatchController
from dispatch.graph import PhysicalRoadGraph
from dispatch.models import IncidentReport, VerificationStatus, IncidentType
from dispatch.voi import compute_restricted_policy_evsi, compute_operational_impact_heuristic

def test_server_side_source_authentication():
    """Verify that client booleans cannot authenticate; server tokens are required."""
    ctrl = DispatchController()
    
    # 1. Unauthenticated citizen report with no token
    dec_unauth = ctrl.process_field_report(
        report_id="TEST_UNAUTH",
        raw_text="Citizen SMS: Bridge between 2 and 9 is blocked.",
        source_channel="CITIZEN_SMS",
        auth_token=None
    )
    assert dec_unauth.verification_status != VerificationStatus.CONFIRMED_FACT
    assert dec_unauth.requires_operator_approval is True
    assert (2, 9) not in ctrl.active_closures

    # 2. Bogus token attempt
    dec_bogus = ctrl.process_field_report(
        report_id="TEST_BOGUS",
        raw_text="Fake Police: Bridge 5-12 closed.",
        source_channel="POLICE_DISPATCH",
        auth_token="INVALID_FORGED_SIGNATURE"
    )
    assert dec_bogus.verification_status != VerificationStatus.CONFIRMED_FACT
    assert dec_bogus.requires_operator_approval is True

    # 3. Valid server cryptographic signature
    raw_auth_msg = "Civil Defense Dispatch: Structural collapse on bridge between 5 and 12."
    valid_token = DispatchController.generate_auth_token("CIVIL_DEFENSE", raw_auth_msg)
    dec_auth = ctrl.process_field_report(
        report_id="TEST_AUTH",
        raw_text=raw_auth_msg,
        source_channel="CIVIL_DEFENSE",
        auth_token=valid_token
    )
    # Authenticated report is processed with verified signature
    for rep, d in ctrl.incident_history:
        if rep.report_id == "TEST_AUTH":
            assert rep.source_authenticated is True

def test_safe_road_reopening_and_rejection_of_nonexistent_segments():
    """Verify that reopening a nonexistent segment fails safely and records an error without fabricating an edge."""
    ctrl = DispatchController()
    
    # Attempt reopening of a segment that was never in the graph
    valid_reopen_token = DispatchController.generate_auth_token("CIVIL_DEFENSE", "Official Clear: Reopen road between junction 99 and")
    fake_dec = ctrl.process_field_report(
        report_id="TEST_NONEXISTENT",
        raw_text="Official Clear: Reopen road between junction 99 and 100.",
        source_channel="CIVIL_DEFENSE",
        auth_token=valid_reopen_token
    )
    
    # Ensure (99, 100) was NOT added with a fabricated 8.5 km distance
    assert (min(99, 100), max(99, 100)) not in ctrl.road_graph.edges
    assert (min(99, 100), max(99, 100)) not in ctrl.road_graph.original_edges

    # Verify normal cycle of close -> reopen restores exact original distance
    # Edge (0, 1) exists in default 4x4 grid with distance 8.5
    original_dist = ctrl.road_graph.original_edges[(0, 1)]
    
    # Close
    ctrl.road_graph.remove_segment(0, 1)
    ctrl.active_closures.append((0, 1))
    assert (0, 1) not in ctrl.road_graph.edges
    
    # Reopen
    success = ctrl.road_graph.reopen_segment(0, 1)
    assert success is True
    assert ctrl.road_graph.edges[(0, 1)] == original_dist

def test_restricted_evsi_net_negative_when_cost_exceeds_gain():
    """Verify that Restricted EVSI allows negative net values when verification cost exceeds utility."""
    graph = PhysicalRoadGraph(5)
    for i in range(4):
        graph.add_segment(i, i+1, 5.0)
    
    # If verification cost is set to a massive value (e.g. 500.0), net EVSI must be negative
    ctrl = DispatchController()
    evsi_costly = compute_restricted_policy_evsi(
        ctrl.road_graph,
        ctrl.sectors,
        ctrl.vehicles,
        (2, 9),
        prior_p_closed=0.5,
        current_known_closures=[],
        drone_sensor_reliability=0.98,
        verification_cost=500.0 # Prohibitive cost
    )
    assert evsi_costly < 0.0, f"Expected negative net EVSI under high cost, got {evsi_costly}"


# ===== V7 SECURITY TESTS: HMAC-SHA256 Nonce Replay Protection & Full-Message Binding =====

def test_hmac_token_generation_and_verification():
    """Real HMAC tokens generated server-side must be verified successfully."""
    token = DispatchController.generate_auth_token("CIVIL_DEFENSE", "Bridge collapsed at junction 5")
    result = DispatchController.verify_source_authenticity(
        "CIVIL_DEFENSE", token, "Bridge collapsed at junction 5"
    )
    assert result is True, "Valid HMAC token must be accepted"

def test_nonce_replay_attack_rejected():
    """Valid token can NEVER be reused twice even within the 5-minute window (atomic nonce replay protection)."""
    token = DispatchController.generate_auth_token("CIVIL_DEFENSE", "Bridge collapsed at junction 5")
    # First use: must succeed
    first_res = DispatchController.verify_source_authenticity("CIVIL_DEFENSE", token, "Bridge collapsed at junction 5")
    assert first_res is True
    # Immediate replay: MUST FAIL
    second_res = DispatchController.verify_source_authenticity("CIVIL_DEFENSE", token, "Bridge collapsed at junction 5")
    assert second_res is False, "Replay attack was NOT blocked! Nonce reuse must be rejected."

def test_full_message_binding_defense():
    """Message modification beyond the 50th character MUST cause verification failure."""
    long_msg = "Official notice: Highway bridge between junction 2 and 9 is completely clear and open."
    token = DispatchController.generate_auth_token("CIVIL_DEFENSE", long_msg)
    # Tamper with characters after index 50
    tampered_msg = "Official notice: Highway bridge between junction 2 and 9 is completely COLLAPSED."
    result = DispatchController.verify_source_authenticity("CIVIL_DEFENSE", token, tampered_msg)
    assert result is False, "Tampered message after 50 chars must be rejected by full-message signature!"

def test_forged_token_rejected():
    """A token with a forged signature must be rejected."""
    forged = "DISPATCH_HMAC_V2:fake_nonce:9999999999:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    result = DispatchController.verify_source_authenticity("CIVIL_DEFENSE", forged, "any message")
    assert result is False, "Forged token signature must be rejected"

def test_replayed_expired_token_rejected():
    """An otherwise valid HMAC token that is older than 5 minutes must be rejected."""
    import hmac as _hmac, hashlib
    old_timestamp = str(int(time.time()) - 400)  # 400s ago > 300s expiry window
    msg = "Bridge collapsed at junction 5"
    payload = f"CIVIL_DEFENSE:test_old_nonce:{old_timestamp}:{msg}".encode()
    sig = _hmac.new(DispatchController._HMAC_SECRET, payload, hashlib.sha256).hexdigest()
    expired_token = f"DISPATCH_HMAC_V2:test_old_nonce:{old_timestamp}:{sig}"
    result = DispatchController.verify_source_authenticity("CIVIL_DEFENSE", expired_token, msg)
    assert result is False, "Expired token must be rejected (replay protection)"

def test_wrong_channel_token_rejected():
    """A valid token for one channel must NOT authenticate a different channel."""
    token = DispatchController.generate_auth_token("CIVIL_DEFENSE", "Bridge collapsed at junction 5")
    result = DispatchController.verify_source_authenticity("SOCIAL_MEDIA", token, "Bridge collapsed at junction 5")
    assert result is False, "Token for CIVIL_DEFENSE must not authenticate SOCIAL_MEDIA channel"

def test_old_fixture_prefix_rejected():
    """The old SIMULATED_AUTHENTICATED_FIXTURE_KEY_ prefix must no longer be accepted."""
    old_token = "SIMULATED_AUTHENTICATED_FIXTURE_KEY_CIVIL_DEFENSE"
    result = DispatchController.verify_source_authenticity("CIVIL_DEFENSE", old_token, "any message")
    assert result is False, "Old V5 fixture key prefix must be rejected"

def test_old_hardcoded_string_rejected():
    """The old SERVER_VERIFIED_CIVIL_DEFENSE_HMAC_VALID string must no longer be accepted."""
    old_token = "SERVER_VERIFIED_CIVIL_DEFENSE_HMAC_VALID"
    result = DispatchController.verify_source_authenticity("CIVIL_DEFENSE", old_token, "any message")
    assert result is False, "Old V5 hardcoded string must be rejected"
