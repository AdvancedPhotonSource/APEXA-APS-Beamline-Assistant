"""One compact index of every attempted trial, with pending audits explicit."""
from collections import Counter
import csv
import json
from pathlib import Path

from prepare_fixtures import write_json


def report(root):
    spec=json.loads((root/'setup/study.json').read_text())
    record_file=root/'results/attempts.jsonl'
    records=[json.loads(line) for line in record_file.read_text().splitlines()] if record_file.exists() else []
    rows=[]
    for record in records:
        entry=record['entry'];execution=record.get('execution',{});audit=record.get('model_audit',{})
        assessment=record.get('assessment',{})
        rows.append({**{k:entry[k] for k in ('index','fixture','arm','condition')},
            'execution':execution.get('outcome'),'requests':audit.get('requests'),
            'elapsed_s':execution.get('elapsed_s'),'scientific_evaluable':audit.get('scientific_evaluable',False),
            'manifest_status':assessment.get('manifest_status'),'decision':assessment.get('decision'),
            'incorrect_release':assessment.get('incorrect_release'),
            'valid_retention_pending_claim_review':assessment.get('valid_result_retained',False),
            'hold_with_specific_evidence_pending_review':assessment.get('justified_hold',False),
            'repair_audit_pending':bool(assessment.get('repair_audit')),
            'record':f"trial{entry['index']:02d}/record.json"})
    groups=[]
    for arm in ('A1','A5'):
        for feedback in ('automatic','on_demand'):
            group=[r for r in rows if r['arm']==arm and r['condition']==feedback]
            groups.append({'arm':arm,'feedback':feedback,'attempted':len(group),
                'manifest_status_counts':dict(Counter(str(x['manifest_status']) for x in group)),
                'execution_counts':dict(Counter(str(x['execution']) for x in group)),
                'evaluable_transport':sum(x['scientific_evaluable'] for x in group),
                'decisions':dict(Counter(str(x['decision']) for x in group))})
    write_json(root/'results/summary.json',{'assessment_status':'deterministic_checks_with_trace_and_claim_audit_pending',
        'attempted':len(rows),'scheduled':len(spec.get('trial_schedule',range(16))),'groups':groups,'attempts':rows,
        'scope':'Exploratory review-and-repair pilot on two acquisitions; no general harness ranking.'})
    if rows:
        target=root/'results/attempts.csv';temp=target.with_suffix('.csv.tmp')
        with temp.open('w',newline='') as f:
            writer=csv.DictWriter(f,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
        temp.replace(target)
