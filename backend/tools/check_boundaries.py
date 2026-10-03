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


def _used_modules(src: Path, path: Path, tree: ast.AST) -> Iterator[tuple[int, str]]:
    """Yield (line, fully qualified module name) for every module the file uses.

    That is every import and every attribute chain that reaches a submodule of an
    imported ``dawam`` package: after ``import dawam.modules.x as y``, ``y.tables.Foo``
    uses ``dawam.modules.x.tables``. For ``from pkg import name`` the used name is
    ``pkg.name`` when ``name`` is a submodule on disk, otherwise ``pkg``.
    """
    this = _module_name(src, path)
    package = this if path.name == "__init__.py" else this.rpartition(".")[0]
    bound: dict[str, str] = {}  # local name -> the dawam module it refers to
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield node.lineno, alias.name
                if alias.asname:
                    bound[alias.asname] = alias.name
                else:  # ``import a.b.c`` binds ``a``
                    root = alias.name.partition(".")[0]
                    bound[root] = root
        elif isinstance(node, ast.ImportFrom):
            base = _import_base(package, node)
            for alias in node.names:
                if _is_submodule(src, base, alias.name):
                    name = f"{base}.{alias.name}"
                    bound[alias.asname or alias.name] = name
                    yield node.lineno, name
                else:
                    yield node.lineno, base
    bound = {local: name for local, name in bound.items() if _is_within(name, ROOT_PACKAGE)}
    yield from _attribute_uses(src, tree, bound)


def _attribute_uses(src: Path, tree: ast.AST, bound: dict[str, str]) -> Iterator[tuple[int, str]]:
    """Yield (line, submodule) for each ``name.attr...`` chain reaching a submodule."""
    inner = {node.value for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    for node in ast.walk(tree):
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


def check(src: Path) -> list[str]:
    src = Path(src)
    violations: list[str] = []
    for path in sorted((src / ROOT_PACKAGE).rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        this = _module_name(src, path)
        for line, imported in sorted(_used_modules(src, path, tree)):
            problem = _violation(this, imported)
            if problem:
                location = f"{path.relative_to(src).as_posix()}:{line}"
                violations.append(f"{location}: {problem}")
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
