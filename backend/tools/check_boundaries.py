"""Module-boundary check for the DAWAM backend.

Rules (see src/dawam/modules/README.md):

1. Code may import another module only through its public interface: the package
   itself (``dawam.modules.<name>``), never a submodule such as
   ``dawam.modules.<name>.tables`` or ``dawam.modules.<name>.internal``.
   A module may import its own submodules freely.
2. The shared kernel ``dawam.platform`` must not import ``dawam.modules`` or the
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


def _imported_names(src: Path, path: Path, tree: ast.AST) -> Iterator[tuple[int, str]]:
    """Yield (line, fully qualified imported module name) for every import.

    For ``from pkg import name`` the yielded name is ``pkg.name`` when ``name`` is a
    submodule on disk, otherwise ``pkg``.
    """
    this = _module_name(src, path)
    package = this if path.name == "__init__.py" else this.rpartition(".")[0]
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield node.lineno, alias.name
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base_parts = package.split(".")
                if node.level > 1:
                    base_parts = base_parts[: -(node.level - 1)]
                base = ".".join(base_parts)
                target = f"{base}.{node.module}" if node.module else base
            else:
                target = node.module or ""
            for alias in node.names:
                candidate = f"{target}.{alias.name}"
                if _is_submodule(src, target, alias.name):
                    yield node.lineno, candidate
                else:
                    yield node.lineno, target


def _violation(this: str, imported: str) -> str | None:
    if _is_within(this, PLATFORM_PACKAGE):
        for forbidden in PLATFORM_FORBIDDEN:
            if _is_within(imported, forbidden):
                return (
                    f"{imported} is imported by the shared kernel ({PLATFORM_PACKAGE}), "
                    "which must not depend on modules or the app"
                )
    target_module = _owning_module(imported)
    if target_module is None:
        return None
    if _owning_module(this) == target_module:
        return None
    public_interface = f"{MODULES_PACKAGE}.{target_module}"
    if imported != public_interface:
        return f"{imported} is internal to module '{target_module}'; import {public_interface}"
    return None


def check(src: Path) -> list[str]:
    src = Path(src)
    violations: list[str] = []
    for path in sorted((src / ROOT_PACKAGE).rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        this = _module_name(src, path)
        for line, imported in _imported_names(src, path, tree):
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
