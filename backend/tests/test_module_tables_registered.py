"""Importing ``dawam.modules`` registers every module's tables for Alembic.

``dawam/migrations/env.py`` imports ``dawam.modules`` and autogenerates from
``Base.metadata``. A module's ``__init__`` may not import its own ``tables`` (rule 2
in src/dawam/modules/README.md), so its tables are registered only if its service
code imports them; a module that breaks this would silently drop out of
``alembic revision --autogenerate``.
"""

import subprocess
import sys
from pathlib import Path

import dawam.modules


def unimported_tables(package: str, package_dir: Path, path_entry: Path) -> list[str]:
    """``<package>.<module>.tables`` modules on disk that importing ``package`` leaves out.

    The import runs in a fresh interpreter, as Alembic's does, so nothing the test
    session already imported can hide a missing one.
    """
    expected = sorted(
        f"{package}.{tables.parent.name}.tables" for tables in package_dir.glob("*/tables.py")
    )
    script = (
        "import importlib, sys\n"
        f"sys.path.insert(0, {str(path_entry)!r})\n"
        f"importlib.import_module({package!r})\n"
        f"print(*(name for name in {expected!r} if name not in sys.modules), sep='\\n')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, check=True
    )
    return result.stdout.split()


def test_importing_dawam_modules_imports_every_modules_tables():
    package_dir = Path(dawam.modules.__file__).parent
    assert unimported_tables("dawam.modules", package_dir, package_dir.parents[1]) == []


def write(root: Path, rel: str, source: str = "") -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")


def test_a_module_whose_tables_are_not_imported_is_reported(tmp_path):
    write(tmp_path, "fixture/__init__.py")
    write(tmp_path, "fixture/modules/__init__.py", "from fixture.modules import alpha, beta\n")
    for name in ("alpha", "beta"):
        write(tmp_path, f"fixture/modules/{name}/__init__.py", "from .service import Service\n")
        write(tmp_path, f"fixture/modules/{name}/tables.py")
    write(
        tmp_path, "fixture/modules/alpha/service.py", "from . import tables\nclass Service: ...\n"
    )
    write(tmp_path, "fixture/modules/beta/service.py", "class Service: ...\n")
    write(tmp_path, "fixture/modules/gamma/__init__.py")  # no tables: nothing to register

    package_dir = tmp_path / "fixture" / "modules"
    assert unimported_tables("fixture.modules", package_dir, tmp_path) == [
        "fixture.modules.beta.tables"
    ]
