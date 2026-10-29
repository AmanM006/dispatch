"""
DISPATCH V3 - NVIDIA Nemotron Triage Engine
Calls Token Factory to parse unstructured disaster reports into structured operational decisions.
Enforces fail-closed schema parsing and external source verification.
"""

import os
import time
import json
import re
import urllib.request
from typing import Dict, Any, Optional

from dispatch.models import (
    IncidentReport,
    TriageDecision,
    IncidentType,
    VerificationStatus
)

def triage_incident_with_nemotron(
    report: IncidentReport,
    model_id: str = "nvidia/Nemotron-3_5-Lightning",
    timeout_sec: int = 15
) -> TriageDecision:
    """
    Parses unstructured text with NVIDIA Nemotron 3.5 Lightning.
    Extracts structured constraints:
    - Incident Type (ROAD_CLOSURE, ROAD_REOPENING, DEMAND_SURGE, etc.)
    - Affected Junction Segment (u, v)
    - Rumor / Disinformation detection
    - Triage Verification Gate (human approval required for unauthenticated or ambiguous reports)
    """
    api_key = os.environ.get("NEBIUS_API_KEY")

    # If no live API key is set in the environment, return a clean fail-closed triage record
    if not api_key:
        return TriageDecision(
            incident_id=report.report_id,
            incident_type=IncidentType.OTHER,
            affected_segment=None,
            confidence_score=0.0,
            is_actionable=False,
            requires_operator_approval=True,
            verification_status=VerificationStatus.PENDING_OPERATOR_APPROVAL,
            reasoning_summary="NEBIUS_API_KEY not configured in environment. Failed-closed for safety.",
            latency_ms=0.0,
            model_id=model_id,
            operator_notes="API key required for live AI triage."
        )

    url = "https://api.tokenfactory.nebius.com/v1/chat/completions"

    system_prompt = (
        "You are DISPATCH Emergency Logistics AI. You analyze raw disaster field reports "
        "and output strictly valid JSON describing physical road closures and supply surges. "
        "Respond ONLY with valid JSON. Never output conversational text or thinking blocks outside JSON."
    )

    user_prompt = f"""Field Report ID: {report.report_id}
Source Channel: {report.source_channel} (Authenticated: {report.source_authenticated})
Report Content: "{report.raw_text}"

Analyze whether this report indicates an actionable road closure, reopening, or demand surge.
Identify the numerical junction IDs (pure integers, e.g. [5, 12]) if mentioned.
Flag if the report contains unverified rumors, social media speculation, or disinformation.

Output MUST be strictly valid JSON with this exact schema:
{{
  "incident_type": "ROAD_CLOSURE" | "ROAD_REOPENING" | "DEMAND_SURGE" | "TRAFFIC_DELAY" | "OTHER",
  "affected_segment": [5, 12] or null,
  "confidence_score": float between 0.0 and 1.0,
  "is_unverified_rumor": boolean,
  "explanation": "concise physical description"
}}"""

    payload = json.dumps({
        "model": model_id,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        "temperature": 0.1,
        "max_tokens": 2048
    }).encode("utf-8")

    req = urllib.request.Request(url, data=payload, headers={
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    })

    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
            raw_data = json.loads(resp.read().decode("utf-8"))
        latency_ms = (time.time() - t0) * 1000.0
        usage = raw_data.get("usage", {})
        content = raw_data["choices"][0]["message"]["content"]

        # Fail-closed JSON extraction
        matches = re.findall(r'\{[^{}]*"incident_type"[^{}]*\}', content, re.DOTALL)
        if not matches:
            # Fallback regex search for outermost braces
            idx = content.find('{')
            end_idx = content.rfind('}')
            if idx != -1 and end_idx != -1:
                parsed_json = json.loads(content[idx:end_idx+1])
            else:
                raise ValueError("Model output did not contain valid JSON schema")
        else:
            parsed_json = json.loads(matches[-1])

        # Validate typed fields
        inc_type_str = parsed_json.get("incident_type", "OTHER")
        try:
            inc_type = IncidentType(inc_type_str)
        except Exception:
            inc_type = IncidentType.OTHER

        segment = parsed_json.get("affected_segment")
        valid_segment = None
        if segment and isinstance(segment, list) and len(segment) == 2:
            try:
                # Clean up if model outputs strings like "junction 5", "junction_5", "j5", or integers
                u_digits = re.sub(r'[^0-9]', '', str(segment[0]))
                v_digits = re.sub(r'[^0-9]', '', str(segment[1]))
                if u_digits and v_digits:
                    valid_segment = (int(u_digits), int(v_digits))
            except Exception:
                valid_segment = None

        confidence = float(parsed_json.get("confidence_score", 0.5))
        is_rumor = bool(parsed_json.get("is_unverified_rumor", False))
        explanation = str(parsed_json.get("explanation", ""))

        # EVIDENCE-TO-ACTION SAFETY POLICY:
        # 1. If source is unauthenticated or report is a rumor -> PENDING_OPERATOR_APPROVAL (Requires Human)
        # 2. If source is authenticated and concrete segment extracted -> CONFIRMED_FACT (Auto-Actionable)
        # 3. If invalid or unparseable segment -> REJECTED_UNVERIFIED
        if not report.source_authenticated or is_rumor:
            status = VerificationStatus.PENDING_OPERATOR_APPROVAL
            is_actionable = False
            requires_human = True
        elif valid_segment is not None and inc_type in [IncidentType.ROAD_CLOSURE, IncidentType.ROAD_REOPENING]:
            status = VerificationStatus.CONFIRMED_FACT
            is_actionable = True
            requires_human = False
        else:
            status = VerificationStatus.REJECTED_UNVERIFIED
            is_actionable = False
            requires_human = True

        return TriageDecision(
            incident_id=report.report_id,
            incident_type=inc_type,
            affected_segment=valid_segment,
            confidence_score=confidence,
            is_actionable=is_actionable,
            requires_operator_approval=requires_human,
            verification_status=status,
            reasoning_summary=explanation,
            latency_ms=round(latency_ms, 1),
            model_id=model_id,
            tokens_used=usage
        )

    except Exception as e:
        latency_ms = (time.time() - t0) * 1000.0
        return TriageDecision(
            incident_id=report.report_id,
            incident_type=IncidentType.OTHER,
            affected_segment=None,
            confidence_score=0.0,
            is_actionable=False,
            requires_operator_approval=True,
            verification_status=VerificationStatus.REJECTED_UNVERIFIED,
            reasoning_summary=f"Parsing error: {str(e)}",
            latency_ms=round(latency_ms, 1),
            model_id=model_id,
            operator_notes="Exception caught during AI triage. Fail-closed."
        )
