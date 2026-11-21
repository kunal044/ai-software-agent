"""Import and read isolated working copies."""

import os
import re
import shutil
import stat
import subprocess
import time
import zipfile
from pathlib import Path, PureWindowsPath
from tempfile import TemporaryFile

from fastapi import HTTPException

from services.languages import REGISTRY

SECRET = re.compile(
    r"(?i)(?:-----BEGIN [^-]+PRIVATE KEY-----|sk-[\w-]{16,}|gsk_[a-z0-9]{20,}|gh[pousr]_[\w]{20,}|github_pat_[\w]{20,}|"
    r"\b(?:[\w-]{1,80}[_-])?(?:api[_-]?key|token|password|secret)['\"]?\s*[:=]\s*['\"][^'\"]{6,})"
)
MAX_FILES = 4000
MAX_BYTES = 100 * 1024**2
MAX_SOURCE = 256 * 1024
IGNORED = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".ruff_cache",
    ".pytest_cache",
    ".mypy_cache",
    "dist",
    "build",
    ".next",
}
SENSITIVE = {
    ".aws",
    ".ssh",
    ".npmrc",
    ".pypirc",
    "credentials",
    "credentials.json",
    "secrets.json",
    "secrets.yml",
    "secrets.yaml",
    "id_rsa",
    "id_ed25519",
}
LANGUAGES = {extension: item.monaco for extension, item in REGISTRY.items()}


def github_url(url):
    match = re.fullmatch(
        r"https://github\.com/([a-zA-Z0-9](?:[a-zA-Z0-9-]{0,38}))/([a-zA-Z0-9_.-]{1,100})/?",
        url,
    )
    if not match:
        raise HTTPException(422, "Enter a public HTTPS GitHub repository URL.")
    owner, name = match.groups()
    if owner.endswith("-") or "--" in owner:
        raise HTTPException(422, "Invalid GitHub owner.")
    name = name.removesuffix(".git")
    if name in {"", ".", ".."}:
        raise HTTPException(422, "Invalid repository name.")
    return f"https://github.com/{owner}/{name}.git", name


def parts_for(name):
    parts = name.split("/")
    if len(parts) > 32 or len(name) > 500:
        raise HTTPException(422, "Project path is too long or deeply nested.")
    for part in parts:
        if (
            part in {"", ".", ".."}
            or part.endswith((" ", "."))
            or PureWindowsPath(part).is_reserved()
            or re.search(r'[\\:*?"<>|\x00-\x1f\u202a-\u202e\u2066-\u2069]', part)
        ):
            raise HTTPException(422, "Project contains an unsafe path.")
    return parts


def excluded(parts):
    return any(
        p.casefold() in IGNORED | SENSITIVE
        or p.casefold().startswith(".env")
        or p.casefold().endswith((".pem", ".key", ".pfx", ".p12", ".tmp", ".pyc"))
        for p in parts
    )


def linked(path):
    info = path.lstat()
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
    )


def project_file(root, name):
    parts = parts_for(name)
    if excluded(parts):
        raise HTTPException(404, "File unavailable.")
    current = root
    for part in parts:
        current /= part
        if not current.exists() or linked(current):
            raise HTTPException(404, "File unavailable.")
    if not current.is_file():
        raise HTTPException(404, "File unavailable.")
    return current


def files_in(root, include_excluded=False):
    for folder, dirs, files in os.walk(root):
        base = Path(folder)
        dirs[:] = sorted(
            d
            for d in dirs
            if d.casefold() != ".git"
            and not linked(base / d)
            and (include_excluded or not excluded([d]))
        )
        for name in sorted(files):
            path = base / name
            if not linked(path) and stat.S_ISREG(path.stat().st_mode):
                rel = path.relative_to(root).as_posix()
                if include_excluded or not excluded(rel.split("/")):
                    yield rel, path


def source_language(path):
    if path.name.casefold() in {
        "readme",
        "license",
        "copying",
        "makefile",
        "dockerfile",
    }:
        return "plaintext"
    return LANGUAGES.get(path.suffix.lower().lstrip("."))


def source(root, name):
    path = project_file(root, name)
    language = source_language(path)
    if not language or path.stat().st_size > MAX_SOURCE:
        raise HTTPException(415, "File type or size is unsupported for preview.")
    try:
        with path.open("rb") as stream:
            raw = stream.read(MAX_SOURCE + 1)
        if len(raw) > MAX_SOURCE:
            raise HTTPException(415, "File size is unsupported for preview.")
        content = raw.decode("utf-8")
        if "\0" in content:
            raise ValueError
    except ValueError:
        raise HTTPException(415, "Preview requires UTF-8 source text.") from None
    return path, content, language


def copy_bounded(stream, destination, remaining):
    size = 0
    with destination.open("xb") as output:
        while chunk := stream.read(65536):
            size += len(chunk)
            if size > remaining:
                raise HTTPException(413, "Project exceeds the size limit.")
            output.write(chunk)
    return size


