# Phase 1: Production Reliability & Stress Challenge Report

**Target Deployment:** `https://dispatch-sentinel.onrender.com`  
**Deploy ID:** `dep-dbibtqp7lnhs73edffqg` (Commit `c34b22fab3977acdbbb83a41d6251e4762b2bcc1`)  
**Audit Timestamp:** 2026-10-30T16:07:22Z  
**Target Environment:** Render Cloud Container (Free Tier, Linux x86_64)

---

## 1. Executive Summary & Verdict

| Challenge Dimension | Requirement | Measured Result | Status |
| :--- | :--- | :--- | :---: |
| **Consecutive Fresh Missions** | 10 consecutive full cycles | **10 / 10 Success (100%)** | **PASS** |
| **Nemotron 3.5 Lightning Ingest** | Segment extraction `[5, 12]` | **10 / 10 Extracted Cleanly** | **PASS** |
| **Pillar Sequence Execution** | State → Ingest → Recon → Op → CF → EVSI | **10 / 10 Complete Sequences** | **PASS** |
| **Average End-to-End Cycle** | Reliable latency | **24.89s mean duration** | **PASS** |
| **Concurrent Health Probes** | Non-blocking `/health` (< 2.0s) | **8 / 8 probes OK (mean 0.93s)** | **PASS** |
| **Session Isolation & State** | Unique HMAC session cookie | **10 unique tokens, 0 crosstalk** | **PASS** |
| **Secret & Leak Protection** | Zero leaked credentials in public API | **Verified clean (0 leaks)** | **PASS** |

---

## 2. 10 Consecutive Fresh-Session Execution Log

Every mission initiated a brand-new session token (`dispatch_session_token`), pulled initial state, invoked live NVIDIA Nemotron 3.5 Lightning via Nebius Token Factory, dispatched drone reconnaissance, executed the human operator gate, re-solved OR-Tools routing, verified the counterfactual baseline, and retrieved the EVSI inspector breakdown.

| Run # | Total Time (s) | Nemotron Latency (s) | Extracted Segment | Scout Verdict | Baseline Gain (pp) | Status |
| :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **01** | 28.36 | 8.88 | `[5, 12]` | `SCOUT_OBSERVED_CLEAR` | +78.6 pp | **PASS** |
| **02** | 25.51 | 9.19 | `[5, 12]` | `SCOUT_OBSERVED_CLEAR` | +78.6 pp | **PASS** |
| **03** | 22.99 | 6.76 | `[5, 12]` | `SCOUT_OBSERVED_CLEAR` | +78.6 pp | **PASS** |
| **04** | 23.49 | 7.57 | `[5, 12]` | `SCOUT_OBSERVED_CLEAR` | +78.6 pp | **PASS** |
| **05** | 23.49 | 7.68 | `[5, 12]` | `SCOUT_OBSERVED_CLEAR` | +78.6 pp | **PASS** |
| **06** | 24.71 | 8.29 | `[5, 12]` | `SCOUT_OBSERVED_CLEAR` | +78.6 pp | **PASS** |
| **07** | 21.50 | 5.50 | `[5, 12]` | `SCOUT_OBSERVED_CLEAR` | +78.6 pp | **PASS** |
| **08** | 23.71 | 7.86 | `[5, 12]` | `SCOUT_OBSERVED_CLEAR` | +78.6 pp | **PASS** |
| **09** | 23.80 | 7.80 | `[5, 12]` | `SCOUT_OBSERVED_CLEAR` | +78.6 pp | **PASS** |
| **10** | 30.33 | 13.84 | `[5, 12]` | `SCOUT_OBSERVED_CLEAR` | +78.6 pp | **PASS** |

* **Success Rate:** 10/10 (100.0%)
* **Mean Total Cycle Time:** 24.89 seconds (Min: 21.50s, Max: 30.33s, StdDev: 2.65s)
* **Mean Nemotron Inference Time:** 8.34 seconds (Min: 5.50s, Max: 13.84s)

---

## 3. Concurrency & Health Starvation Check

To verify that synchronous LLM inference and OR-Tools VRP computation do not starve health probes (which previously caused Render instance restart loops), 8 concurrent `/health` probe requests were fired simultaneously while an active Nemotron extraction mission was processing on the server.

```
Health Probe 1: OK in 1.026s
Health Probe 2: OK in 0.940s
Health Probe 3: OK in 0.987s
Health Probe 4: OK in 0.948s
Health Probe 5: OK in 0.903s
Health Probe 6: OK in 0.901s
Health Probe 7: OK in 0.877s
Health Probe 8: OK in 0.855s
Concurrent Mission Under Load: SUCCESS in 25.381s
```

* **Result:** Zero probe drops, zero restarts. The asynchronous worker implementation (`ThreadPoolExecutor` via `run_in_executor`) ensures that `/health` responds in < 1.05s even under peak inference and route-solving load.

---

## 4. Security & Data Leak Audit

1. **API Keys:** Checked `GET /`, `GET /api/state`, `GET /api/counterfactual_state`, and `GET /api/evsi_inspector/*`. No instance of `NEBIUS_API_KEY`, `tokenfactory`, or Bearer credentials exists in any response payload.
2. **Hidden Ground Truth:** In `GET /api/state`, ground truth is never serialized to the client. The client receives only `active_closures` and `incident_feed` (unverified reports). Ground truth is strictly server-authoritative in `sim.hidden_ground_truth`.
3. **Session Token Isolation:** Session cookies use `HttpOnly`, `SameSite=Lax`, and HMAC signature verification. State manipulation across session boundaries is strictly rejected.
