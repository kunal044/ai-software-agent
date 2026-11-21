import io
import json
import zipfile

import pytest
from fastapi.testclient import TestClient

from main import app
from services import patch_evaluation, patches, repository, store

REAL_GROQ = patches.groq.Groq


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "WORKSPACE", tmp_path)
    return TestClient(app, base_url="http://localhost")


def archive(entries):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as output:
        for path, content in entries.items():
            output.writestr(path, content)
    return stream.getvalue()


def upload(client, entries):
    return client.post(
        "/api/repos/upload",
        files={"archive": ("example.zip", archive(entries), "application/zip")},
    )


def test_health(client):
    assert client.get("/").status_code == 200
    assert client.get("/api/health").json()["status"] == "healthy"


@pytest.mark.parametrize(
    "url",
    [
        "https://me@github.com/o/r",
        "http://github.com/o/r",
        "https://github.com.evil.invalid/o/r",
        "https://github.com/o/r?x=1",
        "https://github.com/o/..",
        "https://github.com/o/r/branch",
    ],
)
def test_rejects_invalid_github_urls(client, url):
    assert client.post("/api/repos/import", json={"url": url}).status_code == 422


def test_import_failure_cleans_workspace(client, monkeypatch):
    assert repository.github_url("https://github.com/octocat/Hello-World.git") == (
        "https://github.com/octocat/Hello-World.git",
        "Hello-World",
    )
    monkeypatch.setattr(
        repository.subprocess,
        "Popen",
        lambda *a, **k: (_ for _ in ()).throw(OSError("unavailable")),
    )
    assert (
        client.post(
            "/api/repos/import", json={"url": "https://github.com/octocat/Hello-World"}
        ).status_code
        == 502
    )
    assert client.get("/api/repos").json() == []
    assert not any(i.is_dir() for i in store.WORKSPACE.iterdir())


@pytest.mark.parametrize(
    "path",
    [
        "../outside.py",
        "C:/unsafe.py",
        "src/CON.txt",
        "src/..\\unsafe.py",
        "src/hidden\u202e.py",
    ],
)
def test_zip_traversal(client, path):
    assert upload(client, {path: "bad"}).status_code == 422
    assert client.get("/api/repos").json() == []


def test_import_folder_and_read_restrictions(client):
    destination = client.post(
        "/api/repos/upload",
        files=[
            ("files", ("app.py", b"print('hello')\n")),
            ("files", ("data.py", b"\0binary")),
            ("files", (".env", b"SECRET=sensitive")),
        ],
        data={
            "paths": json.dumps(
                ["project/src/app.py", "project/data.py", "project/.env"]
            )
        },
    )
    assert destination.status_code == 201
    repo = destination.json()
    base = "/api/repos/" + repo["id"]
    assert repo["name"] == "project"
    assert {i["path"] for i in client.get(base + "/files").json()} == {
        "src",
        "src/app.py",
        "data.py",
    }
    for path, status in (
        ("src/app.py", 200),
        ("../../outside.py", 422),
        (".env", 404),
        ("data.py", 415),
        ("src", 404),
    ):
        assert client.get(base + "/file", params={"path": path}).status_code == status


@pytest.fixture
def scanned(client):
    imported = upload(
        client,
        {
            "src/main.py": "import os\n\nprint('ok')\n",
            ".env": "SECRET=hide",
            "node_modules/fake.js": "ignored",
            "README.md": "Project",
            "local.txt": "password = 'example-test-fixture'",
        },
    )
    assert imported.status_code == 201
    repo_id = imported.json()["id"]
    path = "/api/repos/" + repo_id
    result = client.post(path + "/analyze")
    assert result.status_code == 200, result.text
    scan = result.json()
    issue = next(i for i in scan["findings"] if i["rule"] == "F401")
    assert (issue["path"], issue["line"], issue["category"]) == (
        "src/main.py",
        1,
        "static",
    )
    assert client.get(path + "/analysis").json() == scan
    assert client.get(path + "/file", params={"path": "local.txt"}).status_code == 403
    return repo_id, path, issue["id"]


