import { lazy, Suspense, useRef, useState } from 'react'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import {
  ArrowDownToLine, ArrowRight, BookOpen, Bug, ChartNoAxesColumnIncreasing, CheckCircle2,
  FileCode, FolderGit2, MessageSquare, ShieldCheck, Sparkles,
} from 'lucide-react'
import { api, base, sourceQuery, useAction, useData, workspacePath } from './api.js'
import { Empty, Feedback, Page } from './ui.jsx'

const Editor = lazy(() => import('./monaco.js'))
const editorOptions = { readOnly: true, minimap: { enabled: false }, scrollBeyondLastLine: false, fontSize: 13 }
const origins = { github: 'GitHub', zip: 'ZIP archive', folder: 'Local folder' }
const tools = [
  ['architecture', 'Repository Architecture', 'Inspect source structure and dependencies.', FolderGit2, 'blue'],
  ['review', 'Code Review', 'Review source or a patch with Groq.', FileCode, 'violet'],
  ['github', 'GitHub Pull Requests', 'Publish an accepted change set.', FolderGit2, 'green'],
  ['bugs', 'Find Bugs', 'Find syntax and static issues.', Bug, 'blue', true],
  ['improve', 'Improve Code', 'Propose a focused change for review.', Sparkles, 'violet'],
  ['explain', 'Explain Code', 'Understand a file, function, or selection.', BookOpen, 'cyan'],
  ['security', 'Security Scan', 'Run supported deterministic checks.', ShieldCheck, 'green', true],
  ['assistant', 'Ask AI About Repository', 'Ask questions backed by source references.', MessageSquare, 'violet'],
  ['quality', 'Code Quality Analysis', 'Review lint and maintainability findings.', ChartNoAxesColumnIncreasing, 'blue', true],
]

function sizeLabel(bytes) {
  if (bytes < 1024) return bytes + ' B'
  if (bytes < 1024 ** 2) return (bytes / 1024).toFixed(1) + ' KB'
  return (bytes / 1024 ** 2).toFixed(1) + ' MB'
}

export function Workspace() {
  const { repoId } = useParams()
  const repo = useData('/repos/' + repoId)
  const navigate = useNavigate()
  const action = useAction()
  const [running, setRunning] = useState('')
  const data = repo.data
  function scan(view) {
    setRunning(view)
    action.run(async () => {
      await api.post('/repos/' + repoId + '/analyze')
      navigate(workspacePath(repoId, view))
    })
  }
  return <Page title={data?.name || 'Repository workspace'} subtitle={data && 'Manage the imported working copy and review proposed changes.'}
    action={data && <div className="actions">
      <Link className="button" to={workspacePath(repoId, 'files')}><FileCode size={17} /> File explorer</Link>
      <a className="button" href={base + '/api/repos/' + repoId + '/report'}><ArrowDownToLine size={17} /> Download report</a>
      <a className="button" href={base + '/api/repos/' + repoId + '/export'}><ArrowDownToLine size={17} /> Export ZIP</a>
    </div>}>
    <Feedback error={repo.error || action.error} busy={repo.loading || action.busy}
      message={action.busy ? 'Running static analysis on the working copy…' : 'Loading repository…'} />
    {data && <>
      <section className="panel repository-summary" aria-label="Repository details">
        <div><span className="tool-icon blue"><FolderGit2 size={22} /></span><div><small>Source</small>
          {data.source_url ? <a href={data.source_url} target="_blank" rel="noreferrer"><strong>{origins[data.origin]}</strong> <ArrowRight size={13} /></a> : <strong>{origins[data.origin] || data.origin}</strong>}</div></div>
        <div><div><small>Files</small><strong>{data.file_count.toLocaleString()}</strong></div></div>
        <div><div><small>Imported size</small><strong>{sizeLabel(data.size_bytes)}</strong></div></div>
        <div><div><small>Import status</small><span className="status-ok"><CheckCircle2 size={15} /> Imported</span></div></div>
      </section>
      <div className="section-heading"><h2>What would you like the AI agent to do?</h2></div>
      <div className="tool-grid">{tools.map(([view, title, description, Icon, tone, runsScan]) => {
        const content = <><span className={'tool-icon ' + tone}><Icon size={22} /></span>
          <strong>{running === view && action.busy ? 'Analyzing…' : title}</strong><span>{description}</span><ArrowRight size={18} className="tool-arrow" /></>
        return runsScan
          ? <button type="button" className="tool-card" disabled={action.busy} key={view} onClick={() => scan(view)}>{content}</button>
          : <Link className="tool-card" key={view} to={workspacePath(repoId, view)}>{content}</Link>
      })}</div>
      <Link className="text-link" to={workspacePath(repoId, 'patches')}>View patch history <ArrowRight size={15} /></Link>
    </>}
  </Page>
}

export function RepositoryPicker() {
  const { view } = useParams()
  const repos = useData('/repos')
  const destination = tools.some(tool => tool[0] === view) ? view : ''
  return <Page title="Choose a repository" subtitle="Open an imported working copy to continue."
    action={<Link className="button primary" to="/import">Import repository</Link>}>
    <Feedback error={repos.error} busy={repos.loading} />
    {repos.data && <section className="panel">
      {repos.data.length === 0 && <Empty title="No imported repositories"><Link className="button" to="/import">Import a project</Link></Empty>}
      {repos.data.map(repo => <Link className="repo-row" key={repo.id} to={workspacePath(repo.id, destination)}>
        <FolderGit2 size={20} /><div><strong>{repo.name}</strong><small>{origins[repo.origin] || repo.origin} · {sizeLabel(repo.size_bytes)}</small></div><ArrowRight size={16} />
      </Link>)}
    </section>}
  </Page>
}

