import { create } from 'zustand'

export type ThemeMode = 'dark' | 'light' | 'midnight'

interface ThemeState {
  mode: ThemeMode
  setMode: (mode: ThemeMode) => void
}

function initialTheme(): ThemeMode {
  try {
    const saved = localStorage.getItem('apexa-theme')
    if (saved === 'dark' || saved === 'light' || saved === 'midnight') return saved
  } catch { /* A blocked browser store should not prevent rendering. */ }
  return 'light'
}

export const useThemeStore = create<ThemeState>((set) => ({
  mode: initialTheme(),
  setMode: (mode) => {
    try { localStorage.setItem('apexa-theme', mode) } catch { /* Keep the current window usable. */ }
    set({ mode })
  },
}))
