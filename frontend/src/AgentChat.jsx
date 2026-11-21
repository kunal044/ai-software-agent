import { lazy, Suspense, useEffect, useRef, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { Check, FileCode, Maximize2, Minimize2, Plus, Send, X } from 'lucide-react'
import { api, base, useAction, useData, workspacePath } from './api.js'
import { Feedback } from './ui.jsx'
import { WorkspacePanels, WorkspaceFiles } from './WorkspacePanels.jsx'
import { readEditorPreferences, readPreference, savePreference } from './workspacePreferences.js'
import './AgentChat.css'

const MarkdownReply = lazy(() => import('./MarkdownReply.jsx'))
const Editor = lazy(() => import('./monaco.js'))
const DiffView = lazy(() => import('./DiffView.jsx'))
const editorOptions = { readOnly: true, automaticLayout: true, minimap: { enabled: false }, scrollBeyondLastLine: false }

function newRequestId() {
  const bytes = crypto.getRandomValues(new Uint8Array(16))
  bytes[6] = (bytes[6] & 15) | 64
  bytes[8] = (bytes[8] & 63) | 128
  const hex = [...bytes].map(byte => byte.toString(16).padStart(2, '0')).join('')
  return [hex.slice(0, 8), hex.slice(8, 12), hex.slice(12, 16), hex.slice(16, 20), hex.slice(20)].join('-')
}

function explainError(error) {
  const status = error.response?.status
  const detail = error.response?.data?.detail
  if (detail?.message) return detail.message
  if (status === 404 && (!detail || detail === 'Not Found')) {
    return 'The API at ' + base + ' is missing the coding-agent routes. Restart FastAPI from this project and verify VITE_API_URL.'
  }
  if (status === 429) return (detail || 'The AI provider reached its rate limit.') + ' Wait a moment, then retry.'
  if (Array.isArray(detail)) return detail.map(item => item.msg).join(', ')
  return typeof detail === 'string' ? detail : 'Could not complete the request. Check FastAPI at ' + base + ', then retry.'
}

export default function AgentChat({ tabs }) {
  const { repoId } = useParams()
  const prefix = '/repos/' + repoId
  const sessionUrl = prefix + '/agent/sessions'
  const sessionKey = 'code-review-agent:' + repoId
  const editorKey = 'code-review-editor:' + repoId
  const [sessionId, setSessionId] = useState(() => readPreference(sessionKey))
  const [panel, setPanel] = useState(() => readEditorPreferences(editorKey))
  const [revision, refresh] = useState(0)
  const [draft, setDraft] = useState('')
  const [optimistic, setOptimistic] = useState(null)
  const [sending, setSending] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [view, setView] = useState('source')
  const [patchId, setPatchId] = useState('')
  const [diffPath, setDiffPath] = useState('')
  const [range, setRange] = useState(null)
  const [maximized, setMaximized] = useState(false)
  const lock = useRef(false)
  const retry = useRef(null)
  const transcript = useRef(null)
  const activity = useAction()
  const sessions = useData(sessionUrl, revision)
  const settings = useData('/settings')
  const activeId = sessionId ?? sessions.data?.[0]?.id
  const session = useData(activeId ? sessionUrl + '/' + activeId : null, revision)
  const [delivered, setDelivered] = useState(null)
  const turns = delivered && delivered.id === activeId ? delivered.turns : session.data?.turns || []
  const files = useData(prefix + '/files', revision)
  const selected = panel.selected || ''
  const openTabs = panel.tabs || []
  const expanded = panel.expanded || []
  const source = useData(selected ? prefix + '/file?path=' + encodeURIComponent(selected) : null, revision)
  const patch = useData(patchId ? '/patches/' + patchId : null, revision)
  const changes = patch.data?.changes || []
  const item = changes.find(change => change.path === diffPath) || changes[0]
  const last = turns.at(-1)
  const restoring = sessionId === null && sessions.loading
  const busy = sending || activity.busy || restoring

  useEffect(() => { savePreference(editorKey, JSON.stringify(panel)) }, [editorKey, panel])
  useEffect(() => {
    if (transcript.current) transcript.current.scrollTop = transcript.current.scrollHeight
  }, [turns.length, optimistic, sending])

  function remember(id) {
    setSessionId(id)
    savePreference(sessionKey, id)
  }

  function openFile(path, citation = null) {
    setPanel(current => ({ ...current, selected: path, tabs: [...new Set([...(current.tabs || []), path])] }))
    setRange(citation)
    setView('source')
  }

  function closeFile(path) {
    setPanel(current => {
      const remaining = (current.tabs || []).filter(tab => tab !== path)
      return { ...current, tabs: remaining, selected: current.selected === path ? remaining.at(-1) || '' : current.selected }
    })
    setRange(null)
  }

  function review(id) {
    setPatchId(id)
    setDiffPath('')
    setView('diff')
  }

  async function send(text, forceNew = false) {
    if (!text.trim() || lock.current || restoring) return
    lock.current = true
    const request = !forceNew && retry.current?.text === text
      ? retry.current : { text, selected_path: selected || null, request_id: newRequestId() }
    retry.current = request
    setOptimistic(request)
    setSending(true)
    setError('')
    setNotice('')
    let id = activeId
    try {
      const create = async () => {
        const { data } = await api.post(sessionUrl)
        remember(data.id)
        return data.id
      }
      if (!id) id = await create()
      let response
      try {
        response = await api.post(sessionUrl + '/' + id + '/messages', request)
      } catch (failure) {
        if (failure.response?.data?.detail?.code !== 'session_missing') throw failure
        id = await create()
        response = await api.post(sessionUrl + '/' + id + '/messages', request)
      }
      const data = response.data
      setDelivered(data)
      remember(data.id)
      const reply = data.turns.find(turn => turn.request_id === request.request_id)?.reply || data.turns.at(-1)?.reply
      if (reply?.patch_id) review(reply.patch_id)
      retry.current = null
      setOptimistic(null)
      setDraft('')
      refresh(n => n + 1)
    } catch (failure) {
      setError(explainError(failure))
    } finally {
      setSending(false)
      lock.current = false
    }
  }

  function decide(decision) {
    setNotice('')
    activity.run(async () => {
      const { data } = await api.post('/patches/' + patchId + '/' + decision)
      setNotice((decision === 'accept' ? 'Applied changes to ' : 'Rejected changes for ') +
        data.changes.map(change => change.path).join(', ') + '.')
      if (decision === 'accept') {
        const deleted = new Set(data.changes.filter(change => change.operation === 'delete').map(change => change.path))
        const updated = data.changes.find(change => change.path === selected && !deleted.has(change.path)) ||
          data.changes.find(change => change.operation !== 'delete')
        setPanel(current => {
          const remaining = (current.tabs || []).filter(path => !deleted.has(path))
          return { ...current, tabs: updated ? [...new Set([...remaining, updated.path])] : remaining,
            selected: updated?.path || (deleted.has(current.selected) ? remaining.at(-1) || '' : current.selected) }
        })
        setRange(null)
        setView('source')
      }
      refresh(n => n + 1)
    })
  }

  function switchSession(id) {
    remember(id)
    setDelivered(null)
    setPatchId('')
    setView('source')
    setDraft('')
    setError('')
    setNotice('')
    retry.current = null
    setOptimistic(null)
  }

  const sessionWarning = session.errorCode === 'session_missing' ? 'This conversation is unavailable. Sending a message will start a new one.' :
    sessions.status === 404 && !sessions.errorCode ? 'The backend is missing the coding-agent routes. Restart FastAPI from this project.' :
      session.error || sessions.error
  const treeEntries = files.data || []
  const visibleTabs = files.data ? openTabs.filter(path => treeEntries.some(entry => entry.path === path)) : openTabs

  return <div className="agent-workspace">
    <header className="ide-heading"><div><h1>AI Repository Assistant</h1>{tabs}</div>
      {notice && <span className="status-ok" role="status"><Check size={15} />{notice}</span>}</header>
    <WorkspacePanels maximized={maximized}>
      <WorkspaceFiles files={files} selected={selected} expanded={expanded}
        onExpand={next => setPanel(current => ({ ...current, expanded: next }))} onSelect={openFile} />
      <section className="ide-panel ide-assistant" aria-label="AI assistant">
        <header className="ide-panel-title"><h2>AI assistant</h2><button className="button icon-button" aria-label="New conversation"
          title="New conversation" disabled={busy} onClick={() => switchSession('')}><Plus size={17} /></button></header>
        {sessions.data?.length > 0 && <div className="ide-history"><select aria-label="Conversation history" value={activeId || ''} disabled={busy}
          onChange={event => switchSession(event.target.value)}>
          <option value="">New conversation</option>
          {sessions.data.map(conversation => <option key={conversation.id} value={conversation.id}>{conversation.title}</option>)}
        </select></div>}
        <div className="ide-scroll ide-transcript" ref={transcript} aria-live="polite">
          {settings.data && !settings.data.configured && <Feedback error={settings.data.configuration_message} />}
          <Feedback error={sessionWarning || activity.error} busy={session.loading && !turns.length} message="Restoring conversation…" />
          {!turns.length && !optimistic && !session.loading && <div className="ide-empty"><p>Ask a question or request a change to the selected file.</p></div>}
          {turns.map(({ text, reply, request_id }, index) => <article className="chat-turn" key={request_id || index}>
            <p className="chat-question">{text}</p>
            <div className="answer">{reply.answer && <Suspense fallback={<p>{reply.answer}</p>}><MarkdownReply text={reply.answer} /></Suspense>}
              {reply.citations && <ul className="ide-citations">{reply.citations.map(citation => <li key={citation.path + ':' + citation.start}>
                <button className="button" onClick={() => openFile(citation.path, citation)}>{citation.path}:{citation.start}–{citation.end}</button>
              </li>)}</ul>}
              {reply.type === 'analysis' && <p>{reply.files} files analyzed · {reply.findings} findings. <Link to={workspacePath(repoId, 'bugs')}>View analysis</Link></p>}
              {reply.patch_id && <><p>Proposed changes for {reply.paths.join(', ')}</p>
                <button type="button" className="button" onClick={() => review(reply.patch_id)}>Review changes</button></>}
            </div>
          </article>)}
          {optimistic && !turns.some(turn => turn.request_id === optimistic.request_id) && <p className="chat-question">{optimistic.text}</p>}
        </div>
        <form className="ide-composer" onSubmit={event => { event.preventDefault(); send(draft) }}>
          <Feedback error={error} busy={busy} message={sending ? 'Reading context and waiting for the agent…' : restoring ? 'Restoring conversation...' : 'Recording patch decision…'} />
          {error && optimistic && <button type="button" className="button" disabled={busy} onClick={() => send(optimistic.text)}>Retry message</button>}
          <label><span>{selected ? 'Context · ' + selected : 'Repository context'}</span><textarea aria-label="Message" value={draft}
            placeholder="Ask or request a change…" rows={2} maxLength={1500} disabled={busy}
            onChange={event => setDraft(event.target.value)} onKeyDown={event => {
              if (event.key === 'Enter' && (event.ctrlKey || event.metaKey) && !event.nativeEvent.isComposing) {
                event.preventDefault()
                event.currentTarget.form.requestSubmit()
              }
            }} /></label>
          <div className="ide-send"><span>Ctrl + Enter</span><button className="button primary" disabled={busy || !draft.trim()}><Send size={15} /> Send</button></div>
        </form>
      </section>
      <section className="ide-panel ide-editor" aria-label="Code editor">
        <header className="ide-panel-title"><h2>Editor</h2><div className="actions">
          {patchId && <button className="button" onClick={() => setView(view === 'diff' ? 'source' : 'diff')}>{view === 'diff' ? 'Source' : 'Diff'}</button>}
          <button className="button icon-button" aria-label={maximized ? 'Restore editor' : 'Maximize editor'} onClick={() => setMaximized(!maximized)}>
            {maximized ? <Minimize2 size={16} /> : <Maximize2 size={16} />}
          </button>
        </div></header>
        {visibleTabs.length > 0 && <div className="ide-tabs" aria-label="Open files">{visibleTabs.map(path => <div className={path === selected ? 'active' : ''} key={path}>
          <button aria-pressed={path === selected} title={path} onClick={() => openFile(path)}><FileCode size={14} />{path.split('/').at(-1)}</button>
          <button aria-label={'Close ' + path} onClick={() => closeFile(path)}><X size={14} /></button>
        </div>)}</div>}
        {view === 'source' ? <>
          {selected && <div className="ide-path">{selected}<span>{source.data?.language_name ? source.data.language_name + ' ? ' : ''}Read only</span></div>}
          <Feedback error={source.error} busy={source.loading} message="Loading source…" />
          <div className="ide-code">
            {source.data ? <Suspense fallback={<p className="panel-body muted">Loading editor…</p>}>
              <Editor key={selected + ':' + (range?.start || '') + ':' + (range?.end || '')} height="100%" theme="vs"
                language={source.data.language} value={source.data.content} options={editorOptions}
                onMount={editor => {
                  if (!range?.start) return
                  const model = editor.getModel()
                  const end = Math.min(range.end || range.start, model.getLineCount())
                  const start = Math.min(range.start, end)
                  editor.setSelection({ startLineNumber: start, startColumn: 1, endLineNumber: end, endColumn: model.getLineMaxColumn(end) })
                  editor.revealLineInCenter(start)
                }} />
            </Suspense> : !source.loading && !source.error && <div className="ide-empty"><FileCode size={24} /><p>Select a project file to preview its source.</p></div>}
          </div>
        </> : <>
          <Feedback error={patch.error} busy={patch.loading} message="Loading proposed changes…" />
          {item && <><div className="ide-path">{item.path}
            {changes.length > 1 && <select aria-label="File diff" value={item.path} onChange={event => setDiffPath(event.target.value)}>
              {changes.map(change => <option key={change.path}>{change.path}</option>)}
            </select>}</div>
            <div className="ide-diff-labels"><span>Original</span><span>Proposed</span></div>
            <div className="ide-code"><Suspense fallback={<p className="panel-body muted">Loading diff…</p>}>
              <DiffView original={item.original} modified={item.proposed} language={item.language || 'python'} inlineBreakpoint={320} />
            </Suspense></div></>}
        </>}
        {patch.data && item && <div className="ide-review">
          <div className="actions"><span className="badge">{patch.data.status}</span><span>Validation {patch.data.syntax}</span></div>
          <p>File validation ? {item.syntax || patch.data.syntax}</p>
          <p title={item.path}><strong>{item.path}</strong> · {item.explanation || item.expected}</p>
          <div className="actions">
            {patch.data.status === 'proposed' && <>
              <button className="button primary" disabled={busy} onClick={() => decide('accept')}>Accept Changes</button>
              <button className="button" disabled={busy} onClick={() => decide('reject')}>Reject Changes</button>
            </>}
            {last?.reply.patch_id === patchId && ['proposed', 'rejected'].includes(patch.data.status) &&
              <button className="button" disabled={busy} onClick={() => send(last.text, true)}>Regenerate Proposal</button>}
            {patch.data.status === 'accepted' && <>
              {item.operation !== 'delete' && <button className="button" onClick={() => openFile(item.path)}>View Updated File</button>}
              <Link className="button" to={'/patches/' + patchId + '/comparison'}>Before / after</Link>
              <button className="button" disabled={busy} onClick={() => send('Find bugs in this repository')}>Reanalyze</button>
            </>}
          </div>
        </div>}
      </section>
    </WorkspacePanels>
  </div>
}

