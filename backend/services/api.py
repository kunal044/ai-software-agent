import json
import shutil
import threading
import zipfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile

from services import (
    agent,
    analysis,
    assistant,
    change_set,
    languages,
    patches,
    repository,
    store,
    validation,
)

from services import evaluation, github, intelligence

router = APIRouter(prefix="/api")
mutations = threading.Lock()


class ImportRequest(BaseModel):
    url: str = Field(max_length=250)


class Benchmark(BaseModel):
    expected: set[str] = Field(max_length=10000)
    detected: set[str] = Field(max_length=10000)


def required(conn, kind, identifier):
    value = store.get(conn, kind, identifier)
    if value is None:
        raise HTTPException(404, f"{kind.capitalize()} not found.")
    return value


def root_for(repo_id):
    return store.WORKSPACE / repo_id / "source"


def add_project(name, origin, populate, source_url=None):
    with mutations:
        identifier = str(uuid4())
        root = root_for(identifier)
        root.mkdir(parents=True)
        try:
            populate(root)
            files = list(repository.files_in(root))
            if not files:
                raise HTTPException(422, "The project has no supported stored files.")
            repo = dict(
                id=identifier,
                name=name,
                origin=origin,
                file_count=len(files),
                size_bytes=sum(p.stat().st_size for _, p in files),
                source_url=source_url,
                imported_at=datetime.now(timezone.utc).isoformat(),
            )
            with store.database() as conn:
                store.save(conn, "repository", repo)
            return repo
        except BaseException:
            shutil.rmtree(root.parent, ignore_errors=True)
            raise


@router.post("/repos/import", status_code=201)
def import_repository(payload: ImportRequest):
    url, name = repository.github_url(payload.url)
    return add_project(
        name,
        "github",
        lambda root: repository.clone(url, root),
        url.removesuffix(".git"),
    )


def save_upload(form):
    uploads = form.getlist("files")
    archive = form.get("archive")
    if isinstance(archive, UploadFile) and not uploads:
        if (archive.size or 0) > repository.MAX_BYTES:
            raise HTTPException(413, "Archive exceeds the size limit.")
        name = Path(archive.filename or "project").stem[:100]
        return add_project(
            name, "zip", lambda root: repository.extract_zip(archive.file, root)
        )

    try:
        paths = json.loads(form.get("paths", "[]"))
        if not isinstance(paths, list) or not all(isinstance(p, str) for p in paths):
            raise ValueError
    except (ValueError, TypeError):
        raise HTTPException(
            422, "Folder paths must be a JSON array of strings."
        ) from None
    if archive is not None or not uploads or len(uploads) != len(paths):
        raise HTTPException(
            422, "Provide a ZIP archive or files with matching relative paths."
        )
    if not all(isinstance(f, UploadFile) for f in uploads):
        raise HTTPException(422, "Folder entries must be uploaded files.")
    if sum(f.size or 0 for f in uploads) > repository.MAX_BYTES:
        raise HTTPException(413, "Folder exceeds the size limit.")
    validated = [repository.parts_for(path) for path in paths]
    if len({p.casefold() for p in paths}) != len(paths):
        raise HTTPException(422, "Folder contains duplicate paths.")
    base = (
        validated[0][0]
        if all(len(p) > 1 and p[0] == validated[0][0] for p in validated)
        else None
    )

    def populate(root):
        total = 0
        try:
            for parts, file in zip(validated, uploads):
                relative = parts[1:] if base else parts
                if repository.excluded(relative):
                    continue
                target = root.joinpath(*relative)
                target.parent.mkdir(parents=True, exist_ok=True)
                total += repository.copy_bounded(
                    file.file, target, repository.MAX_BYTES - total
                )
        except OSError:
            raise HTTPException(422, "Folder contains conflicting paths.") from None

    return add_project((base or "Uploaded folder")[:100], "folder", populate)


@router.post("/repos/upload", status_code=201)
async def upload(request: Request):
    async with request.form(
        max_files=repository.MAX_FILES, max_fields=4, max_part_size=2 * 1024**2
    ) as form:
        return await run_in_threadpool(save_upload, form)


@router.get("/repos")
def repositories():
    with store.database() as conn:
        return store.records(conn, "repository")


