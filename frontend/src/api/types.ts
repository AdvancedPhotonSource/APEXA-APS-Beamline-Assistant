export type MessageRole = 'user' | 'assistant' | 'system'

export interface ChatMessage {
  id: string
  role: MessageRole
  content: string
  timestamp: number
  toolResults?: ToolResult[]
  artifacts?: VizArtifact[]
}

export interface ToolResult {
  tool: string
  status: 'success' | 'error' | 'completed' | 'failed' | 'warning'
  data: Record<string, unknown>
  args?: Record<string, unknown>   // tool-call inputs, for the "in:" view
}

export interface ChatSession {
  id: string
  title: string
  messages: ChatMessage[]
  createdAt: number
  updatedAt: number
}

export type ArtifactType = 'plotly' | 'image' | 'table' | 'text' | 'diffraction'

/**
 * Provenance travels with every scientific artifact so a result is never just
 * a number/plot — it carries what produced it. This is the trust + reproducibility
 * layer that distinguishes a facility tool from a chatbot.
 */
export interface Provenance {
  inputs?: string[]                              // input files / data sources
  params?: Record<string, string | number>      // key parameters used
  tool?: string                                  // tool / engine that produced it
  version?: string                               // tool/package version
  timestamp?: number                             // epoch ms
  command?: string                               // exact CLI / notebook cell (reproducibility)
}

export interface VizArtifact {
  id: string
  type: ArtifactType
  title: string
  data: unknown
  sourceMessageId: string
  provenance?: Provenance
  createdAt?: number             // epoch ms, stamped by vizStore on add
}

export interface WsOutgoing {
  type: 'chat' | 'change_model' | 'confirm_response' | 'delete_thread'
  message?: string
  model?: string
  confirm_id?: string
  approved?: boolean
  // Frontend thread id (localStorage session) so the backend keeps per-thread
  // conversation memory instead of one global history. Sent on every chat turn;
  // and on delete_thread to drop that thread's server-side memory bucket.
  thread_id?: string
}

export interface WsIncoming {
  type: 'chat_response' | 'error' | 'analysis_progress' | 'model_changed' | 'tool_start' | 'tool_result' | 'confirm_required'
  message?: string
  step?: string
  progress?: number
  model?: string
  tool?: string
  result?: string
  args?: Record<string, unknown>
  // confirm_required (human-in-the-loop gate for consequential actions)
  confirm_id?: string
  action?: string          // e.g. "move_motor_absolute"
  detail?: string          // human-readable description of what will happen
  danger?: boolean         // true → irreversible/physical action, show prominently
  safety?: string[]        // safety-check state lines to display before approval
  // chat_response, FF-HEDM graph mode (APEXA_WORKFLOW_MODE=graph): set when the
  // assistant turn is a paused workflow gate awaiting a human decision. The reply
  // is just the next chat message (it resumes the checkpointed graph) — no separate
  // response type. Optional/additive; a badge on the message is the intended use.
  awaiting_input?: boolean
  gate?: string | null     // gate name, e.g. "select_calibrant", "ring_overlay"
}

export interface ServerStatus {
  status: string
  mcp_client_connected: boolean
  connected_servers: string[]
  active_connections: number
  available_models: Record<string, Record<string, string>>
}

// Phase 2 types

export interface BrowseEntry {
  name: string
  path: string
  is_dir: boolean
  size: number | null
  modified: number
  ext?: string
  is_diffraction?: boolean
}

export interface BrowseResult {
  path: string
  parent: string | null
  entries: BrowseEntry[]
}

export interface ImageStats {
  shape: number[]
  dtype: string
  min: number
  max: number
  mean: number
  std: number
}

export interface ImageLoadResult {
  success: boolean
  file_id: string
  filename: string
  stats: ImageStats
  preview: string
}

export interface CsvData {
  columns: string[]
  data: Record<string, number[]>
  rows: number
  file: string
}

export interface ViewerAdjustResult {
  success: boolean
  image: string
  colormap: string
  vmin: number
  vmax: number
  gamma: number
}
