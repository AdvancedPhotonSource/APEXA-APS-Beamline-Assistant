import { test, beforeEach } from 'node:test'
import assert from 'node:assert/strict'
import { useVizStore } from '../src/stores/vizStore.ts'
import { extractArtifacts, parseDirectToolResult } from '../src/lib/parseToolResult.ts'
import { canvasContext } from '../src/lib/canvasContext.ts'
import { isHtmlReport, reportApiUrl, reportFileArtifact } from '../src/lib/reportArtifacts.ts'
import { fileViewId, vizResultId } from '../src/lib/canvasIdentity.ts'
import { restoredWorkspace } from '../src/lib/sessionWorkspace.ts'

beforeEach(() => useVizStore.getState().clear())
const artifact = (id, type = 'plotly') => ({ id, type, title: id, data: {}, sourceMessageId: 'turn' })

test('artifact updates preserve tabs, pins, and comparison', () => {
  const store = useVizStore.getState()
  store.addArtifacts([artifact('a'), artifact('b')])
  store.togglePin('a'); store.toggleCompare('a'); store.toggleCompare('b')
  store.updateArtifact('a', { data: { preview: 'updated' } })
  const state = useVizStore.getState()
  assert.deepEqual(state.pinned, ['a'])
  assert.deepEqual(state.compareIds, ['a', 'b'])
  assert.equal(state.artifacts[0].data.preview, 'updated')
})

test('duplicate delivery does not duplicate artifacts or tabs', () => {
  useVizStore.getState().addArtifacts([artifact('a'), artifact('a')])
  useVizStore.getState().addArtifacts([artifact('a')])
  assert.equal(useVizStore.getState().artifacts.length, 1)
  assert.deepEqual(useVizStore.getState().openIds, ['a'])
})

test('a trailing parameter table does not hide the generated plot', () => {
  useVizStore.getState().addArtifacts([artifact('plot'), artifact('params', 'table')])
  assert.equal(useVizStore.getState().activeId, 'plot')
  useVizStore.getState().addArtifacts([artifact('log', 'text')])
  assert.equal(useVizStore.getState().activeId, 'plot')
})

test('closing a result preserves history for reopening', () => {
  useVizStore.getState().addArtifact(artifact('a'))
  useVizStore.getState().closeTab('a')
  assert.equal(useVizStore.getState().artifacts.length, 1)
  useVizStore.getState().reopen('a')
  assert.equal(useVizStore.getState().activeId, 'a')
})

test('MP and COD outputs produce structure artifacts with actual CIF paths', () => {
  const mp = extractArtifacts([{ tool: 'midas:fetch_cif_from_mp', status: 'success', data: { files: [{ material_id: 'mp-23', cif_file: '/data/Ni.cif' }] } }], 'm')
  const cod = extractArtifacts([{ tool: 'gsas2:propose_structures', status: 'success', data: { candidates: [{ cod_id: 123, cif: '/data/cod.cif' }] } }], 'm')
  assert.equal(mp[0].type, 'structure')
  assert.equal(mp[0].data.path, '/data/Ni.cif')
  assert.equal(cod[0].data.path, '/data/cod.cif')
  assert.equal(extractArtifacts([{ tool: 'fetch_cif_from_mp', status: 'error', data: { results: [{ cif_file: '/bad.cif' }] } }], 'm').length, 0)
})

test('native science tool opens a typed view and exits an unrelated comparison', () => {
  const store = useVizStore.getState()
  store.addArtifacts([artifact('a'), artifact('b')]); store.toggleCompare('a'); store.toggleCompare('b')
  const result = { tool: 'core:open_science_view', status: 'success', data: {
    canvas: { schema_version: 1, type: 'structure', title: 'Ni.cif', data: { path: '/data/Ni.cif', location: 'local' } },
  } }
  const visuals = extractArtifacts([result], 'message')
  store.addArtifacts(visuals)
  assert.equal(useVizStore.getState().activeId, visuals[0].id)
  assert.deepEqual(useVizStore.getState().compareIds, [])
  assert.equal(visuals[0].type, 'structure')
  assert.equal(extractArtifacts([{ ...result, status: 'error' }], 'message').length, 0)
})

