import type { ToolResult } from '../api/types'

// Stable across live events, CLI import and page reload. This is a UI identity,
// not a cryptographic digest or a scientific integrity check.
export function resultIdentity(result: ToolResult): string {
  const text = JSON.stringify([result.tool, result.data])
  let a = 2166136261, b = 5381
  for (let i = 0; i < text.length; i++) {
    a = Math.imul(a ^ text.charCodeAt(i), 16777619)
    b = Math.imul(b, 33) ^ text.charCodeAt(i)
  }
  return `${(a >>> 0).toString(36)}-${(b >>> 0).toString(36)}`
}
