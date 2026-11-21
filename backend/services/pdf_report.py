"""Generate a summary PDF from persisted evaluation data."""

from collections import Counter
from io import BytesIO
import os
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate

from services import evaluation, repository


def display(value):
    if value is None:
        return "N/A"
    if isinstance(value, float):
        return f"{value:.1%}"
    return str(value)


def counts(values):
    return (
        ", ".join(f"{name} ({count})" for name, count in values.items())
        or "None recorded"
    )


def render(repo, scan, patches, detection, patch_runs):
    buffer = BytesIO()
    styles = getSampleStyleSheet()
    styles["Title"].textColor = colors.HexColor("#112640")
    styles["Heading2"].textColor = colors.HexColor("#205577")
    story = []
    credentials = [
        value
        for key in ("GROQ_API_KEY", "OPENAI_API_KEY", "GITHUB_TOKEN")
        if (value := os.getenv(key, "").strip())
    ]

    def add(text, style="BodyText"):
        for credential in credentials:
            text = text.replace(credential, "[redacted]")
        story.append(
            Paragraph(escape(repository.SECRET.sub("[redacted]", text)), styles[style])
        )

    add("Repository evaluation report", "Title")
    add(
        f"AI Software Engineer Agent | {repo['name'][:200]} | {repo['file_count']} stored files"
    )
    add("Static analysis", "Heading2")
    if scan:
        add(f"Scan timestamp: {scan.get('created_at') or 'N/A'}")
        add(
            f"Scanned: {scan['files_analyzed']} | Skipped: {len(scan['skipped'])} | "
            f"Analyzer unavailable: {len(scan.get('validation_unavailable', []))}"
        )
        add("Analyzed languages and file counts: " + counts(scan.get("languages", {})))
        add("Finding severities: " + counts(scan["severity"]))
        add(
            "Finding categories: "
            + counts(Counter(f["category"] for f in scan["findings"]))
        )
    else:
        add("Scan results: N/A")
    add("Detection benchmark", "Heading2")
    add(
        "Labels match the exact file, line and analyzer rule. Unsupported cases and "
        "files with unavailable analyzers are excluded from scoring."
    )
    metrics = (detection or {}).get("metrics") or {}
    add(
        f"Dataset: {(detection or {}).get('dataset', 'N/A')} | Timestamp: {(detection or {}).get('created_at', 'N/A')}"
    )
    add(
        f"Precision: {display(metrics.get('precision'))} | Recall: {display(metrics.get('recall'))} | "
        f"F1: {display(metrics.get('f1'))} | Cases evaluated: {display(metrics.get('evaluated'))}"
    )
    add("Patch verification", "Heading2")
    outcomes = evaluation.totals(patches)
    add(
        f"Repository patches: {len(patches)} | Accepted targeted patches: "
        f"{outcomes['targeted_accepted']} | Static successes: {outcomes['verified_static_successes']}"
    )
    add("Verification outcomes: " + counts(outcomes["verification_outcomes"]))
    for patch in patches:
        if comparison := patch.get("comparison"):
            before = sum(comparison["before_severity"].values())
            after = sum(comparison["after_severity"].values())
            add(
                f"Findings before and after: {before} to {after} | New: {len(comparison['introduced'])}"
            )
            break
    add("AI patch benchmark", "Heading2")
    for mode in ("mocked", "offline replay", "live Groq"):
        record = next((run for run in patch_runs if run.get("mode") == mode), None)
        if record is None:
            add(f"{mode}: N/A")
            continue
        rates = record["metrics"]
        add(
            f"{mode} | {record['dataset']} | {record['created_at']} | Model: {record.get('model') or 'N/A'}",
            "Heading3",
        )
        add(
            f"Generation: {display(rates['generation_rate'])} ({rates['attempted']} attempted) | "
            f"Syntax: {display(rates['syntax_rate'])} ({rates['syntax_attempted']} checked) | "
            f"Static: {display(rates['static_rate'])} ({rates['static_checked']} compared)"
        )
    add("Security and interpretation", "Heading2")
    add(
        "Local single-user workspace. Import limits, patch path validation and human approval apply."
    )
    add(
        "Runtime tests: NOT RUN. Static analysis covers selected diagnostics and secret screening is heuristic."
    )
    add(
        "Review the diff and run trusted project tests separately before publishing a change."
    )
    document = SimpleDocTemplate(
        buffer, pagesize=A4, title="Repository evaluation report"
    )
    document.build(story)
    return buffer.getvalue()
