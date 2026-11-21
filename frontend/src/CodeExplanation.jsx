import { lazy, Suspense, useRef, useState } from 'react'
import { Link, useParams, useSearchParams } from 'react-router-dom'
import { BookOpen, FileCode, Maximize2, Minimize2, Sparkles } from 'lucide-react'
import { api, sourceQuery, useAction, useData, workspacePath } from './api.js'
import { Feedback } from './ui.jsx'
import { WorkspaceFiles, WorkspacePanels } from './WorkspacePanels.jsx'
import './AgentChat.css'

const MarkdownReply = lazy(() => import('./MarkdownReply.jsx'))
const Editor = lazy(() => import('./monaco.js'))
const options = { readOnly: true, fontSize: 13, automaticLayout: true, minimap: { enabled: false }, scrollBeyondLastLine: false }

function highlight(editor, start, end) {
  const model = editor?.getModel()
  if (!model || !Number.isInteger(start) || !Number.isInteger(end) || start < 1 || end < start || start > model.getLineCount()) return
  const last = Math.min(end, model.getLineCount())
  editor.setSelection({ startLineNumber: start, startColumn: 1, endLineNumber: last, endColumn: model.getLineMaxColumn(last) })
  editor.revealLineInCenter(start)
}

export default function CodeExplanation({ tabs }) {
  const { repoId } = useParams()
  const [params] = useSearchParams()
  const prefix = '/repos/' + repoId
  const files = useData(prefix + '/files')
  const settings = useData('/settings')
  const [path, setPath] = useState(params.get('path') || '')
  const [range, setRange] = useState([params.get('start') || '', params.get('end') || ''])
  const [question, setQuestion] = useState('')
  const [revision, setRevision] = useState(0)
  const [answer, setAnswer] = useState(null)
  const [maximized, setMaximized] = useState(false)
  const editorRef = useRef(null)
  const state = useData(path ? prefix + '/source-context?path=' + encodeURIComponent(path) : null, revision)
  const action = useAction()
  const source = state.data
  const symbols = source?.symbols || []
  const validRange = range.every(line => !line) || (range.every(Boolean) && Number(range[0]) <= Number(range[1]))

  function select(file) {
    editorRef.current = null
    setPath(file)
    setRange(['', ''])
    setAnswer(null)
  }

  function submit(event) {
    event.preventDefault()
    if (!source || !validRange || action.busy) return
    action.run(async () => {
      const { data } = await api.post(prefix + '/explain', {
        path, source_hash: source.source_hash,
        start: range[0] ? Number(range[0]) : null,
        end: range[1] ? Number(range[1]) : null,
        question: question.trim() || 'Explain the purpose, flow, inputs, outputs, and limitations of this code.',
      })
      setAnswer(data)
    })
  }

  return <div className="agent-workspace explanation-workspace">
    <header className="ide-heading"><div><h1>Code Explanation</h1>{tabs}</div></header>
    <WorkspacePanels maximized={maximized} initial={[25, 65]}>
      <WorkspaceFiles files={files} selected={path} onSelect={select} />
      <section className="ide-panel ide-editor" aria-label="Code editor">
        <header className="ide-panel-title"><h2>Source preview</h2><button className="button icon-button"
          aria-label={maximized ? 'Restore editor' : 'Maximize editor'} onClick={() => setMaximized(!maximized)}>
          {maximized ? <Minimize2 size={16} /> : <Maximize2 size={16} />}</button></header>
        {path && <div className="ide-path">{path}<span>Read only</span></div>}
        <Feedback error={state.error} busy={state.loading} message="Loading source…" />
        <div className="ide-code">
          {source && <Suspense fallback={<p className="panel-body muted">Loading editor…</p>}>
            <Editor height="100%" key={path + ':' + revision} theme="vs" language={source.language} value={source.content}
              options={options} onMount={editor => {
                editorRef.current = editor
                if (range.every(Boolean)) highlight(editor, Number(range[0]), Number(range[1]))
                editor.onDidChangeCursorSelection(({ selection }) => {
                  if (selection.isEmpty()) return
                  const end = selection.endColumn === 1 && selection.endLineNumber > selection.startLineNumber
                    ? selection.endLineNumber - 1 : selection.endLineNumber
                  setRange([String(selection.startLineNumber), String(end)])
                })
              }} />
          </Suspense>}
          {!path && <div className="ide-empty"><FileCode size={24} /><p>Choose a file, then select the lines you want explained.</p></div>}
        </div>
      </section>
      <section className="ide-panel" aria-label="Explanation workspace">
        <header className="ide-panel-title"><h2>Explain code</h2><BookOpen size={16} /></header>
        <div className="ide-scroll explanation-content">
          {settings.data && !settings.data.configured && <Feedback error={settings.data.configuration_message} />}
          <form className="explain-form" onSubmit={submit}>
            {symbols.length > 0 && <label>Function<select value={range.join(':')} disabled={action.busy}
              onChange={event => {
                const lines = event.target.value.split(':')
                setRange(lines)
                if (lines.every(Boolean)) highlight(editorRef.current, Number(lines[0]), Number(lines[1]))
              }}>
              <option value=":">Whole file</option>
              {range.some(Boolean) && !symbols.some(symbol => [String(symbol.start), String(symbol.end)].join(':') === range.join(':')) &&
                <option value={range.join(':')}>Custom selection</option>}
              {symbols.map(symbol => <option key={symbol.start} value={symbol.start + ':' + symbol.end}>{symbol.name} · lines {symbol.start}–{symbol.end}</option>)}
            </select></label>}
            <div className="range-fields">{['Start line', 'End line'].map((label, index) =>
              <label key={label}>{label}<input type="number" min="1" max={source?.content?.split('\n').length}
                placeholder="Whole file" value={range[index]} disabled={!source || action.busy}
                onChange={event => setRange(previous => previous.map((line, i) => i === index ? event.target.value : line))} /></label>)}</div>
            <label>Question<textarea rows={3} maxLength={1000} value={question} disabled={action.busy}
              placeholder="What would you like to understand?" onChange={event => setQuestion(event.target.value)} /></label>
            {!validRange && <p className="muted">Enter both line numbers with the start before or equal to the end.</p>}
            <div className="actions"><button className="button primary" disabled={!source || !validRange || action.busy}><Sparkles size={15} /> Explain code</button>
              {path && <button type="button" className="button" disabled={action.busy} onClick={() => {
                setRevision(value => value + 1)
                setAnswer(null)
              }}>Reload source</button>}</div>
          </form>
          <Feedback error={action.error} busy={action.busy} message="Waiting for the AI provider…" />
          {answer ? <article className="explain-output" aria-live="polite"><h2>Explanation</h2>
            <Suspense fallback={<p>{answer.answer}</p>}><MarkdownReply text={answer.answer} /></Suspense>
            <ul className="ide-citations">{(answer.citations || []).map(citation => <li key={citation.path + ':' + citation.start}>
              <Link className="button" to={workspacePath(repoId, 'files') + sourceQuery(citation.path, citation.start, citation.end)}>
                {citation.path}:{citation.start}–{citation.end}
              </Link>
            </li>)}</ul>
          </article> : <div className="ide-empty"><p>Explanations and source references appear here.</p></div>}
        </div>
      </section>
    </WorkspacePanels>
  </div>
}

