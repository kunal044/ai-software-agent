# AI Software Engineer Agent

Code Review is a local workspace for inspecting imported source, running bounded multi-language static analysis, and reviewing AI-proposed remediations before applying them. It addresses the gap between a linter diagnostic and a change someone can inspect, approve, and measure.

## What you can do

- Import a public GitHub repository, a ZIP archive, or a browser-selected folder into an isolated working copy.
- Browse nested files and open UTF-8 previews for Python, JavaScript, TypeScript, Java, C/C++, JSON, YAML, Markdown, and several other text formats.
- Run language-specific syntax and static checks without executing imported code.
- Open the repository workspace to launch bug, code-quality, and security views or to export the working copy.
- Ask the configured AI provider to explain a file, Python function, or highlighted range. Follow verified citation links back to the source.
- Ask repository-wide questions using bounded keyword, file-path, and Python-symbol retrieval.
- Propose a source improvement for readability, complexity, maintainability, performance, or error handling. Review the expected benefit and risks before approval.
- Request an AI explanation and single-file patch. Inspect the Monaco side-by-side view or unified diff before accepting or rejecting.
- Compare resolved, remaining, and introduced findings after a syntax-validated fix. Export the working copy and review measured scan and patch outcomes.

## Architecture

~~~mermaid
flowchart LR
    UI["React · Vite · Tailwind · Monaco"] -->|Axios| API["FastAPI"]
    API --> Import["Git / ZIP / folder import"]
    Import --> Work["Isolated working copy"]
    API --> Static["Language compilers, Ruff and ESLint"]
    Work --> Static
    API --> Review["Patch review"]
    Review -->|"Bounded source excerpt"| Provider["GroqCloud or OpenAI"]
    Review -->|"User approval"| Work
    Work --> Export["ZIP export"]
    Static --> DB["SQLite"]
    Review --> DB
    DB --> API
~~~

Run one FastAPI worker for this single-user MVP. Mutations are serialized in-process. SQLite persists repositories, scans, findings, and patches. The working copy is the source for previews, scans, and exports. Accepted patches preserve the exact original file in SQLite. If applying or rescanning throws an exception, the service restores the original file. A recovery marker reconciles interrupted writes on restart.

[Architecture and data flow](docs/architecture.md)

## Workflow details

~~~mermaid
flowchart TD
    Import[GitHub, ZIP or folder] --> Bounds[Check path and size limits]
    Bounds --> Working[Local working copy]
    Working --> Scan[Language diagnostics]
    Scan --> SQLite[(SQLite)]
    Scan --> Finding[Selected finding]
    Finding --> Model[Bounded prompt and structured AI response]
    Model --> Diff[Review patch diff]
    Diff -->|Reject| SQLite
    Diff -->|Approve| Check[Check path, hash and syntax]
    Check --> Apply[Journal and apply]
    Apply --> Compare[Rescan and compare findings]
    Compare --> SQLite
    Compare --> Preview[Preview accepted changes against GitHub]
    Preview --> Confirm{Confirm publication}
    Confirm -->|Confirm| Branch[Feature branch and pull request]
    Confirm -->|Cancel| Working
~~~

The API uses FastAPI and SQLite. The React client uses Vite, Tailwind, and Monaco. Server-side reporting uses ReportLab. See the [demo guide](DEMO_GUIDE.md), [security model](SECURITY.md), and [contribution guide](CONTRIBUTING.md).

## Run on Windows

Requires Python 3.11, Git, and Node.js. Install a JDK and G++ for Java and C/C++ diagnostics. Run the backend from PowerShell.

~~~powershell
git clone https://github.com/kunal044/ai-software-agent.git
cd ai-software-agent\backend
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn main:app --reload --port 8000
~~~

In a second terminal opened at the cloned repository root:

~~~powershell
cd frontend
npm ci
npm run dev
~~~

Skip npm ci when node_modules already matches the lockfile. Open http://localhost:5173. FastAPI serves health at http://127.0.0.1:8000/api/health and Swagger at http://127.0.0.1:8000/docs. For a different backend port, set VITE_API_URL in frontend/.env and restart Vite. The backend accepts browser requests from the two loopback origins on port 5173.

### AI provider configuration

Keep backend/.env if it already exists. Set these values in that file, using the key issued by GroqCloud:

~~~dotenv
AI_PROVIDER=groq
GROQ_API_KEY=
GROQ_MODEL=llama-3.3-70b-versatile
~~~

Groq is selected by default. Restart FastAPI after editing .env and open Settings to check the active provider, model, and whether a key is set. A configured key can still be rejected by the provider. Import, analysis, and export work without AI credentials.