@router.get("/repos/{repo_id}")
def metadata(repo_id: str):
    with store.database() as conn:
        return required(conn, "repository", repo_id)


@router.get("/repos/{repo_id}/files")
def tree(repo_id: str):
    metadata(repo_id)
    with mutations:
        entries = {}
        for name, path in repository.files_in(root_for(repo_id)):
            for parent in Path(name).parents:
                if parent != Path("."):
                    rel = parent.as_posix()
                    entries[rel] = dict(path=rel, name=parent.name, type="directory")
            entries[name] = dict(
                path=name,
                name=path.name,
                type="file",
                size_bytes=path.stat().st_size,
                extension=path.suffix,
                **languages.capabilities(name),
            )
        return sorted(entries.values(), key=lambda item: item["path"])


@router.get("/repos/{repo_id}/file")
def read_file(repo_id: str, path: str):
    metadata(repo_id)
    with mutations:
        _, code, language = repository.source(root_for(repo_id), path)
    if repository.SECRET.search(code):
        raise HTTPException(
            403, "Preview blocked because this file may contain a secret."
        )
    return dict(
        path=path, content=code, language=language, **languages.capabilities(path, code)
    )


@router.post("/repos/{repo_id}/analyze")
def run_analysis(repo_id: str):
    with mutations, store.database() as conn:
        required(conn, "repository", repo_id)
        result = analysis.scan(root_for(repo_id), repo_id)
        store.save(conn, "analysis", result)
        for item in result["findings"]:
            store.save(
                conn,
                "finding",
                {**item, "repo_id": repo_id, "analysis_id": result["id"]},
            )
        return result


@router.get("/repos/{repo_id}/analysis")
def latest_analysis(repo_id: str):
    with store.database() as conn:
        required(conn, "repository", repo_id)
        return store.latest(conn, "analysis", repo_id)


@router.get("/repos/{repo_id}/patches")
def repo_patches(repo_id: str):
    with store.database() as conn:
        required(conn, "repository", repo_id)
        return [
            {
                key: item.get(key)
                for key in ("id", "status", "path", "analysis_id", "kind")
            }
            for item in store.records(
                conn,
                "patch",
                repo_id,
                exclude=("original", "proposed", "diff", "comparison", "changes"),
            )
        ]


@router.get("/findings/{finding_id}")
def get_finding(finding_id: str):
    with store.database() as conn:
        return required(conn, "finding", finding_id)


@router.post("/findings/{finding_id}/fix", status_code=201)
def generate(finding_id: str):
    with mutations, store.database() as conn:
        item = required(conn, "finding", finding_id)
        repo_id = item["repo_id"]
        if store.latest(conn, "analysis", repo_id)["id"] != item["analysis_id"]:
            raise HTTPException(409, "Use a finding from the latest analysis.")
        _, code, _ = repository.source(root_for(repo_id), item["path"])
        patch = patches.propose(item, code, repo_id, item["analysis_id"])
        store.save(conn, "patch", patch)
        return patch


@router.get("/patches/{patch_id}")
def get_patch(patch_id: str):
    with store.database() as conn:
        return required(conn, "patch", patch_id)


def recover_pending_patches():
    """Reconcile interrupted writes with SQLite before serving requests."""
    with mutations, store.database() as conn:
        for marker in store.WORKSPACE.glob("*/.pending-patch"):
            raw = marker.read_text(encoding="utf-8")
            journal = (
                json.loads(raw) if raw.startswith("{") else {"id": raw, "dirs": []}
            )
            patch = required(conn, "patch", journal["id"])
            root = root_for(patch["repo_id"])
            if marker.parent != root.parent:
                raise RuntimeError(
                    "Patch recovery marker does not match its working copy."
                )
            accepted = patch["status"] == "accepted"
            for path, old, new, _ in change_set.inspect(root, patch, recovery=True):
                change_set.write(path, new if accepted else old)
            if not accepted:
                change_set.remove_empty(
                    [
                        root.joinpath(*repository.parts_for(name))
                        for name in journal["dirs"]
                    ]
                )
            marker.unlink()