export function Citations({ repoId, items }) {
  return <ul className="citations" aria-label="Source references">{items.map(item => (
    <li key={item.path + ':' + item.start + '-' + item.end}>
      <Link to={workspacePath(repoId, 'files') + sourceQuery(item.path, item.start, item.end)}>
        <FileCode size={14} /> {item.path}:{item.start}{item.end !== item.start && '–' + item.end}
      </Link>
    </li>
  ))}</ul>
}

const goals = [
  ['readability', 'Improve readability'],
  ['complexity', 'Refactor complexity'],
  ['maintainability', 'Improve maintainability'],
  ['performance', 'Optimize performance'],
  ['error-handling', 'Improve error handling'],
]

export function SourceTool({ tabs }) {
  const { repoId } = useParams()
  const [params] = useSearchParams()
  const navigate = useNavigate()
  const prefix = '/repos/' + repoId
  const tree = useData(prefix + '/files')
  const [path, setPath] = useState(params.get('path') || '')
  const [revision, setRevision] = useState(0)
  const source = useData(path ? prefix + '/source-context?path=' + encodeURIComponent(path) : null, revision)
  const action = useAction()
  const [range, setRange] = useState([params.get('start') || '', params.get('end') || ''])
  const editorRef = useRef(null)
  const [goal, setGoal] = useState(goals[0][0])
  const files = tree.data?.filter(entry => entry.type === 'file' && entry.editable)
  function submit(event) {
    event.preventDefault()
    action.run(async () => {
      const body = { path, start: range[0] ? Number(range[0]) : null, end: range[1] ? Number(range[1]) : null }
      const { data } = await api.post(prefix + '/improvements', { ...body, goal, source_hash: source.data.source_hash })
      navigate('/patches/' + data.id)
    })
  }
  return <Page title="AI code improvements" subtitle="Select supported source and propose a change for explicit review.">
    {tabs}
    <Feedback error={tree.error || source.error || action.error} busy={tree.loading || source.loading || action.busy}
      message={action.busy ? 'Waiting for the AI provider…' : 'Loading source…'} />
    <div className="source-layout">
      <section className="panel code-panel">
        <div className="panel-title"><h2>{path || 'Source preview'}</h2></div>
        {source.data ? <Suspense fallback={<p className="panel-body muted">Loading editor…</p>}>
          <Editor key={path + ':' + revision} height="470px" theme="vs" language={source.data.language} value={source.data.content}
            options={editorOptions} onMount={editor => {
              editorRef.current = editor
              editor.onDidChangeCursorSelection(({ selection }) => {
                if (!selection.isEmpty()) {
                  const end = selection.endColumn === 1 && selection.endLineNumber > selection.startLineNumber
                    ? selection.endLineNumber - 1 : selection.endLineNumber
                  setRange([String(selection.startLineNumber), String(end)])
                }
              })
            }} />
        </Suspense> : <Empty title="Choose a source file"><p>Highlight code in the editor to select its lines.</p></Empty>}
      </section>
      <div>
        <form className="panel panel-body source-form" onSubmit={submit}>
          <h2>Improvement scope</h2>
          <label>Source file<select value={path} disabled={action.busy} onChange={event => {
            setPath(event.target.value)
            setRange(['', ''])
          }} required>
            <option value="">Choose a file</option>
            {files?.map(file => <option key={file.path} value={file.path}>{file.path}</option>)}
          </select></label>
          {source.data?.symbols.length > 0 && <label>Function<select value={range.join(':')} disabled={action.busy}
            onChange={event => {
              const next = event.target.value.split(':')
              setRange(next)
              const editor = editorRef.current
              const model = editor?.getModel()
              if (model && next.every(Boolean)) {
                const [start, end] = next.map(Number)
                editor.setSelection({ startLineNumber: start, startColumn: 1, endLineNumber: end,
                  endColumn: model.getLineMaxColumn(end) })
                editor.revealLineInCenter(start)
              }
            }}>
            <option value=":">Whole file</option>
            {!source.data.symbols.some(symbol => String(symbol.start) === range[0] && String(symbol.end) === range[1]) &&
              range.some(Boolean) && <option value={range.join(':')}>Custom selection</option>}
            {source.data.symbols.map(symbol => <option key={symbol.start} value={symbol.start + ':' + symbol.end}>{symbol.name} · lines {symbol.start}–{symbol.end}</option>)}
          </select></label>}
          <div className="range-fields">{['Start line', 'End line'].map((label, index) => <label key={label}>{label}
            <input type="number" value={range[index]} min="1" placeholder="Optional" disabled={action.busy}
              onChange={event => setRange(current => current.map((line, i) => i === index ? event.target.value : line))} />
          </label>)}</div>
          <label>Improvement goal<select value={goal} onChange={event => setGoal(event.target.value)}>
            {goals.map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select></label>
          <div className="actions"><button className="button primary" disabled={!source.data || action.busy}>
            <Sparkles size={17} /> Generate proposal</button>
            {path && <button className="button" type="button" disabled={action.busy} onClick={() => {
              setRevision(value => value + 1)
              }}>Reload source</button>}</div>
        </form>
      </div>
    </div>
  </Page>
}

