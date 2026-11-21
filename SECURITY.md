# Security

Run FastAPI on loopback with a single worker. The application assumes a trusted local user, filters browser origins and hosts, and has no account authentication. Public hosting requires a separate deployment security review.

## Boundaries

- ZIP and GitHub imports use bounded working copies. Path validation rejects traversal, Windows device names, and bidirectional control characters. ZIP extraction checks size, entry count, duplicate paths, links and special entries. Preview uses bounded UTF-8 reads.
- The request guard caps regular POST bodies at 2 MiB. Archive uploads require Content-Length and have a separate allowance for multipart overhead.
- Approval checks editable paths and source hashes, validates syntax where available, writes reviewed changes, and records the comparison. A journal supports recovery on restart after a partial write.
- Model prompts treat repository excerpts as untrusted data. Response schemas, path validation, exact source snapshots, and human review control effects. Recognizable secrets are screened from supported source flows. Review output and diffs before approving.
- GitHub publication requires user confirmation for an accepted patch. It uses a dedicated branch and requires the configured token's repository permissions. The PDF report uses persisted aggregate findings and omits source snippets, diffs, and diagnostic messages.

## Limitations

Static tools cover selected diagnostics and have execution and input bounds. Ruff runs from the trusted tools directory with Python isolated mode and a restricted subprocess environment. Imported application code and tests are never run. Parser vulnerabilities, prompt injection, and credential patterns outside the built-in heuristics remain risks. A trusted local process with access to the same workspace can modify files between checks. A machine or filesystem failure may prevent immediate recovery. Preserve a backup of important working copies.

Report suspected vulnerabilities privately to the repository maintainer. Include the affected version and a description without credentials or private repository content.