@router.post("/patches/{patch_id}/{decision}")
def decide(patch_id: str, decision: str):
    if decision not in {"accept", "reject"}:
        raise HTTPException(404, "Unknown patch action.")
    with mutations:
        restore = []
        directories = []
        marker = None
        try:
            with store.database() as conn:
                patch = required(conn, "patch", patch_id)
                if patch["status"] != "proposed":
                    if (
                        patch["status"]
                        == {"accept": "accepted", "reject": "rejected"}[decision]
                    ):
                        return patch
                    raise HTTPException(409, "This proposal has already been reviewed.")
                if decision == "reject":
                    patch["status"] = "rejected"
                    store.save(conn, "patch", patch)
                    return patch
                root = root_for(patch["repo_id"])
                targets = change_set.inspect(root, patch)
                directories = [
                    directory for _, _, _, missing in targets for directory in missing
                ]
                checks = []
                reviewed = change_set.changes(patch)
                try:
                    for item in reviewed:
                        if change_set.desired(item) is not None:
                            status = patches.check_syntax(
                                item["proposed"], item["path"]
                            )
                            checks.append(status)
                            item["syntax"] = status
                except HTTPException:
                    item["syntax"] = "FAILED"
                    patch["syntax"] = "FAILED"
                    patch["failed_validation"] = item["path"]
                    store.save(conn, "patch", patch)
                    conn.commit()
                    raise
                before = analysis.scan(root, patch["repo_id"])
                targets = change_set.inspect(root, patch)
                directories = [
                    directory for _, _, _, missing in targets for directory in missing
                ]
                marker = root.parent / ".pending-patch"
                patches.atomic_write(
                    marker,
                    json.dumps(
                        {
                            "id": patch_id,
                            "dirs": [
                                d.relative_to(root).as_posix() for d in directories
                            ],
                        }
                    ),
                )
                for path, old, new, _ in targets:
                    restore.append((path, old))
                    change_set.write(path, new)
                after = analysis.scan(root, patch["repo_id"])
                syntax = (
                    validation.UNAVAILABLE
                    if validation.UNAVAILABLE in checks
                    else validation.PASSED
                    if checks
                    else "NOT APPLICABLE"
                )
                comparison, verification = evaluation.verify(
                    before, after, patch["finding"], reviewed, syntax
                )
                patch.update(
                    status="accepted",
                    syntax=syntax,
                    comparison=comparison,
                    verification=verification,
                )
                for scan in (before, after):
                    store.save(conn, "analysis", scan)
                    for item in scan["findings"]:
                        store.save(
                            conn,
                            "finding",
                            {
                                **item,
                                "repo_id": patch["repo_id"],
                                "analysis_id": scan["id"],
                            },
                        )
                if "changes" in patch:
                    repo = required(conn, "repository", patch["repo_id"])
                    repo["file_count"] += sum(
                        int(old is None) - int(new is None)
                        for _, old, new, _ in targets
                    )
                    repo["size_bytes"] += sum(
                        len((new or "").encode()) - len((old or "").encode())
                        for _, old, new, _ in targets
                    )
                    store.save(conn, "repository", repo)
                store.save(conn, "patch", patch)
        except BaseException as error:
            for path, old in reversed(restore):
                change_set.write(path, old)
            if restore:
                change_set.remove_empty(directories)
            if marker:
                marker.unlink(missing_ok=True)
            if isinstance(error, OSError):
                raise HTTPException(
                    500, "The write failed. The working copy was restored."
                ) from error
            raise
        if marker:
            marker.unlink(missing_ok=True)
        return patch


@router.get("/repos/{repo_id}/export")
def export(repo_id: str):
    name = metadata(repo_id)["name"]
    with mutations:
        with NamedTemporaryFile(
            dir=store.WORKSPACE, suffix=".zip", delete=False
        ) as temp:
            destination = Path(temp.name)
        try:
            with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as archive:
                for relative, path in repository.files_in(root_for(repo_id)):
                    repository.parts_for(relative)
                    if repository.exportable(path):
                        archive.write(path, relative)
        except BaseException:
            destination.unlink(missing_ok=True)
            raise
    return FileResponse(
        destination,
        media_type="application/zip",
        filename=f"{name}.zip",
        background=BackgroundTask(destination.unlink, missing_ok=True),
    )


