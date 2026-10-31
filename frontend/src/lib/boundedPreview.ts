/** Bounded inspection only: the original scientific payload stays untouched. */
export function boundedPreview(value: unknown): string {
  let remaining = 240
  const seen = new WeakSet<object>()
  const visit = (item: unknown, depth: number): unknown => {
    if (remaining-- <= 0) return '… preview limit'
    if (typeof item === 'string') return item.length > 1500 ? item.slice(0, 1500) + '…' : item
    if (item == null || typeof item !== 'object') return item
    if (seen.has(item)) return '[Repeated reference]'
    if (depth === 5) return '[Nested data]'
    seen.add(item)
    if (Array.isArray(item)) {
      const result = item.slice(0, Math.min(30, Math.max(0, remaining))).map(v => visit(v, depth + 1))
      if (result.length < item.length) result.push(`… ${item.length - result.length} more items`)
      return result
    }
    const result: Record<string, unknown> = {}
    let count = 0
    for (const key in item) {
      if (!Object.hasOwn(item, key)) continue
      if (++count > 30 || remaining <= 0) { result['…'] = 'More fields omitted'; break }
      result[key] = visit((item as Record<string, unknown>)[key], depth + 1)
    }
    return result
  }
  try { return JSON.stringify(visit(value, 0), null, 2) ?? String(value) } catch { return '[Unserializable data]' }
}

export function downloadJson(value: unknown, filename: string) {
  const url = URL.createObjectURL(new Blob([JSON.stringify(value, null, 2)], { type: 'application/json' }))
  const link = document.createElement('a')
  link.href = url
  link.download = filename.replace(/[^\w.-]/g, '_') + '.json'
  link.click()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}
