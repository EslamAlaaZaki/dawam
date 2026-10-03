"""Module-boundary check for the DAWAM backend.

Rules, numbered as in src/dawam/modules/README.md:

1. Code may use another module only through its public interface: the package
   itself (``dawam.modules.<name>``), never a submodule such as
   ``dawam.modules.<name>.tables`` or ``dawam.modules.<name>.internal``, whether
   it imports the submodule or reaches it by attribute access on an imported
   package (``dawam.modules.<name>.tables.Foo``, ``alias.internal.helpers``).
   A module may use its own submodules freely.
2. A module's tables are private. Checked only in part: a module's public
   interface (its package ``__init__``) must not import its own ``tables``, so it
   cannot re-export table classes. Everything else about rule 2 is left to review.
3. The shared kernel ``dawam.platform`` must not import ``dawam.modules`` or the
   composition root (``dawam.app``) at all.

Usage: ``python tools/check_boundaries.py [SRC_DIR]`` (default: ``src``).
Exits 1 and prints one line per violation if any rule is broken.
"""

from __future__ import annotations

import ast
import sys
from collections.abc import Iterator
from pathlib import Path

ROOT_PACKAGE = "dawam"
MODULES_PACKAGE = f"{ROOT_PACKAGE}.modules"
PLATFORM_PACKAGE = f"{ROOT_PACKAGE}.platform"
PLATFORM_FORBIDDEN = (MODULES_PACKAGE, f"{ROOT_PACKAGE}.app")


def _module_name(src: Path, path: Path) -> str:
    parts = list(path.relative_to(src).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _is_within(name: str, package: str) -> bool:
    return name == package or name.startswith(package + ".")


def _owning_module(name: str) -> str | None:
    """``dawam.modules.jobs.service`` -> ``jobs``; None outside dawam.modules."""
    if not _is_within(name, MODULES_PACKAGE):
        return None
    rest = name[len(MODULES_PACKAGE) + 1 :]
    return rest.split(".")[0] if rest else None


def _is_submodule(src: Path, package: str, name: str) -> bool:
    # Compare names exactly: Windows and macOS file systems are case-insensitive.
    package_dir = src.joinpath(*package.split("."))
    if not package_dir.is_dir():
        return False
    entries = {entry.name for entry in package_dir.iterdir()}
    return f"{name}.py" in entries or (name in entries and (package_dir / name).is_dir())


def _import_base(package: str, node: ast.ImportFrom) -> str:
    """The absolute name of the module that ``from <base> import ...`` imports from."""
    if not node.level:
        return node.module or ""
    base_parts = package.split(".")
    if node.level > 1:
        base_parts = base_parts[: -(node.level - 1)]
    base = ".".join(base_parts)
    return f"{base}.{node.module}" if node.module else base


def _package_of(src: Path, path: Path) -> str:
    """The package that relative imports in ``path`` are resolved against."""
    this = _module_name(src, path)
    return this if path.name == "__init__.py" else this.rpartition(".")[0]


def _imported(src: Path, package: str, node: ast.Import | ast.ImportFrom) -> Iterator[str]:
    """Yield the fully qualified name of every module ``node`` uses.

    For ``from pkg import name`` that is ``pkg.name`` when ``name`` is a submodule on
    disk, otherwise ``pkg``.
    """
    if isinstance(node, ast.Import):
        for alias in node.names:
            yield alias.name
        return
    base = _import_base(package, node)
    for alias in node.names:
        yield f"{base}.{alias.name}" if _is_submodule(src, base, alias.name) else base


# Nodes that open a new namespace. Python looks names up in the scope that binds them.
_SCOPES = (
    ast.FunctionDef,
    ast.AsyncFunctionDef,
    ast.Lambda,
    ast.ClassDef,
    ast.ListComp,
    ast.SetComp,
    ast.DictComp,
    ast.GeneratorExp,
)
_FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)
_COMPREHENSIONS = (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)
# Nodes whose ``name`` field, when set, is a name bound in the enclosing scope.
_NAMED_BINDINGS = (
    ast.FunctionDef,
    ast.AsyncFunctionDef,
    ast.ClassDef,
    ast.ExceptHandler,
    ast.MatchAs,
    ast.MatchStar,
)