@router.get("/dashboard")
def dashboard():
    with store.database() as conn:
        repos = store.records(conn, "repository")
        scans = store.records(
            conn,
            "analysis",
            exclude=("findings", "skipped", "analyzed_paths", "validation_unavailable"),
        )
        review = store.records(
            conn,
            "patch",
            exclude=("original", "proposed", "diff", "comparison.remaining", "changes"),
        )
    latest = {}
    for scan in scans:
        latest.setdefault(scan["repo_id"], scan)
    severity = Counter()
    by_language = Counter()
    recent = []
    with store.database() as conn:
        for scan in latest.values():
            counts = scan.get("findings_by_language")
            if counts is None:
                counts = evaluation.summarize_findings(
                    store.get(conn, "analysis", scan["id"])["findings"]
                )
            by_language.update(counts)
    names = {repo["id"]: repo["name"] for repo in repos}
    for patch in review:
        if "comparison" not in patch:
            continue
        comparison = patch["comparison"]
        recent.append(
            dict(
                id=patch["id"],
                repo=names.get(patch["repo_id"], ""),
                path=patch["path"],
                before=sum(comparison["before_severity"].values()),
                after=sum(comparison["after_severity"].values()),
                status=(patch.get("verification") or {}).get(
                    "status", "legacy comparison"
                ),
            )
        )
        if len(recent) == 10:
            break
    for scan in latest.values():
        severity.update(scan["severity"])
    comparisons = [p["comparison"] for p in review if "comparison" in p]
    return dict(
        **evaluation.totals(review),
        findings_by_language=dict(by_language),
        verification_history=recent,
        scan_trend=[
            dict(
                id=scan["id"],
                repo=names.get(scan["repo_id"], ""),
                created_at=scan.get("created_at"),
                findings=sum(scan["severity"].values()),
                duration_ms=scan["duration_ms"],
            )
            for scan in reversed(scans[:25])
        ],
        repositories=len(repos),
        files_analyzed=sum(s["files_analyzed"] for s in latest.values()),
        findings=sum(severity.values()),
        severity=dict(severity),
        scans=len(scans),
        average_ms=round(sum(s["duration_ms"] for s in scans) / len(scans), 1)
        if scans
        else None,
        proposed=len(review),
        accepted=sum(p["status"] == "accepted" for p in review),
        rejected=sum(p["status"] == "rejected" for p in review),
        resolved=sum(len(c["resolved"]) for c in comparisons),
        introduced=sum(len(c["introduced"]) for c in comparisons),
        syntax=dict(Counter(p["syntax"] for p in review)),
        runtime="NOT RUN",
    )


@router.post("/dashboard/benchmark")
def benchmark(payload: Benchmark):
    true_positive = len(payload.expected & payload.detected)
    precision = true_positive / len(payload.detected) if payload.detected else 0
    recall = true_positive / len(payload.expected) if payload.expected else 0
    return dict(
        true_positive=true_positive,
        false_positive=len(payload.detected - payload.expected),
        false_negative=len(payload.expected - payload.detected),
        precision=precision,
        recall=recall,
        f1=2 * precision * recall / (precision + recall) if precision + recall else 0,
    )


@router.get("/dashboard/benchmark/latest")
def latest_benchmark():
    with store.database() as conn:
        return store.latest(conn, "benchmark_run", "bundled")


@router.post("/dashboard/benchmark/run", status_code=201)
def run_benchmark():
    with mutations:
        result = evaluation.evaluate()
        result["repo_id"] = "bundled"
        with store.database() as conn:
            store.save(conn, "benchmark_run", result)
        return result


@router.get("/patches/{patch_id}/verification")
def patch_verification(patch_id: str):
    with store.database() as conn:
        patch = required(conn, "patch", patch_id)
        comparison = patch.get("comparison")
        if not comparison:
            syntax = patch["syntax"]
            status = "syntax failed" if syntax == "FAILED" else patch["status"]
            changed = [
                dict(
                    path=item["path"],
                    operation=item["operation"],
                    syntax="FAILED"
                    if item["path"] == patch.get("failed_validation")
                    else "NOT APPLICABLE"
                    if item["operation"] == "delete"
                    else item.get("syntax", syntax),
                )
                for item in change_set.changes(patch)
            ]
            return dict(
                original=[],
                comparison=None,
                verification=dict(
                    status=status,
                    syntax=syntax,
                    static_coverage="NOT RUN",
                    target="NOT CHECKED",
                    runtime="NOT RUN",
                    changed_files=changed,
                ),
            )
        original = required(conn, "analysis", comparison["before_id"])
        return dict(
            original=original["findings"],
            comparison=comparison,
            verification=patch.get("verification"),
        )


