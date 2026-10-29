"""
End-to-End Smoke Test for Dispatch Sentinel Digital Twin
=========================================================
Tests the complete Scenario B pipeline:
  1. Initialize session (GET /api/state)
  2. Ingest HAM_RADIO report via Nemotron (POST /api/ingest_next_incident)
  3. Confirm EVSI > 0 in verification queue (GET /api/state)
  4. Dispatch drone recon — server determines observation (POST /api/dispatch_drone_recon)
  5. Operator dismisses false rumor (POST /api/operator_action)
  6. Confirm 100% delivery ratio (GET /api/state)

Scenario B ground truth: bridge (5,12) is OPEN (false rumor scenario).
Expected outcome: drone observes CLEAR → operator dismisses → delivery stays 100%.

Usage:
    python tests/smoke_test_e2e.py [--port 8088] [--timeout 60]
"""

import sys
import json
import time
import argparse
import urllib.request
import urllib.error
import http.cookiejar

# Force UTF-8 stdout
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

BASE_URL = "http://127.0.0.1:8088"
PASS = "[PASS]"
FAIL = "[FAIL]"
INFO = "[INFO]"


def make_request(opener, url, data=None, method=None, timeout=90):
    """Make HTTP request with JSON body, return parsed response."""
    headers = {"Content-Type": "application/json"}
    if data is not None:
        encoded = json.dumps(data).encode("utf-8")
        req = urllib.request.Request(url, data=encoded, headers=headers, method=method or "POST")
    elif method == "POST":
        req = urllib.request.Request(url, data=b"", headers=headers, method="POST")
    else:
        req = urllib.request.Request(url, method=method or "GET")
    try:
        resp = opener.open(req, timeout=timeout)
        body = json.loads(resp.read().decode("utf-8"))
        return resp.status, body
    except urllib.error.HTTPError as e:
        body = {}
        try:
            body = json.loads(e.read().decode("utf-8"))
        except Exception:
            pass
        return e.code, body


