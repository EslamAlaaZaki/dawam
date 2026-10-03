"""The module-boundary checker enforces the convention in src/dawam/modules/README.md."""

from pathlib import Path

from tools.check_boundaries import check, main


def write(root: Path, rel: str, source: str = "") -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")


def make_tree(tmp_path: Path) -> Path:
    """A tiny dawam package with two modules, `alpha` and `beta`."""
    src = tmp_path / "src"
    write(src, "dawam/__init__.py")
    write(src, "dawam/platform/__init__.py")
    write(src, "dawam/platform/db.py")
    write(src, "dawam/modules/__init__.py")
    for name in ("alpha", "beta"):
        write(src, f"dawam/modules/{name}/__init__.py", "from .service import Service\n")
        write(src, f"dawam/modules/{name}/service.py", "class Service: ...\n")
        write(src, f"dawam/modules/{name}/tables.py", "")
        write(src, f"dawam/modules/{name}/internal/__init__.py", "")
        write(src, f"dawam/modules/{name}/internal/helpers.py", "")
    return src


def test_clean_tree_has_no_violations(tmp_path):
    src = make_tree(tmp_path)
    write(
        src,
        "dawam/modules/alpha/service.py",
        "import dawam.modules.beta\n"
        "from dawam.modules import beta\n"
        "from dawam.modules.beta import Service\n"
        "from dawam.platform.db import x\n"
        "from . import tables\n"
        "from .internal import helpers\n"
        "from dawam.modules.alpha.tables import T\n"
        "import dawam.modules.beta as b\n"
        "b.Service()\n"
        "beta.Service()\n"
        "dawam.modules.beta.Service()\n"
        "import dawam.modules.alpha\n"
        "dawam.modules.alpha.tables.T\n",
    )
    write(src, "dawam/platform/db.py", "import dawam\nversion = dawam.__version__\n")
    assert check(src) == []


def test_importing_another_modules_internals_is_a_violation(tmp_path):
    src = make_tree(tmp_path)
    write(
        src, "dawam/modules/alpha/service.py", "from dawam.modules.beta.tables import BetaTable\n"
    )
    violations = check(src)
    assert len(violations) == 1
    assert "dawam/modules/alpha/service.py:1" in violations[0]
    assert "dawam.modules.beta.tables" in violations[0]


def test_plain_import_of_internals_is_a_violation(tmp_path):
    src = make_tree(tmp_path)
    write(src, "dawam/modules/alpha/service.py", "import dawam.modules.beta.internal.helpers\n")
    assert len(check(src)) == 1


def test_from_package_import_of_a_submodule_is_a_violation(tmp_path):
    src = make_tree(tmp_path)
    write(src, "dawam/modules/alpha/service.py", "from dawam.modules.beta import tables\n")
    violations = check(src)
    assert len(violations) == 1
    assert "dawam.modules.beta.tables" in violations[0]


def test_relative_import_into_another_module_is_a_violation(tmp_path):
    src = make_tree(tmp_path)
    write(src, "dawam/modules/alpha/service.py", "from ..beta.tables import BetaTable\n")
    assert len(check(src)) == 1


def test_code_outside_modules_may_not_reach_into_internals(tmp_path):
    src = make_tree(tmp_path)
    write(src, "dawam/app.py", "from dawam.modules.alpha.internal.helpers import h\n")
    assert len(check(src)) == 1


def test_platform_may_not_import_modules_at_all(tmp_path):
    src = make_tree(tmp_path)
    write(src, "dawam/platform/db.py", "from dawam.modules import alpha\n")
    violations = check(src)
    assert len(violations) == 1
    assert "platform" in violations[0]


def test_attribute_access_into_internals_is_a_violation(tmp_path):
    src = make_tree(tmp_path)
    write(
        src,
        "dawam/modules/alpha/service.py",
        "import dawam.modules.beta\n\nx = dawam.modules.beta.tables.BetaTable\n",
    )
    violations = check(src)
    assert len(violations) == 1
    assert "dawam/modules/alpha/service.py:3" in violations[0]
    assert "dawam.modules.beta.tables" in violations[0]


def test_attribute_access_through_an_alias_is_a_violation(tmp_path):
    src = make_tree(tmp_path)
    write(src, "dawam/modules/alpha/service.py", "import dawam.modules.beta as b\nb.tables.T\n")
    violations = check(src)
    assert len(violations) == 1
    assert "dawam.modules.beta.tables" in violations[0]


