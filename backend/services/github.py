"""Server-side GitHub requests and explicit, accepted-patch publication."""

import base64
import binascii
import hashlib
import os
import time
from datetime import datetime, timezone
from urllib.parse import quote

import httpx
from fastapi import HTTPException

from services import change_set, patches, repository, store

ROOT = "https://api.github.com"
CONNECTION = "github:connection"


def token():
    return os.getenv("GITHUB_TOKEN", "").strip()


def fingerprint():
    return hashlib.sha256(token().encode()).hexdigest() if token() else None


def request(method, path, **kwargs):
    key = token()
    if not key:
        raise HTTPException(
            503, "Configure GITHUB_TOKEN in backend/.env and restart FastAPI."
        )
    try:
        with httpx.Client(
            base_url=ROOT,
            timeout=10,
            follow_redirects=False,
            trust_env=False,
            headers={
                "Authorization": "Bearer " + key,
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        ) as client:
            response = client.request(method, path, **kwargs)
    except httpx.HTTPError:
        raise HTTPException(
            502, "GitHub could not be reached. Check connectivity and retry."
        ) from None
    status = response.status_code
    if status == 401:
        raise HTTPException(401, "GitHub rejected the configured token.")
    if status == 429 or (
        status == 403 and response.headers.get("x-ratelimit-remaining") == "0"
    ):
        raise HTTPException(429, "GitHub rate limit reached. Try again later.")
    if status == 403:
        raise HTTPException(
            403, "GitHub denied access. Check the token's repository permissions."
        )
    if status == 404:
        raise HTTPException(
            404, "GitHub could not find this resource or the token lacks access."
        )
    if status == 422:
        raise HTTPException(
            422, "GitHub rejected the request. Check repository and branch rules."
        )
    if not response.is_success:
        raise HTTPException(502, "GitHub returned an unexpected response.")
    try:
        return response.json()
    except ValueError:
        raise HTTPException(502, "GitHub returned invalid data.") from None


def connection(conn):
    row = store.get(conn, "github_connection", CONNECTION)
    connected = bool(row and token() and row.get("fingerprint") == fingerprint())
    return dict(
        configured=bool(token()),
        connected=connected,
        username=row["username"] if connected else None,
    )


def require_connection(conn):
    if not connection(conn)["connected"]:
        raise HTTPException(
            409, "Connect GitHub in Settings before using authenticated actions."
        )


def save_connection(conn, username):
    store.save(
        conn,
        "github_connection",
        dict(
            id=CONNECTION,
            username=username,
            fingerprint=fingerprint() if username else None,
        ),
    )


def slug(url):
    clean, _ = repository.github_url(url)
    return clean.removeprefix("https://github.com/").removesuffix(".git")


def head(project):
    api = "/repos/" + project
    repo = request("GET", api)
    branch = repo["default_branch"]
    sha = request("GET", api + "/git/ref/heads/" + quote(branch, safe="/"))["object"][
        "sha"
    ]
    tree = request("GET", api + "/git/commits/" + sha)["tree"]["sha"]
    return api, branch, sha, tree


def tree_entries(api, tree):
    data = request("GET", api + "/git/trees/" + tree, params={"recursive": "1"})
    if data.get("truncated") or len(data["tree"]) > repository.MAX_FILES:
        raise HTTPException(413, "GitHub tree exceeds the supported size.")
    return {row["path"]: row for row in data["tree"]}


def materialize(project, root):
    api, _, _, tree = head(project)
    entries = tree_entries(api, tree)
    files = []
    size = 0
    for name, row in entries.items():
        parts = repository.parts_for(name)
        if repository.excluded(parts) or row["type"] == "tree":
            continue
        if row["type"] != "blob" or row["mode"] not in {"100644", "100755"}:
            raise HTTPException(
                422, "GitHub import encountered a symbolic link or submodule."
            )
        size += row["size"]
        files.append((parts, row))
    if size > repository.MAX_BYTES or len(files) > 250:
        raise HTTPException(
            413, "Authenticated import exceeds 250 files or the source size limit."
        )
    seen = set()
    deadline = time.monotonic() + 75
    for parts, row in files:
        key = "/".join(parts).casefold()
        if key in seen or time.monotonic() > deadline:
            raise HTTPException(
                422,
                "Authenticated import exceeded its time budget or has conflicting paths.",
            )
        seen.add(key)
        blob = request("GET", api + "/git/blobs/" + row["sha"])
        try:
            raw = base64.b64decode("".join(blob["content"].split()), validate=True)
        except (KeyError, ValueError, binascii.Error):
            raise HTTPException(502, "GitHub returned an invalid file.") from None
        if len(raw) != row["size"]:
            raise HTTPException(502, "GitHub file size does not match its tree.")
        path = root.joinpath(*parts)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as stream:
            stream.write(raw)


def blob_id(content):
    raw = content.encode()
    return hashlib.sha1(
        f"blob {len(raw)}\0".encode() + raw, usedforsecurity=False
    ).hexdigest()


def publication_preview(repo, patch, root):
    if not repo.get("source_url") or repo["origin"] != "github":
        raise HTTPException(422, "Pull requests require a GitHub-imported repository.")
    if patch["repo_id"] != repo["id"] or patch["status"] != "accepted":
        raise HTTPException(409, "Select an accepted patch from this repository.")
    project = slug(repo["source_url"])
    api, default, base, tree = head(project)
    branch = "code-review/" + patch["id"]
    if branch == default:
        raise HTTPException(409, "The proposed branch is the default branch.")
    remote = tree_entries(api, tree)
    files = []
    for item in change_set.changes(patch):
        path = item["path"]
        _, local, _ = change_set.snapshot(root, path)
        old, new = change_set.previous(item), change_set.desired(item)
        if local != new:
            raise HTTPException(409, "The working copy changed after approval: " + path)
        if any(
            content is not None
            and (repository.SECRET.search(content) or token() in content)
            for content in (old, new)
        ):
            raise HTTPException(422, "The approved source may contain a credential.")
        entry = remote.get(path)
        if old is None:
            matches = entry is None
        else:
            matches = bool(
                entry
                and entry["type"] == "blob"
                and entry["mode"] in {"100644", "100755"}
                and entry["sha"] == blob_id(old)
            )
        if not matches:
            raise HTTPException(
                409, "The GitHub base differs from the approved original: " + path
            )
        files.append(
            dict(
                path=path,
                operation=item["operation"],
                mode=entry["mode"] if entry else "100644",
            )
        )
    return dict(
        repository=project,
        base_branch=default,
        base=base,
        tree=tree,
        branch=branch,
        patch_id=patch["id"],
        files=files,
        confirmation=patches.digest(project + ":" + patch["id"] + ":" + base),
    )


def pr_status(data, record):
    number = int(data["number"])
    return {
        **record,
        "number": number,
        "url": "https://github.com/" + record["repository"] + "/pull/" + str(number),
        "state": "merged"
        if data.get("merged") or data.get("merged_at")
        else data["state"],
    }


def publish(conn, plan, patch, title):
    key = "github-pr:" + patch["id"]
    previous = store.get(conn, "github_pr", key)
    if previous and previous.get("number"):
        return previous
    if previous and previous["confirmation"] != plan["confirmation"]:
        raise HTTPException(
            409, "The base moved after publication began. Review the branch in GitHub."
        )
    api = "/repos/" + plan["repository"]
    record = previous or {
        **plan,
        "id": key,
        "repo_id": patch["repo_id"],
        "state": "preparing",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    if not previous:
        tree = []
        for item, metadata in zip(
            change_set.changes(patch), plan["files"], strict=True
        ):
            content = change_set.desired(item)
            sha = (
                None
                if content is None
                else request(
                    "POST",
                    api + "/git/blobs",
                    json={
                        "content": base64.b64encode(content.encode()).decode(),
                        "encoding": "base64",
                    },
                )["sha"]
            )
            tree.append(
                dict(path=item["path"], mode=metadata["mode"], type="blob", sha=sha)
            )
        tree_sha = request(
            "POST", api + "/git/trees", json={"base_tree": plan["tree"], "tree": tree}
        )["sha"]
        record["commit"] = request(
            "POST",
            api + "/git/commits",
            json={
                "message": title,
                "tree": tree_sha,
                "parents": [plan["base"]],
            },
        )["sha"]
        store.save(conn, "github_pr", record)
        conn.commit()

    # Never update a branch. Resume only when its exact previously prepared commit matches.
    fresh = request("GET", api)
    if (
        fresh["default_branch"] != plan["base_branch"]
        or fresh["default_branch"] == plan["branch"]
    ):
        raise HTTPException(
            409, "The GitHub default branch changed. Refresh and review again."
        )
    ref = api + "/git/ref/heads/" + quote(plan["branch"], safe="/")
    try:
        existing = request("GET", ref)["object"]["sha"]
    except HTTPException as error:
        if error.status_code != 404:
            raise
        request(
            "POST",
            api + "/git/refs",
            json={"ref": "refs/heads/" + plan["branch"], "sha": record["commit"]},
        )
    else:
        if existing != record["commit"]:
            raise HTTPException(
                409, "The GitHub feature branch already contains another commit."
            )
    record["state"] = "branch created"
    store.save(conn, "github_pr", record)
    conn.commit()
    owner = plan["repository"].split("/")[0]
    existing = request(
        "GET",
        api + "/pulls",
        params={
            "head": owner + ":" + plan["branch"],
            "base": plan["base_branch"],
            "state": "all",
        },
    )
    result = (
        existing[0]
        if existing
        else request(
            "POST",
            api + "/pulls",
            json={
                "title": title,
                "head": plan["branch"],
                "base": plan["base_branch"],
                "body": "Changes from explicitly approved Code Review patch "
                + patch["id"]
                + ".",
            },
        )
    )
    record = pr_status(result, record)
    store.save(conn, "github_pr", record)
    return record
