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

function BenchmarkBreakdown({ title, rows }) {
  return <details><summary>{title}</summary>
    <ul>{Object.entries(rows).map(([label, metrics]) => <li key={label}>
      <strong>{label}</strong> ({metrics.evaluated}/{metrics.cases} cases evaluated)
      {' · '}{benchmarkFields.map(([key, name]) => name + ' ' + format(key, metrics[key])).join(' · ')}
      {' · '}{metrics.positive_labels} positive labels, {metrics.detected_findings} detections
    </li>)}</ul>
  </details>
}

function Benchmark({ onRun }) {
  const saved = useData('/dashboard/benchmark/latest')
  const [started, setStarted] = useState(null)
  const action = useAction()
  const result = started || saved.data
  return <section className="panel"><div className="panel-title"><h2>Labeled detection benchmark</h2>
    <button className="button primary" disabled={action.busy} onClick={() => action.run(async () => {
      const { data } = await api.post('/dashboard/benchmark/run')
      setStarted(data)
      onRun()
    })}><ClipboardCheck size={16} /> Run benchmark</button></div>
    <div className="panel-body evaluation-detail">
      <Feedback busy={saved.loading || action.busy} error={saved.error || action.error} message="Checking benchmark source files…" />
      {!result && !saved.loading && <p className="muted">Run the bundled benchmark to measure precision, recall, and F1 against its labeled findings.</p>}
      {result && <>
        <p><strong>{result.dataset}</strong> · {new Date(result.created_at).toLocaleString()}</p>
        {result.metrics ? <CardGrid data={result.metrics} fields={benchmarkFields} />
          : <p className="muted">Scores unavailable. These fixtures were not analyzed: {result.missing.join(', ')}.</p>}
        <p className="muted">Precision uses detected findings. Recall uses labeled findings. F1 uses both sets. A zero denominator displays a dash.</p>
        {result.mode && <p>{result.mode} · {result.metrics.evaluated} of {result.metrics.cases} cases evaluated · {result.metrics.supported} supported · {result.metrics.unsupported} unsupported</p>}
        {result.mode && <p className="muted">{result.metrics.positive_labels} expected positives · {result.metrics.detected_findings} detections · {result.metrics.true_negative} clean files without findings{result.missing.length > 0 && ' · ' + result.missing.length + ' supported files unavailable'}</p>}
        {result.by_language && <BenchmarkBreakdown title="Results by language" rows={result.by_language} />}
        {result.by_category && <BenchmarkBreakdown title="Results by case category" rows={result.by_category} />}
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

function EvaluationHistory({ revision }) {
  const history = useData('/dashboard/evaluations', revision)
  return <section className="panel"><div className="panel-title"><h2>Evaluation history</h2></div>
    <div className="panel-body evaluation-detail">
      <Feedback busy={history.loading} error={history.error} />
      {!!history.data?.detection.length && <details><summary>Static detection runs</summary><ul>
        {history.data.detection.map(run => <li key={run.id}>
          {run.dataset} · {new Date(run.created_at).toLocaleString()} · F1 {format('f1', run.metrics?.f1)}
          {' · '}{run.metrics?.evaluated ?? 'Unknown'} cases evaluated
        </li>)}
      </ul></details>}
      {history.data && ['mocked', 'offline replay', 'live Groq'].map(mode => {
        const runs = history.data.patches.filter(run => run.mode === mode)
        return <details key={mode} open={mode === 'live Groq'}><summary className="capitalize">{mode} patch evaluations</summary>
          {runs.length ? <ul>{runs.map(run => <li key={run.id}>
            {run.dataset} · {new Date(run.created_at).toLocaleString()}{run.model && ' · ' + run.model}
            <ul>
              <li>Generation {format('precision', run.metrics.generation_rate)} ({run.metrics.attempted} attempted)</li>
              <li>Syntax {format('precision', run.metrics.syntax_rate)} ({run.metrics.syntax_attempted} checked)</li>
              <li>Static verification {format('precision', run.metrics.static_rate)} ({run.metrics.static_checked} compared)</li>
              <li>New finding rate {format('precision', run.metrics.introduced_rate)} ({run.metrics.static_checked} compared)</li>
              <li>Runtime tests {run.case.runtime}</li>
            </ul>
          </li>)}</ul> : <p className="muted">No {mode} results recorded.</p>}
        </details>
      })}
    </div>
  </section>
}

export default function Analytics() {
  const [revision, setRevision] = useState(0)
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
    <Benchmark onRun={() => setRevision(value => value + 1)} />
    <EvaluationHistory revision={revision} />
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

