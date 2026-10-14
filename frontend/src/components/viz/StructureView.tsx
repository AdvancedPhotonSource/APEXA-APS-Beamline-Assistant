import { useEffect, useMemo, useState } from 'react'
import { PlotlyChart } from './PlotlyChart'

interface Structure {
  name: string; space_group: string; path: string; vectors: number[][]
  cell: Record<string, number>
  atoms: { element: string; position: number[]; occupancy: number }[]
}

export function StructureView({ path, source }: { path: string; source?: string }) {
  const [structure, setStructure] = useState<Structure | null>(null)
  const [error, setError] = useState('')
  useEffect(() => {
    const controller = new AbortController()
    fetch(`/local/api/science/structure?path=${encodeURIComponent(path)}`, { signal: controller.signal })
      .then(async r => { const data = await r.json(); if (!r.ok) throw new Error(data.detail); return data })
      .then(setStructure).catch(e => { if (e.name !== 'AbortError') setError(String(e)) })
    return () => controller.abort()
  }, [path])
  const plot = useMemo(() => {
    if (!structure) return null
    const elements = [...new Set(structure.atoms.map(a => a.element))]
    const data: Record<string, unknown>[] = elements.map(element => {
      const atoms = structure.atoms.filter(a => a.element === element)
      return { type: 'scatter3d', mode: 'markers', name: element,
        x: atoms.map(a => a.position[0]), y: atoms.map(a => a.position[1]), z: atoms.map(a => a.position[2]),
        marker: { size: 9, opacity: 0.95, line: { width: 0.5, color: '#fff' } },
        text: atoms.map(a => `${element} · occupancy ${a.occupancy}`),
        hovertemplate: '%{text}<br>x %{x:.3f} Å<br>y %{y:.3f} Å<br>z %{z:.3f} Å<extra></extra>' }
    })
    const xyz: (number | null)[][] = [[], [], []]
    for (let corner = 0; corner < 8; corner++) {
      for (let axis = 0; axis < 3; axis++) {
        if (corner & (1 << axis)) continue
        for (const c of [corner, corner | (1 << axis)]) {
          for (let k = 0; k < 3; k++) xyz[k].push(structure.vectors.reduce((v, vec, j) => v + ((c >> j) & 1) * vec[k], 0))
        }
        xyz.forEach(a => a.push(null))
      }
    }
    data.push({ type: 'scatter3d', mode: 'lines', name: 'Unit cell', x: xyz[0], y: xyz[1], z: xyz[2], line: { color: '#94a3b8', width: 3 }, hoverinfo: 'skip' })
    return { data, layout: { title: { text: structure.name }, scene: { aspectmode: 'data', xaxis: { title: { text: 'x (Å)' } }, yaxis: { title: { text: 'y (Å)' } }, zaxis: { title: { text: 'z (Å)' } } }, margin: { l: 0, r: 0, b: 0, t: 35 } } }
  }, [structure])
  if (error) return <div role="alert" style={{ padding: 20 }}>Could not read this CIF: {error}</div>
  if (!structure || !plot) return <div style={{ padding: 20 }}>Reading CIF and expanding symmetry…</div>
  return (
    <div style={{ height: '100%', minHeight: 400, display: 'flex', flexDirection: 'column', minWidth: 0 }}>
      <div style={{ padding: 12, fontSize: 12, borderBottom: '1px solid var(--apexa-border)' }}>
        <b>{structure.name}</b> · space group {structure.space_group} · {structure.atoms.length} sites
        <div style={{ marginTop: 4 }}>{Object.entries(structure.cell).map(([key, value]) => `${key} ${value.toFixed(3)}${['a', 'b', 'c'].includes(key) ? ' Å' : '°'}`).join(' · ')}</div>
        <div style={{ color: 'var(--apexa-text-muted)', overflowWrap: 'anywhere', marginTop: 4 }}>{source ? `${source} · ` : ''}{path}</div>
        <div style={{ color: 'var(--apexa-text-muted)', marginTop: 4 }}>Drag to rotate · scroll to zoom · sites and occupancy from CIF; bonds are not inferred</div>
      </div>
      <div style={{ flex: 1, minHeight: 300 }}><PlotlyChart data={plot} /></div>
    </div>
  )
}
