# Dispatch Sentinel — AI-Powered Disaster Digital Twin

> **Nebius × NVIDIA Hackathon 2026 Submission**  
> Category: Agentic AI Systems • Infrastructure Resilience

[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.12-green.svg)](https://python.org)
[![Three.js](https://img.shields.io/badge/Three.js-r128-orange.svg)](https://threejs.org)

---

## What It Does

Dispatch Sentinel is a real-time disaster-response digital twin that uses **NVIDIA Nemotron 3.5 Lightning** (via Nebius Token Factory) to triage unverified civilian emergency reports, applies **Expected Value of Sample Information (EVSI)** decision theory to determine when autonomous drone reconnaissance is justified, and re-optimizes convoy delivery routes using **Google OR-Tools** — all before a human watch commander authorizes any closure.

**Flagship result (Scenario B — Critical Bottleneck Bridge):**

| Policy | Planned Fulfillment | Sectors Served |
|--------|--------------------:|---------------:|
| Conservative (no verification) | **21.4%** | 2/8 |
| Dispatch Sentinel | **100.0%** | 8/8 |
| **Advantage** | **+78.6 pp** | **+6 facilities** |

> This result is for a specific frozen disaster configuration. See `BENCHMARK_V3_RESULTS.json` for multi-scenario results.

---

## Architecture

```
Ham Radio / Social Media Reports
          ↓
  [Nemotron 3.5 Lightning]  ← Nebius Token Factory (NVIDIA NIM)
  Structured triage + segment extraction
          ↓
  [EVSI Governor]
  Restricted-policy decision: scout or act?
  EVSI_R = E_y[E_{x|y}[U(π_R(y),x)]] - E_x[U(π_0,x)] - C_recon
          ↓
  [Drone Reconnaissance]  (simulated, sensor η=0.999)
          ↓
  [Human Operator Gate]  ← required before any road closure
          ↓
  [Google OR-Tools VRP]  ← route reoptimization
          ↓
  [Three.js 3D Digital Twin]  ← live visualization
```

---

## Quick Start

### Prerequisites
- Python 3.12+
- Docker (optional)
- Nebius API key (for live Nemotron inference)

### Local Run

```bash
git clone https://github.com/AmanM006/dispatch
cd dispatch

# Copy and fill in your Nebius key
cp .env.example .env
# Edit .env: NEBIUS_API_KEY=your_key_here

pip install -r requirements.txt
python -m uvicorn dispatch.twin_server:app --host 0.0.0.0 --port 8088
# Open http://localhost:8088
```

### Docker

```bash
docker build -t dispatch-sentinel .
docker run -e NEBIUS_API_KEY=your_key_here -p 8088:8080 dispatch-sentinel
# Open http://localhost:8088
```

### Run Tests

```bash
export PYTHONPATH=.
pytest tests/test_twin_api.py -v          # 4/4 API unit tests
python tests/smoke_test_e2e.py            # 20/20 live end-to-end test (calls Nemotron)
```

---

## Key Features

### 1. Live Nemotron AI Triage
Unstructured civilian reports (ham radio, social media) → structured `{segment, confidence, is_rumor}` JSON via Nemotron 3.5 Lightning.

### 2. EVSI-Gated Reconnaissance
Before dispatching a drone, the governor computes restricted-policy EVSI:
```
EVSI_R = E_y[E_{x|y}[U(π_R(y), x)]] - E_x[U(π_0, x)] - C_recon
```
If EVSI ≤ 0, scouting is not justified. The drone is dispatched only when information gain exceeds cost.

### 3. Human-in-the-Loop Authorization
No road is ever closed without an operator reviewing the drone's sensor observation and explicitly authorizing. All actions are audit-logged.

### 4. Counterfactual Comparison
One-click toggle shows what would have happened under the conservative "do nothing" baseline — same scenario, independently computed routes.

### 5. 3D Digital Twin (Three.js)
Real-time animated map showing convoy routes, destroyed bridges, isolated facilities, hospital markers, and live KPI telemetry.

---

## API Reference

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | Serve twin_ui.html |
| `/api/state` | GET | Full simulation state |
| `/api/switch_scenario/{A\|B}` | POST | Load disaster scenario |
| `/api/ingest_next_incident` | POST | Triage next report via Nemotron |
| `/api/dispatch_drone_recon` | POST | Dispatch reconnaissance drone |
| `/api/operator_action` | POST | Human authorization gate |
| `/api/counterfactual_state` | GET | Conservative baseline comparison |
| `/api/evsi_inspector/{id}` | GET | Full EVSI mathematical breakdown |

---

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `NEBIUS_API_KEY` | Yes | Nebius Token Factory API key |
| `PORT` | No | Server port (default: 8080 Docker / 8088 local) |

**Never commit `.env` to version control.** See `.gitignore`.

---

## Benchmark Results

See `BENCHMARK_V3_RESULTS.json` for full multi-scenario results including:
- Scenario A (baseline urban routing)
- Scenario B (critical bottleneck — flagship scenario)
- Centrality and severity baseline comparisons

---

## Project Structure

```
dispatch/
├── twin_server.py      # FastAPI server + session management
├── twin_ui.html        # Three.js digital twin UI (single-file)
├── controller.py       # Google OR-Tools VRP route optimizer
├── triage.py           # Nemotron integration + EVSI governor
└── scenario_loader.py  # Disaster scenario definitions

tests/
├── test_twin_api.py    # Unit tests (4 tests, no external calls)
└── smoke_test_e2e.py   # End-to-end test (calls live Nemotron)
```

---

## License

Apache 2.0 — see [LICENSE](LICENSE).

---

## Team

Built for the Nebius × NVIDIA Hackathon 2026.
