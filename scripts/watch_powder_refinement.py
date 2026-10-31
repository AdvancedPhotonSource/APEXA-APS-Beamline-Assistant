#!/usr/bin/env python3
"""Follow an existing powder run in a terminal, using recorded evidence only."""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from apexa_refinement_live import _resolve, publication_request, refinement_snapshot


def number(value, digits=3):
    return f'{value:.{digits}f}' if isinstance(value, (int, float)) and math.isfinite(value) else 'unavailable'


def snapshot(attempt, name):
    if not isinstance(name, str) or not re.fullmatch(r'snapshot-[0-9]{6}', name):
        return {}
    return json.loads((attempt / 'decision_stream' / f'{name}.json').read_text())


def evidence_lines(data, baseline=None):
    before = {h['index']: h for h in (baseline or {}).get('histograms', [])}
    for hist in data.get('histograms', []):
        label = hist.get('name', f"Histogram {hist['index']}")
        if hist.get('error'):
            yield f'  {label}: evidence unavailable: {hist["error"]}'
            continue
        old = before.get(hist['index'], {})
        rwp = number(hist.get('metrics', {}).get('Rwp'))
        if old:
            rwp = number(old.get('metrics', {}).get('Rwp')) + '% -> ' + rwp
        yield f'  {label}: Rwp {rwp}%'
        classifier = hist.get('classifier', {})
        scope = 'used in ranking bias' if classifier.get('used_for_priority_bias') else 'diagnostic only'
        yield f'    Residual shape: {classifier.get("dominant", "unavailable")} ({scope}; heuristic)'
        comparisons = {w['id']: w for w in hist.get('comparison_windows', [])}
        for window in (old.get('windows') or hist.get('windows') or [])[:2]:
            lo, hi = window['range']
            loss = number(window.get('weighted_loss'), 1)
            matched = comparisons.get(window['id']) if old else None
            if matched:
                loss += ' -> ' + number(matched.get('weighted_loss'), 1)
            role = 'ranking evidence' if window.get('used_for_priority_bias') else 'diagnostic'
            yield f'    Window {lo:.3f}-{hi:.3f} {hist.get("axis", "")}: weighted loss {loss} ({role})'


