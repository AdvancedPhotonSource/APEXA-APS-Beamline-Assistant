import { create } from 'zustand'
import { wsManager } from '@/api/websocket'
import { fetchModels } from '@/api/endpoints'

interface ConnectionState {
  connected: boolean
  selectedModel: string
  availableModels: Record<string, Record<string, string>>

  init: () => () => void
  setModel: (model: string) => void
  setConnected: (connected: boolean) => void
}

export const useConnectionStore = create<ConnectionState>((set) => ({
  connected: false,
  selectedModel: 'gpt55',   // backend default (ARGO_MODEL); replaced by /models on load
  availableModels: {},

  init: () => {
    let alive = true
    const unsubscribe = wsManager.onMessage((data) => {
      if (data.type === 'connection_status') set({ connected: !!data.connected })
      if (data.type === 'model_changed') {
        if (data.message === 'connected') {
          set({ connected: true })
        } else if (data.model) {
          set({ selectedModel: data.model })
        }
      }
      if (data.type === 'error' && data.message === 'disconnected') {
        set({ connected: false })
      }
    })
    wsManager.connect()

    fetchModels()
      .then(({ models, selected }) => {
        if (alive) set({ availableModels: models, selectedModel: selected })
      })
      .catch(() => {})
    return () => { alive = false; unsubscribe(); wsManager.disconnect() }
  },

  setModel: (model: string) => {
    wsManager.send({ type: 'change_model', model })
    set({ selectedModel: model })
  },

  setConnected: (connected: boolean) => set({ connected }),
}))
