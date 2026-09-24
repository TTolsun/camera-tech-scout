"""Symbol extraction with tree-sitter.

Evidence has to point at a named thing, not at a line number that will move on
the next commit. This module turns a source file into a list of declarations
with their line ranges, their body text and the comment block that precedes
them, because that comment is very often where the author explains the problem
being solved.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .lexicon.terms import LANGUAGE_BY_EXTENSION
from .util import log

try:  # pragma: no cover - exercised only when the dependency is missing
    from tree_sitter_language_pack import get_parser as _get_parser
    TREE_SITTER_AVAILABLE = True
except Exception as exc:  # noqa: BLE001
    _get_parser = None
    TREE_SITTER_AVAILABLE = False
    log.warning("tree-sitter is unavailable, symbol extraction is disabled: %s", exc)


# Node types that introduce a named declaration worth citing as evidence.
DEFINITION_NODES: dict[str, dict[str, str]] = {
    "c": {
        "function_definition": "function",
        "struct_specifier": "struct",
        "enum_specifier": "enum",
        "type_definition": "typedef",
    },
    "cpp": {
        # `namespace_definition` and `template_declaration` are deliberately
        # absent: a namespace spans the whole file, so citing it points at
        # nothing useful, and the function or class inside a template is
        # captured on its own anyway.
        "function_definition": "function",
        "class_specifier": "class",
        "struct_specifier": "struct",
        "enum_specifier": "enum",
    },
    "python": {
        "function_definition": "function",
        "class_definition": "class",
    },
    "java": {
        "method_declaration": "method",
        "class_declaration": "class",
        "interface_declaration": "interface",
        "enum_declaration": "enum",
    },
    "rust": {
        "function_item": "function",
        "struct_item": "struct",
        "impl_item": "impl",
        "trait_item": "trait",
        "enum_item": "enum",
    },
    "go": {
        "function_declaration": "function",
        "method_declaration": "method",
        "type_declaration": "type",
    },
    "javascript": {
        "function_declaration": "function",
        "class_declaration": "class",
        "method_definition": "method",
    },
}

_NAME_NODE_TYPES = (
    "identifier", "field_identifier", "type_identifier", "qualified_identifier",
    "property_identifier", "operator_name", "destructor_name", "namespace_identifier",
    "scoped_identifier",
)

_COMMENT_NODE_TYPES = ("comment", "line_comment", "block_comment")

_parser_cache: dict[str, Any] = {}


@dataclass
class Symbol:
    name: str
    kind: str
    line_start: int
    line_end: int
    text: str
    doc: str = ""

    @property
    def searchable(self) -> str:
        """Everything a signal matcher should look at for this symbol."""
        return "\n".join(part for part in (self.doc, self.text) if part)


def language_for(path: str) -> str | None:
    return LANGUAGE_BY_EXTENSION.get(Path(path).suffix.lower())


def _parser(language: str):
    if not TREE_SITTER_AVAILABLE:
        return None
    if language not in _parser_cache:
        try:
            _parser_cache[language] = _get_parser(language)
        except Exception as exc:  # noqa: BLE001
            log.debug("no tree-sitter grammar for %s: %s", language, exc)
            _parser_cache[language] = None
    return _parser_cache[language]


def extract_symbols(source: str, language: str, max_symbols: int = 400,
                    max_body_chars: int = 2600) -> list[Symbol]:
    """Return the named declarations of a file, outermost first."""
    parser = _parser(language)
    wanted = DEFINITION_NODES.get(language)
    if parser is None or not wanted:
        return []

    data = source.encode("utf-8", errors="replace")
    try:
        tree = parser.parse(data)
    except Exception as exc:  # noqa: BLE001
        log.debug("tree-sitter failed to parse a %s file: %s", language, exc)
        return []

    lines = source.splitlines()
    symbols: list[Symbol] = []
    stack = [tree.root_node]
    while stack and len(symbols) < max_symbols:
        node = stack.pop()
        kind = wanted.get(node.type)
        if kind:
            name = _name_of(node, data)
            if name:
                body = data[node.start_byte:node.end_byte].decode("utf-8", errors="replace")
                symbols.append(
                    Symbol(
                        name=name,
                        kind=kind,
                        line_start=node.start_point[0] + 1,
                        line_end=node.end_point[0] + 1,
                        text=body[:max_body_chars],
                        doc=_leading_comment(lines, node.start_point[0]),
                    )
                )
        stack.extend(reversed(node.children))

    symbols.sort(key=lambda s: (s.line_start, s.line_end))
    return symbols


def _name_of(node: Any, data: bytes) -> str | None:
    """Best-effort declaration name, following C/C++ declarator chains."""
    named = node.child_by_field_name("name")
    if named is not None:
        return _text(named, data)

    declarator = node.child_by_field_name("declarator")
    seen = 0
    while declarator is not None and seen < 8:
        seen += 1
        if declarator.type in _NAME_NODE_TYPES:
            return _text(declarator, data)
        inner = declarator.child_by_field_name("declarator")
        if inner is None:
            for child in declarator.children:
                if child.type in _NAME_NODE_TYPES:
                    return _text(child, data)
            break
        declarator = inner

    for child in node.children:
        if child.type in _NAME_NODE_TYPES:
            return _text(child, data)
    return None


def _text(node: Any, data: bytes) -> str:
    return data[node.start_byte:node.end_byte].decode("utf-8", errors="replace").strip()


def _leading_comment(lines: list[str], start_row: int, max_lines: int = 14) -> str:
    """Collect the contiguous comment block directly above a declaration."""
    collected: list[str] = []
    row = start_row - 1
    while row >= 0 and len(collected) < max_lines:
        stripped = lines[row].strip()
        if not stripped:
            if collected:
                break
            row -= 1
            continue
        if stripped.startswith(("//", "#", "*", "/*", "*/", '"""', "'''", "///")):
            collected.append(stripped.lstrip("/*# ").rstrip("*/").strip())
            row -= 1
            continue
        break
    return "\n".join(reversed([line for line in collected if line]))


def fallback_symbols(source: str, max_symbols: int = 120) -> list[Symbol]:
    """Very small heuristic used for languages without a grammar.

    It only reports lines that look like a declaration, so the evidence still
    points at a name rather than at a bare line number.
    """
    import re

    pattern = re.compile(
        r"^\s*(?:(?:public|private|protected|static|inline|virtual|const|export|async)\s+)*"
        r"(?:class|struct|def|fn|func|function|interface|enum)\s+([A-Za-z_][\w:]*)"
    )
    out: list[Symbol] = []
    lines = source.splitlines()
    for index, line in enumerate(lines):
        match = pattern.match(line)
        if not match:
            continue
        end = min(len(lines), index + 40)
        out.append(
            Symbol(
                name=match.group(1),
                kind="declaration",
                line_start=index + 1,
                line_end=end,
                text="\n".join(lines[index:end]),
                doc=_leading_comment(lines, index),
            )
        )
        if len(out) >= max_symbols:
            break
    return out
