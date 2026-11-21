import { useMemo, useState } from 'react'
import { ChevronRight, FileCode, FileText, Folder, FolderOpen, Braces, FileImage } from 'lucide-react'

function fileIcon(path) {
  if (/\.(json|toml|ya?ml)$/i.test(path)) return Braces
  if (/\.(md|txt)$/i.test(path)) return FileText
  if (/\.(png|jpe?g|gif|svg|webp)$/i.test(path)) return FileImage
  return FileCode
}

export default function Tree({ entries, selected, onSelect, query = '', expanded, onExpand }) {
  const [internal, setInternal] = useState([])
  const open = expanded ?? internal
  const setOpen = onExpand ?? setInternal
  const search = query.trim().toLowerCase()
  const index = useMemo(() => {
    const matched = new Set()
    if (search) {
      for (const entry of entries) {
        if (!entry.path.toLowerCase().includes(search)) continue
        let name = entry.path
        while (name && !matched.has(name)) {
          matched.add(name)
          name = name.includes('/') ? name.slice(0, name.lastIndexOf('/')) : ''
        }
      }
    }
    const groups = new Map()
    for (const entry of entries) {
      if (search && !matched.has(entry.path)) continue
      const parent = entry.path.slice(0, Math.max(0, entry.path.lastIndexOf('/')))
      if (!groups.has(parent)) groups.set(parent, [])
      groups.get(parent).push(entry)
    }
    for (const list of groups.values()) {
      list.sort((a, b) => a.type.localeCompare(b.type) || a.name.localeCompare(b.name))
    }
    return groups
  }, [entries, search])
  const openSet = new Set(open)
  function branch(parent) {
    return index.get(parent)?.map(entry => {
      if (entry.type === 'directory') {
        const visible = Boolean(search) || openSet.has(entry.path)
        const Icon = visible ? FolderOpen : Folder
        return <div className="tree-folder" key={entry.path}>
          <button type="button" aria-expanded={visible} title={entry.path}
            onClick={() => setOpen(visible ? open.filter(path => path !== entry.path) : [...open, entry.path])}>
            <ChevronRight size={13} className={visible ? 'expanded' : ''} /><Icon size={15} />{entry.name}
          </button>
          {visible && <div className="tree-branch">{branch(entry.path)}</div>}
        </div>
      }
      const Icon = fileIcon(entry.path)
      return <button type="button" aria-pressed={selected === entry.path} key={entry.path}
        onClick={() => onSelect(entry.path)} title={entry.path}><Icon size={15} />{entry.name}</button>
    })
  }
  return <div className="tree">{index.get('')?.length ? branch('') : <p className="muted">{search ? 'No matching files.' : 'No files are available.'}</p>}</div>
}

