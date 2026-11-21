export function readPreference(key) {
  try { return localStorage.getItem(key) } catch { return null }
}

export function savePreference(key, value) {
  try { localStorage.setItem(key, value) } catch { /* Workspace state remains available for this visit. */ }
}

export function readEditorPreferences(key) {
  try {
    const data = JSON.parse(readPreference(key))
    return {
      selected: typeof data?.selected === 'string' ? data.selected : '',
      tabs: Array.isArray(data?.tabs) ? data.tabs.filter(item => typeof item === 'string') : [],
      expanded: Array.isArray(data?.expanded) ? data.expanded.filter(item => typeof item === 'string') : [],
    }
  } catch {
    return { selected: '', tabs: [], expanded: [] }
  }
}