test('job lifecycle states stay distinct and FF reports produce a view', () => {
  for (const status of ['running', 'submitted', 'partial', 'unknown']) {
    assert.equal(parseDirectToolResult('refinement_status', JSON.stringify({ status })).status, status)
  }
  const report = parseDirectToolResult('midas:generate_ff_reconstruction_report', JSON.stringify({ status: 'success', report_html: '/data/report.html' }))
  assert.equal(extractArtifacts([report], 'm')[0].type, 'html-report')
})

test('chat gets the accepted frame identity without preview or draft settings', () => {
  const selected = { ...artifact('frame', 'sequence'), data: { path: '/old/path', preview: 'large image',
    viewerState: { source: { path: '/draft' } }, appliedContext: { source: { path: '/accepted' }, sequence_frame: 3 } } }
  const context = canvasContext(selected)
  assert.equal(context.path, '/accepted')
  assert.equal(context.frame_index, 3)
  assert.equal(context.preview, undefined)
  assert.equal(context.viewerState, undefined)
})

test('report paths use stable identities, explicit locality and encoded API queries', () => {
  const path = '/data/scan #1/report & summary.HTML'
  assert.equal(isHtmlReport(path), true)
  assert.equal(isHtmlReport('/data/report.pdf'), false)
  const local = reportFileArtifact(path)
  const remote = reportFileArtifact(path, '', 'remote')
  assert.notEqual(local.id, remote.id)
  assert.equal(reportFileArtifact(path).id, local.id)
  assert.equal(reportApiUrl(path), `/local/api/science/report?path=${encodeURIComponent(path)}`)
  assert.equal(reportApiUrl(path, 'remote'), `/remote/api/science/report?path=${encodeURIComponent(path)}`)
})

test('report tool, direct open, close and reopen share one canvas entry', () => {
  const path = '/data/report.html'
  const result = { tool: 'core:open_science_view', status: 'success', data: {
    canvas: { schema_version: 1, type: 'html-report', title: 'QC report', data: { path, location: 'local' } },
  } }
  const visuals = extractArtifacts([result], 'message')
  const store = useVizStore.getState()
  store.addArtifact(reportFileArtifact(path))
  store.addArtifacts(visuals)
  assert.equal(useVizStore.getState().artifacts.length, 1)
  store.closeTab(visuals[0].id)
  assert.equal(useVizStore.getState().openIds.length, 0)
  const revision = useVizStore.getState().canvasRevision
  store.setActive(visuals[0].id)
  assert.deepEqual(useVizStore.getState().openIds, [visuals[0].id])
  assert.ok(useVizStore.getState().canvasRevision > revision)
  for (const status of ['failed', 'error', 'running', 'unknown']) {
    assert.equal(extractArtifacts([{ ...result, status }], 'message').length, 0)
  }
})

test('structured reports deduplicate delivery and require a completed tool result', () => {
  const result = { tool: 'generate_run_report', status: 'success', data: {
    report: '/data/report.html', report_spec: { title: 'QC report', verdict: 'look', findings: [] },
  } }
  const first = extractArtifacts([result], 'message')
  assert.equal(first[0].type, 'report')
  assert.equal(first[0].id, extractArtifacts([result], 'message')[0].id)
  assert.equal(first[0].id, extractArtifacts([result], 'final-message')[0].id)
  assert.deepEqual(first[0].provenance.inputs, ['/data/report.html'])
  assert.equal(extractArtifacts([{ ...result, status: 'failed' }], 'message').length, 0)
  assert.equal(extractArtifacts([{ ...result, data: { report: '/data/report.html' } }], 'message')[0].type, 'html-report')
})

test('repeat delivery preserves a closed tab, current selection and layout revision', () => {
  const store = useVizStore.getState()
  store.addArtifacts([artifact('a'), artifact('b')])
  store.closeTab('b')
  const before = useVizStore.getState()
  store.addArtifacts([artifact('b')])
  const after = useVizStore.getState()
  assert.deepEqual(after.openIds, ['a'])
  assert.equal(after.activeId, 'a')
  assert.equal(after.revealRevision, before.revealRevision)
  assert.equal(after.canvasRevision, before.canvasRevision)
  store.addArtifacts([artifact('b')], { activate: true })
  assert.equal(useVizStore.getState().activeId, 'b')
  assert.deepEqual(useVizStore.getState().openIds, ['a', 'b'])
})

