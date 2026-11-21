import { useState } from 'react'
import { Link } from 'react-router-dom'
import { Activity, BarChart3, Check, ClipboardCheck } from 'lucide-react'
import { api, useAction, useData } from './api.js'
import { Feedback, Page } from './ui.jsx'

const metricFields = [
  ['proposed', 'Proposed fixes'], ['accepted', 'Accepted fixes'], ['rejected', 'Rejected fixes'],
  ['syntax_valid', 'Syntax-valid patches'], ['targeted_resolved', 'Targeted findings resolved'],
  ['introduced', 'New findings introduced'], ['verified_fix_success_rate', 'Static verification success rate'],
  ['runtime_verified_fixes', 'Runtime-verified fixes'],
]
const benchmarkFields = [
  ['true_positive', 'True positives'], ['false_positive', 'False positives'], ['false_negative', 'False negatives'],
  ['precision', 'Precision'], ['recall', 'Recall'], ['f1', 'F1 score'],
]
const percentages = new Set(['verified_fix_success_rate', 'precision', 'recall', 'f1'])

function format(key, value) {
  if (value == null) return '—'
  return percentages.has(key) ? new Intl.NumberFormat(undefined, {
    style: 'percent', maximumFractionDigits: 1,
  }).format(value) : value.toLocaleString()
}

function CardGrid({ data, fields }) {
  return <div className="metrics">{fields.map(([key, label]) => <article className="metric" key={key}>
    <span className="metric-icon"><Activity size={17} /></span>
    <strong>{format(key, data[key])}</strong><span>{label}</span>
  </article>)}</div>
}

function Distribution({ title, values }) {
  const rows = Object.entries(values || {})
  const total = rows.reduce((sum, [, count]) => sum + count, 0)
  return <section className="panel"><div className="panel-title"><h2>{title}</h2><BarChart3 size={16} /></div>
    <div className="panel-body evaluation-bars">
      {rows.length ? rows.map(([label, value]) => <div className="evaluation-bar" key={label}>
        <span className="capitalize">{label}</span>
        <progress aria-label={label} className={label} max={total || 1} value={value} />
        <strong>{value}</strong>
      </div>) : <p className="muted">No recorded data.</p>}
    </div>
  </section>
}

function ScanHistory({ scans }) {
  const [repo, setRepo] = useState('')
  const repos = [...new Set(scans.map(item => item.repo))]
  const visible = repo ? scans.filter(item => item.repo === repo) : scans
  const maximum = Math.max(1, ...visible.map(item => item.findings))
  return <section className="panel"><div className="panel-title"><h2>Recent scan trends</h2>
    <select aria-label="Filter scans by repository" value={repo} onChange={event => setRepo(event.target.value)}>
      <option value="">All repositories</option>
      {repos.filter(Boolean).map(name => <option key={name}>{name}</option>)}
    </select></div>
    <div className="panel-body evaluation-bars">
      {!visible.length && <p className="muted">Run an analysis to start scan history.</p>}
      {visible.map(item => <div className="evaluation-bar evaluation-trend" key={item.id}>
        <span><strong>{item.repo || 'Repository'}</strong> <small>{item.created_at ? new Date(item.created_at).toLocaleString() : 'Undated scan'}</small></span>
        <progress aria-label={item.repo + ' findings'} value={item.findings} max={maximum} />
        <span>{item.findings} findings · {item.duration_ms} ms</span>
      </div>)}
    </div>
  </section>
}

function Benchmark() {
  const saved = useData('/dashboard/benchmark/latest')
  const [started, setStarted] = useState(null)
  const action = useAction()
  const result = started || saved.data
  return <section className="panel"><div className="panel-title"><h2>Labeled detection benchmark</h2>
    <button className="button primary" disabled={action.busy} onClick={() => action.run(async () => {
      const { data } = await api.post('/dashboard/benchmark/run')
      setStarted(data)
    })}><ClipboardCheck size={16} /> Run benchmark</button></div>
    <div className="panel-body evaluation-detail">
      <Feedback busy={saved.loading || action.busy} error={saved.error || action.error} message="Checking benchmark source files…" />
      {!result && !saved.loading && <p className="muted">Run the bundled benchmark to measure precision, recall, and F1 against its labeled findings.</p>}
      {result && <>
        <p><strong>{result.dataset}</strong> · {new Date(result.created_at).toLocaleString()}</p>
        {result.metrics ? <CardGrid data={result.metrics} fields={benchmarkFields} />
          : <p className="muted">Scores unavailable. These fixtures were not analyzed: {result.missing.join(', ')}.</p>}
        <p className="muted">Precision uses detected findings. Recall uses labeled findings. F1 uses both sets. A zero denominator displays a dash.</p>
        <details><summary>Scored labels and detected findings</summary>
          {[['Expected', result.expected], ['Detected', result.detected]].map(([label, items]) => <div key={label}>
            <h3>{label}</h3><ul>{items.map(item =>
              <li key={item.path + ':' + item.rule + ':' + item.line}>{item.path}:{item.line} · {item.rule}</li>)}</ul>
          </div>)}
          {result.known_unsupported.map(item => <p key={item.path}>{item.path} · {item.description}</p>)}
        </details>
      </>}
    </div>
  </section>
}

