import os
import sys
from dispatch.triage import triage_incident_with_nemotron
from dispatch.models import IncidentReport, IncidentType

with open('.env') as f:
    for line in f:
        if line.startswith('NEBIUS_API_KEY='):
            os.environ['NEBIUS_API_KEY'] = line.split('=', 1)[1].strip().strip('"')

rep_rumor = IncidentReport(
    report_id='LIVE_TEST_RUMOR',
    raw_text='Citizen Telegram Chatter: Someone said the South Truss Bridge between 8 and 15 is shaking violently and might be cracked, avoid it guys!',
    source_channel='SOCIAL_MEDIA',
    source_authenticated=False,
    timestamp=1700000000.0
)

print("Calling Nemotron on unauthenticated rumor...")
triage = triage_incident_with_nemotron(rep_rumor)
print('Triage Status:', triage.verification_status)
print('Incident Type:', triage.incident_type)
print('Segment:', triage.affected_segment)
print('Summary:', triage.reasoning_summary)
print('Requires Operator Approval:', triage.requires_operator_approval)
print('Actionable directly:', triage.is_actionable)
print('Latency (ms):', triage.latency_ms)