Groq calls use JSON Object Mode, with application-side validation against the same Pydantic schemas used for OpenAI. Groq lists llama-3.3-70b-versatile as enterprise-only after its free/developer shutdown. If your Groq project cannot access it, set GROQ_MODEL to an enabled JSON-capable model. Groq recommends openai/gpt-oss-120b as the replacement. That model ID still uses GroqCloud when AI_PROVIDER=groq. See [Groq model availability](https://console.groq.com/docs/changelog) and [JSON Object Mode](https://console.groq.com/docs/structured-outputs#json-object-mode).

For optional OpenAI use, explicitly set AI_PROVIDER=openai, OPENAI_API_KEY, and a structured-output-capable OPENAI_MODEL. Each provider uses its own key. The app does not fall back to another provider on failure.

## Demonstration

1. Open **Import repository**. Try the public GitHub URL https://github.com/octocat/Hello-World to see imported files. This repository demonstrates cloning and browsing.
2. For analysis, make a small folder containing a UTF-8 Python file with an unused import and a print statement. Upload the folder directly or ZIP it and choose ZIP archive.
3. Open the project, preview the Python file, and open the repository workspace, and click **Find Bugs**. Open the Ruff F401 finding.
4. With an AI provider configured, generate a fix. Review the root cause and both sides of the diff. Reject it to leave the file untouched, or accept it to apply the patch and run static comparison.
5. Open **Before / after**, confirm the reported outcome and verification status, then download the working copy from **Explorer**. Open **Analytics** for persisted totals.
6. Try **Explain Code** with a function or highlighted line range and open one of the returned source references. In **Ask AI About Repository**, ask where a function or module is implemented.
7. Choose **Improve Code**, select supported source and a goal, and review the proposed change, expected benefit, and risks. The patch uses the same accept, reject, and comparison workflow.

## Conversational coding agent

Open an imported repository and choose **AI Repository Assistant**. Start by asking the assistant to explain ex.py, then enter **Modify ex.py to print Hello world five times.** Open the proposal, inspect the diff, and choose **Accept Changes** or **Reject Changes**. A follow-up such as **Make it ten times instead** can revise a pending proposal. A revised proposal supersedes the earlier version. Typing **Accept** in chat directs you to the review controls.

The assistant opens a resizable explorer, chat, and Monaco editor on desktop. Code Explanation shares the same layout system and displays explanations alongside the selected source. Both tools render Markdown and highlight fenced code. Search by file name or path, use tabs to switch files, and send with the button or Ctrl+Enter. The browser remembers open tabs and expanded folders for each repository.

If the assistant reports missing agent routes, restart FastAPI from this project's backend folder. Its OpenAPI document at http://127.0.0.1:8000/openapi.json should include the agent session and message routes. Check the frontend's VITE_API_URL if it is connecting to a different server. A missing saved session is recovered when you send a message. Failed submissions retain the draft and offer a retry.

Select a file in the explorer or give its exact repository-relative path when names conflict. The agent can propose edits, refactors, fixes, file creation, and explicit deletion across the supported languages of up to four files per proposal. Use **Delete old.py** or **Delete old.py and legacy.py** for deletion. Ordinary questions can use other supported text formats. **Find bugs in this repository** runs the existing static analyzer without any model call. Session history is stored per repository in SQLite. The latest 30 turns are retained per session, with four recent user turns supplied as model context.

Approval checks all original snapshots, journals the pending operation, and applies the reviewed files. If any write or static scan fails, the transaction restores previous files and removes newly created empty directories. Startup recovery also handles interrupted writes. Accepted proposals appear in patch history and in subsequent ZIP exports. Imported code is never executed.

## API

JSON is used except the multipart upload and ZIP download. IDs are UUID strings. A finding contains path, line, rule, message, evidence, severity, and category.

| Method and path | Contract |
| --- | --- |
| GET /api/health | Backend status |
| POST /api/repos/import | JSON with a public GitHub URL under url |
| POST /api/repos/upload | Multipart archive, or repeated files and a paths JSON array of matching relative paths |
| GET /api/repos | Repository metadata list |
| GET /api/repos/{id} | Name, source type, file count, and imported size |
| GET /api/repos/{id}/files | Flat tree entries with relative paths, type, and file size and extension |
| GET /api/repos/{id}/file?path=… | Source content and editor language |
| GET /api/repos/{id}/source-context?path=... | Source preview, content hash, and Python function boundaries |
| POST /api/repos/{id}/explain | Explain the selected file or inclusive lines, with validated source references |
| POST /api/repos/{id}/assistant | Answer a repository question using retrieved excerpts and up to four prior user questions |
| GET /api/repos/{id}/agent/sessions | List recent session IDs and titles |
| POST /api/repos/{id}/agent/sessions | Start a conversation |
| GET /api/repos/{id}/agent/sessions/{session_id} | Retrieve bounded conversation history |
| POST /api/repos/{id}/agent/sessions/{session_id}/messages | Route an instruction and return an answer, analysis, clarification, or proposal ID |
| POST /api/repos/{id}/improvements | Generate a reviewable source patch for an explicit goal and source hash |
| POST /api/repos/{id}/analyze | Run and persist a multi-language static scan |
| GET /api/repos/{id}/analysis | Latest scan or null |
| GET /api/findings/{id} | Finding detail |
| POST /api/findings/{id}/fix | Generate and persist a patch proposal |
| GET /api/repos/{id}/patches | Patch summaries for a repository |
| GET /api/patches/{id} | Original and proposed source, diff, explanation, status, and comparison when accepted |
| POST /api/patches/{id}/accept | Run applicable validation, apply the patch, and rescan |
| POST /api/patches/{id}/reject | Record rejection |
| GET /api/repos/{id}/export | Download ZIP |
| GET /api/dashboard | Current findings plus historical scan and patch metrics |
| POST /api/dashboard/benchmark | Compare expected and detected arrays of labeled finding IDs |
| GET /api/settings | Active provider, model, and configuration flags without the key |

## Language support

| Language | Isolated diagnostics |
| --- | --- |
| Python | Python syntax checks and Ruff, including the existing security rules |
| JavaScript | Parser diagnostics and ESLint with the app's fixed rule set |
| TypeScript | Compiler syntax diagnostics and selected single-file type checks |
| Java | JDK compiler parse diagnostics when a JDK is found |
| C and C++ | GCC or G++ syntax-only diagnostics when installed and the source can be checked in isolation |
| JSON | Strict JSON parsing |

Each language above supports explanations, agent edits, reviewed creation and deletion, and improvement proposals. A bug-fix proposal can start from a reported finding or from a request in chat. Cross-file renames use a bounded identifier search and propose at most four files. Check the diffs for shadowed names or references that depend on the wider project.

Install frontend dependencies with npm ci to make the JavaScript and TypeScript tools available to the backend. It runs its own fixed ESLint configuration and a TypeScript compiler with import resolution disabled. Java does not run annotation processors. C and C++ validation allows only designated standard headers and otherwise reports that validation is unavailable. A plain .h file without clear C or C++ markers is previewed as a header and reported as unvalidated. Missing tools and exhausted scan budgets also appear as unavailable. These checks do not run project builds or tests.

## Evaluation

Open **Patch verification** after approving a patch. The report keeps the original scan, remaining and resolved findings, new findings, changed file operations, syntax results, and the static coverage status. The comparison matches findings by path, rule, message, and evidence, tolerating line shifts. Changes in evidence can appear as a resolved finding and a new finding. A target with no matching baseline or missing coverage is inconclusive. Imported code and its tests remain **NOT RUN**.

**Static verification success rate** is the number of accepted targeted patches with a resolved finding, passing applicable syntax checks, complete static comparison coverage, and zero newly introduced findings, divided by all accepted targeted patches. Incomplete and legacy targeted approvals are included in the denominator and receive no success credit. Untargeted changes are excluded from that rate and appear in verification outcomes. Syntax-valid counts patches with a stored PASSED syntax result. A zero denominator produces an unavailable rate.

**Detection benchmark.** The bundled suite covers 100 labeled cases across Python, JavaScript, TypeScript, Java, and C++. The recorded Windows run scored 76 supported cases and yielded 56 true positives, 2 unmatched detections, 2 missed labels, and 18 clean negatives. Precision, recall, and F1 were each 96.55%. The other 24 cases represent unsupported coverage. The Analytics page stores static runs and shows separate histories for mocked, offline replay, and live Groq patch evaluation.

See [EVALUATION.md](EVALUATION.md) for reproduction, case coverage, rate denominators, and the live Groq confirmation step.

Analytics uses the latest scan from each repository for severity and language distributions. Its trend view shows the most recent 25 stored scans, including before and after scans produced during approval. Historical rows without a timestamp remain undated.

~~~powershell
cd D:\AI-Software-Engineer-Agent\backend
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m ruff check main.py services tests
cd ..\frontend
npm run lint
npm run build
~~~

For the live Chrome workspace regression test, start an isolated backend and frontend in separate PowerShell terminals. The test imports a disposable fixture and makes one live Groq generation request.

~~~powershell
# From the project root, run in the first terminal
$env:ENGINEER_WORKSPACE = Join-Path (Get-Location) 'backend/workspace/ide-verification/data'
cd backend
.\.venv\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port 8017
~~~

~~~powershell
# From the project root, run in the second terminal
$env:VITE_API_URL = 'http://127.0.0.1:8017'
cd frontend
npm.cmd run dev -- --host 127.0.0.1 --port 5173 --strictPort
~~~

Run node backend/tests/ide_smoke.mjs from the project root with Chrome installed. Use the matching servers above so the test uses its isolated repository database.

For a mocked browser regression, set the first terminal's PYTHONPATH to its backend directory and replace its Uvicorn command with .\.venv\Scripts\python.exe tests/ide_mock_server.py and set IDE_SMOKE_MOCK=1 before running the Node test. This also checks a first visit without saved conversation state, patch rejection, provider error messages, and the Code Explanation workflow.

## GitHub and repository intelligence

Set GITHUB_TOKEN to a fine-grained personal access token in backend/.env, restart FastAPI, then select **Connect GitHub** in Settings. Limit repository access to the repositories you want to use. Authenticated import needs Contents read access. Publication needs Contents write and Pull requests write. Token revocation and rotation happen in GitHub. Disconnecting clears the application's connection record and keeps local imports and publication history. [GitHub token permissions](https://docs.github.com/en/rest/authentication/permissions-required-for-fine-grained-personal-access-tokens)

- Use **Connected GitHub** on the import page to browse accessible repositories or enter an eligible repository URL. Authenticated import is capped at 250 stored files and 100 MiB. Symlinks and submodules are excluded.
- **Architecture** shows bounded source indexing, candidate entry points, declarations, and direct dependencies. Language coverage varies. The Groq interpretation is labeled separately.
- **Code Review** accepts up to four current source files or one pending or accepted change set, with a diff selector for multi-file patches. AI suggestions cite the reviewed source version. Static findings come from the latest stored scan.
- **Pull requests** previews one accepted change set against the GitHub default branch. Publication requires explicit confirmation and creates a dedicated code-review feature branch. If GitHub rejects PR creation after the branch has been created, retry from the workspace after resolving the error. The operation never updates the default branch directly.
- **Download report** and **Download PDF** export persisted scan, patch, verification, and benchmark records. Markdown includes the architecture snapshot when available. The report distinguishes static verification from runtime testing.

New API routes (under /api):

| Method | Path |
| --- | --- |
| GET | /github/status |
| POST | /github/connect, /github/disconnect |
| GET | /github/repositories |
| POST | /github/import |
| GET | /repos/{repo_id}/github/preview |
| GET, POST | /repos/{repo_id}/github/pull-requests |
| GET | /repos/{repo_id}/github/pull-requests/{patch_id} |
| GET | /repos/{repo_id}/architecture |
| POST | /repos/{repo_id}/architecture/summary |
| POST | /repos/{repo_id}/review |
| GET | /repos/{repo_id}/report, /repos/{repo_id}/report.pdf |
| GET | /dashboard/evaluations |

Set IDE_SMOKE_PHASE3=1 on both the mock backend and the mocked Chrome smoke command to cover architecture, code review, authenticated import, and pull request confirmation and status.

## Security and limits

- Keep the API on loopback. The MVP assumes a trusted local user and has no account authentication.
- Imports have a 100 MiB project limit and 4,000-entry limit. Git uses a shallow HTTPS clone with no redirects, interactive prompts, repository checkout, hooks, or submodules. The clone has a time limit and monitored disk usage. Peak temporary disk use can exceed the final project size.
- Paths, links, special files, encrypted ZIP entries, and excessive expansion are checked before materialization. Generated and common sensitive paths are excluded. Recognizable credential patterns are also blocked from previews and matched in small UTF-8 files during export. A hardcoded secret can evade these heuristics.
- AI requests include a selected excerpt with limited neighboring lines or up to five retrieved repository excerpts. Retrieval considers supported text files up to 64 KiB each, reads up to 6 MiB, and skips files that match secret heuristics. Answers use references resolved against the retrieved path and line metadata. Provider failures and missing configuration surface as errors.
- Improvement proposals are limited to supported source and a selected range of up to 12,000 characters. The service rejects new imports and changes that increase calls in its sensitive-capability checks. Human review is required for semantic changes and behavior. Chat turns stay in the current view and the backend receives at most four prior user questions.
- Analysis covers supported UTF-8 files up to 256 KiB. Other preview formats can still be explained. Scans run serially per service instance, and unusually large projects can hit the analysis time limit.
- SQLite and workspace contents inherit local filesystem permissions. The application does not encrypt source copies. Exports require review before external sharing.

## Screenshots

These screenshots show a local browser run against a real ZIP import and Ruff scan. The AI responses in the browser run came from a test double.

- [Dashboard](docs/screenshots/dashboard.png)
- [Repository Workspace](docs/screenshots/workspace.png)
- [Repository Explorer](docs/screenshots/explorer.png)
- [Analysis Results](docs/screenshots/findings.png)
- [Side-by-side Patch Review](docs/screenshots/patch-review.png)
- [Code Explanation](docs/screenshots/explanation.png)
- [Repository Assistant](docs/screenshots/assistant.png)

## Future work

Future work includes larger held-out repositories, more targeted patch cases, and opt-in test execution inside a disposable sandbox with network and resource limits.

