import pytest
from fastapi.testclient import TestClient
from dispatch.twin_server import app, SESSION_STORE, GLOBAL_SPEND_LOCK
import dispatch.twin_server as ts

client = TestClient(app)

def test_api_state_scenario_b_defaults():
    """Verify session initialization and default Scenario B state."""
    response = client.get("/api/state")
    assert response.status_code == 200
    data = response.json()
    assert data["scenario_mode"] == "B"
    assert "recon_budget" in data
    assert data["recon_budget"]["total"] == 2
    assert data["recon_budget"]["remaining"] == 2
    assert "kpi" in data
    assert data["kpi"]["delivery_ratio_pct"] == 100.0
    assert data["kpi"]["active_closures_count"] == 2

def test_switch_scenario_and_evsi_governor():
    """Verify Scenario A rejects negative EVSI reconnaissance."""
    # Create isolated session client
    c = TestClient(app)
    init_res = c.get("/api/state")
    assert init_res.status_code == 200
    
    # Switch to Scenario A
    switch_res = c.post("/api/switch_scenario/A")
    assert switch_res.status_code == 200
    assert switch_res.json()["mode"] == "A"
    
    st = c.get("/api/state").json()
    assert st["scenario_mode"] == "A"
    
    # Try to recon when no incidents exist
    recon_res = c.post("/api/dispatch_drone_recon", json={"incident_id": "NON_EXISTENT"})
    assert recon_res.status_code == 400
    assert "No eligible incidents" in recon_res.json()["detail"]

def test_operator_override_requires_justification():
    """Verify operator cannot contradict scout without explicit override justification."""
    c = TestClient(app)
    c.get("/api/state")
    session_id = list(SESSION_STORE.keys())[-1]
    sim = SESSION_STORE[session_id]
    
    # Setup simulated incident and observation manually in session
    with sim.lock:
        sim.controller.process_field_report("REP_TEST_1", "Bridge 5 to 12 safe passage", "RADIO")
        incident_id = sim.controller.incident_history[-1][1].incident_id
        # Bridge 5-12 in Scenario B is open (False)
        sim.pending_observations[incident_id] = {
            "mission_id": "DRONE-TEST-01",
            "incident_id": incident_id,
            "segment": [5, 12],
            "observed_closed": False,
            "verdict": "CLEAR / PASSABLE"
        }
        
    # Operator tries to APPROVE closure (contradicting drone clear verdict) without justification
    res = c.post("/api/operator_action", json={
        "incident_id": incident_id,
        "approve_closure": True,
        "override_reason": ""
    })
    assert res.status_code == 400
    assert "OPERATOR OVERRIDE REJECTED" in res.json()["detail"]
    
    # Operator tries with short reason (<5 chars)
    res_short = c.post("/api/operator_action", json={
        "incident_id": incident_id,
        "approve_closure": True,
        "override_reason": "bad"
    })
    assert res_short.status_code == 400
    assert "minimum 5 characters" in res_short.json()["detail"]

def test_session_isolation():
    """Verify separate clients have independent state."""
    c1 = TestClient(app)
    c2 = TestClient(app)
    
    r1 = c1.get("/api/state").json()
    r2 = c2.get("/api/state").json()
    
    assert r1["session_id"] != r2["session_id"]