def check(label, condition, detail=""):
    status = PASS if condition else FAIL
    print(f"  {status}  {label}")
    if detail:
        print(f"        → {detail}")
    if not condition:
        print(f"\033[91m        ASSERTION FAILED\033[0m")
    return condition


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8088)
    parser.add_argument("--timeout", type=int, default=90, help="Per-request timeout (s)")
    args = parser.parse_args()

    global BASE_URL
    BASE_URL = f"http://127.0.0.1:{args.port}"

    print("=" * 65)
    print("  DISPATCH SENTINEL — End-to-End Smoke Test (Scenario B)")
    print("=" * 65)

    # Build a session with cookie persistence
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    results = []

    # ── Step 1: Initialize session ────────────────────────────────────────
    print("\n[Step 1] Initialize session via GET /api/state ...")
    t0 = time.time()
    code, body = make_request(opener, f"{BASE_URL}/api/state", timeout=args.timeout)
    elapsed = time.time() - t0
    ok1 = check("GET /api/state returns 200", code == 200, f"status={code}, {elapsed:.1f}s")
    ok1b = check("Response has scenario_mode key", "scenario_mode" in body, f"keys={list(body.keys())[:6]}")
    ok1c = check("Scenario is B", body.get("scenario_mode") == "B",
                 f"scenario_mode={body.get('scenario_mode')}")
    ok1d = check("Session cookie was set", len(list(jar)) > 0, f"cookies={len(list(jar))}")
    results += [ok1, ok1b, ok1c, ok1d]

    kpi = body.get("kpi", {})
    initial_closures = kpi.get("active_closures_count", 0)
    initial_delivery = kpi.get("delivery_ratio_pct", 0.0)
    print(f"  {INFO}  Initial closures: {initial_closures}, delivery_ratio: {initial_delivery}%")

    # ── Step 2: Ingest incident via Nemotron ──────────────────────────────
    print(f"\n[Step 2] Ingest HAM_RADIO incident via Nemotron (timeout={args.timeout}s) ...")
    print(f"  {INFO}  This calls Nemotron 3.5 Lightning — expect 6-20 seconds ...")
    t0 = time.time()
    code, body2 = make_request(opener, f"{BASE_URL}/api/ingest_next_incident",
                               method="POST", timeout=args.timeout)
    elapsed = time.time() - t0
    print(f"  {INFO}  Nemotron call completed in {elapsed:.1f}s")
    ok2 = check("POST /api/ingest_next_incident returns 200", code == 200,
                f"status={code}, body={str(body2)[:120]}")
    incident_id = body2.get("incident_id", "")
    ok2b = check("Incident ID returned", bool(incident_id), f"incident_id={incident_id}")
    results += [ok2, ok2b]

    if not ok2:
        print(f"\033[91m  ABORT: Ingestion failed. Cannot continue.\033[0m")
        sys.exit(1)

    # ── Step 3: Check EVSI queue ──────────────────────────────────────────
    print(f"\n[Step 3] Check verification queue via GET /api/state ...")
    code, body3 = make_request(opener, f"{BASE_URL}/api/state", timeout=30)
    queue = body3.get("verification_queue", [])
    ok3 = check("Queue has at least 1 candidate", len(queue) >= 1, f"queue_length={len(queue)}")
    results.append(ok3)

    top = queue[0] if queue else {}
    evsi_val = top.get("evsi", None)
    ok3b = check("Top candidate EVSI is positive", evsi_val is not None and evsi_val > 0,
                 f"evsi={evsi_val}")
    ok3c = check("Top candidate is our ingested incident",
                 top.get("incident_id") == incident_id,
                 f"top_id={top.get('incident_id')}, expected={incident_id}")
    is_worth = top.get("is_worth_scouting", top.get("worth_scouting"))
    ok3d = check("worth_scouting is True", is_worth is True,
                 f"is_worth_scouting={is_worth}")
    results += [ok3b, ok3c, ok3d]
    print(f"  {INFO}  EVSI={evsi_val}, segment={top.get('segment')}, "
          f"confidence={top.get('confidence')}")

    # ── Step 4: Dispatch drone recon (server-authoritative) ───────────────
    print(f"\n[Step 4] Dispatch drone recon via POST /api/dispatch_drone_recon ...")
    code, body4 = make_request(
        opener,
        f"{BASE_URL}/api/dispatch_drone_recon",
        data={"incident_id": incident_id},
        timeout=30
    )
    ok4 = check("POST /api/dispatch_drone_recon returns 200", code == 200,
                f"status={code}, body={str(body4)[:200]}")
    results.append(ok4)

    if not ok4:
        print(f"\033[91m  ABORT: Recon failed (status={code}): {body4}\033[0m")
        sys.exit(1)

    obs = body4.get("observation", {})
    verdict = obs.get("verdict", "")
    ok4b = check("Observation has a verdict", bool(verdict), f"verdict={verdict}")
    ok4c = check("Verdict indicates CLEAR (bridge is open in Scenario B)",
                 "CLEAR" in verdict.upper() or "OPEN" in verdict.upper(),
                 f"verdict={verdict} (ground truth: bridge 5-12 is OPEN)")
    results += [ok4b, ok4c]
    print(f"  {INFO}  Drone observation: {verdict}")
    print(f"  {INFO}  Observation details: {obs}")

    # ── Step 5: Operator dismisses false rumor ────────────────────────────
    print(f"\n[Step 5] Operator action — dismiss false closure rumor ...")
    op_payload = {
        "incident_id": incident_id,
        "approve_closure": False,
        "operator_display_name": "Chief Logistics Commander"
        # No override_reason needed since operator agrees with drone (CLEAR → dismiss)
    }
    code, body5 = make_request(
        opener,
        f"{BASE_URL}/api/operator_action",
        data=op_payload,
        timeout=30
    )
    ok5 = check("POST /api/operator_action returns 200", code == 200,
                f"status={code}, body={str(body5)[:200]}")
    results.append(ok5)

    if not ok5:
        print(f"\033[91m  ABORT: Operator action failed: {body5}\033[0m")
        sys.exit(1)

    action_taken = body5.get("action", "")
    delivery_ratio = body5.get("plan_delivery_ratio", 0)
    distance_km = body5.get("plan_distance_km", 0)

    ok5b = check("Action recorded (dismiss/rumor dismissed)", bool(action_taken),
                 f"action={action_taken}")
    ok5c = check("Delivery ratio is 100%", abs(delivery_ratio - 1.0) < 0.001,
                 f"delivery_ratio={delivery_ratio:.1%}")
    results += [ok5b, ok5c]
    print(f"  {INFO}  Action: {action_taken}")
    print(f"  {INFO}  Delivery ratio: {delivery_ratio:.1%}")
    print(f"  {INFO}  Fleet distance: {distance_km} km")

    # ── Step 6: Final state verification ─────────────────────────────────
    print(f"\n[Step 6] Final state confirmation ...")
    code, body6 = make_request(opener, f"{BASE_URL}/api/state", timeout=30)
    ok6 = check("Final GET /api/state returns 200", code == 200, f"status={code}")
    final_kpi = body6.get("kpi", {})
    final_delivery = final_kpi.get("delivery_ratio_pct", 0)
    final_closures = final_kpi.get("active_closures_count", -1)
    ok6b = check("Final delivery ratio is 100%", abs(final_delivery - 100.0) < 0.1,
                 f"final_delivery={final_delivery}%")
    ok6c = check("Active closures still = initial count (5-12 confirmed clear)",
                 final_closures == initial_closures,
                 f"final_closures={final_closures}, initial={initial_closures}")
    ok6d = check("Queue is now empty (incident resolved)", len(body6.get("verification_queue", [])) == 0,
                 f"queue_length={len(body6.get('verification_queue', []))}")
    results += [ok6, ok6b, ok6c, ok6d]

    # ── Summary ───────────────────────────────────────────────────────────
    passed = sum(1 for r in results if r)
    total = len(results)
    print("\n" + "=" * 65)
    print(f"  SMOKE TEST RESULT: {passed}/{total} checks passed")
    if passed == total:
        print("  [PASS] ALL CHECKS PASSED - Scenario B pipeline verified")
        print("  Dispatch correctly dismissed a false closure rumor.")
        print(f"  Fleet delivery maintained at 100% ({delivery_ratio:.1%}).")
    else:
        failed_count = total - passed
        print(f"  [FAIL] {failed_count} checks FAILED")
    print("=" * 65)

    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