@router.get("/settings")
def settings():
    active = patches.config()
    return dict(
        provider=active.provider,
        model=active.model,
        api_key_configured=bool(active.key),
        configured=active.warning is None,
        configuration_message=active.warning,
        openai_configured=active.provider == "openai" and active.warning is None,
        runtime="NOT RUN",
    )


@router.get("/repos/{repo_id}/source-context")
def source_context(repo_id: str, path: str):
    metadata(repo_id)
    with mutations:
        code, language = assistant.read_source(root_for(repo_id), path)
    return dict(
        path=path,
        content=code,
        language=language,
        source_hash=patches.digest(code),
        symbols=assistant.source_symbols(code, path, languages.detect(path, code)),
        **languages.capabilities(path, code),
    )


@router.post("/repos/{repo_id}/explain")
def explain(repo_id: str, payload: assistant.ExplanationRequest):
    metadata(repo_id)
    with mutations:
        code, _ = assistant.read_source(root_for(repo_id), payload.path)
    if payload.source_hash is not None and patches.digest(code) != payload.source_hash:
        raise HTTPException(
            409, "The source changed. Reload the selection and try again."
        )
    excerpt = assistant.selection(payload, code)
    return assistant.answer(
        payload.question, [excerpt, *assistant.neighboring_context(code, excerpt)]
    )


@router.post("/repos/{repo_id}/assistant")
def ask_repository(repo_id: str, payload: assistant.ChatRequest):
    metadata(repo_id)
    if not payload.question.strip():
        raise HTTPException(
            422, "Use a question and recent turns of up to 1,000 characters each."
        )
    with mutations:
        excerpts = assistant.search(
            root_for(repo_id), payload.question + " " + " ".join(payload.history[-1:])
        )
    return assistant.answer(payload.question, excerpts, payload.history)


@router.post("/repos/{repo_id}/improvements", status_code=201)
def improve(repo_id: str, payload: assistant.ImprovementRequest):
    metadata(repo_id)
    root = root_for(repo_id)
    with mutations:
        code, _ = assistant.read_source(root, payload.path)
    if not languages.capabilities(payload.path, code)["editable"]:
        raise HTTPException(422, "This file does not support improvements.")
    if patches.digest(code) != payload.source_hash:
        raise HTTPException(
            409, "The source changed. Reload the selection and try again."
        )
    excerpt = assistant.selection(payload, code)
    patch = assistant.improvement(code, payload, repo_id, excerpt)
    with mutations, store.database() as conn:
        latest, _ = assistant.read_source(root, payload.path)
        if patches.digest(latest) != payload.source_hash:
            raise HTTPException(
                409, "The source changed during generation. Reload the selection."
            )
        store.save(conn, "patch", patch)
    return patch


def agent_repository(conn, repo_id):
    if store.get(conn, "repository", repo_id) is None:
        raise HTTPException(
            404,
            {
                "code": "repository_missing",
                "message": "This repository is unavailable. Choose or import a repository.",
            },
        )


@router.get("/repos/{repo_id}/agent/sessions")
def list_agent_sessions(repo_id: str):
    with store.database() as conn:
        agent_repository(conn, repo_id)
        return [
            {"id": item["id"], "title": item.get("title", item["id"])}
            for item in store.records(
                conn, "agent_session", repo_id, exclude=("turns",)
            )[:30]
        ]


@router.post("/repos/{repo_id}/agent/sessions", status_code=201)
def create_agent_session(repo_id: str):
    with mutations, store.database() as conn:
        agent_repository(conn, repo_id)
        session = dict(
            id=str(uuid4()), repo_id=repo_id, turns=[], recent_paths=[], pending=None
        )
        store.save(conn, "agent_session", session)
        return session


