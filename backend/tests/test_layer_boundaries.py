"""The layer boundaries this refactor enforces — and the debt it is burning down.

Three rules, one test each:

1. ``db/models/**`` is the model layer: dataclasses and nothing else. It imports
   the standard library only — no pydantic, no psycopg, no project code.
2. The logic layers (``planner/``, ``agent/``, ``api/``, ``quality/``) never speak
   SQL and never touch a driver. Every database read goes through a repository.
3. Every top-level package is declared in the three places that need it — wheel
   packages, isort's first-party list and pyright's include in ``pyproject.toml``,
   plus the ``COPY`` lines in the ``Dockerfile``.

Rules 1 and 2 ship with ``_PENDING_*``: the violations that exist today, each
named with the workstream that removes it. A violation that is *not* on a list
fails the build, and an entry that no longer violates anything fails too — the
lists cannot rot. Rule 3 has no list: a new package must be declared.

``db/seed/`` is deliberately out of scope for rule 2: it is the write path, it
lives in the persistence package, and CLAUDE.md makes it the one sanctioned
writer.
"""

from __future__ import annotations

import ast
import re
import sys
import tomllib
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]

# --- rule 1: the model layer ------------------------------------------------

MODEL_LAYER = BACKEND / "db" / "models"
_MODEL_LAYER_ALLOWED = frozenset(sys.stdlib_module_names) | {"__future__"}

# --- rule 2: the layers that must not speak SQL -----------------------------

LOGIC_LAYERS = ("planner", "agent", "api", "quality")

# A statement, not a word: "SELECT " has to be followed by a column or a number,
# and "SET " has to be followed by a dotted GUC name — otherwise prose in an
# unrelated string would trip the scan.
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

# Violations that exist today. Each entry goes away with the workstream that
# closes it; the last test in this file fails if one stays behind.
_PENDING_SQL = {
    "agent/tools/_db.py": "W4c — _db_area_rows moves to AreaRepository.search",
    "planner/pipeline.py": "W4d — health()'s SELECT 1 becomes PlaceRepository.ping()",
    "planner/resolve.py": "W4d — _without_forbidden becomes PlaceRepository.category_of()",
    "planner/retrieve.py": "W4d — _category_signal becomes PlaceRepository.by_category()",
    "quality/evals.py": "W4e — run_services() becomes PlaceRepository.with_category()",
}
_PENDING_DRIVER = {
    "planner/pipeline.py": "W4 — Pipeline takes repositories, not a connection",
    "planner/refine.py": "W4d — takes PlaceRepository",
    "planner/resolve.py": "W4d — takes PlaceRepository",
    "planner/retrieve.py": "W4d — takes PlaceRepository",
    "quality/evals.py": "W4e — opens a repository instead of a connection",
}

# --- rule 3: what the tooling and the image must know about -----------------

# Packages the image deliberately does not ship (backend/.dockerignore).
_NOT_SHIPPED = frozenset({"tests", "quality"})
# Directories copied into the image that are not Python packages.
_COPIED_AS_IS = frozenset({"data"})


def _python_files(directory: Path) -> list[Path]:
    """Every module under `directory`, excluding caches. Empty if it is absent."""
    if not directory.is_dir():
        return []
    return sorted(
        path
        for path in directory.rglob("*.py")
        if "__pycache__" not in path.parts
    )


def _package_dirs() -> set[str]:
    """Top-level directories in the backend that hold Python (hidden ones aside)."""
    return {
        child.name
        for child in BACKEND.iterdir()
        if child.is_dir()
        and not child.name.startswith(".")
        and any(child.rglob("*.py"))
    }


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


def _pyproject() -> dict:
    return tomllib.loads((BACKEND / "pyproject.toml").read_text(encoding="utf-8"))


def _declared_packages() -> tuple[set[str], set[str], set[str]]:
    """The three lists in `pyproject.toml` that enumerate packages by name."""
    config = _pyproject()
    return (
        set(config["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"]),
        set(config["tool"]["ruff"]["lint"]["isort"]["known-first-party"]),
        set(config["tool"]["pyright"]["include"]),
    )