def extract_zip(stream, root):
    try:
        with zipfile.ZipFile(stream) as archive:
            items = archive.infolist()
            if len(items) > MAX_FILES:
                raise HTTPException(413, "Too many archive entries.")
            total = 0
            seen = set()
            for item in items:
                parts = parts_for(item.orig_filename.removesuffix("/"))
                key = "/".join(parts).casefold()
                if key in seen:
                    raise HTTPException(422, "Archive contains duplicate paths.")
                seen.add(key)
                mode = stat.S_IFMT(item.external_attr >> 16)
                if mode not in (0, stat.S_IFREG, stat.S_IFDIR) or item.flag_bits & 1:
                    raise HTTPException(
                        422, "Archive contains a special or encrypted entry."
                    )
                if excluded(parts):
                    continue
                if item.file_size > max(item.compress_size * 200, 1024**2):
                    raise HTTPException(
                        413, "Archive compression ratio exceeds the limit."
                    )
                destination = root.joinpath(*parts)
                if item.is_dir():
                    destination.mkdir(parents=True, exist_ok=True)
                    continue
                destination.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(item) as entry:
                    total += copy_bounded(entry, destination, MAX_BYTES - total)
    except (zipfile.BadZipFile, NotImplementedError, OSError, RuntimeError):
        raise HTTPException(422, "Invalid ZIP archive or conflicting paths.") from None


def clone(url, root):
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("GIT_")}
    env.update(
        GIT_CONFIG_NOSYSTEM="1",
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_CONFIG_SYSTEM=os.devnull,
        GIT_TERMINAL_PROMPT="0",
        GIT_ALLOW_PROTOCOL="https",
    )
    command = [
        "git",
        "-c",
        "http.followRedirects=false",
        "-c",
        "credential.helper=",
        "-c",
        "core.hooksPath=" + str(root.parent / "disabled-hooks"),
        "-c",
        "core.symlinks=true",
    ]
    git_data = root.parent / "git-data"
    args = [
        "clone",
        "--bare",
        "--template=",
        "--no-tags",
        "--depth=1",
        "--",
        url,
        str(git_data),
    ]
    try:
        with subprocess.Popen(
            command + args,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            cwd=root.parent,
        ) as proc:
            deadline = time.monotonic() + 60
            try:
                while proc.poll() is None:
                    metadata = git_data
                    size = 0
                    for path in metadata.rglob("*"):
                        try:
                            size += path.stat().st_size
                        except FileNotFoundError:
                            continue
                    if time.monotonic() > deadline or size > MAX_BYTES:
                        raise HTTPException(
                            413, "Clone exceeded its time or size limit."
                        )
                    time.sleep(0.2)
            finally:
                if proc.poll() is None:
                    if os.name == "nt":
                        subprocess.run(
                            ["taskkill.exe", "/PID", str(proc.pid), "/T", "/F"],
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            timeout=5,
                            check=False,
                        )
                    if proc.poll() is None:
                        proc.kill()
            if proc.returncode:
                raise HTTPException(
                    502, "GitHub clone failed. Check public access and connectivity."
                )
        listing = subprocess.check_output(
            command + ["ls-tree", "-rlz", "HEAD"], env=env, cwd=git_data, timeout=15
        )
        entries = listing.rstrip(b"\0").split(b"\0") if listing else []
        if len(entries) > MAX_FILES:
            raise HTTPException(413, "Repository has too many files.")
        total = 0
        seen = set()
        blobs = []
        for entry in entries:
            header, name = entry.split(b"\t", 1)
            mode, _, oid, size = header.split()
            if mode not in {b"100644", b"100755"}:
                raise HTTPException(
                    422, "Repositories with links or submodules are unsupported."
                )
            parts = parts_for(name.decode("utf-8"))
            identity = name.decode("utf-8").casefold()
            if identity in seen:
                raise HTTPException(422, "Repository contains conflicting paths.")
            seen.add(identity)
            total += int(size)
            if total > MAX_BYTES:
                raise HTTPException(413, "Repository exceeds the size limit.")
            if excluded(parts):
                continue
            blobs.append((oid, int(size), root.joinpath(*parts)))
        with TemporaryFile(dir=root.parent) as batch:
            subprocess.run(
                command + ["cat-file", "--batch"],
                cwd=git_data,
                env=env,
                input=b"".join(oid + b"\n" for oid, _, _ in blobs),
                stdout=batch,
                stderr=subprocess.DEVNULL,
                check=True,
                timeout=max(1, deadline - time.monotonic()),
            )
            batch.seek(0)
            for oid, size, path in blobs:
                if batch.readline(128).split() != [oid, b"blob", str(size).encode()]:
                    raise ValueError("Unexpected git blob header")
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("xb") as output:
                    remaining = size
                    while remaining:
                        chunk = batch.read(min(65536, remaining))
                        if not chunk:
                            raise ValueError("Incomplete git blob")
                        output.write(chunk)
                        remaining -= len(chunk)
                if batch.read(1) != b"\n":
                    raise ValueError("Invalid git blob delimiter")
                if time.monotonic() > deadline:
                    raise HTTPException(413, "Repository import timed out.")
        shutil.rmtree(git_data, ignore_errors=True)
    except (OSError, subprocess.SubprocessError, UnicodeError, ValueError):
        raise HTTPException(502, "Could not import the GitHub repository.") from None


def exportable(path):
    """Exclude recognizable secrets and the configured GitHub credential from exports."""
    credential = os.getenv("GITHUB_TOKEN", "").strip().encode()
    if credential:
        tail = b""
        with path.open("rb") as stream:
            while chunk := stream.read(65536):
                block = tail + chunk
                if credential in block:
                    return False
                tail = block[-len(credential) :]
    if path.stat().st_size > MAX_SOURCE:
        return True
    try:
        return SECRET.search(path.read_bytes().decode("utf-8")) is None
    except UnicodeDecodeError:
        return True
