import { execFileSync, spawn } from 'node:child_process'
import { mkdir, mkdtemp, readFile, writeFile } from 'node:fs/promises'
import { join, resolve } from 'node:path'
import assert from 'node:assert/strict'
import { setTimeout as wait } from 'node:timers/promises'

const root = resolve('backend/workspace/ide-verification')
// Run the isolated servers as described in the README before invoking this script.
const mocked = process.env.IDE_SMOKE_MOCK === '1'
const settings = await (await fetch('http://127.0.0.1:8017/api/settings')).json()
assert.ok(mocked || (settings.provider === 'groq' && settings.api_key_configured), 'The isolated backend needs Groq credentials.')
await mkdir(root, { recursive: true })
execFileSync(resolve(process.env.IDE_SMOKE_PYTHON || 'backend/.venv/Scripts/python.exe'), ['-c', [
  'from zipfile import ZipFile',
  'import sys',
  'with ZipFile(sys.argv[1], "w") as fixture:',
  '    fixture.writestr("src/tools/ex.py", sys.argv[2])',
  '    fixture.writestr("other.py", sys.argv[3])',
].join('\n'), join(root, 'ide-fixture.zip'), 'if __name__ == "__main__":\n    print("Hello world!")\n', 'value = 1\n'])
const profile = await mkdtemp(join(root, 'chrome-'))
const chrome = spawn('C:/Program Files/Google/Chrome/Application/chrome.exe', [
  '--headless=new', '--disable-gpu', '--no-first-run', '--disable-background-networking',
  '--remote-debugging-port=0', '--user-data-dir=' + profile, 'about:blank',
], { windowsHide: true, stdio: 'ignore' })
let socket, diagnostic
async function until(fn, label, attempts = 150) {
  for (let i = 0; i < attempts; i++) {
    const result = await fn().catch(() => null)
    if (result) return result
    await wait(250)
  }
  throw Error('Timed out: ' + label)
}
try {
  const port = await until(async () => (await readFile(join(profile, 'DevToolsActivePort'), 'utf8')).split(/\r?\n/)[0], 'Chrome')
  const tabs = await (await fetch('http://127.0.0.1:' + port + '/json/list')).json()
  socket = new WebSocket(tabs.find(tab => tab.type === 'page').webSocketDebuggerUrl)
  await new Promise((res, rej) => {
    socket.addEventListener('open', res, { once: true })
    socket.addEventListener('error', rej, { once: true })
  })
  const jobs = new Map(), exceptions = [], network = []
  let id = 0
  socket.onmessage = event => {
    const message = JSON.parse(event.data)
    if (message.method === 'Runtime.exceptionThrown') exceptions.push(message.params.exceptionDetails)
    if (message.method === 'Network.responseReceived' && message.params.response.url.includes(':8017/api/')) {
      network.push([message.params.response.status, new URL(message.params.response.url).pathname])
    }
    const job = jobs.get(message.id)
    if (job) { clearTimeout(job.timer); jobs.delete(message.id); message.error ? job.reject(message.error) : job.resolve(message.result) }
  }
  function run(method, params = {}) {
    return new Promise((resolve, reject) => {
      const current = ++id
      jobs.set(current, { resolve, reject, timer: setTimeout(() => reject(Error(method)), 15000) })
      socket.send(JSON.stringify({ id: current, method, params }))
    })
  }
  async function js(expression) {
    const response = await run('Runtime.evaluate', { expression, returnByValue: true })
    if (response.exceptionDetails) throw Error(response.exceptionDetails.exception?.description)
    return response.result.value
  }
  diagnostic = () => js('document.body.innerText.slice(0,1500)')
  const text = value => until(() => js('document.body.innerText.toLowerCase().includes(' + JSON.stringify(value.toLowerCase()) + ')'), value, 220)
  const selector = value => until(() => js('Boolean(document.querySelector(' + JSON.stringify(value) + '))'), value)
  const click = value => until(() => js('(() => {const b=[...document.querySelectorAll("button,a")].find(e=>e.textContent.trim().toLowerCase()==='+JSON.stringify(value.toLowerCase())+');if(!b||b.disabled) return false;b.click();return true})()'), value)
  async function input(selector, value) {
    await js('(() => {const t=document.querySelector('+JSON.stringify(selector)+'); const p=Object.getPrototypeOf(t); Object.getOwnPropertyDescriptor(p,"value").set.call(t,'+JSON.stringify(value)+');t.dispatchEvent(new Event("input",{bubbles:true}));})()')
  }
  await run('Page.enable')
  await run('Runtime.enable')
  await run('Network.enable')
  await run('Emulation.setDeviceMetricsOverride', { width: 1440, height: 920, deviceScaleFactor: 1, mobile: false })
  await run('Page.navigate', { url: 'http://127.0.0.1:5173/import' })
  await click('ZIP archive')
  const tree = await run('DOM.getDocument')
  const field = await run('DOM.querySelector', { nodeId: tree.root.nodeId, selector: 'input[type=file]' })
  await run('DOM.setFileInputFiles', { nodeId: field.nodeId, files: [join(root, 'ide-fixture.zip')] })
  await click('Import project')
  await text('What would you like the AI agent to do?')
  const repoId = await js('location.pathname.split("/")[2]')
  const api = 'http://127.0.0.1:8017/api/repos/' + repoId
  const file = async () => (await (await fetch(api + '/file?path=src%2Ftools%2Fex.py')).json()).content
  const original = await file()
  assert.match(original, /print\("Hello world!"\)/)
  await click('AI Repository Assistant')
  await text('Ask a question or request a change to the selected file.')
  const assistantUrl = await js('location.href')
  assert.match(assistantUrl, /\/workspace\/assistant$/)
  await js('localStorage.setItem("code-review-agent:" + ' + JSON.stringify(repoId) + ',"11111111-1111-1111-1111-111111111111")')
  await js('localStorage.setItem("code-review-editor:" + ' + JSON.stringify(repoId) + ',JSON.stringify({tabs:"legacy",expanded:{}}))')
  await run('Page.navigate', { url: assistantUrl })
  await text('This conversation is unavailable')
  await click('src')
  await click('tools')
  await click('ex.py')
  await selector('.monaco-editor .view-line')
  await input('[aria-label="Search project files"]', 'src/tools/ex.py')
  assert.ok(await js('!document.querySelector(".ide-explorer button[title=\\"other.py\\"]")'))
  await input('[aria-label="Search project files"]', '')
  const heights = await js('[...document.querySelectorAll(".ide-panel")].map(e=>e.getBoundingClientRect().height)')
  assert.ok(heights.length === 3 && Math.max(...heights) - Math.min(...heights) < 2)
  const grip = await js('(()=>{const r=document.querySelector(".ide-divider").getBoundingClientRect();return {x:r.x+2,y:r.y+100}})()')
  await run('Input.dispatchMouseEvent', { type:'mousePressed', button:'left', x:grip.x,y:grip.y,clickCount:1 })
  await run('Input.dispatchMouseEvent', { type:'mouseMoved', button:'left', buttons:1,x:grip.x+35,y:grip.y })
  await run('Input.dispatchMouseEvent', { type:'mouseReleased', button:'left',x:grip.x+35,y:grip.y,clickCount:1 })
  assert.ok(Number(await js('document.querySelector(".ide-divider").getAttribute("aria-valuenow")')) > 25)
  const instruction = 'i want to print hello world 5 times'
  await input('.ide-composer textarea', instruction)
  await click('Send')
  await text(instruction)
  assert.equal(await file(), original)
  await text('Proposed changes for src/tools/ex.py')
  await selector('.monaco-diff-editor .line-insert, .monaco-diff-editor .line-delete')
  await selector('.ide-editor .ide-review')
  assert.equal(await file(), original)
  if (mocked) {
    await click('Reject Changes')
    await text('Rejected changes for src/tools/ex.py.')
    assert.equal(await file(), original)
    await click('Regenerate Proposal')
  }
  await click('Accept Changes')
  await text('Applied changes to src/tools/ex.py.')
  await selector('.ide-editor .monaco-editor .view-line')
  const modified = await file()
  assert.notEqual(modified, original)
  assert.ok(await js('!document.querySelector(".ide-editor .monaco-diff-editor")'))
  const array = await (await fetch(api + '/export')).arrayBuffer()
  assert.equal(Buffer.from(array).subarray(0,2).toString(), 'PK')
  await writeFile(join(root,'exported.zip'), Buffer.from(array))
  await run('Page.reload')
  await text('Proposed changes for src/tools/ex.py')
  await selector('.ide-explorer button[aria-pressed=true][title="src/tools/ex.py"]')
  await selector('.ide-editor .monaco-editor .view-line')
  await input('.ide-composer textarea', 'Find bugs in this repository')
  await js('document.querySelector(".ide-composer textarea").dispatchEvent(new KeyboardEvent("keydown",{key:"Enter",ctrlKey:true,bubbles:true,cancelable:true}))')
  await text('files analyzed')
  assert.ok(network.some(([status, path]) => status === 404 && path.endsWith('/11111111-1111-1111-1111-111111111111/messages')))
  assert.ok(network.some(([status, path]) => status === 201 && path.endsWith('/agent/sessions')))
  assert.ok(network.some(([status, path]) => status === 200 && path.endsWith('/messages')))
  if (mocked) {
    await input('.ide-composer textarea', 'fixture-rate-limit')
    await click('Send')
    await text('Wait a moment, then retry.')
    assert.equal(await js('document.querySelector(".ide-composer textarea").value'), 'fixture-rate-limit')
    await selector('.ide-composer [type=button]')
    await input('.ide-composer textarea', 'fixture-no-key')
    await click('Send')
    await until(async () => network.some(([status, path]) => status === 503 && path.endsWith('/messages')), 'missing key response')
    await text('Set GROQ_API_KEY')
    await click('Patch history')
    await text('Agent changes')
    await click('Dashboard')
    await until(() => js('location.pathname === "/"'), 'dashboard navigation')
    await until(() => js('[...document.querySelectorAll("aside a")].some(a=>a.textContent.includes("Code Explanation") && a.href.includes("/repositories/"))'), 'remembered repository')
    await click('Code Explanation')
    await text('Choose a file, then select the lines')
    await click('src')
    await click('tools')
    await click('ex.py')
    await selector('.monaco-editor .view-line')
    await js('document.querySelector(".explain-form button.primary").click()')
    await selector('.explain-output .hljs')
    await text('Greeting')
    await click('AI Repository Assistant')
    await text('Proposed changes for src/tools/ex.py')
  }

  if (mocked && process.env.IDE_SMOKE_PHASE3 === '1') {
    await click('Architecture')
    await text('Candidate entry points')
    await click('Summarize architecture')
    await text('AI interpretation')
    await text('Fixture has a Python entry point.')
    await click('Code Review')
    await selector('select[multiple] option')
    await js('(() => {const s=document.querySelector("select[multiple]"); s.options[0].selected = true; s.dispatchEvent(new Event("change",{bubbles:true}))})()')
    await click('Review selected code')
    await text('AI review suggestions')
    await text('Review the greeting output.')
    await click('Settings')
    await click('Connect GitHub')
    await text('Connected as fixture-user')
    await click('Import Repository')
    await click('Connected GitHub')
    await selector('datalist option[value="https://github.com/fixture/repository"]')
    await input('input[list="github-repository-choices"]', 'https://github.com/fixture/repository')
    await click('Import project')
    await text('What would you like the AI agent to do?')
    await click('AI Repository Assistant')
    await click('src')
    await click('tools')
    await click('ex.py')
    await input('.ide-composer textarea', instruction)
    await click('Send')
    await click('Accept Changes')
    await text('Applied changes to src/tools/ex.py.')
    await click('Pull requests')
    await text('Prepare an accepted patch')
    await until(() => js('[...document.querySelectorAll("select option")].some(o=>o.textContent.includes("src/tools/ex.py"))'), 'accepted patch choices')
    await js('(() => {const s=document.querySelector("select"); s.value=s.options[1].value; s.dispatchEvent(new Event("change",{bubbles:true}))})()')
    await click('Preview publication')
    await text('Create this feature branch')
    assert.ok(await js('[...document.querySelectorAll("button")].find(b=>b.textContent.trim()==="Publish pull request").disabled'))
    await input('input[maxlength="120"]', 'Repeat greeting')
    await js('document.querySelector(".confirm-checkbox input").click()')
    await click('Publish pull request')
    await text('PR #42')
    await click('Refresh status')
    await text('merged')
    const importedId = await js('location.pathname.split("/")[2]')
    const report = await (await fetch('http://127.0.0.1:8017/api/repos/' + importedId + '/report')).text()
    assert.match(report, /Accepted: 1/)
    assert.match(report, /Runtime verification: NOT RUN/)
    const pdf = await fetch('http://127.0.0.1:8017/api/repos/' + importedId + '/report.pdf')
    assert.equal(pdf.headers.get('content-type'), 'application/pdf')
    assert.equal(Buffer.from(await pdf.arrayBuffer()).subarray(0, 5).toString(), '%PDF-')
    const evaluation = await fetch('http://127.0.0.1:8017/api/dashboard/evaluations').then(response => response.json())
    assert.deepEqual(evaluation.patches, [])
    console.log('Chrome passed architecture, review, authenticated import, publication confirmation, PR status, and report.')
  }


  assert.equal(exceptions.length, 0, exceptions[0]?.exception?.description)
  console.log('Chrome passed ' + (mocked ? 'mocked' : 'live Groq') + ' proposal, stale-session recovery, nested explorer, search, drag resize, approval, restored editor, Ctrl+Enter, and export.')
  console.log('Network sequence:', network.filter(([,path]) => path.endsWith('/messages') || path.endsWith('/agent/sessions')).map(([status]) => status).join(', '))
} catch (error) {
  console.error(error.message)
  if (diagnostic) console.error(await diagnostic().catch(() => 'Page unavailable'))
  throw error
} finally {
  socket?.close()
  chrome.kill()
}

