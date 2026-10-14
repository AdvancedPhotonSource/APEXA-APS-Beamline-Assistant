import type { WsIncoming, WsOutgoing } from './types'

type MessageHandler = (data: WsIncoming) => void

class WebSocketManager {
  private ws: WebSocket | null = null
  private reconnectAttempts = 0
  private maxReconnectAttempts = 10
  private reconnectDelay = 2000
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null
  private stableTimer: ReturnType<typeof setTimeout> | null = null
  private stopped = true
  private handlers: Set<MessageHandler> = new Set()
  private url = ''

  connect(url?: string) {
    const nextUrl = url ?? `${window.location.protocol === 'https:' ? 'wss:' : 'ws:'}//${window.location.host}/ws`
    if (this.url && nextUrl !== this.url) this.disconnect()
    this.url = nextUrl
    this.stopped = false
    if (this.reconnectTimer !== null) return
    this.doConnect()
  }

  private doConnect() {
    if (this.stopped || this.ws?.readyState === WebSocket.OPEN || this.ws?.readyState === WebSocket.CONNECTING) return

    const socket = new WebSocket(this.url)
    this.ws = socket

    socket.onopen = () => {
      if (this.ws !== socket || this.stopped) return
      // An accepted socket that immediately closes is still a failed attempt.
      this.stableTimer = setTimeout(() => {
        this.stableTimer = null
        if (this.ws === socket && !this.stopped) this.reconnectAttempts = 0
      }, 10000)
      this.notify({ type: 'connection_status', connected: true })
    }

    socket.onmessage = (event: MessageEvent) => {
      if (this.ws !== socket || this.stopped) return
      try {
        const data: WsIncoming = JSON.parse(event.data)
        this.notify(data)
      } catch {
        console.error('Failed to parse WebSocket message:', event.data)
      }
    }

    socket.onclose = (event: CloseEvent) => {
      if (this.ws !== socket || this.stopped) return
      if (this.stableTimer !== null) clearTimeout(this.stableTimer)
      this.stableTimer = null
      this.ws = null
      console.warn('APEXA WebSocket closed', { code: event.code, reason: event.reason, clean: event.wasClean })
      this.notify({ type: 'connection_status', connected: false, close_code: event.code, close_reason: event.reason })
      this.tryReconnect()
    }

    socket.onerror = () => {
      if (this.ws === socket && !this.stopped) socket.close()
    }
  }

  private tryReconnect() {
    if (this.stopped || this.reconnectTimer !== null || this.reconnectAttempts >= this.maxReconnectAttempts) return
    this.reconnectAttempts++
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null
      this.doConnect()
    }, this.reconnectDelay * this.reconnectAttempts)
  }

  send(message: WsOutgoing) {
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(message))
      return true
    } else {
      console.warn('WebSocket not connected; message was not sent')
      return false
    }
  }

  onMessage(handler: MessageHandler) {
    this.handlers.add(handler)
    return () => { this.handlers.delete(handler) }
  }

  private notify(data: WsIncoming) {
    this.handlers.forEach(h => h(data))
  }

  get connected() {
    return this.ws?.readyState === WebSocket.OPEN
  }

  disconnect() {
    this.stopped = true
    if (this.reconnectTimer !== null) clearTimeout(this.reconnectTimer)
    if (this.stableTimer !== null) clearTimeout(this.stableTimer)
    this.reconnectTimer = null
    this.stableTimer = null
    this.reconnectAttempts = 0
    const socket = this.ws
    this.ws = null
    socket?.close(1000, 'Client disposed')
  }
}

export const wsManager = new WebSocketManager()
