import { useEffect, Component, type ReactNode, type ErrorInfo } from 'react'
import { AppShell } from '@/components/layout/AppShell'
import { useConnectionStore } from '@/stores/connectionStore'
import { useChatStore } from '@/stores/chatStore'

class ErrorBoundary extends Component<{ children: ReactNode }, { error: Error | null }> {
  state = { error: null as Error | null }
  static getDerivedStateFromError(error: Error) { return { error } }
  componentDidCatch(error: Error, info: ErrorInfo) { console.error('App crash:', error, info) }
  render() {
    if (this.state.error) {
      return (
        <div style={{ padding: 40, fontFamily: 'system-ui', color: '#fafafa', background: '#18181b', height: '100vh' }}>
          <h1 style={{ color: '#ef4444', marginBottom: 16 }}>APEXA Error</h1>
          <p style={{ marginBottom: 8 }}>Something went wrong. Check browser console (F12) for details.</p>
          <pre style={{ padding: 16, background: '#27272a', borderRadius: 8, overflow: 'auto', fontSize: 13 }}>
            {this.state.error.message}
          </pre>
          <button onClick={() => window.location.reload()}
            style={{ marginTop: 16, padding: '8px 20px', background: '#3b82f6', color: '#fff', border: 'none', borderRadius: 8, cursor: 'pointer', fontSize: 14 }}>
            Reload
          </button>
        </div>
      )
    }
    return this.props.children
  }
}

export default function App() {
  const initConnection = useConnectionStore((s) => s.init)
  const initChat = useChatStore((s) => s.init)

  useEffect(() => {
    const disposeChat = initChat()
    const disposeConnection = initConnection()
    return () => { disposeChat(); disposeConnection() }
  }, [initConnection, initChat])

  return (
    <ErrorBoundary>
      <AppShell />
    </ErrorBoundary>
  )
}
