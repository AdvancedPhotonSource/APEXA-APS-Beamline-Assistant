export type WorkspaceView = 'chat' | 'split' | 'canvas'
export const WORKSPACE_LAYOUT_KEY = 'apexa.workspace.layout.v1'
export const MIN_CHAT_WIDTH = 360
export const MIN_CANVAS_WIDTH = 380
export const DIVIDER_WIDTH = 6

export function parseWorkspaceView(value: unknown): WorkspaceView | null {
  return value === 'chat' || value === 'split' || value === 'canvas' ? value : null
}

export function workspaceLayout(choice: WorkspaceView | null, width: number) {
  const canSplit = width >= MIN_CHAT_WIDTH + MIN_CANVAS_WIDTH + DIVIDER_WIDTH
  const preferred = choice ?? 'split'
  return { canSplit, view: preferred === 'split' && !canSplit ? 'chat' as const : preferred }
}
