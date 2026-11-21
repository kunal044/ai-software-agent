"""Counters and verification for stored scans and labeled benchmarks."""

from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter
from uuid import uuid4

from services import analysis, languages

DATASET = Path(__file__).resolve().parents[1] / "evaluation"


def score(expected, detected):
    true = len(expected & detected)
    return dict(
        true_positive=true,
        false_positive=len(detected - expected),
        false_negative=len(expected - detected),
        precision=true / len(detected) if detected else None,
        recall=true / len(expected) if expected else None,
        f1=2 * true / (len(expected) + len(detected)) if expected or detected else None,
    )


def evaluate():
    manifest = json.loads((DATASET / "cases.json").read_text(encoding="utf-8"))
    cases = manifest["cases"]
    by_path = {case["path"]: case for case in cases}
    if len(by_path) != len(cases) or len({case["id"] for case in cases}) != len(cases):
        raise ValueError("Benchmark case paths and IDs must be unique.")
    measured = set()
    observed = []
    started = perf_counter()
    with TemporaryDirectory(prefix="source-benchmark-") as workspace:
        for language in sorted({case["language"] for case in cases}):
            root = Path(workspace) / language
            root.mkdir()
            for case in cases:
                if case["language"] == language:
                    (root / case["path"]).write_text(
                        case["source"] + "\n", encoding="utf-8", newline="\n"
                    )
            scan = analysis.scan(root, "bundled-benchmark")
            measured.update(scan["analyzed_paths"])
            observed.extend(scan["findings"])

    def key(item):
        return item["path"], item["line"], item["rule"]

    def summarize(subset):
        covered = [
            case for case in subset if case["supported"] and case["path"] in measured
        ]
        paths = {case["path"] for case in covered}
        expected = {
            key({"path": case["path"], **case["expected"]})
            for case in covered
            if case["expected"]
        }
        findings = [key(item) for item in observed if item["path"] in paths]
        result = score(expected, set(findings))
        duplicates = len(findings) - len(set(findings))
        result["false_positive"] += duplicates
        if findings:
            result["precision"] = result["true_positive"] / len(findings)
        if expected or findings:
            result["f1"] = 2 * result["true_positive"] / (len(expected) + len(findings))
        return dict(
            **(result if covered else dict.fromkeys(result)),
            cases=len(subset),
            evaluated=len(covered),
            supported=sum(case["supported"] for case in subset),
            unsupported=sum(not case["supported"] for case in subset),
            positive_labels=len(expected),
            detected_findings=len(findings),
            true_negative=sum(
                not case["expected"]
                and case["path"] not in {item[0] for item in findings}
                for case in covered
            ),
        )

    def break_down(field):
        return {
            label: summarize([case for case in cases if case[field] == label])
            for label in sorted({case[field] for case in cases})
        }

    public_cases = [
        {key: value for key, value in case.items() if key != "source"} for case in cases
    ]
    return dict(
        id=str(uuid4()),
        dataset=manifest["version"],
        mode="static analysis",
        created_at=datetime.now(timezone.utc).isoformat(),
        duration_ms=round((perf_counter() - started) * 1000, 1),
        metrics=summarize(cases),
        by_language=break_down("language"),
        by_category=break_down("category"),
        missing=sorted(
            case["path"]
            for case in cases
            if case["supported"] and case["path"] not in measured
        ),
        cases=public_cases,
        expected=[
            dict(path=case["path"], **case["expected"])
            for case in public_cases
            if case["expected"]
        ],
        known_unsupported=[case for case in public_cases if not case["supported"]],
        detected=[
            dict(path=path, line=line, rule=rule)
            for path, line, rule in sorted({key(item) for item in observed})
        ],
    )


def verify(before, after, target, changes, syntax):
    changed = {item["path"]: item["operation"] for item in changes}
    before_checked = set(before["analyzed_paths"])
    after_checked = set(after["analyzed_paths"])
    # Creation and deletion have a legitimate one-sided baseline.
    comparable = before_checked & after_checked
    comparable |= {
        path
        for path, op in changed.items()
        if (op == "create" and path in after_checked)
        or (op == "delete" and path in before_checked)
    }
    left = {
        **before,
        "findings": [item for item in before["findings"] if item["path"] in comparable],
    }
    right = {
        **after,
        "findings": [item for item in after["findings"] if item["path"] in comparable],
    }
    comparison = analysis.compare(left, right, target)
    comparison["before_severity"] = before["severity"]
    comparison["after_severity"] = after["severity"]
    unresolved_scope = sorted(
        (set(changed) | (before_checked ^ after_checked)) - comparable
    )
    incomplete = bool(
        unresolved_scope
        or before["skipped"]
        or after["skipped"]
        or before["validation_unavailable"]
        or after["validation_unavailable"]
    )
    outcome = "not targeted"
    if target:

        def same_location(item):
            return (
                item["path"] == target["path"]
                and item["rule"] == target["rule"]
                and item["message"] == target["message"]
            )

        baseline = [item for item in left["findings"] if same_location(item)]
        latest = [item for item in right["findings"] if same_location(item)]
        if not any(item["evidence"] == target["evidence"] for item in baseline):
            outcome = "inconclusive"
        elif any(item["evidence"] == target["evidence"] for item in latest) or len(
            latest
        ) >= len(baseline):
            outcome = "remaining"
        else:
            outcome = "resolved"
        comparison["target_disappeared"] = (
            True if outcome == "resolved" else False if outcome == "remaining" else None
        )

    if comparison["introduced"]:
        status = "regression detected"
    elif incomplete or outcome == "inconclusive" or syntax != "PASSED":
        status = "incomplete"
    elif outcome == "remaining":
        status = "target remains"
    elif outcome == "resolved":
        status = "static checks passed"
    else:
        status = "no new static findings"
    return comparison, dict(
        status=status,
        syntax=syntax,
        static_coverage="partial" if incomplete else "complete",
        target=outcome,
        runtime="NOT RUN",
        uncomparable_paths=unresolved_scope,
        changed_files=[
            dict(
                path=item["path"],
                operation=item["operation"],
                syntax="NOT APPLICABLE"
                if item["operation"] == "delete"
                else item.get("syntax", syntax),
            )
            for item in changes
        ],
    )


def totals(patches):
    accepted = [patch for patch in patches if patch["status"] == "accepted"]
    targeted = [patch for patch in accepted if patch.get("finding")]
    verified = [patch.get("verification") or {} for patch in targeted]
    successful = sum(item.get("status") == "static checks passed" for item in verified)
    resolved = sum(item.get("target") == "resolved" for item in verified)
    statuses = Counter(
        (patch.get("verification") or {}).get("status", "legacy comparison")
        for patch in accepted
    )
    return dict(
        syntax_valid=sum(patch.get("syntax") == "PASSED" for patch in patches),
        targeted_resolved=resolved,
        targeted_accepted=len(targeted),
        verified_static_successes=successful,
        verified_fix_success_rate=successful / len(targeted) if targeted else None,
        runtime_verified_fixes=sum(
            (patch.get("verification") or {}).get("runtime") == "PASSED"
            for patch in accepted
        ),
        verification_outcomes=dict(statuses),
    )


def summarize_findings(findings):
    coverage = Counter()
    for finding in findings:
        language = languages.detect(finding["path"])
        coverage[
            finding.get("language") or (language.name if language else "Other")
        ] += 1
    return dict(coverage)


if __name__ == "__main__":
    print(json.dumps(evaluate(), indent=2))
