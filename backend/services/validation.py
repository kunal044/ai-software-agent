"""Bounded diagnostic tools. Only trusted tool code is executed."""

import json
import os
from pathlib import Path
import re
import shutil
import subprocess

from fastapi import HTTPException

from services.languages import detect

TOOLS = Path(__file__).resolve().parents[1] / "tools"
INCLUDES = set(
    "stdio.h stdlib.h string.h math.h stdint.h stddef.h stdbool.h limits.h assert.h cstdio cstdlib cstdint cstring cmath iostream string vector array algorithm utility map set memory limits".split()
)
PASSED = "PASSED"
UNAVAILABLE = "Validation not available"


def diagnostic(rule, message, line=1, category="syntax"):
    return dict(
        rule=rule, message=message.strip()[:500], line=max(1, line), category=category
    )


def run(args, source, timeout=12):
    names = {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "LANG", "LC_ALL"}
    env = {key: value for key, value in os.environ.items() if key.upper() in names}
    env.update(LANG="C", LC_ALL="C")
    return subprocess.run(
        args,
        input=source.encode("utf-8"),
        capture_output=True,
        timeout=timeout,
        check=False,
        cwd=TOOLS,
        env=env,
    )


def java_executable():
    names = [shutil.which("javac")]
    if home := os.getenv("JAVA_HOME"):
        names.append(
            str(Path(home) / "bin" / ("javac.exe" if os.name == "nt" else "javac"))
        )
    if os.name == "nt" and os.getenv("ProgramFiles"):
        names.append(
            str(Path(os.environ["ProgramFiles"]) / "Java/latest/bin/javac.exe")
        )
    for name in filter(None, names):
        compiler = Path(name)
        java = compiler.with_name("java" + compiler.suffix)
        if compiler.is_file() and java.is_file():
            return str(java)
    return None


def javascript(code, path, lint=False):
    node = shutil.which("node")
    if not node:
        return None
    result = run(
        [node, "--max-old-space-size=192", str(TOOLS / "javascript_diagnostics.mjs")],
        json.dumps(
            {"code": code, "extension": Path(path).suffix.lower()[1:], "lint": lint}
        ),
    )
    if result.returncode:
        return None
    return json.loads(result.stdout)


def diagnose(code, path, lint=False):
    """Return diagnostics or None when isolated validation cannot be performed."""
    language = detect(path, code)
    kind = language.validator if language else None
    clean = code.removeprefix("\ufeff")
    try:
        if kind == "python":
            try:
                compile(clean, str(path), "exec")
                return []
            except (SyntaxError, ValueError, RecursionError) as error:
                return [
                    diagnostic(
                        "PY-SYNTAX",
                        getattr(error, "msg", "Invalid Python source"),
                        getattr(error, "lineno", 1) or 1,
                    )
                ]
        if kind == "json":
            try:
                json.loads(clean, parse_constant=reject_constant)
                return []
            except json.JSONDecodeError as error:
                return [diagnostic("JSON-SYNTAX", error.msg, error.lineno)]
            except (ValueError, RecursionError):
                return [
                    diagnostic(
                        "JSON-SYNTAX", "Invalid JSON constant or excessive nesting."
                    )
                ]
        if kind in {"javascript", "typescript"}:
            checked = javascript(
                clean, path, lint=True if kind == "javascript" else lint
            )
            return checked["issues"] if checked is not None else None
        if kind == "java":
            java = java_executable()
            if not java:
                return None
            result = run(
                [
                    java,
                    "-Xmx128m",
                    "-Dfile.encoding=UTF-8",
                    str(TOOLS / "JavaSyntax.java"),
                ],
                clean,
            )
            if result.returncode:
                return None
            return [
                diagnostic(rule, message, int(line))
                for line, rule, message in (
                    row.split("\t", 2)
                    for row in result.stdout.decode("utf-8", "replace").splitlines()
                )
            ]
        if kind in {"c", "cpp"}:
            return c_diagnostics(clean, kind)
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    return None


def reject_constant(value):
    raise ValueError(value)


def c_diagnostics(code, kind):
    # Prevent file reads via arbitrary includes, macro expansion, alternate directive
    # spellings, line splicing, and nonstandard preprocessing directives.
    if re.search(r"%:|\?\?|\\\s*[\r\n]", code):
        return None
    for line in code.splitlines():
        if "#" in line:
            include = re.fullmatch(r"\s*#\s*include\s*<([a-z0-9.]+)>\s*", line)
            if not include or include[1] not in INCLUDES:
                return None
    compiler = shutil.which("gcc" if kind == "c" else "g++")
    if not compiler:
        return None
    result = run(
        [
            compiler,
            "-fsyntax-only",
            "-std=c11" if kind == "c" else "-std=c++14",
            "-Wall",
            "-Wextra",
            "-fmax-errors=12",
            "-x",
            "c" if kind == "c" else "c++",
            "-",
        ],
        code,
    )
    output = result.stderr.decode("utf-8", "replace")
    issues = [
        diagnostic(
            "GCC" if kind == "c" else "G++",
            message,
            int(line),
            "syntax" if level == "error" else "static",
        )
        for line, level, message in re.findall(
            r"^<stdin>:(\d+):(?:\d+:)?\s*(error|warning):\s*(.+)", output, re.M
        )
    ]
    if result.returncode and not any(issue["category"] == "syntax" for issue in issues):
        return None
    return issues


def require_valid(code, path):
    issues = diagnose(code, path)
    if issues is None:
        return UNAVAILABLE
    if any(item["category"] == "syntax" for item in issues):
        language = detect(path, code)
        raise HTTPException(
            422, f"The proposed {language.name} code failed syntax validation."
        )
    return PASSED
