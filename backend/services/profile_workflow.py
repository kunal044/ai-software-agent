"""Profile controlled workflows using an isolated, synthetic two-file archive."""

from io import BytesIO
import json
import os
from pathlib import Path
import platform
from tempfile import TemporaryDirectory
from time import perf_counter
import tracemalloc
from unittest.mock import patch
from zipfile import ZipFile

from services import api, intelligence, patch_evaluation, pdf_report, repository, store


def profile(output=None):
    archive = BytesIO()
    with ZipFile(archive, "w") as bundle:
        bundle.writestr("sample.py", patch_evaluation.SOURCE)
        bundle.writestr("control.py", patch_evaluation.CONTROL)
    archive.seek(0)
    timings = {}

    def measure(label, action, *args):
        start = perf_counter()
        value = action(*args)
        timings[label] = round((perf_counter() - start) * 1000, 2)
        return value

    def structured(schema, **_):
        from types import SimpleNamespace

        return SimpleNamespace(usage=None), schema.model_validate(
            patch_evaluation.MOCK_RESPONSE
        )

    tracemalloc.start()
    try:
        with TemporaryDirectory(prefix="workflow-profile-") as directory:
            with (
                patch.object(store, "WORKSPACE", Path(directory)),
                patch.object(patch_evaluation.patches, "structured", structured),
            ):
                repo = measure(
                    "zip_import",
                    api.add_project,
                    "Workflow profile",
                    "zip",
                    lambda root: repository.extract_zip(archive, root),
                )
                repo_id = repo["id"]
                before = measure("analysis", api.run_analysis, repo_id)
                measure("indexing", intelligence.index, api.root_for(repo_id))
                target = next(f for f in before["findings"] if f["rule"] == "F401")
                proposal = api.generate(target["id"])
                accepted = measure(
                    "patch_approval_and_verification",
                    api.decide,
                    proposal["id"],
                    "accept",
                )
                after = api.latest_analysis(repo_id)
                measure(
                    "markdown_report",
                    intelligence.report,
                    repo,
                    after,
                    [accepted],
                    None,
                )
                pdf = measure(
                    "pdf_report", pdf_report.render, repo, after, [accepted], None, []
                )
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    if output:
        output.write_bytes(pdf)
    return dict(
        platform=platform.platform(),
        python=platform.python_version(),
        logical_cpus=os.cpu_count(),
        fixture_files=repo["file_count"],
        fixture_bytes=repo["size_bytes"],
        archive_bytes=len(archive.getvalue()),
        elapsed_ms=timings,
        peak_traced_python_bytes=peak,
        repetitions=1,
    )


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf", type=Path)
    options = parser.parse_args()
    print(json.dumps(profile(options.pdf), indent=2))
