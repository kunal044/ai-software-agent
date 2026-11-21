# Evaluation

## Static detection

Run **Analytics → Run benchmark** to persist a result in the local SQLite database. From `backend`, run `./.venv/Scripts/python.exe -m services.evaluation` for JSON on standard output. The CLI leaves the database alone. Install frontend dependencies, a JDK and G++ for full coverage. The Java check uses the JDK parser and can use `JAVA_HOME`.

[source-diagnostics-v2](backend/evaluation/cases.json) contains 100 labeled fixture files, 20 per language. Sources are materialized in temporary directories, scanned through the application's existing analyzers, and discarded. The manifest records the source, expected rule and line when applicable, analyzer coverage, and explanation. The original v1 fixtures remain for historical reference.

| Analyzer | Coverage exercised |
| --- | --- |
| Python compiler and Ruff | Syntax, undefined names, unused imports and variables, maintainability, selected security diagnostics |
| TypeScript parser and ESLint | JavaScript syntax, unused bindings, constant conditions, unreachable code, duplicate keys |
| TypeScript parser and local type checks | TypeScript syntax, local type mismatches and calls |
| JDK parser | Java parse diagnostics |
| G++ | C++ parse and local type checks and selected warnings |

A finding matches one label at the exact relative path, line, and analyzer rule. There is zero line tolerance and no cross-rule equivalence. Additional detections on the same tuple count as unmatched. Breakdown groups come from fixture labels. An extra diagnostic is a benchmark false positive even if it identifies a real issue outside that case's expected label. Unsupported cases and unavailable analyzers do not enter the scoring denominator. The response lists missing supported cases separately. True negatives count clean, successfully analyzed fixture files with zero detections.

| Metric | Calculation |
| --- | --- |
| Precision | Matching findings / scored detections |
| Recall | Matching findings / scored positive labels |
| F1 | Twice the matching findings / sum of scored detections and positive labels |

Zero denominators are reported as N/A. Runtime and specification-dependent behavior are outside the static benchmark.

### Recorded baseline

Windows, Python 3.11.9, Node 24.14.0, JDK and G++, November 22, 2026 (India time). The 100-case run evaluated 76 supported cases and excluded 24 unsupported cases. All supported cases were available. It took 72.97 s.

| Language | Cases scored | TP | FP | FN | TN | Precision / recall / F1 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Python | 18 | 14 | 2 | 2 | 2 | 87.50% |
| JavaScript | 14 | 10 | 0 | 0 | 4 | 100% |
| TypeScript | 16 | 12 | 0 | 0 | 4 | 100% |
| Java | 12 | 8 | 0 | 0 | 4 | 100% |
| C++ | 16 | 12 | 0 | 0 | 4 | 100% |
| Total | 76 | 56 | 2 | 2 | 18 | 96.55% |

Ruff did not return the two labeled E711 findings. It returned two additional E731 diagnostics on late-binding fixtures. These disagreements remain visible in the benchmark. The fixture scores describe selected diagnostics on small examples.

## Patch evaluation

From `backend`, run `./.venv/Scripts/python.exe -m services.patch_evaluation` for a mocked run. It creates a disposable two-file repository, generates a patch for an F401 finding, reviews the targeted file and unrelated function, and uses the existing approval and static comparison path. The unchanged control file is checked after approval. Outcomes are printed as JSON and persisted as a summary in the configured workspace. No imported code runs.

- **Mocked** uses a hand-authored response. The recorded baseline had one attempt, one valid proposal, one syntax pass, one static success, and zero new findings.
- **Offline replay** accepts `--mode replay --replay path/to/response.json`. The JSON contains `dataset` set to `targeted-python-v1`, a required `provenance` string, optional `model`, and a `response` object containing `original_block`, `replacement_block`, `explanation`, and `rationale`. Use saved responses without credentials.
- **Live Groq** requires local Groq settings and `--mode live`. The runner shows the configured model and estimated one request. Type `RUN` to authorize that request. The provider client disables retries.

Generation uses all attempts. Syntax uses proposals where a syntax outcome was recorded, including unavailable validation. Static success and newly introduced finding rate use approved proposals that reached static comparison. The new-finding rate counts comparisons with at least one introduced finding. Runtime success is N/A. The benchmark has one targeted Python case per run and cannot estimate general model performance.

## Performance reproduction

Run `./.venv/Scripts/python.exe -m services.profile_workflow` from `backend`. The optional `--pdf` path saves a PDF generated from the disposable fixture. Timings cover ZIP import, static scan, indexing, patch approval and comparison, and both reports. The profiler uses a mocked model response and reports Python allocations from `tracemalloc`. Compiler subprocess memory and total process RSS are outside that measurement.

The observed profile ran on Windows with Python 3.11.9, 8 logical CPUs and approximately 7.65 GiB of reported physical memory. The 279-byte archive contained two Python files totaling 67 source bytes. One run measured 49.97 ms for ZIP import, 599.77 ms for static analysis, 4.90 ms for indexing, 1,324.36 ms for approval and verification, 1.84 ms for Markdown generation, and 68.16 ms for PDF generation. Peak traced Python allocations were 556,382 bytes. These are single-run measurements on the small fixture.
