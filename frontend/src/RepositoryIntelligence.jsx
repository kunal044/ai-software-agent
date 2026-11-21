import { lazy, Suspense, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { GitBranch, GitPullRequest, FolderGit2, Network, ShieldCheck, Sparkles } from 'lucide-react'
import { api, sourceQuery, useAction, useData, workspacePath } from './api.js'
import { Feedback, Page } from './ui.jsx'

const DiffView = lazy(() => import('./DiffView.jsx'))

export function GitHubConnection() {
  const [revision, setRevision] = useState(0)
  const result = useData('/github/status', revision)
  const action = useAction()
  const connected = result.data?.connected
  return <section className="panel"><div className="panel-title"><h2>GitHub connection</h2><FolderGit2 size={18} /></div>
    <div className="panel-body">
      <Feedback busy={result.loading || action.busy} error={result.error || action.error} />
      <p>{connected ? 'Connected as ' + result.data.username : 'GitHub is disconnected.'}</p>
      {result.data && !result.data.configured && <p className="muted">Set GITHUB_TOKEN in backend/.env and restart FastAPI.</p>}
      <button className="button" disabled={!result.data?.configured || action.busy} onClick={() => action.run(async () => {
        await api.post('/github/' + (connected ? 'disconnect' : 'connect'))
        setRevision(value => value + 1)
      })}>{connected ? 'Disconnect GitHub' : 'Connect GitHub'}</button>
    </div>
  </section>
}

function FileLink({ repoId, path, line, end }) {
  return <Link className="source-link" to={workspacePath(repoId, 'files') + sourceQuery(path, line, end || line)}>
    {path}{line ? ':' + line + (end && end !== line ? '–' + end : '') : ''}
  </Link>
}

function ArchitectureList({ title, children }) {
  return <section className="panel"><div className="panel-title"><h2>{title}</h2></div>
    <div className="panel-body intelligence-list">{children}</div></section>
}

export function Architecture({ tabs }) {
  const { repoId } = useParams()
  const prefix = '/repos/' + repoId
  const [selected, setSelected] = useState('')
  const result = useData(prefix + '/architecture' + (selected ? '?selected=' + encodeURIComponent(selected) : ''))
  const files = useData(prefix + '/files')
  const [summary, setSummary] = useState('')
  const action = useAction()
  const facts = result.data
  return <Page title="Repository architecture" subtitle="Explore detected files, entry points, symbols, and internal links."
    action={<button className="button primary" disabled={action.busy || !facts} onClick={() => action.run(async () => {
      const { data } = await api.post(prefix + '/architecture/summary')
      setSummary(data.summary)
    })}><Sparkles size={17} /> Summarize architecture</button>}>
    {tabs}<Feedback error={result.error || action.error || files.error} busy={result.loading || action.busy} />
    {summary && <ArchitectureList title="AI interpretation"><p className="answer">{summary}</p></ArchitectureList>}
    {facts && <>
      <section className="panel panel-body"><div className="actions"><Network size={19} /><strong>{facts.indexed_files} of {facts.file_count} files indexed</strong></div>
        <p className="muted">{facts.limits}</p></section>
      <div className="evaluation-grid">
        <ArchitectureList title="Detected languages">
          {Object.entries(facts.languages).map(([name, count]) => <p key={name}>{name} · {count} files</p>)}
        </ArchitectureList>
        <ArchitectureList title="Project structure">
          {Object.entries(facts.structure).map(([name, count]) => <p key={name}>{name} · {count} files</p>)}
        </ArchitectureList>
        <ArchitectureList title="Candidate entry points">
          {facts.entry_points.length ? facts.entry_points.map(item => <p key={item.path}><FileLink repoId={repoId} path={item.path} /> · {item.signal}</p>) : <p className="muted">No entry points detected.</p>}
        </ArchitectureList>
        <ArchitectureList title="Important modules">
          {facts.important_modules.map(item => <p key={item.path}><FileLink repoId={repoId} path={item.path} /> · {item.direct_dependents} direct dependents · {item.symbols} symbols</p>)}
        </ArchitectureList>
      </div>
      <ArchitectureList title="Potentially affected files">
        <label>Inspect direct dependents<select value={selected} onChange={event => setSelected(event.target.value)}>
          <option value="">Choose a file</option>
          {files.data?.filter(item => item.type === 'file').map(item => <option key={item.path}>{item.path}</option>)}
        </select></label>
        {selected && !facts.affected_files.length && <p className="muted">No direct dependents detected.</p>}
        {facts.affected_files.map(path => <FileLink key={path} repoId={repoId} path={path} />)}
      </ArchitectureList>
      <div className="evaluation-grid">
        <ArchitectureList title="Internal dependencies">
          {facts.dependencies.length ? facts.dependencies.map(link => <p className="intelligence-edge" key={link.source + ':' + link.target}>
            <FileLink repoId={repoId} path={link.source} /> → <FileLink repoId={repoId} path={link.target} />
          </p>) : <p className="muted">No internal dependencies detected.</p>}
        </ArchitectureList>
        <ArchitectureList title="Functions, classes, and declarations">
          {facts.symbols.length ? facts.symbols.map((item, index) => <p key={item.path + ':' + index}>
            <strong>{item.name}</strong> · {item.kind} · <FileLink repoId={repoId} path={item.path} line={item.line} /> · {item.method}
          </p>) : <p className="muted">No declarations detected.</p>}
        </ArchitectureList>
      </div>
    </>}
  </Page>
}

export function CodeReview({ tabs }) {
  const { repoId } = useParams()
  const prefix = '/repos/' + repoId
  const files = useData(prefix + '/files')
  const patches = useData(prefix + '/patches')
  const [paths, setPaths] = useState([])
  const [patchId, setPatchId] = useState('')
  const [filePath, setFilePath] = useState('')
  const [report, setReport] = useState(null)
  const action = useAction()
  const patch = useData(patchId ? '/patches/' + patchId : null)
  const changes = patch.data?.changes || (patch.data ? [patch.data] : [])
  const active = changes.find(file => file.path === filePath) || changes[0]
  return <Page title="Code Review" subtitle="Review one approved or pending change set, or select up to four source files for a cited defensive review.">
    {tabs}<Feedback error={files.error || patches.error || patch.error || action.error} busy={action.busy} message="Reviewing the selected source…" />
    <div className="overview">
      <section className="panel panel-body intelligence-form"><h2>Review scope</h2>
        <label>Change set<select value={patchId} disabled={action.busy} onChange={event => {
          setPatchId(event.target.value); setFilePath(''); setPaths([]); setReport(null)
        }}><option value="">Review selected source files</option>
          {patches.data?.filter(item => ['accepted', 'proposed'].includes(item.status)).map(item =>
            <option key={item.id} value={item.id}>{item.status} · {item.path}</option>)}
        </select></label>
        {!patchId && <label>Source files<select multiple size={7} value={paths} disabled={action.busy} onChange={event => {
          setPaths(Array.from(event.target.selectedOptions, option => option.value).slice(0, 4)); setReport(null)
        }}>{files.data?.filter(item => item.type === 'file' && item.editable).map(item =>
          <option key={item.path}>{item.path}</option>)}</select></label>}
        <button className="button primary" disabled={action.busy || (!patchId && !paths.length)} onClick={() => action.run(async () => {
          const { data } = await api.post(prefix + '/review', { paths, patch_id: patchId || null })
          setReport(data)
        })}><ShieldCheck size={16} /> Review selected code</button>
        {patchId && <Link className="button" to={'/patches/' + patchId}>Open patch approval</Link>}
      </section>
      {active && <section className="panel code-panel"><div className="panel-title">
        <label>Affected file<select value={active.path} onChange={event => setFilePath(event.target.value)}>
          {changes.map(file => <option key={file.path}>{file.path}</option>)}
        </select></label></div>
        <div className="panel-body"><p>{active.explanation || patch.data.explanation}</p>
          <p className="muted">{active.risks || patch.data.risks || 'No separate risk note recorded.'}</p></div>
        <Suspense fallback={<p className="panel-body">Loading diff…</p>}>
          <DiffView original={active.original} modified={active.proposed} language={active.language || 'python'} />
        </Suspense></section>}
    </div>
    {report && <div className="evaluation-grid">
      <ArchitectureList title="Deterministic findings from the latest working-copy scan">
        {!report.static_scan_id && <p className="muted">No static scan recorded.</p>}
        {report.static_scan_id && !report.static_findings.length && <p className="muted">No recorded findings for this scope.</p>}
        {report.static_findings.map(item => <article key={item.id}><span className={'badge ' + item.severity}>{item.severity}</span>
          <p>{item.rule} · {item.message}</p><FileLink repoId={repoId} path={item.path} line={item.line} /></article>)}
      </ArchitectureList>
      <ArchitectureList title="AI review suggestions">
        <p className="muted">Suggestions need human verification. References identify the source version reviewed.</p>
        {!report.ai_suggestions.length && <p>No evidence-backed suggestions returned.</p>}
        {report.ai_suggestions.map((item, index) => <article key={index}>
          <span className="badge">{item.category}</span>
          {item.version === 'working copy' ? <FileLink repoId={repoId} path={item.path} line={item.start} end={item.end} />
            : <Link className="source-link" to={'/patches/' + patchId}>{item.path}:{item.start}–{item.end} · {item.version}</Link>}
          <p>{item.observation}</p><p><strong>Suggested action</strong> {item.recommendation}</p>
        </article>)}
      </ArchitectureList>
    </div>}
  </Page>
}

export function PullRequests({ tabs }) {
  const { repoId } = useParams()
  const prefix = '/repos/' + repoId
  const repo = useData(prefix)
  const github = useData('/github/status')
  const patches = useData(prefix + '/patches')
  const [revision, setRevision] = useState(0)
  const results = useData(prefix + '/github/pull-requests', revision)
  const [patchId, setPatchId] = useState('')
  const [preview, setPreview] = useState(null)
  const [confirmed, setConfirmed] = useState(false)
  const [title, setTitle] = useState('')
  const action = useAction()
  const canPublish = github.data?.connected && repo.data?.origin === 'github'
  return <Page title="GitHub pull requests" subtitle="Publish one accepted change set on a new feature branch after reviewing its destination.">
    {tabs}<Feedback error={repo.error || github.error || patches.error || results.error || action.error}
      busy={action.busy} message="Waiting for GitHub…" />
    {github.data && !github.data.connected && <p className="scan-summary">Connect GitHub in <Link className="source-link" to="/settings">Settings</Link> to publish.</p>}
    {repo.data && repo.data.origin !== 'github' && <p className="scan-summary">Publication requires a repository imported from GitHub.</p>}
    {canPublish && <section className="panel panel-body intelligence-form"><h2>Prepare an accepted patch</h2>
      <label>Accepted change set<select value={patchId} disabled={action.busy} onChange={event => {
        setPatchId(event.target.value); setPreview(null); setConfirmed(false)
      }}><option value="">Choose an accepted change set</option>
        {patches.data?.filter(item => item.status === 'accepted').map(item =>
          <option key={item.id} value={item.id}>{item.path} · {item.id.slice(0, 8)}</option>)}
      </select></label>
      <button className="button" disabled={!patchId || action.busy} onClick={() => action.run(async () => {
        const { data } = await api.get(prefix + '/github/preview', { params: { patch_id: patchId } })
        setPreview(data); setConfirmed(false)
      })}><GitBranch size={16} /> Preview publication</button>
      {preview && <>
        <p><strong>{preview.repository}</strong> · {preview.branch} → {preview.base_branch}</p>
        <ul className="intelligence-paths">{preview.files.map(file => <li key={file.path}>{file.operation} · {file.path}</li>)}</ul>
        <label>Pull request title<input maxLength={120} value={title} onChange={event => setTitle(event.target.value)} /></label>
        <label className="confirm-checkbox"><input type="checkbox" checked={confirmed} onChange={event => setConfirmed(event.target.checked)} />
          Create this feature branch, commit, and pull request on GitHub</label>
        <button className="button primary" disabled={!confirmed || !title.trim() || action.busy} onClick={() => action.run(async () => {
          await api.post(prefix + '/github/pull-requests', { patch_id: patchId, confirmation: preview.confirmation, confirmed, title })
          setPreview(null); setConfirmed(false); setRevision(n => n + 1)
        })}><GitPullRequest size={16} /> Publish pull request</button>
      </>}
    </section>}
    <ArchitectureList title="Publication history">
      {!results.data?.length && !results.loading && <p className="muted">No GitHub publications recorded.</p>}
      {results.data?.map(item => <div className="actions" key={item.id}>
        {item.url ? <a className="source-link" href={item.url} target="_blank" rel="noreferrer">PR #{item.number}</a> : <strong>{item.branch}</strong>}
        <span className="badge">{item.state}</span>
        {item.number && github.data?.connected && <button className="button" disabled={action.busy} onClick={() => action.run(async () => {
          await api.get(prefix + '/github/pull-requests/' + item.patch_id); setRevision(n => n + 1)
        })}>Refresh status</button>}
      </div>)}
    </ArchitectureList>
  </Page>
}

