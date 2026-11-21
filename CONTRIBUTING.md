# Contributing

Use Python 3.11, Git, and Node.js on Windows. Follow the [Windows setup](README.md#run-on-windows), and install npm dependencies before running the language checks. A JDK and G++ enable optional analyzers.

From `backend`:

~~~powershell
.\.venv\Scripts\python.exe -m ruff check main.py services tests
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m services.evaluation
~~~

From `frontend`:

~~~powershell
npm ci
npm run lint
npm run build
~~~

Use a disposable `ENGINEER_WORKSPACE` for manual or browser testing. [Browser regression instructions](README.md#evaluation) cover the mock servers and Chrome. Keep replay responses sanitized and include dataset version and provenance. Reports and test fixtures should contain synthetic or approved source. Describe test coverage and any remaining limitations in the pull request.
