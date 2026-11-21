# Architecture notes

## Components

| Component | Responsibility |
| --- | --- |
| backend/main.py | FastAPI entry point, loopback request guard, upload limits, startup recovery |
| backend/services/repository.py | URL and path validation, isolated import, safe source reads |
| backend/services/languages.py and validation.py | Source capabilities, Monaco identifiers, and bounded syntax validators |\n| backend/services/analysis.py | Registry-driven static checks and isolated Ruff diagnostics, normalized findings, scan comparison |
| backend/services/patches.py | Groq and OpenAI structured calls and patch records, source-hash binding, syntax validation, atomic writes |
| backend/services/assistant.py | Bounded source selection, Python and JavaScript/TypeScript function lookup, repository retrieval, verified citation metadata, and improvement validation |
| backend/services/store.py | SQLite storage and transaction lifetime |
| backend/services/api.py | Repository, scan, patch, export, metrics, and configuration routes |
| frontend/src/App.jsx | Application navigation and dashboard, import, scan, and patch views |
| frontend/src/Workspace.jsx | Repository launchpad, source selection and explanation, and chat |
| frontend/src/api.js | Axios client, shared request handling, and repository URLs |
| frontend/src/monaco.js | On-demand local Monaco editor and browser workers |

The conversational agent uses a structured plan over explicit, selected, or recent paths. It invokes existing file listing, source reading, symbol inspection, retrieval, and static analysis functions. Proposals include validated source snapshots and diffs. The explicit user approval action remains a regular patch endpoint and is never exposed as a model action.

## Storage layout

Each imported project has a UUID directory under backend/workspace, with its working files under source. The SQLite database lives beside the project directories. Git clone data is staged outside source. The service attempts to remove it after import. ZIP exports are temporary files and are removed when the response completes. Upload spooling stays in the configured workspace. ENGINEER_WORKSPACE can override the workspace location for isolated verification.

## Approval and recovery

A proposal records the selected finding or improvement goal, the original file and its hash, the proposed file, and the unified diff. Approval validates the hash and applicable language checks, records a recovery marker, atomically replaces the file, runs a new scan, and commits the outcome in SQLite. Startup reconciles a remaining marker against the persisted patch status. The marker lets the service restore the expected file after an interruption. External file edits during recovery require manual resolution. One service process owns the workspace.

## Data boundaries

Imports and static analysis use local files. Ruff runs with isolated configuration and receives source over stdin. Existing Git filters, project dependencies, hooks, and arbitrary test code are outside the execution path. The configured AI provider receives bounded excerpts for requested fixes, improvements, explanations, and repository questions. The assistant ranks candidate paths before reading source, scores Python symbols and excerpt keywords, and resolves returned citation IDs against excerpts supplied to the model. The browser receives source previews and patch data through the loopback API and renders them using local Monaco assets.