def agent_session(conn, repo_id, session_id):
    agent_repository(conn, repo_id)
    session = store.get(conn, "agent_session", session_id)
    if not session or session["repo_id"] != repo_id:
        raise HTTPException(
            404,
            {
                "code": "session_missing",
                "message": "This conversation no longer exists. Send again to start a new one.",
            },
        )
    return session


@router.get("/repos/{repo_id}/agent/sessions/{session_id}")
def read_agent_session(repo_id: str, session_id: str):
    with store.database() as conn:
        return agent_session(conn, repo_id, session_id)


@router.post("/repos/{repo_id}/agent/sessions/{session_id}/messages")
def agent_message(repo_id: str, session_id: str, payload: agent.Message):
    with mutations, store.database() as conn:
        session = agent_session(conn, repo_id, session_id)
        if payload.request_id and any(
            turn.get("request_id") == payload.request_id for turn in session["turns"]
        ):
            return session
        pending = (
            store.get(conn, "patch", session["pending"]) if session["pending"] else None
        )
        if pending and pending["status"] != "proposed":
            pending = None
        reply, patch = agent.respond(root_for(repo_id), session, payload, pending)
        if reply["type"] == "analysis":
            scan = analysis.scan(root_for(repo_id), repo_id)
            store.save(conn, "analysis", scan)
            for item in scan["findings"]:
                store.save(
                    conn,
                    "finding",
                    {**item, "repo_id": repo_id, "analysis_id": scan["id"]},
                )
            reply.update(files=scan["files_analyzed"], findings=len(scan["findings"]))
        if patch:
            if pending and {item["path"] for item in patch["changes"]} & {
                item["path"] for item in pending["changes"]
            }:
                pending["status"] = "superseded"
                store.save(conn, "patch", pending)
            store.save(conn, "patch", patch)
            session["pending"] = patch["id"]
        session.setdefault("title", payload.text.strip()[:80])
        session["turns"] = [
            *session["turns"],
            {"text": payload.text, "reply": reply, "request_id": payload.request_id},
        ][-30:]
        store.save(conn, "agent_session", session)
        return session


@router.get("/github/status")
def github_status():
    with store.database() as conn:
        return github.connection(conn)


@router.post("/github/connect")
def github_connect():
    with mutations, store.database() as conn:
        account = github.request("GET", "/user")
        github.save_connection(conn, account["login"])
        return github.connection(conn)


@router.post("/github/disconnect")
def github_disconnect():
    with mutations, store.database() as conn:
        github.save_connection(conn, None)
        return github.connection(conn)


@router.get("/github/repositories")
def github_repositories(page: int = Query(1, ge=1, le=100)):
    with store.database() as conn:
        github.require_connection(conn)
    rows = github.request(
        "GET",
        "/user/repos",
        params={
            "page": page,
            "per_page": 50,
            "sort": "updated",
            "visibility": "all",
        },
    )
    return dict(
        items=[
            dict(
                name=repo["full_name"],
                private=repo["private"],
                url="https://github.com/" + repo["full_name"],
            )
            for repo in rows
        ],
        next_page=page + 1 if len(rows) == 50 and page < 100 else None,
    )


@router.post("/github/import", status_code=201)
def github_import(payload: ImportRequest):
    url, name = repository.github_url(payload.url)

    def materialize(root):
        with store.database() as conn:
            github.require_connection(conn)
        github.materialize(github.slug(url), root)

    return add_project(name, "github", materialize, url.removesuffix(".git"))


class PublishRequest(BaseModel):
    patch_id: str = Field(max_length=40)
    confirmation: str = Field(pattern=r"^[a-f0-9]{64}$")
    title: str = Field(min_length=1, max_length=120)
    confirmed: bool = False


@router.get("/repos/{repo_id}/github/preview")
def github_preview(repo_id: str, patch_id: str):
    with mutations, store.database() as conn:
        github.require_connection(conn)
        repo = required(conn, "repository", repo_id)
        patch = required(conn, "patch", patch_id)
        return github.publication_preview(repo, patch, root_for(repo_id))