def _outer_parts(scope: ast.AST) -> list[ast.AST]:
    """The parts of a nested scope's node that are evaluated in the enclosing scope."""
    if isinstance(scope, _FUNCTIONS):
        args = scope.args
        parts: list[ast.AST] = [*args.defaults, *(d for d in args.kw_defaults if d)]
        if not isinstance(scope, ast.Lambda):
            every_arg = [*args.posonlyargs, *args.args, *args.kwonlyargs]
            every_arg += [arg for arg in (args.vararg, args.kwarg) if arg]
            parts += [arg.annotation for arg in every_arg if arg.annotation]
            parts += [*scope.decorator_list, *([scope.returns] if scope.returns else [])]
        return parts
    if isinstance(scope, ast.ClassDef):
        return [*scope.decorator_list, *scope.bases, *scope.keywords]
    if isinstance(scope, _COMPREHENSIONS):
        return [scope.generators[0].iter]
    return []


def _inner_parts(scope: ast.AST) -> list[ast.AST]:
    """The parts of a scope's node that are evaluated in that scope itself."""
    if isinstance(scope, ast.Lambda):
        return [scope.body]
    if isinstance(scope, ast.Module | ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
        return list(scope.body)
    if isinstance(scope, _COMPREHENSIONS):
        elements = [scope.key, scope.value] if isinstance(scope, ast.DictComp) else [scope.elt]
        first, *rest = scope.generators
        return [*elements, first.target, *first.ifs, *rest]
    return []


def _walk_scope(scope: ast.AST) -> Iterator[ast.AST]:
    """Yield every node evaluated in ``scope``, including nested scopes' nodes, not their bodies."""
    todo = _inner_parts(scope)
    while todo:
        node = todo.pop()
        yield node
        todo.extend(_outer_parts(node) if isinstance(node, _SCOPES) else ast.iter_child_nodes(node))


def _bindings(src: Path, package: str, scope: ast.AST) -> Iterator[tuple[str, int, str | None]]:
    """Yield (name, line, module) for every name ``scope`` binds.

    ``module`` is the module an import binds the name to, or None if the name is bound
    to anything else (a parameter, an assignment, a def, a non-module import, ...).
    """
    if isinstance(scope, _FUNCTIONS):
        args = scope.args
        for arg in [*args.posonlyargs, *args.args, *args.kwonlyargs, args.vararg, args.kwarg]:
            if arg:
                yield arg.arg, scope.lineno, None
    for node in _walk_scope(scope):
        if isinstance(node, ast.Name) and not isinstance(node.ctx, ast.Load):
            yield node.id, node.lineno, None
        elif isinstance(node, _NAMED_BINDINGS) and node.name:
            yield node.name, node.lineno, None
        elif isinstance(node, ast.MatchMapping) and node.rest:
            yield node.rest, node.lineno, None
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname:
                    yield alias.asname, node.lineno, alias.name
                else:  # ``import a.b.c`` binds ``a``
                    root = alias.name.partition(".")[0]
                    yield root, node.lineno, root
        elif isinstance(node, ast.ImportFrom):
            base = _import_base(package, node)
            for alias in node.names:
                module = f"{base}.{alias.name}" if _is_submodule(src, base, alias.name) else None
                yield alias.asname or alias.name, node.lineno, module


def _used_modules(src: Path, path: Path, tree: ast.AST) -> Iterator[tuple[int, str]]:
    """Yield (line, fully qualified module name) for every module the file uses.

    That is every import and every attribute chain that reaches a submodule of an
    imported ``dawam`` package: after ``import dawam.modules.x as y``, ``y.tables.Foo``
    uses ``dawam.modules.x.tables``.
    """
    package = _package_of(src, path)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import | ast.ImportFrom):
            for name in _imported(src, package, node):
                yield node.lineno, name
    yield from _attribute_uses(src, package, tree, {})


