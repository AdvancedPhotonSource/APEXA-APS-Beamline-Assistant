/** Deterministic request-size comparison; no server, model or numerical engine. */
import { writeServerWorkspace, resetWorkspaceTransport } from '../src/api/sessions.ts'

resetWorkspaceTransport()
const calls = []
const originalFetch = globalThis.fetch
globalThis.fetch = async (_url, options) => {
  const payload = JSON.parse(options.body)
  calls.push({ bytes: Buffer.byteLength(options.body), newArtifacts: payload.artifact_slots.filter(slot => 'value' in slot).length })
  return { ok: true, json: async () => ({ workspaceRevision: payload.expected_revision + 1, revision: payload.expected_revision + 1 }) }
}
try {
  const plot = { id: 'synthetic-250k', type: 'plotly', title: 'Synthetic exact points', sourceMessageId: '',
    data: { data: [{ x: Array.from({ length: 250000 }, (_, i) => i), y: Array.from({ length: 250000 }, (_, i) => i / 4) }] } }
  const workspace = { artifacts: [plot], openIds: [plot.id], activeId: plot.id, pinned: [], compareIds: [], draft: '' }
  await writeServerWorkspace('synthetic', workspace, 0)
  const edited = { ...workspace, draft: 'Inspect the next scan' }
  await writeServerWorkspace('synthetic', edited, 1)
  console.log(JSON.stringify({ fixture: 'One synthetic Plotly trace with 250000 exact x/y points',
    method: 'Actual frontend request bodies captured by an in-memory HTTP stub; no browser/network/engine latency claim',
    baselineFullSnapshotBytes: Buffer.byteLength(JSON.stringify({ workspace: edited, expected_revision: 1 })),
    initialPatchBytes: calls[0].bytes, draftOnlyPatchBytes: calls[1].bytes,
    scientificPayloadsInDraftOnlySave: calls[1].newArtifacts,
  }, null, 2))
} finally { globalThis.fetch = originalFetch; resetWorkspaceTransport() }