export default function Analytics() {
  const report = useData('/dashboard')
  const data = report.data
  return <Page title="Analytics" subtitle="Inspect recorded scan findings, patch outcomes, and detection benchmark scores.">
    <Feedback busy={report.loading} error={report.error} />
    {data && <>
      <CardGrid data={data} fields={metricFields} />
      <p className="scan-summary">Static verification success uses accepted targeted fixes as its denominator ({data.verified_static_successes} of {data.targeted_accepted}). A success requires a resolved target, available syntax validation, complete static coverage, and zero new findings. Runtime tests are not run.</p>
      <div className="evaluation-grid">
        <Distribution title="Bug severity distribution" values={data.severity} />
        <Distribution title="Findings by language" values={data.findings_by_language} />
        <Distribution title="Fix approval" values={{ accepted: data.accepted, rejected: data.rejected, other: data.proposed - data.accepted - data.rejected }} />
        <Distribution title="Verification outcomes" values={data.verification_outcomes} />
        <Distribution title="Syntax validation results" values={data.syntax} />
      </div>
      <ScanHistory scans={data.scan_trend || []} />
      <section className="panel"><div className="panel-title"><h2>Recent before / after comparisons</h2><Check size={16} /></div>
        {data.verification_history?.length ? data.verification_history.map(item =>
          <Link key={item.id} className="repo-row" to={'/patches/' + item.id + '/comparison'}>
            <div><strong>{item.path}</strong><small>{item.repo} · {item.status}</small></div>
            <span>{item.before} → {item.after} findings</span>
          </Link>) : <div className="panel-body muted">Approve a patch to record a comparison.</div>}
      </section>
    </>}
    <Benchmark />
  </Page>
}

export function VerificationReport({ patchId, Findings, Severity }) {
  const result = useData('/patches/' + patchId + '/verification')
  const review = result.data?.comparison
  const record = result.data?.verification
  return <>
    <Feedback busy={result.loading} error={result.error} />
    {record && <section className="panel"><div className="panel-title"><h2>Verification status</h2><span className="badge">{record.status}</span></div>
      <div className="panel-body evaluation-detail">
        <dl><dt>Syntax validation</dt><dd>{record.syntax}</dd><dt>Static coverage</dt><dd>{record.static_coverage}</dd>
          <dt>Targeted finding</dt><dd>{record.target}</dd><dt>Runtime tests</dt><dd>{record.runtime}</dd></dl>
        {record.uncomparable_paths?.length > 0 && <p>Uncomparable files: {record.uncomparable_paths.join(', ')}</p>}
        <h3>Changed files</h3>
        <ul>{record.changed_files.map(file => <li key={file.path}>{file.path} · {file.operation} · {file.syntax}</li>)}</ul>
        <Link className="button" to={'/patches/' + patchId}>View code diff</Link>
        <p className="muted">Static checks measure analyzer findings. Behavioral correctness requires separate verification.</p>
      </div>
    </section>}
    {review && <>
      {!record && <p className="scan-summary">Historical scan comparison. Runtime tests {review.runtime}.</p>}
      <div className="evaluation-grid">
        {[['Before', review.before_severity], ['After', review.after_severity]].map(([label, values]) =>
          <section className="panel" key={label}><div className="panel-title"><h2>{label}</h2></div><Severity values={values} /></section>)}
      </div>
      {[['Original findings', result.data.original], ['Remaining findings', review.remaining],
        ['Resolved findings', review.resolved], ['Newly introduced findings', review.introduced]].map(([label, items]) =>
        <section className="panel" key={label}><div className="panel-title"><h2>{label} ({items.length})</h2></div><Findings items={items} /></section>)}
    </>}
  </>
}