@router.post("/repos/{repo_id}/github/pull-requests", status_code=201)
def github_publish(repo_id: str, payload: PublishRequest):
    if not payload.confirmed:
        raise HTTPException(
            422, "Confirm the preview before creating a GitHub branch and pull request."
        )
    title = payload.title.strip()
    if (
        not title
        or repository.SECRET.search(title)
        or (github.token() and github.token() in title)
    ):
        raise HTTPException(422, "Enter a pull request title without secrets.")
    with mutations, store.database() as conn:
        github.require_connection(conn)
        repo = required(conn, "repository", repo_id)
        patch = required(conn, "patch", payload.patch_id)
        previous = store.get(conn, "github_pr", "github-pr:" + patch["id"])
        if previous and previous.get("number") and previous["repo_id"] == repo_id:
            if previous["confirmation"] != payload.confirmation:
                raise HTTPException(409, "The publication preview is stale.")
            return previous
        plan = github.publication_preview(repo, patch, root_for(repo_id))
        if plan["confirmation"] != payload.confirmation:
            raise HTTPException(
                409, "The GitHub base changed. Preview again before publishing."
            )
        return github.publish(conn, plan, patch, title)


@router.get("/repos/{repo_id}/github/pull-requests")
def github_pull_requests(repo_id: str):
    with store.database() as conn:
        required(conn, "repository", repo_id)
        return store.records(conn, "github_pr", repo_id)


@router.get("/repos/{repo_id}/github/pull-requests/{patch_id}")
def github_refresh(repo_id: str, patch_id: str):
    with mutations, store.database() as conn:
        github.require_connection(conn)
        record = required(conn, "github_pr", "github-pr:" + patch_id)
        if record["repo_id"] != repo_id or not record.get("number"):
            raise HTTPException(404, "No published pull request for this patch.")
        result = github.request(
            "GET", "/repos/" + record["repository"] + "/pulls/" + str(record["number"])
        )
        record = github.pr_status(result, record)
        store.save(conn, "github_pr", record)
        return record


@router.get("/repos/{repo_id}/architecture")
def repository_architecture(repo_id: str, selected: str | None = None):
    with mutations, store.database() as conn:
        required(conn, "repository", repo_id)
        facts = intelligence.index(root_for(repo_id), selected)
        store.save(
            conn,
            "architecture",
            dict(
                id="architecture:" + repo_id,
                repo_id=repo_id,
                data=facts,
                observed_at=datetime.now(timezone.utc).isoformat(),
            ),
        )
        return facts


@router.post("/repos/{repo_id}/architecture/summary")
def architecture_summary(repo_id: str):
    with mutations, store.database() as conn:
        required(conn, "repository", repo_id)
        facts = intelligence.index(root_for(repo_id))
    return dict(summary=intelligence.summarize(facts), kind="AI interpretation")


@router.post("/repos/{repo_id}/review")
def code_review(repo_id: str, payload: intelligence.ReviewInput):
    with mutations, store.database() as conn:
        required(conn, "repository", repo_id)
        patch = required(conn, "patch", payload.patch_id) if payload.patch_id else None
        if patch and patch["repo_id"] != repo_id:
            raise HTTPException(404, "Patch not found.")
        scan = store.latest(conn, "analysis", repo_id)
        scope = (
            {item["path"] for item in change_set.changes(patch)}
            if patch
            else set(payload.paths)
        )
        suggestions = intelligence.review(root_for(repo_id), payload, patch)
        return dict(
            static_findings=[item for item in scan["findings"] if item["path"] in scope]
            if scan
            else [],
            static_scan_id=scan["id"] if scan else None,
            ai_suggestions=suggestions,
        )


@router.get("/repos/{repo_id}/report")
def repository_report(repo_id: str):
    with store.database() as conn:
        repo = required(conn, "repository", repo_id)
        scan = store.latest(conn, "analysis", repo_id)
        review = store.records(
            conn,
            "patch",
            repo_id,
            exclude=("original", "proposed", "diff", "changes", "comparison.remaining"),
        )
        benchmark = store.latest(conn, "benchmark_run", "bundled")
        architecture = store.get(conn, "architecture", "architecture:" + repo_id)
        report = intelligence.report(repo, scan, review, benchmark, architecture)
    return Response(
        report,
        media_type="text/markdown",
        headers={
            "Content-Disposition": 'attachment; filename="repository-report.md"',
            "Cache-Control": "no-store",
        },
    )