@pytest.fixture
def model(monkeypatch):
    class Responses:
        output = patches.Proposal(
            explanation="The import is unused.",
            rationale="Remove the unused import.",
            original_block="import os\n\n",
            replacement_block="",
        )

        def parse(self, **args):
            assert args["store"] is False
            assert args["text_format"] is patches.Proposal
            return type("Result", (), {"output_parsed": self.output, "usage": None})()

    responses = Responses()
    monkeypatch.setenv("AI_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "test-config")
    monkeypatch.setenv("OPENAI_MODEL", "test-model")
    monkeypatch.setattr(
        patches,
        "OpenAI",
        lambda **kwargs: type("Client", (), {"responses": responses})(),
    )
    return responses


def test_accept_reject_and_export(client, scanned, model):
    _, path, finding_id = scanned
    generate = "/api/findings/" + finding_id + "/fix"
    proposal = client.post(generate)
    assert proposal.status_code == 201, proposal.text
    rejected = "/api/patches/" + proposal.json()["id"]
    assert client.post(rejected + "/reject").json()["status"] == "rejected"
    file_path = {"path": "src/main.py"}
    assert "import os" in client.get(path + "/file", params=file_path).json()["content"]
    assert client.post(rejected + "/accept").status_code == 409
    proposal = client.post(generate).json()
    assert "-import os" in proposal["diff"]
    accepted = "/api/patches/" + proposal["id"]
    response = client.post(accepted + "/accept")
    assert response.status_code == 200, response.text
    assert response.json()["comparison"]["target_disappeared"]
    assert response.json()["syntax"] == "PASSED"
    assert response.json()["comparison"]["runtime"] == "NOT RUN"
    assert client.post(accepted + "/accept").status_code == 200
    assert client.get(path + "/analysis").json()["findings"] == []
    exported = client.get(path + "/export")
    with zipfile.ZipFile(io.BytesIO(exported.content)) as result:
        assert set(result.namelist()) == {"src/main.py", "README.md"}
        assert result.read("src/main.py") == b"print('ok')\n"
    assert client.get("/api/dashboard").json()["resolved"] == 1


def test_stale_and_invalid_proposals(client, scanned, model):
    repo_id, _, finding_id = scanned
    endpoint = "/api/findings/" + finding_id + "/fix"
    valid = model.output
    model.output = valid.model_copy(update={"original_block": "unrelated code"})
    assert client.post(endpoint).status_code == 422
    model.output = valid.model_copy(update={"replacement_block": "def invalid(\n"})
    invalid = client.post(endpoint).json()
    assert client.post("/api/patches/" + invalid["id"] + "/accept").status_code == 422
    assert client.get("/api/patches/" + invalid["id"]).json()["syntax"] == "FAILED"
    model.output = valid
    patch = client.post(endpoint).json()
    project = store.WORKSPACE / repo_id / "source" / "src" / "main.py"
    project.write_text("# user edit\n", encoding="utf-8")
    assert client.post("/api/patches/" + patch["id"] + "/accept").status_code == 409
    assert project.read_text(encoding="utf-8") == "# user edit\n"


def test_syntax_and_benchmark(client):
    repo = upload(client, {"broken.py": "def invalid(\n"}).json()
    scan = client.post("/api/repos/" + repo["id"] + "/analyze").json()
    assert scan["findings"][0]["category"] == "syntax"
    metrics = client.post(
        "/api/dashboard/benchmark",
        json={"expected": ["one", "two"], "detected": ["one", "three"]},
    ).json()
    assert metrics["precision"] == metrics["recall"] == metrics["f1"] == 0.5


def test_cross_origin_upload_is_rejected(client):
    response = client.post(
        "/api/repos/upload",
        headers={"Origin": "https://foreign.invalid"},
        files={"archive": ("x.zip", archive({"x.py": "pass"}))},
    )
    assert response.status_code == 403


def test_interrupted_patch_recovery(client, scanned, model):
    from services.api import recover_pending_patches

    repo_id, _, finding_id = scanned
    result = client.post("/api/findings/" + finding_id + "/fix").json()
    root = store.WORKSPACE / repo_id
    marker = root / ".pending-patch"
    target = root / "source" / "src" / "main.py"
    patches.atomic_write(marker, result["id"])
    patches.atomic_write(target, result["proposed"])
    recover_pending_patches()
    assert target.read_bytes() == result["original"].encode()
    assert not marker.exists()


@pytest.fixture
def ai_workspace(client, monkeypatch):
    from services import assistant

    response = upload(
        client,
        {
            "README.md": "# Demo repository\n",
            "app.py": "import math\n\n\ndef multiply_values(a, b):\n    return a * b\n",
            "other.py": "def unrelated():\n    return 42\n",
            "private.py": "password = 'example-test-fixture'\n",
        },
    )
    assert response.status_code == 201
    repo_id = response.json()["id"]

    class Responses:
        citation = 1
        replacement = "def multiply_values(left, right):\n    return left * right\n"
        inputs = []

        def parse(self, **args):
            assert args["store"] is False
            data = json.loads(args["input"])
            self.inputs.append(data)
            if args["text_format"] is assistant.Improvement:
                parsed = assistant.Improvement(
                    replacement=self.replacement,
                    explanation="Rename the parameters for clarity.",
                    benefit="Makes the formula easier to read.",
                    risks="Keyword callers may depend on the original names.",
                )
            else:
                assert args["text_format"] is assistant.Answer
                parsed = assistant.Answer(
                    answer="This function multiplies two values.",
                    citations=[self.citation],
                )
            return type("Result", (), {"output_parsed": parsed, "usage": None})()

    mock = Responses()
    monkeypatch.setenv("AI_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "test-config")
    monkeypatch.setenv("OPENAI_MODEL", "test-model")
    monkeypatch.setattr(
        patches, "OpenAI", lambda **kwargs: type("Client", (), {"responses": mock})()
    )
    return repo_id, "/api/repos/" + repo_id, mock


def test_source_context_explanation_and_retrieval(client, ai_workspace):
    _, prefix, mock = ai_workspace
    source = client.get(prefix + "/source-context", params={"path": "app.py"}).json()
    assert source["symbols"] == [{"name": "multiply_values", "start": 4, "end": 5}]
    body = {"path": "app.py", "start": 4, "end": 5, "question": "What is returned?"}
    response = client.post(prefix + "/explain", json=body)
    assert response.status_code == 200, response.text
    assert response.json()["citations"] == [{"path": "app.py", "start": 4, "end": 5}]
    assert mock.inputs[-1]["excerpts"][0]["code"].startswith("def multiply_values")
    assert client.post(prefix + "/explain", json={**body, "end": 6}).status_code == 422
    assert (
        client.post(
            prefix + "/explain", json={**body, "path": "private.py"}
        ).status_code
        == 403
    )

    response = client.post(
        prefix + "/assistant", json={"question": "What does multiply_values do?"}
    )
    assert response.status_code == 200, response.text
    assert response.json()["citations"][0]["path"] == "app.py"
    assert not any(item["path"] == "private.py" for item in mock.inputs[-1]["excerpts"])
    mock.citation = 900
    assert (
        client.post(
            prefix + "/assistant", json={"question": "Explain this repository."}
        ).status_code
        == 502
    )


def test_improvement_approval_and_stale_source(client, ai_workspace):
    repo_id, prefix, mock = ai_workspace
    source = client.get(prefix + "/source-context", params={"path": "app.py"}).json()
    body = {
        "path": "app.py",
        "start": 4,
        "end": 5,
        "goal": "readability",
        "source_hash": source["source_hash"],
    }
    response = client.post(prefix + "/improvements", json=body)
    assert response.status_code == 201, response.text
    patch = response.json()
    assert patch["kind"] == "improvement"
    assert patch["risks"] and "+def multiply_values" in patch["diff"]
    file = store.WORKSPACE / repo_id / "source" / "app.py"
    assert file.read_text() == source["content"]
    assert client.post("/api/patches/" + patch["id"] + "/reject").status_code == 200
    assert file.read_text() == source["content"]

    mock.replacement = "import subprocess\n\n" + mock.replacement
    assert client.post(prefix + "/improvements", json=body).status_code == 422
    mock.replacement = mock.replacement.removeprefix("import subprocess\n\n")
    patch = client.post(prefix + "/improvements", json=body).json()
    accepted = client.post("/api/patches/" + patch["id"] + "/accept")
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["comparison"]["target_disappeared"] is None
    assert accepted.json()["syntax"] == "PASSED"
    assert "return left * right" in file.read_text()
    assert client.post(prefix + "/improvements", json=body).status_code == 409


def test_ai_requires_configuration(client, ai_workspace, monkeypatch):
    _, prefix, _ = ai_workspace
    monkeypatch.delenv("OPENAI_API_KEY")
    response = client.post(prefix + "/explain", json={"path": "app.py"})
    assert response.status_code == 503
    assert "OPENAI_API_KEY" in response.json()["detail"]


@pytest.fixture
def groq_settings(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "groq")
    monkeypatch.setenv("GROQ_API_KEY", "test-groq-secret")
    monkeypatch.setenv("GROQ_MODEL", "llama-3.3-70b-versatile")


def mock_groq(monkeypatch, handler):
    import httpx

    real_client = REAL_GROQ

    def client(**kwargs):
        return real_client(
            **kwargs, http_client=httpx.Client(transport=httpx.MockTransport(handler))
        )

    monkeypatch.setattr(patches.groq, "Groq", client)


def groq_completion(content, finish_reason="stop"):
    return {
        "id": "test-completion",
        "object": "chat.completion",
        "created": 1,
        "model": "llama-3.3-70b-versatile",
        "choices": [
            {
                "index": 0,
                "finish_reason": finish_reason,
                "message": {"role": "assistant", "content": content},
            }
        ],
        "usage": {"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5},
    }


def test_groq_settings(client, groq_settings, monkeypatch):
    data = client.get("/api/settings").json()
    assert data["provider"] == "groq"
    assert data["model"] == "llama-3.3-70b-versatile"
    assert data["api_key_configured"] and data["configured"]
    assert "test-groq-secret" not in json.dumps(data)

    monkeypatch.delenv("AI_PROVIDER")
    monkeypatch.delenv("GROQ_MODEL")
    monkeypatch.delenv("GROQ_API_KEY")
    monkeypatch.setenv("OPENAI_API_KEY", "unused")
    data = client.get("/api/settings").json()
    assert data["provider"] == "groq"
    assert data["model"] == "llama-3.3-70b-versatile"
    assert not data["api_key_configured"] and not data["configured"]
    assert "GROQ_API_KEY" in data["configuration_message"]
    assert "OPENAI_API_KEY" not in data["configuration_message"]


def test_groq_routes_use_validated_json(client, scanned, groq_settings, monkeypatch):
    import httpx

    _, prefix, finding_id = scanned
    calls = []

    def handler(request):
        assert request.url.host == "api.groq.com"
        assert request.url.path == "/openai/v1/chat/completions"
        payload = json.loads(request.content)
        assert payload["response_format"] == {"type": "json_object"}
        assert payload["model"] == "llama-3.3-70b-versatile"
        prompt = payload["messages"][0]["content"]
        calls.append(prompt)
        if "original_block" in prompt:
            result = dict(
                explanation="Unused import.",
                rationale="Remove it.",
                original_block="import os\n\n",
                replacement_block="",
            )
        elif "replacement" in prompt:
            result = dict(
                replacement="print('ok')\n\n",
                explanation="Add spacing.",
                benefit="Clearer layout.",
                risks="Formatting only.",
            )
        else:
            result = dict(answer="The source prints a value.", citations=[1])
        return httpx.Response(200, json=groq_completion(json.dumps(result)))

    mock_groq(monkeypatch, handler)
    source_url = prefix + "/source-context"
    params = {"path": "src/main.py"}
    source = client.get(source_url, params=params).json()
    bug_fix = client.post("/api/findings/" + finding_id + "/fix")
    assert bug_fix.status_code == 201, bug_fix.text
    assert bug_fix.json()["usage"]["total_tokens"] == 5
    explanation = client.post(
        prefix + "/explain",
        json={
            **params,
            "start": 1,
            "end": 1,
            "question": "Explain the root cause of F401.",
        },
    )
    assert explanation.status_code == 200
    assert explanation.json()["citations"][0]["path"] == params["path"]
    assert (
        client.post(
            prefix + "/assistant", json={"question": "Explain this repository."}
        ).status_code
        == 200
    )
    improvement = client.post(
        prefix + "/improvements",
        json={
            **params,
            "start": 3,
            "end": 3,
            "goal": "readability",
            "source_hash": source["source_hash"],
        },
    )
    assert improvement.status_code == 201, improvement.text
    assert client.get(source_url, params=params).json()["content"] == source["content"]
    assert len(calls) == 4


def test_groq_missing_credentials_blocks_generation(
    client, scanned, groq_settings, monkeypatch
):
    monkeypatch.delenv("GROQ_API_KEY")
    _, _, finding_id = scanned
    response = client.post("/api/findings/" + finding_id + "/fix")
    assert response.status_code == 503
    assert "GROQ_API_KEY" in response.json()["detail"]
    assert "OPENAI" not in response.json()["detail"]


@pytest.mark.parametrize(
    "upstream,content,finish,expected,detail",
    [
        (401, None, "stop", 502, "rejected GROQ_API_KEY"),
        (404, None, "stop", 502, "GROQ_MODEL"),
        (429, None, "stop", 429, "rate limit"),
        (200, "not JSON", "stop", 502, "invalid or incomplete"),
        (
            200,
            '{"explanation":"missing required fields"}',
            "stop",
            502,
            "invalid or incomplete",
        ),
        (200, "{}", "length", 502, "invalid or incomplete"),
    ],
)
def test_groq_errors_are_sanitized(
    client,
    scanned,
    groq_settings,
    monkeypatch,
    upstream,
    content,
    finish,
    expected,
    detail,
):
    import httpx

    def handler(_request):
        body = (
            groq_completion(content, finish)
            if upstream == 200
            else {"error": {"message": "private upstream data"}}
        )
        return httpx.Response(upstream, json=body)

    mock_groq(monkeypatch, handler)
    _, _, finding_id = scanned
    response = client.post("/api/findings/" + finding_id + "/fix")
    assert response.status_code == expected
    assert detail in response.json()["detail"]
    assert "private upstream data" not in response.text


@pytest.fixture
def agent_project(client, groq_settings, monkeypatch):
    import httpx

    content = 'print("Hello world!")\n'
    uploaded = upload(
        client,
        {
            "ex.py": content,
            "old.py": "print('old')\n",
            "left/shared.py": "pass\n",
            "right/shared.py": "pass\n",
        },
    )
    assert uploaded.status_code == 201
    repo = uploaded.json()["id"]
    prefix = "/api/repos/" + repo
    session = client.post(prefix + "/agent/sessions").json()
    endpoint = prefix + "/agent/sessions/" + session["id"]

    def handler(request):
        messages = json.loads(request.content)["messages"]
        data = json.loads(messages[1]["content"])
        if "candidate_paths" in data:
            text = data["request"].lower()
            names = (
                data["explicit_paths"]
                or data["recent_paths"]
                or data["candidate_paths"]
            )
            intent, operation = (
                ("CREATE", "create")
                if text.startswith("create")
                else (
                    ("DELETE", "delete")
                    if text.startswith("delete")
                    else ("MODIFY", "modify")
                )
            )
            if text.startswith("explain"):
                intent, names = "QUESTION", []
            output = dict(
                intent=intent,
                targets=[dict(path=name, operation=operation) for name in names],
            )
        elif "excerpts" in data:
            output = dict(answer="It prints a greeting.", citations=[1])
        else:
            n = 10 if "ten" in data["request"] else 5
            if n == 10:
                assert "range(5)" in data["draft"]
            code = f'for _ in range({n}):\n    print("Hello world!")\n'
            output = dict(
                source=code,
                explanation="Repeat the greeting.",
                expected=f"{n} lines.",
                risks="None expected.",
            )
        return httpx.Response(200, json=groq_completion(json.dumps(output)))

    mock_groq(monkeypatch, handler)
    return repo, prefix, endpoint, content


def agent_send(client, endpoint, text, path=None):
    return client.post(
        endpoint + "/messages", json={"text": text, "selected_path": path}
    )


def agent_patch(client, endpoint, text, path=None):
    response = agent_send(client, endpoint, text, path)
    assert response.status_code == 200, response.text
    return response.json()["turns"][-1]["reply"]["patch_id"]


def test_agent_conversation_and_approval(client, agent_project):
    _, prefix, endpoint, original = agent_project
    target = {"path": "ex.py"}
    assert (
        agent_send(client, endpoint, "Explain ex.py").json()["turns"][-1]["reply"][
            "type"
        ]
        == "answer"
    )
    first = agent_patch(client, endpoint, "Change it to print five times")
    patch_url = "/api/patches/"
    assert (
        "+for _ in range(5):"
        in client.get(patch_url + first).json()["changes"][0]["diff"]
    )
    assert client.get(prefix + "/file", params=target).json()["content"] == original
    assert client.post(patch_url + first + "/reject").status_code == 200
    assert client.get(prefix + "/file", params=target).json()["content"] == original
    second = agent_patch(
        client, endpoint, "Modify ex.py to print Hello world five times."
    )
    accepted = client.post(patch_url + second + "/accept")
    assert accepted.status_code == 200, accepted.text
    modified = client.get(prefix + "/file", params=target).json()["content"]
    assert modified == 'for _ in range(5):\n    print("Hello world!")\n'
    with zipfile.ZipFile(
        io.BytesIO(client.get(prefix + "/export").content)
    ) as exported:
        assert exported.read("ex.py").decode() == modified
    assert client.get(prefix + "/patches").json()[0]["status"] == "accepted"
    assert len(client.get(endpoint).json()["turns"]) == 3
    assert client.get(prefix + "/agent/sessions").json()[0]["title"] == "Explain ex.py"


def test_agent_revision_ambiguity_and_stale(client, agent_project):
    repo, _, endpoint, _ = agent_project
    assert agent_send(client, endpoint, "Modify shared.py").status_code == 409
    assert (
        agent_send(client, endpoint, "Modify shared.py", "left/shared.py").status_code
        == 200
    )
    first = agent_patch(client, endpoint, "Modify ex.py to print five times")
    revised = agent_patch(client, endpoint, "Make it ten times instead")
    assert client.post("/api/patches/" + first + "/accept").status_code == 409
    assert "range(10)" in client.get("/api/patches/" + revised).json()["proposed"]
    assert (
        agent_send(client, endpoint, "Accept").json()["turns"][-1]["reply"]["type"]
        == "clarification"
    )
    (store.WORKSPACE / repo / "source" / "ex.py").write_text(
        "# changed\n", encoding="utf-8"
    )
    assert client.post("/api/patches/" + revised + "/accept").status_code == 409


def test_agent_create_delete_and_traversal(client, agent_project):
    _, prefix, endpoint, _ = agent_project
    assert agent_send(client, endpoint, "Create ../outside.py").status_code == 422
    created = agent_patch(client, endpoint, "Create utils/helpers.py")
    path = {"path": "utils/helpers.py"}
    assert client.get(prefix + "/file", params=path).status_code == 404
    assert client.post("/api/patches/" + created + "/accept").status_code == 200
    assert client.get(prefix + "/file", params=path).status_code == 200
    ambiguous = agent_send(client, endpoint, "Delete a function in old.py")
    assert ambiguous.json()["turns"][-1]["reply"]["type"] == "clarification"
    deleted = agent_patch(client, endpoint, "Delete old.py.")
    assert client.get(prefix + "/file", params={"path": "old.py"}).status_code == 200
    assert client.post("/api/patches/" + deleted + "/accept").status_code == 200
    assert client.get(prefix + "/file", params={"path": "old.py"}).status_code == 404


def test_agent_multifile_rollback(client, agent_project, monkeypatch):
    from services import change_set

    _, prefix, endpoint, original = agent_project
    patch = agent_patch(client, endpoint, "Modify ex.py and old.py")
    real_write = change_set.write

    def fail_second(path, content):
        if path.name == "old.py" and content and "range(5)" in content:
            raise OSError("fixture failure")
        real_write(path, content)

    monkeypatch.setattr(change_set, "write", fail_second)
    assert client.post("/api/patches/" + patch + "/accept").status_code == 500
    assert (
        client.get(prefix + "/file", params={"path": "ex.py"}).json()["content"]
        == original
    )
    assert (
        client.get(prefix + "/file", params={"path": "old.py"}).json()["content"]
        == "print('old')\n"
    )
    assert client.get("/api/patches/" + patch).json()["status"] == "proposed"


def test_agent_invalid_plan(client, agent_project, monkeypatch):
    import httpx

    _, _, endpoint, _ = agent_project
    mock_groq(
        monkeypatch,
        lambda _: httpx.Response(200, json=groq_completion('{"intent":"APPROVE"}')),
    )
    assert agent_send(client, endpoint, "Can you modify ex.py?").status_code == 502
    assert client.get(endpoint).json()["turns"] == []


def test_agent_missing_session_and_repository_codes(client, agent_project):
    _, prefix, _, _ = agent_project
    missing = "00000000-0000-0000-0000-000000000000"
    route = prefix + "/agent/sessions/" + missing
    response = client.get(route)
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "session_missing"
    response = agent_send(client, route, "Hello")
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "session_missing"
    response = client.post("/api/repos/" + missing + "/agent/sessions")
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "repository_missing"


def test_first_selected_file_message_and_idempotent_retry(
    client, groq_settings, monkeypatch
):
    import httpx

    original = 'if __name__ == "__main__":\n    print("Hello world!")\n'
    created = upload(client, {"src/tools/ex.py": original, "README.md": "Example"})
    prefix = "/api/repos/" + created.json()["id"]
    session = client.post(prefix + "/agent/sessions").json()
    session_url = prefix + "/agent/sessions/" + session["id"]
    calls = []

    def handler(request):
        data = json.loads(json.loads(request.content)["messages"][1]["content"])
        calls.append(data)
        assert data["path"] == "src/tools/ex.py"
        assert data["current_source"] == original
        if len(calls) == 1:
            return httpx.Response(429, json={"error": {"message": "Rate limit"}})
        payload = dict(
            source='if __name__ == "__main__":\n    for _ in range(5):\n        print("Hello world!")\n',
            explanation="Repeat the greeting.",
            expected="Five greetings.",
            risks="None expected.",
        )
        return httpx.Response(200, json=groq_completion(json.dumps(payload)))

    mock_groq(monkeypatch, handler)
    body = {
        "text": "i want to print hello world 5 times",
        "selected_path": "src/tools/ex.py",
        "request_id": "00000000-0000-0000-0000-000000000001",
    }
    assert client.post(session_url + "/messages", json=body).status_code == 429
    assert client.get(session_url).json()["turns"] == []
    response = client.post(session_url + "/messages", json=body)
    assert response.status_code == 200, response.text
    replay = client.post(session_url + "/messages", json=body)
    assert replay.json() == response.json()
    assert len(calls) == 2
    assert client.get(session_url).json() == response.json()
    tree = client.get(prefix + "/files").json()
    assert {item["path"] for item in tree} == {
        "README.md",
        "src",
        "src/tools",
        "src/tools/ex.py",
    }
    params = {"path": "src/tools/ex.py"}
    assert client.get(prefix + "/file", params=params).json()["content"] == original
    patch_id = response.json()["turns"][0]["reply"]["patch_id"]
    patch = client.post("/api/patches/" + patch_id + "/accept")
    assert patch.status_code == 200
    assert "range(5)" in client.get(prefix + "/file", params=params).json()["content"]


LANGUAGE_CASES = [
    ("example.py", "print('old')\n", "print('new')\n", "Python", "python"),
    (
        "src/async_bug.js",
        'async function original() { return "Taylor"; }\nconsole.log(original());\n',
        'async function kunal() { return "Taylor"; }\nconsole.log(kunal());\n',
        "JavaScript",
        "javascript",
    ),
    (
        "format.ts",
        "const value: number = 1;\n",
        "const value: number = 2;\n",
        "TypeScript",
        "typescript",
    ),
    (
        "Loop.java",
        "class Loop { void count() { for (int i=0; i<=5; i++) {} } }\n",
        "class Loop { void count() { for (int i=0; i<5; i++) {} } }\n",
        "Java",
        "java",
    ),
    (
        "array.cpp",
        "int f() { int a[3] = {1, 2, 3}; return a[3]; }\n",
        "int f() { int a[3] = {1, 2, 3}; return a[2]; }\n",
        "C++",
        "cpp",
    ),
    (
        "array.c",
        "int f(void) { int a[3] = {1, 2, 3}; return a[3]; }\n",
        "int f(void) { int a[3] = {1, 2, 3}; return a[2]; }\n",
        "C",
        "cpp",
    ),
    ("config.json", '{"enabled":false}\n', '{"enabled":true}\n', "JSON", "json"),
]


def language_project(client, files):
    response = upload(client, files)
    assert response.status_code == 201, response.text
    repo = response.json()["id"]
    prefix = "/api/repos/" + repo
    session = client.post(prefix + "/agent/sessions").json()
    return repo, prefix, prefix + "/agent/sessions/" + session["id"]


@pytest.mark.parametrize("path,original,updated,language,monaco", LANGUAGE_CASES)
def test_multilanguage_agent_approval(
    client, groq_settings, monkeypatch, path, original, updated, language, monaco
):
    import httpx

    from services import validation

    _, prefix, session = language_project(client, {path: original})

    def handler(request):
        source = json.loads(json.loads(request.content)["messages"][1]["content"])
        assert source["language"] == language
        assert source["current_source"] == original
        proposal = dict(
            source=updated,
            explanation="Update the selected code.",
            expected="Requested change.",
            risks="Review the change.",
        )
        return httpx.Response(200, json=groq_completion(json.dumps(proposal)))

    mock_groq(monkeypatch, handler)
    prompt = (
        "Change the function name to kunal."
        if language == "JavaScript"
        else "Modify " + path
    )
    first = agent_patch(client, session, prompt, path)
    preview = client.get("/api/patches/" + first).json()["changes"][0]
    assert preview["language"] == monaco
    expected = (
        validation.UNAVAILABLE
        if validation.diagnose(updated, path) is None
        else validation.PASSED
    )
    assert preview["syntax"] == expected
    assert preview["original"] == original and preview["proposed"] == updated
    assert "-" in preview["diff"] and "+" in preview["diff"]
    if language in {"Python", "JavaScript", "TypeScript", "JSON"}:
        assert expected == validation.PASSED
    assert client.post("/api/patches/" + first + "/reject").status_code == 200
    query = {"path": path}
    assert client.get(prefix + "/file", params=query).json()["content"] == original
    second = agent_patch(client, session, prompt, path)
    accepted = client.post("/api/patches/" + second + "/accept")
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["syntax"] == expected
    assert client.get(prefix + "/file", params=query).json()["content"] == updated
    with zipfile.ZipFile(io.BytesIO(client.get(prefix + "/export").content)) as package:
        assert package.read(path).decode() == updated


def test_multifile_javascript_rename_and_stale_check(
    client, groq_settings, monkeypatch
):
    import httpx

    files = {
        "src/definition.js": "export function oldName() { return 1; }\n",
        "src/caller.js": "import { oldName } from './definition.js';\nconsole.log(oldName());\n",
        "src/other.js": "console.log('different');\n",
    }
    repo, prefix, session = language_project(client, files)

    def handler(request):
        data = json.loads(json.loads(request.content)["messages"][1]["content"])
        proposal = dict(
            source=data["current_source"].replace("oldName", "newName"),
            explanation="Rename the symbol.",
            expected="Same behavior.",
            risks="Check external references.",
        )
        return httpx.Response(200, json=groq_completion(json.dumps(proposal)))

    mock_groq(monkeypatch, handler)
    prompt = "Rename oldName to newName across files"
    identifier = agent_patch(client, session, prompt, "src/definition.js")
    patch = client.get("/api/patches/" + identifier).json()
    assert {item["path"] for item in patch["changes"]} == {
        "src/definition.js",
        "src/caller.js",
    }
    assert client.post("/api/patches/" + identifier + "/accept").status_code == 200
    assert (
        "newName"
        in client.get(prefix + "/file", params={"path": "src/caller.js"}).json()[
            "content"
        ]
    )
    assert (
        client.get(prefix + "/file", params={"path": "src/other.js"}).json()["content"]
        == files["src/other.js"]
    )

    # Replay the same proposal against an independently changed second file.
    original = {
        name: text.replace("oldName", "newName") for name, text in files.items()
    }
    for name, text in files.items():
        (store.WORKSPACE / repo / "source" / name).write_bytes(text.encode())
    identifier = agent_patch(client, session, prompt, "src/definition.js")
    caller = store.WORKSPACE / repo / "source" / "src" / "caller.js"
    caller.write_bytes((original["src/caller.js"] + "// external edit\n").encode())
    assert client.post("/api/patches/" + identifier + "/accept").status_code == 409
    assert (
        client.get(prefix + "/file", params={"path": "src/definition.js"}).json()[
            "content"
        ]
        == files["src/definition.js"]
    )


def test_multilanguage_diagnostics_and_invalid_patch(
    client, groq_settings, monkeypatch
):
    import httpx

    from services import languages, validation

    _, prefix, session = language_project(
        client,
        {
            "bad.json": "{bad}",
            "basic.js": "const unused = 1;\n",
            "typed.ts": 'const value: number = "text";\n',
        },
    )
    scan = client.post(prefix + "/analyze").json()
    rules = {item["rule"] for item in scan["findings"]}
    assert {"JSON-SYNTAX", "no-unused-vars", "TS2322"} <= rules
    assert languages.detect("includes/plain.h", "int f(void);") is languages.HEADER
    assert languages.detect("includes/cpp.h", "namespace ns {}") is languages.CPP
    assert validation.c_diagnostics('#include "/host/file"\n', "c") is None

    proposal = dict(
        source="function {", explanation="invalid", expected="invalid", risks="invalid"
    )
    mock_groq(
        monkeypatch,
        lambda _: httpx.Response(200, json=groq_completion(json.dumps(proposal))),
    )
    response = agent_send(client, session, "Modify basic.js")
    assert response.status_code == 422
    assert "JavaScript" in response.json()["detail"]
    from services import change_set
    from fastapi import HTTPException

    root = store.WORKSPACE / prefix.split("/")[-1] / "source"
    for path in (".env.js", "../outside.js", "data.gif"):
        with pytest.raises(HTTPException) as failure:
            change_set.snapshot(root, path)
        assert failure.value.status_code == 422


def test_ambiguous_header_reports_unavailable_validation(
    client, groq_settings, monkeypatch
):
    import httpx

    from services import validation

    _, prefix, session = language_project(client, {"shared.h": "int oldName(void);\n"})
    proposal = dict(
        source="int newName(void);\n",
        explanation="Rename declaration.",
        expected="New public name.",
        risks="Review callers.",
    )
    mock_groq(
        monkeypatch,
        lambda _: httpx.Response(200, json=groq_completion(json.dumps(proposal))),
    )
    identifier = agent_patch(client, session, "Modify shared.h", "shared.h")
    preview = client.get("/api/patches/" + identifier).json()
    assert preview["syntax"] == validation.UNAVAILABLE
    result = client.post("/api/patches/" + identifier + "/accept")
    assert result.status_code == 200
    assert result.json()["syntax"] == validation.UNAVAILABLE
    assert client.get(prefix + "/analysis").json()["validation_unavailable"] == [
        "shared.h"
    ]


def test_multilanguage_explanation_improvement_and_finding_fix(
    client, groq_settings, monkeypatch
):
    import httpx

    _, prefix, _ = language_project(
        client,
        {
            "file.js": "const unused = 1;\nconsole.log(2);\n",
            "typed.ts": "function value(): number { return 1; }\n",
            "settings.json": '{"password":"example-test-credential"}',
        },
    )

    def handler(request):
        payload = json.loads(request.content)["messages"]
        if "original_block" in payload[0]["content"]:
            result = dict(
                explanation="The declaration is unused.",
                rationale="Remove it.",
                original_block="const unused = 1;\n",
                replacement_block="",
            )
        elif (
            "improvement" in payload[0]["content"].lower()
            or "Improve the selected" in payload[0]["content"]
        ):
            data = json.loads(payload[1]["content"])
            assert data["selection"]["language"] == "TypeScript"
            result = dict(
                replacement="function value(): number { return 2; }\n",
                explanation="Update result.",
                benefit="Requested result.",
                risks="Review callers.",
            )
        else:
            result = dict(answer="This function returns a number.", citations=[1])
        return httpx.Response(200, json=groq_completion(json.dumps(result)))

    mock_groq(monkeypatch, handler)
    assert (
        client.get(prefix + "/file", params={"path": "settings.json"}).status_code
        == 403
    )
    source = client.get(prefix + "/source-context", params={"path": "typed.ts"}).json()
    assert source["symbols"] == [{"name": "value", "start": 1, "end": 1}]
    assert (
        client.post(prefix + "/explain", json={"path": "typed.ts"}).status_code == 200
    )
    improvement = client.post(
        prefix + "/improvements",
        json={
            "path": "typed.ts",
            "goal": "readability",
            "source_hash": source["source_hash"],
        },
    )
    assert improvement.status_code == 201, improvement.text
    assert improvement.json()["language"] == "typescript"
    assert improvement.json()["syntax"] == "PASSED"
    scan = client.post(prefix + "/analyze").json()
    issue = next(item for item in scan["findings"] if item["rule"] == "no-unused-vars")
    proposal = client.post("/api/findings/" + issue["id"] + "/fix")
    assert proposal.status_code == 201, proposal.text
    assert proposal.json()["language"] == "javascript"
    assert proposal.json()["syntax"] == "PASSED"


def test_detection_metric_denominators():
    from services import evaluation

    sample = evaluation.score({"match", "miss"}, {"match", "extra"})
    assert sample == dict(
        true_positive=1,
        false_positive=1,
        false_negative=1,
        precision=0.5,
        recall=0.5,
        f1=0.5,
    )
    assert evaluation.score(set(), set()) == dict(
        true_positive=0,
        false_positive=0,
        false_negative=0,
        precision=None,
        recall=None,
        f1=None,
    )
    assert evaluation.score({"miss"}, set())["recall"] == 0
    assert evaluation.totals([])["verified_fix_success_rate"] is None


def test_verification_records_regression_and_original_findings(client, scanned, model):
    _, _, finding_id = scanned
    model.output = model.output.model_copy(
        update={"replacement_block": "import sys\n\n"}
    )
    patch = client.post("/api/findings/" + finding_id + "/fix").json()
    endpoint = "/api/patches/" + patch["id"]
    applied = client.post(endpoint + "/accept")
    assert applied.status_code == 200, applied.text
    detail = client.get(endpoint + "/verification").json()
    assert detail["verification"]["status"] == "regression detected"
    assert detail["verification"]["target"] == "resolved"
    assert detail["verification"]["runtime"] == "NOT RUN"
    assert detail["verification"]["changed_files"] == [
        {"path": "src/main.py", "operation": "modify", "syntax": "PASSED"},
    ]
    assert detail["original"][0]["rule"] == "F401"
    assert (
        len(detail["comparison"]["resolved"])
        == len(detail["comparison"]["introduced"])
        == 1
    )
    dashboard = client.get("/api/dashboard").json()
    assert dashboard["targeted_resolved"] == 1
    assert dashboard["targeted_accepted"] == 1
    assert dashboard["verified_fix_success_rate"] == 0
    assert dashboard["findings_by_language"] == {"Python": 1}
    assert dashboard["verification_history"][0]["status"] == "regression detected"
    assert all(item["created_at"] for item in dashboard["scan_trend"])


def test_validation_failure_is_visible_without_comparison(client, scanned, model):
    _, _, finding_id = scanned
    model.output = model.output.model_copy(
        update={"replacement_block": "def invalid(\n"}
    )
    patch = client.post("/api/findings/" + finding_id + "/fix").json()
    endpoint = "/api/patches/" + patch["id"]
    assert client.post(endpoint + "/accept").status_code == 422
    record = client.get(endpoint + "/verification").json()
    assert record["verification"]["status"] == "syntax failed"
    assert record["verification"]["changed_files"][0]["syntax"] == "FAILED"
    assert record["comparison"] is None


def test_incomplete_coverage_does_not_resolve_finding():
    from services import evaluation

    issue = dict(
        id="target",
        path="demo.py",
        line=1,
        rule="F401",
        message="Unused",
        evidence="import os",
    )
    before = dict(
        id="before",
        findings=[issue],
        analyzed_paths=["demo.py"],
        skipped=[],
        validation_unavailable=[],
        severity={"low": 1},
        runtime="NOT RUN",
        duration_ms=1,
    )
    after = dict(
        before,
        id="after",
        findings=[],
        analyzed_paths=[],
        validation_unavailable=["demo.py"],
        severity={},
    )
    comparison, record = evaluation.verify(
        before, after, issue, [{"path": "demo.py", "operation": "modify"}], "PASSED"
    )
    assert comparison["resolved"] == []
    assert comparison["target_disappeared"] is None
    assert record["status"] == "incomplete" and record["target"] == "inconclusive"


def test_bundled_benchmark_persists_real_results(client):
    assert client.get("/api/dashboard/benchmark/latest").json() is None
    response = client.post("/api/dashboard/benchmark/run")
    assert response.status_code == 201, response.text
    result = response.json()
    assert result == client.get("/api/dashboard/benchmark/latest").json()
    assert result["dataset"] == "source-diagnostics-v2"
    assert result["mode"] == "static analysis"
    assert len(result["cases"]) == 100
    assert len(result["by_language"]) == 5
    assert result["metrics"]["cases"] == 100
    assert (
        result["metrics"]["evaluated"] + len(result["missing"])
        == result["metrics"]["supported"]
    )
    assert result["metrics"]["supported"] + len(result["known_unsupported"]) == 100
    assert (
        result["metrics"]["true_positive"] + result["metrics"]["false_negative"]
        == (result["metrics"]["positive_labels"])
    )
    assert (
        result["metrics"]["true_positive"] + result["metrics"]["false_positive"]
        == (result["metrics"]["detected_findings"])
    )


def test_github_auth_failure_is_sanitized(client, monkeypatch):
    import httpx
    from services import github

    monkeypatch.setenv("GITHUB_TOKEN", "test-token-private")
    real = httpx.Client

    def fail(request):
        assert request.url.host == "api.github.com"
        assert request.headers["authorization"] == "Bearer test-token-private"
        return httpx.Response(401, json={"message": "test-token-private"})

    monkeypatch.setattr(
        github.httpx,
        "Client",
        lambda **args: real(**args, transport=httpx.MockTransport(fail)),
    )
    response = client.post("/api/github/connect")
    assert response.status_code == 401
    assert "test-token-private" not in response.text
    assert client.get("/api/github/status").json() == {
        "configured": True,
        "connected": False,
        "username": None,
    }


def test_github_import_and_disconnect(client, monkeypatch):
    import base64

    from services import github

    monkeypatch.setenv("GITHUB_TOKEN", "fixture-only")
    raw = b"print('private')\n"
    slug = "/repos/team/private"
    replies = {
        "/user": {"login": "fixture-user"},
        "/user/repos": [{"full_name": "team/private", "private": True}],
        slug: {"default_branch": "main"},
        slug + "/git/ref/heads/main": {"object": {"sha": "base"}},
        slug + "/git/commits/base": {"tree": {"sha": "tree"}},
        slug + "/git/trees/tree": {
            "tree": [
                {
                    "path": "main.py",
                    "mode": "100644",
                    "type": "blob",
                    "size": len(raw),
                    "sha": "blob",
                },
                {
                    "path": ".env",
                    "mode": "100644",
                    "type": "blob",
                    "size": 20,
                    "sha": "secret",
                },
            ]
        },
        slug + "/git/blobs/blob": {"content": base64.b64encode(raw).decode()},
    }
    monkeypatch.setattr(github, "request", lambda method, path, **kwargs: replies[path])
    assert client.post("/api/github/connect").json()["username"] == "fixture-user"
    assert client.get("/api/github/repositories").json()["items"][0]["private"]
    response = client.post(
        "/api/github/import", json={"url": "https://github.com/team/private"}
    )
    assert response.status_code == 201, response.text
    prefix = "/api/repos/" + response.json()["id"]
    assert (
        client.get(prefix + "/file", params={"path": "main.py"}).json()["content"]
        == raw.decode()
    )
    assert client.get(prefix + "/file", params={"path": ".env"}).status_code == 404
    assert not client.post("/api/github/disconnect").json()["connected"]
    assert client.get("/api/github/repositories").status_code == 409


def test_github_publication_requires_confirmation_and_safe_branch(
    client, scanned, model, monkeypatch
):
    import base64

    from fastapi import HTTPException
    from services import github

    repo_id, prefix, finding_id = scanned
    with store.database() as conn:
        row = store.get(conn, "repository", repo_id)
        row.update(origin="github", source_url="https://github.com/team/project")
        store.save(conn, "repository", row)
    patch = client.post("/api/findings/" + finding_id + "/fix").json()
    patch_id = patch["id"]
    assert client.post("/api/patches/" + patch_id + "/accept").status_code == 200
    monkeypatch.setenv("GITHUB_TOKEN", "fixture-only")
    project = "/repos/team/project"
    branch = "code-review/" + patch_id
    calls = []
    original = github.blob_id(patch["original"])

    def remote(method, path, **kwargs):
        calls.append((method, path, kwargs))
        if method == "GET":
            data = {
                "/user": {"login": "fixture-user"},
                project: {"default_branch": "main"},
                project + "/git/ref/heads/main": {"object": {"sha": "base"}},
                project + "/git/commits/base": {"tree": {"sha": "root-tree"}},
                project + "/git/trees/root-tree": {
                    "tree": [
                        {
                            "path": "src/main.py",
                            "type": "blob",
                            "mode": "100644",
                            "sha": original,
                        },
                    ]
                },
                project + "/pulls": [],
                project + "/pulls/12": {
                    "number": 12,
                    "state": "closed",
                    "merged": True,
                },
            }
            if path == project + "/git/ref/heads/" + branch:
                raise HTTPException(404)
            return data[path]
        return {
            project + "/git/blobs": {"sha": "new-blob"},
            project + "/git/trees": {"sha": "new-tree"},
            project + "/git/commits": {"sha": "new-commit"},
            project + "/git/refs": {},
            project + "/pulls": {"number": 12, "state": "open"},
        }[path]

    monkeypatch.setattr(github, "request", remote)
    assert client.post("/api/github/connect").status_code == 200
    preview = client.get(
        prefix + "/github/preview", params={"patch_id": patch_id}
    ).json()
    url = prefix + "/github/pull-requests"
    payload = {
        "patch_id": patch_id,
        "confirmation": preview["confirmation"],
        "title": "Remove unused import",
    }
    assert client.post(url, json=payload).status_code == 422
    assert (
        client.post(
            url, json={**payload, "confirmed": True, "confirmation": "0" * 64}
        ).status_code
        == 409
    )
    assert not any(method == "POST" for method, _, _ in calls)
    result = client.post(url, json={**payload, "confirmed": True})
    assert result.status_code == 201, result.text
    assert result.json()["url"] == "https://github.com/team/project/pull/12"
    writes = {path: args["json"] for method, path, args in calls if method == "POST"}
    assert writes[project + "/git/refs"]["ref"] == "refs/heads/" + branch
    assert writes[project + "/pulls"]["head"] == branch
    assert writes[project + "/pulls"]["base"] == "main"
    assert writes[project + "/git/commits"]["parents"] == ["base"]
    assert (
        base64.b64decode(writes[project + "/git/blobs"]["content"]).decode()
        == patch["proposed"]
    )
    assert all(method in {"GET", "POST"} for method, _, _ in calls)
    assert client.post(url, json={**payload, "confirmed": True}).json() == result.json()
    assert client.get(url + "/" + patch_id).json()["state"] == "merged"


def test_architecture_review_and_report(client, monkeypatch):
    import httpx

    monkeypatch.setenv("AI_PROVIDER", "groq")
    monkeypatch.setenv("GROQ_API_KEY", "fixture")
    monkeypatch.setenv("GROQ_MODEL", "fixture")
    _, prefix, _ = language_project(
        client,
        {
            "main.py": "from worker import run\nrun()\n",
            "worker.py": "def run():\n    return 1\n",
        },
    )
    result = client.get(
        prefix + "/architecture", params={"selected": "worker.py"}
    ).json()
    assert result["affected_files"] == ["main.py"]
    assert result["dependencies"] == [{"source": "main.py", "target": "worker.py"}]
    assert any(
        item["name"] == "run" and item["line"] == 1 for item in result["symbols"]
    )
    assert result["entry_points"][0]["path"] == "main.py"
    assert client.post(prefix + "/analyze").status_code == 200

    def provider(request):
        source = json.loads(request.content)["messages"]
        if "Summarize the architecture" in source[0]["content"]:
            parsed = {"summary": "Main imports the worker."}
        else:
            assert json.loads(source[1]["content"])[0]["path"] == "worker.py"
            parsed = {
                "suggestions": [
                    {
                        "excerpt": 1,
                        "start": 1,
                        "end": end,
                        "category": "maintainability",
                        "observation": "The function has a generic name.",
                        "recommendation": "Use a more descriptive name.",
                    }
                ]
            }
        return httpx.Response(200, json=groq_completion(json.dumps(parsed)))

    end = 2
    mock_groq(monkeypatch, provider)
    assert (
        client.post(prefix + "/architecture/summary").json()["kind"]
        == "AI interpretation"
    )
    result = client.post(prefix + "/review", json={"paths": ["worker.py"]}).json()
    assert result["ai_suggestions"][0]["path"] == "worker.py"
    assert result["static_findings"] == []
    end = 999
    assert (
        client.post(prefix + "/review", json={"paths": ["worker.py"]}).status_code
        == 502
    )
    report = client.get(prefix + "/report")
    assert "attachment" in report.headers["content-disposition"]
    assert (
        "Files scanned: 2" in report.text
        and "Runtime verification: NOT RUN" in report.text
    )
    assert "Architecture snapshot:" in report.text and "worker.py" in report.text
    assert "def run():" not in report.text


def test_json_input_limit(client):
    response = client.post(
        "/api/dashboard/benchmark",
        content=b" " * (2 * 1024**2 + 1),
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 413
    assert client.get("/api/health").status_code == 200


def test_streamed_input_limit(client):
    def body():
        for _ in range(3):
            yield b" " * 1024**2

    response = client.post(
        "/api/dashboard/benchmark",
        content=body(),
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 413


@pytest.mark.parametrize(
    ("response", "expected_proposal", "accepted"),
    [(patch_evaluation.MOCK_RESPONSE, "valid", 1), ({}, "invalid", 0)],
)
def test_isolated_patch_evaluation(client, response, expected_proposal, accepted):
    result = patch_evaluation.run("mocked", response, "Test fixture")
    assert result["case"]["proposal"] == expected_proposal
    assert result["metrics"]["accepted"] == accepted
    assert result["metrics"]["runtime_rate"] is None
    assert client.get("/api/repos").json() == []
    with store.database() as conn:
        store.save(conn, "patch_benchmark_run", result)
    assert client.get("/api/dashboard/evaluations").json()["patches"] == [result]


def test_pdf_report_omits_source(client, monkeypatch):
    marker = "source-content-only"
    created = upload(client, {"sample.py": f"value = '{marker}'\n"})
    assert created.status_code == 201
    base = f"/api/repos/{created.json()['id']}"
    assert client.post(base + "/analyze").status_code == 200

    from services import pdf_report

    original = pdf_report.render

    def render(repo, scan, patches, detection, runs):
        assert scan["files_analyzed"] == 1
        result = original(repo, scan, patches, detection, runs)
        assert result.startswith(b"%PDF-")
        return result

    monkeypatch.setattr(pdf_report, "render", render)
    response = client.get(base + "/report.pdf")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["cache-control"] == "no-store"
    assert marker.encode() not in response.content
    assert (
        client.get(base + "/report").headers["content-type"].startswith("text/markdown")
    )


def test_detection_benchmark_rejects_concurrent_runs(client):
    from services.api import benchmark_lock

    with benchmark_lock:
        response = client.post("/api/dashboard/benchmark/run")
        assert response.status_code == 409
        assert client.get("/api/health").status_code == 200


def test_groq_timeout_keeps_source_and_proposals_unchanged(
    client, scanned, groq_settings, monkeypatch
):
    import httpx

    def handler(request):
        raise httpx.ReadTimeout("private provider timeout", request=request)

    mock_groq(monkeypatch, handler)
    _, prefix, finding_id = scanned
    params = {"path": "src/main.py"}
    original = client.get(prefix + "/file", params=params).json()["content"]
    response = client.post(f"/api/findings/{finding_id}/fix")
    assert response.status_code == 504
    assert response.json()["detail"] == "Groq request timed out."
    assert client.get(prefix + "/patches").json() == []
    assert client.get(prefix + "/file", params=params).json()["content"] == original


def test_database_write_failure_restores_source(client, scanned, model, monkeypatch):
    import sqlite3

    repo_id, prefix, finding_id = scanned
    proposed = client.post(f"/api/findings/{finding_id}/fix")
    assert proposed.status_code == 201
    patch_id = proposed.json()["id"]
    path = proposed.json()["path"]
    original = client.get(prefix + "/file", params={"path": path}).json()["content"]
    save = store.save

    def fail_final_save(conn, kind, record):
        if kind == "patch" and record["status"] == "accepted":
            raise sqlite3.OperationalError("fixture database write failure")
        return save(conn, kind, record)

    with monkeypatch.context() as fault:
        fault.setattr(store, "save", fail_final_save)
        failing_client = TestClient(
            app, base_url="http://localhost", raise_server_exceptions=False
        )
        response = failing_client.post(f"/api/patches/{patch_id}/accept")
    assert response.status_code == 500
    assert "fixture database write failure" not in response.text
    assert (
        client.get(prefix + "/file", params={"path": path}).json()["content"]
        == original
    )
    assert client.get(f"/api/patches/{patch_id}").json()["status"] == "proposed"
    assert not (store.WORKSPACE / repo_id / ".pending-patch").exists()
    assert client.post(f"/api/patches/{patch_id}/accept").status_code == 200
