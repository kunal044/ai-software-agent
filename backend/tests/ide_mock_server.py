import base64
import os

import uvicorn
from fastapi import HTTPException
from main import app
from services import agent, assistant, github, intelligence, patches


class Response:
    usage = None


def github_fixture(method, path, **kwargs):
    api = "/repos/fixture/repository"
    original = 'if __name__ == "__main__":\n    print("Hello world!")\n'
    original_sha = github.blob_id(original)
    if method == "GET":
        data = {
            "/user": {"login": "fixture-user"},
            "/user/repos": [{"full_name": "fixture/repository", "private": True}],
            api: {"default_branch": "main"},
            api + "/git/ref/heads/main": {"object": {"sha": "base"}},
            api + "/git/commits/base": {"tree": {"sha": "tree"}},
            api + "/git/trees/tree": {"tree": [
                {"path": "src/tools/ex.py", "mode": "100644", "type": "blob", "size": len(original.encode()), "sha": original_sha},
            ]},
            api + "/git/blobs/" + original_sha: {"content": base64.b64encode(original.encode()).decode()},
            api + "/pulls": [],
            api + "/pulls/42": {"number": 42, "state": "closed", "merged": True},
        }
        if path.startswith(api + "/git/ref/heads/code-review/"):
            raise HTTPException(404)
        return data[path]
    data = kwargs["json"]
    if path.endswith("/git/refs"):
        assert data["ref"].startswith("refs/heads/code-review/")
        return {}
    if path.endswith("/pulls"):
        assert data["head"].startswith("code-review/") and data["base"] == "main"
        return {"number": 42, "state": "open"}
    return {"sha": "written-object"}


def provider(schema, instructions, input, max_output_tokens=4000):
    if "fixture-rate-limit" in input:
        raise HTTPException(429, "Groq rate limit or quota exceeded.")
    if "fixture-no-key" in input:
        raise HTTPException(
            503, "Set GROQ_API_KEY in backend/.env and restart FastAPI."
        )
    if schema is agent.Edit:
        return Response(), schema(
            source='if __name__ == "__main__":\n    for _ in range(5):\n        print("Hello world!")\n',
            explanation="Repeat the greeting.",
            expected="Five lines.",
            risks="None expected.",
        )
    if schema is agent.Plan:
        return Response(), schema(intent="QUESTION", targets=[])
    if schema is assistant.Answer:
        return Response(), schema(
            answer="**Greeting**\n\nHere is the source:\n\n"
            + chr(96) * 3
            + 'python\nprint("Hello world!")\n'
            + chr(96) * 3,
            citations=[1],
        )
    if schema is intelligence.Summary:
        return Response(), schema(summary="Fixture has a Python entry point.")
    if schema is intelligence.Review:
        return Response(), schema(suggestions=[dict(
            excerpt=1, start=1, end=1, category="maintainability",
            observation="Review the greeting output.", recommendation="Document expected output.",
        )])
    raise AssertionError(schema)


if __name__ == "__main__":
    patches.structured = provider
    if os.getenv("IDE_SMOKE_PHASE3") == "1":
        os.environ["GITHUB_TOKEN"] = "browser-fixture-only"
        github.request = github_fixture
    uvicorn.run(app, host="127.0.0.1", port=8017)