def _attribute_uses(
    src: Path, package: str, scope: ast.AST, enclosing: dict[str, str]
) -> Iterator[tuple[int, str]]:
    """Yield (line, submodule) for each ``name.attr...`` chain in ``scope`` reaching a submodule.

    ``enclosing`` maps the names visible from enclosing scopes to the ``dawam`` module
    they refer to. A name the scope binds to anything but a ``dawam`` module import
    shadows the outer name throughout the scope, as it does in Python (rebinding is
    not followed in order: a name bound both ways in one scope counts as shadowed).
    """
    declared = {
        name
        for node in _walk_scope(scope)
        if isinstance(node, ast.Global | ast.Nonlocal)
        for name in node.names
    }
    bound = dict(enclosing)
    shadowed: set[str] = set()
    for name, _, module in sorted(_bindings(src, package, scope), key=lambda b: b[1]):
        if name in declared:
            continue
        if module is not None and _is_within(module, ROOT_PACKAGE):
            bound[name] = module
        else:
            shadowed.add(name)
    for name in shadowed:
        bound.pop(name, None)
    # Names a nested function looks up skip class scopes, as in Python.
    visible_inside = enclosing if isinstance(scope, ast.ClassDef) else bound

    nodes = list(_walk_scope(scope))
    inner = {node.value for node in nodes if isinstance(node, ast.Attribute)}
    for node in nodes:
        if isinstance(node, _SCOPES):
            yield from _attribute_uses(src, package, node, visible_inside)
        if not isinstance(node, ast.Attribute) or node in inner:
            continue  # only the outermost Attribute of each chain
        attrs: list[str] = []
        root: ast.expr = node
        while isinstance(root, ast.Attribute):
            attrs.append(root.attr)
            root = root.value
        if not isinstance(root, ast.Name) or root.id not in bound:
            continue
        name = bound[root.id]
        for attr in reversed(attrs):
            if not _is_submodule(src, name, attr):
                break
            name = f"{name}.{attr}"
        if name != bound[root.id]:
            yield node.lineno, name


def _violation(this: str, imported: str) -> str | None:
    if _is_within(this, PLATFORM_PACKAGE):
        for forbidden in PLATFORM_FORBIDDEN:
            if _is_within(imported, forbidden):
                return (
                    f"{imported} is used by the shared kernel ({PLATFORM_PACKAGE}), "
                    "which must not depend on modules or the app (rule 3)"
                )
    target_module = _owning_module(imported)
    if target_module is None:
        return None
    public_interface = f"{MODULES_PACKAGE}.{target_module}"
    if _owning_module(this) == target_module:
        if this == public_interface and _is_within(imported, f"{public_interface}.tables"):
            return (
                f"{imported} is imported by the public interface of module "
                f"'{target_module}'; its tables are private and must not be re-exported "
                "(rule 2)"
            )
        return None
    if imported != public_interface:
        return (
            f"{imported} is internal to module '{target_module}'; "
            f"use only what {public_interface} exports (rule 1)"
        )
    return None


def _submodule_name_clashes(src: Path, path: Path, tree: ast.Module) -> Iterator[tuple[int, str]]:
    """Yield (line, problem) for each public name of a module that reuses a submodule name.

    After ``api = ...`` in ``dawam/modules/beta/__init__.py``, ``beta.api`` would be
    either the object or the submodule, so the attribute-chain part of rule 1 could
    not tell whether it reaches an internal. Binding the submodule itself is fine.
    """
    this = _module_name(src, path)
    owner = _owning_module(this)
    if owner is None or this != f"{MODULES_PACKAGE}.{owner}":
        return  # not a module's public interface
    for name, line, module in _bindings(src, this, tree):
        if _is_submodule(src, this, name) and module != f"{this}.{name}":
            problem = (
                f"the public interface of module '{owner}' binds '{name}', the name of its "
                f"submodule {this}.{name}; public names must not reuse submodule names (rule 1)"
            )
            yield line, problem


def check(src: Path) -> list[str]:
    src = Path(src)
    violations: list[str] = []
    for path in sorted((src / ROOT_PACKAGE).rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        this = _module_name(src, path)
        problems = [
            (line, problem)
            for line, imported in _used_modules(src, path, tree)
            if (problem := _violation(this, imported))
        ]
        problems += _submodule_name_clashes(src, path, tree)
        location = path.relative_to(src).as_posix()
        violations += [f"{location}:{line}: {problem}" for line, problem in sorted(problems)]
    return violations


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    src = Path(args[0]) if args else Path("src")
    violations = check(src)
    for violation in violations:
        print(violation)
    if violations:
        print(f"\n{len(violations)} module-boundary violation(s).")
        return 1
    print("Module boundaries OK.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
