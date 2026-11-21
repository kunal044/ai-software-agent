import { lazy, Suspense, useEffect, useState } from 'react'
import { BrowserRouter, Link, NavLink, Route, Routes, useLocation, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { api, base, useAction, useData, workspacePath, sourceQuery } from './api.js'
import Analytics, { VerificationReport } from './Analytics.jsx'
import RepositoryChat from './AgentChat.jsx'
import CodeExplanation from './CodeExplanation.jsx'
import { readPreference, savePreference } from './workspacePreferences.js'
import Tree from './RepoTree.jsx'
import { Architecture, CodeReview, GitHubConnection, PullRequests } from './RepositoryIntelligence.jsx'
import { Empty, Feedback, Page } from './ui.jsx'
import { Workspace, RepositoryPicker, SourceTool, Citations } from './Workspace.jsx'
import {
  ArrowDownToLine, ArrowRight, BarChart3, Check, ChevronRight, Code2,
  FileCode, Folder, FolderGit2, GitPullRequest, LayoutDashboard, Plus, ScanLine,
  Settings as SettingsIcon, ShieldCheck, UploadCloud, X, BookOpen, Bug, MessageSquare, Sparkles, FolderOpen,
} from 'lucide-react'

const Editor = lazy(() => import('./monaco.js'))
const DiffView = lazy(() => import('./DiffView.jsx'))
const editorOptions = { readOnly: true, minimap: { enabled: false }, scrollBeyondLastLine: false, fontSize: 13 }

const mainMetrics = [
  ['repositories', 'Repositories', FolderGit2],
  ['files_analyzed', 'Files analyzed', FileCode],
  ['findings', 'Open findings', ScanLine],
  ['accepted', 'Accepted fixes', ShieldCheck],
]
const outcomeMetrics = [['rejected', 'Fixes rejected', X], ['resolved', 'Findings resolved', Check]]
function Metrics({ data, fields }) {
  return <div className="metrics">{fields.map(([key, label, Icon]) => (
    <article className="metric" key={key}>
      <span className="metric-icon"><Icon size={18} /></span>
      <strong>{data[key] == null ? '—' : data[key].toLocaleString() + (key === 'average_ms' ? ' ms' : '')}</strong>
      <span>{label}</span>
    </article>
  ))}</div>
}

function Severity({ values }) {
  const total = Object.values(values).reduce((a, b) => a + b, 0)
  return <div className="severity">{['high', 'medium', 'low'].map(level => (
    <div key={level}><span className={'dot ' + level} /><span className="capitalize">{level}</span>
      <progress className={level} max={total || 1} value={values[level] || 0} aria-label={level + ' severity'} />
      <strong>{values[level] || 0}</strong></div>
  ))}</div>
}

function Overview() {
  const stats = useData('/dashboard')
  const repos = useData('/repos')
  return <Page title="Dashboard"
    subtitle="Import a project, inspect findings, and review fixes in one workspace."
    action={<Link className="button primary" to="/import"><Plus size={17} /> Import repository</Link>}>
    <section className="dashboard-hero">
      <div><p className="eyebrow">Repository workspace</p><h2>Understand, review, and improve your code.</h2>
        <p>Run static analysis and review AI-proposed changes before they reach your working copy.</p>
        <Link className="button primary" to="/workspace">Open a workspace <ArrowRight size={16} /></Link>
      </div><div className="hero-graphic" aria-hidden="true"><Code2 size={76} /><Sparkles size={29} /></div>
    </section>
    <Feedback error={stats.error || repos.error} busy={stats.loading || repos.loading} />
    {stats.data && <Metrics data={stats.data} fields={[...mainMetrics, ...outcomeMetrics]} />}
    <div className="overview">
      <section className="panel"><div className="panel-title"><h2>Recent repository activity</h2><Link to="/import">Add project <ArrowRight size={15} /></Link></div>
        {repos.data?.length === 0 && <Empty title="No repositories yet"><p>Import from GitHub or upload a project to start reviewing code.</p><Link className="button" to="/import">Import a project</Link></Empty>}
        {repos.data?.slice(0, 8).map(repo => <Link className="repo-row" to={workspacePath(repo.id)} key={repo.id}><FolderGit2 size={20} /><div><strong>{repo.name}</strong><small>{repo.origin} · {repo.file_count} {repo.file_count === 1 ? 'file' : 'files'}</small></div>{repo.imported_at && <small className="activity-date">{new Intl.DateTimeFormat(undefined, { dateStyle: 'medium' }).format(new Date(repo.imported_at))}</small>}<ChevronRight size={17} /></Link>)}
      </section>
      {stats.data && <section className="panel"><div className="panel-title"><h2>Finding severity</h2><ScanLine size={17} /></div><Severity values={stats.data.severity} /></section>}

    </div>
  </Page>
}

function Import() {
  const action = useAction()
  const [files, setFiles] = useState([])
  const [mode, setMode] = useState('github')
  const [url, setUrl] = useState('')
  const [progress, setProgress] = useState(null)
  const navigate = useNavigate()
  const github = useData(mode === 'connected' ? '/github/status' : null)
  const [page, setPage] = useState(1)
  const accessible = useData(mode === 'connected' && github.data?.connected ? '/github/repositories?page=' + page : null)
  const options = [['github', 'GitHub', FolderGit2], ['connected', 'Connected GitHub', FolderGit2], ['zip', 'ZIP archive', UploadCloud], ['folder', 'Folder', Folder]]
  function submit(event) {
    event.preventDefault()
    setProgress(null)
    action.run(async () => {
      let response
      if (mode === 'github') {
        response = await api.post('/repos/import', { url: url.trim() })
      } else if (mode === 'connected') {
        response = await api.post('/github/import', { url: url.trim() })
      } else {
        const form = new FormData()
        if (mode === 'zip') form.append('archive', files[0])
        else {
          files.forEach(file => form.append('files', file))
          form.append('paths', JSON.stringify(files.map(file => file.webkitRelativePath)))
        }
        response = await api.post('/repos/upload', form, {
          onUploadProgress: event => { if (event.total) setProgress(Math.round(100 * event.loaded / event.total)) },
        })
      }
      navigate(workspacePath(response.data.id))
    })
  }
  return <Page title="Import repository" subtitle="Create an isolated working copy for static analysis and patch review.">
    <section className="panel import"><div className="tabs">{options.map(([id, label, Icon]) =>
      <button type="button" key={id} disabled={action.busy} aria-pressed={mode === id} onClick={() => { setMode(id); setFiles([]); setUrl(''); setPage(1) }}><Icon size={17} /> {label}</button>)}</div>
      <form onSubmit={submit} className="panel-body">
        {mode === 'github' ? <label>Public GitHub URL<input type="url" placeholder="https://github.com/owner/repository" value={url} onChange={e => setUrl(e.target.value)} required /></label>
          : mode === 'connected' ? <><label>Accessible GitHub repository<input type="url" list="github-repository-choices" value={url} onChange={e => setUrl(e.target.value)} required /></label>
            <datalist id="github-repository-choices">{accessible.data?.items.map(item => <option key={item.url} value={item.url}>{item.name}</option>)}</datalist>
            {github.data?.connected ? <div className="actions"><button type="button" className="button" disabled={page <= 1} onClick={() => setPage(page - 1)}>Previous</button><button type="button" className="button" disabled={!accessible.data?.next_page} onClick={() => setPage(page + 1)}>Next</button></div> : <Link to="/settings">Connect GitHub in Settings</Link>}
            <Feedback busy={github.loading || accessible.loading} error={github.error || accessible.error} /></>
          : <label>{mode === 'zip' ? 'Project archive' : 'Project folder'}<input key={mode} type="file"
            {...(mode === 'folder' ? { webkitdirectory: '', multiple: true } : { accept: '.zip' })}
            onChange={e => setFiles(Array.from(e.target.files))} required /></label>}
        <button className="button primary" disabled={action.busy || (mode === 'connected' ? !github.data?.connected : mode !== 'github' && !files.length)}><Plus size={17} /> Import project</button>
        {action.busy && progress != null && <progress value={progress} max={100} aria-label="Upload progress" />}
        <Feedback error={action.error} busy={action.busy} message={progress === 100 ? 'Processing uploaded project…' : 'Import in progress…'} />
      </form>
    </section>
  </Page>
}

function RepoTabs({ repoId }) {
  return <nav className="subnav" aria-label="Repository">
    <NavLink end to={workspacePath(repoId)}>Workspace</NavLink>
    <NavLink to={workspacePath(repoId, 'files')}>Files</NavLink>
    <NavLink to={workspacePath(repoId, 'bugs')}>Bug analysis</NavLink>
    <NavLink to={workspacePath(repoId, 'patches')}>Patch history</NavLink>
    <NavLink to={workspacePath(repoId, 'architecture')}>Architecture</NavLink>
    <NavLink to={workspacePath(repoId, 'review')}>Code Review</NavLink>
    <NavLink to={workspacePath(repoId, 'github')}>Pull requests</NavLink>
  </nav>
}

function ScanButton({ repoId, existing, onDone }) {
  const action = useAction()
  return <div><button className="button primary" disabled={action.busy} onClick={() => action.run(async () => {
    await api.post('/repos/' + repoId + '/analyze')
    onDone()
  })}><ScanLine size={17} />{action.busy ? 'Analyzing…' : existing ? 'Reanalyze' : 'Analyze repository'}</button><Feedback error={action.error} /></div>
}

function Explorer() {
  const { repoId } = useParams()
  const [params] = useSearchParams()
  const repo = useData('/repos/' + repoId)
  const tree = useData('/repos/' + repoId + '/files')
  const [selected, setSelected] = useState(params.get('path') || '')
  const source = useData(selected ? '/repos/' + repoId + '/file?path=' + encodeURIComponent(selected) : null)
  const navigate = useNavigate()
  return <Page title={repo.data?.name || 'Repository explorer'} subtitle={repo.data && repo.data.file_count + (repo.data.file_count === 1 ? ' file' : ' files') + ' in your working copy'}
    action={<div className="actions"><ScanButton repoId={repoId} onDone={() => navigate(workspacePath(repoId, 'bugs'))} />
      <a className="button" href={base + '/api/repos/' + repoId + '/export'}><ArrowDownToLine size={17} /> Download project</a></div>}>
    <RepoTabs repoId={repoId} />
    <Feedback error={repo.error || tree.error} busy={repo.loading || tree.loading} />
    <div className="explorer"><section className="panel"><div className="panel-title"><h2>Project files</h2></div>
      {tree.data && <Tree entries={tree.data} selected={selected} onSelect={setSelected} />}</section>
      <section className="panel code-panel"><div className="panel-title"><h2>{selected || 'Source preview'}</h2></div>
        <Feedback error={source.error} busy={source.loading} />
        {!selected && <Empty title="Choose a source file"><p>Browse the project tree to inspect the working copy.</p></Empty>}
        {source.data && <Suspense fallback={<p className="muted">Loading editor…</p>}>
          <Editor height="510px" theme="vs" language={source.data.language} value={source.data.content} options={editorOptions}
            onMount={editor => {
              const line = Number(params.get('start'))
              if (line > 0 && selected === params.get('path')) {
                editor.setSelection({ startLineNumber: line, startColumn: 1, endLineNumber: Number(params.get('end')) || line, endColumn: Number.MAX_SAFE_INTEGER })
                editor.revealLineInCenter(line)
              }
            }} />
        </Suspense>}
      </section></div>
  </Page>
}

function Findings({ items, evidence = false }) {
  return items.length ? <div className="findings">{items.map(issue => <Link className="issue" key={issue.id} to={'/findings/' + issue.id}>
    <span className={'badge ' + issue.severity}>{issue.severity}</span><div><strong>{issue.message}</strong>
      <small>{issue.path}:{issue.line} · {issue.rule} · {issue.category}</small>{evidence && issue.evidence && <code className="evidence">{issue.evidence}</code>}</div><ChevronRight size={17} />
  </Link>)}</div> : <div className="panel-body muted">No findings in this view.</div>
}

function Results({ category }) {
  const { repoId } = useParams()
  const [revision, setRevision] = useState(0)
  const [severity, setSeverity] = useState('')
  const result = useData('/repos/' + repoId + '/analysis', revision)
  const scan = result.data
  const matches = scan?.findings.filter(issue => category === 'security' ? issue.category === 'security'
    : category === 'quality' ? issue.category === 'static' : category !== 'bugs' || issue.category !== 'security')
  return <Page title={category === 'security' ? 'Security analysis' : category === 'quality' ? 'Code quality analysis' : category === 'bugs' ? 'Bug analysis' : 'Analysis results'}
    subtitle={category === 'security' ? 'Review supported deterministic checks on Python source. Coverage is limited to the configured rules.'
      : 'Review syntax and static findings from the latest repository scan.'}
    action={<ScanButton repoId={repoId} existing={Boolean(scan)} onDone={() => setRevision(revision + 1)} />}>
    <RepoTabs repoId={repoId} /><Feedback error={result.error} busy={result.loading} />
    {scan === null && <section className="panel"><Empty title="No scans yet"><p>Analyze this repository to see its first results.</p></Empty></section>}
    {scan && <section className="panel"><div className="panel-title"><h2>{matches.length} findings</h2>
      <select aria-label="Filter by severity" value={severity} onChange={e => setSeverity(e.target.value)}>
        <option value="">All severities</option><option>high</option><option>medium</option><option>low</option>
      </select></div>
      <div className="scan-summary">{scan.files_analyzed} files · {scan.duration_ms} ms · Runtime tests {scan.runtime}
      {scan.validation_unavailable?.length > 0 && <details><summary>Validation unavailable for {scan.validation_unavailable.length} files</summary>{scan.validation_unavailable.join(', ')}</details>}
      {scan.skipped.length > 0 && <details><summary>{scan.skipped.length} files skipped</summary>{scan.skipped.join(', ')}</details>}</div>
      <Findings evidence={category === 'security'} items={matches.filter(issue => !severity || issue.severity === severity)} />
    </section>}
  </Page>
}

function Issue() {
  const { findingId } = useParams()
  const result = useData('/findings/' + findingId)
  const action = useAction()
  const navigate = useNavigate()
  const issue = result.data
  const [explanation, setExplanation] = useState(null)
  const [pendingMessage, setPendingMessage] = useState('')
  return <Page title="Issue details" subtitle={issue && issue.path + ':' + issue.line}>
    <Feedback error={result.error || action.error} busy={result.loading || action.busy} message={action.busy ? pendingMessage : 'Loading...'} />
    {issue && <><RepoTabs repoId={issue.repo_id} /><section className="panel panel-body">
      <span className={'badge ' + issue.severity}>{issue.severity}</span><h2>{issue.message}</h2>
      <p className="muted">{issue.rule} · {issue.category}</p><pre>{issue.evidence}</pre>
      <div className="actions"><button className="button primary" disabled={action.busy} onClick={() => action.run(async () => {
        setPendingMessage('Generating a proposed fix...')
        const response = await api.post('/findings/' + findingId + '/fix')
        navigate('/patches/' + response.data.id)
      })}><GitPullRequest size={17} /> Generate AI fix</button>
      <button className="button" disabled={action.busy} onClick={() => action.run(async () => {
        setPendingMessage('Waiting for an explanation...')
        const response = await api.post('/repos/' + issue.repo_id + '/explain', {
          path: issue.path, start: issue.line, end: issue.line,
          question: 'Explain static rule ' + issue.rule + ' and its likely root cause. ' + issue.message,
        })
        setExplanation(response.data)
      })}><BookOpen size={17} /> Explain issue</button>
      <Link className="button" to={workspacePath(issue.repo_id, 'files') + sourceQuery(issue.path, issue.line, issue.line)}>View affected code</Link>
      <button className="button" disabled={action.busy} onClick={() => action.run(async () => {
        setPendingMessage('Running static analysis...')
        await api.post('/repos/' + issue.repo_id + '/analyze')
        navigate(workspacePath(issue.repo_id, 'bugs'))
      })}>Reanalyze</button></div>
      {explanation && <div className="answer"><p>{explanation.answer}</p><Citations repoId={issue.repo_id} items={explanation.citations} /></div>}
    </section></>}
  </Page>
}

function PatchList() {
  const { repoId } = useParams()
  const result = useData('/repos/' + repoId + '/patches')
  return <Page title="Fix workspace" subtitle="Review proposed fixes and revisit patch decisions.">
    <RepoTabs repoId={repoId} /><Feedback error={result.error} busy={result.loading} />
    <section className="panel">{result.data?.length === 0 && <Empty title="No proposed fixes"><Link className="button" to={workspacePath(repoId, 'bugs')}>View findings</Link></Empty>}
      {result.data?.map(patch => <Link className="repo-row" key={patch.id} to={'/patches/' + patch.id}><GitPullRequest size={17} /><div><strong>{patch.path}</strong><small>{patch.kind === 'agent' ? 'Agent changes' : patch.kind === 'improvement' ? 'Improvement' : 'Bug fix'} · {patch.status}</small></div><ChevronRight size={17} /></Link>)}</section>
  </Page>
}

function Patch({ comparison = false }) {
  const { patchId } = useParams()
  const [revision, setRevision] = useState(0)
  const result = useData('/patches/' + patchId, revision)
  const action = useAction()
  const patch = result.data
  const review = patch?.comparison
  return <Page title={comparison ? 'Patch verification' : 'Patch review'} subtitle={patch?.path}>
    <Feedback error={result.error || action.error} busy={result.loading || action.busy} />
    {patch && <RepoTabs repoId={patch.repo_id} />}
    {patch && !comparison && <>
      <section className="panel panel-body"><div className="actions"><span className="badge">{patch.status}</span><span className="muted">Syntax {patch.syntax}</span></div>
        <h2>{patch.kind === 'agent' ? 'Change summary' : patch.kind === 'improvement' ? 'Proposed improvement' : 'Root cause'}</h2><p>{patch.explanation}</p>
        <h2>{patch.kind === 'improvement' ? 'Expected benefit' : 'Proposed change'}</h2><p>{patch.rationale}</p>
        {patch.risks && <><h2>Potential risks</h2><p>{patch.risks}</p></>}
        {patch.usage?.total_tokens != null && <p className="muted">{patch.usage.total_tokens} tokens used</p>}
        {patch.status === 'proposed' && <div className="actions">{['accept', 'reject'].map(decision =>
          <button key={decision} className={'button capitalize ' + (decision === 'accept' ? 'primary' : '')} disabled={action.busy}
            onClick={() => action.run(async () => {
              try {
                await api.post('/patches/' + patchId + '/' + decision)
              } finally {
                setRevision(revision + 1)
              }
            })}>{decision === 'accept' ? <><Check size={17} /> Accept Fix</> : <><X size={17} /> Decline Fix</>}</button>)}</div>}
        {(review || patch.syntax !== 'NOT RUN') && <Link className="button primary" to={'/patches/' + patchId + '/comparison'}>{review ? 'View before / after' : 'View validation'} <ArrowRight size={17} /></Link>}
      </section>
      {(patch.changes || [patch]).map(item => <section className="panel code-panel" key={item.path}>
        <div className="panel-title"><h2>{item.operation ? item.operation + ' / ' + item.path : 'Original / proposed'}</h2></div>
        {item.operation && <div className="panel-body"><p>{item.explanation}</p><p>{item.expected}</p><p>{item.risks}</p><p>Validation ? {item.syntax || patch.syntax}</p></div>}
        <Suspense fallback={<p className="muted">Loading diff editor...</p>}>
          <DiffView original={item.original} modified={item.proposed} language={item.language || 'python'} />
        </Suspense>
        <details className="panel-body"><summary>Unified diff</summary><pre>{item.diff}</pre></details>
      </section>)}
    </>}
    {comparison && patch && <VerificationReport patchId={patchId} Findings={Findings} Severity={Severity} />}
  </Page>
}

function Settings() {
  const status = useData('/settings')
  return <Page title="Settings" subtitle="Inspect local service configuration.">
    <GitHubConnection />
    <Feedback error={status.error} busy={status.loading} />
    {status.data && <section className="panel"><div className="panel-title"><h2>AI provider</h2></div><div className="panel-body">
      <dl><dt>Provider</dt><dd>{status.data.provider === 'groq' ? 'GroqCloud' : status.data.provider === 'openai' ? 'OpenAI' : 'Unsupported provider'}</dd>
        <dt>Configuration</dt><dd>{status.data.configured ? 'Configured' : 'Configuration required'}</dd>
        <dt>API key</dt><dd>{status.data.api_key_configured ? 'Configured' : 'Missing'}</dd>
        <dt>Model</dt><dd>{status.data.model || '—'}</dd><dt>Backend URL</dt><dd>{base}</dd><dt>Runtime tests</dt><dd>{status.data.runtime}</dd></dl>
      <Feedback error={status.data.configuration_message} />
      <p className="muted">AI actions send bounded excerpts to the configured provider. Repository answers link to source ranges retrieved from the working copy.</p>
    </div></section>}
  </Page>
}

const navigation = [
  ['', 'Repository Workspace', FolderOpen],
  ['bugs', 'Bug Analysis', Bug],
  ['improve', 'AI Code Improvements', Sparkles],
  ['explain', 'Code Explanation', BookOpen],
  ['assistant', 'AI Repository Assistant', MessageSquare],
  ['security', 'Security Analysis', ShieldCheck],
]

function Shell() {
  const health = useData('/health')
  const { pathname, search } = useLocation()
  const repoId = pathname.match(/^\/(?:repositories|repos)\/([^/]+)/)?.[1]
  const record = useData(/^\/(findings|patches)\/[^/]+/.test(pathname)
    ? pathname.split('/').slice(0, 3).join('/') : null)
  const routeRepo = repoId || record.data?.repo_id
  const previousRepo = readPreference('code-review-active-repository')
  const savedRepo = /^[a-f0-9-]{36}$/i.test(previousRepo || '') ? previousRepo : null
  const remembered = useData(!routeRepo && savedRepo ? '/repos/' + encodeURIComponent(savedRepo) : null)
  const currentRepo = routeRepo || (remembered.status !== 404 ? savedRepo : null)
  useEffect(() => {
    if (routeRepo) savePreference('code-review-active-repository', routeRepo)
  }, [routeRepo])

  const links = [['/', 'Dashboard', LayoutDashboard], ['/import', 'Import Repository', Plus],
    ...navigation.map(([view, label, Icon]) => [currentRepo ? workspacePath(currentRepo, view) : '/workspace' + (view ? '/' + view : ''), label, Icon]),
    ['/analytics', 'Analytics', BarChart3], ['/settings', 'Settings', SettingsIcon]]
  const activeLabel = links.find(([path]) => pathname === path)?.[1] || 'Repository Workspace'
  const tabs = repoId ? <RepoTabs repoId={repoId} /> : null
  return <div className="shell"><aside><Link className="brand" to="/"><span><Code2 size={22} /></span><div>Code Review<small>Engineering workspace</small></div></Link>
    <nav aria-label="Main navigation">{links.map(([path, title, Icon]) => <NavLink key={path} end to={path}><Icon size={18} />{title}</NavLink>)}</nav>
    <div className="connection" role="status"><span className={'dot ' + (health.data ? 'online' : health.error ? 'high' : 'low')} />
      {health.data ? 'Backend connected' : health.error ? 'Backend unavailable' : 'Connecting…'}</div>
  </aside><div className="main-area"><div className="topbar"><Link to="/">Workspace</Link><ChevronRight size={14} /><span>{activeLabel}</span>{currentRepo && <Link className="repository-switch" to="/workspace">Change repository</Link>}</div>
  <main className={/\/workspace\/(assistant|explain)$/.test(pathname) ? 'ide-main' : undefined}><Routes>
    <Route path="/" element={<Overview />} /><Route path="/import" element={<Import />} />
    <Route path="/analytics" element={<Analytics />} /><Route path="/settings" element={<Settings />} />
    <Route path="/workspace/:view?" element={<RepositoryPicker />} />
    <Route path="/repositories/:repoId/workspace" element={<Workspace key={repoId} />} />
    <Route path="/repositories/:repoId/workspace/files" element={<Explorer key={pathname + search} />} />
    <Route path="/repositories/:repoId/workspace/bugs" element={<Results category="bugs" key={pathname} />} />
    <Route path="/repositories/:repoId/workspace/security" element={<Results category="security" key={pathname} />} />
    <Route path="/repositories/:repoId/workspace/quality" element={<Results category="quality" key={pathname} />} />
    <Route path="/repositories/:repoId/workspace/improve" element={<SourceTool tabs={tabs} key={pathname} />} />
    <Route path="/repositories/:repoId/workspace/explain" element={<CodeExplanation tabs={tabs} key={pathname + search} />} />
    <Route path="/repositories/:repoId/workspace/assistant" element={<RepositoryChat tabs={tabs} key={pathname} />} />
    <Route path="/repositories/:repoId/workspace/architecture" element={<Architecture tabs={tabs} key={pathname} />} />
    <Route path="/repositories/:repoId/workspace/review" element={<CodeReview tabs={tabs} key={pathname} />} />
    <Route path="/repositories/:repoId/workspace/github" element={<PullRequests tabs={tabs} key={pathname} />} />
    <Route path="/repositories/:repoId/workspace/patches" element={<PatchList />} />
    <Route path="/repos/:repoId" element={<Explorer key={pathname} />} /><Route path="/repos/:repoId/analysis" element={<Results key={pathname} />} />
    <Route path="/repos/:repoId/patches" element={<PatchList />} /><Route path="/findings/:findingId" element={<Issue key={pathname} />} />
    <Route path="/patches/:patchId" element={<Patch key={pathname} />} /><Route path="/patches/:patchId/comparison" element={<Patch comparison key={pathname} />} />
    <Route path="*" element={<Page title="Page unavailable"><Link className="button" to="/">Back to dashboard</Link></Page>} />
  </Routes></main></div></div>
}

export default function App() {
  return <BrowserRouter><Shell /></BrowserRouter>
}

