// Synthetic API contract fixture: no engine or hardware calls.
export function workflowFixture(overrides = {}) {
  return {
    workflow_id: 'workflow-fixture', session_id: 'session-a', revision: 4,
    orchestration_state: 'awaiting_review', current_step: 'calibrate', durability: 'sqlite',
    plan: { schema_version: 1, recipe: 'calibration_integration', recipe_version: '1', dataset_id: 'synthetic-detector',
      plan_hash: 'fixture-plan', recipe_source_hash: 'fixture-recipe', tool_schema_hash: 'fixture-schema', steps: [
        { id: 'calibrate', tool: 'midas_auto_calibrate', arguments: { data_file: '/synthetic/image.tif' }, bindings: {}, review_after: true },
        { id: 'integrate', tool: 'midas_integrate_series', arguments: { images: ['/synthetic/image.tif'] },
          bindings: { parameter_file: { step: 'calibrate', path: ['refined_param_file'] } }, review_after: true },
      ] },
    gate: { gate_id: 'gate-plan', kind: 'plan_review', step_id: null, decisions: ['approve', 'reject'], recovery: false,
      prompt: 'Review the immutable analysis plan before executing its first stage.' },
    stages: [
      { id: 'calibrate', tool: 'midas_auto_calibrate', state: 'pending', run_id: null, outcome: null, cancellation_status: null },
      { id: 'integrate', tool: 'midas_integrate_series', state: 'pending', run_id: null, outcome: null, cancellation_status: null },
    ], job_execution: 'unknown', scientific_verdict: 'unassessed', error: null,
    ...overrides,
  }
}
