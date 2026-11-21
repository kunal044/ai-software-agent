"""Controlled proposal snapshots and journal recovery."""

from fastapi import HTTPException

from services import languages, patches, repository


def changes(patch):
    if "changes" in patch:
        return patch["changes"]
    return [{**patch, "operation": "modify"}]


def snapshot(root, name):
    if not root.is_dir() or repository.linked(root) or repository.linked(root.parent):
        raise HTTPException(422, "The working copy is unavailable.")
    parts = repository.parts_for(name)
    language = languages.detect(name)
    if repository.excluded(parts) or not language or not language.editable:
        raise HTTPException(
            422, "Choose an unprotected file in a supported coding language."
        )
    path = root
    missing = []
    for part in parts[:-1]:
        path /= part
        if path.exists() or path.is_symlink():
            if repository.linked(path) or not path.is_dir():
                raise HTTPException(422, "Parent path is unavailable.")
        else:
            missing.append(path)
    path /= parts[-1]
    if not path.exists() and not path.is_symlink():
        return path, None, missing
    _, code, _ = repository.source(root, name)
    return path, code, missing


def previous(item):
    return None if item["operation"] == "create" else item["original"]


def desired(item):
    return None if item["operation"] == "delete" else item["proposed"]


def write(path, content):
    if content is None:
        path.unlink(missing_ok=True)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        patches.atomic_write(path, content)


def remove_empty(directories):
    for path in sorted(
        set(directories), key=lambda item: len(item.parts), reverse=True
    ):
        try:
            path.rmdir()
        except OSError:
            pass


def inspect(root, patch, recovery=False):
    targets = []
    for item in changes(patch):
        path, content, missing = snapshot(root, item["path"])
        old, new = previous(item), desired(item)
        if recovery:
            if content not in (old, new):
                raise RuntimeError(
                    "Unexpected external edit during recovery: " + item["path"]
                )
        elif content != old or (
            content is not None and patches.digest(content) != item["original_hash"]
        ):
            raise HTTPException(
                409, "Source changed since proposal generation: " + item["path"]
            )
        targets.append((path, old, new, missing))
    return targets
