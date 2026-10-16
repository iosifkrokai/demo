"""The architecture the target layering promises: one test per rule."""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]


LEAF_RULES: dict[str, frozenset[str]] = {
    "routing": frozenset({"agent", "api"}),
    "agent": frozenset({"api"}),
}

LOGIC_LAYERS = ("agent", "api", "routing", "quality")

MODEL_LAYER = BACKEND / "db" / "models"
_MODEL_LAYER_ALLOWED = frozenset(sys.stdlib_module_names) | {"__future__"}

_SQL = re.compile(
    r"\b(?:"
    r"SELECT\s+(?:[\w*\"]|\d)"
    r"|INSERT\s+INTO\b"
    r"|UPDATE\s+\w+\s+SET\b"
    r"|DELETE\s+FROM\b"
    r"|CREATE\s+(?:TABLE|INDEX|UNIQUE|EXTENSION|OR)\b"
    r"|ALTER\s+TABLE\b"
    r"|SET\s+\w+\.\w+\s*="
    r")",
    re.IGNORECASE,
)

_DRIVER = re.compile(r"^\s*(?:import|from)\s+psycopg\b", re.MULTILINE)


def _python_files(directory: Path) -> list[Path]:
    """Every module under `directory`, caches aside. Empty if it is absent."""
    if not directory.is_dir():
        return []
    return sorted(path for path in directory.rglob("*.py") if "__pycache__" not in path.parts)


def _imported_roots(tree: ast.AST) -> set[str]:
    """The top-level module names a tree imports. Relative imports are skipped."""
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
    return roots


def _docstring_nodes(tree: ast.AST) -> set[int]:
    """`id()` of every string constant that is a docstring, so SQL scans skip prose."""
    found: set[int] = set()
    holders = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
    for node in ast.walk(tree):
        if not isinstance(node, holders):
            continue
        body = getattr(node, "body", [])
        if not body:
            continue
        first = body[0]
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            found.add(id(first.value))
    return found


def _sql_lines(path: Path) -> list[int]:
    """Line numbers of string literals in `path` that hold a SQL statement."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings = _docstring_nodes(tree)
    lines: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant):
            if isinstance(node.value, str) and id(node) not in docstrings:
                text = node.value
            else:
                continue
        elif isinstance(node, ast.JoinedStr):
            text = "".join(
                part.value
                for part in node.values
                if isinstance(part, ast.Constant) and isinstance(part.value, str)
            )
        else:
            continue
        if _SQL.search(text):
            lines.append(node.lineno)
    return lines


def test_the_leaf_layers_stay_below_every_logic_layer() -> None:
    """`routing` and `agent` declare their own input; they never reach upward."""
    offenders: dict[str, set[str]] = {}
    for leaf, forbidden in LEAF_RULES.items():
        for path in _python_files(BACKEND / leaf):
            reached = _imported_roots(ast.parse(path.read_text(encoding="utf-8")))
            upward = reached & forbidden
            if upward:
                offenders[path.relative_to(BACKEND).as_posix()] = upward
    assert not offenders, f"a leaf layer reaches upward — invert the import: {offenders}"


def test_logic_layers_do_not_speak_sql() -> None:
    """Every database read goes through `db/store`; the logic layers call it."""
    offenders: dict[str, list[int]] = {}
    for layer in LOGIC_LAYERS:
        for path in _python_files(BACKEND / layer):
            lines = _sql_lines(path)
            if lines:
                offenders[path.relative_to(BACKEND).as_posix()] = lines
    assert not offenders, f"SQL in a logic layer — move it to a repository in db/store: {offenders}"


def test_logic_layers_do_not_import_a_driver() -> None:
    """A logic layer holds repositories, never a connection."""
    offenders: list[str] = []
    for layer in LOGIC_LAYERS:
        for path in _python_files(BACKEND / layer):
            if _DRIVER.search(path.read_text(encoding="utf-8")):
                offenders.append(path.relative_to(BACKEND).as_posix())
    assert not offenders, f"a logic layer imports psycopg — take a repository instead: {offenders}"


def test_model_layer_imports_stdlib_only() -> None:
    """`db/models` describes data; it may not depend on anything but the stdlib."""
    offenders: dict[str, set[str]] = {}
    for path in _python_files(MODEL_LAYER):
        roots = _imported_roots(ast.parse(path.read_text(encoding="utf-8")))
        foreign = roots - _MODEL_LAYER_ALLOWED
        if foreign:
            offenders[path.relative_to(BACKEND).as_posix()] = foreign
    assert not offenders, (
        "the model layer must import the standard library only: "
        f"{ {name: sorted(mods) for name, mods in offenders.items()} }"
    )