def test_attribute_access_from_an_imported_package_is_a_violation(tmp_path):
    src = make_tree(tmp_path)
    write(
        src,
        "dawam/modules/alpha/service.py",
        "from dawam import modules\n"
        "from dawam.modules import beta as b\n"
        "modules.beta.internal.helpers.h()\n"
        "b.tables.T\n",
    )
    violations = check(src)
    assert len(violations) == 2
    assert "dawam.modules.beta.internal" in violations[0]
    assert "dawam.modules.beta.tables" in violations[1]


def test_local_names_shadowing_an_imported_module_are_not_the_module(tmp_path):
    src = make_tree(tmp_path)
    write(
        src,
        "dawam/modules/alpha/service.py",
        "from dawam.modules import beta\n"
        "def by_parameter(beta):\n"
        "    return beta.internal\n"
        "def by_assignment():\n"
        "    beta = object()\n"
        "    return beta.tables\n"
        "def by_loop(items):\n"
        "    for beta in items:\n"
        "        beta.tables\n"
        "by_lambda = lambda beta: beta.tables\n"
        "by_comprehension = [beta.tables for beta in ()]\n"
        "class Holder:\n"
        "    beta = None\n"
        "    def method(self, beta=beta):\n"
        "        return beta.internal\n",
    )
    assert check(src) == []


def test_shadowing_is_limited_to_its_own_scope(tmp_path):
    src = make_tree(tmp_path)
    write(
        src,
        "dawam/modules/alpha/service.py",
        "from dawam.modules import beta\n"
        "def shadowed(beta):\n"
        "    return beta.tables\n"
        "def not_shadowed():\n"
        "    return beta.tables\n"
        "def inner_import():\n"
        "    from dawam.modules import beta as b\n"
        "    return b.internal\n"
        "def uses_global():\n"
        "    global beta\n"
        "    return beta.tables\n"
        "def default_is_outer(x=beta.internal):\n"
        "    beta = None\n"
        "first_iterable_is_outer = [beta for beta in beta.tables.rows]\n",
    )
    violations = check(src)
    assert [v.split(": ")[0] for v in violations] == [
        "dawam/modules/alpha/service.py:5",
        "dawam/modules/alpha/service.py:8",
        "dawam/modules/alpha/service.py:11",
        "dawam/modules/alpha/service.py:12",
        "dawam/modules/alpha/service.py:14",
    ]


def test_platform_may_not_reach_modules_by_attribute_access(tmp_path):
    src = make_tree(tmp_path)
    write(src, "dawam/platform/db.py", "import dawam\nx = dawam.modules.alpha.Service\n")
    violations = check(src)
    assert len(violations) == 1
    assert "platform" in violations[0]


def test_public_interface_may_not_re_export_its_tables(tmp_path):
    src = make_tree(tmp_path)
    for source in (
        "from .tables import BetaTable\n",
        "from . import tables\n",
        "from dawam.modules.beta.tables import BetaTable\n",
    ):
        write(src, "dawam/modules/beta/__init__.py", "from .service import Service\n" + source)
        violations = check(src)
        assert len(violations) == 1, source
        assert "dawam/modules/beta/__init__.py:2" in violations[0]
        assert "tables" in violations[0]


def test_public_names_may_not_reuse_submodule_names(tmp_path):
    src = make_tree(tmp_path)
    write(src, "dawam/modules/beta/api.py", "api = object()\n")
    for source in (
        "from .api import api\n",
        "from .service import Service as internal\n",
        "service = Service()\n",
        "def api(): ...\n",
        "import dawam.modules.alpha as api\n",
    ):
        write(src, "dawam/modules/beta/__init__.py", "from .service import Service\n" + source)
        violations = check(src)
        assert len(violations) == 1, source
        assert "dawam/modules/beta/__init__.py:2" in violations[0]
        assert "submodule" in violations[0]


def test_public_interface_may_bind_its_submodules_themselves(tmp_path):
    src = make_tree(tmp_path)
    write(src, "dawam/modules/beta/api.py", "router = object()\n")
    write(
        src,
        "dawam/modules/beta/__init__.py",
        "from . import api\n"
        "from dawam.modules.beta import service\n"
        "from .api import router\n"
        "def build(api):\n"
        "    service = api\n"
        "    return service\n",
    )
    write(src, "dawam/modules/alpha/service.py", "from dawam.modules import beta\nbeta.api\n")
    violations = check(src)
    assert len(violations) == 1  # alpha reaching beta.api; nothing in beta/__init__.py
    assert "dawam/modules/alpha/service.py:2" in violations[0]


def test_main_exit_code_reflects_violations(tmp_path, capsys):
    src = make_tree(tmp_path)
    assert main([str(src)]) == 0
    write(src, "dawam/modules/alpha/service.py", "from dawam.modules.beta.tables import T\n")
    assert main([str(src)]) == 1
    assert "dawam.modules.beta.tables" in capsys.readouterr().out
