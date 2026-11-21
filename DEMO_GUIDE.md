# Five-minute demo

Before the demo, start the backend and frontend. Prepare a ZIP with a `sample.py` file containing `import os` followed by a small function that never uses it. Run the detection benchmark ahead of time so Analytics can load a persisted result. Configure Groq only if a live request is planned.

| Time | Show |
| --- | --- |
| 0:00–0:45 | Import the ZIP, open the file explorer, and inspect the Python source. |
| 0:45–1:30 | Run **Find Bugs** and open the Ruff F401 finding. Explain what the static rule checks. |
| 1:30–2:30 | Generate a fix, inspect the diff, and approve it. Open the before/after verification. |
| 2:30–3:30 | Show Analytics. Point out sample sizes, supported cases, and the distinctions among mocked, offline replay, and live runs. |
| 3:30–4:15 | Download Markdown and PDF reports. Show the stored scan, patch outcome, and runtime status. |
| 4:15–5:00 | Explain the GitHub preview and confirmation workflow. Use a disposable connected repository if demonstrating preview, then cancel at confirmation. |

Without Groq, run the mocked patch benchmark from [EVALUATION.md](EVALUATION.md) ahead of the demo and show its persisted outcome. Use tracked application screenshots or capture fresh images of import, findings, diff review, and Analytics with synthetic data.
