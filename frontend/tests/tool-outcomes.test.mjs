import { test } from 'node:test'
import assert from 'node:assert/strict'
import { extractArtifacts, inferStatus, parseDirectToolResult, parseToolResults } from '../src/lib/parseToolResult.ts'

const status = data => parseDirectToolResult('fixture', JSON.stringify(data)).status

test('typed job and scientific outcomes do not become successful cards', () => {
  for (const [execution, scientific_verdict, expected] of [
    ['pending', 'unassessed', 'pending'], ['running', 'unassessed', 'running'],
    ['completed', 'provisional', 'provisional'], ['completed', 'rejected', 'rejected'],
    ['denied', 'unassessed', 'denied'], ['cancelled', 'unassessed', 'cancelled'],
    ['failed', 'unassessed', 'error'],
  ]) {
    const outcome = { execution, scientific_verdict }
    assert.equal(status({ success: false, result: {}, outcome }), expected)
    assert.equal(status({ _apexa_outcome: outcome }), expected)
  }
  assert.equal(status({ success: true, outcome: { execution: 'completed', scientific_verdict: 'accepted' } }), 'completed')
})

test('explicit execution errors override optimistic envelopes and legacy aliases remain distinct', () => {
  for (const failure of [{ error: 'engine failed' }, { errors: ['bad frame'] }, { isError: true },
    { return_code: 1 }, { exit_code: 1 }, { timed_out: true }]) {
    assert.equal(status({ status: 'success', _apexa_outcome: { execution: 'completed' }, ...failure }), 'error')
  }
  assert.equal(status({ execution_status: 'running', status: 'success' }), 'running')
  assert.equal(status({ execution_status: 'completed', scientific_status: 'provisional' }), 'provisional')
  assert.equal(status({ execution_status: 'completed', scientific_status: 'failed' }), 'rejected')
  assert.equal(status({ _apexa_outcome: { execution: 'completed', scientific_verdict: 'accepted' }, scientific_verdict: 'rejected' }), 'rejected')
  assert.equal(status({ _apexa_outcome: { execution: 'completed', scientific_verdict: 'rejected' }, gate_passed: true }), 'rejected')
  assert.equal(status({ success: true, outcome: { execution: 'completed' }, result: { status: 'failed' } }), 'error')
  assert.equal(status({ status: 'success', gate_passed: false }), 'rejected')
  assert.equal(status({ status: 'success', errors: [] }), 'success')
  assert.equal(status({ status: 'queued' }), 'pending')
  assert.equal(status({ status: 'resource_unavailable', nothing_was_run: true, success: false,
    reason: 'Insufficient available RAM', compute: { n_workers: 0 } }), 'denied')
})

test('error prose and calibration numbers alone are not successful calculations', () => {
  assert.equal(parseDirectToolResult('fixture', 'Error: missing executable').status, 'error')
  assert.equal(parseDirectToolResult('fixture', '⛔ This command was not approved').status, 'denied')
  assert.equal(parseToolResults('The nominal detector distance: 900000 micrometers.')[0].status, 'unknown')
})

test('calibration canvas retains provisional and rejected verdicts after parameter extraction', () => {
  for (const verdict of ['provisional', 'rejected']) {
    const result = parseDirectToolResult('midas_auto_calibrate', JSON.stringify({ status: 'success',
      scientific_verdict: verdict, calibrated_parameters: { Lsd: 900000 } }))
    const [artifact] = extractArtifacts([result], 'message')
    assert.equal(artifact.data.Lsd, 900000)
    assert.equal(inferStatus(artifact.data), verdict)
  }
})
