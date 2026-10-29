import os
import sys
from dispatch.triage import triage_incident_with_nemotron
from dispatch.models import IncidentReport, IncidentType

with open('.env') as f:
    for line in f:
        if line.startswith('NEBIUS_API_KEY='):
            os.environ['NEBIUS_API_KEY'] = line.split('=', 1)[1].strip().strip('"')

rep = IncidentReport(
    report_id='LIVE_TEST_01',
    raw_text='Civil Defense Dispatch: Flash flood collapsed the Central Expressway Bridge between junction 5 and junction 12. Both lanes blocked.',
    source_channel='CIVIL_DEFENSE',
    source_authenticated=True,
    timestamp=1700000000.0
)

print("Calling Nemotron via triage_incident_with_nemotron()...")
triage = triage_incident_with_nemotron(rep)
print('Triage Status:', triage.verification_status)
print('Incident Type:', triage.incident_type)
print('Segment:', triage.affected_segment)
print('Summary:', triage.reasoning_summary)
print('Confidence:', triage.confidence_score)
print('Latency (ms):', triage.latency_ms)
