"""Static checks without importing or running project code."""

from collections import Counter
from datetime import datetime, timezone
import json
import subprocess
import sys
import time
from uuid import uuid4

from fastapi import HTTPException

from services import languages, validation
from services.repository import SECRET, files_in, source

SECURITY = {"S102", "S307", "S324", "S506", "S602", "S701"}


def finding(path, line, rule, message, lines, category):
    severity = {"syntax": "high", "security": "medium", "static": "low"}[category]
    evidence = lines[line - 1].strip() if 0 < line <= len(lines) else ""
    evidence = SECRET.sub("[redacted]", evidence)[:300]
    return dict(
        id=str(uuid4()),
        path=path,
        line=line,
        rule=rule,
        message=SECRET.sub("[redacted]", message),
        evidence=evidence,
        severity=severity,
        category=category,
    )


def scan(root, repo_id):
    started = time.perf_counter()
    findings, skipped = [], []
    scanned = 0
    coverage = Counter()
    unavailable = []
    external_checks = 0
    analyzed_paths = []
    language_by_path = {}
    for name, path in files_in(root):
        language = languages.detect(path)
        if not language or not language.editable:
            continue
        if time.perf_counter() - started > 150 or (
            language.validator in {"java", "c", "cpp", "javascript", "typescript"}
            and external_checks >= 50
        ):
            unavailable.append(name)
            continue
        try:
            _, code, _ = source(root, name)
        except HTTPException:
            skipped.append(name)
            continue
        lines = code.splitlines()
        language = languages.detect(path, code)
        external_checks += language.validator in {
            "java",
            "c",
            "cpp",
            "javascript",
            "typescript",
        }
        issues = validation.diagnose(code, name, lint=True)
        if issues is None:
            unavailable.append(name)
            continue
        scanned += 1
        analyzed_paths.append(name)
        coverage[language.name] += 1
        language_by_path[name] = language.name
        for item in issues:
            findings.append(
                finding(
                    name,
                    item["line"],
                    item["rule"],
                    item["message"],
                    lines,
                    item["category"],
                )
            )
        if language is not languages.PYTHON or issues:
            continue
        try:
            result = validation.run(
                [
                    sys.executable,
                    "-I",
                    "-m",
                    "ruff",
                    "check",
                    "--isolated",
                    "--no-cache",
                    "--ignore-noqa",
                    "--output-format=json",
                    "--target-version=py311",
                    "--select=E4,E7,E9,F,B,S102,S307,S324,S506,S602,S701",
                    f"--stdin-filename={name}",
                    "-",
                ],
                code,
                timeout=15,
            )
            if result.returncode not in {0, 1}:
                raise ValueError
            for issue in json.loads(result.stdout):
                rule = issue["code"]
                category = "security" if rule in SECURITY else "static"
                if rule == "invalid-syntax":
                    category = "syntax"
                findings.append(
                    finding(
                        name,
                        issue["location"]["row"],
                        rule,
                        issue["message"],
                        lines,
                        category,
                    )
                )
        except (ValueError, OSError, subprocess.SubprocessError):
            raise HTTPException(
                503, "Ruff analysis failed. Check the backend installation."
            ) from None
    unique = {(f["path"], f["line"], f["rule"], f["message"]): f for f in findings}
    findings = list(unique.values())
    for item in findings:
        path = item["path"]
        item["language"] = language_by_path[path]
    findings_by_language = Counter(item["language"] for item in findings)
    return dict(
        id=str(uuid4()),
        repo_id=repo_id,
        findings=findings,
        files_analyzed=scanned,
        analyzed_paths=analyzed_paths,
        findings_by_language=dict(findings_by_language),
        created_at=datetime.now(timezone.utc).isoformat(),
        skipped=skipped,
        validation_unavailable=unavailable,
        languages=dict(coverage),
        duration_ms=round((time.perf_counter() - started) * 1000, 1),
        severity=dict(Counter(f["severity"] for f in findings)),
        runtime="NOT RUN",
    )


def compare(before, after, target):
    def signature(item):
        return tuple(item[k] for k in ("path", "rule", "message", "evidence"))

    older = before["findings"]
    newer = after["findings"]

    def unmatched(items, other):
        remaining = Counter(map(signature, other))
        extra = []
        for item in items:
            key = signature(item)
            if remaining[key]:
                remaining[key] -= 1
            else:
                extra.append(item)
        return extra

    resolved = unmatched(older, newer)
    introduced = unmatched(newer, older)
    introduced_ids = {f["id"] for f in introduced}
    return dict(
        before_id=before["id"],
        after_id=after["id"],
        resolved=resolved,
        introduced=introduced,
        remaining=[f for f in newer if f["id"] not in introduced_ids],
        target_disappeared=None
        if target is None
        else not any(signature(f) == signature(target) for f in newer),
        before_severity=before["severity"],
        after_severity=after["severity"],
        duration_ms=after["duration_ms"],
        runtime=after["runtime"],
    )