test('comparison survives selecting its second tab; a different explicit open exits it', () => {
  const store = useVizStore.getState()
  store.addArtifacts([artifact('a'), artifact('b'), artifact('c')])
  store.setActive('a'); store.toggleCompare('a')
  store.setActive('b'); store.toggleCompare('b')
  assert.deepEqual(useVizStore.getState().compareIds, ['a', 'b'])
  store.reopen('c')
  assert.equal(useVizStore.getState().activeId, 'c')
  assert.deepEqual(useVizStore.getState().compareIds, [])
})

test('new visuals reveal Active and comparison can reopen closed selections', () => {
  const store = useVizStore.getState()
  store.addArtifacts([artifact('a'), artifact('b')])
  store.closeTab('a')
  store.toggleCompare('a'); store.toggleCompare('b'); store.showComparison()
  assert.deepEqual(useVizStore.getState().compareIds, ['a', 'b'])
  assert.ok(useVizStore.getState().openIds.includes('a'))
  const revision = useVizStore.getState().revealRevision
  store.addArtifacts([artifact('new', 'image')])
  assert.equal(useVizStore.getState().activeId, 'new')
  assert.deepEqual(useVizStore.getState().compareIds, [])
  assert.ok(useVizStore.getState().revealRevision > revision)
})

test('closing the active tab selects its right neighbor, then its left neighbor', () => {
  const store = useVizStore.getState()
  store.addArtifacts([artifact('a'), artifact('b'), artifact('c')])
  store.setActive('b'); store.closeTab('b')
  assert.equal(useVizStore.getState().activeId, 'c')
  store.closeTab('c')
  assert.equal(useVizStore.getState().activeId, 'a')
  store.closeTab('a')
  assert.equal(useVizStore.getState().activeId, null)
  assert.equal(useVizStore.getState().artifacts.length, 3)
})

test('CIF entry points reuse the viewer and retain settings; remote paths stay distinct', () => {
  const store = useVizStore.getState()
  const path = '/data/Ni.cif'
  store.addArtifact({ ...artifact('old-data-browser-id', 'structure'), data: { path, viewerState: { axes: true } } })
  const result = { tool: 'core:open_science_view', status: 'completed', data: {
    canvas: { schema_version: 1, type: 'structure', data: { path, location: 'local' } },
  } }
  store.addArtifacts(extractArtifacts([result], 'm'))
  assert.equal(useVizStore.getState().artifacts.length, 1)
  assert.deepEqual(useVizStore.getState().artifacts[0].data.viewerState, { axes: true })
  store.addArtifact({ ...artifact('remote', 'structure'), data: { path, location: 'remote' } })
  assert.equal(useVizStore.getState().artifacts.length, 2)
  assert.notEqual(fileViewId('structure', path), fileViewId('structure', path, 'remote'))
})

test('legacy saved file identities migrate with closed state, favorites and viewer settings', () => {
  const path = '/data/Ni.cif'
  const old = { ...artifact('structure-old', 'structure'), data: { path, viewerState: { axes: true } } }
  const workspace = restoredWorkspace({ messages: [{ id: 'm', artifacts: [old] }], workspace: {
    artifacts: [old], openIds: [], activeId: null, pinned: [old.id], compareIds: [old.id], recordedArtifactIds: [old.id],
  } })
  assert.equal(workspace.artifacts.length, 1)
  assert.deepEqual(workspace.openIds, [])
  assert.deepEqual(workspace.pinned, [fileViewId('structure', path)])
  assert.deepEqual(workspace.compareIds, workspace.pinned)
  assert.deepEqual(workspace.artifacts[0].data.viewerState, { axes: true })
})

test('plot snapshots distinguish changed data or settings without timestamp duplicates', () => {
  const plot = { data: [{ x: [1, 2], y: [3, 4] }] }
  const id = vizResultId('lineout', '/data/scan.csv', plot, { y: 'I', x: 'q' })
  assert.equal(id, vizResultId('lineout', '/data/scan.csv', structuredClone(plot), { x: 'q', y: 'I' }))
  assert.notEqual(id, vizResultId('lineout', '/data/scan.csv', { data: [{ x: [1, 2], y: [5, 6] }] }, { x: 'q', y: 'I' }))
  assert.notEqual(id, vizResultId('lineout', '/data/scan.csv', plot, { x: 'q', y: 'sigma' }))
})
