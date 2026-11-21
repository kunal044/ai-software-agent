"""Source capabilities and Monaco identifiers shared across repository workflows."""

from dataclasses import dataclass
from pathlib import Path
import re


@dataclass(frozen=True)
class Language:
    name: str
    monaco: str
    analyzer: str | None = None
    validator: str | None = None
    editable: bool = False

    @property
    def operations(self):
        actions = ("preview", "explain")
        if self.editable:
            actions += ("modify", "create", "delete", "fix", "improve")
        if self.analyzer:
            actions += ("analyze",)
        return actions


PYTHON = Language("Python", "python", "ruff", "python", True)
JS = Language("JavaScript", "javascript", "eslint", "javascript", True)
TS = Language("TypeScript", "typescript", "typescript", "typescript", True)
JAVA = Language("Java", "java", "javac", "java", True)
C = Language("C", "cpp", "gcc", "c", True)
CPP = Language("C++", "cpp", "g++", "cpp", True)
HEADER = Language("C/C++ header", "cpp", editable=True)
JSON = Language("JSON", "json", "json", "json", True)
REGISTRY = {
    **dict.fromkeys(("py", "pyi"), PYTHON),
    **dict.fromkeys(("js", "jsx", "mjs", "cjs"), JS),
    **dict.fromkeys(("ts", "tsx", "mts", "cts"), TS),
    "java": JAVA,
    "c": C,
    "h": HEADER,
    **dict.fromkeys(("cc", "cpp", "cxx", "hh", "hpp", "hxx"), CPP),
    "json": JSON,
    "yaml": Language("YAML", "yaml"),
    "yml": Language("YAML", "yaml"),
    "md": Language("Markdown", "markdown"),
    "txt": Language("Text", "plaintext"),
    "toml": Language("TOML", "ini"),
    "html": Language("HTML", "html"),
    "css": Language("CSS", "css"),
}


def detect(path, code=None):
    path = Path(path)
    extension = path.suffix.lower().lstrip(".")
    language = REGISTRY.get(extension)
    if language is HEADER and code:
        if re.search(r"\b(?:namespace|constexpr|template\s*<|std::)\b", code):
            return CPP
        if re.search(r"\b(?:_Static_assert|_Generic|_Atomic)\b", code):
            return C
    return language


def capabilities(path, code=None):
    language = detect(path, code)
    return (
        dict(
            language_name=language.name,
            editable=language.editable,
            analyzer=language.analyzer,
            operations=language.operations,
        )
        if language
        else dict(editable=False)
    )
