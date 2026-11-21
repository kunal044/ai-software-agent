import { useRef, useState } from 'react'
import { Search } from 'lucide-react'
import { Feedback } from './ui.jsx'
import Tree from './RepoTree.jsx'

export function WorkspacePanels({ children, maximized = false, initial = [25, 65] }) {
  const [columns, setColumns] = useState(initial)
  const layout = useRef(null)
  const [left, center, right] = children

  function move(index, position) {
    setColumns(previous => {
      const min = index === 0 ? 13 : previous[0] + 24
      const max = index === 0 ? previous[1] - 24 : 80
      return previous.map((value, i) => i === index ? Math.max(min, Math.min(max, position)) : value)
    })
  }

  function divider(index, label) {
    return <div className="ide-divider" tabIndex={0} role="separator" aria-label={label}
      aria-orientation="vertical" aria-valuenow={Math.round(columns[index])}
      onKeyDown={event => {
        if (!['ArrowLeft', 'ArrowRight'].includes(event.key)) return
        event.preventDefault()
        move(index, columns[index] + (event.key === 'ArrowLeft' ? -2 : 2))
      }}
      onPointerDown={event => event.currentTarget.setPointerCapture(event.pointerId)}
      onPointerMove={event => {
        if (!event.currentTarget.hasPointerCapture(event.pointerId)) return
        const bounds = layout.current.getBoundingClientRect()
        move(index, (event.clientX - bounds.left) * 100 / bounds.width)
      }} />
  }

  return <div ref={layout} className={'ide-grid' + (maximized ? ' editor-maximized' : '')}
    style={{ '--left': columns[0] + '%', '--right': (100 - columns[1]) + '%' }}>
    {left}{divider(0, 'Resize first panel')}{center}{divider(1, 'Resize last panel')}{right}
  </div>
}

export function WorkspaceFiles({ files, selected, expanded, onExpand, onSelect }) {
  const [query, setQuery] = useState('')
  const entries = files.data
  return <section className="ide-panel ide-explorer" aria-label="Project explorer">
    <header className="ide-panel-title"><h2>Project files</h2>{entries && <span>{entries.filter(entry => entry.type === 'file').length}</span>}</header>
    <label className="ide-search"><Search size={15} /><input aria-label="Search project files" placeholder="Search name or path"
      value={query} onChange={event => setQuery(event.target.value)} /></label>
    <div className="ide-scroll">
      <Feedback busy={files.loading} error={files.error} message="Loading project files…" />
      {entries && <Tree entries={entries} query={query} selected={selected} expanded={expanded}
        onExpand={onExpand} onSelect={onSelect} />}
    </div>
    <details className="ide-exclusions"><summary>Import and preview limits</summary>
      <p>Git metadata, dependency caches, and protected paths are excluded. Binary, oversized, and secret-containing files may be unavailable in the editor.</p></details>
  </section>
}

