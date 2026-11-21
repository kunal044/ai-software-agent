"""Run targeted fixes on disposable source. Persist summary records only."""

import argparse
from contextlib import nullcontext
from datetime import datetime, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from dotenv import load_dotenv
from fastapi import HTTPException
from pydantic import ValidationError

from services import api, patches, store, validation

DATASET = "targeted-python-v1"
SOURCE = "import os\n\n\ndef square(value):\n    return value * value\n"
CONTROL = "value = 42\n"
MOCK_RESPONSE = {
    "original_block": "import os\n",
    "replacement_block": "",
    "explanation": "The imported module is unused.",
    "rationale": "Removing the import leaves square unchanged.",
}


def fixture(root):
    (root / "sample.py").write_bytes(SOURCE.encode())
    (root / "control.py").write_bytes(CONTROL.encode())


def attempt():
    repo = api.add_project(DATASET, "evaluation", fixture)
    repo_id = repo["id"]
    root = api.root_for(repo_id)
    scan = api.run_analysis(repo_id)
    finding = next(item for item in scan["findings"] if item["rule"] == "F401")
    result = dict(runtime="NOT RUN", proposal="invalid", syntax="NOT RUN")
    try:
        proposal = api.generate(finding["id"])
    except ValidationError:
        return result
    except HTTPException as error:
        if (
            error.status_code in {429, 502, 503, 504}
            and "invalid or incomplete" not in error.detail
        ):
            result["proposal"] = "API failure"
        return result
    result["proposal"] = "valid"
    result["target_file"] = proposal["path"] == finding["path"]
    # The benchmark contract requires keeping the public function byte-for-byte.
    result["preserved_unrelated"] = proposal["proposed"].endswith(
        SOURCE.split("\n", 1)[1]
    )
    if not result["target_file"] or not result["preserved_unrelated"]:
        result["decision"] = api.decide(proposal["id"], "reject")["status"]
        return result
    try:
        accepted = api.decide(proposal["id"], "accept")
    except HTTPException as error:
        if error.status_code not in {404, 409, 415, 422}:
            raise
        record = api.get_patch(proposal["id"])
        result["syntax"] = record["syntax"]
        result["decision"] = api.decide(proposal["id"], "reject")["status"]
        return result
    result.update(
        decision=accepted["status"],
        syntax=accepted["syntax"],
        target=accepted["verification"]["target"],
        verification=accepted["verification"]["status"],
        introduced=len(accepted["comparison"]["introduced"]),
        preserved_unrelated=(root / "control.py").read_bytes() == CONTROL.encode(),
    )
    return result


def run(mode, response=None, provenance=None, model=None):
    def provider(schema, **_):
        return SimpleNamespace(usage=None), schema.model_validate(response, strict=True)

    override = (
        nullcontext()
        if mode == "live Groq"
        else patch.object(patches, "structured", provider)
    )
    with TemporaryDirectory(prefix="patch-benchmark-") as workspace:
        with patch.object(store, "WORKSPACE", Path(workspace)), override:
            result = attempt()
    valid = int(result["proposal"] == "valid")
    syntax_attempted = int(result["syntax"] != "NOT RUN")
    static_checked = int("verification" in result)
    metrics = dict(
        attempted=1,
        valid=valid,
        invalid=int(result["proposal"] == "invalid"),
        api_failures=int(result["proposal"] == "API failure"),
        accepted=int(result.get("decision") == "accepted"),
        rejected=int(result.get("decision") == "rejected"),
        syntax_attempted=syntax_attempted,
        syntax_passed=int(result["syntax"] == validation.PASSED),
        resolved=int(result.get("target") == "resolved"),
        remaining=int(result.get("target") == "remaining"),
        introduced=result.get("introduced"),
        unverifiable=int(result.get("verification") in {None, "incomplete"}),
        generation_rate=float(valid),
        syntax_rate=float(result["syntax"] == validation.PASSED)
        if syntax_attempted
        else None,
        static_rate=float(result["verification"] == "static checks passed")
        if static_checked
        else None,
        introduced_rate=float(result["introduced"] > 0) if static_checked else None,
        static_checked=static_checked,
        runtime_rate=None,
    )
    return dict(
        id=str(uuid4()),
        repo_id="bundled",
        created_at=datetime.now(timezone.utc).isoformat(),
        dataset=DATASET,
        mode=mode,
        provenance=provenance,
        model=model,
        case=result,
        metrics=metrics,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("mock", "replay", "live"), default="mock")
    parser.add_argument("--replay", type=Path)
    args = parser.parse_args()
    if args.mode == "live":
        load_dotenv(Path(__file__).resolve().parents[1] / ".env", encoding="utf-8-sig")
        active = patches.config()
        if active.provider != "groq" or active.warning:
            parser.error("Configure Groq before starting a live benchmark.")
        print(f"Estimated Groq requests: 1. Model: {active.model}.")
        if input("Type RUN to authorize this request: ") != "RUN":
            return
        result = run("live Groq", provenance="Direct Groq request", model=active.model)
    elif args.mode == "replay":
        if not args.replay:
            parser.error("Provide --replay with a saved response JSON file.")
        with args.replay.open(encoding="utf-8") as stream:
            saved = json.load(stream)
        if saved.get("dataset") != DATASET or not saved.get("provenance"):
            parser.error("Replay requires a matching dataset and recorded provenance.")
        result = run(
            "offline replay", saved["response"], saved["provenance"], saved.get("model")
        )
    else:
        result = run("mocked", MOCK_RESPONSE, "Hand-authored fixture")
    with store.database() as conn:
        store.save(conn, "patch_benchmark_run", result)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