def test_model_layer_imports_stdlib_only() -> None:
    """`db/models` describes data; it may not depend on anything but the stdlib."""
    offenders: dict[str, set[str]] = {}
    for path in _python_files(MODEL_LAYER):
        roots = _imported_roots(ast.parse(path.read_text(encoding="utf-8")))
        foreign = roots - _MODEL_LAYER_ALLOWED
        if foreign:
            offenders[str(path.relative_to(BACKEND))] = foreign
    assert not offenders, (
        "the model layer must import the standard library only: "
        f"{ {name: sorted(mods) for name, mods in offenders.items()} }"
    )


def test_logic_layers_do_not_speak_sql() -> None:
    """Every database read goes through `db/store`; the logic layers call it."""
    offenders: dict[str, list[int]] = {}
    for layer in LOGIC_LAYERS:
        for path in _python_files(BACKEND / layer):
            relative = path.relative_to(BACKEND).as_posix()
            if relative in _PENDING_SQL:
                continue
            lines = _sql_lines(path)
            if lines:
                offenders[relative] = lines
    assert not offenders, (
        "SQL in a logic layer — move it to a repository in db/store: "
        f"{offenders}"
    )


def test_logic_layers_do_not_import_a_driver() -> None:
    """A logic layer holds repositories, never a connection."""
    offenders: list[str] = []
    for layer in LOGIC_LAYERS:
        for path in _python_files(BACKEND / layer):
            relative = path.relative_to(BACKEND).as_posix()
            if relative in _PENDING_DRIVER:
                continue
            if _DRIVER.search(path.read_text(encoding="utf-8")):
                offenders.append(relative)
    assert not offenders, (
        f"a logic layer imports psycopg — take a repository instead: {offenders}"
    )


def test_the_agent_layer_is_a_leaf() -> None:
    """The reader declares its own input; it never reaches up for a wire model.

    This is the edge that keeps `planner -> agent` one-way, and therefore the
    pipeline able to drive the reader. The agent used to import `GenerateReq`
    straight out of `contracts`, and `TripRequirements` out of `domain`; both
    now live in `agent.models`.
    """
    offenders: dict[str, set[str]] = {}
    for path in _python_files(BACKEND / "agent"):
        reached = _imported_roots(ast.parse(path.read_text(encoding="utf-8")))
        upward = reached & {"planner", "contracts"}
        if upward:
            offenders[path.relative_to(BACKEND).as_posix()] = upward
    assert not offenders, f"the agent layer must stay a leaf: {offenders}"


def test_pending_lists_are_current() -> None:
    """An entry that was fixed (or whose file vanished) must leave the list."""
    stale: list[str] = []
    for relative, reason in _PENDING_SQL.items():
        path = BACKEND / relative
        if not path.exists() or not _sql_lines(path):
            stale.append(f"{relative} ({reason})")
    for relative, reason in _PENDING_DRIVER.items():
        path = BACKEND / relative
        if not path.exists() or not _DRIVER.search(path.read_text(encoding="utf-8")):
            stale.append(f"{relative} ({reason})")
    assert not stale, (
        "these are listed as pending but no longer violate anything — "
        f"remove them from the list: {stale}"
    )


def test_every_package_is_declared() -> None:
    """A package the tooling does not know about is a package nothing checks."""
    expected = _package_dirs() - _NOT_SHIPPED
    packages, first_party, typed = _declared_packages()
    assert packages == expected, (
        "pyproject [tool.hatch.build.targets.wheel].packages does not match the "
        f"packages on disk: {sorted(packages ^ expected)}"
    )
    assert first_party == expected, (
        "pyproject [tool.ruff.lint.isort].known-first-party does not match the "
        f"packages on disk: {sorted(first_party ^ expected)}"
    )
    assert typed == expected, (
        "pyproject [tool.pyright].include does not match the packages on disk: "
        f"{sorted(typed ^ expected)}"
    )


def test_dockerfile_copies_every_package() -> None:
    """A package the image does not COPY fails at import time, in the container."""
    text = (BACKEND / "Dockerfile").read_text(encoding="utf-8")
    copied = set(re.findall(r"^COPY (\w+)/ /app/\1/", text, re.MULTILINE))
    packages, _, _ = _declared_packages()
    expected = packages | _COPIED_AS_IS
    assert copied == expected, (
        "the Dockerfile does not copy every package: "
        f"{sorted(copied ^ expected)}"
    )