def event_lines(event, attempt):
    kind = event.get('event', 'event')
    step = event.get('driver_step', '?')
    prefix = f'Step {step}'
    if kind == 'evaluating':
        yield f'{prefix}: {event.get("stage", "evaluating")}'
    elif kind == 'candidate_ranking':
        candidates = event.get('candidates', [])
        yield f'{prefix}: ranked {len(candidates)} candidates; top three:'
        for candidate in candidates[:3]:
            yield (f'  {candidate.get("name")}: score={number(candidate.get("score"))}, '
                   f'leverage={number(candidate.get("leverage"))}, bias={number(candidate.get("priority_bias"))}')
        yield from evidence_lines(snapshot(attempt, event.get('snapshot')))
    elif kind == 'candidate_selected':
        yield f'{prefix}: selected {event.get("picked")} ({event.get("category")})'
    elif kind == 'candidate_skipped':
        yield f'{prefix}: skipped {event.get("picked")}: {event.get("reason", "reason unavailable")}'
    elif kind == 'trial_result':
        yield (f'{prefix}: requested release {", ".join(event.get("refined") or []) or "unavailable"}; '
               f'ranked parameter {event.get("picked", "unavailable")}')
        data = snapshot(attempt, event.get('snapshot'))
        varying = data.get('varying_parameters', [])
        if varying:
            yield '  Actually varying: ' + ', '.join(map(str, varying))
    elif kind == 'step_result':
        before, after = event.get('Rwp_before'), event.get('Rwp_after')
        outcome = 'ROLLED BACK' if event.get('rolled_back') else 'RETAINED'
        if isinstance(before, (int, float)) and isinstance(after, (int, float)):
            outcome += '; residual improved' if after < before else '; residual worsened' if after > before else '; residual unchanged'
        yield f'{prefix}: {outcome}; joint trial Rwp {number(before)}% -> {number(after)}%'
        if event.get('reason'):
            yield '  Reason: ' + str(event['reason'])
        baseline = snapshot(attempt, event.get('before_snapshot'))
        trial = snapshot(attempt, event.get('trial_snapshot'))
        if trial:
            yield '  Before -> trial evidence (trial precedes any rollback):'
            yield from evidence_lines(trial, baseline)
    elif kind == 'finished':
        yield f'Refinement decisions finished: {event.get("reason") or event.get("stop_reason") or "awaiting final result"}'
    else:
        yield f'{prefix}: {kind}'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('job_id', help='APEXA group ID or individual engine job ID')
    parser.add_argument('--jobs-dir', type=Path)
    parser.add_argument('--interval', type=float, default=3)
    parser.add_argument('--tail-events', type=int, default=10, help='Recent events to replay when attaching')
    parser.add_argument('--log', type=Path, help='Also append readable updates to this file')
    parser.add_argument('--details', action='store_true', help='Also show candidate rankings, skipped parameters and every trial release')
    args = parser.parse_args()
    if args.interval < 1 or args.tail_events < 0:
        parser.error('interval must be at least 1 second and tail-events nonnegative')
    if args.jobs_dir:
        os.environ['APEXA_GSAS2_JOBS'] = str(args.jobs_dir.expanduser().resolve())
    root, _ = _resolve(args.job_id)
    expects_report = publication_request(args.job_id) is not None
    log = None
    if args.log:
        args.log.expanduser().parent.mkdir(parents=True, exist_ok=True)
        log = args.log.expanduser().open('a', encoding='utf-8', buffering=1)

    def emit(message, timestamp=None):
        stamp = time.strftime('%H:%M:%S', time.localtime(timestamp or time.time()))
        line = f'[{stamp}] {message}'
        print(line, flush=True)
        if log:
            log.write(line + '\n')

    emit(f'APEXA live refinement: {args.job_id}')
    emit('Following recorded decisions; Ctrl-C stops this monitor only.')
    emit('Peak-window losses may overlap. Retained steps do not establish scientific validity.')
    seen, states, results, histories = {}, {}, set(), {}
    last_heartbeat = 0
    last_publication = last_error = None
    try:
        while True:
            try:
                current = refinement_snapshot(args.job_id)
                if last_error:
                    emit('Monitoring connection restored.')
                    last_error = None
                for job in current['jobs']:
                    key = (job['id'], job.get('attempt'))
                    if states.get(key) != job['state']:
                        emit(f'{job["id"]}: {job["state"]}' + (f' — {job["error"]}' if job.get('error') else ''))
                        states[key] = job['state']
                    events = job.get('decision', {}).get('events', [])
                    if key not in seen:
                        seen[key] = 0
                        tail = events[-args.tail_events:] if args.tail_events else []
                        if len(events) > len(tail):
                            seen[key] = events[len(events) - len(tail) - 1].get('sequence', 0)
                            emit(f'Replaying the latest {len(tail)} of {len(events)} recorded events.')
                    for event in events:
                        if event.get('sequence', 0) <= seen[key]:
                            continue
                        if args.details or event.get('event') in ('evaluating', 'step_result', 'finished'):
                            attempt = root / job['id'] / job['attempt']
                            for line in event_lines(event, attempt):
                                emit(line, event.get('timestamp'))
                        seen[key] = event['sequence']
                    steps = [e for e in events if e.get('event') == 'step_result']
                    if steps and histories.get(key) != len(steps):
                        emit('RUN HISTORY — lower Rwp means closer profile agreement')
                        emit('Step  Joint Rwp before -> trial   Decision       Requested change')
                        for step in steps:
                            related = [e for e in events if e.get('driver_step') == step.get('driver_step') and e.get('event') == 'trial_result']
                            trial = related[-1] if related else step
                            decision = 'rolled back' if step.get('rolled_back') else 'kept'
                            emit(f'{step.get("driver_step", "?")!s:>4}  {number(step.get("Rwp_before")):>7}% -> '
                                 f'{number(step.get("Rwp_after")):>7}%   {decision:<13} '
                                 f'{", ".join(trial.get("refined") or []) or trial.get("category", "unavailable")}')
                        start_rwp = steps[0].get('Rwp_before')
                        last = steps[-1]
                        current_rwp = last.get('Rwp_before') if last.get('rolled_back') else last.get('Rwp_after')
                        emit(f'Start -> latest kept fit: {number(start_rwp)}% -> {number(current_rwp)}%')
                        histories[key] = len(steps)
                    result = job.get('result') or {}
                    if result and key not in results:
                        emit(f'FINAL: scientific status {result.get("scientific_status", "unavailable")}; '
                             f'Rwp {number(result.get("Rwp"))}%; GOF {number(result.get("GOF"))}; '
                             f'stop={result.get("stop_reason", "unavailable")}')
                        for hist in result.get('per_histogram_metrics', []):
                            emit(f'  {hist.get("name")}: Rwp {number(hist.get("Rwp"))}%')
                        for check, coverage in (result.get('evidence_coverage') or {}).items():
                            if isinstance(coverage, dict):
                                emit(f'  {check}: {coverage.get("status", "unavailable")} '
                                     f'{coverage.get("reason") or coverage.get("error") or ""}')
                        results.add(key)
                publication = current.get('publication') or {}
                report = publication.get('report') or {}
                signature = (publication.get('state'), report.get('state'), report.get('path'),
                             publication.get('error'), report.get('error'))
                if signature != last_publication:
                    emit(f'Artifacts: {publication.get("state", "unavailable")}; '
                         f'report: {report.get("state", "awaiting generation" if expects_report else "not requested")}')
                    if publication.get('error') or report.get('error'):
                        emit(str(publication.get('error') or report['error']))
                    last_publication = signature
                if current['terminal'] and (not expects_report or report.get('state') == 'published'):
                    if report.get('path'):
                        emit('REPORT READY: ' + report['path'])
                    emit('Monitoring complete.')
                    return
                if current['terminal'] and publication.get('state') == 'waiting_timeout':
                    emit('Refinement ended; report publication timed out. See export-status.json.')
                    return
                if time.monotonic() - last_heartbeat >= 15:
                    for job in current['jobs']:
                        latest = job.get('decision', {}).get('latest', {})
                        if job['state'] in ('running', 'queued', 'pending'):
                            emit(f'{job["state"]}: step {latest.get("driver_step", "?")}; '
                                 f'{latest.get("stage") or latest.get("event", "waiting for evidence")}; '
                                 f'heartbeat age {number(job.get("heartbeat_age_seconds"), 0)}s')
                    if current['terminal']:
                        emit('Refinement ended; waiting for the automatic report publisher.')
                    last_heartbeat = time.monotonic()
            except (OSError, ValueError, sqlite3.Error) as exc:
                if str(exc) != last_error:
                    emit(f'Monitor read failed; retrying: {exc}')
                    last_error = str(exc)
            time.sleep(args.interval)
    except KeyboardInterrupt:
        emit('Monitor stopped.')
    finally:
        if log:
            log.close()


if __name__ == '__main__':
    main()
